"""Job search, job details and location (geoId) resolution.

Every HTTP call made from this module counts against BOTH the `api_total`
quota and the dedicated `jobs` quota (30/day at most, see `api/quotas.py`).
One search page = one call; a location name that is not in the local geo
cache costs one extra typeahead call the first time only.

Parsing follows the same normalized-JSON conventions as `api/search.py`:
records live in `included[]`, keyed by `entityUrn`, and `*key` fields are
URN references into it.
"""

from __future__ import annotations

import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from linkedin_cli.api.client import LinkedInClient
from linkedin_cli.api.endpoints import (
    GEO_TYPEAHEAD_QUERY,
    JOB_POSTING,
    JOB_POSTING_DECORATION,
    JOB_VIEW_URL,
    JOBS_SEARCH,
    JOBS_SEARCH_DECORATION,
    JOBS_SEARCH_PAGE_SIZE,
    TYPEAHEAD,
)
from linkedin_cli.api.search import _build_included_index, _deep_resolve, _text
from linkedin_cli.models.job import JobCard, JobDetail

JOBS_QUOTA = ("jobs",)

GEO_CACHE_PATH = Path.home() / ".config" / "mayai-cli" / "linkedin" / "geo_cache.json"

# CLI-facing filter names → the codes the web app puts in `selectedFilters`.
EXPERIENCE_LEVELS: dict[str, str] = {
    "internship": "1",
    "entry": "2",
    "associate": "3",
    "mid-senior": "4",
    "director": "5",
    "executive": "6",
}
JOB_TYPES: dict[str, str] = {
    "full-time": "F",
    "part-time": "P",
    "contract": "C",
    "temporary": "T",
    "internship": "I",
    "volunteer": "V",
    "other": "O",
}
DATE_POSTED: dict[str, str | None] = {
    "24h": "r86400",
    "week": "r604800",
    "month": "r2592000",
    "any": None,
}
WORKPLACE_TYPES: dict[str, str] = {"onsite": "1", "remote": "2", "hybrid": "3"}
SORT_BY: dict[str, str] = {"relevant": "R", "recent": "DD"}

MAX_LIMIT = 100

_JOB_ID_RE = re.compile(r"(?:jobPosting(?:Card)?:\(?|jobs/view/)(\d+)")
_GEO_URN_RE = re.compile(r"urn:li:(?:fs_geo|fsd_geo|geo):(\d+)")


class JobsInputError(ValueError):
    """Bad filter value or job id; raised before any network call."""


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def search_jobs(
    client: LinkedInClient,
    keywords: str = "",
    *,
    location: str | None = None,
    geo_id: str | None = None,
    experience: list[str] | tuple[str, ...] = (),
    job_types: list[str] | tuple[str, ...] = (),
    date_posted: str = "any",
    workplace: list[str] | tuple[str, ...] = (),
    sort: str = "relevant",
    limit: int = JOBS_SEARCH_PAGE_SIZE,
    start: int = 0,
) -> list[JobCard]:
    """Run a job search and return up to `limit` cards starting at `start`.

    Pages are fetched sequentially, `JOBS_SEARCH_PAGE_SIZE` cards per call,
    and we stop as soon as a page comes back short. `location` is resolved
    to a geoId (cached on disk) unless `geo_id` is given.
    """
    if limit < 1 or limit > MAX_LIMIT:
        raise JobsInputError(f"limit must be between 1 and {MAX_LIMIT}")
    if start < 0:
        raise JobsInputError("start must be >= 0")
    filters = build_filters(
        experience=experience,
        job_types=job_types,
        date_posted=date_posted,
        workplace=workplace,
        sort=sort,
    )
    if not geo_id and location:
        geo_id = resolve_geo_id(client, location)["geo_id"]
    if geo_id and not geo_id.isdigit():
        raise JobsInputError(f"geo id must be numeric, got {geo_id!r}")

    cards: list[JobCard] = []
    seen: set[str] = set()
    offset = start
    while len(cards) < limit:
        count = min(JOBS_SEARCH_PAGE_SIZE, limit - len(cards))
        url = build_search_url(keywords, geo_id=geo_id, filters=filters, start=offset, count=count)
        page = parse_job_cards(client.get_json(url, quota_kinds=JOBS_QUOTA))
        for card in page:
            if card.job_id not in seen:
                seen.add(card.job_id)
                cards.append(card)
        if len(page) < count:
            break
        offset += count
    return cards[:limit]


def build_filters(
    *,
    experience: list[str] | tuple[str, ...] = (),
    job_types: list[str] | tuple[str, ...] = (),
    date_posted: str = "any",
    workplace: list[str] | tuple[str, ...] = (),
    sort: str = "relevant",
) -> dict[str, list[str]]:
    """Translate CLI filter names into `selectedFilters` codes (validated)."""
    filters: dict[str, list[str]] = {}
    if sort not in SORT_BY:
        raise JobsInputError(f"unknown sort {sort!r}; use one of {', '.join(SORT_BY)}")
    filters["sortBy"] = [SORT_BY[sort]]
    if experience:
        filters["experience"] = [_code(EXPERIENCE_LEVELS, v, "experience") for v in experience]
    if job_types:
        filters["jobType"] = [_code(JOB_TYPES, v, "job type") for v in job_types]
    if date_posted not in DATE_POSTED:
        raise JobsInputError(
            f"unknown date-posted {date_posted!r}; use one of {', '.join(DATE_POSTED)}"
        )
    posted = DATE_POSTED[date_posted]
    if posted:
        filters["timePostedRange"] = [posted]
    if workplace:
        filters["workplaceType"] = [_code(WORKPLACE_TYPES, v, "workplace") for v in workplace]
    return filters


def _code(table: dict[str, str], value: str, what: str) -> str:
    key = value.strip().lower()
    if key not in table:
        raise JobsInputError(f"unknown {what} {value!r}; use one of {', '.join(table)}")
    return table[key]


def build_search_url(
    keywords: str,
    *,
    geo_id: str | None,
    filters: dict[str, list[str]],
    start: int,
    count: int,
) -> str:
    """Build the voyagerJobsDashJobCards URL the /jobs/search page uses.

    The `query` param is a restli tuple, not JSON: we only URL-escape the
    free-text keywords so commas/colons/parens inside them can't break it.
    """
    parts = ["origin:JOB_SEARCH_PAGE_JOB_FILTER"]
    if keywords.strip():
        parts.append(f"keywords:{quote(keywords.strip(), safe='')}")
    if geo_id:
        parts.append(f"locationUnion:(geoId:{geo_id})")
    selected = ",".join(f"{k}:List({','.join(v)})" for k, v in filters.items() if v)
    if selected:
        parts.append(f"selectedFilters:({selected})")
    parts.append("spellCorrectionEnabled:true")
    return (
        f"{JOBS_SEARCH}"
        f"?decorationId={JOBS_SEARCH_DECORATION}"
        f"&count={count}"
        "&q=jobSearch"
        f"&query=({','.join(parts)})"
        f"&start={start}"
    )


def parse_job_cards(payload: dict[str, Any]) -> list[JobCard]:
    """Extract job cards, in result order, from a job search response."""
    included = _build_included_index(payload)
    ordered: list[dict[str, Any]] = []

    # Result order comes from data.elements[].jobCardUnion.*jobPostingCard.
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    for element in (data or {}).get("elements", []) or []:
        if not isinstance(element, dict):
            continue
        union = element.get("jobCardUnion") or {}
        ref = union.get("*jobPostingCard") if isinstance(union, dict) else None
        if isinstance(ref, str) and ref in included:
            ordered.append(included[ref])
        elif isinstance(union, dict) and isinstance(union.get("jobPostingCard"), dict):
            ordered.append(union["jobPostingCard"])

    # Fallback: shape changed, but the cards are still in included[]. Skip the
    # empty `(<id>,JOB_DETAILS)` stubs the response also carries.
    if not ordered:
        ordered = [
            e
            for e in included.values()
            if _is_type(e, "JobPostingCard") and (e.get("jobPostingTitle") or e.get("title"))
        ]

    postings = {
        _job_id(e.get("entityUrn", "")): e
        for e in included.values()
        if _is_type(e, "JobPosting")
    }

    out: list[JobCard] = []
    seen: set[str] = set()
    for raw in ordered:
        card = _card_from(_deep_resolve(raw, included), postings)
        if card and card.job_id not in seen:
            seen.add(card.job_id)
            out.append(card)
    return out


def _is_type(entry: dict[str, Any], suffix: str) -> bool:
    return str(entry.get("$type", "")).endswith(f".{suffix}")


def _job_id(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    match = _JOB_ID_RE.search(value)
    return match.group(1) if match else ""


def _card_from(card: dict[str, Any], postings: dict[str, dict[str, Any]]) -> JobCard | None:
    posting_ref = card.get("jobPosting")
    job_id = (
        _job_id(card.get("jobPostingUrn"))
        or _job_id(posting_ref.get("entityUrn") if isinstance(posting_ref, dict) else posting_ref)
        or _job_id(card.get("entityUrn"))
    )
    if not job_id:
        return None
    posting = posting_ref if isinstance(posting_ref, dict) else postings.get(job_id, {})

    # `jobPostingTitle` is the clean string; `title.text` may carry trailing
    # nbsp + a verification badge glyph.
    title = _text(card.get("jobPostingTitle")) or _text(card.get("title"))
    title = _clean(title or _text(posting.get("title")))

    listed_ms: int | None = None
    easy_apply: bool | None = None
    footer = card.get("footerItems")
    if isinstance(footer, list):
        easy_apply = False
        for item in footer:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == "LISTED_DATE" and isinstance(item.get("timeAt"), int):
                listed_ms = item["timeAt"]
            elif kind == "EASY_APPLY_TEXT":
                easy_apply = True
    if listed_ms is None and isinstance(posting.get("listedAt"), int):
        listed_ms = posting["listedAt"]

    return JobCard(
        job_id=job_id,
        title=title,
        company=_clean(_text(card.get("primaryDescription"))),
        location=_clean(_text(card.get("secondaryDescription"))),
        salary=_clean(_text(card.get("tertiaryDescription"))),
        listed_at=_iso(listed_ms),
        url=JOB_VIEW_URL.format(job_id=job_id),
        easy_apply=easy_apply,
        reposted=bool(posting.get("repostedJob")) if "repostedJob" in posting else None,
    )


def _clean(text: str) -> str:
    """Collapse the non-breaking spaces LinkedIn puts in titles and salaries."""
    return " ".join(text.replace("\u00a0", " ").split())


def _iso(ms: int | None) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# job details
# ---------------------------------------------------------------------------


def normalize_job_id(value: str) -> str:
    """Accept a numeric id, a /jobs/view/<id>/ URL, or a jobPosting URN."""
    value = value.strip()
    if value.isdigit():
        return value
    job_id = _job_id(value)
    if not job_id:
        raise JobsInputError(f"not a LinkedIn job id or URL: {value!r}")
    return job_id


def get_job(client: LinkedInClient, job: str) -> JobDetail:
    """Fetch one posting: description, apply method and open/closed state."""
    job_id = normalize_job_id(job)
    url = f"{JOB_POSTING.format(job_id=job_id)}?decorationId={JOB_POSTING_DECORATION}"
    return parse_job_detail(client.get_json(url, quota_kinds=JOBS_QUOTA), job_id)


def parse_job_detail(payload: dict[str, Any], job_id: str) -> JobDetail:
    included = _build_included_index(payload)
    raw = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    posting = _deep_resolve(raw or {}, included)

    apply_url, easy_apply = _apply_method(posting.get("applyMethod"))

    state = str(posting.get("jobState") or "")
    closed_ms = _int(posting.get("closedAt"))

    return JobDetail(
        job_id=job_id,
        title=str(posting.get("title") or ""),
        company=_company_name(posting, included),
        location=str(posting.get("formattedLocation") or ""),
        description=_text(posting.get("description")),
        listed_at=_iso(_int(posting.get("listedAt"))),
        expires_at=_iso(_int(posting.get("expireAt"))),
        closed_at=_iso(closed_ms),
        state=state,
        closed=bool(closed_ms) or state.upper() == "CLOSED",
        employment_type=str(posting.get("formattedEmploymentStatus") or ""),
        experience_level=str(posting.get("formattedExperienceLevel") or ""),
        workplace=_workplace(posting, included),
        remote_allowed=posting.get("workRemoteAllowed")
        if isinstance(posting.get("workRemoteAllowed"), bool)
        else None,
        applies=_int(posting.get("applies")),
        easy_apply=easy_apply,
        apply_url=apply_url,
        url=JOB_VIEW_URL.format(job_id=job_id),
    )


def _apply_method(method: Any) -> tuple[str, bool | None]:
    """Return (off-site apply URL, easy_apply) from `applyMethod`.

    Seen both flat (`{"$type": "...OffsiteApply", "companyApplyUrl": ...}`)
    and as a union keyed by the type name.
    """
    if not isinstance(method, dict):
        return "", None
    variants = [method] if "$type" in method else [
        {**v, "$type": k} for k, v in method.items() if isinstance(v, dict)
    ]
    for variant in variants:
        kind = str(variant.get("$type", ""))
        if kind.endswith("OffsiteApply"):
            return str(variant.get("companyApplyUrl") or ""), False
        if kind.endswith("OnsiteApply"):
            return "", True
    return "", None


def _workplace(posting: dict[str, Any], included: dict[str, dict[str, Any]]) -> str:
    names = []
    for urn in posting.get("workplaceTypes") or []:
        entry = included.get(urn) if isinstance(urn, str) else None
        if isinstance(entry, dict) and entry.get("localizedName"):
            names.append(str(entry["localizedName"]))
    return ", ".join(names)


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _company_name(posting: dict[str, Any], included: dict[str, dict[str, Any]]) -> str:
    details = posting.get("companyDetails")
    if not isinstance(details, dict):
        return ""
    # Flat (`{"company": urn, "companyResolutionResult": {...}}`) or a union
    # keyed by the decoration type name.
    nested = [v for v in details.values() if isinstance(v, dict)]
    for value in [details, *nested]:
        if value.get("companyName"):
            return str(value["companyName"])
        for key in ("companyResolutionResult", "company"):
            company = value.get(key)
            if isinstance(company, str):
                company = included.get(company)
            if isinstance(company, dict) and company.get("name"):
                return str(company["name"])
    return ""


# ---------------------------------------------------------------------------
# geo resolution (cached)
# ---------------------------------------------------------------------------


def _geo_cache_path() -> Path:
    import linkedin_cli.api.jobs as _self

    return _self.GEO_CACHE_PATH


def _geo_key(name: str) -> str:
    return " ".join(name.lower().split())


def load_geo_cache() -> dict[str, dict[str, str]]:
    path = _geo_cache_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_geo_cache(cache: dict[str, dict[str, str]]) -> None:
    path = _geo_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    tmp.replace(path)


def pin_geo_id(name: str, geo_id: str) -> dict[str, Any]:
    """Store `name → geo_id` in the cache without any network call.

    The geoId of any place is visible in the browser: search jobs for it on
    linkedin.com and read `geoId=` from the address bar.
    """
    key = _geo_key(name)
    if not key:
        raise JobsInputError("location must not be empty")
    if not geo_id.strip().isdigit():
        raise JobsInputError(f"geo id must be numeric, got {geo_id!r}")
    cache = load_geo_cache()
    cache[key] = {"geo_id": geo_id.strip(), "name": name.strip()}
    _save_geo_cache(cache)
    return {"query": name, "geo_id": geo_id.strip(), "name": name.strip(), "cached": True}


def resolve_geo_id(client: LinkedInClient, name: str) -> dict[str, Any]:
    """Resolve a location name to a geoId, using the on-disk cache first.

    Returns `{"query", "geo_id", "name", "cached"}`. A cache miss costs one
    typeahead call (counted against the jobs quota); the top hit is cached
    forever, so the second search for "Milan" is free.

    The GEO typeahead is the least stable endpoint here (it answered HTTP 400
    in the 2026-10 validation run); when it fails, pin the id instead with
    `linkedin jobs geo NAME --set GEO_ID` or pass `--geo-id`.
    """
    key = _geo_key(name)
    if not key:
        raise JobsInputError("location must not be empty")
    cache = load_geo_cache()
    hit = cache.get(key)
    if isinstance(hit, dict) and str(hit.get("geo_id", "")).isdigit():
        return {"query": name, "geo_id": hit["geo_id"], "name": hit.get("name", ""), "cached": True}

    url = TYPEAHEAD + GEO_TYPEAHEAD_QUERY.format(keywords=quote(name.strip(), safe=""))
    hits = parse_geo_hits(client.get_json(url, quota_kinds=JOBS_QUOTA))
    if not hits:
        raise JobsInputError(f"no LinkedIn location matches {name!r}; pass --geo-id instead")
    best = hits[0]
    cache[key] = {"geo_id": best["geo_id"], "name": best["name"]}
    _save_geo_cache(cache)
    return {"query": name, "geo_id": best["geo_id"], "name": best["name"], "cached": False}


def parse_geo_hits(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Return `[{"geo_id", "name"}]` from a GEO typeahead response, best first."""
    included = _build_included_index(payload)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for element in (data or {}).get("elements", []) or []:
        if not isinstance(element, dict):
            continue
        element = _deep_resolve(element, included)
        urn = element.get("targetUrn") or element.get("objectUrn") or ""
        match = _GEO_URN_RE.search(str(urn))
        if not match or match.group(1) in seen:
            continue
        seen.add(match.group(1))
        out.append({"geo_id": match.group(1), "name": _text(element.get("text"))})
    if not out:
        for entry in included.values():
            match = _GEO_URN_RE.search(str(entry.get("entityUrn", "")))
            if match and match.group(1) not in seen:
                seen.add(match.group(1))
                name = entry.get("defaultLocalizedName") or _text(entry.get("text"))
                out.append({"geo_id": match.group(1), "name": str(name or "")})
    return out


__all__ = [
    "DATE_POSTED",
    "EXPERIENCE_LEVELS",
    "GEO_CACHE_PATH",
    "JOB_TYPES",
    "JobsInputError",
    "SORT_BY",
    "WORKPLACE_TYPES",
    "build_filters",
    "build_search_url",
    "get_job",
    "normalize_job_id",
    "parse_geo_hits",
    "parse_job_cards",
    "parse_job_detail",
    "pin_geo_id",
    "resolve_geo_id",
    "search_jobs",
]

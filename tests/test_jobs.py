"""Offline tests for job search / job details / geo resolution.

Fixtures under `fixtures/` are fictional (made-up ids and companies) but keep
the exact shape of real Voyager responses recorded in October 2026:
`data.elements[].jobCardUnion.*jobPostingCard` → JobPostingCard records in
`included[]`, plus the empty `(<id>,JOB_DETAILS)` stubs the endpoint returns.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from linkedin_cli.api import jobs
from linkedin_cli.api.jobs import (
    JobsInputError,
    build_filters,
    build_search_url,
    get_job,
    normalize_job_id,
    parse_geo_hits,
    parse_job_cards,
    parse_job_detail,
    pin_geo_id,
    resolve_geo_id,
    search_jobs,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class _FakeClient:
    """Records GETs and replays queued payloads."""

    def __init__(self, *payloads: dict[str, Any]) -> None:
        self.payloads = list(payloads)
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def get_json(self, url: str, extra_headers=None, *, quota_kinds=()):
        self.calls.append((url, tuple(quota_kinds)))
        return self.payloads.pop(0) if self.payloads else {"data": {"elements": []}}


@pytest.fixture(autouse=True)
def _geo_cache(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "geo_cache.json"
    monkeypatch.setattr(jobs, "GEO_CACHE_PATH", path)
    return path


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def test_parse_job_cards_snapshot() -> None:
    cards = [c.to_dict() for c in parse_job_cards(_fixture("jobs_search_response.json"))]
    assert cards == [
        {
            "job_id": "4000000001",
            "title": "AI Engineer Intern",
            "company": "Acme Analytics",
            "location": "Milano, Lombardia, Italia (Ibrido)",
            "salary": "12K €/anno - 15K €/anno",
            "listed_at": "2026-10-16T13:32:07Z",
            "url": "https://www.linkedin.com/jobs/view/4000000001/",
            "easy_apply": False,
            "reposted": False,
        },
        {
            "job_id": "4000000002",
            "title": "Data Analyst Intern",
            "company": "Contoso Bank",
            "location": "Roma, Lazio, Italia (Ibrido)",
            "listed_at": "2026-10-16T11:26:40Z",
            "url": "https://www.linkedin.com/jobs/view/4000000002/",
            "easy_apply": True,
            "reposted": True,
        },
        {
            "job_id": "4000000003",
            "title": "Junior Data Scientist",
            "company": "Fabrikam Ventures",
            "location": "Europa, Medio Oriente e Africa (Da remoto)",
            "listed_at": "2026-10-14T17:46:40Z",
            "url": "https://www.linkedin.com/jobs/view/4000000003/",
            "easy_apply": False,
            "reposted": False,
        },
    ]


def test_parse_job_cards_follows_elements_order_not_included_order() -> None:
    ids = [c.job_id for c in parse_job_cards(_fixture("jobs_search_response.json"))]
    assert ids == ["4000000001", "4000000002", "4000000003"]


def test_parse_job_cards_fallback_skips_job_details_stubs() -> None:
    payload = _fixture("jobs_search_response.json")
    payload["data"]["elements"] = []  # shape drift: no element refs
    ids = sorted(c.job_id for c in parse_job_cards(payload))
    assert ids == ["4000000001", "4000000002", "4000000003"]


def test_parse_job_cards_empty_payload() -> None:
    assert parse_job_cards({}) == []
    assert parse_job_cards({"data": {"elements": []}, "included": []}) == []


def test_parse_job_detail_snapshot() -> None:
    detail = parse_job_detail(_fixture("job_detail_response.json"), "4000000001").to_dict()
    assert detail == {
        "job_id": "4000000001",
        "title": "AI Engineer Intern",
        "company": "Acme Analytics",
        "location": "Milano, Lombardia, Italia",
        "description": (
            "Acme Analytics is looking for an AI Engineer Intern.\nYou will build RAG prototypes."
        ),
        "listed_at": "2026-10-16T13:32:07Z",
        "expires_at": "2026-12-18T10:49:13Z",
        "state": "LISTED",
        "closed": False,
        "employment_type": "Stage",
        "experience_level": "Stage",
        "workplace": "Ibrido",
        "remote_allowed": False,
        "applies": 12,
        "easy_apply": False,
        "apply_url": "https://careers.acme-analytics.example/jobs/42",
        "url": "https://www.linkedin.com/jobs/view/4000000001/",
    }


def test_parse_job_detail_closed_and_easy_apply_union() -> None:
    payload = _fixture("job_detail_response.json")
    payload["data"]["jobState"] = "CLOSED"
    payload["data"]["closedAt"] = 1792200000000
    payload["data"]["applyMethod"] = {
        "com.linkedin.voyager.jobs.ComplexOnsiteApply": {"easyApplyUrl": "https://x.example"}
    }
    detail = parse_job_detail(payload, "4000000001")
    assert detail.closed is True
    assert detail.closed_at == "2026-10-17T01:20:00Z"
    assert detail.easy_apply is True
    assert detail.apply_url == ""


def test_parse_geo_hits() -> None:
    hits = parse_geo_hits(_fixture("geo_typeahead_response.json"))
    assert hits[0] == {"geo_id": "100000001", "name": "Milano, Lombardia, Italia"}
    assert len(hits) == 2


# ---------------------------------------------------------------------------
# URL + filters
# ---------------------------------------------------------------------------


def test_build_filters_maps_codes() -> None:
    filters = build_filters(
        experience=["internship", "entry"],
        job_types=["internship", "full-time"],
        date_posted="24h",
        workplace=["remote", "hybrid"],
        sort="recent",
    )
    assert filters == {
        "sortBy": ["DD"],
        "experience": ["1", "2"],
        "jobType": ["I", "F"],
        "timePostedRange": ["r86400"],
        "workplaceType": ["2", "3"],
    }


def test_build_filters_any_date_omits_range() -> None:
    assert "timePostedRange" not in build_filters(date_posted="any")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"experience": ["senior-wizard"]},
        {"job_types": ["gig"]},
        {"date_posted": "yesterday"},
        {"workplace": ["moon"]},
        {"sort": "random"},
    ],
)
def test_build_filters_rejects_unknown_values(kwargs: dict[str, Any]) -> None:
    with pytest.raises(JobsInputError):
        build_filters(**kwargs)


def test_build_search_url_shape() -> None:
    url = build_search_url(
        "data, scientist (AI)",
        geo_id="103350119",
        filters=build_filters(experience=["internship"], date_posted="week"),
        start=25,
        count=25,
    )
    assert url.startswith("https://www.linkedin.com/voyager/api/voyagerJobsDashJobCards?")
    assert "q=jobSearch" in url
    assert "decorationId=com.linkedin.voyager.dash.deco.jobs.search.JobSearchCards" in url
    # Free text is escaped so it can't break the restli tuple.
    assert "keywords:data%2C%20scientist%20%28AI%29" in url
    assert "locationUnion:(geoId:103350119)" in url
    selected = "selectedFilters:(sortBy:List(R),experience:List(1),timePostedRange:List(r604800))"
    assert selected in url
    assert url.endswith("&start=25")
    assert "&count=25" in url


def test_build_search_url_without_keywords_or_geo() -> None:
    url = build_search_url("", geo_id=None, filters={}, start=0, count=10)
    assert "keywords:" not in url
    assert "locationUnion" not in url
    assert "query=(origin:JOB_SEARCH_PAGE_JOB_FILTER,spellCorrectionEnabled:true)" in url


# ---------------------------------------------------------------------------
# search_jobs (fake client)
# ---------------------------------------------------------------------------


def test_search_jobs_one_call_counts_jobs_quota() -> None:
    client = _FakeClient(_fixture("jobs_search_response.json"))
    cards = search_jobs(client, "ai engineer", geo_id="103350119", limit=25)  # type: ignore[arg-type]
    assert len(cards) == 3
    assert len(client.calls) == 1
    assert client.calls[0][1] == ("jobs",)


def test_search_jobs_paginates_until_short_page() -> None:
    full = _fixture("jobs_search_response.json")
    client = _FakeClient(full, full)
    # page size 3 via limit 3 → first page full (3), stop at limit.
    cards = search_jobs(client, "x", limit=3)  # type: ignore[arg-type]
    assert len(cards) == 3
    assert len(client.calls) == 1


def test_search_jobs_stops_on_short_page(monkeypatch) -> None:
    monkeypatch.setattr(jobs, "JOBS_SEARCH_PAGE_SIZE", 2)
    page1 = _fixture("jobs_search_response.json")
    page1["data"]["elements"] = page1["data"]["elements"][:2]
    page2 = _fixture("jobs_search_response.json")
    page2["data"]["elements"] = page2["data"]["elements"][2:]
    client = _FakeClient(page1, page2)
    cards = search_jobs(client, "x", limit=10, start=0)  # type: ignore[arg-type]
    assert [c.job_id for c in cards] == ["4000000001", "4000000002", "4000000003"]
    assert len(client.calls) == 2
    assert client.calls[1][0].endswith("&start=2")


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 101}, {"start": -1}])
def test_search_jobs_rejects_bad_paging(kwargs: dict[str, Any]) -> None:
    client = _FakeClient()
    with pytest.raises(JobsInputError):
        search_jobs(client, "x", **kwargs)  # type: ignore[arg-type]
    assert client.calls == []


def test_search_jobs_rejects_non_numeric_geo() -> None:
    client = _FakeClient()
    with pytest.raises(JobsInputError):
        search_jobs(client, "x", geo_id="Milan")  # type: ignore[arg-type]
    assert client.calls == []


def test_search_jobs_resolves_location_once_then_uses_cache() -> None:
    geo = _fixture("geo_typeahead_response.json")
    page = _fixture("jobs_search_response.json")
    client = _FakeClient(geo, page, page)
    search_jobs(client, "x", location="Milan")  # type: ignore[arg-type]
    search_jobs(client, "x", location="  milan ")  # type: ignore[arg-type]
    urls = [u for u, _ in client.calls]
    assert "typeahead/hitsV2" in urls[0] and "type=GEO" in urls[0]
    assert all("geoId:100000001" in u for u in urls[1:])
    assert len(urls) == 3  # 1 typeahead + 2 searches; second lookup was cached
    assert all(kinds == ("jobs",) for _, kinds in client.calls)


# ---------------------------------------------------------------------------
# geo cache + job ids
# ---------------------------------------------------------------------------


def test_pin_geo_id_is_offline_and_used_by_resolve(_geo_cache: Path) -> None:
    pin_geo_id("Amsterdam", "123456")
    client = _FakeClient()
    out = resolve_geo_id(client, "amsterdam")  # type: ignore[arg-type]
    assert out == {"query": "amsterdam", "geo_id": "123456", "name": "Amsterdam", "cached": True}
    assert client.calls == []
    assert json.loads(_geo_cache.read_text())["amsterdam"]["geo_id"] == "123456"


def test_pin_geo_id_rejects_non_numeric() -> None:
    with pytest.raises(JobsInputError):
        pin_geo_id("Paris", "abc")


def test_resolve_geo_no_hits_raises() -> None:
    client = _FakeClient({"data": {"elements": []}, "included": []})
    with pytest.raises(JobsInputError, match="--geo-id"):
        resolve_geo_id(client, "Atlantis")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("4000000001", "4000000001"),
        ("https://www.linkedin.com/jobs/view/4000000001/", "4000000001"),
        ("https://www.linkedin.com/jobs/view/4000000001/?trk=x", "4000000001"),
        ("urn:li:fsd_jobPosting:4000000001", "4000000001"),
        ("urn:li:fsd_jobPostingCard:(4000000001,JOBS_SEARCH)", "4000000001"),
    ],
)
def test_normalize_job_id(value: str, expected: str) -> None:
    assert normalize_job_id(value) == expected


def test_normalize_job_id_rejects_garbage() -> None:
    with pytest.raises(JobsInputError):
        normalize_job_id("https://www.linkedin.com/in/someone")


def test_get_job_one_call_on_jobs_quota() -> None:
    client = _FakeClient(_fixture("job_detail_response.json"))
    detail = get_job(client, "https://www.linkedin.com/jobs/view/4000000001/")  # type: ignore[arg-type]
    assert detail.title == "AI Engineer Intern"
    assert len(client.calls) == 1
    url, kinds = client.calls[0]
    assert "/jobs/jobPostings/4000000001?decorationId=" in url
    assert kinds == ("jobs",)

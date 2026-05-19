"""Profile lookups against Voyager's identity APIs."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from linkedin_cli.api.client import LinkedInClient
from linkedin_cli.api.endpoints import BASE, PROFILE_BY_USERNAME
from linkedin_cli.models.profile import Profile


def get_profile(client: LinkedInClient, username_or_url: str) -> Profile:
    """Fetch a profile by public id (e.g. `mariorossi`) or full URL."""
    username = _extract_username(username_or_url)
    url = PROFILE_BY_USERNAME.format(username=quote(username, safe=""))
    payload = client.get_json(url)
    return _parse_profile(payload, fallback_username=username)


def _extract_username(value: str) -> str:
    """Accept `johndoe`, `linkedin.com/in/johndoe`, or full URLs."""
    value = value.strip().rstrip("/")
    if "linkedin.com/in/" in value:
        return value.split("linkedin.com/in/", 1)[1].split("/", 1)[0].split("?", 1)[0]
    return value


def _parse_profile(payload: dict[str, Any], fallback_username: str) -> Profile:
    """Normalize Voyager's TopCardSupplementary response into a flat Profile.

    Voyager wraps the actual profile in either `elements[0]` (collection
    responses) or `included[]` (normalized responses). We try both.
    """
    # Collection-style: data.elements[0]
    elements = (payload.get("data") or {}).get("elements") or payload.get("elements") or []
    candidate: dict[str, Any] | None = elements[0] if elements else None

    if candidate is None:
        for entry in payload.get("included", []):
            if "firstName" in entry and "lastName" in entry:
                candidate = entry
                break

    if candidate is None:
        return Profile(public_id=fallback_username)

    public_id = candidate.get("publicIdentifier") or fallback_username
    first_name = _text(candidate.get("firstName"))
    last_name = _text(candidate.get("lastName"))
    profile = Profile(
        profile_id=_first_str(candidate.get("entityUrn"), candidate.get("objectUrn")),
        public_id=public_id,
        first_name=first_name,
        last_name=last_name,
        name=" ".join(p for p in [first_name, last_name] if p).strip(),
        headline=_text(candidate.get("headline")),
        location=_text(candidate.get("locationName")) or _text(candidate.get("geoLocationName")),
        connections_count=_connections_count(candidate, payload),
        profile_url=f"{BASE}/in/{public_id}/",
    )

    # Some responses inline current position under `positionView` or
    # `companyName` / `title`; try a couple of paths.
    position = candidate.get("currentPosition") or {}
    profile.company = _first_str(
        position.get("companyName"),
        candidate.get("currentCompany"),
    )
    profile.title = _first_str(position.get("title"), candidate.get("currentTitle"))
    profile.member_id = _member_id_from_urn(profile.profile_id)
    return profile


def _connections_count(candidate: dict[str, Any], payload: dict[str, Any]) -> int:
    """Pull `connections` (or related count) out of the response.

    LinkedIn exposes connections under several names depending on the
    decoration version: `connections`, `connectionsCount`, or wrapped in
    a `topCardSupplementary` block in `included[]`. We try them in order.
    """
    for key in ("connectionsCount", "connections"):
        value = candidate.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, dict):
            inner = value.get("paging") or value
            if isinstance(inner.get("total"), int):
                return int(inner["total"])
    # Walk included[] for a node that carries a connections counter.
    for entry in payload.get("included", []) or []:
        if not isinstance(entry, dict):
            continue
        for key in ("connectionsCount", "numConnections"):
            if isinstance(entry.get(key), int):
                return int(entry[key])
    return 0


def _text(node: Any) -> str:
    if isinstance(node, dict):
        return str(node.get("text") or "")
    if isinstance(node, str):
        return node
    return ""


def _first_str(*values: Any) -> str:
    for v in values:
        if isinstance(v, str) and v:
            return v
    return ""


_MEMBER_ID_RE = re.compile(r"(?:fsd_profile|member):(\d+)")


def _member_id_from_urn(urn: str) -> str:
    if not urn:
        return ""
    match = _MEMBER_ID_RE.search(urn)
    return match.group(1) if match else ""

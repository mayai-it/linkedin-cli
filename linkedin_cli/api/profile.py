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
    profile = Profile(
        profile_id=_first_str(candidate.get("entityUrn"), candidate.get("objectUrn")),
        public_id=public_id,
        first_name=_text(candidate.get("firstName")),
        last_name=_text(candidate.get("lastName")),
        headline=_text(candidate.get("headline")),
        location=_text(candidate.get("locationName")) or _text(candidate.get("geoLocationName")),
        profile_url=f"{BASE}/in/{public_id}/",
    )

    # Some responses inline current position under `positionView` or
    # `companyName` / `title`; try a couple of paths.
    position = candidate.get("currentPosition") or {}
    profile.company = _first_str(
        position.get("companyName"),
        candidate.get("currentCompany"),
        _text(candidate.get("primaryLocale")),
    )
    profile.title = _first_str(position.get("title"), candidate.get("currentTitle"))
    profile.member_id = _member_id_from_urn(profile.profile_id)
    return profile


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

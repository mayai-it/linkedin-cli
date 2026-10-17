"""Flat dataclasses for job search results and job details."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# Fields kept even when falsy: `easy_apply: false` / `closed: false` carry
# information, unlike an empty string.
_KEEP_FALSY = {"easy_apply", "closed", "reposted", "remote_allowed", "applies"}


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v for k, v in data.items() if v or (k in _KEEP_FALSY and v is not None)
    }


@dataclass
class JobCard:
    job_id: str = ""
    title: str = ""
    company: str = ""
    location: str = ""
    salary: str = ""               # as displayed, when the employer provides it
    listed_at: str = ""            # ISO-8601 UTC, e.g. 2026-10-15T08:00:00Z
    url: str = ""                  # https://www.linkedin.com/jobs/view/<id>/
    easy_apply: bool | None = None  # None = the card didn't say
    reposted: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact(asdict(self))


@dataclass
class JobDetail:
    job_id: str = ""
    title: str = ""
    company: str = ""
    location: str = ""
    description: str = ""
    listed_at: str = ""
    expires_at: str = ""
    closed_at: str = ""
    state: str = ""                # LinkedIn's jobState, e.g. LISTED / CLOSED
    closed: bool = False
    employment_type: str = ""
    experience_level: str = ""
    workplace: str = ""            # e.g. "Hybrid" (localized)
    remote_allowed: bool | None = None
    applies: int | None = None
    easy_apply: bool | None = None
    apply_url: str = ""            # off-site apply URL, when not Easy Apply
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _compact(asdict(self))

"""Lightweight dataclasses for LinkedIn profiles / search hits / messages.

Voyager responses are deeply nested and inconsistent across endpoints, so we
keep the shape flat here — just the fields we actually emit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Profile:
    profile_id: str = ""          # urn:li:fsd_profile:XXXX
    public_id: str = ""           # vanity name from URL
    member_id: str = ""           # numeric urn:li:member:NNNN if known
    first_name: str = ""
    last_name: str = ""
    headline: str = ""
    location: str = ""
    company: str = ""
    title: str = ""
    profile_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v}


@dataclass
class SearchHit:
    profile_id: str = ""
    public_id: str = ""
    name: str = ""
    headline: str = ""
    location: str = ""
    profile_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v}


@dataclass
class Conversation:
    conversation_id: str = ""
    unread_count: int = 0
    last_activity_at: int = 0
    participants: list[dict[str, str]] = field(default_factory=list)
    last_message: str = ""
    last_message_from: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v or k == "unread_count"}

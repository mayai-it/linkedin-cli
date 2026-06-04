"""Smoke tests — pure-function helpers that need no live LinkedIn session."""

from __future__ import annotations

import pytest

from linkedin_cli import __version__
from linkedin_cli.api import LinkedInAPIError
from linkedin_cli.api.messages import _to_member_urn
from linkedin_cli.api.profile import _extract_username
from linkedin_cli.api.quotas import (
    DEFAULT_LIMITS,
    QuotaExceededError,
    QuotaLimits,
    check_and_increment,
    snapshot,
)
from linkedin_cli.auth.credentials import Credentials, normalize_csrf
from linkedin_cli.main import _mask
from linkedin_cli.models.profile import Conversation, Profile, SearchHit

# ---------------------------------------------------------------------------
# Version / packaging
# ---------------------------------------------------------------------------


def test_version_is_exposed() -> None:
    assert __version__ == "0.2.0"


# ---------------------------------------------------------------------------
# CSRF normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('"ajax:1234567890"', "ajax:1234567890"),
        ("ajax:1234567890", "ajax:1234567890"),
        ('  "ajax:42"  ', "ajax:42"),
        ('"', '"'),  # too short to be a quoted pair — left untouched
    ],
)
def test_normalize_csrf_strips_surrounding_quotes(raw: str, expected: str) -> None:
    assert normalize_csrf(raw) == expected


def test_credentials_csrf_token_property() -> None:
    creds = Credentials(li_at="x", jsessionid='"ajax:99"')
    assert creds.csrf_token == "ajax:99"


# ---------------------------------------------------------------------------
# Profile public-id extraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("mario-rossi-9558832a", "mario-rossi-9558832a"),
        ("https://www.linkedin.com/in/mario-rossi-9558832a", "mario-rossi-9558832a"),
        ("https://www.linkedin.com/in/mario-rossi-9558832a/", "mario-rossi-9558832a"),
        ("linkedin.com/in/johndoe?originalSubdomain=it", "johndoe"),
    ],
)
def test_extract_username(value: str, expected: str) -> None:
    assert _extract_username(value) == expected


# ---------------------------------------------------------------------------
# Messaging recipient -> member URN
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("12345678", "urn:li:member:12345678"),
        ("urn:li:member:12345678", "urn:li:member:12345678"),
        ("urn:li:fsd_profile:12345678", "urn:li:member:12345678"),
    ],
)
def test_to_member_urn_accepts_numeric_and_urns(value: str, expected: str) -> None:
    assert _to_member_urn(value) == expected


def test_to_member_urn_rejects_public_id() -> None:
    with pytest.raises(LinkedInAPIError, match="public id"):
        _to_member_urn("mariorossi")


# ---------------------------------------------------------------------------
# Secret masking
# ---------------------------------------------------------------------------


def test_mask_hides_middle() -> None:
    assert _mask("AQEDARxxxxxxYYYY") == "AQED…YYYY"


def test_mask_short_value_fully_hidden() -> None:
    assert _mask("short") == "***"


def test_mask_empty() -> None:
    assert _mask("") == ""


# ---------------------------------------------------------------------------
# Model to_dict() drops empty fields
# ---------------------------------------------------------------------------


def test_profile_to_dict_omits_empty_fields() -> None:
    p = Profile(public_id="johndoe", name="John Doe")
    d = p.to_dict()
    assert d == {"public_id": "johndoe", "name": "John Doe"}
    assert "headline" not in d


def test_search_hit_to_dict_omits_empty_fields() -> None:
    h = SearchHit(public_id="johndoe", name="John Doe")
    assert h.to_dict() == {"public_id": "johndoe", "name": "John Doe"}


def test_conversation_to_dict_keeps_unread_count_zero() -> None:
    c = Conversation(conversation_id="urn:li:msg_conversation:1")
    d = c.to_dict()
    # unread_count is explicitly kept even when 0 (it's meaningful).
    assert d["unread_count"] == 0
    assert d["conversation_id"] == "urn:li:msg_conversation:1"


# ---------------------------------------------------------------------------
# Quotas (offline, tmp-backed)
# ---------------------------------------------------------------------------


def test_quota_increments_then_blocks(tmp_path) -> None:
    path = tmp_path / "quotas.json"
    limits = QuotaLimits(connections=2, messages=25, api_total=200)

    s1 = check_and_increment("connections", limits=limits, path=path)
    assert s1["connections"] == 1
    s2 = check_and_increment("connections", limits=limits, path=path)
    assert s2["connections"] == 2

    with pytest.raises(QuotaExceededError):
        check_and_increment("connections", limits=limits, path=path)


def test_quota_unknown_kind_raises(tmp_path) -> None:
    with pytest.raises(ValueError, match="unknown quota kind"):
        check_and_increment("nope", path=tmp_path / "q.json")


def test_snapshot_reports_defaults(tmp_path) -> None:
    snap = snapshot(path=tmp_path / "missing.json")
    assert snap["connections"]["limit"] == DEFAULT_LIMITS.connections
    assert snap["connections"]["used"] == 0

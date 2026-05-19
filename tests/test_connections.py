"""Tests for connections.send_invitation URN resolution and quota behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from linkedin_cli.api import quotas
from linkedin_cli.api.client import LinkedInAPIError
from linkedin_cli.api.connections import _resolve_profile_urn, send_invitation
from linkedin_cli.models.profile import Profile


class _FakeClient:
    """Minimal stand-in for LinkedInClient that records POST bodies."""

    def __init__(self, throttle: bool = True):
        self.throttle = throttle
        self.posted: list[tuple[str, dict]] = []

    def post_json(self, url, body):
        self.posted.append((url, body))
        return {}


def test_resolve_urn_passes_through_fsd_profile():
    client = _FakeClient()
    urn = "urn:li:fsd_profile:ACoAAA_XYZ"
    assert _resolve_profile_urn(client, urn) == urn


def test_resolve_urn_passes_through_member_urn():
    client = _FakeClient()
    urn = "urn:li:member:123456"
    assert _resolve_profile_urn(client, urn) == urn


def test_resolve_urn_rejects_garbage():
    with pytest.raises(LinkedInAPIError):
        _resolve_profile_urn(_FakeClient(), "https://example.com/some/url")


def test_resolve_urn_looks_up_public_id():
    client = _FakeClient()
    with patch("linkedin_cli.api.profile.get_profile") as mock_get:
        mock_get.return_value = Profile(profile_id="urn:li:fsd_profile:ACoAAA_LOOKED_UP")
        urn = _resolve_profile_urn(client, "daniele-giovanardi-282462390")
    assert urn == "urn:li:fsd_profile:ACoAAA_LOOKED_UP"


def test_send_invitation_increments_quota(tmp_path: Path, monkeypatch):
    quota_file = tmp_path / "quotas.json"
    monkeypatch.setattr(quotas, "QUOTAS_PATH", quota_file)
    client = _FakeClient(throttle=True)
    urn = "urn:li:fsd_profile:ACoAAA_NEW"
    result = send_invitation(client, urn)
    assert result == {"status": "ok", "profile": urn}
    assert client.posted, "POST should have happened"
    snap = quotas.snapshot(path=quota_file)
    assert snap["connections"]["used"] == 1


def test_send_invitation_respects_no_throttle(tmp_path: Path, monkeypatch):
    quota_file = tmp_path / "quotas.json"
    monkeypatch.setattr(quotas, "QUOTAS_PATH", quota_file)
    client = _FakeClient(throttle=False)
    send_invitation(client, "urn:li:fsd_profile:ACoAAA_NEW")
    snap = quotas.snapshot(path=quota_file)
    # With throttle disabled we must not consume the user's daily quota.
    assert snap["connections"]["used"] == 0

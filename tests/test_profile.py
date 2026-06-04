"""Tests for profile parsing and input normalization."""

from __future__ import annotations

import pytest

from linkedin_cli.api.profile import _extract_username, _parse_profile


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("daniele-giovanardi-282462390", "daniele-giovanardi-282462390"),
        (
            "https://www.linkedin.com/in/daniele-giovanardi-282462390",
            "daniele-giovanardi-282462390",
        ),
        (
            "https://www.linkedin.com/in/daniele-giovanardi-282462390/",
            "daniele-giovanardi-282462390",
        ),
        (
            "https://www.linkedin.com/in/daniele-giovanardi-282462390?utm_source=share",
            "daniele-giovanardi-282462390",
        ),
        ("linkedin.com/in/some-user/", "some-user"),
    ],
)
def test_extract_username_handles_urls(raw: str, expected: str):
    assert _extract_username(raw) == expected


def test_parse_profile_top_card_supplementary():
    """Mimics the shape Voyager returns for the TopCardSupplementary
    decoration — `data.elements[0]` carries the profile fields, including
    connectionsCount."""
    payload = {
        "data": {
            "elements": [
                {
                    "entityUrn": "urn:li:fsd_profile:ACoAAA_12345",
                    "publicIdentifier": "mario-rossi-1",
                    "firstName": {"text": "Mario"},
                    "lastName": {"text": "Rossi"},
                    "headline": {"text": "CTO at MayAI"},
                    "locationName": {"text": "Milan, Italy"},
                    "connectionsCount": 487,
                }
            ]
        },
        "included": [],
    }
    profile = _parse_profile(payload, fallback_username="mario-rossi-1")
    d = profile.to_dict()
    assert d["profile_id"] == "urn:li:fsd_profile:ACoAAA_12345"
    assert d["public_id"] == "mario-rossi-1"
    assert d["name"] == "Mario Rossi"
    assert d["headline"] == "CTO at MayAI"
    assert d["location"] == "Milan, Italy"
    assert d["connections_count"] == 487
    assert d["profile_url"] == "https://www.linkedin.com/in/mario-rossi-1/"


def test_parse_profile_falls_back_to_included():
    payload = {
        "data": {},
        "included": [
            {
                "entityUrn": "urn:li:fsd_profile:ACoAAB_99999",
                "publicIdentifier": "anna-bianchi",
                "firstName": {"text": "Anna"},
                "lastName": {"text": "Bianchi"},
                "headline": {"text": "CEO"},
            }
        ],
    }
    profile = _parse_profile(payload, fallback_username="anna-bianchi")
    assert profile.public_id == "anna-bianchi"
    assert profile.name == "Anna Bianchi"


def test_parse_profile_empty_returns_fallback_only():
    profile = _parse_profile({"data": {}, "included": []}, fallback_username="ghost")
    assert profile.public_id == "ghost"
    assert profile.profile_id == ""

"""Snapshot tests for the people-search parser.

The parser walks Voyager's normalized JSON: clusters → SearchItems →
composite EntityResult URNs → inner Profile records in `included[]`. The
fixture in `fixtures/search_people_response.json` is a minimal payload that
exercises that full chain end-to-end without hitting the network.
"""

from __future__ import annotations

import json
from pathlib import Path

from linkedin_cli.api.search import _parse_people_hits

FIXTURE = Path(__file__).parent / "fixtures" / "search_people_response.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_search_people_snapshot():
    payload = _load_fixture()
    hits = [h.to_dict() for h in _parse_people_hits(payload)]

    assert hits == [
        {
            "profile_id": "urn:li:fsd_profile:ACoAAA_MARIO_ROSSI_XYZ",
            "public_id": "mario-rossi-9558832a",
            "name": "Mario Rossi",
            "headline": "CTO at MayAI",
            "profile_url": "https://www.linkedin.com/in/mario-rossi-9558832a",
        },
        {
            "profile_id": "urn:li:fsd_profile:ACoAAB_ANNA_BIANCHI_PQR",
            "public_id": "annabianchi",
            "name": "Anna Bianchi",
            "headline": "CEO @ Example",
            "profile_url": "https://www.linkedin.com/in/annabianchi",
        },
        {
            "profile_id": "urn:li:fsd_profile:ACoAAC_LUIGI_VERDI_MNO",
            "public_id": "luigi-verdi-123",
            "name": "Luigi Verdi",
            "headline": "Senior Software Engineer",
            "profile_url": "https://www.linkedin.com/in/luigi-verdi-123",
        },
    ]


def test_search_people_strips_query_string_from_profile_url():
    """The miniProfileUrn query parameter must be stripped from profile_url."""
    payload = _load_fixture()
    hits = _parse_people_hits(payload)
    for hit in hits:
        assert "?" not in hit.profile_url
        assert "miniProfileUrn" not in hit.profile_url


def test_search_people_uses_inner_profile_urn_not_composite():
    """profile_id must be the inner fsd_profile URN, not the composite."""
    payload = _load_fixture()
    hits = _parse_people_hits(payload)
    for hit in hits:
        assert hit.profile_id.startswith("urn:li:fsd_profile:"), hit.profile_id
        assert "fsd_entityResultViewModel" not in hit.profile_id
        assert "SEARCH_SRP" not in hit.profile_id


def test_search_people_synthesizes_url_when_navigation_missing():
    """The third hit's fixture has no navigationUrl; we must build it from public_id."""
    payload = _load_fixture()
    hits = _parse_people_hits(payload)
    luigi = next(h for h in hits if h.public_id == "luigi-verdi-123")
    assert luigi.profile_url == "https://www.linkedin.com/in/luigi-verdi-123"

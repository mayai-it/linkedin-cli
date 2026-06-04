"""MCP tool tests.

The ``@mcp.tool()`` decorator registers the function with FastMCP but returns
the original callable unchanged — we exercise the tool logic by calling those
functions directly with a hand-rolled Context whose
``request_context.lifespan_context`` exposes a real ``AppContext``. The
underlying API functions are monkeypatched on the ``mcp_server`` module, so no
HTTP request and no Playwright browser is ever touched.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from linkedin_cli import mcp_server
from linkedin_cli.api import LinkedInAPIError
from linkedin_cli.auth import Credentials
from linkedin_cli.mcp_server import (
    AppContext,
    _reset_session_write_log,
    linkedin_auth_status,
    linkedin_connections_list,
    linkedin_connections_pending,
    linkedin_connections_send,
    linkedin_messages_list,
    linkedin_messages_send,
    linkedin_profile_get,
    linkedin_search_companies,
    linkedin_search_people,
)
from linkedin_cli.models.profile import Conversation, Profile, SearchHit

CREDS = Credentials(li_at="x", jsessionid='"ajax:test"', member_urn="urn:li:fsd_profile:ME")


def _ctx(client: Any = object(), creds: Credentials | None = CREDS) -> Any:
    """Build the minimal Context surface the tools actually read."""
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=AppContext(creds=creds, client=client),
        ),
    )


@pytest.fixture(autouse=True)
def _clean_write_log() -> None:
    """Isolate the module-level per-session write log between tests."""
    _reset_session_write_log()


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


def test_profile_get_returns_dict(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_server,
        "get_profile",
        lambda client, u: Profile(public_id="johndoe", name="John Doe", headline="CTO"),
    )
    out = linkedin_profile_get(_ctx(), username_or_url="johndoe")
    assert out["name"] == "John Doe"
    assert out["headline"] == "CTO"


def test_search_people_applies_limit(monkeypatch) -> None:
    hits = [SearchHit(public_id=f"p{i}", name=f"P {i}") for i in range(5)]
    monkeypatch.setattr(
        mcp_server, "search_people", lambda client, q, company=None, title=None: hits
    )
    out = linkedin_search_people(_ctx(), query="x", limit=2)
    assert len(out) == 2
    assert out[0]["public_id"] == "p0"


def test_search_people_passes_filters(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def _fake(client, q, company=None, title=None):
        captured.update(query=q, company=company, title=title)
        return []

    monkeypatch.setattr(mcp_server, "search_people", _fake)
    linkedin_search_people(_ctx(), query="eng", company="MayAI", title="Senior")
    assert captured == {"query": "eng", "company": "MayAI", "title": "Senior"}


def test_search_companies_applies_limit(monkeypatch) -> None:
    rows = [{"name": f"C{i}"} for i in range(3)]
    monkeypatch.setattr(mcp_server, "search_companies", lambda client, q: rows)
    out = linkedin_search_companies(_ctx(), query="x", limit=2)
    assert len(out) == 2


def test_connections_list_passthrough(monkeypatch) -> None:
    rows = [{"connection_urn": "urn:li:fsd_connection:1", "connected_at": "2025-09-14"}]
    monkeypatch.setattr(mcp_server, "list_connections", lambda client, limit=40: rows)
    assert linkedin_connections_list(_ctx(), limit=10) == rows


def test_connections_pending_passthrough(monkeypatch) -> None:
    rows = [{"from_name": "Mario Rossi", "invitation_id": "urn:li:invitation:1"}]
    monkeypatch.setattr(mcp_server, "list_pending_invitations", lambda client: rows)
    assert linkedin_connections_pending(_ctx()) == rows


def test_messages_list_uses_member_urn(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def _fake(client, urn):
        captured["urn"] = urn
        return [Conversation(conversation_id="urn:li:msg_conversation:1", unread_count=2)]

    monkeypatch.setattr(mcp_server, "list_conversations", _fake)
    out = linkedin_messages_list(_ctx())
    assert captured["urn"] == "urn:li:fsd_profile:ME"
    assert out[0]["unread_count"] == 2


# ---------------------------------------------------------------------------
# auth_status
# ---------------------------------------------------------------------------


def test_auth_status_authenticated() -> None:
    out = linkedin_auth_status(_ctx())
    assert out["authenticated"] is True
    assert out["member_urn"] == "urn:li:fsd_profile:ME"


def test_auth_status_not_authenticated() -> None:
    out = linkedin_auth_status(_ctx(client=None, creds=None))
    assert out == {"authenticated": False}


# ---------------------------------------------------------------------------
# Error / auth guards
# ---------------------------------------------------------------------------


def test_read_tools_raise_when_unauthenticated() -> None:
    ctx = _ctx(client=None, creds=None)
    with pytest.raises(ToolError, match="not authenticated"):
        linkedin_profile_get(ctx, username_or_url="johndoe")
    with pytest.raises(ToolError, match="not authenticated"):
        linkedin_connections_list(ctx)


def test_api_error_translated_to_tool_error(monkeypatch) -> None:
    def _boom(client, u):
        raise LinkedInAPIError("rate limited by LinkedIn", status=429)

    monkeypatch.setattr(mcp_server, "get_profile", _boom)
    with pytest.raises(ToolError, match="rate limited"):
        linkedin_profile_get(_ctx(), username_or_url="johndoe")


# ---------------------------------------------------------------------------
# Write tool: connections_send
# ---------------------------------------------------------------------------


def test_connections_send_dry_run_does_not_call_api(monkeypatch) -> None:
    called = False

    def _send(client, pid):
        nonlocal called
        called = True
        return {"status": "ok"}

    monkeypatch.setattr(mcp_server, "send_invitation", _send)
    out = linkedin_connections_send(_ctx(), profile_id="johndoe", dry_run=True)
    assert out["status"] == "dry-run"
    assert called is False


def test_connections_send_requires_confirm(monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "send_invitation", lambda c, p: {"status": "ok"})
    with pytest.raises(ToolError, match="confirm=True"):
        linkedin_connections_send(_ctx(), profile_id="johndoe")


def test_connections_send_empty_profile_raises() -> None:
    with pytest.raises(ToolError, match="must not be empty"):
        linkedin_connections_send(_ctx(), profile_id="   ", confirm=True)


def test_connections_send_confirmed_calls_api(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_server, "send_invitation", lambda c, p: {"status": "ok", "profile": p}
    )
    out = linkedin_connections_send(_ctx(), profile_id="urn:li:member:1", confirm=True)
    assert out == {"status": "ok", "profile": "urn:li:member:1"}


def test_connections_send_rate_limited(monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "send_invitation", lambda c, p: {"status": "ok"})
    for _ in range(mcp_server._RATE_LIMIT_MAX):
        linkedin_connections_send(_ctx(), profile_id="johndoe", confirm=True)
    with pytest.raises(ToolError, match="Rate limit"):
        linkedin_connections_send(_ctx(), profile_id="johndoe", confirm=True)


# ---------------------------------------------------------------------------
# Write tool: messages_send
# ---------------------------------------------------------------------------


def test_messages_send_dry_run_does_not_call_api(monkeypatch) -> None:
    called = False

    def _send(client, r, t):
        nonlocal called
        called = True
        return {"status": "ok"}

    monkeypatch.setattr(mcp_server, "send_message", _send)
    out = linkedin_messages_send(_ctx(), recipient="12345678", text="ciao", dry_run=True)
    assert out["status"] == "dry-run"
    assert out["length"] == 4
    assert called is False


def test_messages_send_requires_confirm(monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "send_message", lambda c, r, t: {"status": "ok"})
    with pytest.raises(ToolError, match="confirm=True"):
        linkedin_messages_send(_ctx(), recipient="12345678", text="ciao")


def test_messages_send_empty_text_raises() -> None:
    with pytest.raises(ToolError, match="`text` must not be empty"):
        linkedin_messages_send(_ctx(), recipient="12345678", text="  ", confirm=True)


def test_messages_send_confirmed_calls_api(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_server,
        "send_message",
        lambda c, r, t: {"status": "ok", "recipient": r, "conversation_id": "urn:li:c:1"},
    )
    out = linkedin_messages_send(
        _ctx(), recipient="urn:li:member:1", text="ciao", confirm=True
    )
    assert out["status"] == "ok"
    assert out["recipient"] == "urn:li:member:1"


def test_write_tools_raise_when_unauthenticated(monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "send_invitation", lambda c, p: {"status": "ok"})
    monkeypatch.setattr(mcp_server, "send_message", lambda c, r, t: {"status": "ok"})
    ctx = _ctx(client=None, creds=None)
    with pytest.raises(ToolError, match="not authenticated"):
        linkedin_connections_send(ctx, profile_id="johndoe", confirm=True)
    with pytest.raises(ToolError, match="not authenticated"):
        linkedin_messages_send(ctx, recipient="12345678", text="ciao", confirm=True)

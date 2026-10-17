"""MCP server exposing linkedin-cli as native tools for AI agents.

Runs over stdio. A single :class:`~linkedin_cli.api.client.LinkedInClient` is
created once at startup and shared by all tools via the FastMCP lifespan
context. Cookies are loaded from the same on-disk store the CLI uses
(`linkedin auth login`); the server refuses to start if no session is present.

CRITICAL: never write to stdout — the stdio transport reserves it for the MCP
protocol. All diagnostics go to stderr.

Safety posture mirrors the rest of the MayAI CLI suite: read tools are free to
call (the two job tools are bounded by the 30/day `jobs` quota), but the two
*write* tools (`linkedin_connections_send`, `linkedin_messages_send`) require
an explicit ``confirm=True`` from the caller, support ``dry_run=True`` for
validation only, and are rate-limited per session on top of the on-disk daily
quotas the underlying API layer already enforces.
"""

from __future__ import annotations

import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.session import ServerSession

from linkedin_cli.api import LinkedInAPIError, LinkedInBlockedError, LinkedInClient
from linkedin_cli.api.connections import (
    list_connections,
    list_pending_invitations,
    send_invitation,
)
from linkedin_cli.api.jobs import JobsInputError, get_job, search_jobs
from linkedin_cli.api.messages import list_conversations, send_message
from linkedin_cli.api.profile import get_profile
from linkedin_cli.api.search import search_companies, search_people
from linkedin_cli.auth import Credentials, load_credentials

# In-process write log used to rate-limit the two write tools within a single
# MCP session. Each entry: {"kind": str, "target": str, "at": float (monotonic)}.
# Module-level on purpose — one MCP session = one process, so this is the right
# scope. Reset for tests via _reset_session_write_log().
_SESSION_WRITE_LOG: list[dict[str, Any]] = []
_RATE_LIMIT_WINDOW_SEC = 300  # 5 minutes
_RATE_LIMIT_MAX = 5           # max writes of one kind to one target per window


def _reset_session_write_log() -> None:
    """Test hook — clears the in-process write log."""
    _SESSION_WRITE_LOG.clear()


@dataclass
class AppContext:
    creds: Credentials | None
    client: LinkedInClient | None


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    try:
        creds = load_credentials()
    except RuntimeError as exc:
        sys.stderr.write(f"linkedin-mcp: credentials unreadable: {exc}\n")
        raise SystemExit(2) from exc
    if creds is None:
        sys.stderr.write(
            "linkedin-mcp: not authenticated — run `linkedin auth login` first\n"
        )
        raise SystemExit(2)

    # throttle=True keeps the jittered inter-request delay AND the daily
    # quota checks active — an MCP agent should behave at least as carefully
    # as a human at the CLI.
    client = LinkedInClient(creds, verbose=False, throttle=True)
    masked = creds.member_urn or "(member urn not detected)"
    sys.stderr.write(f"linkedin-mcp: ready for {masked}\n")
    try:
        yield AppContext(creds=creds, client=client)
    finally:
        client.close()


mcp = FastMCP("linkedin-cli", lifespan=app_lifespan)


def _app(ctx: Context[ServerSession, AppContext]) -> AppContext:
    return ctx.request_context.lifespan_context


def _require(
    ctx: Context[ServerSession, AppContext],
) -> tuple[Credentials, LinkedInClient]:
    """Return the live credentials + client, or raise a ToolError.

    The lifespan refuses to start unauthenticated, so in production this never
    raises — but tools guard anyway so they can be unit-tested with a stub
    context that carries ``client=None``.
    """
    app = _app(ctx)
    if app.client is None or app.creds is None:
        raise ToolError("not authenticated — run `linkedin auth login` first")
    return app.creds, app.client


def _check_rate_limit(kind: str, target: str) -> None:
    """Raise ToolError if `kind`/`target` exceeded the per-session window."""
    now = time.monotonic()
    _SESSION_WRITE_LOG[:] = [
        e for e in _SESSION_WRITE_LOG if now - e["at"] < _RATE_LIMIT_WINDOW_SEC
    ]
    recent = [e for e in _SESSION_WRITE_LOG if e["kind"] == kind and e["target"] == target]
    if len(recent) >= _RATE_LIMIT_MAX:
        raise ToolError(
            f"Rate limit: {_RATE_LIMIT_MAX} {kind} actions to {target} in the "
            f"last {_RATE_LIMIT_WINDOW_SEC // 60} minutes. Aborting to prevent "
            "accidental duplicates and to keep the account under LinkedIn's "
            "anti-abuse radar."
        )


def _record_write(kind: str, target: str) -> None:
    _SESSION_WRITE_LOG.append({"kind": kind, "target": target, "at": time.monotonic()})


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


@mcp.tool()
def linkedin_profile_get(
    ctx: Context[ServerSession, AppContext],
    username_or_url: str,
) -> dict[str, Any]:
    """Fetch a single LinkedIn profile by public id or full URL.

    Args:
        username_or_url: A vanity public id (e.g. `mario-rossi-9558832a`) or a
            full profile URL (`https://www.linkedin.com/in/mario-rossi-9558832a`).

    Returns:
        A dict with the profile's name, headline, location, connections count,
        the canonical `profile_id` URN, the numeric `member_id`, and
        `profile_url`. Empty fields are omitted.
    """
    _, client = _require(ctx)
    try:
        profile = get_profile(client, username_or_url)
    except LinkedInAPIError as exc:
        raise ToolError(str(exc)) from exc
    return profile.to_dict()


@mcp.tool()
def linkedin_search_people(
    ctx: Context[ServerSession, AppContext],
    query: str,
    company: str | None = None,
    title: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search LinkedIn for people by name, role, or keyword.

    Args:
        query: Free-text query (name and/or keywords).
        company: Optional company name; folded into the keyword string so
            LinkedIn restricts to people associated with it.
        title: Optional job title; folded into the keyword string.
        limit: Maximum number of hits to return (default 10).

    Returns:
        One dict per person with `profile_id`, `public_id`, `name`,
        `headline`, `location`, and `profile_url`.
    """
    _, client = _require(ctx)
    try:
        hits = search_people(client, query, company=company, title=title)
    except LinkedInAPIError as exc:
        raise ToolError(str(exc)) from exc
    rows = [h.to_dict() for h in hits]
    return rows[:limit] if limit and limit > 0 else rows


@mcp.tool()
def linkedin_search_companies(
    ctx: Context[ServerSession, AppContext],
    query: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search LinkedIn for companies by name.

    Args:
        query: Company name or keyword.
        limit: Maximum number of hits to return (default 10).

    Returns:
        One dict per company hit (name, url, and any other fields the
        search-clusters endpoint returns).
    """
    _, client = _require(ctx)
    try:
        hits = search_companies(client, query)
    except LinkedInAPIError as exc:
        raise ToolError(str(exc)) from exc
    return hits[:limit] if limit and limit > 0 else hits


@mcp.tool()
def linkedin_connections_list(
    ctx: Context[ServerSession, AppContext],
    limit: int = 40,
) -> list[dict[str, Any]]:
    """List your first-degree connections, newest first.

    The rows are intentionally lean — LinkedIn's connections endpoint does not
    inline profile names, so each row carries `connection_urn` and
    `connected_at`. Use `linkedin_profile_get` when you need the full profile.

    Args:
        limit: Maximum number of connections to return (default 40).
    """
    _, client = _require(ctx)
    try:
        return list_connections(client, limit=limit)
    except LinkedInAPIError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def linkedin_connections_pending(
    ctx: Context[ServerSession, AppContext],
) -> list[dict[str, Any]]:
    """List incoming connection invitations awaiting your response.

    Returns:
        One dict per pending invitation with the inviter's name, headline,
        public id, the invitation id, and the message (if any).
    """
    _, client = _require(ctx)
    try:
        return list_pending_invitations(client)
    except LinkedInAPIError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def linkedin_messages_list(
    ctx: Context[ServerSession, AppContext],
) -> list[dict[str, Any]]:
    """List your latest LinkedIn conversations.

    Returns:
        One dict per conversation with `conversation_id`, `unread_count`,
        `last_activity_at`, the `participants`, and the last message preview.
    """
    creds, client = _require(ctx)
    try:
        convs = list_conversations(client, creds.member_urn)
    except LinkedInAPIError as exc:
        raise ToolError(str(exc)) from exc
    return [c.to_dict() for c in convs]


def _jobs_error(exc: Exception) -> ToolError:
    if isinstance(exc, LinkedInBlockedError):
        return ToolError(
            f"blocked by LinkedIn ({exc.reason}): {exc}. Stop all LinkedIn calls; "
            "do NOT retry. The user must open linkedin.com in a browser and complete "
            "the verification, then run `linkedin auth login`."
        )
    return ToolError(str(exc))


@mcp.tool()
def linkedin_jobs_search(
    ctx: Context[ServerSession, AppContext],
    keywords: str = "",
    location: str | None = None,
    geo_id: str | None = None,
    experience: list[str] | None = None,
    job_types: list[str] | None = None,
    date_posted: str = "week",
    workplace: list[str] | None = None,
    sort: str = "recent",
    limit: int = 25,
    start: int = 0,
) -> list[dict[str, Any]]:
    """Search LinkedIn job postings (read-only).

    Each page of up to 25 results is one call against the daily `jobs` quota
    (30/day at most) — prefer one well-filtered search over many small ones.

    Args:
        keywords: Free-text query, e.g. "data scientist".
        location: Location name ("Milan, Italy"); resolved to a geoId and cached.
            The first lookup of a new name costs one extra call.
        geo_id: LinkedIn geoId; takes precedence over `location`.
        experience: Any of internship, entry, associate, mid-senior, director, executive.
        job_types: Any of full-time, part-time, contract, temporary, internship,
            volunteer, other.
        date_posted: 24h, week (default), month or any.
        workplace: Any of onsite, remote, hybrid.
        sort: recent (default) or relevant.
        limit: Max postings (1-100, default 25).
        start: Offset for pagination.

    Returns:
        One dict per posting: `job_id`, `title`, `company`, `location`,
        `listed_at` (ISO UTC), `url` (https://www.linkedin.com/jobs/view/<id>/),
        and `easy_apply` when the card says.
    """
    _, client = _require(ctx)
    try:
        cards = search_jobs(
            client,
            keywords,
            location=location,
            geo_id=geo_id,
            experience=experience or (),
            job_types=job_types or (),
            date_posted=date_posted,
            workplace=workplace or (),
            sort=sort,
            limit=limit,
            start=start,
        )
    except (LinkedInAPIError, JobsInputError) as exc:
        raise _jobs_error(exc) from exc
    return [c.to_dict() for c in cards]


@mcp.tool()
def linkedin_jobs_get(
    ctx: Context[ServerSession, AppContext],
    job: str,
) -> dict[str, Any]:
    """Fetch one job posting's description, apply method and open/closed state.

    One call against the daily `jobs` quota.

    Args:
        job: A numeric job id or a https://www.linkedin.com/jobs/view/<id>/ URL.
    """
    _, client = _require(ctx)
    try:
        return get_job(client, job).to_dict()
    except (LinkedInAPIError, JobsInputError) as exc:
        raise _jobs_error(exc) from exc


@mcp.tool()
def linkedin_auth_status(
    ctx: Context[ServerSession, AppContext],
) -> dict[str, Any]:
    """Report whether this server holds a valid LinkedIn session.

    Returns:
        Dict with `authenticated` and (when authenticated) the captured
        `member_urn`. If this tool returns `authenticated: True` the server is
        bound to a session by construction — it would have refused to start
        otherwise.
    """
    app = _app(ctx)
    if app.client is None or app.creds is None:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "member_urn": app.creds.member_urn or "(not detected)",
    }


# ---------------------------------------------------------------------------
# Write tools (gated)
# ---------------------------------------------------------------------------


@mcp.tool()
def linkedin_connections_send(
    ctx: Context[ServerSession, AppContext],
    profile_id: str,
    confirm: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Send a connection request to a profile.

    ⚠️ This performs a real, user-visible action and counts against the daily
    connections quota (15/day). The agent MUST obtain explicit user consent and
    set `confirm=True` before calling. Use `dry_run=True` to validate without
    sending.

    Args:
        profile_id: A public id (e.g. `mario-rossi-9558832a`) or an
            `urn:li:fsd_profile:…` / `urn:li:member:…` URN. Public ids cost one
            extra lookup to resolve to a URN.
        confirm: Must be True to actually send. The user — not the agent —
            should make this decision.
        dry_run: If True, return what would happen without contacting LinkedIn.

    Returns:
        `{"status": "ok", "profile": <urn>}` on success, `"already-invited"` if
        LinkedIn replies 409, or `{"status": "dry-run", ...}` in dry-run mode.
    """
    if not profile_id.strip():
        raise ToolError("`profile_id` must not be empty")

    if dry_run:
        return {"status": "dry-run", "profile": profile_id, "validated": True}

    if not confirm:
        raise ToolError(
            "Sending a connection request is a real action that the recipient "
            "sees and that counts against your daily quota. Set confirm=True to "
            "proceed — this should be confirmed by the user, not the agent "
            "autonomously."
        )

    _check_rate_limit("connection", profile_id)
    _, client = _require(ctx)
    try:
        result = send_invitation(client, profile_id)
    except LinkedInAPIError as exc:
        raise ToolError(str(exc)) from exc
    _record_write("connection", profile_id)
    return result


@mcp.tool()
def linkedin_messages_send(
    ctx: Context[ServerSession, AppContext],
    recipient: str,
    text: str,
    confirm: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Send a 1:1 LinkedIn message.

    ⚠️ This sends a real message and counts against the daily messages quota
    (25/day). The agent MUST obtain explicit user consent and set `confirm=True`
    before calling. Use `dry_run=True` to validate without sending.

    Args:
        recipient: A numeric member id or an `urn:li:member:…` URN. A public id
            is NOT accepted here — resolve it first with `linkedin_profile_get`
            and use the returned `member_id`.
        text: The message body (must not be empty).
        confirm: Must be True to actually send. The user — not the agent —
            should make this decision.
        dry_run: If True, return what would happen without contacting LinkedIn.

    Returns:
        `{"status": "ok", "recipient": <urn>, "conversation_id": …}` on success,
        or `{"status": "dry-run", ...}` in dry-run mode.
    """
    if not recipient.strip():
        raise ToolError("`recipient` must not be empty")
    if not text.strip():
        raise ToolError("`text` must not be empty")

    if dry_run:
        return {
            "status": "dry-run",
            "recipient": recipient,
            "length": len(text),
            "validated": True,
        }

    if not confirm:
        raise ToolError(
            "Sending a message is a real action the recipient sees. Set "
            "confirm=True to proceed — this should be confirmed by the user, "
            "not the agent autonomously."
        )

    _check_rate_limit("message", recipient)
    _, client = _require(ctx)
    try:
        result = send_message(client, recipient, text)
    except LinkedInAPIError as exc:
        raise ToolError(str(exc)) from exc
    _record_write("message", recipient)
    return result


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

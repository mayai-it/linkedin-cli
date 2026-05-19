"""Connections list, pending invitations, and outbound invitation sending."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from linkedin_cli.api.client import LinkedInAPIError, LinkedInClient, _tracking_id
from linkedin_cli.api.endpoints import (
    CONNECTIONS_LIST,
    INVITATIONS_PENDING,
    INVITATIONS_SEND,
)
from linkedin_cli.api.quotas import QuotaExceededError, check_and_increment


def list_connections(client: LinkedInClient, limit: int = 40) -> list[dict[str, Any]]:
    """Return up to `limit` first-degree connections, newest first."""
    out: list[dict[str, Any]] = []
    start = 0
    page = min(40, limit) if limit > 0 else 40

    while True:
        url = CONNECTIONS_LIST.format(start=start, count=page)
        payload = client.get_json(url)
        included_index = _build_included_index(payload)

        # Normalized JSON: `*elements` holds Connection URNs; the actual
        # Connection objects (with `connectedMember` + `createdAt`) are in
        # `included[]`. Some response shapes nest the collection under
        # `data` instead of putting it at the top level.
        container = payload.get("data") or payload
        refs = container.get("*elements")
        if isinstance(refs, list):
            connection_objs = [included_index.get(urn, {}) for urn in refs if isinstance(urn, str)]
        else:
            connection_objs = container.get("elements") or []

        # Last-ditch: scan included[] for Connection $type entries.
        if not connection_objs:
            connection_objs = [
                entry
                for entry in payload.get("included", []) or []
                if "Connection" in str(entry.get("$type", ""))
                and entry.get("connectedMember")
            ]

        if not connection_objs:
            break
        for conn in connection_objs:
            row = _format_connection(conn, included_index)
            if row:
                out.append(row)
            if 0 < limit <= len(out):
                return out
        if len(connection_objs) < page:
            break
        start += page

    return out


def _build_included_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index `included[]` by entityUrn so the Connection-list code can find
    the Connection objects the top-level `*elements` array refers to.
    (Profile objects aren't in included[] for this endpoint — see comment in
    `_format_connection`.)
    """
    index: dict[str, dict[str, Any]] = {}
    for entry in payload.get("included", []) or []:
        if not isinstance(entry, dict):
            continue
        urn = entry.get("entityUrn")
        if isinstance(urn, str) and urn:
            index.setdefault(urn, entry)
    return index


def list_pending_invitations(client: LinkedInClient) -> list[dict[str, Any]]:
    payload = client.get_json(INVITATIONS_PENDING)
    elements = payload.get("elements", []) or (payload.get("data") or {}).get("elements", [])
    out: list[dict[str, Any]] = []
    for element in elements:
        invitation = element.get("invitation") or element
        from_member = invitation.get("fromMember") or {}
        out.append(
            {
                "invitation_id": (
                    invitation.get("entityUrn") or invitation.get("invitationId") or ""
                ),
                "shared_secret": invitation.get("sharedSecret", ""),
                "from_name": _join_name(
                    from_member.get("firstName"),
                    from_member.get("lastName"),
                ),
                "from_headline": from_member.get("occupation", ""),
                "from_public_id": from_member.get("publicIdentifier", ""),
                "sent_at": invitation.get("sentTime", 0),
                "message": _text(invitation.get("message")),
            }
        )
    return out


def send_invitation(client: LinkedInClient, profile_id: str) -> dict[str, Any]:
    """Send a connection request to a profile.

    `profile_id` accepts a bare public id (e.g. `daniele-giovanardi-282462390`),
    an `urn:li:fsd_profile:...` URN, or a `urn:li:member:...` URN. Public ids
    are resolved to an fsd_profile URN via `get_profile` before we POST.
    The daily `connections` quota is checked before the network call and only
    consumed when LinkedIn accepts the request.
    """
    profile_urn = _resolve_profile_urn(client, profile_id)

    if client.throttle:
        try:
            check_and_increment("connections")
        except QuotaExceededError as exc:
            raise LinkedInAPIError(str(exc)) from exc

    body = {
        "trackingId": _tracking_id(),
        "invitations": [],
        "excludeInvitations": [],
        "invitee": {
            "com.linkedin.voyager.growth.invitation.InviteeProfile": {
                "profileId": profile_urn,
            }
        },
    }
    try:
        client.post_json(INVITATIONS_SEND, body)
    except LinkedInAPIError as exc:
        if exc.status == 409:
            return {"status": "already-invited", "profile": profile_urn}
        raise
    return {"status": "ok", "profile": profile_urn}


def _resolve_profile_urn(client: LinkedInClient, value: str) -> str:
    """Return the `urn:li:fsd_profile:...` form for a public id or URN.

    If `value` already looks like a URN, just normalize. Otherwise we treat
    it as a public id and look it up via `get_profile`, which costs one
    extra API request (also counted against the api_total quota).
    """
    value = value.strip()
    if value.startswith("urn:li:fsd_profile:") or value.startswith("urn:li:member:"):
        return value
    # Heuristic: a bare ASCII alnum/dash/underscore token that isn't already a
    # URN is a public id (e.g. `daniele-giovanardi-282462390`). Anything else
    # is invalid.
    if not _BARE_ID_RE.match(value):
        raise LinkedInAPIError(f"invalid profile id: {value!r}")

    # Import here to avoid a circular import at module load.
    from linkedin_cli.api.profile import get_profile

    profile = get_profile(client, value)
    if not profile.profile_id:
        raise LinkedInAPIError(
            f"could not resolve public id {value!r} to a profile URN — "
            "check the spelling or pass the URN directly"
        )
    return profile.profile_id


def _format_connection(
    element: dict[str, Any],
    included: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Format a Connection record.

    Voyager's `/relationships/dash/connections` endpoint does *not* include
    profile objects in `included[]` — only the Connection records themselves
    (entityUrn + connectedMember URN + createdAt). Resolving name/headline
    would require a separate `/identity/dash/profiles?...&memberIdentity=...`
    request per connection, which we don't want to do implicitly. Callers
    that need the full profile can run `linkedin profile get <public-id>`.
    """
    return {
        "connection_urn": element.get("entityUrn", ""),
        "connected_at": _format_timestamp_ms(int(element.get("createdAt") or 0)),
    }


def _format_timestamp_ms(ms: int) -> str:
    if not ms:
        return ""
    try:
        return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")
    except (ValueError, OverflowError, OSError):
        return ""


def _text(node: Any) -> str:
    if isinstance(node, dict):
        return str(node.get("text") or "")
    if isinstance(node, str):
        return node
    return ""


def _join_name(first: Any, last: Any) -> str:
    a = _text(first) or (first if isinstance(first, str) else "")
    b = _text(last) or (last if isinstance(last, str) else "")
    return " ".join(part for part in [a, b] if part).strip()


_BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

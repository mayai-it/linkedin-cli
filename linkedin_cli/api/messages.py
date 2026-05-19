"""Messaging: list conversations and send 1:1 messages."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from linkedin_cli.api.client import LinkedInAPIError, LinkedInClient
from linkedin_cli.api.endpoints import (
    CONVERSATIONS_GRAPHQL,
    CONVERSATIONS_QUERY_ID,
    MESSAGES_SEND,
)
from linkedin_cli.models.profile import Conversation


def list_conversations(client: LinkedInClient, profile_urn: str) -> list[Conversation]:
    if not profile_urn:
        raise LinkedInAPIError(
            "missing member URN — re-run `linkedin auth login` so the CLI can capture it"
        )

    mailbox = quote(profile_urn, safe="")
    url = (
        f"{CONVERSATIONS_GRAPHQL}"
        f"?queryId={CONVERSATIONS_QUERY_ID}"
        f"&variables=(mailboxUrn:{mailbox})"
    )
    payload = client.get_json(url)

    # Voyager's GraphQL responses can wrap the payload as either
    # `{data: {<query>: {...}}}` or `{data: {data: {<query>: {...}}}}` —
    # the inner `data` shows up when the response goes through the REST
    # envelope. We try both before falling back to scanning included[].
    data = payload.get("data") or {}
    inner = data.get("data") if isinstance(data.get("data"), dict) else None
    sources = [data, inner] if inner else [data]

    container: dict[str, Any] = {}
    for src in sources:
        container = (
            src.get("messengerConversationsBySyncToken")
            or src.get("messengerConversationsBySyncTokenV2")
            or src.get("messengerConversations")
            or {}
        )
        if container:
            break

    # Build the lookup table for normalized JSON: top-level `included[]`
    # holds every entity keyed by its `entityUrn`. The container's
    # `*elements` field is just a list of URN strings pointing into it.
    included_index = _build_included_index(payload)

    refs = container.get("*elements") or []
    if refs:
        elements = [included_index.get(urn, {}) for urn in refs if isinstance(urn, str)]
    else:
        # Older / non-normalized response: data is inlined.
        elements = container.get("elements") or []

    # Last-ditch fallback: scan included[] for $type containing
    # "Conversation". Drops any entry that's actually a participant or
    # message body — those also live in included[] but with different types.
    if not elements:
        elements = [
            entry
            for entry in payload.get("included", []) or []
            if "Conversation" in str(entry.get("$type", ""))
            and ("conversationParticipants" in entry or "*conversationParticipants" in entry)
        ]

    return [_parse_conversation(el, included_index) for el in elements if el]


def _build_included_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for entry in payload.get("included", []) or []:
        urn = entry.get("entityUrn")
        if isinstance(urn, str):
            index[urn] = entry
    return index


def send_message(client: LinkedInClient, recipient: str, text: str) -> dict[str, Any]:
    """Send a 1:1 message to a recipient by member id or URN."""
    member_urn = _to_member_urn(recipient)
    body = {
        "recipients": {
            "com.linkedin.voyager.messaging.MessagingMemberRecipients": {
                "values": [member_urn],
            }
        },
        "subtype": "MEMBER_TO_MEMBER",
        "body": text,
    }
    response = client.post_json(MESSAGES_SEND, body)
    return {
        "status": "ok",
        "recipient": member_urn,
        "conversation_id": (response.get("value") or {}).get("conversationUrn", ""),
    }


def _parse_conversation(
    element: dict[str, Any],
    included: dict[str, dict[str, Any]],
) -> Conversation:
    def resolve(node: Any) -> Any:
        """Follow a URN string into `included[]`; passthrough for dicts."""
        if isinstance(node, str) and node in included:
            return included[node]
        return node

    def collection(parent: dict[str, Any], name: str) -> list[Any]:
        """Read `*name` (URN-ref list) or `name` (inline list) — whichever exists."""
        refs = parent.get(f"*{name}")
        if isinstance(refs, list):
            return [resolve(r) for r in refs]
        return parent.get(name) or []

    participants = []
    for p in collection(element, "conversationParticipants"):
        if not isinstance(p, dict):
            continue
        member = resolve((p.get("participantType") or {}).get("member"))
        if not isinstance(member, dict):
            member = {}
        participants.append(
            {
                "name": _join_name(member.get("firstName"), member.get("lastName")),
                "headline": _text(member.get("headline")),
                "profile_url": member.get("profileUrl", ""),
            }
        )

    last_msg = ""
    last_from = ""
    messages_node = resolve(element.get("messages")) if "messages" in element else element
    messages = collection(messages_node, "elements") if isinstance(messages_node, dict) else []
    if not messages:
        # Some shapes hang messages directly off the element under `*messages`.
        messages = collection(element, "messages")
    if messages:
        first = messages[0] if isinstance(messages[0], dict) else resolve(messages[0])
        if isinstance(first, dict):
            last_msg = _text(first.get("body"))
            sender = resolve(first.get("sender"))
            sender_member = resolve((sender or {}).get("participantType", {}).get("member"))
            if isinstance(sender_member, dict):
                last_from = _join_name(
                    sender_member.get("firstName"), sender_member.get("lastName")
                )

    return Conversation(
        conversation_id=element.get("backendUrn") or element.get("entityUrn") or "",
        unread_count=int(element.get("unreadCount") or 0),
        last_activity_at=int(element.get("lastActivityAt") or 0),
        participants=participants,
        last_message=last_msg,
        last_message_from=last_from,
    )


_BARE_DIGITS = re.compile(r"^\d+$")
_BARE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _to_member_urn(value: str) -> str:
    value = value.strip()
    if value.startswith("urn:li:member:"):
        return value
    if value.startswith("urn:li:fsd_profile:"):
        # Some callers only have the profile URN — Voyager accepts the
        # numeric tail as a member id.
        tail = value.rsplit(":", 1)[-1]
        if _BARE_DIGITS.match(tail):
            return f"urn:li:member:{tail}"
    if _BARE_DIGITS.match(value):
        return f"urn:li:member:{value}"
    if _BARE_ID.match(value):
        # Public id like `mariorossi` — we can't resolve it to a numeric
        # member id without an extra round-trip; surface that to the caller.
        raise LinkedInAPIError(
            f"recipient {value!r} looks like a public id — pass the numeric member id "
            "or `urn:li:member:N` URN instead (you can find it via `linkedin profile get`)"
        )
    raise LinkedInAPIError(f"invalid recipient: {value!r}")


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

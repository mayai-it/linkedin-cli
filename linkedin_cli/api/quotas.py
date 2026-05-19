"""Daily quota tracking for LinkedIn API usage.

LinkedIn's anti-abuse heuristics get suspicious when a single account makes
too many of certain actions per day. We enforce per-account caps locally so
the CLI behaves like a normal human user. State lives in
~/.config/mayai-cli/linkedin/quotas.json and resets at midnight (local date).
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import date
from pathlib import Path

QUOTAS_PATH = Path.home() / ".config" / "mayai-cli" / "linkedin" / "quotas.json"


class QuotaExceededError(RuntimeError):
    def __init__(self, kind: str, used: int, limit: int) -> None:
        super().__init__(
            f"daily quota exceeded for {kind}: {used}/{limit} — "
            f"try again tomorrow or pass --no-throttle (at your own risk)"
        )
        self.kind = kind
        self.used = used
        self.limit = limit


@dataclass
class QuotaLimits:
    connections: int = 15
    messages: int = 25
    api_total: int = 200


DEFAULT_LIMITS = QuotaLimits()


def _today() -> str:
    return date.today().isoformat()


def _resolve_path(path: Path | None) -> Path:
    """Late-bind QUOTAS_PATH so tests can monkeypatch the module-level value."""
    if path is not None:
        return path
    import linkedin_cli.api.quotas as _self
    return _self.QUOTAS_PATH


def _load(path: Path | None = None) -> dict[str, int | str]:
    path = _resolve_path(path)
    if not path.exists():
        return {"date": _today(), "connections": 0, "messages": 0, "api_total": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"date": _today(), "connections": 0, "messages": 0, "api_total": 0}
    if data.get("date") != _today():
        return {"date": _today(), "connections": 0, "messages": 0, "api_total": 0}
    data.setdefault("connections", 0)
    data.setdefault("messages", 0)
    data.setdefault("api_total", 0)
    return data


def _save(state: dict[str, int | str], path: Path | None = None) -> None:
    path = _resolve_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    tmp.replace(path)


def check_and_increment(
    kind: str,
    limits: QuotaLimits = DEFAULT_LIMITS,
    path: Path | None = None,
) -> dict[str, int | str]:
    """Atomically check the quota for `kind` and increment if there's room.

    Raises QuotaExceededError if the limit has been hit. `kind` must be one
    of "connections", "messages", or "api_total".
    """
    limit = getattr(limits, kind, None)
    if limit is None:
        raise ValueError(f"unknown quota kind: {kind!r}")
    state = _load(path)
    used = int(state.get(kind, 0) or 0)
    if used >= limit:
        raise QuotaExceededError(kind, used, limit)
    state[kind] = used + 1
    _save(state, path)
    return state


def snapshot(
    limits: QuotaLimits = DEFAULT_LIMITS,
    path: Path | None = None,
) -> dict[str, dict[str, int] | str]:
    """Return current usage vs limits for inspection."""
    state = _load(path)
    return {
        "date": str(state.get("date", _today())),
        "connections": {"used": int(state.get("connections", 0) or 0), "limit": limits.connections},
        "messages": {"used": int(state.get("messages", 0) or 0), "limit": limits.messages},
        "api_total": {"used": int(state.get("api_total", 0) or 0), "limit": limits.api_total},
    }


__all__ = [
    "DEFAULT_LIMITS",
    "QUOTAS_PATH",
    "QuotaExceededError",
    "QuotaLimits",
    "check_and_increment",
    "snapshot",
]

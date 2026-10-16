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
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

QUOTAS_PATH = Path.home() / ".config" / "mayai-cli" / "linkedin" / "quotas.json"

# Job search is the most "scrape-like" thing the CLI does, so it gets its own,
# much tighter budget on top of `api_total`. 30 is a hard ceiling: the env var
# below can only lower it, never raise it.
JOBS_DAILY_MAX = 30
JOBS_LIMIT_ENV = "LINKEDIN_JOBS_DAILY_LIMIT"

KINDS = ("connections", "messages", "api_total", "jobs")


def jobs_daily_limit() -> int:
    """Return the effective jobs quota: `LINKEDIN_JOBS_DAILY_LIMIT`, clamped to 0..30."""
    raw = os.environ.get(JOBS_LIMIT_ENV, "").strip()
    if not raw:
        return JOBS_DAILY_MAX
    try:
        value = int(raw)
    except ValueError:
        return JOBS_DAILY_MAX
    return max(0, min(value, JOBS_DAILY_MAX))


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
    jobs: int = field(default_factory=jobs_daily_limit)


DEFAULT_LIMITS = QuotaLimits()


def _today() -> str:
    return date.today().isoformat()


def _resolve_path(path: Path | None) -> Path:
    """Late-bind QUOTAS_PATH so tests can monkeypatch the module-level value."""
    if path is not None:
        return path
    import linkedin_cli.api.quotas as _self
    return _self.QUOTAS_PATH


def _fresh() -> dict[str, int | str]:
    state: dict[str, int | str] = {"date": _today()}
    for kind in KINDS:
        state[kind] = 0
    return state


def _load(path: Path | None = None) -> dict[str, int | str]:
    path = _resolve_path(path)
    if not path.exists():
        return _fresh()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _fresh()
    if not isinstance(data, dict) or data.get("date") != _today():
        return _fresh()
    for kind in KINDS:
        data.setdefault(kind, 0)
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
    of "connections", "messages", "api_total" or "jobs".
    """
    return check_and_increment_many((kind,), limits, path)


def check_and_increment_many(
    kinds: Iterable[str],
    limits: QuotaLimits = DEFAULT_LIMITS,
    path: Path | None = None,
) -> dict[str, int | str]:
    """Check several quotas at once and increment them all, or none.

    A job search counts against both `jobs` and `api_total`: if either is
    exhausted nothing is consumed, so a refused call never burns budget.
    """
    kinds = tuple(dict.fromkeys(kinds))
    state = _load(path)
    for kind in kinds:
        limit = getattr(limits, kind, None) if kind in KINDS else None
        if limit is None:
            raise ValueError(f"unknown quota kind: {kind!r}")
        used = int(state.get(kind, 0) or 0)
        if used >= limit:
            raise QuotaExceededError(kind, used, limit)
    for kind in kinds:
        state[kind] = int(state.get(kind, 0) or 0) + 1
    _save(state, path)
    return state


def snapshot(
    limits: QuotaLimits = DEFAULT_LIMITS,
    path: Path | None = None,
) -> dict[str, dict[str, int] | str]:
    """Return current usage vs limits for inspection."""
    state = _load(path)
    out: dict[str, dict[str, int] | str] = {"date": str(state.get("date", _today()))}
    for kind in KINDS:
        out[kind] = {"used": int(state.get(kind, 0) or 0), "limit": getattr(limits, kind)}
    return out


__all__ = [
    "DEFAULT_LIMITS",
    "JOBS_DAILY_MAX",
    "JOBS_LIMIT_ENV",
    "KINDS",
    "QUOTAS_PATH",
    "QuotaExceededError",
    "QuotaLimits",
    "check_and_increment",
    "check_and_increment_many",
    "jobs_daily_limit",
    "snapshot",
]

"""Tests for the daily quota tracker."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from linkedin_cli.api.quotas import (
    DEFAULT_LIMITS,
    QuotaExceededError,
    QuotaLimits,
    check_and_increment,
    snapshot,
)


def _path(tmp_path: Path) -> Path:
    return tmp_path / "quotas.json"


def test_check_and_increment_starts_at_zero(tmp_path: Path):
    path = _path(tmp_path)
    state = check_and_increment("connections", DEFAULT_LIMITS, path)
    assert state["connections"] == 1
    assert state["date"] == date.today().isoformat()


def test_quota_blocks_when_exceeded(tmp_path: Path):
    path = _path(tmp_path)
    limits = QuotaLimits(connections=2, messages=2, api_total=2)
    check_and_increment("connections", limits, path)
    check_and_increment("connections", limits, path)
    with pytest.raises(QuotaExceededError) as exc_info:
        check_and_increment("connections", limits, path)
    assert exc_info.value.kind == "connections"
    assert exc_info.value.used == 2
    assert exc_info.value.limit == 2


def test_quota_resets_at_midnight(tmp_path: Path):
    path = _path(tmp_path)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"date": yesterday, "connections": 99, "messages": 99, "api_total": 99}),
        encoding="utf-8",
    )
    # Even with `connections` already over the limit yesterday, today should
    # see a fresh counter starting at 0 → 1.
    state = check_and_increment("connections", QuotaLimits(connections=1), path)
    assert state["connections"] == 1
    assert state["date"] == date.today().isoformat()


def test_quota_kinds_are_independent(tmp_path: Path):
    path = _path(tmp_path)
    limits = QuotaLimits(connections=1, messages=1, api_total=10)
    check_and_increment("connections", limits, path)
    # `messages` should still have room even though `connections` is full.
    state = check_and_increment("messages", limits, path)
    assert state["messages"] == 1
    with pytest.raises(QuotaExceededError):
        check_and_increment("connections", limits, path)


def test_snapshot_reports_usage(tmp_path: Path):
    path = _path(tmp_path)
    limits = QuotaLimits(connections=5, messages=10, api_total=20)
    check_and_increment("api_total", limits, path)
    check_and_increment("api_total", limits, path)
    snap = snapshot(limits, path)
    assert snap["api_total"] == {"used": 2, "limit": 20}
    assert snap["connections"] == {"used": 0, "limit": 5}


def test_unknown_kind_raises(tmp_path: Path):
    with pytest.raises(ValueError):
        check_and_increment("unknown", DEFAULT_LIMITS, _path(tmp_path))


def test_corrupt_quota_file_recovers(tmp_path: Path):
    path = _path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json at all", encoding="utf-8")
    state = check_and_increment("messages", DEFAULT_LIMITS, path)
    assert state["messages"] == 1

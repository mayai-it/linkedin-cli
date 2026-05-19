from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

from linkedin_cli.output.formatter import emit


def test_emit_json_object():
    buf = io.StringIO()
    with redirect_stdout(buf):
        emit({"a": 1, "b": None, "c": "x"}, as_json=True)
    parsed = json.loads(buf.getvalue().strip())
    # JSON mode preserves all fields, including null
    assert parsed == {"a": 1, "b": None, "c": "x"}


def test_emit_ndjson_list():
    buf = io.StringIO()
    with redirect_stdout(buf):
        emit([{"a": 1}, {"a": 2}], as_json=True)
    lines = [json.loads(line) for line in buf.getvalue().splitlines()]
    assert lines == [{"a": 1}, {"a": 2}]


def test_emit_human_strips_empty():
    buf = io.StringIO()
    with redirect_stdout(buf):
        emit({"a": 1, "b": None, "c": ""}, as_json=False)
    out = buf.getvalue()
    assert "a: 1" in out
    assert "b:" not in out
    assert "c:" not in out

"""Output helpers used across commands.

- `emit(data, as_json)`: NDJSON on --json, compact human text otherwise.
- `error(msg)`: always writes to stderr.

Human formatting drops empty / null fields to stay context-efficient
for AI agents that pipe the output back into a prompt.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _strip_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_empty(v) for k, v in value.items() if not _is_empty(v)}
    if isinstance(value, list):
        return [_strip_empty(v) for v in value if not _is_empty(v)]
    return value


def _human(value: Any, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            if _is_empty(v):
                continue
            if isinstance(v, (dict, list)):
                lines.append(f"{pad}{k}:")
                lines.append(_human(v, indent + 1))
            else:
                lines.append(f"{pad}{k}: {v}")
        return "\n".join(lines)
    if isinstance(value, list):
        return "\n".join(_human(item, indent) for item in value if not _is_empty(item))
    return f"{pad}{value}"


def emit(data: Any, as_json: bool = False) -> None:
    """Print `data` to stdout in the requested format.

    For lists in --json mode we use NDJSON (one object per line).
    """
    if as_json:
        if isinstance(data, list):
            for item in data:
                sys.stdout.write(json.dumps(item, ensure_ascii=False, default=str))
                sys.stdout.write("\n")
        else:
            sys.stdout.write(json.dumps(data, ensure_ascii=False, default=str))
            sys.stdout.write("\n")
        return

    cleaned = _strip_empty(data)
    sys.stdout.write(_human(cleaned))
    sys.stdout.write("\n")


def error(message: str) -> None:
    sys.stderr.write(f"error: {message}\n")

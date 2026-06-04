# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.0] — 2026-06-04

### Added
- **Native MCP server** (`linkedin_cli/mcp_server.py`), exposed as the
  `linkedin-mcp` console entry point. Runs over stdio (FastMCP) and lets MCP
  clients like Claude Desktop drive LinkedIn directly — no subprocess, no JSON
  parsing. One shared `LinkedInClient` is created in the server lifespan; the
  server refuses to start (exit code `2`) if no session is stored. Tools:
  - Read: `linkedin_profile_get`, `linkedin_search_people`,
    `linkedin_search_companies`, `linkedin_connections_list`,
    `linkedin_connections_pending`, `linkedin_messages_list`,
    `linkedin_auth_status`.
  - Write (gated): `linkedin_connections_send`, `linkedin_messages_send` —
    each requires an explicit `confirm=True`, supports `dry_run=True`, and is
    rate-limited per session (5 actions per target per 5 minutes) on top of the
    existing on-disk daily quotas.
- `mcp>=1.2.0` runtime dependency; `pytest-cov` and `mypy` dev dependencies.
- Tests: 28 → 72, all offline (mocked HTTP, no browser, no live LinkedIn
  traffic). New `tests/test_mcp_server.py` (21) and `tests/test_smoke.py` (23);
  the MCP server module sits at ~80% branch coverage. Coverage now tracked in CI.
- Italian `README.it.md`, a `docs/` folder (AUTHENTICATION, FAQ), CONTRIBUTING.md,
  and GitHub issue templates (bug report + feature request).

### Changed
- Project now ships **two** entry points: `linkedin` (CLI) and `linkedin-mcp`
  (MCP server). README restructured with badge header, an MCP section, and a
  Quality bar.
- CI expanded to a 3-OS × 3-Python matrix (Ubuntu / macOS / Windows × 3.11 /
  3.12 / 3.13) plus a dedicated `pip-audit` job. `ruff` now also lints `tests/`,
  and `mypy linkedin_cli/` is blocking.
- `pyproject.toml`: added `[tool.mypy]` (pragmatic baseline), `[tool.coverage]`,
  and an `sdist` target.

### Security
- `starlette>=1.0.1` pinned directly in project dependencies (PYSEC-2026-161
  affects `starlette<1.0.1`, transitively pulled in by the `mcp` SDK with a
  loose `>=0.27` constraint).
- Write tools never act without an explicit `confirm=True` from the caller — an
  agent cannot send a connection request or a message autonomously.

## [0.1.0] — 2026-05-19

Initial release. See PyPI
[`mayai-linkedin-cli==0.1.0`](https://pypi.org/project/mayai-linkedin-cli/0.1.0/).

- CLI for LinkedIn's internal Voyager API, cookie-based auth captured from a
  real browser via Playwright (`linkedin auth login`).
- Commands: `profile get`, `search people`, `search companies`,
  `connections list`, `connections pending`, `connections send`,
  `messages list`, `messages send`, plus `auth login | status | logout`.
- Normalized-JSON / URN-graph parser with live `queryId` discovery for
  people search.
- Jittered request throttling and per-account daily quotas
  (`connections`, `messages`, `api_total`), Fernet-encrypted credential store.

# Contributing to linkedin-cli

Thanks for your interest. This project is small enough that bug reports
with reproduction steps are as welcome as PRs.

## Setup

```bash
git clone https://github.com/mayai-it/linkedin-cli.git
cd linkedin-cli
python -m venv .venv && source .venv/bin/activate
make dev    # pip install -e ".[dev]" + playwright install chromium
make test   # pytest
make lint   # ruff
make typecheck   # mypy linkedin_cli/
```

You only need a real LinkedIn account if you're working on the live Voyager
paths. The test suite runs fully offline against mocks — see "Testing
discipline" below.

## PR checklist

Before opening a PR, please confirm:

- [ ] `ruff check linkedin_cli/ tests/` is clean.
- [ ] `mypy linkedin_cli/` reports `Success: no issues found`.
- [ ] `pytest tests/` is fully green.
- [ ] Coverage on the touched module is not lower than `main`. Run
      `pytest --cov=linkedin_cli --cov-report=term tests/` and check the row.
- [ ] If you added a public-facing change, the PR description includes a
      `Changelog` line ready to drop into `CHANGELOG.md` under the next version.
- [ ] No new `# type: ignore` without a specific error code and a one-line
      motivation in the surrounding comment.
- [ ] No real LinkedIn cookies (`li_at`, `JSESSIONID`), member URNs from a real
      account, or scraped private message bodies in the diff or test fixtures.

## Commit convention

We follow the loose [Conventional
Commits](https://www.conventionalcommits.org/) pattern. A few prefixes cover
most changes:

| Prefix | When |
|---|---|
| `feat:` | New user-visible behavior |
| `fix:` | Bug fix |
| `docs:` | README / docs / changelog only |
| `test:` | Test changes only |
| `refactor:` | Code restructure with no behavior change |
| `chore:` | Tooling, deps, CI |
| `security:` | Hardening, vuln fix |

Subject line ≤ 72 chars. Body is optional; when present, explain *why*, not
*what* — the diff already shows the what.

## Testing discipline

**Never send real LinkedIn traffic from tests.** Connection requests and
messages are user-visible actions that count against daily quotas and feed
LinkedIn's anti-abuse heuristics. An accidental live `send` from a test fixture
is a real action you can't take back.

Patterns we enforce in the existing suite:

- The HTTP layer (`LinkedInClient`) is exercised through `unittest.mock` —
  no real socket ever opens, no Playwright browser ever launches.
- The MCP tool tests call the tool functions directly with a hand-rolled
  context (`SimpleNamespace`) whose `request_context.lifespan_context` carries a
  mocked client — the FastMCP server lifespan is never started.
- Write tools (`linkedin_connections_send`, `linkedin_messages_send`) are tested
  through their `dry_run=True` and `confirm` gates so no test path can reach a
  real POST.
- Fixtures that build a `Credentials` object use obviously-fake cookie values
  (e.g. `li_at="x"`, `jsessionid='"ajax:test"'`).

If you genuinely need to verify against the live API, do it manually from your
own shell on a non-primary account — don't commit a test that does it.

## A note on the Voyager API

The endpoints, headers, `queryId` hashes, and response shapes here are
reverse-engineered and undocumented; LinkedIn changes them without notice. When
you fix a breakage:

- Capture the new shape with `--verbose` (it dumps a body preview) and add a
  fixture under `tests/fixtures/` so the parser change is covered.
- Note the date and the web `clientVersion` you verified against in the relevant
  module (see the header comment in `linkedin_cli/api/endpoints.py`).

## Reporting bugs

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md). Include the
linkedin-cli version (`linkedin --version`), OS + Python version, the exact
command with `--verbose`, and whether you used `--json`. **Redact cookie values
and private message contents.**

## Proposing features

Use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.md).
Lead with the use case — "what are you trying to do" beats "implement X" — and
we'll figure out the shape together.

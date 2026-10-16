[![PyPI version](https://img.shields.io/pypi/v/mayai-linkedin-cli.svg)](https://pypi.org/project/mayai-linkedin-cli/)
[![Python versions](https://img.shields.io/pypi/pyversions/mayai-linkedin-cli.svg)](https://pypi.org/project/mayai-linkedin-cli/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Built for AI agents](https://img.shields.io/badge/Built%20for-AI%20agents-purple)](https://mayai.it)
[![Tests](https://github.com/mayai-it/linkedin-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/mayai-it/linkedin-cli/actions/workflows/ci.yml)
[![mypy](https://img.shields.io/badge/mypy-checked-blue)](https://mypy-lang.org/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-yellow.svg)](https://conventionalcommits.org)

> Italiano: [README.it.md](README.it.md) — versione ridotta.

> [!WARNING]
> This tool uses LinkedIn's internal Voyager API, which is not publicly documented
> and is not officially supported by LinkedIn. Usage may violate LinkedIn's
> [Terms of Service (Section 8.2)](https://www.linkedin.com/legal/user-agreement).
> LinkedIn may temporarily or permanently restrict your account.
> **Use at your own risk, preferably on a non-primary account.**

# linkedin-cli

Command-line client **and MCP server** for **LinkedIn**, driving the internal
Voyager API the same way the website does. Built for both humans and AI agents:
context-efficient defaults, NDJSON output for piping into LLMs or `jq`, a native
MCP server for tools like Claude Desktop, and no API key — just session cookies
captured from a real browser.

Part of [MayAI CLI](https://mayai.it).

## Quality bar

- **Cross-platform CI**: Ubuntu / macOS / Windows × Python 3.11 / 3.12 / 3.13,
  plus a `pip-audit` job — via GitHub Actions.
- **`ruff` + blocking `mypy`** on every push; **134 tests** run fully offline
  against mocks (no live LinkedIn traffic, no browser launch).
- **Two surfaces, one core**: the `linkedin` CLI and the `linkedin-mcp` server
  share the same Voyager client, throttle, and daily-quota safety net.

## Requirements

- Python 3.11+
- A LinkedIn account
- Chromium (installed automatically by `make install` via Playwright)

## Installation

From [PyPI](https://pypi.org/project/mayai-linkedin-cli/) (recommended):

```bash
pip install mayai-linkedin-cli
playwright install chromium
```

The second command downloads the Chromium binary that Playwright drives
during `linkedin auth login`. The CLI is exposed as the `linkedin`
command on your `$PATH`.

From source:

```bash
git clone https://github.com/mayai-it/linkedin-cli.git
cd linkedin-cli
make install
```

The `make install` target installs the package in editable mode and
runs `playwright install chromium`.

For local development (adds `pytest`, `ruff`, `mypy`):

```bash
make dev
```

## MCP Server

linkedin-cli ships with a native MCP server, letting AI agents like Claude
access your LinkedIn directly — no subprocess, no JSON parsing. It runs over
stdio and reuses the same session cookies, throttle, and daily quotas as the
CLI; it refuses to start (exit code `2`) until you've run `linkedin auth login`.

### Setup with Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "linkedin": {
      "command": "/path/to/linkedin-mcp"
    }
  }
}
```

Find your path with `which linkedin-mcp`.

### Compatible MCP clients

| Client | Status |
|--------|--------|
| Claude Desktop | Tested |
| Cursor | Compatible (same stdio config) |
| Continue (VS Code) | Compatible (same stdio config) |
| Zed | Compatible (same stdio config) |

### Available tools

| Tool | Description |
|------|-------------|
| `linkedin_profile_get` | Fetch a profile by public id or URL |
| `linkedin_search_people` | Search people by name / role (`company`, `title`, `limit`) |
| `linkedin_search_companies` | Search companies by name |
| `linkedin_connections_list` | List first-degree connections, newest first |
| `linkedin_connections_pending` | List incoming invitations |
| `linkedin_messages_list` | List latest conversations |
| `linkedin_jobs_search` | Search job postings (keywords, location/geoId, experience, job type, date posted, workplace, paging) — read-only, counts against the `jobs` quota |
| `linkedin_jobs_get` | One posting's description, apply method and open/closed state — read-only, `jobs` quota |
| `linkedin_connections_send` | Send a connection request (requires `confirm=True`) |
| `linkedin_messages_send` | Send a 1:1 message (requires `confirm=True`) |
| `linkedin_auth_status` | Check authentication status |

The two write tools refuse to act without an explicit `confirm=True`, support
`dry_run=True`, and are rate-limited per session on top of the daily quotas —
see the [Safety note](#safety-note-on-write-actions) below.

## Quick start

```bash
# 1. Authenticate — opens a Chromium window for you to sign in normally.
#    Captures the session cookies once login is complete.
linkedin auth login

# 2. Verify
linkedin auth status

# 3. Find someone
linkedin --json search people "Mario Rossi"

# 4. Read their profile by public id (or full URL)
linkedin --json profile get mario-rossi-9558832a
linkedin --json profile get https://www.linkedin.com/in/mario-rossi-9558832a

# 5. Send a connection request (dry-run first if you want to see the payload)
linkedin connections send mario-rossi-9558832a --dry-run
linkedin connections send mario-rossi-9558832a

# 6. Latest 40 first-degree connections
linkedin --json connections list

# 7. Read the most recent conversations
linkedin --json messages list

# 8. Send a 1:1 message (member id from `profile get`)
linkedin messages send 12345678 "Ciao Mario, parliamo?"

# 9. Internship / entry-level jobs posted in the last week, newest first
linkedin --json jobs search "data scientist" --geo-id 103350119 \
  --experience internship --experience entry --date-posted week --sort recent

# 10. Full description + open/closed state of one posting
linkedin --json jobs get https://www.linkedin.com/jobs/view/4000000001/

# 11. Today's quota usage (local file, no network)
linkedin --json quota
```

## Command reference

| Command | Description |
|---|---|
| `linkedin auth login [--headless] [--timeout S]` | Open Chromium, wait for the user to sign in, capture `li_at` + `JSESSIONID`, resolve the user's own `urn:li:fsd_profile:…`. |
| `linkedin auth status` | Show whether a session is stored (masked) and which member URN was captured. |
| `linkedin auth logout` | Delete saved cookies + encryption key. |
| `linkedin profile get <username-or-url>` | Fetch a single profile by vanity public id (e.g. `mario-rossi-9558832a`) or full URL (`https://www.linkedin.com/in/…`). Returns name, headline, location, connections count, and `profile_url`. |
| `linkedin search people <query> [--company N] [--title R]` | People search; `--company` / `--title` fold into the keyword string. |
| `linkedin search companies <query>` | Companies search via the REST search-clusters endpoint. |
| `linkedin connections list [--limit N]` | First-degree connections, newest first. |
| `linkedin connections pending` | Incoming connection requests awaiting response. |
| `linkedin connections send <profile-id> [--dry-run]` | Send a connection request. `<profile-id>` accepts a public id (resolved to a URN via an extra `profile get` call) or an `urn:li:fsd_profile:…` / `urn:li:member:…` URN. Increments the daily connections quota only on a successful POST. |
| `linkedin messages list` | Latest conversations from the inbox. |
| `linkedin messages send <recipient> <text> [--dry-run]` | Send a 1:1 message. `recipient` is a numeric member id or `urn:li:member:N`. |
| `linkedin jobs search [KEYWORDS] [options]` | Job search. Options: `--location NAME` or `--geo-id ID`; `--experience internship\|entry\|associate\|mid-senior\|director\|executive` (repeatable); `--job-type full-time\|part-time\|contract\|temporary\|internship\|volunteer\|other` (repeatable); `--date-posted 24h\|week\|month\|any`; `--workplace onsite\|remote\|hybrid` (repeatable) or `--remote`; `--sort relevant\|recent`; `--limit N` (1–100, 25 per call) and `--start N` for paging. |
| `linkedin jobs get <job-id-or-url>` | One posting: description, employment type, experience level, workplace, listed/expiry/closed dates, `closed` flag, Easy Apply vs off-site `apply_url`. |
| `linkedin jobs geo <name> [--set GEO_ID]` | Resolve a location name to a geoId via the GEO typeahead (cached on disk), or pin one yourself with `--set` (no network). |
| `linkedin quota` | Today's local quota usage per kind. No network call. |

### Global flags

These work in any position (before or after the subcommand):

| Flag | Effect |
|---|---|
| `--json` | Emit one JSON object per line (NDJSON). |
| `--verbose` | Log request URL, status, timing, and a body preview to stderr. |
| `--no-throttle` | Skip the jittered inter-request delay **and** daily quota checks. Use at your own risk — this is the flag most likely to get an account flagged. |
| `-h`, `--help` | Show help for the current command. |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Application error (network, search 500, bad arguments) |
| `2` | Not authenticated, or session expired — run `linkedin auth login` |
| `3` | **Blocked by LinkedIn**: security checkpoint / challenge redirect, captcha page, HTTP 429 or HTTP 999. The CLI never retries. Stop, open linkedin.com in a browser, complete the verification, then `linkedin auth login`. The stderr line starts with `error: blocked (<reason>):`, reason one of `checkpoint`, `captcha`, `rate_limited`, `denied`. |
| `4` | A local daily quota is exhausted (nothing was sent). Try again tomorrow. |

### Safety note on write actions

Connection requests and messages are real, user-visible actions that count
against daily quotas and feed LinkedIn's anti-abuse heuristics. To avoid
accidents:

- **CLI**: `connections send` and `messages send` accept `--dry-run` to print
  the intended action without contacting LinkedIn.
- **MCP**: the `linkedin_connections_send` and `linkedin_messages_send` tools
  refuse to act unless the caller passes `confirm=True`, support `dry_run=True`,
  and are rate-limited to 5 actions per target per 5 minutes per session.
- **Both** honor the per-account daily quotas (`connections` 15/day, `messages`
  25/day) unless `--no-throttle` is set — which you should treat as the fastest
  way to get an account flagged.

## Authentication

LinkedIn does **not** expose a public API for the operations this CLI
performs — there's no OAuth flow, no developer app, no API key. The
website itself authenticates using browser cookies, and that's what we
capture.

`linkedin auth login`:

1. Launches Chromium via Playwright and navigates to
   `https://www.linkedin.com/feed/`. If you're already signed in
   (cookies present in the fresh Playwright profile), it captures the
   session immediately and returns. Otherwise LinkedIn redirects you
   to the login page; you sign in normally (including 2FA / captcha)
   and the CLI watches the cookie jar.
2. Waits for `li_at` + `JSESSIONID` to appear **and** for the page URL
   to leave the auth flow (`/login`, `/checkpoint`, `/uas`, `/authwall`,
   `/signup`).
3. Calls `/voyager/api/me` with the captured cookies + the full Voyager
   header set to resolve your own `urn:li:fsd_profile:<id>` — required
   later for the messaging endpoint. Falls back to
   `/voyager/api/identity/profiles/me`, and finally to decoding the
   `li_at` cookie, if both endpoints fail.
4. Generates a Fernet key (if not already present) and encrypts the
   cookie jar with it. Both `credentials.json` and `key.bin` land in
   `~/.config/mayai-cli/linkedin/`, mode `0600`.

`linkedin auth logout` removes both files.

### Session lifetime

LinkedIn rotates `li_at` aggressively (typically every couple of months,
sometimes sooner). When it expires you'll see:

```
error: session expired or invalid — run `linkedin auth login` again
```

(exit code 2). Just re-run login — the same Playwright profile is reused
so you usually don't have to re-enter credentials.

## How it works

LinkedIn ships a single-page web app backed by a private REST/GraphQL API
called **Voyager**. This CLI reverse-engineers the same calls the browser
makes. None of this is documented or stable; everything below is current
as of the response shapes captured during development.

### The Voyager request shape

Every Voyager request needs:

| Header | Source |
|---|---|
| `cookie: li_at=…; JSESSIONID="ajax:…"; …` | Captured by Playwright at login. |
| `csrf-token: ajax:…` | The JSESSIONID value **with surrounding double-quote characters stripped**. Voyager 403s if you send the quoted form. |
| `x-li-lang: it_IT` | UI locale. |
| `x-restli-protocol-version: 2.0.0` | restli v2. |
| `x-li-track: {"clientVersion":"1.13.…","osName":"web", …}` | JSON object identifying the web client build. |
| `accept: application/vnd.linkedin.normalized+json+2.1` | Asks for the normalized response shape (see below). |
| `user-agent: Mozilla/5.0 …` | Chrome-shaped UA. |

All of this lives in `linkedin_cli/api/client.py:_base_headers`.

### Normalized JSON and `*key` URN references

With `accept: application/vnd.linkedin.normalized+json+2.1`, Voyager
returns a response of the form:

```jsonc
{
  "data": {
    "data": {
      "searchDashClustersByAll": {
        "metadata": {"totalResultCount": 8758},
        "*elements": [                    // URN refs to clusters
          "urn:li:fsd_searchDashClusterViewModel:<…>",
          "urn:li:fsd_searchDashClusterViewModel:<…>"
        ]
      }
    }
  },
  "included": [                            // every actual entity, keyed by entityUrn
    {"entityUrn": "urn:li:fsd_searchDashClusterViewModel:<…>",
     "*items": ["urn:li:fsd_searchDashClusterItem:<…>"]},
    {"entityUrn": "urn:li:fsd_searchDashClusterItem:<…>",
     "$type": "com.linkedin.voyager.dash.search.SearchItem",
     "item": {"*entityResult": "urn:li:fsd_entityResultViewModel:(urn:li:fsd_profile:ACoAAA…,SEARCH_SRP,DEFAULT)"}},
    {"entityUrn": "urn:li:fsd_profile:ACoAAA…",
     "firstName": "Mario", "lastName": "Rossi", "occupation": "CTO at MayAI", "publicIdentifier": "mariorossi"}
  ]
}
```

The convention is: any object key prefixed with `*` is a **reference**
whose value is a URN (or list of URNs) that resolves into `included[]`
by `entityUrn`. The actual data is never inlined; you always have to
follow refs through `included[]`.

`linkedin_cli/api/search.py:_deep_resolve` is a recursive walker that:

- Strips the `*` prefix from any key.
- Replaces URN-string values with the resolved object from `included[]`.
- Recurses into the resolved object so nested `*key` refs get inlined too.
- Tracks visited URNs to break cycles in the graph.

After one pass the response becomes a normal nested tree the parser can
read directly.

### Composite URNs

The people-search response goes a step further: `SearchItem.item.
*entityResult` carries a **composite** URN like

```
urn:li:fsd_entityResultViewModel:(urn:li:fsd_profile:ACoAAA…,SEARCH_SRP,DEFAULT)
```

That composite URN is *not* in `included[]`. The inner profile URN
(`urn:li:fsd_profile:ACoAAA…`) **is**. `_extract_inner_urn` peels the
outer wrapper to look up the inner one — and that's where firstName /
lastName / occupation / publicIdentifier actually live.

### queryId rotation

GraphQL endpoints take a `queryId` like
`voyagerSearchDashClusters.02af92d4df45aef4ee11b7c453545c26`. The hash
changes whenever LinkedIn ships a new web bundle, and a stale id makes
the endpoint **hard-500**. To stay alive without recompiling the CLI:

1. `LinkedInClient.get_search_people_query_ids` returns an ordered list:
   the id that worked last in this session, the id scraped *live* from
   `https://www.linkedin.com/search/results/people/?keywords=test`, and
   two hardcoded fallbacks.
2. `search_people` walks the list and stops at the first id that doesn't
   500. The winner gets cached on the client for the rest of the session.
3. Scraping the queryId uses a strict regex
   (`r'"queryId"\s*:\s*"(voyagerSearchDashClusters\.[a-f0-9]{32})"'`)
   with a permissive fallback for when the minifier strips quotes.

If you ever see `error: all N queryId candidates returned 500`,
re-run `linkedin auth login` to refresh cookies — the scrape will pick
up a fresh id on the next call.

### CSRF token quirks

The web client reads the `JSESSIONID` cookie value and sends it as the
`csrf-token` header **with the surrounding `"` characters removed**.
Playwright captures the cookie verbatim, *including* those quotes.
`auth/credentials.py:normalize_csrf` strips them and is the single source
of truth — used both by `api/client.py` on every request and by
`auth/browser_login.py` for the `/me` lookup at login time.

### Throttling and daily quotas

LinkedIn's anti-abuse heuristics are tuned to spot mechanical traffic
— evenly-spaced bursts get flagged much faster than noisy human-paced
activity, and the absolute volume per day matters too. The CLI defends
against both:

**Jittered delay between requests.** Every `LinkedInClient` sleeps a
random `uniform(2.0, 6.0)` seconds between requests
(`JITTER_MIN_S` / `JITTER_MAX_S` in `api/client.py`). The first request
in a session goes immediately; subsequent ones wait.

**Per-account daily quotas.** State lives at
`~/.config/mayai-cli/linkedin/quotas.json` and resets automatically at
local midnight. When a limit is hit the offending command exits 1 with a
clear error.

| Quota | Limit | Counted when |
|---|---|---|
| `connections` | 15 per day | A successful `linkedin connections send` POST. |
| `messages` | 25 per day | Any `linkedin messages send` call (incremented before the POST so we never double-send). |
| `api_total` | 200 per day | Every HTTP request to Voyager — search, profile lookups, listings, sends. |
| `jobs` | 30 per day | Every request made by `jobs search` (one per page of 25), `jobs get`, and a geo lookup that misses the cache. These also count against `api_total`. |

The `jobs` limit can be **lowered** with `LINKEDIN_JOBS_DAILY_LIMIT=N`
(values above 30 are clamped to 30), and unlike the other quotas it stays
enforced under `--no-throttle`. A call is only counted if every quota it
touches still has room, so a refused call never burns budget.

**`--no-throttle`.** Disables both the jitter and the quota checks. The
flag exists for power users who know exactly what they're doing (e.g.
running a one-shot script under a tight time budget). It is the single
fastest way to get an account flagged — only use it if you accept the
risk and ideally on a non-primary account.

If you ever do see HTTP 429 (or 999, or a checkpoint) from LinkedIn:

```
error: blocked (rate_limited): rate limited by LinkedIn (HTTP 429) — stop for today, do not retry
```

…the command exits `3`. Stop and wait. Don't retry in a loop; LinkedIn
extends the penalty the more you hit them.

### Job search

`linkedin jobs search` makes the same call as the web app's `/jobs/search`
page (validated against the live endpoint in October 2026):

```
GET /voyager/api/voyagerJobsDashJobCards
    ?decorationId=com.linkedin.voyager.dash.deco.jobs.search.JobSearchCardsCollection-174
    &count=25&q=jobSearch&start=0
    &query=(origin:JOB_SEARCH_PAGE_JOB_FILTER,keywords:data%20scientist,
            locationUnion:(geoId:103350119),
            selectedFilters:(sortBy:List(DD),experience:List(1,2),timePostedRange:List(r604800)),
            spellCorrectionEnabled:true)
```

Filter codes: experience `1` internship … `6` executive; job type `F P C T I V O`;
`timePostedRange` `r86400` / `r604800` / `r2592000`; workplace `1` on-site,
`2` remote, `3` hybrid; `sortBy` `R` relevance or `DD` most recent. The
response is normalized JSON: `data.elements[].jobCardUnion.*jobPostingCard`
points at `JobPostingCard` records in `included[]` (title, company in
`primaryDescription`, location in `secondaryDescription`, salary in
`tertiaryDescription`, `footerItems` with `LISTED_DATE` and `EASY_APPLY_TEXT`).
`jobs get` reads `/voyager/api/jobs/jobPostings/<id>` with the
`WebFullJobPosting-65` decoration.

Locations need a numeric **geoId** (Italy is `103350119`). `--location NAME`
resolves one through the GEO typeahead and caches it in
`~/.config/mayai-cli/linkedin/geo_cache.json`. That typeahead is the least
stable endpoint here (it returned HTTP 400 in the October 2026 validation),
so the reliable path is to read the geoId from the browser — search jobs for
the place on linkedin.com and copy `geoId=` from the address bar — and either
pass `--geo-id` or pin it once with `linkedin jobs geo "Milan" --set <id>`.

## Why this was hard

Unlike the other tools in the [MayAI CLI](https://mayai.it) collection,
LinkedIn provides no public API for any of this functionality. Every
endpoint, header, queryId, and response shape was reverse-engineered
from the browser's network tab. A few specific things that made this
much harder than building against a documented API:

- **No API contract.** There's no spec, no SDK, no changelog. The
  response shape for the same endpoint can differ slightly between web
  bundles, and `queryId` hashes rotate. We had to handle multiple
  shapes per endpoint and build live discovery for queryIds.
- **Normalized JSON with URN graphs.** Voyager doesn't return inline
  nested objects — it returns a flat `included[]` array and a tree of
  `*key`-prefixed URN references that has to be resolved recursively.
  Building the parser was 80% of the effort.
- **Composite URNs.** The people-search endpoint encodes the inner
  profile URN inside an outer EntityResultViewModel URN with
  context tags appended. The outer URN isn't indexed; only the inner
  one is. That took several iterations to figure out — and the response
  format kept shifting under us as we narrowed it down.
- **queryId expiry.** A perfectly-formed search request would hard-500
  one day and work the next. The fix is to scrape the *live* queryId
  from the rendered HTML of LinkedIn's own search results page on
  every fresh session — a moving target by design.
- **CSRF token quoting.** The CSRF header has to be the JSESSIONID
  cookie value with surrounding `"` characters stripped. Sending the
  raw cookie produces a generic 403 "CSRF check failed" with no hint
  that the issue is whitespace/quoting.
- **No password auth.** OAuth doesn't cover any of the endpoints we
  need, and password login from a script triggers a checkpoint. The
  only reliable path is a real browser via Playwright, which means
  shipping Chromium as a dependency and dealing with all the timing
  edge cases around "is the login actually complete yet".

The good news: once the parser and auth flow are in place, the
day-to-day commands are stable. If LinkedIn ships a breaking change,
the verbose mode (`--verbose`) dumps enough of the response body for
the next 2–3 lines of fix to be obvious.

## Output format

- **Default** — compact human-readable text. Empty / null fields are
  stripped so terminal output stays scannable.
- **`--json`** — NDJSON. One object per line; lists stream one element
  per line so consumers can process incrementally without loading the
  whole array.
- **`--verbose`** — adds request lines on stderr (e.g.
  `[linkedin] GET https://www.linkedin.com/voyager/api/me -> 200 (284 ms)`)
  plus a 5 KB preview of the response body. Useful for debugging when
  LinkedIn shifts a response shape.

Errors always go to stderr, prefixed with `error:`.

### Sample rows

`linkedin search people`:

```json
{
  "profile_id": "urn:li:fsd_profile:ACoAAA…",
  "public_id": "mario-rossi-9558832a",
  "name": "Mario Rossi",
  "headline": "CTO at MayAI",
  "location": "Milan, Italy",
  "profile_url": "https://www.linkedin.com/in/mario-rossi-9558832a"
}
```

`linkedin jobs search`:

```json
{
  "job_id": "4000000001",
  "title": "AI Engineer Intern",
  "company": "Acme Analytics",
  "location": "Milano, Lombardia, Italia (Ibrido)",
  "salary": "12K €/anno - 15K €/anno",
  "listed_at": "2026-10-16T13:32:07Z",
  "url": "https://www.linkedin.com/jobs/view/4000000001/",
  "easy_apply": false,
  "reposted": false
}
```

`linkedin connections list` (intentionally lean — names require a
per-row API call that we don't make implicitly; use `linkedin profile
get <public_id>` when you need them):

```json
{
  "connection_urn": "urn:li:fsd_connection:(ACoAA-me,ACoAA-other)",
  "connected_at": "2025-09-14"
}
```

`linkedin messages list`:

```json
{
  "conversation_id": "urn:li:msg_conversation:…",
  "unread_count": 2,
  "last_activity_at": 1731920000000,
  "participants": [
    {"name": "Mario Rossi", "headline": "CTO at MayAI",
     "profile_url": "https://www.linkedin.com/in/mariorossi"}
  ],
  "last_message": "Ciao!",
  "last_message_from": "Mario Rossi"
}
```

## Development

```bash
make dev          # install with dev extras + Chromium
make playwright   # install just the Chromium binary
make test         # run pytest
make lint         # run ruff (linkedin_cli/ + tests/)
make typecheck    # run mypy linkedin_cli/
make clean        # remove caches and build artifacts
```

Contributing guide and PR checklist: [CONTRIBUTING.md](CONTRIBUTING.md).

## Help

- [docs/AUTHENTICATION.md](docs/AUTHENTICATION.md) — login flow, cookie storage, session lifetime
- [docs/FAQ.md](docs/FAQ.md) — common questions and gotchas (queryId 500s, rate limits, MCP setup)
- [CHANGELOG.md](CHANGELOG.md) — release notes
- [Issues](https://github.com/mayai-it/linkedin-cli/issues) — bug reports and feature requests

## License

MIT — see [LICENSE](./LICENSE).

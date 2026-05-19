# linkedin-cli

Command-line client for **LinkedIn**, driving the internal Voyager API the
same way the website does. Built for both humans and AI agents:
context-efficient defaults, NDJSON output for piping into LLMs or `jq`, and
no API key — just session cookies captured from a real browser.

Part of [MayAI CLI](https://mayai.it).

> ## Terms of Service warning
>
> LinkedIn forbids automated access to its platform in its [User Agreement](https://www.linkedin.com/legal/user-agreement)
> (sections 8.2 / 8.3). This tool is intended for **personal and research
> use** on your own account — the kind of thing you'd do manually, just
> scripted. **Do not** use it for mass scraping, bulk outreach, spam,
> evading platform rate limits, or any commercial scraping operation. The
> CLI deliberately defaults to a ~1.5 s minimum delay between requests for
> exactly this reason. LinkedIn may rate-limit, restrict, or terminate
> accounts that look automated; you are responsible for your usage.

## Requirements

- Python 3.11+
- A LinkedIn account
- Chromium (installed automatically by `make install` via Playwright)

## Installation

From source:

```bash
git clone https://github.com/mayai-it/linkedin-cli.git
cd linkedin-cli
make install
```

The `make install` target installs the package in editable mode and
runs `playwright install chromium` so the browser-based login flow has
a Chromium binary to drive.

Or directly with pip:

```bash
pip install -e .
playwright install chromium
```

For local development (adds `pytest`, `ruff`):

```bash
make dev
```

## Quick start

```bash
# 1. Authenticate — opens a Chromium window for you to sign in normally.
#    Captures the session cookies once login is complete.
linkedin auth login

# 2. Verify
linkedin auth status

# 3. Find someone
linkedin --json search people "Mario Rossi"

# 4. Read their profile by public id
linkedin --json profile get mario-rossi-9558832a

# 5. Latest 40 first-degree connections
linkedin --json connections list

# 6. Read the most recent conversations
linkedin --json messages list

# 7. Send a 1:1 message (member id from `profile get`)
linkedin messages send 12345678 "Ciao Mario, parliamo?"
```

## Command reference

| Command | Description |
|---|---|
| `linkedin auth login [--headless] [--timeout S]` | Open Chromium, wait for the user to sign in, capture `li_at` + `JSESSIONID`, resolve the user's own `urn:li:fsd_profile:…`. |
| `linkedin auth status` | Show whether a session is stored (masked) and which member URN was captured. |
| `linkedin auth logout` | Delete saved cookies + encryption key. |
| `linkedin profile get <username-or-url>` | Fetch a single profile by vanity public id or full LinkedIn URL. |
| `linkedin search people <query> [--company N] [--title R]` | People search; `--company` / `--title` fold into the keyword string. |
| `linkedin search companies <query>` | Companies search via the REST search-clusters endpoint. |
| `linkedin connections list [--limit N]` | First-degree connections, newest first. |
| `linkedin connections pending` | Incoming connection requests awaiting response. |
| `linkedin connections send <profile-id> [--dry-run]` | Send a connection request. Accepts a public id or a profile URN. |
| `linkedin messages list` | Latest conversations from the inbox. |
| `linkedin messages send <recipient> <text> [--dry-run]` | Send a 1:1 message. `recipient` is a numeric member id or `urn:li:member:N`. |

### Global flags

These work in any position (before or after the subcommand):

| Flag | Effect |
|---|---|
| `--json` | Emit one JSON object per line (NDJSON). |
| `--verbose` | Log request URL, status, timing, and a body preview to stderr. |
| `-h`, `--help` | Show help for the current command. |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Application error (network, rate limit, search 500, bad arguments) |
| `2` | Not authenticated, or session expired — run `linkedin auth login` |

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

### Rate limiting

Every `LinkedInClient` enforces a ~1.5 s minimum delay between requests
to a single instance (`DEFAULT_RATE_LIMIT_S`). This matches what the web
client does loosely and is the main reason this tool isn't immediately
flagged. **Do not** disable it in a loop. If you get back HTTP 429:

```
error: rate limited by LinkedIn — wait a few minutes and try again
```

…stop and wait. Don't retry in a tight loop; LinkedIn will keep the
account flagged longer the more you hit them.

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
make lint         # run ruff
make clean        # remove caches and build artifacts
```

## License

MIT — see [LICENSE](./LICENSE).

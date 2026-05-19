---
name: linkedin-cli
description: Use whenever the user asks about searching LinkedIn profiles, reading or sending LinkedIn messages, listing or growing their LinkedIn network, or mentions "LinkedIn", "connessione LinkedIn", "messaggio LinkedIn", "InMail". Provides a CLI that talks to LinkedIn's internal Voyager API using session cookies captured from a real browser.
---

# linkedin-cli — agent usage guide

`linkedin` is a command-line client for **LinkedIn** that drives the
internal Voyager API the way the website does. Use it any time the user
asks you to search profiles, read/send messages, or manage their network.

> LinkedIn forbids automated use in its Terms of Service. Use the tool
> the way a careful human would: respect rate limits, don't scrape at
> scale, and don't spam. The CLI defaults to 1.5 s between requests for
> exactly this reason — don't disable that throttle without a reason.

## When to use this skill

Trigger on user prompts like:
- "Find Mario Rossi on LinkedIn"
- "Who's the CTO of MayAI on LinkedIn?"
- "Send a connection request to <profile-id>"
- "Reply to my latest LinkedIn message"
- "Show me my pending invitations"
- "Quanti messaggi LinkedIn non letti ho?"
- "Cerca su LinkedIn ingegneri AI in Italia"

## Golden rules

1. **Always pass `--json`** when you intend to parse the output. The
   default format is for humans; `--json` is NDJSON (one object per
   line) and is what you should consume.
2. **Check auth first** with `linkedin auth status` if you're unsure
   whether the user is logged in. Exit code `2` means not authenticated
   — tell the user to run `linkedin auth login`, which opens a real
   browser via Playwright. You cannot script the login: it needs the
   user to physically sign in (2FA / captcha may be required).
3. **Never try to log in with username/password on the CLI.** The auth
   flow is cookie-capture only. Don't ask the user for their password.
4. **Respect the throttle.** The client sleeps ~1.5 s between requests
   by default. Don't issue tight loops; if you need many results,
   prefer one large `--limit` over many small calls.
5. **Read stderr separately.** Errors go to stderr with the prefix
   `error:`. Exit codes: `0` ok, `1` application error, `2` auth.
   Rate-limit errors carry status `429` — surface them to the user
   verbatim and stop sending more requests for a few minutes.
6. **Never echo credentials.** They live Fernet-encrypted at
   `~/.config/mayai-cli/linkedin/credentials.json` and
   `~/.config/mayai-cli/linkedin/key.bin`. Do not cat or print them.
7. **profile_id vs public_id.** Most commands accept either:
   - `public_id`: vanity name from the URL, e.g. `mario-rossi-9558832a`
   - `profile_id`: the canonical URN, e.g. `urn:li:fsd_profile:ACoAAA...`
   When you have a choice, the URN is more stable.

## Command cheat sheet

### Auth
```bash
linkedin auth login           # opens Chromium via Playwright
linkedin auth status
linkedin auth logout
```

`auth login` accepts `--headless` (only useful if the cookies are
already present from a previous run) and `--timeout SECONDS` (default
300 — the wait window for the user to finish signing in).

### Profile
```bash
linkedin --json profile get mario-rossi-9558832a
linkedin --json profile get https://www.linkedin.com/in/mario-rossi-9558832a/
```

Profile row shape:
```json
{
  "profile_id": "urn:li:fsd_profile:ACoAAA...",
  "public_id": "mario-rossi-9558832a",
  "member_id": "12345678",
  "first_name": "Mario",
  "last_name": "Rossi",
  "headline": "CTO at MayAI",
  "location": "Milan, Italy",
  "profile_url": "https://www.linkedin.com/in/mario-rossi-9558832a"
}
```

### Search
```bash
# Free-form keyword search (uses GraphQL search/dash/clusters)
linkedin --json search people "Mario Rossi"

# Constrain by company (folded into keywords — LinkedIn does the matching)
linkedin --json search people "CTO" --company "MayAI"

# Constrain by title
linkedin --json search people "engineer" --title "Senior"

# Companies search
linkedin --json search companies "MayAI"
```

Person hit shape:
```json
{
  "profile_id": "urn:li:fsd_profile:ACoAAA...",
  "public_id": "mario-rossi-9558832a",
  "name": "Mario Rossi",
  "headline": "CTO at MayAI",
  "location": "Milan, Italy",
  "profile_url": "https://www.linkedin.com/in/mario-rossi-9558832a"
}
```

If you ever get `error: all N queryId candidates returned 500: …`,
LinkedIn has rotated its bundle queryId beyond what's cached. Run
`linkedin auth login` to refresh cookies — the CLI auto-scrapes the
live queryId on the next call after that.

### Connections
```bash
# Latest 40 first-degree connections
linkedin --json connections list

# More
linkedin --json connections list --limit 200

# Pending incoming invitations
linkedin --json connections pending

# Send a connection request (accepts public_id or URN)
linkedin --json connections send mario-rossi-9558832a
linkedin --json connections send urn:li:fsd_profile:ACoAAA...

# Dry-run a connection request — prints what would happen, no API call
linkedin connections send urn:li:fsd_profile:ACoAAA... --dry-run
```

Connection list shape (lean: profile names require a per-row API call
that we don't make implicitly — use `linkedin profile get <public_id>`
when you need them):
```json
{
  "connection_urn": "urn:li:fsd_connection:(ACoAA-me,ACoAA-other)",
  "connected_at": "2025-09-14"
}
```

`connections send` returns `{"status": "ok"}` on success, or
`{"status": "already-invited"}` when LinkedIn replies 409.

### Messages
```bash
# Latest conversations
linkedin --json messages list

# Send a 1:1 message — recipient is a member id (numeric) or member URN
linkedin --json messages send 12345678 "Ciao Mario, parliamo?"
linkedin --json messages send urn:li:member:12345678 "..."

# Dry-run — no message is actually sent
linkedin messages send 12345678 "test" --dry-run
```

`messages send` does NOT accept a public_id directly — LinkedIn's
endpoint wants the numeric `member` URN. If you only have a public_id,
first run `linkedin profile get <public_id>` and use the `member_id`
field from the result.

Conversation list row shape:
```json
{
  "conversation_id": "urn:li:msg_conversation:...",
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

## Common workflows

### "Find Mario Rossi's profile and connect with him"
```bash
linkedin --json search people "Mario Rossi" \
  | head -1 \
  | python3 -c 'import sys, json; print(json.loads(sys.stdin.read())["profile_id"])' \
  | xargs linkedin --json connections send
```

### "Have I ever talked to <person>?"
```bash
linkedin --json messages list \
  | jq 'select(.participants[].name | contains("Mario"))'
```

### "Who are my newest connections?"
```bash
linkedin --json connections list --limit 20
```
(Already returned newest-first.)

### "Send a polite intro to Anna Bianchi"
1. Find her: `linkedin --json profile get annabianchi`
2. Grab the `member_id` from the result.
3. `linkedin messages send <member_id> "Ciao Anna, ..."`
   (Drop `--json` here unless you want to parse the send confirmation.)

## Failure modes you should recognize

- `error: not authenticated — run \`linkedin auth login\` first`
  → exit code 2. Tell the user; do not retry.
- `error: session expired or invalid — run \`linkedin auth login\` again`
  → exit code 2. Cookies expired; same response.
- `error: rate limited by LinkedIn — wait a few minutes and try again`
  → exit code 1, status 429. Stop and wait. Do not retry in a loop.
- `error: HTTP 500 on GET https://www.linkedin.com/voyager/api/graphql…`
  → likely a stale queryId for search. Re-running `linkedin auth login`
  refreshes cookies and lets the next call scrape a fresh queryId.
- `error: missing member URN — re-run \`linkedin auth login\``
  → the /me lookup at login time didn't recover the user's own
  fsd_profile URN. Re-running login retries the resolution.

## Output you should NEVER pipe back

- The contents of `~/.config/mayai-cli/linkedin/`. Those are secrets.
- The cookie values printed by `auth status` are intentionally masked
  to first/last 4 chars; don't try to recover them.
- Full message bodies of the user's PMs without explicit permission —
  treat private DMs like private email.

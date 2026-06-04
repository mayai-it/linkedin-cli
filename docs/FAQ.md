# FAQ

## Is this allowed? Will my account get banned?

LinkedIn's Terms of Service (section 8.2) forbid automated access. This tool is
for personal and research use; using it carries a real risk that LinkedIn
restricts or bans your account. `linkedin-cli` is built to behave like a careful
human — jittered delays between requests, conservative daily quotas — but it
cannot make automation *allowed*. Use it at your own risk, ideally on a
non-primary account, and never for bulk scraping or spam.

## Where are my credentials stored?

Fernet-encrypted under `~/.config/mayai-cli/linkedin/` —
`credentials.json` (the cookie blob) and `key.bin` (the key), both mode `0600`.
`linkedin auth logout` removes both. Full layout:
[AUTHENTICATION.md](AUTHENTICATION.md).

## People search suddenly returns `HTTP 500` / `all N queryId candidates returned 500`

LinkedIn rotated the `queryId` hash for the people-search GraphQL endpoint when
it shipped a new web bundle, and the cached/fallback ids are now stale. The CLI
scrapes a fresh id from LinkedIn's own rendered search page on the next session,
so the fix is almost always:

```bash
linkedin auth login   # refresh cookies → next call scrapes a live queryId
```

If it still 500s, LinkedIn may have changed the response shape — run with
`--verbose` and open an issue with the (redacted) body preview.

## I get `error: rate limited by LinkedIn` (HTTP 429)

Stop. Wait several minutes — ideally longer — before trying again. Do **not**
retry in a loop: LinkedIn extends the penalty the more you hit it while limited.
This is separate from the CLI's own daily quotas (below), which are a local
safety net, not LinkedIn's limit.

## What are the daily quotas and how do I change them?

State lives in `~/.config/mayai-cli/linkedin/quotas.json` and resets at local
midnight. Defaults:

| Quota | Limit | Counted when |
|---|---|---|
| `connections` | 15/day | A successful `connections send` POST. |
| `messages` | 25/day | Each `messages send`. |
| `api_total` | 200/day | Every Voyager request. |

`--no-throttle` disables **both** the inter-request delay and these quota checks.
It's the single fastest way to get an account flagged — only use it if you fully
accept the risk.

## I added `linkedin-mcp` to my MCP client and the tools don't show up

Three usual causes:

1. **Path**: the `command` in the MCP client config must be the absolute path to
   the installed entry point. Run `which linkedin-mcp` and paste the result
   verbatim — `~` and shell aliases are not expanded.
2. **Not authenticated**: the server refuses to start (exit code `2`) if no
   cookies are stored. Run `linkedin auth login` first, then restart the MCP
   client.
3. **Server logs**: `linkedin-mcp` writes diagnostics to stderr. In Claude
   Desktop, check `~/Library/Logs/Claude/mcp-server-linkedin.log`. The first
   line tells you whether it bound to a session or which precondition failed.

## Can the agent send connection requests or messages on its own?

No. The MCP write tools (`linkedin_connections_send`, `linkedin_messages_send`)
refuse to act unless the caller passes `confirm=True`, and they support
`dry_run=True` for validation. They're also rate-limited per session (5 actions
per target per 5 minutes) on top of the daily quotas. The intent is that a human
approves each real action.

## `messages send` says my recipient "looks like a public id"

LinkedIn's messaging endpoint wants a numeric `member` id or a
`urn:li:member:N` URN — not a vanity public id. Resolve it first:

```bash
linkedin --json profile get mario-rossi-9558832a   # read the member_id field
linkedin messages send <member_id> "Ciao Mario, ..."
```

## Why are `connections list` rows so bare?

LinkedIn's connections endpoint does not inline profile names — each row is just
`connection_urn` + `connected_at`. Resolving names would mean one extra API call
per row, which we won't do implicitly (it burns the `api_total` quota fast). When
you need the full profile, run `linkedin profile get <public_id>`.

## SSL / certificate errors

The client uses the OS root certificate store. On macOS with a python.org
build, run the bundled `Install Certificates.command`. On Linux, ensure the
system CA bundle is present (`pip install --upgrade certifi` if needed). We do
not disable certificate verification.

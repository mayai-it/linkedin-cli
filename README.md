# linkedin-cli

CLI for interacting with LinkedIn through its internal Voyager API.
Built for AI agents and developers — JSON-first, terminal-native.

> ⚠️ LinkedIn forbids automated use in its Terms of Service. This tool is intended
> for personal and research use. Do not use for mass scraping or spam.

## Install

```bash
make install            # installs the CLI and downloads the Playwright chromium build
# or
pip install -e .
playwright install chromium
```

## Login

The login flow opens a real browser via Playwright. Sign in as you normally
would and the CLI captures your session cookies (`li_at`, `JSESSIONID`).
Credentials are stored Fernet-encrypted under
`~/.config/mayai-cli/linkedin/`.

```bash
linkedin auth login
linkedin auth status
linkedin auth logout
```

## Commands

```bash
# profiles
linkedin profile get <username>

# search
linkedin search people "Mario Rossi"
linkedin search people "CTO" --company MayAI
linkedin search people "engineer" --title "Senior"
linkedin search companies "MayAI"

# connections
linkedin connections list [--limit N]
linkedin connections pending
linkedin connections send <profile-id>

# messages
linkedin messages list
linkedin messages send <profile-id> "Ciao!"
```

Every command accepts `--json` for NDJSON output (one object per line) and
`--verbose` for headers/timing logs to stderr.

## Notes

- LinkedIn rate-limits aggressively. The CLI sleeps 1–2 seconds between
  requests by default. If your IP gets blocked, wait a few hours before
  retrying.
- Voyager endpoints change periodically. The version tested is documented in
  `linkedin_cli/api/endpoints.py`.
- Session cookies expire — re-run `linkedin auth login` when that happens.

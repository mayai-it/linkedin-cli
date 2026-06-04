# Authentication

LinkedIn does **not** expose a public API for the operations this CLI performs
— no OAuth flow, no developer app, no API key. The website itself authenticates
with browser cookies, and that's what `linkedin-cli` captures.

## The login flow

`linkedin auth login`:

1. Launches Chromium via Playwright and navigates to
   `https://www.linkedin.com/feed/`. If you're already signed in (cookies in
   the Playwright profile) it captures the session immediately. Otherwise you
   sign in normally — including 2FA / captcha — and the CLI watches the cookie
   jar.
2. Waits for `li_at` + `JSESSIONID` to appear **and** for the page URL to leave
   the auth flow (`/login`, `/checkpoint`, `/uas`, `/authwall`, `/signup`).
3. Calls `/voyager/api/me` with the captured cookies + the full Voyager header
   set to resolve your own `urn:li:fsd_profile:<id>` (needed for the messaging
   endpoint). Falls back to `/voyager/api/identity/profiles/me`, and finally to
   decoding the `li_at` cookie, if both fail.
4. Generates a Fernet key (if not already present) and encrypts the cookie jar
   with it.

You **cannot** script this login: it needs a real browser and a real human to
complete any challenge. Never pass a LinkedIn username/password to the CLI —
there's no such flag, and password login from a script trips a checkpoint.

## File layout

Everything lives under `~/.config/mayai-cli/linkedin/`, each file mode `0600`:

| File | Contents |
|---|---|
| `credentials.json` | The Fernet-encrypted cookie blob (`li_at`, `JSESSIONID`, the full jar, and your member URN). |
| `key.bin` | The 32-byte Fernet key used to decrypt `credentials.json`. |
| `quotas.json` | Per-account daily action counters (`connections`, `messages`, `api_total`); resets at local midnight. |

`linkedin auth logout` deletes `credentials.json` and `key.bin`.

> The encryption protects the cookies at rest from casual reading, but anyone
> with both files can reconstruct the session. Treat the whole directory as a
> secret: never commit it, never paste its contents, never let an agent `cat`
> it.

## The CSRF quirk

Every Voyager request needs a `csrf-token` header equal to the `JSESSIONID`
cookie value **with the surrounding double-quote characters stripped**.
Playwright captures the cookie verbatim, *including* the quotes; Voyager 403s if
you send the quoted form. `auth/credentials.py:normalize_csrf` is the single
source of truth that strips them — used on every request and during the `/me`
lookup at login.

## Session lifetime

LinkedIn rotates `li_at` aggressively (typically every couple of months,
sometimes sooner). When it expires you'll see:

```
error: session expired or invalid — run `linkedin auth login` again
```

(exit code `2`). Re-run login — the same Playwright profile is reused, so you
usually don't have to re-enter credentials. Refreshing cookies also lets the
next people-search call scrape a current `queryId` (see the FAQ).

## Headless mode

`linkedin auth login --headless` only works if the cookies are already present
from a previous interactive run — there's no way to satisfy a fresh login
challenge without a visible browser. Use it to refresh a still-valid profile,
not for first-time setup.

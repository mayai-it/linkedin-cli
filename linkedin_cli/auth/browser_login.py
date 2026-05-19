"""Playwright-driven LinkedIn login.

Opens a real Chromium window pointing at linkedin.com, waits for the user to
finish signing in (including any 2FA / captcha), and then harvests the
session cookies. We detect "logged in" by waiting for the `li_at` cookie to
appear *and* for the browser to navigate away from the `/login` /
`/checkpoint` flow — until both happen the auth cookies aren't actually
useful (`JSESSIONID` is set on the login page itself).

After login we call `/voyager/api/me` with the captured cookies to resolve
the logged-in user's profile URN. The `me` payload contains a
`miniProfile.entityUrn` of the form `urn:li:fs_miniProfile:<id>`; messaging
endpoints want `urn:li:fsd_profile:<id>`, which shares the same numeric id.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import sys
import time

import httpx

from linkedin_cli.auth.credentials import Credentials, normalize_csrf

LOGIN_URL = "https://www.linkedin.com/login"
FEED_URL = "https://www.linkedin.com/feed/"
ME_URLS = (
    "https://www.linkedin.com/voyager/api/me",
    "https://www.linkedin.com/voyager/api/identity/profiles/me",
)
DEFAULT_TIMEOUT_S = 300  # five minutes for the user to log in / pass 2FA

# URL patterns that mean "still in the auth flow"
_AUTH_FLOW_RE = re.compile(r"/(login|checkpoint|uas|authwall|signup)(?:[/?#]|$)")


class BrowserLoginError(RuntimeError):
    pass


def _is_post_auth(page, context) -> bool:
    """True when both auth cookies are set and we're not on an auth-flow URL."""
    try:
        cookies = context.cookies("https://www.linkedin.com")
        current_url = page.url
    except Exception:
        return False
    jar = {c["name"]: c["value"] for c in cookies}
    if not jar.get("li_at") or not jar.get("JSESSIONID"):
        return False
    if not current_url:
        return False
    return not _AUTH_FLOW_RE.search(current_url)


def browser_login(timeout_s: int = DEFAULT_TIMEOUT_S, headless: bool = False) -> Credentials:
    """Run an interactive browser login and return captured credentials."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserLoginError(
            "playwright is not installed — run "
            "`pip install playwright && playwright install chromium`"
        ) from exc

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=headless)
        except Exception as exc:
            raise BrowserLoginError(
                f"could not launch chromium — try `playwright install chromium` ({exc})"
            ) from exc

        context = browser.new_context()
        page = context.new_page()

        # Land on /feed/ directly — LinkedIn redirects to /login if the user
        # isn't authenticated, but lands straight on the feed if a session
        # cookie is already present. That lets us take a fast path for
        # already-logged-in users without showing them a login form.
        try:
            page.goto(FEED_URL, wait_until="domcontentloaded")
        except Exception as exc:
            browser.close()
            raise BrowserLoginError(f"could not open LinkedIn: {exc}") from exc

        jar: dict[str, str] = {}

        if _is_post_auth(page, context):
            sys.stderr.write("Already signed in — capturing existing session.\n")
        else:
            sys.stderr.write(
                "A browser window opened. Sign in to LinkedIn — the CLI will capture "
                "your session cookies automatically.\n"
            )

            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                if _is_post_auth(page, context):
                    break
                time.sleep(1)
            else:
                browser.close()
                raise BrowserLoginError(
                    "timed out waiting for LinkedIn login — session cookies / post-login "
                    "navigation never appeared"
                )

        # Give LinkedIn one more beat to settle — the SPA writes its final
        # cookies *after* navigation completes.
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        cookies = context.cookies("https://www.linkedin.com")
        jar = {c["name"]: c["value"] for c in cookies}

        li_at = jar["li_at"]
        jsessionid = jar["JSESSIONID"]

        member_urn = _fetch_member_urn(jar)

        browser.close()

    return Credentials(
        li_at=li_at,
        jsessionid=jsessionid,
        member_urn=member_urn,
        cookies=jar,
    )


def _fetch_member_urn(jar: dict[str, str]) -> str:
    """Resolve the logged-in user's `urn:li:fsd_profile:<id>` URN.

    Tries, in order:
      1. `/voyager/api/me`
      2. `/voyager/api/identity/profiles/me`
      3. Decoding the `li_at` cookie as if it were JWT-like.

    Diagnostic info is always written to stderr while we stabilize this —
    silent failures here are the whole reason this function exists.
    """
    raw_jsessionid = jar.get("JSESSIONID", "")
    csrf = normalize_csrf(raw_jsessionid)
    sys.stderr.write(f"[auth] JSESSIONID (raw cookie): {raw_jsessionid!r}\n")
    sys.stderr.write(f"[auth] csrf-token (sent):       {csrf!r}\n")
    headers = {
        "accept": "application/vnd.linkedin.normalized+json+2.1",
        "csrf-token": csrf,
        "x-li-lang": "it_IT",
        "x-restli-protocol-version": "2.0.0",
        "x-li-track": json.dumps(
            {
                "clientVersion": "1.13.44236",
                "osName": "web",
                "timezoneOffset": 2,
                "timezone": "Europe/Rome",
                "deviceFormFactor": "DESKTOP",
                "mpName": "voyager-web",
            },
            separators=(",", ":"),
        ),
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36"
        ),
        "referer": "https://www.linkedin.com/feed/",
    }

    with httpx.Client(cookies=jar, headers=headers, timeout=15.0) as client:
        for url in ME_URLS:
            try:
                response = client.get(url)
            except httpx.HTTPError as exc:
                sys.stderr.write(f"[auth] GET {url} -> network error: {exc}\n")
                continue

            body_preview = response.text[:500].replace("\n", " ")
            sys.stderr.write(f"[auth] GET {url} -> {response.status_code}\n")
            sys.stderr.write(f"[auth] body[0:500]: {body_preview}\n")

            if response.status_code != 200:
                continue

            try:
                payload = response.json()
            except ValueError as exc:
                sys.stderr.write(f"[auth] {url} returned non-JSON: {exc}\n")
                continue

            urn = _extract_profile_urn(payload)
            if urn:
                return urn
            sys.stderr.write(f"[auth] no profile URN found in {url} payload\n")

    # Final fallback: decode the li_at cookie.
    urn = _member_urn_from_li_at(jar.get("li_at", ""))
    if urn:
        sys.stderr.write(f"[auth] resolved member URN from li_at cookie: {urn}\n")
    else:
        sys.stderr.write("[auth] could not derive member URN from li_at cookie\n")
    return urn


def _member_urn_from_li_at(li_at: str) -> str:
    """Best-effort decode of the li_at cookie to recover the member id.

    li_at isn't a real JWT (no `header.payload.signature` with JSON in each
    part), but it *is* base64url-ish and historically contains the numeric
    member id near the start. We try a few decoding strategies and look for
    the first sequence of digits that's plausibly a member id (>= 6 chars).
    """
    if not li_at:
        return ""

    candidates: list[str] = [li_at]
    # If a dot is ever present, also try the individual parts.
    candidates.extend(part for part in li_at.split(".") if part)

    digit_run = re.compile(r"\d{6,}")

    for chunk in candidates:
        # Pad for urlsafe base64 decoding.
        pad = "=" * (-len(chunk) % 4)
        for decoder in (base64.urlsafe_b64decode, base64.b64decode):
            try:
                raw = decoder(chunk + pad)
            except (ValueError, binascii.Error):
                continue
            text = raw.decode("utf-8", errors="ignore")
            match = digit_run.search(text)
            if match:
                return f"urn:li:fsd_profile:{match.group(0)}"

    # Last-ditch: digits embedded directly in the raw cookie string.
    match = digit_run.search(li_at)
    if match:
        return f"urn:li:fsd_profile:{match.group(0)}"
    return ""


_URN_TAIL_RE = re.compile(r"urn:li:(?:fs_miniProfile|fsd_profile|member):([A-Za-z0-9_-]+)")


def _extract_profile_urn(payload: dict) -> str:
    """Find a profile URN anywhere in the /me response and normalize it.

    The shape isn't 100% stable — sometimes miniProfile is inlined, sometimes
    it's in `included[]`. We just scan the JSON text for the first matching
    URN and rebuild it as `urn:li:fsd_profile:<id>`.
    """
    # Fast path: documented shape.
    mini = (payload.get("miniProfile") or {}).get("entityUrn")
    if isinstance(mini, str):
        match = _URN_TAIL_RE.search(mini)
        if match:
            return f"urn:li:fsd_profile:{match.group(1)}"

    # Fallback: search the whole payload as text.
    blob = json.dumps(payload)
    match = _URN_TAIL_RE.search(blob)
    if match:
        return f"urn:li:fsd_profile:{match.group(1)}"
    return ""

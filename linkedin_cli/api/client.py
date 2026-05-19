"""httpx-based client for LinkedIn's internal Voyager API.

Voyager only accepts requests that look like they come from the LinkedIn web
app — same headers, same cookies, same CSRF token. The required headers are
documented in linkedin-cli's CLAUDE.md; we set them all here so callers can
just hit endpoints by URL.
"""

from __future__ import annotations

import json
import random
import re
import string
import sys
import time
from typing import Any

import httpx

from linkedin_cli.api.endpoints import (
    CLIENT_VERSION,
    SEARCH_PEOPLE_QUERY_ID_FALLBACKS,
    SEARCH_PEOPLE_RESULTS_PAGE,
)
from linkedin_cli.api.quotas import QuotaExceededError, check_and_increment
from linkedin_cli.auth import Credentials

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)


class LinkedInAPIError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


def _tracking_id() -> str:
    """LinkedIn's invitation API wants a short base64-ish tracking id."""
    alphabet = string.ascii_letters + string.digits + "+/"
    return "".join(random.choices(alphabet, k=16))


# Strict pattern: matches `"queryId":"voyagerSearchDashClusters.<hash>"` as it
# appears in LinkedIn's JS bundle. This avoids picking up the string from
# unrelated places (e.g. error tracking schemas) that sometimes embed the
# same prefix without an actual hash.
_SEARCH_QUERY_ID_RE_STRICT = re.compile(
    r'"queryId"\s*:\s*"(voyagerSearchDashClusters\.[a-f0-9]{32})"'
)
# Permissive fallback for the rare case the JS minifier rewrites quotes.
_SEARCH_QUERY_ID_RE_LOOSE = re.compile(
    r"voyagerSearchDashClusters\.([a-f0-9]{32})"
)


class LinkedInClient:
    """Thin wrapper around httpx with Voyager-specific defaults."""

    # Jittered delay between requests so traffic doesn't look mechanical.
    # LinkedIn's anti-bot heuristics flag tight, evenly-spaced bursts much
    # faster than a noisy human-paced cadence.
    JITTER_MIN_S = 2.0
    JITTER_MAX_S = 6.0

    def __init__(
        self,
        creds: Credentials,
        verbose: bool = False,
        throttle: bool = True,
    ) -> None:
        self.creds = creds
        self.verbose = verbose
        self.throttle = throttle
        self._last_request_at = 0.0
        self._search_query_id: str | None = None  # cached per session

        # Cookies: keep everything we captured at login so the request looks
        # like a normal browser. li_at + JSESSIONID are the load-bearing ones.
        jar = dict(self.creds.cookies)
        jar.setdefault("li_at", self.creds.li_at)
        jar.setdefault("JSESSIONID", self.creds.jsessionid)

        self._client = httpx.Client(
            cookies=jar,
            headers=self._base_headers(),
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=False,
        )

    # ---- lifecycle ----------------------------------------------------------

    def __enter__(self) -> LinkedInClient:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ---- headers ------------------------------------------------------------

    def _base_headers(self) -> dict[str, str]:
        track = {
            "clientVersion": CLIENT_VERSION,
            "osName": "web",
            "timezoneOffset": 2,
            "timezone": "Europe/Rome",
            "deviceFormFactor": "DESKTOP",
            "mpName": "voyager-web",
        }
        return {
            "accept": "application/vnd.linkedin.normalized+json+2.1",
            "accept-language": "it-IT,it;q=0.9,en;q=0.8",
            "csrf-token": self.creds.csrf_token,
            "user-agent": USER_AGENT,
            "x-li-lang": "it_IT",
            "x-li-track": json.dumps(track, separators=(",", ":")),
            "x-restli-protocol-version": "2.0.0",
            "referer": "https://www.linkedin.com/",
        }

    # ---- core HTTP ----------------------------------------------------------

    def _throttle(self) -> None:
        if not self.throttle:
            return
        # First request: no prior timestamp to throttle against.
        if self._last_request_at == 0.0:
            return
        target = random.uniform(self.JITTER_MIN_S, self.JITTER_MAX_S)
        delta = time.monotonic() - self._last_request_at
        wait = target - delta
        if wait > 0:
            time.sleep(wait)

    def _log(self, msg: str) -> None:
        if self.verbose:
            sys.stderr.write(f"[linkedin] {msg}\n")

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        if self.throttle:
            try:
                check_and_increment("api_total")
            except QuotaExceededError as exc:
                raise LinkedInAPIError(str(exc)) from exc
        self._throttle()
        headers = dict(extra_headers or {})
        started = time.monotonic()
        try:
            response = self._client.request(method, url, json=json_body, headers=headers)
        except httpx.HTTPError as exc:
            raise LinkedInAPIError(f"network error: {exc}") from exc
        elapsed = (time.monotonic() - started) * 1000
        self._last_request_at = time.monotonic()
        self._log(f"{method} {url} -> {response.status_code} ({elapsed:.0f} ms)")
        if self.verbose:
            preview = response.text[:5000].replace("\n", " ")
            self._log(f"body[0:5000]: {preview}")

        if response.status_code == 401:
            raise LinkedInAPIError(
                "session expired or invalid — run `linkedin auth login` again",
                status=401,
                body=response.text[:500],
            )
        if response.status_code == 429:
            raise LinkedInAPIError(
                "rate limited by LinkedIn — wait a few minutes and try again",
                status=429,
                body=response.text[:500],
            )
        if response.status_code >= 400:
            raise LinkedInAPIError(
                f"HTTP {response.status_code} on {method} {url}",
                status=response.status_code,
                body=response.text[:500],
            )
        return response

    def get_json(self, url: str, extra_headers: dict[str, str] | None = None) -> dict[str, Any]:
        resp = self._request("GET", url, extra_headers=extra_headers)
        return _safe_json(resp)

    def post_json(
        self,
        url: str,
        body: Any,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {"content-type": "application/json; charset=UTF-8"}
        if extra_headers:
            headers.update(extra_headers)
        resp = self._request("POST", url, json_body=body, extra_headers=headers)
        return _safe_json(resp) if resp.content else {}

    def get_html(self, url: str) -> str:
        """Fetch a LinkedIn web page (not a Voyager API endpoint).

        We override `accept` so the server returns HTML instead of the
        normalized-JSON Voyager would otherwise pick.
        """
        resp = self._request(
            "GET",
            url,
            extra_headers={
                "accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
            },
        )
        return resp.text

    # ---- queryId resolution -------------------------------------------------

    def cache_search_people_query_id(self, query_id: str) -> None:
        """Record the queryId that just worked so we use it directly next time."""
        if query_id and query_id != self._search_query_id:
            self._log(f"cached working search queryId: {query_id}")
        self._search_query_id = query_id

    def get_search_people_query_ids(self) -> list[str]:
        """Return the ordered list of queryIds to try for a people search.

        Order:
          1. The one we've already proven works this session (if any).
          2. The id scraped live from the search results HTML page.
          3. The hardcoded fallbacks declared in `endpoints.py`.

        Duplicates are removed while preserving first-occurrence order.
        """
        candidates: list[str] = []

        if self._search_query_id:
            candidates.append(self._search_query_id)

        scraped = self._extract_search_people_query_id()
        if scraped:
            if scraped not in candidates:
                self._log(f"scraped live search queryId from page: {scraped}")
            candidates.append(scraped)

        candidates.extend(SEARCH_PEOPLE_QUERY_IDS_FALLBACKS_LOCAL)

        seen: set[str] = set()
        ordered: list[str] = []
        for qid in candidates:
            if qid and qid not in seen:
                seen.add(qid)
                ordered.append(qid)
        return ordered

    def _extract_search_people_query_id(self) -> str | None:
        try:
            html = self.get_html(SEARCH_PEOPLE_RESULTS_PAGE)
        except LinkedInAPIError as exc:
            self._log(f"queryId scrape failed: {exc}")
            return None
        match = _SEARCH_QUERY_ID_RE_STRICT.search(html)
        if match:
            return match.group(1)
        match = _SEARCH_QUERY_ID_RE_LOOSE.search(html)
        if match:
            return f"voyagerSearchDashClusters.{match.group(1)}"
        self._log("queryId pattern not found in search page HTML")
        return None


# Alias so the method above can read it without importing inside the body.
SEARCH_PEOPLE_QUERY_IDS_FALLBACKS_LOCAL = tuple(SEARCH_PEOPLE_QUERY_ID_FALLBACKS)


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        return resp.json()
    except json.JSONDecodeError as exc:
        raise LinkedInAPIError(
            f"could not parse JSON response: {exc}",
            status=resp.status_code,
            body=resp.text[:500],
        ) from exc


__all__ = ["LinkedInAPIError", "LinkedInClient", "_tracking_id"]

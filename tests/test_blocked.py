"""Block detection, quota accounting and exit codes — real LinkedInClient, mocked transport.

The client's httpx.Client is swapped for one backed by httpx.MockTransport, so
the full `_request` path (quota check → throttle → send → status mapping) runs
without any network. Every test also asserts that a block is never retried.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from linkedin_cli import main as cli_main
from linkedin_cli.api import (
    LinkedInAPIError,
    LinkedInBlockedError,
    LinkedInClient,
    LinkedInQuotaError,
    jobs,
    quotas,
)
from linkedin_cli.api.jobs import search_jobs
from linkedin_cli.auth import Credentials

CREDS = Credentials(li_at="x", jsessionid='"ajax:test"', member_urn="urn:li:fsd_profile:ME")
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "quotas.json"
    monkeypatch.setattr(quotas, "QUOTAS_PATH", path)
    monkeypatch.setattr(jobs, "GEO_CACHE_PATH", tmp_path / "geo.json")
    monkeypatch.delenv(quotas.JOBS_LIMIT_ENV, raising=False)
    monkeypatch.setattr(LinkedInClient, "JITTER_MIN_S", 0.0)
    monkeypatch.setattr(LinkedInClient, "JITTER_MAX_S", 0.0)
    return path


def _client(handler, *, throttle: bool = True) -> tuple[LinkedInClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = LinkedInClient(CREDS, throttle=throttle)
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(_record), follow_redirects=False)
    return client, seen


def _search_payload() -> dict:
    return json.loads((FIXTURES / "jobs_search_response.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# block mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "response,reason",
    [
        (httpx.Response(429, text="slow down"), "rate_limited"),
        (httpx.Response(999, text="denied"), "denied"),
        (
            httpx.Response(302, headers={"location": "https://www.linkedin.com/checkpoint/challenge/x"}),
            "checkpoint",
        ),
        (
            httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text="<html><div id='captcha-internal'>Security verification</div></html>",
            ),
            "captcha",
        ),
    ],
)
def test_block_signals_raise_blocked_and_never_retry(response, reason) -> None:
    client, seen = _client(lambda req: response)
    with pytest.raises(LinkedInBlockedError) as exc_info:
        search_jobs(client, "data scientist", geo_id="103350119", limit=50)
    assert exc_info.value.reason == reason
    assert len(seen) == 1  # one request, no retry, no second page


def test_login_redirect_is_session_expired_not_blocked() -> None:
    client, _ = _client(
        lambda req: httpx.Response(302, headers={"location": "https://www.linkedin.com/uas/login"})
    )
    with pytest.raises(LinkedInAPIError) as exc_info:
        client.get_json("https://www.linkedin.com/voyager/api/me")
    assert not isinstance(exc_info.value, LinkedInBlockedError)
    assert exc_info.value.status == 401


def test_html_page_mentioning_captcha_is_fine_for_get_html() -> None:
    client, _ = _client(
        lambda req: httpx.Response(
            200, headers={"content-type": "text/html"}, text="<script>recaptchaConfig</script>"
        )
    )
    assert "recaptcha" in client.get_html("https://www.linkedin.com/search/results/people/")


def test_people_search_scrape_does_not_swallow_block() -> None:
    from linkedin_cli.api.search import search_people

    client, seen = _client(lambda req: httpx.Response(999))
    with pytest.raises(LinkedInBlockedError):
        search_people(client, "someone")
    assert len(seen) == 1  # no fallback queryIds tried after the block


# ---------------------------------------------------------------------------
# quota accounting
# ---------------------------------------------------------------------------


def test_jobs_search_counts_api_total_and_jobs(_isolated_state: Path) -> None:
    client, _ = _client(lambda req: httpx.Response(200, json=_search_payload()))
    search_jobs(client, "x", geo_id="103350119")
    snap = quotas.snapshot(quotas.QuotaLimits(), _isolated_state)
    assert snap["jobs"] == {"used": 1, "limit": 30}
    assert snap["api_total"]["used"] == 1  # type: ignore[index]


def test_jobs_quota_exhausted_sends_nothing(_isolated_state: Path, monkeypatch) -> None:
    monkeypatch.setenv(quotas.JOBS_LIMIT_ENV, "1")
    client, seen = _client(lambda req: httpx.Response(200, json=_search_payload()))
    search_jobs(client, "x")
    with pytest.raises(LinkedInQuotaError) as exc_info:
        search_jobs(client, "x")
    assert exc_info.value.kind == "jobs"
    assert len(seen) == 1
    # The refused call consumed nothing.
    assert quotas.snapshot(quotas.QuotaLimits(), _isolated_state)["api_total"]["used"] == 1  # type: ignore[index]


def test_jobs_quota_enforced_even_without_throttle(monkeypatch) -> None:
    monkeypatch.setenv(quotas.JOBS_LIMIT_ENV, "0")
    client, seen = _client(lambda req: httpx.Response(200, json=_search_payload()), throttle=False)
    with pytest.raises(LinkedInQuotaError):
        search_jobs(client, "x")
    assert seen == []


def test_non_jobs_calls_do_not_touch_jobs_quota(_isolated_state: Path) -> None:
    client, _ = _client(lambda req: httpx.Response(200, json={}))
    client.get_json("https://www.linkedin.com/voyager/api/me")
    snap = quotas.snapshot(quotas.QuotaLimits(), _isolated_state)
    assert snap["jobs"]["used"] == 0  # type: ignore[index]
    assert snap["api_total"]["used"] == 1  # type: ignore[index]


# ---------------------------------------------------------------------------
# CLI exit codes
# ---------------------------------------------------------------------------


def _run_cli(monkeypatch, handler, args: list[str]):
    client, seen = _client(handler)
    monkeypatch.setattr(cli_main.CLIContext, "client", lambda self: client)
    result = CliRunner().invoke(cli_main.cli, args)
    return result, seen


def test_cli_jobs_search_outputs_ndjson(monkeypatch) -> None:
    result, _ = _run_cli(
        monkeypatch,
        lambda req: httpx.Response(200, json=_search_payload()),
        ["--json", "jobs", "search", "ai", "--geo-id", "103350119", "--experience", "internship"],
    )
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert [r["job_id"] for r in rows] == ["4000000001", "4000000002", "4000000003"]
    assert rows[0]["url"] == "https://www.linkedin.com/jobs/view/4000000001/"


@pytest.mark.parametrize("status", [429, 999])
def test_cli_blocked_exit_code_3(monkeypatch, status: int) -> None:
    result, seen = _run_cli(
        monkeypatch, lambda req: httpx.Response(status), ["jobs", "search", "x"]
    )
    assert result.exit_code == cli_main.EXIT_BLOCKED == 3
    assert "blocked" in result.stderr
    assert len(seen) == 1


def test_cli_checkpoint_exit_code_3(monkeypatch) -> None:
    result, _ = _run_cli(
        monkeypatch,
        lambda req: httpx.Response(303, headers={"location": "/checkpoint/lg/login-submit"}),
        ["jobs", "get", "4000000001"],
    )
    assert result.exit_code == 3
    assert "checkpoint" in result.stderr


def test_cli_quota_exit_code_4(monkeypatch) -> None:
    monkeypatch.setenv(quotas.JOBS_LIMIT_ENV, "0")
    result, seen = _run_cli(
        monkeypatch, lambda req: httpx.Response(200, json={}), ["jobs", "search", "x"]
    )
    assert result.exit_code == cli_main.EXIT_QUOTA == 4
    assert seen == []


def test_cli_bad_job_id_exit_code_1(monkeypatch) -> None:
    result, seen = _run_cli(monkeypatch, lambda req: httpx.Response(200), ["jobs", "get", "abc"])
    assert result.exit_code == 1
    assert seen == []


def test_cli_quota_command_is_offline(_isolated_state: Path) -> None:
    result = CliRunner().invoke(cli_main.cli, ["--json", "quota"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["jobs"] == {"used": 0, "limit": 30}


def test_cli_geo_set_is_offline(monkeypatch) -> None:
    result = CliRunner().invoke(cli_main.cli, ["--json", "jobs", "geo", "Lisbon", "--set", "42"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["geo_id"] == "42"

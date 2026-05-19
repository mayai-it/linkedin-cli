"""Entry point for the `linkedin` CLI.

    linkedin auth login | status | logout

    linkedin profile get <username-or-url>

    linkedin search people <query> [--company NAME] [--title ROLE]
    linkedin search companies <query>

    linkedin connections list [--limit N]
    linkedin connections pending
    linkedin connections send <profile-id>

    linkedin messages list
    linkedin messages send <profile-id> <text>
"""

from __future__ import annotations

import functools
import sys

import click

from linkedin_cli import __version__
from linkedin_cli.api import LinkedInAPIError, LinkedInClient
from linkedin_cli.api.connections import (
    list_connections,
    list_pending_invitations,
    send_invitation,
)
from linkedin_cli.api.messages import list_conversations, send_message
from linkedin_cli.api.profile import get_profile
from linkedin_cli.api.search import search_companies, search_people
from linkedin_cli.auth import (
    Credentials,
    delete_credentials,
    load_credentials,
    save_credentials,
)
from linkedin_cli.auth.browser_login import BrowserLoginError, browser_login
from linkedin_cli.output import emit, error

# ---------------------------------------------------------------------------
# Shared CLI context
# ---------------------------------------------------------------------------


class CLIContext:
    def __init__(self, as_json: bool, verbose: bool, throttle: bool = True) -> None:
        self.as_json = as_json
        self.verbose = verbose
        self.throttle = throttle

    def require_credentials(self) -> Credentials:
        try:
            creds = load_credentials()
        except RuntimeError as exc:
            error(str(exc))
            sys.exit(2)
        if creds is None:
            error("not authenticated — run `linkedin auth login` first")
            sys.exit(2)
        return creds

    def client(self) -> LinkedInClient:
        return LinkedInClient(
            self.require_credentials(),
            verbose=self.verbose,
            throttle=self.throttle,
        )


pass_ctx = click.make_pass_decorator(CLIContext)


def common_flags(func):
    """Re-declare root `--json` / `--verbose` so flag position doesn't matter."""

    @click.option("--json", "_local_json", is_flag=True, default=False,
                  help="Output one JSON object per line (NDJSON).")
    @click.option("--verbose", "_local_verbose", is_flag=True, default=False,
                  help="Log request details and timings to stderr.")
    @click.option("--no-throttle", "_local_no_throttle", is_flag=True, default=False,
                  help="Skip jitter delay and daily quota checks (at your own risk).")
    @functools.wraps(func)
    def wrapper(
        *args,
        _local_json: bool,
        _local_verbose: bool,
        _local_no_throttle: bool,
        **kwargs,
    ):
        cli_ctx = click.get_current_context().find_object(CLIContext)
        if cli_ctx is not None:
            if _local_json:
                cli_ctx.as_json = True
            if _local_verbose:
                cli_ctx.verbose = True
            if _local_no_throttle:
                cli_ctx.throttle = False
        return func(*args, **kwargs)

    return wrapper


def handle_api_errors(func):
    """Translate LinkedInAPIError into a clean stderr message + exit code."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except LinkedInAPIError as exc:
            error(str(exc))
            sys.exit(2 if exc.status in (401, 403) else 1)

    return wrapper


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    help="CLI for LinkedIn (internal Voyager API) — built for AI agents and developers.",
)
@click.version_option(__version__, prog_name="linkedin")
@click.option("--json", "as_json", is_flag=True, help="Output one JSON object per line (NDJSON).")
@click.option("--verbose", is_flag=True, help="Log request details and timings to stderr.")
@click.option("--no-throttle", "no_throttle", is_flag=True,
              help="Skip jitter delay and daily quota checks (at your own risk).")
@click.pass_context
def cli(ctx: click.Context, as_json: bool, verbose: bool, no_throttle: bool) -> None:
    ctx.obj = CLIContext(as_json=as_json, verbose=verbose, throttle=not no_throttle)


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


@cli.group(help="Manage LinkedIn session cookies.")
def auth() -> None: ...


@auth.command("login", help="Open a browser to capture LinkedIn session cookies.")
@click.option(
    "--headless",
    is_flag=True,
    help="Run Chromium headless (only works if you've already accepted the login on this profile).",
)
@click.option(
    "--timeout",
    type=int,
    default=300,
    show_default=True,
    help="Seconds to wait for the login to complete.",
)
@common_flags
@pass_ctx
def auth_login(ctx: CLIContext, headless: bool, timeout: int) -> None:
    try:
        creds = browser_login(timeout_s=timeout, headless=headless)
    except BrowserLoginError as exc:
        error(str(exc))
        sys.exit(2)

    save_credentials(creds)
    emit(
        {
            "status": "ok",
            "li_at": _mask(creds.li_at),
            "member_urn": creds.member_urn or "(not detected)",
        },
        as_json=ctx.as_json,
    )


@auth.command("status", help="Show whether session cookies are stored.")
@common_flags
@pass_ctx
def auth_status(ctx: CLIContext) -> None:
    try:
        creds = load_credentials()
    except RuntimeError as exc:
        error(str(exc))
        sys.exit(2)
    if creds is None:
        emit({"authenticated": False}, as_json=ctx.as_json)
        sys.exit(2)
    emit(
        {
            "authenticated": True,
            "li_at": _mask(creds.li_at),
            "member_urn": creds.member_urn or "(not detected)",
        },
        as_json=ctx.as_json,
    )


@auth.command("logout", help="Delete saved cookies and encryption key.")
@common_flags
@pass_ctx
def auth_logout(ctx: CLIContext) -> None:
    removed = delete_credentials()
    emit({"removed": removed}, as_json=ctx.as_json)


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------


@cli.group(help="Look up LinkedIn profiles.")
def profile() -> None: ...


@profile.command("get", help="Fetch a profile by public id or full URL.")
@click.argument("username")
@common_flags
@pass_ctx
@handle_api_errors
def profile_get(ctx: CLIContext, username: str) -> None:
    with ctx.client() as client:
        result = get_profile(client, username)
    emit(result.to_dict(), as_json=ctx.as_json)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@cli.group(help="Search LinkedIn.")
def search() -> None: ...


@search.command("people", help="Search for people by name / role.")
@click.argument("query")
@click.option("--company", help="Restrict to people who work at this company.")
@click.option("--title", help="Restrict to people with this job title.")
@common_flags
@pass_ctx
@handle_api_errors
def search_people_cmd(
    ctx: CLIContext,
    query: str,
    company: str | None,
    title: str | None,
) -> None:
    with ctx.client() as client:
        hits = search_people(client, query, company=company, title=title)
    emit([h.to_dict() for h in hits], as_json=ctx.as_json)


@search.command("companies", help="Search for companies by name.")
@click.argument("query")
@common_flags
@pass_ctx
@handle_api_errors
def search_companies_cmd(ctx: CLIContext, query: str) -> None:
    with ctx.client() as client:
        hits = search_companies(client, query)
    emit(hits, as_json=ctx.as_json)


# ---------------------------------------------------------------------------
# connections
# ---------------------------------------------------------------------------


@cli.group(help="Manage your network.")
def connections() -> None: ...


@connections.command("list", help="List your first-degree connections.")
@click.option("--limit", type=int, default=40, show_default=True, help="Max connections to return.")
@common_flags
@pass_ctx
@handle_api_errors
def connections_list_cmd(ctx: CLIContext, limit: int) -> None:
    with ctx.client() as client:
        rows = list_connections(client, limit=limit)
    emit(rows, as_json=ctx.as_json)


@connections.command("pending", help="List incoming invitations awaiting response.")
@common_flags
@pass_ctx
@handle_api_errors
def connections_pending_cmd(ctx: CLIContext) -> None:
    with ctx.client() as client:
        rows = list_pending_invitations(client)
    emit(rows, as_json=ctx.as_json)


@connections.command("send", help="Send a connection request to a profile.")
@click.argument("profile_id")
@click.option("--dry-run", is_flag=True, help="Don't actually send — print what would happen.")
@common_flags
@pass_ctx
@handle_api_errors
def connections_send_cmd(ctx: CLIContext, profile_id: str, dry_run: bool) -> None:
    if dry_run:
        emit({"status": "dry-run", "profile": profile_id}, as_json=ctx.as_json)
        return
    with ctx.client() as client:
        result = send_invitation(client, profile_id)
    emit(result, as_json=ctx.as_json)


# ---------------------------------------------------------------------------
# messages
# ---------------------------------------------------------------------------


@cli.group(help="Read and send LinkedIn messages.")
def messages() -> None: ...


@messages.command("list", help="List your latest conversations.")
@common_flags
@pass_ctx
@handle_api_errors
def messages_list_cmd(ctx: CLIContext) -> None:
    creds = ctx.require_credentials()
    with LinkedInClient(creds, verbose=ctx.verbose) as client:
        convs = list_conversations(client, creds.member_urn)
    emit([c.to_dict() for c in convs], as_json=ctx.as_json)


@messages.command("send", help="Send a 1:1 message to a profile.")
@click.argument("recipient")
@click.argument("text")
@click.option("--dry-run", is_flag=True, help="Don't actually send — print what would happen.")
@common_flags
@pass_ctx
@handle_api_errors
def messages_send_cmd(ctx: CLIContext, recipient: str, text: str, dry_run: bool) -> None:
    if dry_run:
        emit(
            {"status": "dry-run", "recipient": recipient, "length": len(text)},
            as_json=ctx.as_json,
        )
        return
    with ctx.client() as client:
        result = send_message(client, recipient, text)
    emit(result, as_json=ctx.as_json)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _mask(value: str) -> str:
    """Show only the first/last few chars of a secret-like cookie value."""
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}…{value[-4:]}"


if __name__ == "__main__":
    cli()

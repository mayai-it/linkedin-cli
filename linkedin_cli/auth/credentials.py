"""Persisted LinkedIn session cookies, Fernet-encrypted at rest.

Layout under ~/.config/mayai-cli/linkedin/:
    key.bin           # 32-byte url-safe Fernet key, mode 0600
    credentials.json  # JSON with encrypted cookie blob, mode 0600

LinkedIn auth is cookie-based — we capture `li_at` and `JSESSIONID` from a
real browser session via Playwright (see browser_login.py). The CSRF token
that every Voyager request needs is derived from `JSESSIONID`.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

CONFIG_DIR = Path.home() / ".config" / "mayai-cli" / "linkedin"
CREDENTIALS_PATH = CONFIG_DIR / "credentials.json"
KEY_PATH = CONFIG_DIR / "key.bin"


@dataclass
class Credentials:
    """Saved LinkedIn session.

    `cookies` is the full cookie jar captured at login; `li_at` and
    `JSESSIONID` are the two we actually rely on, but we keep the rest so
    Voyager's anti-bot heuristics see a realistic browser.
    """

    li_at: str
    jsessionid: str
    member_urn: str = ""  # urn:li:fsd_profile:XXXXX — needed for some queries
    cookies: dict[str, str] = field(default_factory=dict)

    @property
    def csrf_token(self) -> str:
        # LinkedIn's csrf-token header is the JSESSIONID cookie value with
        # the surrounding double-quote characters stripped. Browsers (and
        # Playwright) preserve those quotes in the raw cookie string, but
        # the web client sends `ajax:NNNN` — *without* quotes — as the
        # csrf-token header, and Voyager 403s if you include them.
        return normalize_csrf(self.jsessionid)


def normalize_csrf(jsessionid: str) -> str:
    """Strip surrounding double-quotes from a JSESSIONID value."""
    value = jsessionid.strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    return value


def _ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _load_or_create_key() -> bytes:
    _ensure_config_dir()
    if KEY_PATH.exists():
        return KEY_PATH.read_bytes()
    key = Fernet.generate_key()
    tmp = KEY_PATH.with_suffix(".tmp")
    tmp.write_bytes(key)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(KEY_PATH)
    return key


def load_credentials() -> Credentials | None:
    if not CREDENTIALS_PATH.exists():
        return None
    with CREDENTIALS_PATH.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)

    if not KEY_PATH.exists():
        raise RuntimeError(
            "credentials present but encryption key is missing — "
            "run `linkedin auth logout` and `linkedin auth login` again"
        )
    key = KEY_PATH.read_bytes()
    try:
        blob = Fernet(key).decrypt(raw["payload_enc"].encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("could not decrypt stored cookies — key/credentials mismatch") from exc

    payload = json.loads(blob)
    return Credentials(
        li_at=payload["li_at"],
        jsessionid=payload["jsessionid"],
        member_urn=payload.get("member_urn", ""),
        cookies=payload.get("cookies", {}),
    )


def save_credentials(creds: Credentials) -> None:
    _ensure_config_dir()
    key = _load_or_create_key()
    blob = json.dumps(
        {
            "li_at": creds.li_at,
            "jsessionid": creds.jsessionid,
            "member_urn": creds.member_urn,
            "cookies": creds.cookies,
        }
    )
    enc = Fernet(key).encrypt(blob.encode("utf-8")).decode("utf-8")

    tmp = CREDENTIALS_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump({"payload_enc": enc}, fh, indent=2)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(CREDENTIALS_PATH)


def delete_credentials() -> bool:
    removed = False
    if CREDENTIALS_PATH.exists():
        CREDENTIALS_PATH.unlink()
        removed = True
    if KEY_PATH.exists():
        KEY_PATH.unlink()
        removed = True
    return removed

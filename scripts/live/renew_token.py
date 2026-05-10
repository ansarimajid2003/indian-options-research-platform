"""
Daily Dhan access token renewal using PIN + TOTP (fully headless, no browser).

Endpoint: POST https://auth.dhan.co/app/generateAccessToken
Params:   dhanClientId, pin, totp (all query params — no body)
Response: {"accessToken": "eyJ...", "expiryTime": "...", ...}

Required env vars:
    DHAN_CLIENT_ID      — numeric client ID (e.g. 1111444766)
    DHAN_PIN            — 6-digit Dhan login PIN
    DHAN_TOTP_SECRET    — base32 TOTP secret from Dhan TOTP enrollment

Optional env vars:
    DHAN_TOKEN_FILE     — path to write the new token (default: .env.live in repo root)

Usage:
    python scripts/live/renew_token.py            # renew + write token file
    python scripts/live/renew_token.py --check    # check current token expiry, no renewal
    python scripts/live/renew_token.py --dry-run  # show TOTP code only, no HTTP call

Importable:
    from scripts.live.renew_token import renew_token, check_token_expiry
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time as _time
from datetime import datetime
from pathlib import Path

import requests

_REPO_ROOT = Path(__file__).parents[2]
_ENDPOINT = "https://auth.dhan.co/app/generateAccessToken"
_DEFAULT_TOKEN_FILE = _REPO_ROOT / ".env.live"

# TOTP window offsets to try (in seconds) — handles clock drift and window boundaries
_TOTP_OFFSETS = [0, 30, -30]


def _totp_code(totp_secret: str, when: float) -> str:
    try:
        import pyotp
    except ImportError as exc:
        raise RuntimeError("pyotp is required for Dhan token renewal; install it in the live venv") from exc
    return pyotp.TOTP(totp_secret).at(when)


def _decode_jwt_exp(token: str) -> float | None:
    """Return JWT exp as Unix timestamp, or None if not parseable."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return float(payload["exp"]) if "exp" in payload else None
    except Exception:
        return None


def check_token_expiry(token: str) -> tuple[float | None, str]:
    """
    Return (remaining_seconds, human_readable_status) for a token.
    remaining_seconds is None if the token can't be decoded.
    """
    exp = _decode_jwt_exp(token)
    if exp is None:
        return None, "cannot decode token expiry"
    remaining = exp - _time.time()
    if remaining <= 0:
        return remaining, "EXPIRED"
    h = int(remaining // 3600)
    m = int((remaining % 3600) // 60)
    return remaining, f"valid for {h}h {m}m"


def renew_token(client_id: str, pin: str, totp_secret: str) -> tuple[str, str]:
    """
    Generate a fresh Dhan access token. Returns (access_token, expiry_str).

    Tries up to 3 TOTP window offsets to handle clock drift / window boundaries.
    Raises RuntimeError if all attempts fail.
    """
    last_error: str = "no attempts made"

    for offset_secs in _TOTP_OFFSETS:
        when = _time.time() + offset_secs
        totp_code = _totp_code(totp_secret, when)

        try:
            resp = requests.post(
                _ENDPOINT,
                params={"dhanClientId": client_id, "pin": pin, "totp": totp_code},
                timeout=20,
            )
        except requests.RequestException as exc:
            last_error = f"network error: {exc}"
            _time.sleep(2)
            continue

        if resp.status_code == 200:
            try:
                data = resp.json()
            except Exception:
                last_error = f"non-JSON response: {resp.text[:120]!r}"
                continue

            token = data.get("accessToken", "")
            expiry = data.get("expiryTime", "unknown")
            if token:
                return token, str(expiry)
            last_error = f"accessToken missing in response: {str(data)[:120]}"
        else:
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]!r}"

        _time.sleep(2)

    raise RuntimeError(f"Token renewal failed after {len(_TOTP_OFFSETS)} attempts — last error: {last_error}")


def write_token_file(token: str, expiry: str, token_file: Path) -> None:
    """Write token to a .env-style file. Overwrites the DHAN_ACCESS_TOKEN line."""
    token_file.parent.mkdir(parents=True, exist_ok=True)

    # Read existing lines (keep any other vars like DHAN_CLIENT_ID)
    lines: list[str] = []
    if token_file.exists():
        lines = token_file.read_text().splitlines()

    # Remove any existing DHAN_ACCESS_TOKEN line
    lines = [ln for ln in lines if not ln.startswith("DHAN_ACCESS_TOKEN=")]

    # Append new token
    lines.append(f"DHAN_ACCESS_TOKEN={token}")
    lines.append(f"# Token expiry: {expiry}")

    token_file.write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Dhan access token renewal")
    parser.add_argument("--check", action="store_true", help="Check current token expiry only — no renewal")
    parser.add_argument("--dry-run", action="store_true", help="Print TOTP code only — no HTTP call")
    parser.add_argument("--token-file", default=str(_DEFAULT_TOKEN_FILE),
                        help=f"File to write renewed token (default: {_DEFAULT_TOKEN_FILE})")
    args = parser.parse_args()

    client_id = os.environ.get("DHAN_CLIENT_ID", "")
    pin = os.environ.get("DHAN_PIN", "")
    totp_secret = os.environ.get("DHAN_TOTP_SECRET", "")

    if args.check:
        token = os.environ.get("DHAN_ACCESS_TOKEN", "")
        if not token:
            print("FAIL  no DHAN_ACCESS_TOKEN in environment")
            return 1
        remaining, status = check_token_expiry(token)
        print(f"token_expiry: {status}")
        return 0 if (remaining is not None and remaining > 0) else 1

    if args.dry_run:
        if not totp_secret:
            print("FAIL  DHAN_TOTP_SECRET not set")
            return 1
        try:
            code = _totp_code(totp_secret, _time.time())
            next_code = _totp_code(totp_secret, _time.time() + 30)
        except RuntimeError as exc:
            print(f"FAIL  {exc}")
            return 1
        print(f"TOTP code (current window): {code}")
        print(f"TOTP code (next window +30s): {next_code}")
        return 0

    missing = [v for v, val in [("DHAN_CLIENT_ID", client_id), ("DHAN_PIN", pin), ("DHAN_TOTP_SECRET", totp_secret)] if not val]
    if missing:
        print(f"FAIL  missing env vars: {', '.join(missing)}")
        return 1

    print(f"renew_token: requesting new token for client_id=...{client_id[-4:]}")

    try:
        token, expiry = renew_token(client_id, pin, totp_secret)
    except RuntimeError as exc:
        print(f"FAIL  {exc}")
        return 1

    remaining, status = check_token_expiry(token)
    print(f"PASS  new token obtained — expiry={expiry} ({status})")

    token_file = Path(args.token_file)
    write_token_file(token, expiry, token_file)
    print(f"PASS  token written to {token_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

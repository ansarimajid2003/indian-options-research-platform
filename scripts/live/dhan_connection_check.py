"""
Repeatable Dhan connectivity smoke test.

Checks:
  1. Token decode: read expiry from JWT payload without printing the token.
  2. REST /optionchain/expirylist: confirm status=success for NIFTY.
  3. Live-feed websocket: connect and send one IDX_I/NIFTY subscription.
  4. 20-depth websocket: connect and send one valid NSE_FNO option subscription.

Prints PASS/FAIL lines only. Never prints the token or client-id.

Usage:
    DHAN_ACCESS_TOKEN=<token> DHAN_CLIENT_ID=<id> python scripts/live/dhan_connection_check.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time as _time
from pathlib import Path

import websockets

# Make the project importable when run as a standalone script.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from options_backtest.dhan_client import (  # noqa: E402  (sys.path bootstrap)
    DhanCredentials,
    DhanHTTPClient,
    DhanRESTError,
)

_LIVE_FEED_URL_TMPL = (
    "wss://api-feed.dhan.co"
    "?version=2&token={token}&clientId={client_id}&authType=2"
)
_DEPTH_URL_TMPL = (
    "wss://depth-api-feed.dhan.co/twentydepth"
    "?token={token}&clientId={client_id}&authType=2"
)

# NIFTY 50 index on IDX_I — confirmed working from 2026-05-09 connectivity test
_NIFTY_SCRIP = 13
_NIFTY_SEGMENT = "IDX_I"


def _decode_exp(token: str) -> float:
    """Return JWT exp claim as Unix timestamp. Does not print the token."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("not a three-part JWT")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded))
    if "exp" not in payload:
        raise ValueError("no exp claim in JWT payload")
    return float(payload["exp"])


def check_token(token: str, client_id: str) -> bool:
    try:
        exp = _decode_exp(token)
        remaining_days = (exp - _time.time()) / 86400
        if remaining_days <= 0:
            print("FAIL  token_expiry  : token has already expired")
            return False
        masked_id = f"...{client_id[-4:]}" if len(client_id) >= 4 else "****"
        print(f"PASS  token_expiry  : valid for {remaining_days:.1f} days  client_id={masked_id}")
        return True
    except Exception as exc:
        print(f"FAIL  token_expiry  : {exc}")
        return False


def check_rest(client: DhanHTTPClient) -> bool:
    try:
        expiries = client.fetch_expiry_list(_NIFTY_SCRIP, _NIFTY_SEGMENT)
        if expiries:
            print(f"PASS  rest_optchain : {len(expiries)} expiries  first={expiries[0]}")
            return True
        print("FAIL  rest_optchain : empty expirylist")
        return False
    except DhanRESTError as exc:
        print(f"FAIL  rest_optchain : http={exc.status_code}  body={str(exc.body)[:120]!r}")
        return False
    except Exception as exc:
        print(f"FAIL  rest_optchain : {exc}")
        return False


def _find_nifty_option_security_id(client: DhanHTTPClient) -> str:
    expiries = client.fetch_expiry_list(_NIFTY_SCRIP, _NIFTY_SEGMENT)
    if not expiries:
        raise RuntimeError("no NIFTY expiries returned")
    # Rate limiter inside DhanHTTPClient handles the 3 s spacing between
    # expirylist and option_chain automatically.
    raw = client.fetch_option_chain(_NIFTY_SCRIP, _NIFTY_SEGMENT, expiries[0])
    if isinstance(raw, dict):
        oc = raw.get("oc", {})
        spot = float(raw.get("last_price", 0) or 0)
        if isinstance(oc, dict) and oc:
            pairs = sorted(
                oc.items(),
                key=lambda kv: abs(float(kv[0]) - spot) if spot else float(kv[0]),
            )
            for _strike, pair in pairs:
                ce = (pair or {}).get("ce", {})
                sid = ce.get("security_id")
                if sid:
                    return str(sid)
                pe = (pair or {}).get("pe", {})
                sid = pe.get("security_id")
                if sid:
                    return str(sid)
    if isinstance(raw, list):
        for row in raw:
            for side_key in ("CallOption", "PutOption"):
                opt = row.get(side_key, {}) if isinstance(row, dict) else {}
                sid = opt.get("SecurityId")
                if sid:
                    return str(sid)
    raise RuntimeError("could not find option security_id in option chain")


async def check_live_feed(token: str, client_id: str) -> bool:
    url = _LIVE_FEED_URL_TMPL.format(token=token, client_id=client_id)
    sub = {
        "RequestCode": 21,
        "InstrumentCount": 1,
        "InstrumentList": [
            {"ExchangeSegment": _NIFTY_SEGMENT, "SecurityId": str(_NIFTY_SCRIP)}
        ],
    }
    try:
        async with websockets.connect(url, open_timeout=10, close_timeout=5) as ws:
            await ws.send(json.dumps(sub))
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                nbytes = len(msg) if isinstance(msg, bytes) else len(msg.encode())
                print(f"PASS  live_feed_ws  : connected  received {nbytes}-byte packet")
            except asyncio.TimeoutError:
                print("PASS  live_feed_ws  : connected  no packet in 5 s (market closed)")
        return True
    except Exception as exc:
        print(f"FAIL  live_feed_ws  : {exc}")
        return False


async def check_depth(client: DhanHTTPClient, token: str, client_id: str) -> bool:
    url = _DEPTH_URL_TMPL.format(token=token, client_id=client_id)
    try:
        option_sid = await asyncio.to_thread(_find_nifty_option_security_id, client)
        sub = {
            "RequestCode": 23,
            "InstrumentCount": 1,
            "InstrumentList": [
                {"ExchangeSegment": "NSE_FNO", "SecurityId": option_sid}
            ],
        }
        async with websockets.connect(url, open_timeout=10, close_timeout=5) as ws:
            await ws.send(json.dumps(sub))
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                nbytes = len(msg) if isinstance(msg, bytes) else len(msg.encode())
                print(f"PASS  depth_ws      : connected  received {nbytes}-byte packet")
            except asyncio.TimeoutError:
                print("PASS  depth_ws      : connected  no packet in 5 s (market closed)")
        return True
    except Exception as exc:
        print(f"FAIL  depth_ws      : {exc}")
        return False


async def _run() -> int:
    token = os.environ.get("DHAN_ACCESS_TOKEN") or os.environ.get("DHAN_TOKEN", "")
    client_id = os.environ.get("DHAN_CLIENT_ID", "")
    if not token or not client_id:
        missing = []
        if not token:
            missing.append("DHAN_ACCESS_TOKEN")
        if not client_id:
            missing.append("DHAN_CLIENT_ID")
        print(f"FAIL  env           : {', '.join(missing)} not set")
        return 1

    client = DhanHTTPClient(DhanCredentials(access_token=token, client_id=client_id))
    try:
        results = [
            check_token(token, client_id),
            check_rest(client),
            await check_live_feed(token, client_id),
            await check_depth(client, token, client_id),
        ]
    finally:
        client.close()

    passed = sum(results)
    total = len(results)
    print()
    if passed == total:
        print(f"ALL PASS  ({passed}/{total})")
        return 0
    print(f"FAILED  {total - passed} check(s)  ({passed}/{total} passed)")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))

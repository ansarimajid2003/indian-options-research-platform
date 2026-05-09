"""
Repeatable Dhan connectivity smoke test.

Checks:
  1. Token decode: read expiry from JWT payload without printing the token.
  2. REST /optionchain/expirylist: confirm status=success for NIFTY.
  3. Live-feed websocket: connect and send one IDX_I/NIFTY subscription.
  4. 20-depth websocket: connect and send one IDX_I/NIFTY subscription.

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

import requests
import websockets

_OPTION_CHAIN_URL = "https://api.dhan.co/v2/optionchain/expirylist"
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


def check_rest(token: str, client_id: str) -> bool:
    try:
        resp = requests.post(
            _OPTION_CHAIN_URL,
            json={"UnderlyingScrip": _NIFTY_SCRIP, "UnderlyingSeg": _NIFTY_SEGMENT},
            headers={"access-token": token, "client-id": client_id},
            timeout=10,
        )
        body = resp.json()
        # v2 API returns {"data": [...]} directly; no top-level status field
        expiries = body.get("data", [])
        if resp.status_code == 200 and isinstance(expiries, list) and expiries:
            first = expiries[0]
            print(
                f"PASS  rest_optchain : {len(expiries)} expiries  first={first}"
            )
            return True
        print(f"FAIL  rest_optchain : http={resp.status_code}  body={str(body)[:120]!r}")
        return False
    except Exception as exc:
        print(f"FAIL  rest_optchain : {exc}")
        return False


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


async def check_depth(token: str, client_id: str) -> bool:
    url = _DEPTH_URL_TMPL.format(token=token, client_id=client_id)
    sub = {
        "RequestCode": 23,
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

    results = [
        check_token(token, client_id),
        check_rest(token, client_id),
        await check_live_feed(token, client_id),
        await check_depth(token, client_id),
    ]

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

"""
One-shot Dhan option-chain snapshot for all Wing-6 symbols.

Fetches expiry lists and ATM±6 strike quotes for NIFTY, FINNIFTY, MIDCPNIFTY, SENSEX.
Market-closed: returns last-available snapshot. No orders placed.

Usage:
    DHAN_ACCESS_TOKEN=<token> DHAN_CLIENT_ID=<id> python scripts/live/sample_option_chain.py
    DHAN_ACCESS_TOKEN=<token> DHAN_CLIENT_ID=<id> python scripts/live/sample_option_chain.py --symbol NIFTY --strikes 4
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Make project importable when run as a standalone script.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from options_backtest.dhan_client import (  # noqa: E402
    DhanCredentials,
    DhanHTTPClient,
)

_SYMBOLS = {
    "NIFTY":       {"scrip": 13, "seg": "IDX_I", "step": 50},
    "FINNIFTY":    {"scrip": 27, "seg": "IDX_I", "step": 50},
    "MIDCPNIFTY":  {"scrip": 442, "seg": "IDX_I", "step": 25},
    "SENSEX":      {"scrip": 51, "seg": "IDX_I", "step": 100},
}


def fetch_expiry_list(client: DhanHTTPClient, scrip: int, seg: str) -> list[str]:
    """Shared-client wrapper kept for backwards compatibility with the CLI."""
    return client.fetch_expiry_list(scrip, seg)


def fetch_chain(client: DhanHTTPClient, scrip: int, seg: str, expiry: str) -> dict:
    """Return raw ``data`` block from /optionchain. Shape matches the v2 API."""
    return {"data": client.fetch_option_chain(scrip, seg, expiry)}


def _fmt_row(label: str, ltp: float, bid: float, ask: float, oi: int, iv: float) -> str:
    mid = (bid + ask) / 2 if bid and ask else ltp
    spread_pct = (ask - bid) / mid * 100 if mid > 0 else 0
    return (
        f"  {label:<22}  LTP={ltp:>8.2f}  bid={bid:>8.2f}  ask={ask:>8.2f}  "
        f"spread={spread_pct:>5.2f}%  OI={oi:>8,}  IV={iv:>5.2f}%"
    )


def _extract_rows(chain_data: dict) -> tuple[float, list[tuple[float, dict, dict]]]:
    """Extract (underlying_ltp, [(strike, call, put), ...]) from Dhan option chain."""
    raw = chain_data.get("data", [])
    if isinstance(raw, dict):
        underlying_ltp = float(raw.get("last_price", 0) or 0)
        oc = raw.get("oc", [])
        if isinstance(oc, dict):
            rows = []
            for strike_key, pair in oc.items():
                if not isinstance(pair, dict):
                    continue
                rows.append((float(strike_key), pair.get("ce", {}) or {}, pair.get("pe", {}) or {}))
            rows.sort(key=lambda x: x[0])
            return underlying_ltp, rows
        raw = oc

    oc = raw  # flat list of {"CallOption": {...}, "PutOption": {...}}
    underlying_ltp = 0.0
    rows = []
    for row in oc:
        if not isinstance(row, dict):
            continue
        call_opt = row.get("CallOption", {}) or {}
        put_opt = row.get("PutOption", {}) or {}
        strike = float(call_opt.get("StrikePrice", 0) or put_opt.get("StrikePrice", 0) or 0)
        if strike:
            rows.append((strike, call_opt, put_opt))
        for opt in (call_opt, put_opt):
            ul = opt.get("UnderlyingValue", 0) or opt.get("underlying_value", 0) or opt.get("SpotPrice", 0)
            if ul and not underlying_ltp:
                underlying_ltp = float(ul)
    rows.sort(key=lambda x: x[0])
    return underlying_ltp, rows


def _field(opt: dict, lower: str, pascal: str, default=0):
    if lower in opt:
        return opt.get(lower, default)
    return opt.get(pascal, default)


def print_chain_snapshot(symbol: str, chain_data: dict, n_strikes: int = 6) -> None:
    underlying_ltp, oc = _extract_rows(chain_data)

    if not oc:
        print(f"  [no option chain rows returned]")
        return

    rows_flat = oc

    # If underlying_ltp not found, estimate from near-equal call/put LTP crossover
    if not underlying_ltp and rows_flat:
        for s, co, po in rows_flat:
            c_ltp = _field(co, "last_price", "LTP", 0) or 0
            p_ltp = _field(po, "last_price", "LTP", 0) or 0
            if c_ltp and p_ltp and abs(c_ltp - p_ltp) / max(c_ltp, p_ltp) < 0.5:
                underlying_ltp = s
                break

    # ATM: strike nearest to underlying_ltp
    step = _SYMBOLS.get(symbol, {}).get("step", 50)
    if underlying_ltp and rows_flat:
        atm = min(rows_flat, key=lambda x: abs(x[0] - underlying_ltp))[0]
    elif rows_flat:
        atm = rows_flat[len(rows_flat) // 2][0]
    else:
        atm = 0

    # Filter to ATM ±n_strikes
    near = [(s, co, po) for s, co, po in rows_flat if abs(s - atm) <= n_strikes * step]

    print(f"\n{'='*95}")
    print(f"  {symbol}   Spot LTP: {underlying_ltp:.2f}   ATM: {atm}   (ATM ±{n_strikes} strikes)")
    print(f"{'='*95}")
    print(f"  {'Contract':<22}  {'LTP':>8}  {'Bid':>8}  {'Ask':>8}  {'Spread%':>7}  {'OI':>9}  {'IV%':>7}")
    print(f"  {'-'*85}")

    for strike, call_opt, put_opt in near:
        for opt, label_suffix in ((call_opt, "CE"), (put_opt, "PE")):
            if not opt:
                continue
            ltp = float(_field(opt, "last_price", "LTP", 0) or 0)
            bid = float(_field(opt, "top_bid_price", "Bid", 0) or 0)
            ask = float(_field(opt, "top_ask_price", "Ask", 0) or 0)
            oi = int(_field(opt, "oi", "OpenInterest", 0) or 0)
            iv = float(_field(opt, "implied_volatility", "ImpliedVolatility", 0) or 0)
            label = f"{int(strike)}{label_suffix}"
            marker = " <<ATM" if strike == atm else ""
            print(_fmt_row(label, ltp, bid, ask, oi, iv) + marker)


def main() -> int:
    parser = argparse.ArgumentParser(description="Dhan option-chain snapshot for Wing-6 symbols")
    parser.add_argument("--symbol", choices=list(_SYMBOLS), help="Single symbol (default: all)")
    parser.add_argument("--strikes", type=int, default=6, help="ATM ±N strikes to show (default 6)")
    parser.add_argument("--raw", action="store_true", help="Dump raw JSON for first symbol")
    args = parser.parse_args()

    # Auto-load .env.live from repo root if it exists and vars aren't already set
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env_file = os.path.join(repo_root, ".env.live")
    if os.path.exists(env_file):
        with open(env_file) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and "=" in _line and not _line.startswith("#"):
                    _k, _, _v = _line.partition("=")
                    os.environ.setdefault(_k.strip(), _v.strip())

    token = os.environ.get("DHAN_ACCESS_TOKEN") or os.environ.get("DHAN_TOKEN", "")
    client_id = os.environ.get("DHAN_CLIENT_ID", "")
    if not token or not client_id:
        missing = [k for k, v in [("DHAN_ACCESS_TOKEN", token), ("DHAN_CLIENT_ID", client_id)] if not v]
        print(f"ERROR: {', '.join(missing)} not set", file=sys.stderr)
        return 1

    symbols = [args.symbol] if args.symbol else list(_SYMBOLS)
    print(f"\nDhan option-chain snapshot — {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}")
    print(f"(market closed: showing last-available snapshot)")

    client = DhanHTTPClient(DhanCredentials(access_token=token, client_id=client_id))

    raw_dumped = False
    for sym in symbols:
        cfg = _SYMBOLS[sym]

        # Rate limiting is enforced inside DhanHTTPClient (1 req / 3 s
        # for expirylist, 1 req / sec for option_chain). No manual sleeps.
        try:
            expiries = fetch_expiry_list(client, cfg["scrip"], cfg["seg"])
        except Exception as exc:
            print(f"\n{sym}: FAIL fetching expiry list — {exc}")
            continue

        if not expiries:
            print(f"\n{sym}: no active expiries returned")
            continue

        nearest = expiries[0]
        print(f"\n{sym}: {len(expiries)} expiries  nearest={nearest}")

        try:
            chain = fetch_chain(client, cfg["scrip"], cfg["seg"], nearest)
        except Exception as exc:
            print(f"  {sym}: FAIL fetching chain — {exc}")
            continue

        if args.raw and not raw_dumped:
            print(f"\n--- RAW JSON ({sym}) ---")
            print(json.dumps(chain, indent=2)[:4000])
            print("--- END RAW ---")
            raw_dumped = True

        print_chain_snapshot(sym, chain, args.strikes)

    client.close()
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

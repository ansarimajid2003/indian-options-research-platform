"""
probe_idx_feed.py — Diagnostic: what packet types does Dhan send for IDX_I instruments?

Connects to the Dhan live feed websocket, subscribes the 6 core IDX instruments
with both Full(21) and Ticker(15), then logs every packet type received for 60 s.

Run during market hours (09:15–15:30 IST) to observe real behaviour.

Usage:
    python -m scripts.live.probe_idx_feed [--duration 60]
    # or from project root:
    DHAN_ACCESS_TOKEN=... DHAN_CLIENT_ID=... python scripts/live/probe_idx_feed.py

Output: per-second summary of packet types received per security_id.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import struct
import sys
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

_IST = ZoneInfo("Asia/Kolkata")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("probe_idx_feed")
logging.getLogger("websockets").setLevel(logging.WARNING)

_FEED_URL = "wss://api-feed.dhan.co?version=2&token={token}&clientId={client_id}&authType=2"

# Security IDs for IDX_I segment
_IDX_INSTRUMENTS = {
    "13": "NIFTY",
    "27": "FINNIFTY",
    "442": "MIDCPNIFTY",
    "51": "SENSEX",
    "25": "BANKNIFTY",
    "21": "INDIA_VIX",
}

# Packet type names
_PTYPE_NAMES = {
    2: "Ticker(2/16B)",
    3: "MktDepth(3)",
    4: "Quote(4/50B)",
    5: "OHLCVol(5)",
    6: "PrevClose(6)",
    7: "OI(7)",
    8: "Full(8/162B)",
    50: "Disconnect(50)",
}


def _now() -> str:
    return datetime.now(tz=_IST).strftime("%H:%M:%S")


def _parse_ltp(raw: bytes, ptype: int) -> float | None:
    try:
        if ptype == 2 and len(raw) >= 12:
            return round(struct.unpack_from("<f", raw, 8)[0], 2)
        if ptype == 4 and len(raw) >= 12:
            return round(struct.unpack_from("<f", raw, 8)[0], 2)
        if ptype == 8 and len(raw) >= 12:
            return round(struct.unpack_from("<f", raw, 8)[0], 2)
    except Exception:
        pass
    return None


def _parse_sid(raw: bytes) -> str | None:
    try:
        if len(raw) >= 8:
            return str(struct.unpack_from("<I", raw, 4)[0])
    except Exception:
        pass
    return None


async def probe(access_token: str, client_id: str, duration: int) -> None:
    import websockets

    url = _FEED_URL.format(token=access_token, client_id=client_id)
    # packet_type → {sid → count}
    counts: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    unknown_ptypes: set[int] = set()
    total_bytes = 0
    total_packets = 0

    _log.info("Connecting to %s", url.split("?")[0])

    async with websockets.connect(url, open_timeout=15, close_timeout=5, ping_interval=20) as ws:
        _log.info("Connected — subscribing IDX_I instruments")

        # Subscribe Full(21)
        full_sub = {
            "RequestCode": 21,
            "InstrumentCount": len(_IDX_INSTRUMENTS),
            "InstrumentList": [
                {"ExchangeSegment": "IDX_I", "SecurityId": sid}
                for sid in _IDX_INSTRUMENTS
            ],
        }
        await ws.send(json.dumps(full_sub))
        _log.info("Sent Full(21) subscription for %d IDX_I instruments", len(_IDX_INSTRUMENTS))

        # Also subscribe Ticker(15) as belt-and-suspenders
        ticker_sub = {
            "RequestCode": 15,
            "InstrumentCount": len(_IDX_INSTRUMENTS),
            "InstrumentList": [
                {"ExchangeSegment": "IDX_I", "SecurityId": sid}
                for sid in _IDX_INSTRUMENTS
            ],
        }
        await ws.send(json.dumps(ticker_sub))
        _log.info("Sent Ticker(15) subscription for %d IDX_I instruments", len(_IDX_INSTRUMENTS))

        _log.info("Listening for %d seconds...", duration)
        deadline = asyncio.get_event_loop().time() + duration
        last_report = asyncio.get_event_loop().time()

        async for raw in ws:
            if not isinstance(raw, bytes) or not raw:
                continue

            total_bytes += len(raw)
            total_packets += 1
            ptype = raw[0]
            sid = _parse_sid(raw)
            ltp = _parse_ltp(raw, ptype)

            if ptype not in _PTYPE_NAMES and ptype not in unknown_ptypes:
                unknown_ptypes.add(ptype)
                _log.warning("UNKNOWN packet type=%d len=%d sid=%s", ptype, len(raw), sid)

            if sid:
                counts[ptype][sid] += 1

            sym = _IDX_INSTRUMENTS.get(sid or "", sid)
            name = _PTYPE_NAMES.get(ptype, f"UNKNOWN({ptype})")
            ltp_str = f" ltp={ltp}" if ltp is not None else ""
            _log.debug("[%s] ptype=%s len=%d sid=%s(%s)%s", _now(), name, len(raw), sid, sym, ltp_str)

            now_mono = asyncio.get_event_loop().time()
            if now_mono - last_report >= 10:
                _print_summary(counts)
                last_report = now_mono

            if now_mono >= deadline:
                break

    _log.info("=== FINAL SUMMARY (%d seconds, %d packets, %d bytes) ===", duration, total_packets, total_bytes)
    _print_summary(counts)
    _log.info("Unknown packet types seen: %s", sorted(unknown_ptypes))


def _print_summary(counts: dict) -> None:
    _log.info("--- Packet counts by type and security_id ---")
    for ptype in sorted(counts):
        name = _PTYPE_NAMES.get(ptype, f"UNKNOWN({ptype})")
        for sid in sorted(counts[ptype]):
            sym = _IDX_INSTRUMENTS.get(sid, sid)
            _log.info("  %-20s sid=%-4s (%s): %d packets", name, sid, sym, counts[ptype][sid])
    seen_sids = {sid for sid_counts in counts.values() for sid in sid_counts}
    missing = {sid: sym for sid, sym in _IDX_INSTRUMENTS.items() if sid not in seen_sids}
    if missing:
        _log.warning("  NO DATA received for: %s", {v: k for k, v in missing.items()})
    else:
        _log.info("  All %d IDX_I instruments received data.", len(_IDX_INSTRUMENTS))


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe IDX_I websocket packet types")
    parser.add_argument("--duration", type=int, default=60, help="Listen duration in seconds (default 60)")
    args = parser.parse_args()

    token = os.environ.get("DHAN_ACCESS_TOKEN") or os.environ.get("DHAN_TOKEN")
    client = os.environ.get("DHAN_CLIENT_ID") or os.environ.get("CLIENT_ID")

    if not token or not client:
        print("ERROR: Set DHAN_ACCESS_TOKEN and DHAN_CLIENT_ID environment variables", file=sys.stderr)
        sys.exit(1)

    asyncio.run(probe(token, client, args.duration))


if __name__ == "__main__":
    main()

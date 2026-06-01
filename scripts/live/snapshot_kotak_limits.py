"""
Standalone Kotak Neo broker-snapshot writer for the account/margin tracker.

This script is NOT imported by engine code. It is the only place allowed to make
network calls to Kotak. It calls ``NeoAPI.limits()`` for the live account and
writes a normalized snapshot to ``{live_root}/account/kotak_limits_{YYYYMMDD}.json``
which the OFFLINE ``options_backtest.account_state`` module then consumes as the
preferred (broker_snapshot) margin tier.

The vendored Kotak SDK lives under ``third_party/Kotak-neo-api-v2`` (package
``neo_api_client``). It is imported LAZILY inside the functions that need it so
``normalize_limits`` and the unit tests run without the SDK or its deps installed
(mirroring how the DhanHQ SDK is referenced from ``third_party`` elsewhere).

Env vars required for the CLI (read from environment only; never hardcode):
    KOTAK_ACCESS_TOKEN   - Neo access token (from the Kotak developer portal)
    KOTAK_CONSUMER_KEY   - consumer key
    KOTAK_MOBILE         - registered mobile number (for totp_login)
    KOTAK_UCC            - UCC / client code (for totp_login)
    KOTAK_TOTP           - current 6-digit TOTP
    KOTAK_MPIN           - 6-digit MPIN (for totp_validate / 2FA)
    KOTAK_ENV            - 'prod' or 'uat' (default 'prod')
    LIVE_ROOT            - live data root (default 'data/live')
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_KOTAK_SDK_DIR = _REPO_ROOT / "third_party" / "Kotak-neo-api-v2"


def normalize_limits(raw_limits_response: dict, now: datetime | None = None) -> dict:
    """Pure given an injected ``now``: map a raw Kotak ``limits()`` response to a clean snapshot dict.

    No network. Missing keys -> None so the result is always engine-consumable.
    """

    def _num(key):
        val = raw_limits_response.get(key)
        if val is None or val == "":
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    return {
        "broker_net": _num("Net"),
        "broker_margin_used": _num("MarginUsed"),
        "broker_collateral": _num("Collateral"),
        "broker_fo_unrealized": _num("FoUnRlsMtomPrsnt"),
        "broker_fo_realized": _num("FoRlsMtomPrsnt"),
        "captured_at": (now or datetime.now()).isoformat(timespec="seconds"),
        "stat": raw_limits_response.get("stat"),
        "stCode": raw_limits_response.get("stCode"),
    }


def fetch_and_write_snapshot(client, live_root: Path, session_date: date) -> Path:
    """Call ``client.limits(...)``, normalize, write snapshot atomically.

    ``client`` is injected (tests pass a Mock) so this stays unit-testable.
    """
    raw = client.limits(segment="ALL", exchange="ALL", product="ALL")
    if isinstance(raw, dict) and ("Error Message" in raw or "Error" in raw):
        raise RuntimeError(f"Kotak limits() failed: {raw}")

    snapshot = normalize_limits(raw)

    account_dir = Path(live_root) / "account"
    account_dir.mkdir(parents=True, exist_ok=True)
    out_path = account_dir / f"kotak_limits_{session_date.strftime('%Y%m%d')}.json"
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, out_path)
    logger.info("wrote broker snapshot %s margin_used=%s net=%s",
                out_path, snapshot["broker_margin_used"], snapshot["broker_net"])
    return out_path


def naked_leg_margin(
    client,
    exchange_segment: str,
    price: float,
    order_type: str,
    product: str,
    quantity: int,
    instrument_token: int,
    transaction_type: str,
) -> float:
    """Diagnostic only. margin_required is SINGLE-LEG, so this is the NAKED margin
    for one leg, NOT the hedged Iron Condor margin. Summing 4 legs overstates IC
    margin ~3x; use limits().MarginUsed for the real hedged figure."""
    resp = client.margin_required(
        exchange_segment=exchange_segment,
        price=price,
        order_type=order_type,
        product=product,
        quantity=quantity,
        instrument_token=instrument_token,
        transaction_type=transaction_type,
    )
    return float(resp["data"]["ordMrgn"])


def _build_client():
    """Lazy SDK import + real 2FA login from env vars. Network-touching."""
    if str(_KOTAK_SDK_DIR) not in sys.path:
        sys.path.insert(0, str(_KOTAK_SDK_DIR))
    from neo_api_client import NeoAPI  # noqa: E402  (lazy: keeps module importable without SDK)

    access_token = os.environ.get("KOTAK_ACCESS_TOKEN")
    consumer_key = os.environ.get("KOTAK_CONSUMER_KEY")
    mobile = os.environ.get("KOTAK_MOBILE")
    ucc = os.environ.get("KOTAK_UCC")
    totp = os.environ.get("KOTAK_TOTP")
    mpin = os.environ.get("KOTAK_MPIN")
    env = os.environ.get("KOTAK_ENV", "prod")

    missing = [
        name for name, val in [
            ("KOTAK_ACCESS_TOKEN", access_token),
            ("KOTAK_CONSUMER_KEY", consumer_key),
            ("KOTAK_MPIN", mpin),
        ] if not val
    ]
    if missing:
        raise SystemExit(f"missing required env vars: {', '.join(missing)}")

    client = NeoAPI(access_token=access_token, consumer_key=consumer_key, environment=env)
    if mobile or ucc or totp:
        client.totp_login(mobile_number=mobile, ucc=ucc, totp=totp)
    client.totp_validate(mpin=mpin)
    return client


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Write a normalized Kotak limits snapshot")
    parser.add_argument("--date", default=None, help="Session date YYYYMMDD (default today)")
    parser.add_argument("--live-root", default=os.environ.get("LIVE_ROOT", "data/live"))
    args = parser.parse_args()

    if args.date:
        session_date = date(int(args.date[:4]), int(args.date[4:6]), int(args.date[6:8]))
    else:
        session_date = date.today()

    try:
        client = _build_client()
    except SystemExit as exc:
        logger.error("%s", exc)
        return 2

    out_path = fetch_and_write_snapshot(client, Path(args.live_root), session_date)
    logger.info("snapshot written: %s", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

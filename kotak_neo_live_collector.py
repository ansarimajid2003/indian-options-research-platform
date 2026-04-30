"""
Kotak Neo — Live 1-Minute Nifty 50 Data Collector
==================================================
Authenticates with Kotak Neo API, subscribes to NIFTY 50 real-time feed
via WebSocket, aggregates ticks into 1-minute OHLCV candles, and appends
them to the master dataset (nifty50_1min_FINAL.csv).

Usage:
    1. Fill in your credentials in the CONFIG section below
    2. Run:  python kotak_neo_live_collector.py
    3. The script runs during market hours and auto-stops at 15:30 IST

Requirements:
    pip install "git+https://github.com/Kotak-Neo/Kotak-neo-api-v2.git@v2.0.1#egg=neo_api_client"
    pip install pyotp pandas

SEBI Static IP note:
    Your machine's public IP must be whitelisted with Kotak Neo.
    Check your IP at whatismyip.com then email tradeapi@kotak.com to register it.
"""

import time
import json
import threading
import pandas as pd
import pyotp
from datetime import datetime, date
from pathlib import Path

try:
    from neo_api_client import NeoAPI
except ImportError:
    raise SystemExit(
        'Install SDK first:\n'
        '  pip install "git+https://github.com/Kotak-Neo/Kotak-neo-api-v2.git@v2.0.1#egg=neo_api_client"'
    )

# ── CONFIG — fill these in ────────────────────────────────────────────────────
CONSUMER_KEY    = "YOUR_CONSUMER_KEY"        # from Kotak Neo App > More > Trade API
MOBILE_NUMBER   = "+91XXXXXXXXXX"            # your registered mobile number
UCC             = "YOUR_UCC"                 # Unique Client Code from Kotak Neo profile
MPIN            = "XXXXXX"                   # 6-digit MPIN
TOTP_SECRET     = "YOUR_TOTP_SECRET"         # base32 secret from TOTP QR scan

MASTER_CSV      = Path("C:/Users/ansar/Documents/indian markets/nifty50_1min_CANONICAL.csv")
SESSION_CSV     = Path("C:/Users/ansar/Documents/indian markets/nifty50_1min_today.csv")

# NIFTY 50 index token in Kotak Neo (NSE segment, instrument_token for index feed)
# Run get_nifty_token() below first if you are unsure
NIFTY_TOKEN     = "26000"   # standard NSE token for NIFTY 50 index
EXCHANGE        = "nse_cm"  # NSE cash market segment

MARKET_OPEN     = "09:15"
MARKET_CLOSE    = "15:31"
# ──────────────────────────────────────────────────────────────────────────────

# In-memory accumulator for the current 1-minute candle
_current_bar = {}
_bar_lock = threading.Lock()
_candles = []

def _now_ist():
    from datetime import timezone, timedelta
    return datetime.now(timezone(timedelta(hours=5, minutes=30)))

def _market_open():
    now = _now_ist()
    open_t  = now.replace(hour=9,  minute=15, second=0,  microsecond=0)
    close_t = now.replace(hour=15, minute=31, second=0,  microsecond=0)
    return open_t <= now <= close_t

def on_message(message):
    """Called for every live tick from the WebSocket."""
    global _current_bar, _candles

    try:
        data = json.loads(message) if isinstance(message, str) else message
    except Exception:
        return

    ltp = data.get("ltp") or data.get("last_traded_price")
    vol = data.get("vol") or data.get("volume") or 0
    if ltp is None:
        return

    now = _now_ist()
    minute_ts = now.replace(second=0, microsecond=0)

    with _bar_lock:
        if _current_bar.get("ts") != minute_ts:
            if _current_bar:
                _candles.append(dict(_current_bar))
                bar = _current_bar
                print(
                    "[{}]  O:{:.2f}  H:{:.2f}  L:{:.2f}  C:{:.2f}  V:{}".format(
                        bar["ts"].strftime("%H:%M"),
                        bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"]
                    )
                )
                _flush_candles()
            _current_bar = {
                "ts": minute_ts,
                "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                "volume": vol,
            }
        else:
            _current_bar["high"]   = max(_current_bar["high"], ltp)
            _current_bar["low"]    = min(_current_bar["low"], ltp)
            _current_bar["close"]  = ltp
            _current_bar["volume"] = vol

def on_error(msg):
    print("WebSocket error:", msg)

def on_close(msg):
    print("WebSocket closed:", msg)

def on_open(msg):
    print("WebSocket connected:", msg)

def _flush_candles():
    """Append completed candles to session CSV."""
    if not _candles:
        return
    rows = []
    for c in _candles:
        rows.append({
            "datetime": c["ts"].strftime("%Y-%m-%d %H:%M:%S"),
            "open":   round(c["open"],  2),
            "high":   round(c["high"],  2),
            "low":    round(c["low"],   2),
            "close":  round(c["close"], 2),
            "volume": int(c["volume"]),
            "source": "kotak_neo_live",
        })
    df = pd.DataFrame(rows)
    header = not SESSION_CSV.exists()
    df.to_csv(SESSION_CSV, mode="a", index=False, header=header)
    _candles.clear()

def merge_session_into_master():
    """Append today's session data into the master CSV, deduplicating."""
    if not SESSION_CSV.exists():
        print("No session file to merge.")
        return

    session = pd.read_csv(SESSION_CSV, parse_dates=["datetime"])
    if MASTER_CSV.exists():
        master  = pd.read_csv(MASTER_CSV, parse_dates=["datetime"])
        combined = pd.concat([master, session], ignore_index=True)
        combined.drop_duplicates(subset=["datetime"], keep="last", inplace=True)
        combined.sort_values("datetime", inplace=True)
    else:
        combined = session

    combined.to_csv(MASTER_CSV, index=False)
    print("Merged {} new candles into master dataset.".format(len(session)))

def get_nifty_token(client):
    """Print the NIFTY 50 index instrument token (run once if unsure)."""
    print("Searching for NIFTY 50 token...")
    results = client.search_scrip(exchange_segment="nse_cm", symbol="NIFTY 50")
    print(results)

def authenticate():
    """Full 2-step Kotak Neo authentication."""
    print("Initialising Kotak Neo client...")
    client = NeoAPI(
        consumer_key=CONSUMER_KEY,
        access_token=None,
        neo_fin_key=None,
        environment="prod",
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        on_open=on_open,
    )

    print("Step 1: Login with mobile + UCC + TOTP...")
    totp_code = pyotp.TOTP(TOTP_SECRET).now()
    resp = client.totp_login(
        mobile_number=MOBILE_NUMBER,
        ucc=UCC,
        totp=totp_code,
    )
    print("TOTP login response:", resp)

    print("Step 2: Validate MPIN to generate trade token...")
    resp2 = client.totp_validate(mpin=MPIN)
    print("TOTP validate response:", resp2)

    return client

def main():
    if CONSUMER_KEY == "YOUR_CONSUMER_KEY":
        print("ERROR: Fill in your credentials in the CONFIG section at the top of this file.")
        return

    client = authenticate()

    print("\nWaiting for market hours ({} - {} IST)...".format(MARKET_OPEN, MARKET_CLOSE))
    while not _market_open():
        time.sleep(10)

    print("Market is open. Subscribing to NIFTY 50 live feed...")
    instrument_tokens = [{"instrument_token": NIFTY_TOKEN, "exchange_segment": EXCHANGE}]
    client.subscribe(instrument_tokens=instrument_tokens, isIndex=True)

    try:
        while _market_open():
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        client.un_subscribe(instrument_tokens=instrument_tokens, isIndex=True)
        with _bar_lock:
            if _current_bar:
                _candles.append(dict(_current_bar))
        _flush_candles()
        merge_session_into_master()
        print("Done. Session data saved.")

if __name__ == "__main__":
    main()

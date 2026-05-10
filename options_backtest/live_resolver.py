"""
Live contract resolver backed by Dhan option-chain REST and websocket quote state.

Mirrors the interface of ContractResolver but draws data from live feeds
instead of parquet files. Used exclusively by the paper trading engine.

Thread-safety: quote_cache and chain_cache are protected by separate locks.
The paper engine reads from a single thread (the trading loop); the feed
listener writes from the websocket receive loop. Locks are therefore
lightweight fine-grained rwlock equivalents using threading.Lock.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import pandas as pd
import requests

from .calendar import get_instrument_spec
from .schemas import Contract, OptionType

_IST = "Asia/Kolkata"
_OPTION_CHAIN_URL = "https://api.dhan.co/v2/optionchain"
_EXPIRY_LIST_URL = "https://api.dhan.co/v2/optionchain/expirylist"

# Freshness limits per the plan (seconds)
_SPOT_MAX_AGE = 5
_OPTION_MAX_AGE = 5
_VIX_MAX_AGE = 60
_DEPTH_MAX_AGE = 5

# Rolling quote deque size (number of ticks kept per security)
_DEQUE_MAXLEN = 500


class StaleQuoteError(Exception):
    """Raised when a required quote is outside its freshness limit."""


@dataclass
class QuoteEntry:
    ltp: float
    open: float
    high: float
    low: float
    close: float
    volume: int
    oi: int
    received_at: pd.Timestamp


@dataclass
class _ChainEntry:
    security_id: str
    strike: int
    option_type: OptionType
    expiry: date
    ticker: str
    greeks: dict = field(default_factory=dict)
    iv: float | None = None


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _normalise_option_chain_rows(raw_data) -> tuple[float, list[tuple[int, OptionType, dict]]]:
    """Return (underlying_ltp, [(strike, option_type, option_payload), ...]).

    Dhan's documented v2 response is data.oc.{strike}.ce/pe with lower-case
    field names. Older/internal responses may arrive as a flat list containing
    CallOption/PutOption payloads with PascalCase field names. The resolver
    accepts both because live trading should fail on missing data, not on a
    cosmetic response-shape change.
    """
    rows: list[tuple[int, OptionType, dict]] = []
    underlying_ltp = 0.0

    if isinstance(raw_data, dict):
        underlying_ltp = _as_float(raw_data.get("last_price"))
        oc = raw_data.get("oc", {})
        if isinstance(oc, dict):
            for strike_key, pair in oc.items():
                if not isinstance(pair, dict):
                    continue
                strike = _as_int(strike_key)
                for side_key, option_type in (("ce", OptionType.CALL), ("pe", OptionType.PUT)):
                    opt = pair.get(side_key) or {}
                    if isinstance(opt, dict) and opt:
                        rows.append((strike, option_type, opt))
            return underlying_ltp, rows
        if isinstance(oc, list):
            raw_data = oc

    if isinstance(raw_data, list):
        for row in raw_data:
            if not isinstance(row, dict):
                continue
            for side_key, option_type in (("CallOption", OptionType.CALL), ("PutOption", OptionType.PUT)):
                opt = row.get(side_key) or {}
                if not isinstance(opt, dict) or not opt:
                    continue
                strike = _as_int(opt.get("StrikePrice") or opt.get("strike_price"))
                ul = _as_float(
                    opt.get("UnderlyingValue")
                    or opt.get("underlying_value")
                    or opt.get("SpotPrice")
                )
                if ul and not underlying_ltp:
                    underlying_ltp = ul
                rows.append((strike, option_type, opt))

    return underlying_ltp, rows


def _option_field(opt: dict, lower_name: str, pascal_name: str, default=None):
    if lower_name in opt:
        return opt.get(lower_name)
    return opt.get(pascal_name, default)


class LiveDhanContractResolver:
    """
    Live resolver for the Wing-6 paper trading engine.

    Usage:
        resolver = LiveDhanContractResolver(
            symbol="NIFTY",
            access_token=os.environ["DHAN_ACCESS_TOKEN"],
            client_id=os.environ["DHAN_CLIENT_ID"],
        )
        # call refresh_option_chain() once before entry; then:
        contract = resolver.resolve_atm_offset(ts, offset_steps=2, option_type=OptionType.CALL)
    """

    def __init__(
        self,
        symbol: str,
        access_token: str,
        client_id: str,
        spot_security_id: str,
        vix_security_id: str = "21",
    ) -> None:
        self.symbol = symbol
        self._access_token = access_token
        self._client_id = client_id
        self._spot_security_id = spot_security_id
        self._vix_security_id = vix_security_id

        spec = get_instrument_spec(symbol)
        self._strike_step = spec.strike_step

        # quote_cache: security_id -> QuoteEntry
        self._quote_lock = threading.Lock()
        self._quote_cache: dict[str, QuoteEntry] = {}

        # rolling deque: security_id -> deque[QuoteEntry]
        self._deque_lock = threading.Lock()
        self._quote_deque: dict[str, deque] = {}

        # option chain: (expiry_str, strike, option_type) -> _ChainEntry
        self._chain_lock = threading.Lock()
        self._chain: dict[tuple, _ChainEntry] = {}
        self._active_expiry: date | None = None
        self._chain_underlying_ltp: float | None = None

        # security_id -> contract (reverse map for feed updates)
        self._id_to_contract: dict[str, _ChainEntry] = {}

    # ─── Quote cache (written by websocket feed listener) ────────────────────

    def update_quote(
        self,
        security_id: str,
        ltp: float,
        open_: float = 0.0,
        high: float = 0.0,
        low: float = 0.0,
        volume: int = 0,
        oi: int = 0,
        received_at: pd.Timestamp | None = None,
    ) -> None:
        """Called by the live feed listener for every LTP/full packet."""
        ts = received_at or pd.Timestamp.now(tz=_IST)
        entry = QuoteEntry(
            ltp=ltp,
            open=open_,
            high=high,
            low=low,
            close=ltp,
            volume=volume,
            oi=oi,
            received_at=ts,
        )
        with self._quote_lock:
            self._quote_cache[security_id] = entry
        with self._deque_lock:
            if security_id not in self._quote_deque:
                self._quote_deque[security_id] = deque(maxlen=_DEQUE_MAXLEN)
            self._quote_deque[security_id].append(entry)

    def _get_quote(self, security_id: str, max_age: int) -> QuoteEntry:
        with self._quote_lock:
            entry = self._quote_cache.get(security_id)
        if entry is None:
            raise StaleQuoteError(f"No quote yet for security_id={security_id}")
        age = (pd.Timestamp.now(tz=_IST) - entry.received_at).total_seconds()
        if age > max_age:
            raise StaleQuoteError(
                f"Quote for {security_id} is {age:.1f}s old (limit {max_age}s)"
            )
        return entry

    # ─── Option chain (written by REST refresh) ──────────────────────────────

    def refresh_option_chain(self, expiry: date, scrip_id: int, segment: str = "IDX_I") -> list[str]:
        """
        Fetch option chain from Dhan REST and populate the chain cache.

        Returns a list of security_ids that should be subscribed to the live feed.
        Rate limited: call once per expiry, not in a tight loop.
        """
        headers = {
            "access-token": self._access_token,
            "client-id": self._client_id,
            "Content-Type": "application/json",
        }
        body = {
            "UnderlyingScrip": scrip_id,
            "UnderlyingSeg": segment,
            "Expiry": expiry.strftime("%Y-%m-%d"),
        }
        resp = requests.post(_OPTION_CHAIN_URL, json=body, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        underlying_ltp, option_rows = _normalise_option_chain_rows(data.get("data", []))
        if not option_rows:
            raise ValueError(f"Empty option chain for {self.symbol} expiry {expiry}")

        new_chain: dict[tuple, _ChainEntry] = {}
        new_id_map: dict[str, _ChainEntry] = {}
        security_ids: list[str] = []

        for strike, ot, opt in option_rows:
            sid = str(_option_field(opt, "security_id", "SecurityId", "") or "")
            if not sid or sid == "0" or strike <= 0:
                continue
            ticker = _option_field(
                opt,
                "trading_symbol",
                "TradingSymbol",
                f"{self.symbol}{expiry.strftime('%y%b').upper()}{strike}{ot.value}",
            )
            greeks = _option_field(opt, "greeks", "Greeks", {}) or {}
            iv = _option_field(opt, "implied_volatility", "ImpliedVolatility", None)
            entry = _ChainEntry(
                security_id=sid,
                strike=strike,
                option_type=ot,
                expiry=expiry,
                ticker=str(ticker),
                greeks=dict(greeks) if isinstance(greeks, dict) else {},
                iv=_as_float(iv, default=0.0) if iv is not None else None,
            )
            key = (expiry.isoformat(), strike, ot)
            new_chain[key] = entry
            new_id_map[sid] = entry
            security_ids.append(sid)

        with self._chain_lock:
            self._chain = new_chain
            self._id_to_contract = new_id_map
            self._active_expiry = expiry
            self._chain_underlying_ltp = underlying_ltp if underlying_ltp > 0 else None

        return security_ids

    def fetch_expiry_list(self, scrip_id: int, segment: str = "IDX_I") -> list[date]:
        """Return active expiry dates for the symbol from Dhan REST."""
        headers = {
            "access-token": self._access_token,
            "client-id": self._client_id,
        }
        body = {"UnderlyingScrip": scrip_id, "UnderlyingSeg": segment}
        resp = requests.post(_EXPIRY_LIST_URL, json=body, headers=headers, timeout=10)
        resp.raise_for_status()
        body = resp.json()
        raw = body.get("data", [])
        # v2 returns data as a flat list of "YYYY-MM-DD" strings (confirmed May 2026)
        # Defensively also handle {"Expirylist": [...]} shape from older SDK versions
        if isinstance(raw, dict):
            raw = raw.get("Expirylist", [])
        expiries = []
        for e in raw:
            try:
                expiries.append(datetime.strptime(str(e), "%Y-%m-%d").date())
            except ValueError:
                continue
        return sorted(expiries)

    # ─── Resolver interface (mirrors ContractResolver) ────────────────────────

    def atm_strike(self, timestamp: pd.Timestamp) -> int:
        """Return ATM strike using fresh spot LTP, rounded to strike_step."""
        entry = self._get_quote(self._spot_security_id, _SPOT_MAX_AGE)
        return int(round(entry.ltp / self._strike_step) * self._strike_step)

    def resolve_atm_offset(
        self, timestamp: pd.Timestamp, offset_steps: int, option_type: OptionType
    ) -> Contract:
        """Resolve Wing-N leg via option-chain security_id. Raises StaleQuoteError if spot stale."""
        atm = self.atm_strike(timestamp)
        target_strike = atm + offset_steps * self._strike_step

        if self._active_expiry is None:
            raise StaleQuoteError(f"Option chain not loaded for {self.symbol}")

        key = (self._active_expiry.isoformat(), target_strike, option_type)
        with self._chain_lock:
            entry = self._chain.get(key)

        if entry is None:
            raise KeyError(
                f"Strike {target_strike} {option_type.value} not in chain for "
                f"{self.symbol} expiry {self._active_expiry}"
            )

        return Contract(
            expiry=entry.expiry,
            strike=entry.strike,
            option_type=entry.option_type,
            ticker=entry.ticker,
        )

    def security_id_for(self, contract: Contract) -> str | None:
        """Return Dhan security_id for a resolved contract."""
        key = (contract.expiry.isoformat(), contract.strike, contract.option_type)
        with self._chain_lock:
            entry = self._chain.get(key)
        return entry.security_id if entry else None

    def chain_atm_strike(self) -> int | None:
        """Return ATM strike from latest option-chain underlying LTP."""
        with self._chain_lock:
            ltp = self._chain_underlying_ltp
        if ltp is None:
            return None
        return int(round(ltp / self._strike_step) * self._strike_step)

    def security_ids_around_chain_atm(self, offset_range: int = 20) -> list[str]:
        """Return CE/PE security ids for strikes within ATM +/- offset_range."""
        with self._chain_lock:
            ltp = self._chain_underlying_ltp
            atm = int(round(ltp / self._strike_step) * self._strike_step) if ltp else None
            if atm is None and self._chain:
                strikes = sorted({strike for _, strike, _ in self._chain})
                atm = strikes[len(strikes) // 2] if strikes else None
            if atm is None:
                return []
            lo = atm - offset_range * self._strike_step
            hi = atm + offset_range * self._strike_step
            entries = [
                entry
                for (_, strike, _), entry in self._chain.items()
                if lo <= strike <= hi
            ]
        return [entry.security_id for entry in sorted(entries, key=lambda e: (e.strike, e.option_type.value))]

    def chain_metadata(self, security_ids: list[str] | None = None) -> dict[str, dict]:
        """Return IV/Greeks diagnostics keyed by Dhan security id."""
        wanted = set(security_ids) if security_ids is not None else None
        with self._chain_lock:
            entries = list(self._id_to_contract.items())
        out: dict[str, dict] = {}
        for sid, entry in entries:
            if wanted is not None and sid not in wanted:
                continue
            out[sid] = {"greeks": entry.greeks, "iv": entry.iv}
        return out

    def bar_at(self, contract: Contract, timestamp: pd.Timestamp) -> pd.Series | None:
        """Return latest quote as a Series compatible with engine bar format.

        Returns None if quote is stale or missing. Never falls back to old prices.
        """
        sid = self.security_id_for(contract)
        if sid is None:
            return None
        try:
            q = self._get_quote(sid, _OPTION_MAX_AGE)
        except StaleQuoteError:
            return None
        return pd.Series(
            {
                "open": q.open,
                "high": q.high,
                "low": q.low,
                "close": q.close,
                "volume": q.volume,
                "oi": q.oi,
            },
            name=timestamp,
        )

    def bars_for(self, contract: Contract) -> pd.DataFrame:
        """Return the rolling intraday quote deque as a DataFrame."""
        sid = self.security_id_for(contract)
        if sid is None:
            return pd.DataFrame()
        with self._deque_lock:
            entries = list(self._quote_deque.get(sid, []))
        if not entries:
            return pd.DataFrame()
        rows = [
            {
                "timestamp": e.received_at,
                "open": e.open,
                "high": e.high,
                "low": e.low,
                "close": e.close,
                "volume": e.volume,
                "oi": e.oi,
            }
            for e in entries
        ]
        return pd.DataFrame(rows).set_index("timestamp")

    def vix_ltp(self) -> float:
        """Return current VIX LTP. Raises StaleQuoteError if too old."""
        return self._get_quote(self._vix_security_id, _VIX_MAX_AGE).ltp

    def spot_ltp(self) -> float:
        """Return current spot LTP. Raises StaleQuoteError if too old."""
        return self._get_quote(self._spot_security_id, _SPOT_MAX_AGE).ltp

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import pandas as pd


SESSION_START = time(9, 15)
SESSION_END = time(15, 30)

NIFTY_LAST_THURSDAY_EXPIRY = date(2025, 8, 28)
NIFTY_FIRST_TUESDAY_EXPIRY = date(2025, 9, 2)
NIFTY_LAST_THURSDAY_MONTHLY_EXPIRY = date(2025, 8, 28)
_THURSDAY = 3
_TUESDAY = 1
_WEDNESDAY = 2
_MONDAY = 0
_FRIDAY = 4

# ──────────────────────────────────────────────────────────────────────────────
# NSE lot-size history (NIFTY 50 index derivatives)
# Source: NSE circulars FAOP30449, FAOP47854, SEBI/HO/MRD/TPD-1/P/CIR/2024/132,
#         NSE/FAOP/70616.  Boundaries are the first weekly expiry date at which
#         the new lot size became effective.
# ──────────────────────────────────────────────────────────────────────────────
_LOT_SIZE_SCHEDULE: tuple[tuple[date, int], ...] = (
    (date(2025, 12, 30), 65),   # NSE/FAOP/70616, effective Dec 30 2025
    (date(2024, 11, 20), 75),   # SEBI/HO/MRD/TPD-1/P/CIR/2024/132
    (date(2021, 7,  1),  50),   # NSE/FAOP/47854, effective for Jul 2021 contracts
    (date(2015, 10, 30), 75),   # NSE/FAOP/30449
    (date(2014, 10, 31), 25),   # SEBI CIR/MRD/DP/14/2015 (Oct 2014–Oct 2015 window)
    (date(2007, 2,  23), 50),
    (date(2005, 4,  1),  100),
    (date(2000, 6,  12), 200),
)


def nifty_lot_size(trade_date: date) -> int:
    """Return the NIFTY 50 lot size that was in effect on *trade_date*."""
    for effective_from, size in _LOT_SIZE_SCHEDULE:
        if trade_date >= effective_from:
            return size
    return 200  # pre-Jun-2000 fallback


@dataclass(frozen=True)
class InstrumentSpec:
    """Exchange-contract metadata needed by the backtest engine."""

    symbol: str
    dhan_folder: str
    display_name: str
    strike_step: int
    default_lot_size: int
    lot_size_schedule: tuple[tuple[date, int], ...]
    weekly_discontinued_after: date | None = None
    spot_csv: str | None = None


def _normalise_symbol(symbol: str | None) -> str:
    value = (symbol or "NIFTY").upper().replace(" ", "").replace("-", "").replace("_", "")
    aliases = {
        "NIFTY": "NIFTY",
        "NIFTY50": "NIFTY",
        "BANKNIFTY": "BANKNIFTY",
        "NIFTYBANK": "BANKNIFTY",
        "FINNIFTY": "FINNIFTY",
        "NIFTYFINSERVICE": "FINNIFTY",
        "NIFTYFINANCIALSERVICES": "FINNIFTY",
        "MIDCPNIFTY": "MIDCPNIFTY",
        "MIDCAPNIFTY": "MIDCPNIFTY",
        "NIFTYMIDCAPSELECT": "MIDCPNIFTY",
        "SENSEX": "SENSEX",
        "BSESENSEX": "SENSEX",
    }
    if value not in aliases:
        raise ValueError(f"Unsupported index symbol: {symbol}")
    return aliases[value]


# Boundaries are trade-date approximations for backtests. Around exchange
# transition months, circulars apply by contract introduction; this table uses
# the first practical front-expiry date where a backtest should use the new
# contract multiplier.
INSTRUMENT_SPECS: dict[str, InstrumentSpec] = {
    "NIFTY": InstrumentSpec(
        symbol="NIFTY",
        dhan_folder="nifty",
        display_name="NIFTY 50",
        strike_step=50,
        default_lot_size=200,
        lot_size_schedule=(
            (date(2025, 12, 31), 65),  # NSE/FAOP/70616: Jan 2026 contracts onward
            (date(2024, 11, 21), 75),  # NSE/FAOP/64625: Jan 2025 contracts onward
            (date(2024, 4, 26), 25),   # NSE/FAOP/61791: May/Jul 2024 contracts onward
            (date(2021, 7, 1), 50),    # NSE/FAOP/47854: Jul 2021 contracts onward
            (date(2015, 10, 30), 75),
            (date(2014, 10, 31), 25),
            (date(2007, 2, 23), 50),
            (date(2005, 4, 1), 100),
            (date(2000, 6, 12), 200),
        ),
        spot_csv="data/processed/spot/nifty50_1min_CANONICAL.csv",
    ),
    "BANKNIFTY": InstrumentSpec(
        symbol="BANKNIFTY",
        dhan_folder="banknifty",
        display_name="NIFTY BANK",
        strike_step=100,
        default_lot_size=25,
        lot_size_schedule=(
            (date(2025, 12, 31), 30),
            (date(2025, 7, 31), 35),
            (date(2024, 11, 21), 30),
            (date(2023, 7, 28), 15),
            (date(2000, 1, 1), 25),
        ),
        weekly_discontinued_after=date(2024, 11, 13),
        spot_csv="data/processed/spot/banknifty_1min_DHAN.csv",
    ),
    "FINNIFTY": InstrumentSpec(
        symbol="FINNIFTY",
        dhan_folder="finnifty",
        display_name="NIFTY FIN SERVICE",
        strike_step=50,
        default_lot_size=40,
        lot_size_schedule=(
            (date(2025, 12, 31), 60),
            (date(2024, 11, 21), 65),
            (date(2024, 7, 30), 25),
            (date(2021, 1, 11), 40),
        ),
        weekly_discontinued_after=date(2024, 11, 19),
        spot_csv="data/processed/spot/finnifty_1min_DHAN.csv",
    ),
    "MIDCPNIFTY": InstrumentSpec(
        symbol="MIDCPNIFTY",
        dhan_folder="midcpnifty",
        display_name="NIFTY MIDCAP SELECT",
        strike_step=25,
        default_lot_size=75,
        lot_size_schedule=(
            (date(2025, 12, 31), 120),
            (date(2025, 7, 31), 140),
            (date(2024, 11, 21), 120),
            (date(2024, 7, 29), 50),
            (date(2022, 1, 24), 75),
        ),
        weekly_discontinued_after=date(2024, 11, 18),
        spot_csv="data/processed/spot/midcpnifty_1min_DHAN.csv",
    ),
    "SENSEX": InstrumentSpec(
        symbol="SENSEX",
        dhan_folder="sensex",
        display_name="BSE SENSEX",
        strike_step=100,
        default_lot_size=10,
        lot_size_schedule=(
            (date(2025, 1, 10), 20),   # BSE circular Nov 28 2024; first weekly w/ new lot
            (date(2020, 1, 1), 10),    # pre-revision lot size
        ),
        spot_csv="data/processed/spot/sensex_1min_DHAN.csv",
    ),
}


def get_instrument_spec(symbol: str | None = "NIFTY") -> InstrumentSpec:
    return INSTRUMENT_SPECS[_normalise_symbol(symbol)]


def lot_size(symbol: str, trade_date: date) -> int:
    """Return the index-derivatives lot size in effect for *symbol*."""
    spec = get_instrument_spec(symbol)
    for effective_from, size in spec.lot_size_schedule:
        if trade_date >= effective_from:
            return size
    return spec.default_lot_size


def nifty_lot_size(trade_date: date) -> int:
    """Compatibility wrapper for older callers."""
    return lot_size("NIFTY", trade_date)


# ──────────────────────────────────────────────────────────────────────────────
# NSE trading calendar
# ──────────────────────────────────────────────────────────────────────────────

# Rare Saturday/Sunday sessions where NSE F&O is open.
NSE_SPECIAL_SESSIONS: frozenset[date] = frozenset({
    date(2025, 2, 1),   # Union Budget Day 2025 — special Saturday session
})

# NSE F&O market holidays (weekday-only; weekend dates are caught by the
# dayofweek check in is_trading_day).  Source: NSE annual holiday circulars.
# Covers 2015-2026.  Fixed-date holidays (Republic Day, Independence Day,
# Gandhi Jayanti, Christmas, Dr Ambedkar Jayanti) are included every year
# they fall on a weekday; variable-date holidays are included where confirmed.
NSE_HOLIDAYS: frozenset[date] = frozenset({
    # ── 2015 ──────────────────────────────────────────────────────────────────
    date(2015, 1, 26),   # Republic Day
    date(2015, 2, 17),   # Mahashivratri
    date(2015, 3,  6),   # Holi
    date(2015, 4,  2),   # Ram Navami
    date(2015, 4,  3),   # Good Friday
    date(2015, 4, 14),   # Dr Ambedkar Jayanti
    date(2015, 5,  1),   # Maharashtra Day
    date(2015, 7, 17),   # Bakri Id
    date(2015, 8, 14),   # Independence Day (observed — Aug 15 was Saturday)
    date(2015, 11, 11),  # Diwali (Laxmi Puja)
    date(2015, 11, 12),  # Diwali Balipratipada
    date(2015, 11, 25),  # Gurunanak Jayanti
    date(2015, 12, 25),  # Christmas
    # ── 2016 ──────────────────────────────────────────────────────────────────
    date(2016, 1, 26),   # Republic Day
    date(2016, 3,  7),   # Mahashivratri
    date(2016, 3, 24),   # Holi
    date(2016, 3, 25),   # Good Friday
    date(2016, 4, 14),   # Dr Ambedkar Jayanti
    date(2016, 4, 19),   # Ram Navami
    date(2016, 5, 21),   # Buddha Purnima
    date(2016, 7,  6),   # Eid-ul-Fitr
    date(2016, 7, 19),   # Id-ul-Adha (Bakri Id)
    date(2016, 8, 15),   # Independence Day
    date(2016, 9,  5),   # Ganesh Chaturthi (observed)
    date(2016, 10, 11),  # Dussehra
    date(2016, 10, 31),  # Diwali (Laxmi Puja)
    date(2016, 11, 14),  # Gurunanak Jayanti
    date(2016, 12, 25),  # Christmas (Sunday — observed Mon Dec 26 by some, NSE gave Mon)
    # ── 2017 ──────────────────────────────────────────────────────────────────
    date(2017, 1, 26),   # Republic Day
    date(2017, 2, 24),   # Mahashivratri
    date(2017, 3, 13),   # Holi
    date(2017, 4,  4),   # Ram Navami
    date(2017, 4, 14),   # Good Friday / Dr Ambedkar Jayanti
    date(2017, 5,  1),   # Maharashtra Day
    date(2017, 6, 26),   # Eid-ul-Fitr
    date(2017, 8, 15),   # Independence Day
    date(2017, 8, 25),   # Janmashtami
    date(2017, 9, 25),   # Dussehra
    date(2017, 10,  2),  # Gandhi Jayanti
    date(2017, 10, 19),  # Diwali (Laxmi Puja)
    date(2017, 10, 20),  # Diwali Balipratipada
    date(2017, 11,  3),  # Gurunanak Jayanti
    date(2017, 12, 25),  # Christmas
    # ── 2018 ──────────────────────────────────────────────────────────────────
    date(2018, 1, 26),   # Republic Day
    date(2018, 2, 13),   # Mahashivratri
    date(2018, 3,  2),   # Holi
    date(2018, 3, 29),   # Good Friday
    date(2018, 4, 30),   # Buddha Purnima
    date(2018, 5,  1),   # Maharashtra Day
    date(2018, 6, 15),   # Eid-ul-Fitr
    date(2018, 8, 15),   # Independence Day
    date(2018, 8, 22),   # Ganesh Chaturthi
    date(2018, 9, 13),   # Muharram (approx)
    date(2018, 9, 20),   # Dussehra (approx — actual may vary)
    date(2018, 10,  2),  # Gandhi Jayanti
    date(2018, 11,  7),  # Diwali (Laxmi Puja)
    date(2018, 11,  8),  # Diwali Balipratipada
    date(2018, 11, 23),  # Gurunanak Jayanti
    date(2018, 12, 25),  # Christmas
    # ── 2019 ──────────────────────────────────────────────────────────────────
    date(2019, 3,  4),   # Mahashivratri
    date(2019, 3, 21),   # Holi
    date(2019, 4, 17),   # Ram Navami
    date(2019, 4, 19),   # Good Friday
    date(2019, 4, 29),   # Maharashtra Day (observed)
    date(2019, 5, 18),   # Buddha Purnima
    date(2019, 6,  5),   # Eid-ul-Fitr
    date(2019, 8, 12),   # Muharram
    date(2019, 8, 15),   # Independence Day
    date(2019, 9,  2),   # Ganesh Chaturthi
    date(2019, 9, 10),   # Id-ul-Adha
    date(2019, 10,  2),  # Gandhi Jayanti
    date(2019, 10,  7),  # Dussehra
    date(2019, 10,  8),  # Dussehra (NSE sometimes gives 2 days)
    date(2019, 10, 28),  # Diwali (Laxmi Puja)
    date(2019, 11, 12),  # Gurunanak Jayanti
    date(2019, 12, 25),  # Christmas
    # ── 2020 ──────────────────────────────────────────────────────────────────
    date(2020, 2, 21),   # Mahashivratri
    date(2020, 3, 10),   # Holi
    date(2020, 4,  2),   # Ram Navami
    date(2020, 4,  6),   # Mahavir Jayanti (approx)
    date(2020, 4, 10),   # Good Friday
    date(2020, 4, 14),   # Dr Ambedkar Jayanti
    date(2020, 5,  1),   # Maharashtra Day
    date(2020, 5, 25),   # Eid-ul-Fitr
    date(2020, 7, 31),   # Bakri Id
    date(2020, 8,  3),   # Muharram (approx)
    date(2020, 10,  2),  # Gandhi Jayanti
    date(2020, 11, 14),  # Diwali (Laxmi Puja)
    date(2020, 11, 16),  # Gurunanak Jayanti
    date(2020, 11, 30),  # Gurunanak Jayanti (observed — varies)
    date(2020, 12, 25),  # Christmas
    # ── 2021 ──────────────────────────────────────────────────────────────────
    date(2021, 1, 26),   # Republic Day
    date(2021, 3, 11),   # Mahashivratri
    date(2021, 3, 29),   # Holi
    date(2021, 4,  2),   # Good Friday
    date(2021, 4, 14),   # Dr Ambedkar Jayanti
    date(2021, 5, 13),   # Eid-ul-Fitr
    date(2021, 7, 20),   # Bakri Id
    date(2021, 8, 19),   # Muharram
    date(2021, 11,  4),  # Diwali (Laxmi Puja)
    date(2021, 11,  5),  # Diwali Balipratipada
    date(2021, 11, 19),  # Gurunanak Jayanti
    # ── 2022 ──────────────────────────────────────────────────────────────────
    date(2022, 1, 26),   # Republic Day
    date(2022, 3,  1),   # Mahashivratri
    date(2022, 3, 18),   # Holi
    date(2022, 4, 14),   # Dr Ambedkar Jayanti
    date(2022, 4, 15),   # Good Friday
    date(2022, 5,  3),   # Eid-ul-Fitr
    date(2022, 8,  9),   # Muharram
    date(2022, 8, 15),   # Independence Day
    date(2022, 8, 31),   # Ganesh Chaturthi
    date(2022, 10,  5),  # Dussehra
    date(2022, 10, 24),  # Diwali (Laxmi Puja)
    date(2022, 10, 26),  # Diwali Balipratipada
    date(2022, 11,  8),  # Gurunanak Jayanti
    # ── 2023 ──────────────────────────────────────────────────────────────────
    date(2023, 1, 26),   # Republic Day
    date(2023, 3,  7),   # Holi
    date(2023, 3, 30),   # Ram Navami
    date(2023, 4,  4),   # Mahavir Jayanti
    date(2023, 4,  7),   # Good Friday
    date(2023, 4, 14),   # Dr Ambedkar Jayanti
    date(2023, 5,  1),   # Maharashtra Day
    date(2023, 6, 28),   # Bakri Id
    date(2023, 8, 15),   # Independence Day
    date(2023, 9, 19),   # Ganesh Chaturthi
    date(2023, 10,  2),  # Gandhi Jayanti
    date(2023, 10, 24),  # Dussehra
    date(2023, 11, 13),  # Diwali (Laxmi Puja)
    date(2023, 11, 27),  # Gurunanak Jayanti
    date(2023, 12, 25),  # Christmas
    # ── 2024 ──────────────────────────────────────────────────────────────────
    date(2024, 1, 22),   # Ram Mandir consecration (special NSE holiday)
    date(2024, 1, 26),   # Republic Day
    date(2024, 3, 25),   # Holi
    date(2024, 3, 29),   # Good Friday
    date(2024, 4, 11),   # Eid-ul-Fitr
    date(2024, 4, 14),   # Dr Ambedkar Jayanti (Sunday — already excluded, but safe to include)
    date(2024, 5, 23),   # Buddha Purnima
    date(2024, 6, 17),   # Bakri Id
    date(2024, 7, 17),   # Muharram
    date(2024, 8, 15),   # Independence Day
    date(2024, 10,  2),  # Gandhi Jayanti
    date(2024, 11,  1),  # Diwali (Laxmi Puja)
    date(2024, 11, 15),  # Gurunanak Jayanti
    date(2024, 12, 25),  # Christmas
    # ── 2025 ──────────────────────────────────────────────────────────────────
    # Feb 1 2025 (Budget Day Saturday) is in NSE_SPECIAL_SESSIONS, NOT here.
    date(2025, 2, 26),   # Mahashivratri
    date(2025, 3, 14),   # Holi
    date(2025, 3, 31),   # Eid-ul-Fitr (approx)
    date(2025, 4, 10),   # Ram Navami (approx)
    date(2025, 4, 14),   # Dr Ambedkar Jayanti / Mahavir Jayanti
    date(2025, 4, 18),   # Good Friday
    date(2025, 5,  1),   # Maharashtra Day
    date(2025, 8, 15),   # Independence Day
    date(2025, 8, 27),   # Ganesh Chaturthi
    date(2025, 10,  2),  # Gandhi Jayanti
    date(2025, 10, 20),  # Dussehra (approx)
    date(2025, 10, 21),  # Diwali (Laxmi Puja) (approx)
    date(2025, 11,  5),  # Gurunanak Jayanti (approx)
    date(2025, 12, 25),  # Christmas
    # ── 2026 ──────────────────────────────────────────────────────────────────
    # NSE/FAOP/71777, dated 2025-12-12. Weekend holidays are handled by the
    # weekday check and are not listed here unless a special session exists.
    date(2026, 1, 26),   # Republic Day
    date(2026, 3,  3),   # Holi
    date(2026, 3, 26),   # Shri Ram Navami
    date(2026, 3, 31),   # Shri Mahavir Jayanti
    date(2026, 4,  3),   # Good Friday
    date(2026, 4, 14),   # Dr Ambedkar Jayanti
    date(2026, 5,  1),   # Maharashtra Day
    date(2026, 5, 28),   # Bakri Id
    date(2026, 6, 26),   # Muharram
    date(2026, 9, 14),   # Ganesh Chaturthi
    date(2026, 10,  2),  # Gandhi Jayanti
    date(2026, 10, 20),  # Dussehra
    date(2026, 11, 10),  # Diwali-Balipratipada
    date(2026, 11, 24),  # Guru Nanak Jayanti
    date(2026, 12, 25),  # Christmas
})


def is_trading_day(d: date) -> bool:
    """Return True if NSE F&O traded on *d* (9:15 AM – 3:30 PM session)."""
    if d in NSE_SPECIAL_SESSIONS:
        return True
    if d.weekday() >= 5:   # Saturday=5, Sunday=6
        return False
    return d not in NSE_HOLIDAYS


def next_trading_day(d: date) -> date:
    """Return the first NSE trading day strictly after *d*."""
    candidate = d + timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


# ──────────────────────────────────────────────────────────────────────────────
# Core calendar helpers
# ──────────────────────────────────────────────────────────────────────────────

def parse_expiry_folder(name: str) -> date:
    return datetime.strptime(name, "%Y%m%d").date()


def combine_date_time(day: date, clock: time) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(day, clock))


def dte(trade_date: date, expiry: date) -> int:
    return (expiry - trade_date).days


def _next_weekday(day: date, weekday: int) -> date:
    return day + timedelta(days=(weekday - day.weekday()) % 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year, month, monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _adjust_to_trading_day(candidate: date, trading_dates: set[date] | None) -> date:
    if trading_dates is None:
        while not is_trading_day(candidate):
            candidate -= timedelta(days=1)
        return candidate
    if candidate in trading_dates:
        return candidate
    if trading_dates and candidate > max(trading_dates):
        while not is_trading_day(candidate):
            candidate -= timedelta(days=1)
        return candidate
    previous = [day for day in trading_dates if day < candidate]
    return max(previous) if previous else candidate


def _weekly_expiry_weekday(symbol: str, anchor: date) -> int:
    symbol = get_instrument_spec(symbol).symbol
    if symbol == "NIFTY":
        return _THURSDAY if anchor <= NIFTY_LAST_THURSDAY_EXPIRY else _TUESDAY
    if symbol == "BANKNIFTY":
        return _THURSDAY if anchor < date(2023, 9, 1) else _WEDNESDAY
    if symbol == "FINNIFTY":
        return _THURSDAY if anchor < date(2021, 10, 14) else _TUESDAY
    if symbol == "MIDCPNIFTY":
        return _WEDNESDAY if anchor < date(2023, 8, 21) else _MONDAY
    if symbol == "SENSEX":
        # BSE weekly options launched May 2023 on Friday;
        # shifted to Tuesday Jan 2025; shifted to Thursday Sep 2025
        if anchor <= date(2025, 1, 3):
            return _FRIDAY
        if anchor <= date(2025, 8, 31):
            return _TUESDAY
        return _THURSDAY
    raise ValueError(f"Unsupported index symbol: {symbol}")


def _monthly_expiry_weekday(symbol: str, year: int, month: int) -> int:
    symbol = get_instrument_spec(symbol).symbol
    if symbol == "NIFTY":
        return _THURSDAY if (year, month) <= (2025, 8) else _TUESDAY
    if symbol == "BANKNIFTY":
        if (year, month) < (2024, 3):
            return _THURSDAY
        if (year, month) < (2025, 1):
            return _WEDNESDAY
        return _THURSDAY if (year, month) <= (2025, 8) else _TUESDAY
    if symbol == "FINNIFTY":
        if (year, month) < (2021, 10):
            return _THURSDAY
        if (year, month) < (2025, 1):
            return _TUESDAY
        return _THURSDAY if (year, month) <= (2025, 8) else _TUESDAY
    if symbol == "MIDCPNIFTY":
        if (year, month) < (2023, 8):
            return _WEDNESDAY
        if (year, month) < (2025, 1):
            return _MONDAY
        return _THURSDAY if (year, month) <= (2025, 8) else _TUESDAY
    if symbol == "SENSEX":
        if (year, month) <= (2024, 12):
            return _FRIDAY
        if (year, month) <= (2025, 8):
            return _TUESDAY
        return _THURSDAY
    raise ValueError(f"Unsupported index symbol: {symbol}")


def weekly_expiry_on_or_after(
    symbol: str,
    trade_date: date,
    *,
    min_dte: int = 0,
    trading_dates: list[date] | set[date] | None = None,
) -> date:
    """Return the front weekly expiry for the requested index symbol."""
    if min_dte < 0:
        raise ValueError("min_dte must be >= 0")
    spec = get_instrument_spec(symbol)
    earliest = trade_date + timedelta(days=min_dte)
    if spec.weekly_discontinued_after is not None and earliest > spec.weekly_discontinued_after:
        return monthly_expiry_on_or_after(
            spec.symbol,
            trade_date,
            min_dte=min_dte,
            trading_dates=trading_dates,
        )
    trading_set = set(trading_dates) if trading_dates is not None else None
    anchor = trade_date
    while True:
        weekday = _weekly_expiry_weekday(spec.symbol, anchor)
        candidate = _next_weekday(anchor, weekday)
        if spec.symbol == "NIFTY" and candidate > NIFTY_LAST_THURSDAY_EXPIRY and weekday == _THURSDAY:
            candidate = NIFTY_FIRST_TUESDAY_EXPIRY
        if spec.weekly_discontinued_after is not None and candidate > spec.weekly_discontinued_after:
            return monthly_expiry_on_or_after(
                spec.symbol,
                trade_date,
                min_dte=min_dte,
                trading_dates=trading_dates,
            )
        unadjusted = candidate
        candidate = _adjust_to_trading_day(unadjusted, trading_set)
        if candidate >= earliest:
            return candidate
        anchor = unadjusted + timedelta(days=1)


def monthly_expiry_on_or_after(
    symbol: str,
    trade_date: date,
    *,
    min_dte: int = 0,
    trading_dates: list[date] | set[date] | None = None,
) -> date:
    """Return the front monthly expiry for the requested index symbol."""
    if min_dte < 0:
        raise ValueError("min_dte must be >= 0")
    spec = get_instrument_spec(symbol)
    trading_set = set(trading_dates) if trading_dates is not None else None
    earliest = trade_date + timedelta(days=min_dte)
    year, month = trade_date.year, trade_date.month
    while True:
        weekday = _monthly_expiry_weekday(spec.symbol, year, month)
        candidate = _adjust_to_trading_day(_last_weekday(year, month, weekday), trading_set)
        if candidate >= earliest:
            return candidate
        month += 1
        if month == 13:
            year += 1
            month = 1


def expiry_on_or_after(
    symbol: str,
    trade_date: date,
    *,
    expiry_type: str = "week",
    min_dte: int = 0,
    trading_dates: list[date] | set[date] | None = None,
) -> date:
    if expiry_type == "week":
        return weekly_expiry_on_or_after(symbol, trade_date, min_dte=min_dte, trading_dates=trading_dates)
    if expiry_type == "month":
        return monthly_expiry_on_or_after(symbol, trade_date, min_dte=min_dte, trading_dates=trading_dates)
    raise ValueError(f"Unsupported expiry_type: {expiry_type}")


def nifty_weekly_expiry_on_or_after(
    trade_date: date,
    *,
    min_dte: int = 0,
    trading_dates: list[date] | set[date] | None = None,
) -> date:
    return weekly_expiry_on_or_after("NIFTY", trade_date, min_dte=min_dte, trading_dates=trading_dates)


def nifty_monthly_expiry_on_or_after(
    trade_date: date,
    *,
    min_dte: int = 0,
    trading_dates: list[date] | set[date] | None = None,
) -> date:
    """Return the front NIFTY monthly expiry across the 2025 Tuesday transition."""
    return monthly_expiry_on_or_after("NIFTY", trade_date, min_dte=min_dte, trading_dates=trading_dates)


def nifty_expiry_on_or_after(
    trade_date: date,
    *,
    expiry_type: str = "week",
    min_dte: int = 0,
    trading_dates: list[date] | set[date] | None = None,
) -> date:
    return expiry_on_or_after("NIFTY", trade_date, expiry_type=expiry_type, min_dte=min_dte, trading_dates=trading_dates)


def nearest_timestamp(index: pd.DatetimeIndex, target: pd.Timestamp) -> pd.Timestamp | None:
    eligible = index[index >= target]
    if len(eligible) == 0:
        return None
    return eligible[0]

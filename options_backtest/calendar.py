from __future__ import annotations

from datetime import date, datetime, time

import pandas as pd


SESSION_START = time(9, 15)
SESSION_END = time(15, 30)


def parse_expiry_folder(name: str) -> date:
    return datetime.strptime(name, "%Y%m%d").date()


def combine_date_time(day: date, clock: time) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(day, clock))


def dte(trade_date: date, expiry: date) -> int:
    return (expiry - trade_date).days


def nearest_timestamp(index: pd.DatetimeIndex, target: pd.Timestamp) -> pd.Timestamp | None:
    eligible = index[index >= target]
    if len(eligible) == 0:
        return None
    return eligible[0]

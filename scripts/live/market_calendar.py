"""Official exchange-calendar cache for the live paper stack."""

from __future__ import annotations

import http.cookiejar
import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from options_backtest.calendar import NSE_SPECIAL_SESSIONS


NSE_HOLIDAY_MASTER_URL = "https://www.nseindia.com/api/holiday-master?type=trading"
NSE_HOME_URL = "https://www.nseindia.com/"
DEFAULT_EXCHANGE = "NSE"
DEFAULT_SEGMENT = "FO"
DEFAULT_MAX_CACHE_AGE = timedelta(hours=36)
_IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class MarketSessionDecision:
    session_date: str
    is_trading_day: bool
    exchange: str
    segment: str
    source: str
    reason: str
    description: str | None = None
    cache_path: str | None = None
    cache_generated_at: str | None = None
    error: str | None = None


def market_calendar_cache_path(live_root: Path) -> Path:
    return Path(live_root) / "calendar" / "market_calendar.json"


def _now_ist() -> datetime:
    return datetime.now(tz=_IST)


def _nse_request(url: str) -> urllib.request.Request:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": NSE_HOME_URL,
    }
    return urllib.request.Request(url, headers=headers)


def _read_json(opener: urllib.request.OpenerDirector, url: str, timeout: float) -> Any:
    with opener.open(_nse_request(url), timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_nse_holiday_master(timeout: float = 10.0) -> dict[str, Any]:
    """
    Fetch NSE's official holiday-master JSON.

    NSE occasionally requires a homepage cookie before API access, so the fetch
    retries once after visiting the home page.
    """
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    try:
        payload = _read_json(opener, NSE_HOLIDAY_MASTER_URL, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code not in {401, 403}:
            raise
        with opener.open(_nse_request(NSE_HOME_URL), timeout=timeout):
            pass
        payload = _read_json(opener, NSE_HOLIDAY_MASTER_URL, timeout)
    if not isinstance(payload, dict):
        raise ValueError("NSE holiday-master response is not an object")
    return payload


def _parse_nse_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%d-%b-%Y").date()


def normalise_nse_holiday_master(payload: dict[str, Any]) -> dict[str, Any]:
    segments: dict[str, list[dict[str, Any]]] = {}
    for segment, rows in payload.items():
        if not isinstance(rows, list):
            continue
        parsed: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("tradingDate"):
                continue
            trading_date = _parse_nse_date(str(row["tradingDate"]))
            parsed.append(
                {
                    "date": trading_date.isoformat(),
                    "week_day": row.get("weekDay"),
                    "description": row.get("description"),
                    "morning_session": row.get("morning_session"),
                    "evening_session": row.get("evening_session"),
                    "sr_no": row.get("Sr_no"),
                }
            )
        if parsed:
            segments[str(segment).upper()] = sorted(parsed, key=lambda rec: rec["date"])

    if DEFAULT_SEGMENT not in segments:
        raise ValueError("NSE holiday-master response did not include FO segment")

    return {
        "version": 1,
        "generated_at": _now_ist().isoformat(),
        "sources": {
            "nse_holiday_master": NSE_HOLIDAY_MASTER_URL,
        },
        "exchanges": {
            "NSE": {
                "segments": segments,
            },
        },
    }


def write_market_calendar_cache(live_root: Path, calendar: dict[str, Any]) -> Path:
    path = market_calendar_cache_path(live_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(calendar, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp_path, path)
    return path


def refresh_market_calendar(live_root: Path, timeout: float = 10.0) -> Path:
    payload = fetch_nse_holiday_master(timeout=timeout)
    calendar = normalise_nse_holiday_master(payload)
    return write_market_calendar_cache(live_root, calendar)


def load_market_calendar_cache(live_root: Path) -> dict[str, Any] | None:
    path = market_calendar_cache_path(live_root)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _cache_generated_at(calendar: dict[str, Any]) -> datetime | None:
    value = calendar.get("generated_at")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_IST)
    return parsed.astimezone(_IST)


def _cache_is_fresh(calendar: dict[str, Any], max_age: timedelta) -> bool:
    generated_at = _cache_generated_at(calendar)
    if generated_at is None:
        return False
    return (_now_ist() - generated_at) <= max_age


def _segment_rows(calendar: dict[str, Any], exchange: str, segment: str) -> list[dict[str, Any]]:
    return (
        calendar.get("exchanges", {})
        .get(exchange.upper(), {})
        .get("segments", {})
        .get(segment.upper(), [])
    )


def _holiday_row(calendar: dict[str, Any], day: date, exchange: str, segment: str) -> dict[str, Any] | None:
    target = day.isoformat()
    for row in _segment_rows(calendar, exchange, segment):
        if row.get("date") == target:
            return row
    return None


def _cache_covers_year(calendar: dict[str, Any], day: date, exchange: str, segment: str) -> bool:
    for row in _segment_rows(calendar, exchange, segment):
        value = row.get("date")
        if isinstance(value, str) and value.startswith(f"{day.year:04d}-"):
            return True
    return False


def _weekday_session(day: date) -> bool:
    if day in NSE_SPECIAL_SESSIONS:
        return True
    return day.weekday() < 5


def market_session_decision(
    live_root: Path,
    day: date,
    *,
    exchange: str = DEFAULT_EXCHANGE,
    segment: str = DEFAULT_SEGMENT,
    refresh: bool = False,
    fail_closed: bool = False,
    max_cache_age: timedelta = DEFAULT_MAX_CACHE_AGE,
    timeout: float = 10.0,
) -> MarketSessionDecision:
    """
    Decide whether a live paper session should run for an exchange segment.

    If refresh is requested and the official source cannot be reached, a fresh
    cache is still accepted. Without a fresh cache, fail_closed=True returns a
    closed decision so the live stack does not trade on an unknown calendar.
    """
    cache_path = market_calendar_cache_path(live_root)
    refresh_error: str | None = None
    if refresh:
        try:
            refresh_market_calendar(live_root, timeout=timeout)
        except Exception as exc:
            refresh_error = repr(exc)

    calendar = load_market_calendar_cache(live_root)
    if calendar is not None:
        holiday = _holiday_row(calendar, day, exchange, segment)
        generated_at = calendar.get("generated_at")
        if holiday is not None:
            return MarketSessionDecision(
                session_date=day.isoformat(),
                is_trading_day=False,
                exchange=exchange.upper(),
                segment=segment.upper(),
                source="official_cache",
                reason="exchange_holiday",
                description=str(holiday.get("description") or ""),
                cache_path=str(cache_path),
                cache_generated_at=str(generated_at) if generated_at else None,
                error=refresh_error,
            )
        if _cache_covers_year(calendar, day, exchange, segment) and _weekday_session(day):
            if _cache_is_fresh(calendar, max_cache_age) or refresh_error is None:
                return MarketSessionDecision(
                    session_date=day.isoformat(),
                    is_trading_day=True,
                    exchange=exchange.upper(),
                    segment=segment.upper(),
                    source="official_cache",
                    reason="regular_session",
                    cache_path=str(cache_path),
                    cache_generated_at=str(generated_at) if generated_at else None,
                    error=refresh_error,
                )
        if not _weekday_session(day):
            return MarketSessionDecision(
                session_date=day.isoformat(),
                is_trading_day=False,
                exchange=exchange.upper(),
                segment=segment.upper(),
                source="weekend",
                reason="weekend_or_no_special_session",
                cache_path=str(cache_path),
                cache_generated_at=str(generated_at) if generated_at else None,
                error=refresh_error,
            )

    if not _weekday_session(day):
        return MarketSessionDecision(
            session_date=day.isoformat(),
            is_trading_day=False,
            exchange=exchange.upper(),
            segment=segment.upper(),
            source="weekend",
            reason="weekend_or_no_special_session",
            cache_path=str(cache_path),
            error=refresh_error,
        )

    if refresh and refresh_error and fail_closed:
        return MarketSessionDecision(
            session_date=day.isoformat(),
            is_trading_day=False,
            exchange=exchange.upper(),
            segment=segment.upper(),
            source="fail_closed",
            reason="calendar_unavailable",
            cache_path=str(cache_path),
            error=refresh_error,
        )

    return MarketSessionDecision(
        session_date=day.isoformat(),
        is_trading_day=True,
        exchange=exchange.upper(),
        segment=segment.upper(),
        source="weekday_fallback",
        reason="weekday_no_official_cache",
        cache_path=str(cache_path),
        error=refresh_error,
    )


def decision_payload(decision: MarketSessionDecision) -> dict[str, Any]:
    return asdict(decision)

from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from options_backtest.dhan_loader import DhanBacktestEngine, load_dhan_data
from options_backtest.reports import daily_pnl, equity_curve, summary, trade_ledger_with_total
from options_backtest.schemas import BacktestConfig
from options_backtest.strategy import CreditSpread, IronCondor, OptionType
from options_backtest.volatility_filter import VixFilter


INDICES: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")
TRAIN_END = pd.Timestamp("2024-12-31").date()
OOS_START = pd.Timestamp("2025-01-01").date()


@dataclass(frozen=True)
class PortfolioSpec:
    name: str
    label: str
    lots: dict[str, float]
    source: str
    bucket_policy: str = "all"


def _expiry_map(default_expiry: str) -> dict[str, str]:
    """NIFTY keeps weekly throughout; non-NIFTY use the specified expiry type.
    This removes the IS/OOS regime confound caused by weekly discontinuation in Nov 2024."""
    if default_expiry == "month":
        return {"NIFTY": "week", "BANKNIFTY": "month", "FINNIFTY": "month", "MIDCPNIFTY": "month"}
    return {s: default_expiry for s in INDICES}


def _out_dir() -> Path:
    return Path("reports/backtests/options/risk_management")


def _focused_dir() -> Path:
    return Path("reports/backtests/options/focused")


def _fmt_money(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    sign = "+" if value >= 0 else "-"
    return f"{sign}INR {abs(value):,.0f}"


def _fmt_pct(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"{value * 100:+.2f}%"


def _fmt_num(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"{value:.3f}"


def _read_ledger(path: Path, symbol: str | None = None) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "strategy" in df.columns:
        df = df[df["strategy"] != "TOTAL"].copy()
    if df.empty:
        return df
    if symbol is not None and "symbol" not in df.columns:
        df.insert(0, "symbol", symbol)
    for col in ("gross_pnl", "charges", "net_pnl", "lot_size", "dte_at_entry"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("entry_time", "exit_time", "vix_timestamp"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in ("entry_date", "exit_date", "expiry"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    return df.reset_index(drop=True)


def _write_ledger_with_total_preserve(ledger: pd.DataFrame, path: Path) -> None:
    columns = list(ledger.columns)
    out = ledger.copy()
    out["trade_count"] = 1
    total = {col: "" for col in columns + ["trade_count"]}
    total.update({"strategy": "TOTAL", "trade_count": int(len(out))})
    for col in ("gross_pnl", "charges", "net_pnl", "entry_credit", "max_theoretical_loss"):
        if col in out.columns:
            total[col] = round(float(pd.to_numeric(out[col], errors="coerce").sum()), 2)
    pd.concat([out[columns + ["trade_count"]], pd.DataFrame([total])], ignore_index=True).to_csv(path, index=False)


def _annotate_vix(df: pd.DataFrame, vix_filter: VixFilter) -> pd.DataFrame:
    if df.empty or "entry_time" not in df.columns:
        return df
    out = df.copy()
    needs_vix = "vix_bucket" not in out.columns or out["vix_bucket"].isna().all() or (out["vix_bucket"] == "").all()
    if not needs_vix:
        return out

    values: list[float | None] = []
    buckets: list[str | None] = []
    timestamps: list[pd.Timestamp | None] = []
    for ts in pd.to_datetime(out["entry_time"]):
        obs = vix_filter.observation_at_or_before(pd.Timestamp(ts))
        if obs is None:
            values.append(None)
            buckets.append(None)
            timestamps.append(None)
        else:
            values.append(round(obs.value, 4))
            buckets.append(obs.bucket)
            timestamps.append(obs.timestamp)
    out["vix_entry"] = values
    out["vix_bucket"] = buckets
    out["vix_timestamp"] = timestamps
    return out


def _apply_bucket_policy(df: pd.DataFrame, policy: str) -> pd.DataFrame:
    if policy == "all" or df.empty:
        return df.copy()
    out = df.copy()
    if policy == "skip_10_13":
        return out[out["vix_bucket"] != "10-13"].copy()
    if policy == "half_10_13":
        scale = np.where(out["vix_bucket"].eq("10-13"), 0.5, 1.0)
        for col in ("gross_pnl", "charges", "net_pnl"):
            out[col] = out[col].astype(float) * scale
        return out
    raise ValueError(f"Unknown bucket policy: {policy}")


def _scale_lots(df: pd.DataFrame, lots: float) -> pd.DataFrame:
    out = df.copy()
    if lots == 1:
        out["portfolio_lots"] = 1.0
        return out
    for col in ("gross_pnl", "charges", "net_pnl", "entry_credit", "max_theoretical_loss"):
        if col in out.columns:
            out[col] = out[col].astype(float) * lots
    out["portfolio_lots"] = float(lots)
    return out


def _combine_portfolio(
    ledgers_by_symbol: dict[str, pd.DataFrame],
    spec: PortfolioSpec,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for symbol, lots in spec.lots.items():
        if lots <= 0:
            continue
        base = ledgers_by_symbol.get(symbol)
        if base is None or base.empty:
            continue
        filtered = _apply_bucket_policy(base, spec.bucket_policy)
        if filtered.empty:
            continue
        scaled = _scale_lots(filtered, lots)
        scaled["portfolio_name"] = spec.name
        frames.append(scaled)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    return combined.sort_values(["exit_time", "symbol"]).reset_index(drop=True)


def _stats_for_ledger(ledger: pd.DataFrame) -> dict:
    if ledger.empty:
        return summary(pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    daily = daily_pnl(ledger)
    curve = equity_curve(daily)
    stats = summary(ledger, curve, daily)
    stats.update(_tail_stats(daily))
    return stats


def _tail_stats(daily: pd.DataFrame) -> dict:
    if daily.empty:
        return {"daily_var_95": 0.0, "daily_cvar_95": 0.0, "max_daily_loss": 0.0}
    pnl = pd.to_numeric(daily["net_pnl"], errors="coerce").dropna()
    if pnl.empty:
        return {"daily_var_95": 0.0, "daily_cvar_95": 0.0, "max_daily_loss": 0.0}
    cutoff = float(pnl.quantile(0.05))
    tail = pnl[pnl <= cutoff]
    return {
        "daily_var_95": round(cutoff, 2),
        "daily_cvar_95": round(float(tail.mean()), 2) if not tail.empty else cutoff,
        "max_daily_loss": round(float(pnl.min()), 2),
    }


_LEG_RE = re.compile(r"(BUY|SELL):([A-Z]+)_DHAN_(\d+)(CE|PE)@([0-9.]+)")


def _parse_legs(raw: str) -> list[dict]:
    legs: list[dict] = []
    if not isinstance(raw, str):
        return legs
    for match in _LEG_RE.finditer(raw):
        legs.append({
            "side": match.group(1),
            "symbol": match.group(2),
            "strike": int(match.group(3)),
            "otype": match.group(4),
            "price": float(match.group(5)),
        })
    return legs


def _add_condor_risk_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    credits: list[float | None] = []
    max_losses: list[float | None] = []
    for row in out.itertuples(index=False):
        legs = _parse_legs(getattr(row, "entry_legs", ""))
        lot_size = float(getattr(row, "lot_size", 0) or 0)
        if len(legs) != 4 or lot_size <= 0:
            credits.append(None)
            max_losses.append(None)
            continue
        credit_unit = sum(leg["price"] if leg["side"] == "SELL" else -leg["price"] for leg in legs)
        calls = sorted([leg for leg in legs if leg["otype"] == "CE"], key=lambda x: x["strike"])
        puts = sorted([leg for leg in legs if leg["otype"] == "PE"], key=lambda x: x["strike"])
        if len(calls) != 2 or len(puts) != 2:
            credits.append(None)
            max_losses.append(None)
            continue
        call_width = abs(calls[1]["strike"] - calls[0]["strike"])
        put_width = abs(puts[1]["strike"] - puts[0]["strike"])
        width = max(call_width, put_width)
        max_loss = max(0.0, (width - credit_unit) * lot_size)
        credits.append(round(credit_unit * lot_size, 2))
        max_losses.append(round(max_loss, 2))
    out["entry_credit"] = credits
    out["max_theoretical_loss"] = max_losses
    return out


def _open_risk_stats(ledger: pd.DataFrame) -> dict:
    if ledger.empty or "max_theoretical_loss" not in ledger.columns:
        return {"avg_trade_max_loss": None, "worst_day_open_risk": None}
    risk = pd.to_numeric(ledger["max_theoretical_loss"], errors="coerce")
    if risk.dropna().empty:
        return {"avg_trade_max_loss": None, "worst_day_open_risk": None}
    by_day = ledger.assign(_risk=risk).groupby("entry_date")["_risk"].sum()
    return {
        "avg_trade_max_loss": round(float(risk.mean()), 2),
        "worst_day_open_risk": round(float(by_day.max()), 2) if not by_day.empty else None,
    }


def _run_condor_variant(
    args: argparse.Namespace,
    run_stamp: str,
    wing_gap: int,
    expiry_type_map: dict[str, str],
) -> dict[str, pd.DataFrame]:
    ledgers: dict[str, pd.DataFrame] = {}
    long_offset = 2 + wing_gap
    for symbol in INDICES:
        expiry_type = expiry_type_map.get(symbol, args.expiry_type)
        config = BacktestConfig(
            symbol=symbol,
            stop_loss_pct=None,
            target_profit_pct=None,
            min_dte=1,
            vix_path=args.vix_path,
            vix_missing_policy=args.vix_missing_policy,
        )
        data = load_dhan_data(args.dhan_root, expiry_type, symbol)
        engine = DhanBacktestEngine(config, dhan_root=args.dhan_root, expiry_type=expiry_type)
        result = engine.run(
            IronCondor(
                short_call_offset=2,
                long_call_offset=long_offset,
                short_put_offset=-2,
                long_put_offset=-long_offset,
            ),
            from_date=args.from_date,
            to_date=args.to_date,
            data=data,
        )
        ledger = result.trade_ledger.copy()
        ledger.insert(0, "symbol", symbol)
        ledger = _add_condor_risk_columns(ledger)
        out = _out_dir() / f"{run_stamp}_ic_wing{wing_gap}_{symbol.lower()}.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        _write_ledger_with_total_preserve(ledger, out)
        ledgers[symbol] = ledger
        stats = result.summary
        print(
            f"  IC wing{wing_gap} {symbol:12s} ({expiry_type:5s}) trades={stats.get('trades', 0):>4} "
            f"net={stats.get('net_pnl', 0):>11,.0f} sharpe={_fmt_num(stats.get('sharpe'))}"
        )
    return ledgers


def _add_spread_risk_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Compute entry_credit and max_theoretical_loss for 2-leg credit spreads."""
    if df.empty:
        return df
    out = df.copy()
    credits: list[float | None] = []
    max_losses: list[float | None] = []
    for row in out.itertuples(index=False):
        legs = _parse_legs(getattr(row, "entry_legs", ""))
        lot_size = float(getattr(row, "lot_size", 0) or 0)
        if len(legs) != 2 or lot_size <= 0:
            credits.append(None)
            max_losses.append(None)
            continue
        sell_legs = [lg for lg in legs if lg["side"] == "SELL"]
        buy_legs = [lg for lg in legs if lg["side"] == "BUY"]
        if len(sell_legs) != 1 or len(buy_legs) != 1:
            credits.append(None)
            max_losses.append(None)
            continue
        credit_unit = sell_legs[0]["price"] - buy_legs[0]["price"]
        width = abs(sell_legs[0]["strike"] - buy_legs[0]["strike"])
        max_loss = max(0.0, (width - credit_unit) * lot_size)
        credits.append(round(credit_unit * lot_size, 2))
        max_losses.append(round(max_loss, 2))
    out["entry_credit"] = credits
    out["max_theoretical_loss"] = max_losses
    return out


def _run_spread_variant(
    args: argparse.Namespace,
    run_stamp: str,
    side: str,
    short_offset: int,
    long_offset: int,
    expiry_type_map: dict[str, str],
) -> dict[str, pd.DataFrame]:
    """Run a credit spread backtest for all indices. side='put' or 'call'."""
    tag = f"cs_{side}_s{abs(short_offset)}_l{abs(long_offset)}"
    ot = OptionType.PUT if side == "put" else OptionType.CALL
    ledgers: dict[str, pd.DataFrame] = {}
    for symbol in INDICES:
        expiry_type = expiry_type_map.get(symbol, args.expiry_type)
        config = BacktestConfig(
            symbol=symbol,
            stop_loss_pct=None,
            target_profit_pct=None,
            min_dte=1,
            vix_path=args.vix_path,
            vix_missing_policy=args.vix_missing_policy,
        )
        data = load_dhan_data(args.dhan_root, expiry_type, symbol)
        engine = DhanBacktestEngine(config, dhan_root=args.dhan_root, expiry_type=expiry_type)
        result = engine.run(
            CreditSpread(option_type=ot, short_offset=short_offset, long_offset=long_offset),
            from_date=args.from_date,
            to_date=args.to_date,
            data=data,
        )
        ledger = result.trade_ledger.copy()
        ledger.insert(0, "symbol", symbol)
        ledger = _add_spread_risk_columns(ledger)
        out = _out_dir() / f"{run_stamp}_{tag}_{symbol.lower()}.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        _write_ledger_with_total_preserve(ledger, out)
        ledgers[symbol] = ledger
        stats = result.summary
        print(
            f"  {tag} {symbol:12s} ({expiry_type:5s}) trades={stats.get('trades', 0):>4} "
            f"net={stats.get('net_pnl', 0):>11,.0f} sharpe={_fmt_num(stats.get('sharpe'))}"
        )
    return ledgers


def _load_strangle_sources(args: argparse.Namespace, vix_filter: VixFilter) -> dict[str, pd.DataFrame]:
    ledgers: dict[str, pd.DataFrame] = {}
    for symbol in INDICES:
        path = _focused_dir() / f"{args.source_run_stamp}_dhan_{symbol.lower()}_x1_short_strangle.csv"
        if not path.exists():
            fallback = _focused_dir() / f"{args.source_run_stamp}_dhan_{symbol.lower()}_short_strangle.csv"
            path = fallback
        if not path.exists():
            raise FileNotFoundError(f"Missing source ledger for {symbol}: {path}")
        ledgers[symbol] = _annotate_vix(_read_ledger(path, symbol), vix_filter)
    return ledgers


def _load_exact_lot_sources(args: argparse.Namespace, vix_filter: VixFilter) -> dict[tuple[str, int], pd.DataFrame]:
    ledgers: dict[tuple[str, int], pd.DataFrame] = {}
    for path in _focused_dir().glob(f"{args.source_run_stamp}_dhan_*_x*_short_strangle.csv"):
        name = path.name
        match = re.match(rf"{re.escape(args.source_run_stamp)}_dhan_(.+)_x(\d+)_short_strangle\.csv", name)
        if not match:
            continue
        symbol = match.group(1).upper()
        lots = int(match.group(2))
        ledgers[(symbol, lots)] = _annotate_vix(_read_ledger(path, symbol), vix_filter)
    return ledgers


def _combine_portfolio_exact(
    base_ledgers: dict[str, pd.DataFrame],
    exact_ledgers: dict[tuple[str, int], pd.DataFrame],
    spec: PortfolioSpec,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for symbol, lots in spec.lots.items():
        if lots <= 0:
            continue
        exact_key = (symbol, int(lots))
        use_exact = float(lots).is_integer() and exact_key in exact_ledgers
        if use_exact:
            selected = exact_ledgers[exact_key].copy()
            selected["portfolio_lots"] = float(lots)
        else:
            base = base_ledgers.get(symbol)
            if base is None or base.empty:
                continue
            selected = _scale_lots(base, lots)
        selected = _apply_bucket_policy(selected, spec.bucket_policy)
        if selected.empty:
            continue
        selected["portfolio_name"] = spec.name
        frames.append(selected)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    return combined.sort_values(["exit_time", "symbol"]).reset_index(drop=True)


def _load_condor_sources(
    ic_source_stamp: str,
    vix_filter: VixFilter,
    wing_gaps: tuple[int, ...] = (2, 4, 6, 8),
) -> dict[int, dict[str, pd.DataFrame]]:
    loaded: dict[int, dict[str, pd.DataFrame]] = {}
    for wing_gap in wing_gaps:
        ledgers: dict[str, pd.DataFrame] = {}
        for symbol in INDICES:
            path = _out_dir() / f"{ic_source_stamp}_ic_wing{wing_gap}_{symbol.lower()}.csv"
            if not path.exists():
                print(f"  Warning: IC ledger not found: {path}")
                continue
            df = _read_ledger(path, symbol)
            if df.empty:
                continue
            ledgers[symbol] = _annotate_vix(df, vix_filter)
        if ledgers:
            loaded[wing_gap] = ledgers
            print(f"  Loaded IC wing{wing_gap}: {list(ledgers.keys())}")
    return loaded


def _daily_matrix(ledgers: dict[str, pd.DataFrame], bucket_policy: str = "all") -> pd.DataFrame:
    parts: list[pd.Series] = []
    for symbol, ledger in ledgers.items():
        filtered = _apply_bucket_policy(ledger, bucket_policy)
        dpnl = daily_pnl(filtered)
        if dpnl.empty:
            continue
        s = pd.Series(dpnl["net_pnl"].to_numpy(dtype=float), index=pd.to_datetime(dpnl["date"]), name=symbol)
        parts.append(s)
    if not parts:
        return pd.DataFrame()
    matrix = pd.concat(parts, axis=1, sort=True).fillna(0.0).sort_index()
    start, end = matrix.index.min(), matrix.index.max()
    return matrix.reindex(pd.bdate_range(start, end), fill_value=0.0)


def _risk_parity_lots(matrix: pd.DataFrame, total_lots: int = 8) -> dict[str, int]:
    train = matrix[matrix.index.date <= TRAIN_END]
    vol = train.std(ddof=1).replace(0, np.nan)
    inv = (1.0 / vol).replace([np.inf, -np.inf], np.nan).dropna()
    if inv.empty:
        return {symbol: 1 for symbol in INDICES}
    raw = inv / inv.sum() * total_lots
    lots = {symbol: max(0, int(round(raw.get(symbol, 0)))) for symbol in INDICES}
    while sum(lots.values()) < total_lots:
        symbol = max(INDICES, key=lambda s: raw.get(s, 0) - lots.get(s, 0))
        lots[symbol] = lots.get(symbol, 0) + 1
    while sum(lots.values()) > total_lots:
        symbol = max((s for s in INDICES if lots.get(s, 0) > 0), key=lambda s: lots.get(s, 0) - raw.get(s, 0))
        lots[symbol] -= 1
    return lots


def _kelly_lots(matrix: pd.DataFrame, fraction: float = 0.25, cap: int = 4) -> tuple[dict[str, float], dict[str, int]]:
    train = matrix[matrix.index.date <= TRAIN_END]
    if train.empty or len(train) < 30:
        return ({symbol: 0.0 for symbol in INDICES}, {symbol: 0 for symbol in INDICES})
    returns = train / 1_000_000.0
    mu = returns.mean().reindex(INDICES).fillna(0.0).to_numpy()
    cov = returns.reindex(columns=INDICES).cov().fillna(0.0).to_numpy()
    raw = np.linalg.pinv(cov) @ mu
    raw = np.where(np.isfinite(raw), raw, 0.0)
    raw = np.maximum(raw, 0.0)
    fractional = raw * fraction
    rounded = {
        symbol: min(cap, max(0, int(math.floor(lot + 0.5))))
        for symbol, lot in zip(INDICES, fractional)
    }
    return ({symbol: float(lot) for symbol, lot in zip(INDICES, raw)}, rounded)


def _split_stats(ledger: pd.DataFrame) -> tuple[dict, dict]:
    if ledger.empty:
        empty = _stats_for_ledger(ledger)
        return empty, empty
    dates = pd.to_datetime(ledger["exit_date"]).dt.date
    train = ledger[dates <= TRAIN_END].copy()
    oos = ledger[dates >= OOS_START].copy()
    return _stats_for_ledger(train), _stats_for_ledger(oos)


def _portfolio_rows(portfolios: list[tuple[PortfolioSpec, pd.DataFrame]]) -> pd.DataFrame:
    rows: list[dict] = []
    for spec, ledger in portfolios:
        stats = _stats_for_ledger(ledger)
        train_stats, oos_stats = _split_stats(ledger)
        row = {
            "name": spec.name,
            "label": spec.label,
            "source": spec.source,
            "bucket_policy": spec.bucket_policy,
            "lots": " ".join(f"{k}:{v:g}" for k, v in spec.lots.items() if v > 0),
            "trades": stats.get("trades", 0),
            "net_pnl": stats.get("net_pnl"),
            "cagr": stats.get("cagr"),
            "sharpe": stats.get("sharpe"),
            "sortino": stats.get("sortino"),
            "max_drawdown_pct": stats.get("max_drawdown_pct"),
            "max_drawdown": stats.get("max_drawdown"),
            "profit_factor": stats.get("profit_factor"),
            "daily_cvar_95": stats.get("daily_cvar_95"),
            "max_daily_loss": stats.get("max_daily_loss"),
            "train_net_pnl": train_stats.get("net_pnl"),
            "train_pf": train_stats.get("profit_factor"),
            "oos_net_pnl": oos_stats.get("net_pnl"),
            "oos_pf": oos_stats.get("profit_factor"),
        }
        row.update(_open_risk_stats(ledger))
        rows.append(row)
    return pd.DataFrame(rows)


def _bucket_attribution(ledger: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for bucket, grp in ledger.groupby("vix_bucket", dropna=False):
        stats = _stats_for_ledger(grp.copy())
        rows.append({
            "vix_bucket": bucket or "missing",
            "trades": stats.get("trades", 0),
            "net_pnl": stats.get("net_pnl"),
            "avg_trade_pnl": stats.get("avg_trade_pnl"),
            "profit_factor": stats.get("profit_factor"),
            "max_drawdown_pct": stats.get("max_drawdown_pct"),
        })
    order = {"<10": 0, "10-13": 1, "13-17": 2, "17-22": 3, "22-30": 4, ">30": 5, "missing": 9}
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["_order"] = out["vix_bucket"].map(order).fillna(8)
    return out.sort_values("_order").drop(columns=["_order"]).reset_index(drop=True)


def _write_report(
    *,
    run_stamp: str,
    args: argparse.Namespace,
    expiry_map: dict[str, str],
    portfolio_table: pd.DataFrame,
    bucket_table: pd.DataFrame,
    kelly_raw: dict[str, float],
    kelly_lots: dict[str, int],
    risk_parity_lots: dict[str, int],
) -> Path:
    path = _out_dir() / f"{run_stamp}_risk_management_experiments.md"
    lines: list[str] = [
        f"# Risk Management Experiments - {run_stamp}",
        "",
        f"Window: `{args.from_date}` to `{args.to_date}`",
        "IC/spread expiry: " + " ".join(f"{s}={'W' if v == 'week' else 'M'}" for s, v in expiry_map.items()) + " (W=weekly M=monthly) | Naked source: `" + args.source_run_stamp + "`",
        "",
        "Purpose: reduce naked short-strangle tail risk and concentration after the lot-sizing study.",
        "",
        "## Research Priors",
        "",
        "- Kelly sizing maximizes long-run log growth, but full Kelly can create severe short-term drawdowns; fractional Kelly is the practical version for noisy estimates.",
        "- For option books, defined-risk spreads are a cleaner input to Kelly/CVaR sizing because the loss distribution is bounded by construction.",
        "- CVaR/expected shortfall is a better optimization target than VaR for short-vol strategies because it measures average tail loss beyond the quantile.",
        "- The indexed paper `2508.16598` is directly aligned with this project: combine Kelly sizing with VIX regime scaling for short-dated option selling rather than fixed lots.",
        "",
        "Sources: MacLean, Thorp, Ziemba on Kelly/fractional Kelly (SSRN https://ssrn.com/abstract=1797366); "
        "Nekrasov multivariate fractional Kelly (SSRN https://ssrn.com/abstract=2259133); "
        "Rockafellar/Uryasev CVaR optimization (Journal of Risk https://www.risk.net/journal-of-risk/technical-paper/2161159/optimization-conditional-value-risk); "
        "Wysocki `2508.16598` hybrid Kelly x VIX put-writing summary (RePEc https://ideas.repec.org/p/arx/papers/2508.16598.html).",
        "",
        "## VIX Bucket Attribution",
        "",
        "Naked short-strangle x1 contributors, annotated with refreshed Dhan India VIX as-of entry timestamp.",
        "",
        "| VIX bucket | Trades | Net PnL | Avg/trade | PF | Max DD% |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in bucket_table.itertuples(index=False):
        lines.append(
            f"| {row.vix_bucket} | {row.trades:,} | {_fmt_money(row.net_pnl)}"
            f" | {_fmt_money(row.avg_trade_pnl)} | {_fmt_num(row.profit_factor)}"
            f" | {_fmt_pct(row.max_drawdown_pct)} |"
        )

    lines.extend([
        "",
        "## Portfolio Comparison",
        "",
        "| Portfolio | Lots | Source | Trades | Net PnL | CAGR | Sharpe | Max DD% | PF | CVaR95/day | OOS Net | OOS PF | Worst open risk |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for row in portfolio_table.itertuples(index=False):
        worst_risk = getattr(row, "worst_day_open_risk")
        risk_str = "unlimited" if pd.isna(worst_risk) else _fmt_money(worst_risk)
        lines.append(
            f"| {row.label} | `{row.lots}` | {row.source}"
            f" | {row.trades:,} | {_fmt_money(row.net_pnl)} | {_fmt_pct(row.cagr)}"
            f" | {_fmt_num(row.sharpe)} | {_fmt_pct(row.max_drawdown_pct)}"
            f" | {_fmt_num(row.profit_factor)} | {_fmt_money(row.daily_cvar_95)}"
            f" | {_fmt_money(row.oos_net_pnl)} | {_fmt_num(row.oos_pf)} | {risk_str} |"
        )

    lines.extend([
        "",
        "## Sizing Diagnostics",
        "",
        "| Symbol | Raw full-Kelly lots | Quarter-Kelly capped lots | Risk-parity lots |",
        "|---|---:|---:|---:|",
    ])
    for symbol in INDICES:
        lines.append(
            f"| {symbol} | {kelly_raw.get(symbol, 0.0):.2f}"
            f" | {kelly_lots.get(symbol, 0)} | {risk_parity_lots.get(symbol, 0)} |"
        )

    lines.extend([
        "",
        "## Read",
        "",
        "- The profitable naked book is not a deployable risk shape. Historical max DD is an observation, not a bound.",
        "- The first VIX control to keep is targeted `10-13` skip/downsize. Broad `13-22` gating is too blunt for the current contributor pack.",
        "- Treat full Kelly as a diagnostic only. Use quarter Kelly or smaller, cap by defined-risk max loss, and recompute on rolling train windows.",
        "- Prefer portfolio risk budget by worst-case spread loss/CVaR, not by contract count. A seven-lot MIDCPNIFTY concentration is exactly what this layer should prevent.",
        "",
        "## Files",
        "",
        f"- Portfolio table: `{run_stamp}_portfolio_comparison.csv`",
        f"- VIX bucket attribution: `{run_stamp}_vix_bucket_attribution.csv`",
        f"- Report: `{path.name}`",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Risk management experiments for focused short-premium book.")
    parser.add_argument("--dhan-root", default="data/processed/options/dhan")
    parser.add_argument("--expiry-type", choices=["week", "month"], default="week")
    parser.add_argument("--from-date", default="2022-02-01")
    parser.add_argument("--to-date", default="2026-04-30")
    parser.add_argument("--source-run-stamp", default="20260505_024624")
    parser.add_argument("--vix-path", default="data/processed/spot/indiavix_1min_DHAN.csv")
    parser.add_argument("--vix-missing-policy", choices=["skip", "allow"], default="skip")
    parser.add_argument("--run-stamp", help="Output prefix; default YYYYMMDD_HHMMSS.")
    parser.add_argument("--skip-condor-runs", action="store_true", help="Only analyze existing naked ledgers.")
    parser.add_argument("--ic-source-stamp", default=None, help="Load pre-existing IC per-symbol CSVs from this run stamp instead of re-running condors.")
    args = parser.parse_args()

    run_stamp = args.run_stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    _out_dir().mkdir(parents=True, exist_ok=True)
    print(f"run_stamp: {run_stamp}")

    vix_filter = VixFilter(args.vix_path, missing_policy=args.vix_missing_policy)
    expiry_type_map = _expiry_map(args.expiry_type)
    strangle_ledgers = _load_strangle_sources(args, vix_filter)
    exact_lot_ledgers = _load_exact_lot_sources(args, vix_filter)
    strangle_matrix = _daily_matrix(strangle_ledgers, bucket_policy="skip_10_13")
    risk_parity_lots = _risk_parity_lots(strangle_matrix, total_lots=6)
    kelly_raw, kelly_lots = _kelly_lots(strangle_matrix, fraction=0.25, cap=4)

    portfolios: list[tuple[PortfolioSpec, pd.DataFrame]] = []
    naked_specs = [
        PortfolioSpec("naked_4x1", "Naked baseline 4x1", {s: 1 for s in INDICES}, "strangle", "all"),
        PortfolioSpec("naked_port_a", "Naked old Port A", {"BANKNIFTY": 1, "MIDCPNIFTY": 7}, "strangle", "all"),
        PortfolioSpec("naked_port_b", "Naked old Port B", {"BANKNIFTY": 2, "MIDCPNIFTY": 5}, "strangle", "all"),
        PortfolioSpec("naked_skip_10_13_4x1", "Naked skip VIX 10-13 4x1", {s: 1 for s in INDICES}, "strangle", "skip_10_13"),
        PortfolioSpec("naked_half_10_13_4x1", "Naked half VIX 10-13 4x1", {s: 1 for s in INDICES}, "strangle", "half_10_13"),
        PortfolioSpec("naked_capped_tilt_skip", "Naked capped tilt skip 10-13", {"NIFTY": 1, "BANKNIFTY": 1, "FINNIFTY": 1, "MIDCPNIFTY": 2}, "strangle", "skip_10_13"),
        PortfolioSpec("naked_no_nifty_tilt_skip", "Naked BN/FN/MCP tilt skip 10-13", {"BANKNIFTY": 1, "FINNIFTY": 1, "MIDCPNIFTY": 2}, "strangle", "skip_10_13"),
        PortfolioSpec("naked_risk_parity_skip", "Naked risk-parity skip 10-13", risk_parity_lots, "strangle", "skip_10_13"),
        PortfolioSpec("naked_qkelly_skip", "Naked quarter-Kelly skip 10-13", kelly_lots, "strangle", "skip_10_13"),
        # MCP-tilt sweep: drop NIFTY, hold FN:1, vary MCP and BN lots (all skip 10-13)
        PortfolioSpec("naked_mcp3_bn1_fn1_skip", "Naked MCP:3 BN:1 FN:1 skip 10-13", {"BANKNIFTY": 1, "FINNIFTY": 1, "MIDCPNIFTY": 3}, "strangle", "skip_10_13"),
        PortfolioSpec("naked_mcp4_bn1_fn1_skip", "Naked MCP:4 BN:1 FN:1 skip 10-13", {"BANKNIFTY": 1, "FINNIFTY": 1, "MIDCPNIFTY": 4}, "strangle", "skip_10_13"),
        PortfolioSpec("naked_mcp3_bn2_fn1_skip", "Naked MCP:3 BN:2 FN:1 skip 10-13", {"BANKNIFTY": 2, "FINNIFTY": 1, "MIDCPNIFTY": 3}, "strangle", "skip_10_13"),
        PortfolioSpec("naked_mcp4_bn2_fn1_skip", "Naked MCP:4 BN:2 FN:1 skip 10-13", {"BANKNIFTY": 2, "FINNIFTY": 1, "MIDCPNIFTY": 4}, "strangle", "skip_10_13"),
        PortfolioSpec("naked_mcp5_bn2_fn1_skip", "Naked MCP:5 BN:2 FN:1 skip 10-13", {"BANKNIFTY": 2, "FINNIFTY": 1, "MIDCPNIFTY": 5}, "strangle", "skip_10_13"),
        PortfolioSpec("naked_mcp6_bn2_fn1_skip", "Naked MCP:6 BN:2 FN:1 skip 10-13", {"BANKNIFTY": 2, "FINNIFTY": 1, "MIDCPNIFTY": 6}, "strangle", "skip_10_13"),
    ]
    for spec in naked_specs:
        portfolios.append((spec, _combine_portfolio_exact(strangle_ledgers, exact_lot_ledgers, spec)))

    condor_variants: dict[int, dict[str, pd.DataFrame]] = {}
    if args.ic_source_stamp:
        print(f"\nLoading IC ledgers from stamp: {args.ic_source_stamp}")
        condor_variants = _load_condor_sources(args.ic_source_stamp, vix_filter)
    elif not args.skip_condor_runs:
        for wing_gap in (2, 4, 6, 8):
            print(f"\n=== Running IC wing gap {wing_gap} (expiry: {' '.join(f'{s}={v[0]}' for s,v in expiry_type_map.items())}) ===")
            condor_variants[wing_gap] = _run_condor_variant(args, run_stamp, wing_gap, expiry_type_map)

    for wing_gap, ledgers in condor_variants.items():
        for policy, label_suffix in (("all", "all VIX"), ("skip_10_13", "skip VIX 10-13")):
            spec = PortfolioSpec(
                f"ic_wing{wing_gap}_{policy}",
                f"IC wing {wing_gap} {label_suffix}",
                {s: 1 for s in INDICES},
                f"iron_condor_wing{wing_gap}",
                policy,
            )
            portfolios.append((spec, _combine_portfolio(ledgers, spec)))

    # IC lot-sizing sweep for each profitable wing (skip 10-13 only)
    _ic_sizing_specs = [
        ("mcp2_bn1_fn1", {"BANKNIFTY": 1, "FINNIFTY": 1, "MIDCPNIFTY": 2}),
        ("mcp4_bn1_fn1", {"BANKNIFTY": 1, "FINNIFTY": 1, "MIDCPNIFTY": 4}),
        ("mcp4_bn2_fn1", {"BANKNIFTY": 2, "FINNIFTY": 1, "MIDCPNIFTY": 4}),
        ("mcp6_bn2_fn1", {"BANKNIFTY": 2, "FINNIFTY": 1, "MIDCPNIFTY": 6}),
        ("qkelly",       {"BANKNIFTY": 4, "FINNIFTY": 4, "MIDCPNIFTY": 4}),
    ]
    for wing_gap in (6, 8):
        if wing_gap in condor_variants:
            for size_tag, lots in _ic_sizing_specs:
                spec = PortfolioSpec(
                    f"ic{wing_gap}_{size_tag}_skip",
                    f"IC{wing_gap} {size_tag.replace('_', ' ')} skip 10-13",
                    lots,
                    f"iron_condor_wing{wing_gap}",
                    "skip_10_13",
                )
                portfolios.append((spec, _combine_portfolio(condor_variants[wing_gap], spec)))

    # Credit spread variants: put and call spreads at widths 4 and 6 (long at ±6 or ±8)
    # |long_offset| ≤ 8 keeps the long leg within ATM±10 at entry; ±6 gives 4-strike buffer
    _spread_defs = [
        ("put",  -2, -6),  # put spread width 4  — 4-strike intraday buffer
        ("put",  -2, -8),  # put spread width 6  — 2-strike intraday buffer (tighter)
        ("call",  2,  6),  # call spread width 4
        ("call",  2,  8),  # call spread width 6
    ]
    spread_ledger_store: dict[str, dict[str, pd.DataFrame]] = {}
    if not args.skip_condor_runs:
        for side, short_off, long_off in _spread_defs:
            tag = f"cs_{side}_s{abs(short_off)}_l{abs(long_off)}"
            print(f"\n=== Running {tag} ===")
            spread_ledger_store[tag] = _run_spread_variant(args, run_stamp, side, short_off, long_off, expiry_type_map)

    for side, short_off, long_off in _spread_defs:
        tag = f"cs_{side}_s{abs(short_off)}_l{abs(long_off)}"
        if tag not in spread_ledger_store:
            continue
        ledgers = spread_ledger_store[tag]
        for policy, label_suffix in (("all", "all VIX"), ("skip_10_13", "skip VIX 10-13")):
            spec = PortfolioSpec(
                f"{tag}_{policy}",
                f"CS {side} ±{abs(short_off)}/±{abs(long_off)} {label_suffix}",
                {s: 1 for s in INDICES},
                f"credit_spread_{side}_{abs(short_off)}x{abs(long_off)}",
                policy,
            )
            portfolios.append((spec, _combine_portfolio(ledgers, spec)))

    portfolio_table = _portfolio_rows(portfolios)
    portfolio_path = _out_dir() / f"{run_stamp}_portfolio_comparison.csv"
    portfolio_table.to_csv(portfolio_path, index=False)

    baseline = pd.concat(strangle_ledgers.values(), ignore_index=True)
    bucket_table = _bucket_attribution(baseline)
    bucket_path = _out_dir() / f"{run_stamp}_vix_bucket_attribution.csv"
    bucket_table.to_csv(bucket_path, index=False)

    report_path = _write_report(
        run_stamp=run_stamp,
        args=args,
        expiry_map=expiry_type_map,
        portfolio_table=portfolio_table,
        bucket_table=bucket_table,
        kelly_raw=kelly_raw,
        kelly_lots=kelly_lots,
        risk_parity_lots=risk_parity_lots,
    )
    print(f"\nportfolio table -> {portfolio_path}")
    print(f"bucket table    -> {bucket_path}")
    print(f"report          -> {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

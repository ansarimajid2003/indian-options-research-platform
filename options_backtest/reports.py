from __future__ import annotations

import math
from datetime import date as _date
from pathlib import Path

import pandas as pd

from .schemas import BacktestConfig, BacktestResult, Trade

# NSE changed NIFTY weekly expiry from Thursday to Tuesday on this date.
# Backtests spanning this boundary mix two market regimes.
_NIFTY_TUESDAY_EXPIRY_START = _date(2025, 9, 2)


def _spot_daily_close(spot_csv: str) -> pd.Series:
    """Return a date-indexed Series of daily close (last 1-min bar per day)."""
    df = pd.read_csv(spot_csv)
    ts_col = "datetime" if "datetime" in df.columns else "timestamp"
    if ts_col not in df.columns or "close" not in df.columns:
        return pd.Series(dtype=float)
    df[ts_col] = pd.to_datetime(df[ts_col])
    df["date"] = df[ts_col].dt.date
    return df.groupby("date")["close"].last()


def _bnh_metrics(
    daily: pd.DataFrame,
    spot_csv: str,
) -> tuple[float | None, float | None, float | None]:
    """Compute buy-and-hold return, CAGR, and Sharpe over the backtest date span."""
    if daily.empty or len(daily) < 2:
        return None, None, None
    spot_path = Path(spot_csv)
    if not spot_path.exists():
        return None, None, None
    closes = _spot_daily_close(spot_csv)
    start_date = daily["date"].iloc[0]
    end_date = daily["date"].iloc[-1]
    before_start = closes[closes.index <= start_date]
    before_end = closes[closes.index <= end_date]
    if before_start.empty or before_end.empty:
        return None, None, None
    start_price = float(before_start.iloc[-1])
    end_price = float(before_end.iloc[-1])
    bnh_return = round((end_price - start_price) / start_price, 4)
    n_days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days
    bnh_cagr = round((end_price / start_price) ** (365.25 / n_days) - 1, 4) if n_days >= 30 else None

    period_closes = closes[(closes.index >= start_date) & (closes.index <= end_date)].astype(float)
    bnh_sharpe: float | None = None
    if len(period_closes) >= 3:
        bnh_ret = period_closes.pct_change().dropna()
        std = float(bnh_ret.std(ddof=1))
        if std > 0:
            bnh_sharpe = round((float(bnh_ret.mean()) / std) * math.sqrt(252.0), 3)
    return bnh_return, bnh_cagr, bnh_sharpe


LEDGER_COLUMNS = [
    "strategy",
    "expiry",
    "entry_date",
    "entry_time",
    "exit_date",
    "exit_time",
    "entry_reason",
    "exit_reason",
    "dte_at_entry",
    "day_of_week",
    "exit_hour",
    "lot_size",
    "spot_entry",
    "spot_exit",
    "vix_entry",
    "vix_bucket",
    "vix_timestamp",
    "entry_legs",
    "exit_legs",
    "gross_pnl",
    "charges",
    "net_pnl",
    "equity",
]


def trade_ledger(trades: list[Trade], initial_capital: float = 0.0) -> pd.DataFrame:
    def _fills_str(fills: list) -> str:
        return ",".join(f"{f.side.value}:{f.contract.ticker}@{f.price}" for f in fills)

    rows = []
    for trade in trades:
        entry_ts = trade.entry_time
        exit_ts = trade.exit_time
        entry_date = entry_ts.date() if entry_ts is not None else None
        exit_date = exit_ts.date() if exit_ts is not None else None
        dte_at_entry = (trade.expiry - entry_date).days if entry_date is not None else None
        meta = trade.metadata or {}
        rows.append({
            "strategy": trade.strategy,
            "expiry": trade.expiry,
            "entry_date": entry_date,
            "entry_time": entry_ts,
            "exit_date": exit_date,
            "exit_time": exit_ts,
            "entry_reason": trade.entry_reason,
            "exit_reason": trade.exit_reason,
            "dte_at_entry": dte_at_entry,
            "day_of_week": entry_ts.day_name() if entry_ts is not None else None,
            "exit_hour": exit_ts.hour if exit_ts is not None else None,
            "lot_size": meta.get("lot_size"),
            "spot_entry": meta.get("spot_entry"),
            "spot_exit": meta.get("spot_exit"),
            "vix_entry": meta.get("vix_entry"),
            "vix_bucket": meta.get("vix_bucket"),
            "vix_timestamp": meta.get("vix_timestamp"),
            "entry_legs": _fills_str(trade.entry_fills),
            "exit_legs": _fills_str(trade.exit_fills),
            "gross_pnl": trade.gross_pnl,
            "charges": trade.charges,
            "net_pnl": trade.net_pnl,
            "equity": None,  # filled below after cumsum
        })
    df = pd.DataFrame(rows, columns=LEDGER_COLUMNS)
    if not df.empty:
        df["equity"] = round(initial_capital + df["net_pnl"].cumsum(), 2)
    return df


def trade_ledger_with_total(ledger: pd.DataFrame) -> pd.DataFrame:
    """Return a CSV-friendly ledger with a final TOTAL row for quick inspection."""
    columns = LEDGER_COLUMNS + ["trade_count"]
    if ledger.empty:
        total = {col: "" for col in columns}
        total.update({"strategy": "TOTAL", "gross_pnl": 0.0, "charges": 0.0, "net_pnl": 0.0, "trade_count": 0})
        return pd.DataFrame([total], columns=columns)

    out = ledger.copy()
    for col in LEDGER_COLUMNS:
        if col not in out.columns:
            out[col] = None
    out["trade_count"] = 1
    total = {col: "" for col in columns}
    total.update({
        "strategy": "TOTAL",
        "gross_pnl": round(float(out["gross_pnl"].sum()), 2),
        "charges": round(float(out["charges"].sum()), 2),
        "net_pnl": round(float(out["net_pnl"].sum()), 2),
        "trade_count": int(len(out)),
    })
    return pd.concat([out[columns], pd.DataFrame([total], columns=columns)], ignore_index=True)


def daily_pnl(ledger: pd.DataFrame) -> pd.DataFrame:
    if ledger.empty:
        return pd.DataFrame(columns=["date", "gross_pnl", "charges", "net_pnl"])
    df = ledger.copy()
    df["date"] = pd.to_datetime(df["exit_time"]).dt.date
    return df.groupby("date", as_index=False)[["gross_pnl", "charges", "net_pnl"]].sum()


def equity_curve(daily: pd.DataFrame, initial_capital: float = 1_000_000.0) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame(columns=["date", "equity"])
    curve = daily[["date", "net_pnl"]].copy()
    curve["equity"] = initial_capital + curve["net_pnl"].cumsum()
    return curve[["date", "equity"]]


def summary(
    ledger: pd.DataFrame,
    curve: pd.DataFrame,
    daily: pd.DataFrame,
    initial_capital: float = 1_000_000.0,
    bnh_return: float | None = None,
    bnh_cagr: float | None = None,
    bnh_sharpe: float | None = None,
) -> dict:
    if ledger.empty:
        return {
            "trades": 0,
            "net_pnl": 0.0,
            "gross_pnl": 0.0,
            "charges": 0.0,
            "win_rate": 0.0,
            "max_drawdown": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe": None,
            "sortino": None,
            "sharpe_tstat": None,
            "calmar": None,
            "cagr": None,
            "total_return": 0.0,
            "profit_factor": None,
            "avg_trade_pnl": 0.0,
            "bnh_return": None,
            "bnh_cagr": None,
            "bnh_sharpe": None,
            "date_from": None,
            "date_to": None,
            "initial_capital": initial_capital,
            "regime_break_in_sample": False,
        }

    wins = (ledger["net_pnl"] > 0).sum()
    equity = curve["equity"] if not curve.empty else pd.Series(dtype=float)
    running_max = equity.cummax() if not equity.empty else equity
    drawdown = float((equity - running_max).min()) if not equity.empty else 0.0

    # CAGR: requires at least two distinct trade days spanning ≥30 calendar days
    cagr: float | None = None
    if not daily.empty and len(daily) >= 2:
        start_dt = pd.to_datetime(daily["date"].iloc[0])
        end_dt = pd.to_datetime(daily["date"].iloc[-1])
        n_days = (end_dt - start_dt).days
        if n_days >= 30 and initial_capital > 0:
            n_years = n_days / 365.25
            final_equity = float(equity.iloc[-1]) if not equity.empty else initial_capital
            ratio = final_equity / initial_capital
            if ratio <= 0:
                cagr = -1.0  # total ruin; negative equity is unphysical, cap at -100%
            else:
                cagr = round(ratio ** (1.0 / n_years) - 1.0, 4)

    # Sharpe, Sortino, and significance — all computed over the same business-day grid
    sharpe: float | None = None
    sortino: float | None = None
    sharpe_tstat: float | None = None
    if not daily.empty and len(daily) >= 2 and initial_capital > 0:
        start_dt = pd.to_datetime(daily["date"].iloc[0])
        end_dt = pd.to_datetime(daily["date"].iloc[-1])
        biz_days = pd.bdate_range(start=start_dt, end=end_dt)
        daily_ret = pd.Series(0.0, index=biz_days)
        trade_ret = pd.Series(
            daily["net_pnl"].values / initial_capital,
            index=pd.to_datetime(daily["date"]),
        )
        daily_ret.update(trade_ret)
        mean_ret = float(daily_ret.mean())
        std = float(daily_ret.std())
        if std > 0:
            sharpe = round((mean_ret / std) * math.sqrt(252.0), 3)
            # t-stat uses weekly buckets: intra-week trades share the same expiry and are
            # not independent observations. Daily n overstates significance by ~sqrt(5).
            weekly_pnl = daily_ret.resample("W-FRI").sum()
            n_weeks = len(weekly_pnl)
            w_std = float(weekly_pnl.std(ddof=1))
            if w_std > 0:
                sharpe_tstat = round((float(weekly_pnl.mean()) / w_std) * math.sqrt(n_weeks), 3)
        # Sortino: same mean, but downside deviation only
        downside = daily_ret[daily_ret < 0]
        if len(downside) > 1:
            down_std = float(downside.std(ddof=1))
            if down_std > 0:
                sortino = round((mean_ret / down_std) * math.sqrt(252.0), 3)

    # Profit factor: gross winning PnL / abs(gross losing PnL)
    gross_wins = float(ledger.loc[ledger["net_pnl"] > 0, "net_pnl"].sum())
    gross_losses = abs(float(ledger.loc[ledger["net_pnl"] < 0, "net_pnl"].sum()))
    profit_factor: float | None = round(gross_wins / gross_losses, 3) if gross_losses > 0 else None

    # Calmar: CAGR / max drawdown percentage (risk-adjusted return for drawdown-sensitive strategies)
    max_dd_pct = round(drawdown / initial_capital, 4) if initial_capital else 0.0
    calmar: float | None = round(cagr / abs(max_dd_pct), 3) if (cagr is not None and max_dd_pct != 0) else None

    # Regime break: NSE changed NIFTY weekly expiry Thu → Tue on 2025-09-02
    regime_break_in_sample = False
    if not daily.empty:
        d0 = daily["date"].iloc[0]
        d1 = daily["date"].iloc[-1]
        if isinstance(d0, str):
            d0 = _date.fromisoformat(d0)
        if isinstance(d1, str):
            d1 = _date.fromisoformat(d1)
        regime_break_in_sample = bool(d0 < _NIFTY_TUESDAY_EXPIRY_START <= d1)

    net_pnl = round(float(ledger["net_pnl"].sum()), 2)
    date_from = str(daily["date"].iloc[0]) if not daily.empty else None
    date_to = str(daily["date"].iloc[-1]) if not daily.empty else None

    return {
        "trades": int(len(ledger)),
        "net_pnl": net_pnl,
        "gross_pnl": round(float(ledger["gross_pnl"].sum()), 2),
        "charges": round(float(ledger["charges"].sum()), 2),
        "win_rate": round(float(wins / len(ledger)), 4),
        "max_drawdown": round(drawdown, 2),
        "max_drawdown_pct": max_dd_pct,
        "sharpe": sharpe,
        "sortino": sortino,
        "sharpe_tstat": sharpe_tstat,
        "calmar": calmar,
        "cagr": cagr,
        "total_return": round(net_pnl / initial_capital, 4) if initial_capital else 0.0,
        "profit_factor": profit_factor,
        "avg_trade_pnl": round(float(ledger["net_pnl"].mean()), 2),
        "bnh_return": bnh_return,
        "bnh_cagr": bnh_cagr,
        "bnh_sharpe": bnh_sharpe,
        "date_from": date_from,
        "date_to": date_to,
        "initial_capital": initial_capital,
        "regime_break_in_sample": regime_break_in_sample,
    }


def batch_summary_md(results: list[tuple[str, dict]], run_stamp: str) -> str:
    """Return a markdown string summarising all strategies in a single batch run."""
    from datetime import datetime as _dt

    def _pnl(v: float | None) -> str:
        if v is None:
            return "—"
        sign = "+" if v >= 0 else ""
        return f"{sign}₹{v:,.0f}"

    def _pct(v: float | None) -> str:
        if v is None:
            return "—"
        return f"{v * 100:+.2f}%"

    def _win(v: float | None) -> str:
        if v is None:
            return "—"
        return f"{v * 100:.1f}%"

    def _num(v: float | None, fmt: str = ".3f") -> str:
        if v is None:
            return "—"
        return format(v, fmt)

    def _display(name: str) -> str:
        return name.replace("_", " ").title()

    lines: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines.append(f"# Backtest Batch — {run_stamp}")
    lines.append("")
    try:
        dt = _dt.strptime(run_stamp[:15], "%Y%m%d_%H%M%S")
        lines.append(f"> Generated {dt.strftime('%Y-%m-%d %H:%M')}")
    except Exception:
        pass

    capital = next((s.get("initial_capital") for _, s in results if s.get("initial_capital")), None)
    if capital:
        lines.append(f"> Initial capital: ₹{capital:,.0f}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Comparison table ──────────────────────────────────────────────────────
    lines.append("## Strategy Comparison")
    lines.append("")
    lines.append(
        "| Strategy | Period | Trades | Net PnL | Total Return | CAGR | BnH CAGR | BnH Sharpe"
        " | Sharpe | t-stat | Sortino | Calmar | Win% | Max DD% | Profit Factor |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for name, s in results:
        period = (
            f"{s['date_from']} → {s['date_to']}"
            if s.get("date_from") and s.get("date_to")
            else "—"
        )
        regime_warn = " ⚠️" if s.get("regime_break_in_sample") else ""
        lines.append(
            f"| {_display(name)}{regime_warn} | {period} | {s['trades']:,}"
            f" | {_pnl(s.get('net_pnl'))} | {_pct(s.get('total_return'))}"
            f" | {_pct(s.get('cagr'))} | {_pct(s.get('bnh_cagr'))}"
            f" | {_num(s.get('bnh_sharpe'))} | {_num(s.get('sharpe'))} | {_num(s.get('sharpe_tstat'))}"
            f" | {_num(s.get('sortino'))} | {_num(s.get('calmar'))}"
            f" | {_win(s.get('win_rate'))}"
            f" | {_pct(s.get('max_drawdown_pct'))} | {_num(s.get('profit_factor'))} |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Per-strategy detail ───────────────────────────────────────────────────
    for name, s in results:
        lines.append(f"## {_display(name)}")
        lines.append("")
        period = (
            f"{s['date_from']} → {s['date_to']}"
            if s.get("date_from") and s.get("date_to")
            else "—"
        )
        lines.append(f"**Period:** {period} &nbsp;|&nbsp; **Trades:** {s['trades']:,}")
        lines.append("")

        # PnL breakdown
        gross = s.get("gross_pnl") or 0.0
        charges = s.get("charges") or 0.0
        charge_drag = f" ({abs(charges / gross * 100):.1f}% of gross)" if gross > 0 else ""
        lines.append("### PnL Breakdown")
        lines.append("")
        lines.append("| Item | Amount |")
        lines.append("|---|---|")
        lines.append(f"| Gross PnL | {_pnl(s.get('gross_pnl'))} |")
        lines.append(f"| Charges | {_pnl(-abs(charges))}{charge_drag} |")
        lines.append(f"| **Net PnL** | **{_pnl(s.get('net_pnl'))}** |")
        lines.append(f"| Avg per trade | {_pnl(s.get('avg_trade_pnl'))} |")
        lines.append("")

        # Returns vs benchmark
        lines.append("### Returns vs Benchmark")
        lines.append("")
        lines.append("| Metric | Strategy | Buy & Hold |")
        lines.append("|---|---|---|")
        lines.append(f"| Total Return | {_pct(s.get('total_return'))} | {_pct(s.get('bnh_return'))} |")
        lines.append(f"| CAGR | {_pct(s.get('cagr'))} | {_pct(s.get('bnh_cagr'))} |")
        lines.append(f"| Sharpe | {_num(s.get('sharpe'))} | {_num(s.get('bnh_sharpe'))} |")
        lines.append("")

        # Risk metrics
        lines.append("### Risk Metrics")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        lines.append(f"| Win Rate | {_win(s.get('win_rate'))} |")
        lines.append(f"| Profit Factor | {_num(s.get('profit_factor'))} |")
        dd = s.get("max_drawdown")
        dd_pct = s.get("max_drawdown_pct")
        dd_str = f"{_pnl(dd)} ({_pct(dd_pct)})" if dd is not None else "—"
        lines.append(f"| Max Drawdown | {dd_str} |")
        lines.append(f"| Sharpe Ratio | {_num(s.get('sharpe'))} |")
        lines.append(f"| Sharpe t-stat | {_num(s.get('sharpe_tstat'))} (need |t| > 1.96 for p < 0.05) |")
        lines.append(f"| Sortino Ratio | {_num(s.get('sortino'))} |")
        lines.append(f"| Calmar Ratio | {_num(s.get('calmar'))} |")
        if s.get("regime_break_in_sample"):
            lines.append("| ⚠️ Regime break | NSE expiry changed Thu → Tue on 2025-09-02 — split pre/post before concluding |")
            pre  = s.get("regime_split_pre")
            post = s.get("regime_split_post")
            if pre or post:
                lines.append("")
                lines.append("### Regime Split")
                lines.append("")
                lines.append("| Period | Trades | Net PnL | Gross PnL | Win% | Sharpe | PF | Max DD% |")
                lines.append("|---|---|---|---|---|---|---|---|")
                for label, expiry_label, sub in [
                    ("Pre",  "Thu expiry", pre),
                    ("Post", "Tue expiry", post),
                ]:
                    if sub is None:
                        continue
                    period_str = f"{sub.get('date_from','?')} → {sub.get('date_to','?')}"
                    sharpe_val = sub.get("sharpe")
                    sharpe_str = (f"{sharpe_val:+.3f}" if sharpe_val is not None else "—")
                    pf_val = sub.get("profit_factor")
                    pf_str = f"{pf_val:.3f}" if pf_val is not None else "—"
                    dd_val = sub.get("max_drawdown_pct")
                    dd_str = f"{dd_val * 100:.2f}%" if dd_val is not None else "—"
                    lines.append(
                        f"| {label} · {expiry_label} · {period_str}"
                        f" | {sub.get('trades', 0):,}"
                        f" | {_pnl(sub.get('net_pnl'))}"
                        f" | {_pnl(sub.get('gross_pnl'))}"
                        f" | {_win(sub.get('win_rate'))}"
                        f" | {sharpe_str}"
                        f" | {pf_str}"
                        f" | {dd_str} |"
                    )
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _regime_split_summaries(ledger: pd.DataFrame, initial_capital: float) -> tuple[dict | None, dict | None]:
    """Split ledger at _NIFTY_TUESDAY_EXPIRY_START and return (pre_summary, post_summary)."""
    exit_dates = pd.to_datetime(ledger["exit_date"]).dt.date
    pre_ledger  = ledger[exit_dates <  _NIFTY_TUESDAY_EXPIRY_START].copy()
    post_ledger = ledger[exit_dates >= _NIFTY_TUESDAY_EXPIRY_START].copy()
    results = []
    for sub in (pre_ledger, post_ledger):
        if sub.empty:
            results.append(None)
            continue
        sub_daily = daily_pnl(sub)
        sub_curve = equity_curve(sub_daily, initial_capital)
        results.append(summary(sub, sub_curve, sub_daily, initial_capital))
    return results[0], results[1]


def build_result(config: BacktestConfig, trades: list[Trade]) -> BacktestResult:
    ledger = trade_ledger(trades, config.initial_capital)
    daily = daily_pnl(ledger)
    curve = equity_curve(daily, config.initial_capital)
    bnh_ret, bnh_cagr, bnh_sharpe = _bnh_metrics(daily, config.spot_csv) if config.spot_csv else (None, None, None)
    s = summary(ledger, curve, daily, config.initial_capital, bnh_ret, bnh_cagr, bnh_sharpe)
    if s.get("regime_break_in_sample") and not ledger.empty:
        pre, post = _regime_split_summaries(ledger, config.initial_capital)
        s["regime_split_pre"]  = pre
        s["regime_split_post"] = post
    return BacktestResult(
        config=config,
        trades=trades,
        summary=s,
        trade_ledger=ledger,
        daily_pnl=daily,
        equity_curve=curve,
    )

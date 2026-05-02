from __future__ import annotations

import pandas as pd

from .schemas import BacktestConfig, BacktestResult, Trade


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
    "legs",
    "gross_pnl",
    "charges",
    "net_pnl",
]


def trade_ledger(trades: list[Trade]) -> pd.DataFrame:
    rows = []
    for trade in trades:
        entry_ts = trade.entry_time
        exit_ts = trade.exit_time
        entry_date = entry_ts.date() if entry_ts is not None else None
        exit_date = exit_ts.date() if exit_ts is not None else None
        dte_at_entry = (trade.expiry - entry_date).days if entry_date is not None else None
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
            "lot_size": trade.metadata.get("lot_size") if trade.metadata else None,
            "legs": ",".join(f"{f.side.value}:{f.contract.ticker}@{f.price}" for f in trade.entry_fills),
            "gross_pnl": trade.gross_pnl,
            "charges": trade.charges,
            "net_pnl": trade.net_pnl,
        })
    return pd.DataFrame(rows, columns=LEDGER_COLUMNS)


def trade_ledger_with_total(ledger: pd.DataFrame) -> pd.DataFrame:
    """Return a CSV-friendly ledger with a final TOTAL row for quick inspection."""
    columns = LEDGER_COLUMNS + ["trade_count"]
    if ledger.empty:
        total = {col: "" for col in columns}
        total.update({"strategy": "TOTAL", "gross_pnl": 0.0, "charges": 0.0, "net_pnl": 0.0, "trade_count": 0})
        return pd.DataFrame([total], columns=columns)

    out = ledger.copy()
    # Ensure all ledger columns are present (handles ledgers built externally)
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


def equity_curve(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame(columns=["date", "equity"])
    curve = daily[["date", "net_pnl"]].copy()
    curve["equity"] = curve["net_pnl"].cumsum()
    return curve[["date", "equity"]]


def summary(ledger: pd.DataFrame, curve: pd.DataFrame) -> dict[str, float | int]:
    if ledger.empty:
        return {"trades": 0, "net_pnl": 0.0, "gross_pnl": 0.0, "charges": 0.0, "win_rate": 0.0, "max_drawdown": 0.0}
    wins = (ledger["net_pnl"] > 0).sum()
    equity = curve["equity"] if not curve.empty else pd.Series(dtype=float)
    running_max = equity.cummax() if not equity.empty else equity
    drawdown = (equity - running_max).min() if not equity.empty else 0.0
    return {
        "trades": int(len(ledger)),
        "net_pnl": round(float(ledger["net_pnl"].sum()), 2),
        "gross_pnl": round(float(ledger["gross_pnl"].sum()), 2),
        "charges": round(float(ledger["charges"].sum()), 2),
        "win_rate": round(float(wins / len(ledger)), 4),
        "max_drawdown": round(float(drawdown), 2),
    }


def build_result(config: BacktestConfig, trades: list[Trade]) -> BacktestResult:
    ledger = trade_ledger(trades)
    daily = daily_pnl(ledger)
    curve = equity_curve(daily)
    return BacktestResult(config=config, trades=trades, summary=summary(ledger, curve), trade_ledger=ledger, daily_pnl=daily, equity_curve=curve)

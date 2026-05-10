"""
Convert paper trades JSON to a pandas DataFrame matching the reports.py trade_ledger format.

Usage (CLI):
    python scripts/live/paper_json_to_ledger.py --date 20260512 --live-root data/live

Usage (import):
    from scripts.live.paper_json_to_ledger import paper_trades_to_ledger
    df = paper_trades_to_ledger(json_path, session_date)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from options_backtest.reports import (
    LEDGER_COLUMNS,
    daily_pnl,
    equity_curve,
    summary,
    trade_ledger_with_total,
)

_IST = "Asia/Kolkata"


def _parse_ts(value: str | None) -> "pd.Timestamp":
    if not value:
        return pd.NaT
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize(_IST)
    else:
        ts = ts.tz_convert(_IST)
    return ts


def paper_trades_to_ledger(json_path: Path, session_date: date) -> pd.DataFrame:
    """
    Convert a paper trades JSON file to a trade ledger DataFrame.

    Each trade in the JSON must have entry_fills and exit_fills lists.
    Returns an empty DataFrame if the file has no trades.
    """
    raw = json.loads(Path(json_path).read_text())
    if not raw:
        return _empty_ledger()

    rows: list[dict] = []
    for trade in raw:
        entry_fills = trade.get("entry_fills", [])
        exit_fills = trade.get("exit_fills", [])

        # Entry credit: SELL legs positive, BUY legs negative
        entry_credit = 0.0
        entry_charges = 0.0
        for f in entry_fills:
            qty = f["quantity"]
            price = f["price"]
            if f["side"] == "SELL":
                entry_credit += price * qty
            else:
                entry_credit -= price * qty
            entry_charges += f.get("charges", 0.0)

        # Exit debit: BUY legs positive (cost to close shorts), SELL legs negative
        exit_debit = 0.0
        exit_charges = 0.0
        for f in exit_fills:
            qty = f["quantity"]
            price = f["price"]
            if f["side"] == "BUY":
                exit_debit += price * qty
            else:
                exit_debit -= price * qty
            exit_charges += f.get("charges", 0.0)

        gross_pnl = entry_credit - exit_debit
        total_charges = entry_charges + exit_charges
        net_pnl = gross_pnl - total_charges

        entry_time = _parse_ts(trade.get("entry_time"))
        exit_time = _parse_ts(trade.get("exit_time"))
        entry_date = entry_time.date() if not pd.isna(entry_time) else None
        exit_date = exit_time.date() if not pd.isna(exit_time) else None
        expiry = date.fromisoformat(trade["expiry"]) if trade.get("expiry") else None

        rows.append({
            "symbol": trade.get("symbol", ""),
            "strategy": trade.get("strategy", "IronCondorWing6"),
            "expiry": expiry,
            "entry_date": entry_date,
            "entry_time": entry_time,
            "exit_date": exit_date,
            "exit_time": exit_time,
            "entry_reason": trade.get("entry_reason", "time_entry"),
            "exit_reason": trade.get("exit_reason", ""),
            "dte_at_entry": (expiry - entry_date).days if expiry and entry_date else None,
            "day_of_week": entry_time.day_name() if not pd.isna(entry_time) else None,
            "exit_hour": exit_time.hour if not pd.isna(exit_time) else None,
            "lot_size": int(trade.get("lot_size", 0)),
            "spot_entry": trade.get("spot_entry"),
            "spot_exit": trade.get("spot_exit"),
            "vix_entry": trade.get("vix_entry"),
            "vix_bucket": trade.get("vix_bucket"),
            "vix_timestamp": trade.get("vix_timestamp"),
            "entry_legs": _fills_str(entry_fills),
            "exit_legs": _fills_str(exit_fills),
            "gross_pnl": round(gross_pnl, 2),
            "charges": round(total_charges, 2),
            "net_pnl": round(net_pnl, 2),
            "equity": None,
        })

    if not rows:
        return _empty_ledger()

    df = pd.DataFrame(rows)
    for col in LEDGER_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[LEDGER_COLUMNS]
    if not df.empty:
        df["equity"] = 1_000_000.0 + df["net_pnl"].cumsum()
    return df


def _empty_ledger() -> pd.DataFrame:
    return pd.DataFrame(columns=LEDGER_COLUMNS)


def _fills_str(fills: list[dict]) -> str:
    parts = []
    for fill in fills:
        side = fill.get("side", "")
        sid = fill.get("security_id", "")
        role = fill.get("leg_role", "")
        price = fill.get("price", "")
        parts.append(f"{side}:{role}:{sid}@{price}")
    return ",".join(parts)


def write_paper_reports(
    json_path: Path,
    session_date: date,
    live_root: Path,
    initial_capital: float = 1_000_000.0,
) -> tuple[Path, Path, Path]:
    ledger = paper_trades_to_ledger(json_path, session_date)
    if not ledger.empty:
        ledger["equity"] = initial_capital + ledger["net_pnl"].cumsum()
    daily = daily_pnl(ledger)
    curve = equity_curve(daily, initial_capital)
    stats = summary(ledger, curve, daily, initial_capital)

    out_dir = live_root / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = session_date.strftime("%Y%m%d")
    md_path = out_dir / f"{date_str}_paper_summary.md"
    json_out = out_dir / f"{date_str}_paper_summary.json"
    ledger_path = out_dir / f"{date_str}_paper_ledger.csv"

    lines = [
        f"# Paper Trading Summary - {session_date.isoformat()}",
        "",
        f"Trades: {stats['trades']}",
        f"Gross PnL: {stats['gross_pnl']:.2f}",
        f"Charges: {stats['charges']:.2f}",
        f"Net PnL: {stats['net_pnl']:.2f}",
        f"Win Rate: {stats['win_rate'] * 100:.1f}%",
        f"Sharpe: {stats.get('sharpe')}",
        f"Max Drawdown: {stats['max_drawdown']:.2f}",
        "",
        "## Daily PnL",
        "",
    ]
    if daily.empty:
        lines.append("No closed paper trades.")
    else:
        lines.append("| Date | Gross PnL | Charges | Net PnL |")
        lines.append("|---|---:|---:|---:|")
        for _, row in daily.iterrows():
            lines.append(
                f"| {row['date']} | {row['gross_pnl']:.2f} | "
                f"{row['charges']:.2f} | {row['net_pnl']:.2f} |"
            )

    md_path.write_text("\n".join(lines), encoding="utf-8")
    json_out.write_text(json.dumps(stats, indent=2, default=str), encoding="utf-8")
    trade_ledger_with_total(ledger).to_csv(ledger_path, index=False)
    return md_path, json_out, ledger_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert paper trades JSON to ledger DataFrame")
    parser.add_argument("--date", required=True, help="Session date YYYYMMDD")
    parser.add_argument("--live-root", default="data/live", help="Path to live data root")
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    args = parser.parse_args()

    session_date = date(int(args.date[:4]), int(args.date[4:6]), int(args.date[6:8]))
    live_root = Path(args.live_root)
    json_path = live_root / "paper_trades" / f"{args.date}.json"

    if not json_path.exists():
        print(f"File not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    df = paper_trades_to_ledger(json_path, session_date)
    md_path, json_out, ledger_path = write_paper_reports(
        json_path=json_path,
        session_date=session_date,
        live_root=live_root,
        initial_capital=args.initial_capital,
    )
    print(f"Rows: {len(df)}")
    print(f"Net PnL total: {df['net_pnl'].sum():.2f}" if not df.empty else "Net PnL total: 0.00")
    print(f"Markdown: {md_path}")
    print(f"JSON: {json_out}")
    print(f"Ledger CSV: {ledger_path}")


if __name__ == "__main__":
    main()

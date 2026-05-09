"""
Compute defined-risk profile for optimized wing-6 IC.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from options_backtest.reports import daily_pnl, equity_curve, summary

OUT_DIR = ROOT / "reports/backtests/options/risk_management"

SOURCES = {
    "NIFTY":      OUT_DIR / "20260506_vixgt13_ic_wing6_nifty.csv",
    "FINNIFTY":   OUT_DIR / "20260506_dtelt7_ic_wing6_finnifty.csv",
    "MIDCPNIFTY": OUT_DIR / "20260506_dtelt7_ic_wing6_midcpnifty.csv",
}

_LEG_RE = re.compile(r"(BUY|SELL):([A-Z]+)_DHAN_(\d+)(CE|PE)@([0-9.]+)")


def parse_legs(raw):
    legs = []
    if not isinstance(raw, str):
        return legs
    for m in _LEG_RE.finditer(raw):
        legs.append({
            "side": m.group(1),
            "symbol": m.group(2),
            "strike": int(m.group(3)),
            "otype": m.group(4),
            "price": float(m.group(5)),
        })
    return legs


def trade_risk(row):
    legs = parse_legs(row.get("entry_legs", ""))
    lot_size = float(row.get("lot_size", 0) or 0)
    if len(legs) != 4 or lot_size <= 0:
        return None, None, None
    credit_unit = sum(l["price"] if l["side"] == "SELL" else -l["price"] for l in legs)
    calls = sorted([l for l in legs if l["otype"] == "CE"], key=lambda x: x["strike"])
    puts = sorted([l for l in legs if l["otype"] == "PE"], key=lambda x: x["strike"])
    if len(calls) != 2 or len(puts) != 2:
        return None, None, None
    call_width = abs(calls[1]["strike"] - calls[0]["strike"])
    put_width = abs(puts[1]["strike"] - puts[0]["strike"])
    width = max(call_width, put_width)
    max_loss = max(0.0, (width - credit_unit) * lot_size)
    entry_credit = credit_unit * lot_size
    return max_loss, entry_credit, width


def main():
    for sym, path in SOURCES.items():
        df = pd.read_csv(path)
        df = df[df["strategy"] != "TOTAL"].copy()
        risks = df.apply(trade_risk, axis=1)
        df["max_loss"] = [r[0] for r in risks]
        df["entry_credit"] = [r[1] for r in risks]
        df["wing_width"] = [r[2] for r in risks]

        ml = df["max_loss"].dropna()
        ec = df["entry_credit"].dropna()
        ww = df["wing_width"].dropna()

        print(f"\n{'='*60}")
        print(f"  {sym}")
        print(f"{'='*60}")
        print(f"  Trades: {len(df)}")
        print(f"  Wing width (strikes): {ww.mode().iloc[0]:.0f}  (range: {ww.min():.0f}-{ww.max():.0f})")
        print(f"  Avg entry credit:  INR {ec.mean():>8,.0f}  (median {ec.median():>8,.0f})")
        print(f"  Avg max loss:      INR {ml.mean():>8,.0f}  (median {ml.median():>8,.0f})")
        print(f"  Worst max loss:    INR {ml.max():>8,.0f}")
        print(f"  Best max loss:     INR {ml.min():>8,.0f}")
        print(f"  Credit/Risk ratio: {ec.mean()/ml.mean():.3f}")

        # Daily open risk
        by_day = df.groupby("entry_date")[["max_loss", "entry_credit"]].sum()
        print(f"\n  Daily open risk:")
        print(f"    Worst day max loss: INR {by_day['max_loss'].max():>10,.0f}")
        print(f"    Avg day max loss:   INR {by_day['max_loss'].mean():>10,.0f}")
        print(f"    Median day max loss: INR {by_day['max_loss'].median():>10,.0f}")

        # CVaR95 on daily PnL
        dpnl = daily_pnl(df)
        pnl = pd.to_numeric(dpnl["net_pnl"], errors="coerce").dropna()
        if not pnl.empty:
            cutoff = float(pnl.quantile(0.05))
            tail = pnl[pnl <= cutoff]
            cvar95 = round(float(tail.mean()), 2)
            var95 = round(cutoff, 2)
            print(f"\n  Daily PnL tail:")
            print(f"    VaR 95%:  INR {var95:>10,.0f}  (5% of days are worse than this)")
            print(f"    CVaR 95%: INR {cvar95:>10,.0f}  (avg loss on those worst 5% days)")
            print(f"    Max daily loss: INR {pnl.min():>10,.0f}")

        # Breakeven analysis
        print(f"\n  Breakeven / Moneyness:")
        print(f"    Avg credit as % of wing width: {(ec.mean() / (ww.mean() * df['lot_size'].astype(float).mean())) * 100:.1f}%")


if __name__ == "__main__":
    main()

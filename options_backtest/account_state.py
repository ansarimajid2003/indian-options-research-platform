"""
Persistent account-state and margin tracker for the live paper-trading stack.

Deterministic and OFFLINE: no network, no random, no global mutable state. The
broker snapshot (Kotak ``limits()``) is consumed only from a pre-written file
(``account/kotak_limits_{YYYYMMDD}.json``); this module never opens a socket.

Two-tier margin model (see configs/live/margin_model.json):
  Tier 1 broker_snapshot   - real Kotak MarginUsed/Net for actual open positions
                             (hedged number; preferred when present).
  Tier 2 calibrated_formula - defined-risk max-loss with a post-Nov-2024 hedged
                             floor per lot, plus an expiry-day ELM add-on.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_STARTING_CAPITAL_FALLBACK = 1_000_000.0


@dataclass(frozen=True)
class IndexMarginParams:
    hedged_floor_per_lot: float
    expiry_elm_pct: float
    expiry_elm_dte: int


@dataclass(frozen=True)
class MarginModel:
    starting_capital: float
    per_index: dict[str, IndexMarginParams]

    @classmethod
    def from_config(cls, path: Path) -> "MarginModel":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        per_index = {
            sym: IndexMarginParams(
                hedged_floor_per_lot=float(p["hedged_floor_per_lot"]),
                expiry_elm_pct=float(p["expiry_elm_pct"]),
                expiry_elm_dte=int(p["expiry_elm_dte"]),
            )
            for sym, p in raw.get("per_index", {}).items()
        }
        return cls(
            starting_capital=float(raw.get("starting_capital", _STARTING_CAPITAL_FALLBACK)),
            per_index=per_index,
        )

    def trade_margin(self, trade: dict, broker_snapshot: dict | None = None) -> dict:
        symbol = trade["symbol"]
        lots = int(trade["lots"])
        lot_size = int(trade["lot_size"])
        quantity = lots * lot_size

        short_call = int(trade.get("short_call_strike", 0))
        long_call = int(trade.get("long_call_strike", 0))
        short_put = int(trade.get("short_put_strike", 0))
        long_put = int(trade.get("long_put_strike", 0))
        wing_width = max(long_call - short_call, short_put - long_put)

        entry_credit = float(trade.get("entry_credit", 0.0))  # already rupees, summed over fills
        defined_max_loss = max(0.0, wing_width * quantity - entry_credit)

        long_premium_paid = sum(
            float(f["price"]) * int(f["quantity"])
            for f in trade.get("entry_fills", [])
            if f.get("side") == "BUY"
        )

        dte_at_entry = (date.fromisoformat(trade["expiry"]) - _entry_date(trade)).days

        params = self.per_index.get(symbol)
        if params is None:
            # Unknown symbol: no hedged floor / ELM, fall back to pure defined risk.
            params = IndexMarginParams(hedged_floor_per_lot=0.0, expiry_elm_pct=0.0, expiry_elm_dte=0)

        # spot_proxy approximates the underlying for the percent-of-notional expiry ELM add-on.
        spot_proxy = trade.get("spot_entry") or (short_call + short_put) / 2.0
        if dte_at_entry <= params.expiry_elm_dte:
            expiry_day_elm = float(spot_proxy) * params.expiry_elm_pct * quantity
        else:
            expiry_day_elm = 0.0

        short_leg_margin = max(defined_max_loss, params.hedged_floor_per_lot * lots) + expiry_day_elm
        blocked_margin = short_leg_margin
        margin_source = "calibrated_formula"

        # Broker MarginUsed is account-level (not per-symbol) for a hedged IC, so it is
        # applied at the session level in compute_session_row. Only a genuine per-symbol
        # breakdown overrides here.
        if broker_snapshot:
            per_symbol_broker = broker_snapshot.get("per_symbol_margin_used")
            if isinstance(per_symbol_broker, dict) and symbol in per_symbol_broker:
                blocked_margin = float(per_symbol_broker[symbol])
                margin_source = "broker_snapshot"

        capital_committed = blocked_margin + long_premium_paid

        return {
            "symbol": symbol,
            "quantity": quantity,
            "wing_width": wing_width,
            "defined_max_loss": round(defined_max_loss, 2),
            "long_premium_paid": round(long_premium_paid, 2),
            "expiry_day_elm": round(expiry_day_elm, 2),
            "blocked_margin": round(blocked_margin, 2),
            "capital_committed": round(capital_committed, 2),
            "dte_at_entry": dte_at_entry,
            "margin_source": margin_source,
        }


def _entry_date(trade: dict) -> date:
    raw = trade.get("entry_time") or trade.get("session_date")
    if raw is None:
        raise KeyError("trade missing entry_time and session_date")
    # entry_time is ISO-8601 (possibly with tz); session_date is a plain ISO date.
    return datetime.fromisoformat(raw).date()


def _trade_pnl(trade: dict) -> tuple[float, float, float]:
    """Prefer the trade's authoritative rupee fields; recompute from fills only if absent."""
    if "net_pnl" in trade and "gross_pnl" in trade and "charges" in trade:
        return (
            float(trade["gross_pnl"]),
            float(trade["charges"]),
            float(trade["net_pnl"]),
        )

    entry_credit = 0.0
    entry_charges = 0.0
    for f in trade.get("entry_fills", []):
        amt = float(f["price"]) * int(f["quantity"])
        entry_credit += amt if f.get("side") == "SELL" else -amt
        entry_charges += float(f.get("charges", 0.0))
    exit_debit = 0.0
    exit_charges = 0.0
    for f in trade.get("exit_fills", []):
        amt = float(f["price"]) * int(f["quantity"])
        exit_debit += amt if f.get("side") == "BUY" else -amt
        exit_charges += float(f.get("charges", 0.0))
    gross = entry_credit - exit_debit
    charges = entry_charges + exit_charges
    return gross, charges, gross - charges


def compute_session_row(
    trades: list[dict],
    opening_balance: float,
    margin_model: MarginModel,
    session_date: date,
    broker_snapshot: dict | None = None,
) -> dict:
    total_gross = 0.0
    total_charges = 0.0
    total_net = 0.0
    per_symbol: dict[str, dict] = {}

    for trade in trades:
        gross, charges, net = _trade_pnl(trade)
        total_gross += gross
        total_charges += charges
        total_net += net

        m = margin_model.trade_margin(trade, broker_snapshot=broker_snapshot)
        sym = m["symbol"]
        agg = per_symbol.setdefault(
            sym,
            {"net_pnl": 0.0, "blocked_margin": 0.0, "long_premium_paid": 0.0, "capital_committed": 0.0},
        )
        agg["net_pnl"] += net
        agg["blocked_margin"] += m["blocked_margin"]
        agg["long_premium_paid"] += m["long_premium_paid"]
        agg["capital_committed"] += m["capital_committed"]

    for agg in per_symbol.values():
        for k in agg:
            agg[k] = round(agg[k], 2)

    closing_balance = opening_balance + total_net

    # peak_margin_used assumes all of the day's trades are held concurrently intraday
    # (Wing-6 enters all symbols ~09:20 and exits ~15:20 same day), so the calibrated
    # peak is the sum of capital committed across trades.
    peak_margin_used = sum(s["capital_committed"] for s in per_symbol.values())
    margin_source = "calibrated_formula"

    broker_net = broker_margin_used = broker_fo_unrealized = broker_fo_realized = None
    if broker_snapshot:
        broker_net = broker_snapshot.get("broker_net")
        broker_margin_used = broker_snapshot.get("broker_margin_used")
        broker_fo_unrealized = broker_snapshot.get("broker_fo_unrealized")
        broker_fo_realized = broker_snapshot.get("broker_fo_realized")
        if broker_margin_used is not None:
            peak_margin_used = float(broker_margin_used)
            margin_source = "broker_snapshot"

    peak_buying_power_pct = (peak_margin_used / opening_balance * 100.0) if opening_balance else 0.0
    margin_breach = peak_margin_used > opening_balance

    row = {
        "session_date": session_date.isoformat(),
        "trades": len(trades),
        "gross_pnl": round(total_gross, 2),
        "charges": round(total_charges, 2),
        "net_pnl": round(total_net, 2),
        "opening_balance": round(opening_balance, 2),
        "closing_balance": round(closing_balance, 2),
        "peak_margin_used": round(peak_margin_used, 2),
        "peak_buying_power_pct": round(peak_buying_power_pct, 2),
        "margin_breach": margin_breach,
        "margin_source": margin_source,
        "per_symbol": per_symbol,
    }
    if broker_snapshot:
        row["broker_net"] = broker_net
        row["broker_margin_used"] = broker_margin_used
        row["broker_fo_unrealized"] = broker_fo_unrealized
        row["broker_fo_realized"] = broker_fo_realized
    return row


def _atomic_write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _atomic_write_jsonl(path: Path, rows: list[dict]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(json.dumps(r, default=str) for r in rows) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def update_account_ledger(
    live_root: Path,
    session_date: date,
    margin_model: MarginModel | None = None,
) -> dict:
    live_root = Path(live_root)
    account_dir = live_root / "account"
    account_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = account_dir / "account_ledger.jsonl"

    if margin_model is None:
        margin_model = MarginModel.from_config(
            Path(__file__).resolve().parents[1] / "configs" / "live" / "margin_model.json"
        )

    date_str = session_date.strftime("%Y%m%d")

    snapshot_path = account_dir / f"kotak_limits_{date_str}.json"
    broker_snapshot = None
    if snapshot_path.exists():
        broker_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

    rows: list[dict] = []
    if ledger_path.exists():
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    iso_date = session_date.isoformat()
    insert_at = len(rows)
    for i, r in enumerate(rows):
        if r.get("session_date") == iso_date:
            insert_at = i
            break

    # Opening balance comes from the row immediately before this session_date.
    if insert_at > 0:
        opening_balance = float(rows[insert_at - 1]["closing_balance"])
    else:
        opening_balance = margin_model.starting_capital

    trades_path = live_root / "paper_trades" / f"{date_str}.json"
    trades: list[dict] = []
    if trades_path.exists():
        raw = json.loads(trades_path.read_text(encoding="utf-8"))
        if raw:
            trades = raw

    row = compute_session_row(
        trades=trades,
        opening_balance=opening_balance,
        margin_model=margin_model,
        session_date=session_date,
        broker_snapshot=broker_snapshot,
    )

    if insert_at < len(rows):
        rows[insert_at] = row  # idempotent in-place replace
    else:
        rows.append(row)

    # Re-walk the whole opening==prior-closing chain so a single past-day net_pnl
    # edit cannot desync downstream rows (corrected trades file / late snapshot).
    prev_closing = margin_model.starting_capital
    for r in rows:
        opening = prev_closing
        closing = round(opening + float(r["net_pnl"]), 2)
        r["opening_balance"] = round(opening, 2)
        r["closing_balance"] = closing
        prev_closing = closing

    # Recompute running peak balance + drawdown across the whole ledger.
    peak_balance = margin_model.starting_capital
    for r in rows:
        closing = float(r["closing_balance"])
        peak_balance = max(peak_balance, closing)
        r["peak_balance"] = round(peak_balance, 2)
        r["drawdown_pct"] = round((closing - peak_balance) / peak_balance * 100.0, 2)

    _atomic_write_jsonl(ledger_path, rows)

    latest = row  # the row for THIS session_date, after the full chain re-walk
    all_time_net_pnl = sum(float(r["net_pnl"]) for r in rows)
    current_balance = float(rows[-1]["closing_balance"])
    max_drawdown_pct = min((float(r["drawdown_pct"]) for r in rows), default=0.0)
    total_trades = sum(int(r["trades"]) for r in rows)

    state = {
        "latest_row": latest,
        "all_time_net_pnl": round(all_time_net_pnl, 2),
        "current_balance": round(current_balance, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "total_sessions": len(rows),
        "total_trades": total_trades,
        "margin_source": latest.get("margin_source"),
    }
    _atomic_write_json(account_dir / "latest_account_state.json", state)

    logger.info(
        "account ledger updated session=%s trades=%d net=%.2f closing=%.2f dd=%.2f%% source=%s",
        iso_date,
        latest["trades"],
        latest["net_pnl"],
        latest["closing_balance"],
        latest["drawdown_pct"],
        latest.get("margin_source"),
    )
    return latest

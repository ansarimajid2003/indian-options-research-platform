# Account-State & Margin Tracker — Design & Deployment Reference

**Version:** 2026-06-02
**Status:** Deployed to zimaos, live in dashboard. Margin model uncalibrated (estimates).
**Author context:** Built in response to the live paper session producing a ~₹2,700 loss with no persistent account/equity mechanic to track cumulative trading PnL.

---

## 1. Why This Exists

Before this, the live paper stack had **no persistent account state**. Every EOD artifact
(`{date}_eod_summary.json`, `{date}_paper_summary.json`, `{date}_paper_ledger.csv`) reset to a
hardcoded `initial_capital` of ₹1,000,000, and the dashboard equity curve was **intraday-only**
(reset to zero each morning). A daily loss lived in exactly one file and was never carried into a
running balance — there was no object that knew "we started with X, we are now at X − loss."

This made it impossible to:
- Track realized drawdown from peak (only per-day reset existed).
- Compare live equity to the backtested 2.746-Sharpe curve.
- Know how much of the ₹10,00,000 account is consumed by margin across the 4 indices.
- Trigger the roadmap rule (pause variant if OOS Sharpe < 1.5 for two consecutive months).

The tracker is a faithful **observer** of what the engine traded, with an honest margin /
buying-power overlay, intended as **evidence for real-money deployment**.

---

## 2. Design Decisions (and Why)

| Decision | Choice | Rationale |
|---|---|---|
| Starting capital | **₹10,00,000** | Matches the backtest's 1,000,000 denominator so live and backtest equity curves are directly comparable. |
| Lot scaling | **1x only** (as live profile trades) | Account reflects exactly what is traded today (N:F:M:S = 1:1:1:1). |
| Margin source | **Config-driven formula + calibration table** | Exact SPAN numbers can't be auto-fetched (Kotak/NSE calculators are JS-only). Formula works today; calibrate later. |
| Margin breach handling | **Record + flag, never block** | Sim still takes all trades (matches engine behavior); flags days where required margin exceeded capital. Keeps evidence complete. |
| Persistence | **Append-only JSONL, idempotent on date** | Crash-safe, one row per session, re-runnable without double-counting. |
| Kotak SDK integration | **Standalone, lazy-imported, opt-in** | Engine path stays offline/deterministic; broker calls live only in a separate script. |

---

## 3. Margin Model — How It Mimics a Real Kotak Neo F&O Account

The Wing-6 Iron Condor is a **defined-risk** structure. Indian exchanges margin on a portfolio
(SPAN) basis, so the long wings hedge the short legs and the blocked margin is far below the
naked-short sum (~70% benefit per Zerodha Varsity's worked example: short strangle ₹1,45,090 →
Iron Condor ₹44,303).

### Per-trade computation (`MarginModel.trade_margin`)

```
quantity         = lots × lot_size
wing_width       = max(long_call − short_call, short_put − long_put)
defined_max_loss = max(0, wing_width × quantity − entry_credit)     # entry_credit is net rupees
long_premium_paid= Σ (price × quantity) over BUY entry legs          # full debit, no margin
expiry_day_elm   = spot_proxy × expiry_elm_pct × quantity  if dte ≤ expiry_elm_dte else 0
short_leg_margin = max(defined_max_loss, hedged_floor_per_lot × lots) + expiry_day_elm
blocked_margin   = short_leg_margin
capital_committed= blocked_margin + long_premium_paid
```

Key real-account facts baked in:
- **Long legs = full premium debit, no margin** (SEBI 100% upfront option-buy premium).
- **Nov-2024 hedged-margin floor**: SEBI raised minimum margin on hedged positions ~80%, so margin
  is NOT simply `max_loss × lot`. Captured via `hedged_floor_per_lot` (the `max(...)` term).
- **Expiry-day +2% ELM** on short legs (from Nov 20 2024) — applies to SENSEX (DTE ≤ 2) and any
  expiry-day trade. `expiry_elm_pct=0.02`, per-index `expiry_elm_dte`.
- **Intraday = no leverage** (SEBI peak-margin 2021): MIS and NRML block the same margin, so the
  model is product-agnostic.

### Session aggregation (`compute_session_row`)

All symbols enter ~09:20 and exit ~15:20 **concurrently**, so:
```
peak_margin_used      = Σ capital_committed across all symbols traded that day
peak_buying_power_pct = peak_margin_used / opening_balance × 100
margin_breach         = peak_margin_used > opening_balance
```

### Two-tier accuracy

1. **Tier 1 — `broker_snapshot`** (most accurate): if `account/kotak_limits_{date}.json` exists
   (captured by `snapshot_kotak_limits.py` via the Kotak `limits()` API), the session uses the
   **broker's real `MarginUsed`/`Net`** — the demat account's own number, hedge benefit included.
2. **Tier 2 — `calibrated_formula`** (fallback): the formula above with the calibration table.
   This is what runs today (no broker calls yet).

### Reference figures (current model, 1-lot, 2026 lots, uncalibrated estimates)

| Index | Lot | Wing | Defined max loss | Long prem | Expiry ELM | Blocked | Committed |
|---|---:|---:|---:|---:|---:|---:|---:|
| NIFTY | 65 | 300 | 15,000 | 1,800 | 0 | 45,000 | 46,800 |
| FINNIFTY | 60 | 300 | 13,800 | 1,700 | 0 | 40,000 | 41,700 |
| MIDCPNIFTY | 120 | 150 | 14,400 | 1,500 | 0 | 40,000 | 41,500 |
| SENSEX | 20 | 600 | 7,000 | 2,000 | 32,000 | 82,000 | 84,000 |
| **Total (1x, concurrent)** | | | | | | | **~214,000** |

→ ~21% buying-power utilization on a ₹10,00,000 account at 1x. **Note:** SENSEX's expiry-day ELM
dominates its margin (DTE ≤ 2 trades). These are estimates — see §7 calibration.

---

## 4. Components Built

| File | Role | Network? |
|---|---|---|
| `options_backtest/account_state.py` | Margin model + idempotent ledger update | No (offline, deterministic) |
| `configs/live/margin_model.json` | Per-index calibration table (₹10L start) | — |
| `scripts/live/backfill_account_ledger.py` | Replay `paper_trades/*.json` to seed ledger | No |
| `scripts/live/snapshot_kotak_limits.py` | Capture real broker balance/margin (Kotak SDK) | Yes (standalone, lazy import) |
| `scripts/live/run_paper_trading.py` | EOD hook (fail-safe) calling `update_account_ledger` | — |
| `options_backtest/dashboard_bridge.py` | `get_account_state()`, `get_account_ledger()` (TTL-cached) | No |
| `scripts/live/api/{models,routes/live}.py` | `GET /api/live/account`, `/account-ledger` | No |
| `dashboard/{panels,app}.jsx` | `AccountStatePanel` (read-only) | — |
| `tests/test_account_state.py`, `test_kotak_snapshot.py`, `test_account_dashboard.py` | 23 unit tests | — |
| `third_party/Kotak-neo-api-v2/` | Vendored Kotak Neo SDK (laptop-only, not in git tree) | — |

### Data artifacts (durable, on WD storage)

```
/media/WD-Storage/indian-markets-live/account/
├── account_ledger.jsonl          # one row per session, append-only, idempotent on date
├── latest_account_state.json     # latest row + all-time stats (balance, dd, sessions, trades)
└── kotak_limits_{YYYYMMDD}.json   # OPTIONAL Tier-1 broker snapshot (not yet captured)
```

Ledger row schema: `session_date, trades, gross_pnl, charges, net_pnl, opening_balance,
closing_balance, peak_balance, drawdown_pct, peak_margin_used, peak_buying_power_pct,
margin_breach, margin_source, per_symbol{symbol → {net_pnl, blocked_margin, long_premium_paid,
capital_committed}}`.

---

## 5. How It Runs

- **Daily**: the EOD hook in `run_paper_trading.py` calls `update_account_ledger(live_root, today)`
  after the engine completes and reports are written. It is wrapped in its own try/except with a
  15s timeout and **never breaks engine shutdown**. Runs even on 0-trade days (flat row).
- **Idempotent**: re-running a date replaces that row in place and re-walks the entire
  opening==prior-closing chain, so a corrected past-day file propagates downstream without
  desyncing the equity chain.
- **Dashboard**: `dashboard-api` serves `/api/live/account` (+ `/account-ledger`); the
  `AccountStatePanel` polls on mount and every 30s, rendering balance, all-time PnL, max drawdown,
  buying-power utilization, and a margin-breach badge.

---

## 6. Deployment Record (2026-06-02)

1. Committed on branch `feat/account-state-tracker`, pushed to `zimaos:/DATA/live-paper/indian-markets.git`.
2. Fast-forward merged into `main` on the zimaos working checkout (HEAD `09632de`).
3. Ran 23 unit tests on zimaos venv — **all pass**.
4. Backfilled the ledger from **14 sessions** of `paper_trades` history.
5. **Incident during deploy**: an orphaned uvicorn (PID 1926, not under the systemd cgroup) held
   port 8000 and kept serving stale code — every `systemctl restart` failed to bind
   (`Errno 98 address already in use`) and silently fell back to the orphan. **Fix**: killed PID
   1926, restarted `dashboard-api` cleanly. Routes then returned 200. *Lesson: after a restart,
   verify the MainPID changed and the new route responds — `is-active` alone is insufficient.*
6. Verified `/api/live/account` + `/account-ledger` return correct data; dashboard serves the new
   `panels.jsx`/`app.jsx`.

### Backfilled history (as deployed)

| Metric | Value |
|---|---|
| Sessions | 14 (2026-05-11 → 2026-06-01) |
| Trades | 10 |
| Current balance | **₹9,66,032.76** |
| All-time net PnL | **−₹33,967.24** |
| Max drawdown | **−3.40%** |
| Worst day | 2026-05-20: 4 trades, **−₹29,692** (drove the entire drawdown) |
| Yesterday (2026-06-01) | 1 NIFTY trade, −₹2,717.81, peak BP util 4.81% |

> The 2026-05-20 session (−₹29,692 across 4 trades) accounts for almost the entire all-time loss
> and the full −3.4% drawdown. Worth a dedicated look — see the session audit for that date.

---

## 7. Deferred / Outstanding (Important)

These must be addressed before the equity record is trustworthy as **real-money deployment evidence**:

1. **Margin calibration (HIGH).** `hedged_floor_per_lot` values (₹40k–50k) are 2026 research
   *estimates*, not exact. Pull the true 1-lot Iron Condor margin from the Kotak Neo SPAN
   calculator (or run `snapshot_kotak_limits.py` once authenticated) for each index and replace the
   table. Until then, margin/buying-power figures are directional only.
2. **SENSEX expiry-day ELM dominance (MEDIUM).** SENSEX margin (~₹82k) is driven by the +2% ELM at
   DTE ≤ 2, not its defined max loss (~₹7k). Realistic, but confirm against the real calculator —
   it makes SENSEX the single largest margin consumer.
3. **Kotak SDK not deployed to zimaos (LOW for paper).** `third_party/Kotak-neo-api-v2/` is
   laptop-only (nested `.git`, not in the committed tree); `neo_api_client` is not pip-installed in
   the zimaos venv. Not needed for paper — `snapshot_kotak_limits.py` lazy-imports it, and the
   account tracker / dashboard work without it. Required only when capturing real broker snapshots.
4. **No live broker snapshot yet (Tier 1 unused).** Every row is `calibrated_formula`. Once Kotak
   auth is set up, scheduling a daily `snapshot_kotak_limits.py` would upgrade accuracy to the
   broker's own `MarginUsed`/`Net`.
5. **Kotak auth not configured.** `snapshot_kotak_limits.py` documents the required env vars
   (`KOTAK_ACCESS_TOKEN`, `KOTAK_CONSUMER_KEY`, `KOTAK_MOBILE`, `KOTAK_UCC`, `KOTAK_TOTP`,
   `KOTAK_MPIN`, `KOTAK_ENV`); none are set. It exits cleanly with a message if creds are absent.
6. **Branch not yet merged to laptop `main`.** Work lives on `feat/account-state-tracker` on both
   the laptop and zimaos (zimaos fast-forwarded its `main`). The laptop `main` still needs the
   branch merged (left to the user — no unprompted git mutations).
7. **`margin_required()` is single-leg only.** The Kotak SDK's per-order margin endpoint does not
   support baskets, so it CANNOT compute the IC hedge benefit (summing legs = ~3x naked overstate).
   Use `limits().MarginUsed` (real positions) for accuracy, `margin_required()` only as a naked
   ceiling diagnostic. Documented in `snapshot_kotak_limits.py::naked_leg_margin`.

---

## 8. Operational Commands

```bash
# Re-backfill the ledger from history (idempotent; --no-rebuild to fold incrementally)
ssh zimaos "cd /DATA/live-paper/indian-markets && .venv/bin/python scripts/live/backfill_account_ledger.py --live-root data/live"

# View account state
ssh zimaos "curl -s http://127.0.0.1:8000/api/live/account | python3 -m json.tool"

# Dashboard (panel renders under the equity curve)
ssh -L 9000:127.0.0.1:8000 zimaos   # then open http://localhost:9000/

# After ANY dashboard-api restart, VERIFY the new PID + route (orphan-uvicorn lesson):
ssh zimaos "systemctl restart dashboard-api && sleep 4 && systemctl show -p MainPID --value dashboard-api && curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/live/account"

# Once Kotak auth exists — capture a real broker snapshot for Tier-1 accuracy
ssh zimaos "cd /DATA/live-paper/indian-markets && .venv/bin/python scripts/live/snapshot_kotak_limits.py"
```

---

## 9. Key Files Reference

| Purpose | Path |
|---|---|
| Margin model + ledger | `options_backtest/account_state.py` |
| Calibration table | `configs/live/margin_model.json` |
| Backfill | `scripts/live/backfill_account_ledger.py` |
| Broker snapshot (opt-in) | `scripts/live/snapshot_kotak_limits.py` |
| EOD hook | `scripts/live/run_paper_trading.py` (engine-complete branch) |
| Dashboard bridge | `options_backtest/dashboard_bridge.py` (`get_account_state`, `get_account_ledger`) |
| API routes | `scripts/live/api/routes/live.py` (`/account`, `/account-ledger`) |
| Dashboard panel | `dashboard/panels.jsx` (`AccountStatePanel`) |
| Vendored Kotak SDK | `third_party/Kotak-neo-api-v2/` (laptop-only) |
| Live deployment master | `docs/design/wing6_live_deployment_reference.md` |

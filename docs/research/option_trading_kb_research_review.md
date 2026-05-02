# Option Trading Research — NIFTY 50

Last updated: 2026-05-01 (v3 — data inventory mapped, vendor comparison added, gaps restated against actual holdings)

---

## Executive View

The 3 PM setup has real academic backing as an intraday reversion trade. The reason all three backtest variants lose is not a bad signal — it is a bad expression of a real signal:

1. The edge is a 1–2 hour intraday reversion, not an all-day hold. We are holding too long.
2. Buying a naked ATM option against a short-duration edge means theta is the dominant PnL driver, not direction. Spreads fix this.
3. The backtest has three correctness bugs that inflate trade counts and contaminate exits.
4. The 2026 STT hike means every historical cost model is now understated by ~30–50%.

The right framing is not "find a better candle filter." It is: fix the backtest → change the exit window → change the structure → add a VIX regime gate. Only then build a separate short-vol track.

---

## Academic Foundation

### Primary: End-of-Day Reversal (validates our signal)

**Baltussen, Da, Soebhag — "End-of-Day Reversal" (April 2025, Erasmus/Notre Dame)**

This paper is the closest academic parallel to our exact setup. Key findings:

- Individual stocks that trend up from open to 3:00 PM (they call this ROD3 = overnight + first 30 min + midday) have significantly negative returns in the final 30 minutes of the day.
- Long-short portfolios based on this reversal yield **3.78–6.86 bps/day** (9.5–17.3% annualised) — economically large and statistically significant across every 3-year rolling window from 1993 to 2019.
- The effect is **asymmetric**: almost entirely driven by intraday winners reversing. Losers barely contribute. The mechanism is retail "buy-the-dip" at the close, combined with short-sellers covering overnight risk on down-day names.
- **The reversal itself partially reverts the next morning** — the gap up (our signal) is a transitory price pressure, not an information signal. It reverts intraday, typically in the first 1–2 hours.

**What this means for our 3 PM setup:**
- The signal is right. The spot data confirms it: 73.4% touch rate of prior 3 PM close when gapped up, 27.4% next-day up rate on those touches, avg O-C of –0.30%.
- The window is wrong. Holding an option through end-of-day means we are holding through the point where the reversion has already completed. The academic evidence says the trade resolves in the first hour to two hours of the next session.
- The structure is wrong. A naked ATM put decays every minute we hold it. If the edge completes by 10:30–11:00 AM, we are paying theta for 4+ hours of dead time afterward.

---

**Lou, Polk, Skouras — "A Tug of War: Overnight versus Intraday Expected Returns" (LSE/JFE 2019)**

- Momentum strategy profits are earned **either overnight or intraday — never both periods simultaneously**.
- An overnight-based signal (our gap-up from the 3 PM close) should be **faded intraday**, not held into the next close again.
- This is a structural confirmation that our strategy belongs in the open-to-noon window, not open-to-close.

---

### VRP and Position Sizing Papers (KB)

**`2508.16598v1` — "Sizing the Risk: Kelly, VIX, and Hybrid Approaches in Put-Writing on Index Options" (2025)**

- Short-dated, far OTM put-writing on index options harvests volatility risk premium consistently, but only with regime-aware sizing.
- A **hybrid method** (Kelly criterion scaled by VIX regime) beats both pure Kelly and pure VIX-scaling alone. In-sample 2018–2023, out-of-sample 2024.
- Ultra-short DTE (0–5 days) + far OTM strikes = best risk-adjusted returns. Less theta drag on entry, faster decay harvested on winning positions.
- **2024 finding**: during low-volatility regimes, naive Kelly over-sizes, then gets hit hard on sudden spikes. The hybrid method was most critical precisely in calm markets.
- Directly actionable: the short-vol track (iron condor / short strangle) must use this sizing approach, not fixed lots.

**`2407.21791v1` — "Deep Learning for Options Trading: An End-To-End Approach"**

- Turnover regularization is non-negotiable — high-churn option strategies are destroyed by costs regardless of model quality.
- Any ML layer added later must penalise turnover explicitly and include IV/skew/DTE/moneyness/volume as features — not just spot direction.
- **Relevance:** even without ML, this confirms that the structural cost problem (not the signal) is killing our backtests. Reducing trade frequency is as important as improving signal quality.

**`1810.12200v1` — "Option Market (In)Efficiency and Implied Volatility Dynamics After Return Jumps"**

- IV adjusts slowly after large moves, especially for ATM options and OTM puts, but this drift is small enough that actual bid/ask spreads often remove the theoretical edge.
- Post-jump IV drift is a feature candidate, not a standalone strategy.

**`1407.5528v1` — "Arbitrage-Free Prediction of the Implied Volatility Smile"**

- Predicting the full option surface (not just spot direction) opens structures like strangles and risk reversals that are impossible to design from spot alone.
- Building a daily NIFTY surface snapshot (moneyness × DTE × call/put IV) is the prerequisite for any surface-based strategy.

---

### Execution and Cost Papers (KB)

**`2103.15302v1` — "Analytic Formula for Option Margin with Liquidity Costs under Dynamic Delta Hedging"**

- Transaction and liquidity costs materially change option hedging PnL.
- Liquidity and fill assumptions must be part of expected edge, not an afterthought. A strategy that looks profitable at mid-price can be a loser at actual fills.

**`1902.05418v4` — "Market Impact: A Systematic Study of the High Frequency Options Market"**

- Option trades impact the volatility surface, not just one contract.
- When sizing, nearby-strike liquidity matters as much as ATM liquidity.

**`2207.02989v1` — "Unbiasing and Robustifying Implied Volatility Calibration with Large Bid-Ask Spreads and Missing Quotes"**

- Mid-price calibration is biased when spreads are wide or quotes are missing.
- Shoonya 1-min OHLC without bid/ask is not trustworthy for live option fill estimates. Our fills are systematically optimistic.

---

### India Market Papers (KB)

**`2207.13123` — "Indian Derivatives Market Evolution and Challenge"**

- NSE index options dominate Indian derivative activity by contract count globally.
- NIFTY options are the right focus, but this paper is market-structure context, not a strategy.

**`2009.14113v1` — "On the Pricing of Currency Options under Variance Gamma Process"**

- For USD-INR options, variance gamma beats Black-Scholes on skewness and kurtosis.
- Indian options need fat-tail and skew-aware pricing. This matters for surface modeling (strategy D below) but not for the directional track.

---

## What Our Backtest Is Getting Wrong — Full Diagnosis

### Bug 1: Expiry Multiplication (Critical)

The daily runner loops through expiry folders and fires the spot signal inside each one. One signal date generates 7–9 duplicate trades across expiries.

- `2024-10-10 15:16:00` appears 7× in `three_pm_v2_call_level_stop.csv`
- `2024-11-04 15:16:00` appears 9× in `three_pm_v2_put.csv`

**Fix:** Build a canonical spot calendar once. For each signal date, select exactly one expiry using an explicit rule: nearest weekly expiry with DTE ≥ 1; step to the next weekly if entry is same-day as expiry.

### Bug 2: Calendar-Day Exit (Critical)

`next_date = trade_date + timedelta(days=1)` — lands on weekends and holidays, silently falling back to a same-day late timestamp labeled as `next_day_open`.

**Fix:** Look up the next available trading date in the spot calendar.

### Bug 3: Long Option Stop Gate (Major)

Stop/target logic is gated on `entry_credit > 0`. Debit strategies (long put, put debit spread) skip all stop/target behavior and ride to EOD.

**Fix:** Gate debit strategies on `entry_credit <= 0`. Add: max premium loss stop (e.g. 50% of premium paid), exit on spot invalidation (spot closes above prior 3 PM level), exit on time-based theta decay threshold.

### Economic Problem 1: Wrong Exit Window (Biggest PnL Killer)

The academic evidence (Baltussen 2025, Lou 2019) says the reversion completes in the first 1–2 hours of the next session. We are holding to EOD — 4–5 hours past the natural exit, paying theta the entire time.

**Fix:** Test exits at 10:00 AM, 11:00 AM, and at touch of prior 3 PM close (our 73.4% touch rate makes this a natural take-profit trigger). EOD should be the fallback, not the primary exit.

### Economic Problem 2: Naked ATM Options Against a Short-Duration Edge

Buying an ATM option pays theta for every minute held. If the edge resolves in 90 minutes, we need a structure where theta cost over 90 minutes is small relative to the directional payoff.

**Fix:** Put debit spread (buy ATM put, sell OTM put). Cuts theta by ~40–50%, cuts total premium at risk, cuts STT, preserves the directional payoff for the expected short move.

### Economic Problem 3: 2026 STT Hike — All Historical Results Are Understated

**Budget 2026, effective April 1, 2026:**

| Transaction | Old STT | New STT | Change |
|---|---|---|---|
| Options sell (on premium) | 0.10% | **0.15%** | +50% |
| Options exercise (ITM, on intrinsic value) | 0.125% | **0.15%** | +20% |
| Futures sell (on contract value) | 0.02% | **0.05%** | +150% |

Full cost stack per NIFTY option round-trip (1 lot, ₹100 premium, lot size 75):
- STT on sell: ~₹11.25
- Exchange fee + SEBI: ~₹5
- Brokerage (flat): ~₹40
- GST 18% on brokerage: ~₹7
- **Total: ~₹63–80 per round-trip**
- **Break-even: ~₹0.85–1.07 per unit move in premium**

The three saved ledgers already showed charges eating 65–95% of gross losses at pre-2026 rates. The new STT makes every result worse. **No backtest run before April 2026 is valid for live deployment cost modeling.**

---

## Existing Backtest Results (Pre-Bug-Fix, Pre-2026 STT)

These results are known-contaminated by the expiry multiplication bug and old STT rates. They are kept for reference, not for strategy decisions.

| Ledger | Trades | Gross PnL | Charges | Net PnL | Win % |
|---|---|---|---|---|---|
| `three_pm_baseline.csv` | 2,348 | –88,000 | 170,648 | –258,648 | 41.6% |
| `three_pm_v2_put.csv` | 629 | –63,348 | 43,164 | –106,511 | 42.3% |
| `three_pm_v2_call_level_stop.csv` | 612 | –52,468 | 46,934 | –99,401 | 43.0% |

After the expiry multiplication bug is fixed, trade counts will drop dramatically (likely 7–9× fewer trades). The gross PnL picture may improve significantly. Do not draw conclusions from these numbers until clean runs exist.

---

## Strategies — Ranked by Evidence and Build Order

### Track 1 (Primary): 3 PM Intraday Reversion — Fixed and Re-expressed

**Academic backing:** Baltussen 2025 (direct), Lou 2019 (structural support), our own 462-observation spot dataset.

The signal is real. The losses are structural. The fix sequence is:

**Step 1 — Exit window.** Test three exits in parallel:
- Touch of prior 3 PM close (natural take-profit; 73.4% hit rate gives high expected value)
- 11:00 AM time stop (intraday reversion typically complete per Baltussen)
- EOD fallback for unresolved trades

**Step 2 — Structure.** Replace naked ATM long put with put debit spread:
- Buy ATM put (same as before)
- Sell OTM put 1–2 strikes below (e.g. 100 points below ATM on NIFTY)
- Net theta cost cut by ~40–50%; max loss capped; STT lower because sell-leg premium is smaller

**Step 3 — VIX regime gate.** Only enter when India VIX is between 13 and 25:
- VIX < 13: premium too thin for spreads to justify cost
- VIX 13–25: normal range, target zone
- VIX 25–35: reduce size by 50%, widen spread, close at 50% profit
- VIX > 35: skip entirely; reversion logic breaks in panic regimes

**Step 4 — 2026 STT.** Update broker_sim.py to 0.15% on sell premium before any new runs.

---

### Track 2 (Parallel, Lower Priority): NIFTY Volatility Risk Premium — Short Defined-Risk

**Academic backing:** `2508.16598` (direct), VRP literature broadly.

Build a short-vol strategy that harvests the persistent gap between implied and realised volatility on NIFTY weekly options. Always defined-risk.

**Preferred structures:**
- Short OTM put spread (primary — collects downside VRP)
- Short OTM call spread (secondary — when call skew is elevated)
- Iron condor (combine both when surface is symmetric)
- Broken-wing condor when put skew is elevated (sell more downside premium)

**Entry filters (in order of importance):**
1. India VIX in 13–25 range (mandatory)
2. IV implied move > 1.2× recent 5-day realised move (VRP exists)
3. No scheduled event in next 5 trading days (earnings, RBI, budget, expiry week)
4. DTE: target 3–7 days for weekly options (best theta/vega ratio per `2508.16598`)

**Sizing: hybrid Kelly × VIX scalar (per `2508.16598`):**
- Compute Kelly fraction from rolling win/loss/avg estimates
- Scale down by `min(1.0, 20/VIX)` — halve size when VIX is 40, full size at VIX 20
- Hard cap at 2% of capital max loss per trade
- Hard stop at 2× credit received (close the position, do not ride)

**Why this is a separate track:** It requires building a surface snapshot pipeline (IV per strike per expiry per day), which is a significant data engineering task. Do not mix this into the directional track work.

---

### Track 3 (Future): IV Surface Dislocation

**Academic backing:** `1407.5528v1` (surface prediction), `1810.12200v1` (post-jump IV drift).

Build daily snapshots of NIFTY option surface per (moneyness, DTE): call IV, put IV, skew, term structure, butterfly curvature. Search for:
- Overpriced wings (sell condor)
- Cheap convexity (buy straddle before catalysts)
- Skew mean reversion after jumps (risk reversals)
- Term-structure anomalies around expiry week

**Prerequisite:** bid/ask data or a conservative spread proxy. Without this, surface dislocations look real but disappear at actual fills (per `2207.02989v1` and `1902.05418v4`). Do not build this track until bid/ask data is available.

---

### ~~Post-Jump IV Drift as Standalone Strategy~~

**Removed.** The KB paper (`1810.12200v1`) itself says simple arbitrage typically disappears after actual spreads. Without bid/ask data this cannot be implemented honestly. It remains a feature candidate for Track 3 surface work, not a standalone strategy.

---

### ~~Cost-Regularized ML Strategy~~

**Removed from roadmap for now.** The academic case (`2407.21791v1`) is sound, but we need 3+ years of clean, bug-free, walk-forward OOS data before adding a model layer. Adding ML to a broken backtest produces confident garbage. Revisit after Track 1 has a validated OOS equity curve.

---

## Data Inventory — What We Actually Have

*Audited 2026-05-01 from `data/manifests/data_inventory_manifest.csv` (502 datasets, 41,788 files, ~30 GB).*
*Dhan options validated 2026-05-01 via `scripts/data/validate_dhan_options.py` — audit at `data/audit/dhan_options_audit.csv`.*

### Confirmed Holdings

| Role | Dataset | Path | Coverage | Quality Status |
|---|---|---|---|---|
| **NIFTY 50 spot 1-min (canonical)** | `processed_spot_nifty50_1min_canonical` | `data/processed/spot/nifty50_1min_CANONICAL.csv` | 2015–2026-04 | `research_ready_cleaned` |
| **NIFTY 50 spot 15-min** | `processed_market_archive_cleaned_nifty50_15min` | `data/processed/market_archive_cleaned/NIFTY 50_15minute.csv` | 2015-01-09–2026-04-08 | `cleaned_non_destructive` |
| **India VIX 1-min** | `processed_market_archive_cleaned_indiavix_1min` | `data/processed/market_archive_cleaned/INDIA VIX_minute.csv` | 2015-01-09–2026-04-08 | `research_ready_cleaned` |
| **India VIX daily** | `processed_market_archive_cleaned_indiavix_daily` | `data/processed/market_archive_cleaned/INDIA VIX_day.csv` | 2015-01-01–2026-04-08 | `research_ready_cleaned` |
| **NIFTY options 1-min (Shoonya, strike-level)** | `raw_shoonya_nifty_options_1min` | `data/raw/options/shoonya/nifty/` | 2024-01-04–2026-04-21 | `cleaned_non_destructive` (see audit) |
| **NIFTY options 1-min (Dhan weekly, rolling ATM±10)** | `raw_dhan_nifty_week_expiry_code_1_*` | `data/raw/options/dhan/nifty/week/expiry_code_1/` | 2021-01-01–2026-04-30 | `validated_cleaned` — 41.4M bars, OHLC PASS, see audit |
| **NIFTY options 1-min (Dhan monthly, rolling ATM±10)** | `raw_dhan_nifty_month_expiry_code_1_*` | `data/raw/options/dhan/nifty/month/expiry_code_1/` | 2021-05-01–2026-04-30 | `validated_cleaned` — included in audit above |
| **Dhan instruments master** | `raw_dhan_instruments_master` | `data/raw/reference/dhan_instruments_master.csv` | Point-in-time | `raw_vendor_reference` |
| **NSE F&O Bhavcopy EOD (NIFTY options, unified)** | `nse_bhavcopy_fo_nifty_options_eod_YYYY` | `data/processed/nse/bhavcopy/fo/nifty_options_eod_{year}.parquet` | 2008-01-01–2026-04 | `validated_cleaned` — 8.07M rows, OHLC PASS, 0 parse errors, 0 duplicates; see `data/audit/nse_bhavcopy_audit.csv` |

**Total bhavcopy source files: 4,512** (2008–2026, 246 trading days/year avg). Two raw schemas merged: legacy (2008–2024-07-05, `OPTION_TYP` col) and UDiFF (2024-07-08+, `OptnTp` col); one early file (2008-02-05) uses `OPTIONTYPE` variant — handled. 8.07M NIFTY option rows total; 1.8M active (OPEN>0), 6.3M flagged `no_trade=True` (NSE untouched-strike convention, OPEN=HIGH=LOW=0). No bid/ask in raw source.

**BANKNIFTY / FINNIFTY / MIDCPNIFTY** Dhan rolling options (weekly, ATM±10) also present for 2021-05-01–2026-04-30 — not needed for NIFTY 50 focus but available for regime cross-checks.

---

## Vendor Comparison — Overlapping NIFTY Option Data

Both Shoonya and Dhan cover NIFTY options intraday. Their overlap period is **2024-01-04 to 2026-04-21** for NIFTY weeklies.

| Dimension | Shoonya (primary backtest source) | Dhan rolling option API |
|---|---|---|
| **Format** | CSV per strike per expiry folder | JSON per rolling-ATM bucket (ATM±10 strikes) |
| **Strike resolution** | Full chain — every 50-pt strike from deep ITM to OTM | Rolling relative only — no absolute strike, just ATM offset |
| **Columns** | Date, Timestamp, Open, High, Low, Close, Volume, OI, Ticker | OHLCV + OI + IV + underlying spot embedded |
| **Bid/ask** | No | No |
| **IV** | No (must derive) | Yes — IV per bar included in API response |
| **OI** | Yes | Yes |
| **Depth of strike coverage** | Full chain available; deep OTM sparse | Only ATM±10 strikes per expiry bucket |
| **Historical depth** | 2024-01-04 to present (2.3 years) | 2021-01-01 to present (5+ years) |
| **Expiry types** | Weekly + monthly, identified by folder name | Weekly + monthly, identified by `expiry_code` |
| **Quality flag** | `cleaned_non_destructive` | `raw_vendor_response_missing_bid_ask` |
| **Known issues** | Two quarantined folders (20250925, 20251224); spot duplicated across folders | Raw JSON not yet normalised; row_count missing from manifest |

**Priority ruling for overlapping period (2024-01-04 onward):**

1. **Shoonya is primary** — it has full strike chain (needed for spread legs), is already cleaned, and has been audited (see [options_data_audit.md](options_data_audit.md)).
2. **Dhan is the cross-check and IV source** — use Dhan ATM close prices to validate Shoonya on overlapping contracts. Any divergence > 5 ticks flags a data quality problem. Use Dhan's embedded IV field to avoid having to derive it.
3. **Dhan is sole source for 2021–2023** — Shoonya data does not exist before 2024; all pre-2024 backtesting uses Dhan rolling option JSON. Note: Dhan provides rolling-ATM buckets only, not absolute strikes — the engine must resolve ATM offset to a strike price using the Dhan instruments master or the embedded spot price.

**Quality checks to run before trusting any backtest result:**

1. For 10 randomly sampled NIFTY expiry dates in 2024–2025, compare Shoonya ATM close vs Dhan ATM (offset 0) close at 09:15, 12:00, and 15:20. Flag if > 5-tick divergence.
2. ~~Confirm Dhan JSON row counts are non-zero per file — the manifest shows `row_count` blank for all Dhan entries.~~ **DONE 2026-05-01** — all 5,292 files parsed, 41.4M clean bars, 0 parse errors. See `data/audit/dhan_options_audit.csv`.
3. Verify India VIX timestamps: merge `INDIA VIX_15minute.csv` against `NIFTY 50_15minute.csv` on datetime — confirm they share the same trading-day calendar and no off-by-one bar shift.
4. Quarantine `20250925` and `20251224` Shoonya folders as documented in [options_data_audit.md](options_data_audit.md).

---

## Data Gaps and How to Fill Them

*Verified 2026-04-30 via Tavily search + Firecrawl scrape of all source pages.*
*Updated 2026-05-01: Dhan weekly/monthly NIFTY rolling options confirmed present in manifest (2021–2026). Shoonya strike-level data confirmed present (2024–2026). VIX daily/1-min confirmed present. Gap 2 below is partially resolved by Dhan data already in hand.*

### Gap 1: No Bid/Ask Spreads on Option Data

**Impact:** All fills are modeled at mid-price. The actual fill is typically mid ± 0.5× spread. For deep OTM options, spreads can be 30–50% of the mid-price. Our fills are systematically optimistic.

**How to fill — free:**
- **NSE Option Chain live JSON** — NSE's option chain page (`nseindia.com/option-chain`) loads its data from an undocumented but stable JSON endpoint. Requires a session cookie obtained by first hitting the main page, then polling the JSON endpoint. Returns full chain bid/ask, OI, IV, volume per strike. Scrape at 15-min intervals, store NDJSON. Builds a timestamped bid/ask series from today forward.
  - Python pattern: `GET https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY` with headers `{'User-Agent': '...', 'Accept-Encoding': 'gzip, deflate, br', 'Accept-Language': 'en-US,en;q=0.9'}` and cookies from a prior session request.
- **NSE Bhavcopy (EOD bid/ask)** — NSE all-reports page at `nseindia.com/all-reports` provides daily F&O Bhavcopy as a zipped CSV. The current format is **CM-UDiFF Common Bhavcopy Final (zip)** (old plain CSV discontinued July 2024 per NSE Circular 62424). This includes LTP, best bid, best ask, volume, OI for every option contract EOD. Free, no login required. Builds EOD bid/ask series from today forward.
  - Direct archive section: `nseindia.com/resources/historical-reports-capital-market-daily-monthly-archives-derivative-market` → "Archives of Daily/Monthly Reports (FO)"

**How to fill — cheap:**
- **Dhan Historical Data API** — `api.dhan.co/v2/charts/intraday` (POST). Provides 1-min OHLCV + OI for last 5 years across all segments including expired F&O contracts. Free with a funded Dhan account (no monthly fee for API access). Intervals supported: 1, 5, 15, 25, 60 min. No bid/ask, but OI+volume per bar is better than Shoonya alone. Already in `scripts/download/download_dhan_expired_options.py` — activate and run.
- **True Data Market Data API** — `truedata.in/products/marketdataapi`. Provides realtime + historical NSE F&O data via WebSocket/REST including L1 bid/ask at 1-sec frequency and live option chain with IV, Greeks. Segments: NSE EQ, NSE Indices, NSE F&O, MCX. Pricing is quote-on-contact (API tier, not Velocity plugin tier — request via `support@truedata.in`). Velocity plugin (charting software) starts at ₹1,440–₹2,796/mo for NSE F&O; API pricing is separate and typically lower for limited-symbol access.

**Interim conservative proxy (use now):** Model fill as `close ± max(0.05, close × 0.003)` — a 0.3% spread proxy that is more pessimistic than mid for low-premium contracts. Apply this in `broker_sim.py` immediately.

---

### Gap 2: Limited Historical Coverage (~2.3 Years of Shoonya Data)

**Impact:** 462 signal observations in spot (2015–2024) is decent for pattern discovery, but the Shoonya options data is only ~2.3 years. That covers one regime (post-COVID recovery). It misses the 2020 crash, 2018 IL&FS, 2016 demonetisation — high-volatility environments where short-vol strategies blow up.

**Status: Substantially resolved.** Dhan rolling option data is validated and cleaned at `data/processed/options/dhan/nifty/` covering 2021-01-01 to 2026-04-30 — 41.4M 1-min bars with embedded IV and OI. Extends the intraday backtest window from 2.3 years (Shoonya) to 5+ years. The remaining gap is 2015–2020 (pre-Dhan intraday) and full-chain strike coverage for 2021–2023 (Dhan covers ATM±10 only, not every strike).

**Remaining actions:**
- ~~Validate Dhan JSON data quality~~ **DONE 2026-05-01** — see `data/audit/dhan_options_audit.csv`.
- Resolve ATM offset to absolute strike using the instruments master or embedded spot price (Phase 4 step 14).
- For 2015–2020 full-chain data: NSE F&O Bhavcopy archives give EOD OHLC back to 2008 — sufficient for VRP/IV proxy studies but not for intraday 1-min backtest. Accept this gap for Track 1 (directional); it only matters for Track 2 (short-vol) which requires surface history.
- **NSE F&O Bhavcopy Archives (2008–present)** — **VALIDATED 2026-05-01.** 8.07M NIFTY option rows, OHLC PASS, 0 parse errors. Cleaned parquet per year at `data/processed/nse/bhavcopy/fo/nifty_options_eod_{year}.parquet`. Dual schema handled (legacy 2008–Jul-2024 + UDiFF Jul-2024+; 2008-02-05 variant also handled). No bid/ask. Use for VRP regime context and EOD settle cross-checks.

**Cross-validation:** Use Dhan vs Shoonya on 2024–2025 overlap to confirm data quality before extending the backtest to 2021–2023 (see Phase 4 step 13).

---

### Gap 3: India VIX Timestamp Alignment Not Yet Validated

**Impact:** We have `INDIA VIX_15minute.csv` and `INDIA VIX_minute.csv` in `data/processed/market_archive_cleaned/` covering 2015–2026. The data is present. The gap is that timestamp alignment against option entry bars has not been verified — a misaligned VIX filter could create lookahead or miss the signal.

**Status: Data is in hand. One validation step needed.**
- Merge `INDIA VIX_15minute.csv` against `NIFTY 50_15minute.csv` on datetime — confirm they share the same trading calendar with no bar-shift. If VIX timestamps are end-of-bar (09:15, 09:30, …) they are safe to use as a 15:00 filter without lookahead.
- Covered in Phase 4 step 18.

---

### Gap 4: No Intraday Volume Profile for NIFTY

**Impact:** We don't know how volume and liquidity evolve through the day. The optimal exit window (10 AM vs 11 AM vs level touch) should be validated against actual liquidity — an exit at 10 AM is only clean if ATM options have enough volume for a real fill.

**Status: Data to build this is already in hand.**
- Shoonya ATM CSV files contain per-bar volume — compute median volume per 15-min bucket across the 2024–2026 period. This is a single `groupby` operation on existing data.
- Dhan weekly ATM data (`data/raw/options/dhan/nifty/week/expiry_code_1/call/ATM/`) extends this profile back to 2021 once validated.

**What to do:** Compute median ATM volume per 15-min bar. Confirm that 09:45–11:00 window has median > 500 contracts/bar. If not, the exit window narrows. This is a 20-minute script, not a data acquisition task.

---

### Gap 5: NIFTY-Specific Microstructure vs US Equity Results

**Impact:** The EOD Reversal paper (Baltussen 2025) is based on US individual stocks. NIFTY is an index — gamma hedging by index option market makers dominates the microstructure at end-of-day in a different way than individual stocks. The direction of the reversal likely holds, but the magnitude and timing may differ.

**How to fill — research approach (no new data needed):**
- Run our own spot-level study: for the 462 signal days, compute the intraday return from open to 10 AM, open to 11 AM, and open to close separately. If the largest directional move happens before 11 AM, it confirms the academic timing.
- This analysis costs nothing — we already have the spot data in `data/processed/market_archive_cleaned/NIFTY 50_15minute.csv`. It is a 30-minute coding task in `scripts/analysis/analyze_3pm_edge_v2.py`.
- This should be done before testing intraday option exits, so we know which window to target.

---

### Data Source Quick Reference

| Source | What it gives | Cost | Coverage | Status |
|---|---|---|---|---|
| **Shoonya strike-level CSV** | Full option chain OHLCV+OI, 1-min per strike | Free (already downloaded) | 2024-01-04–2026-04-21 | **IN HAND** — `data/raw/options/shoonya/nifty/`; primary backtest source; 2 folders quarantined |
| **Dhan rolling option JSON (NIFTY weekly)** | ATM±10 strikes OHLCV+OI+IV, 1-min | Free (Dhan account) | 2021-01-01–2026-04-30 | **VALIDATED** — 41.4M clean bars; OHLC PASS, 0 parse errors; cleaned parquet at `data/processed/options/dhan/nifty/week/`; 1,797 IV spikes capped; 4.3M zero-vol bars retained; audit at `data/audit/dhan_options_audit.csv` |
| **Dhan rolling option JSON (NIFTY monthly)** | ATM±10 strikes OHLCV+OI+IV, 1-min | Free (Dhan account) | 2021-05-01–2026-04-30 | **VALIDATED** — included in audit above; cleaned parquet at `data/processed/options/dhan/nifty/month/` |
| **India VIX 1-min + daily (market archive)** | VIX OHLCV all timeframes | Free (already downloaded) | 2015–2026-04 | **IN HAND** — `data/processed/market_archive_cleaned/INDIA VIX_*.csv`; validate timestamp alignment before use |
| **NIFTY 50 spot 1-min canonical** | Spot OHLCV, trading day calendar | Free (already downloaded) | 2015–2026-04 | **IN HAND** — `data/processed/spot/nifty50_1min_CANONICAL.csv`; use for signal detection and calendar |
| **Dhan instruments master** | Symbol lookup, lot size, tick size, expiry dates | Free (already downloaded) | Point-in-time snapshot | **IN HAND** — `data/raw/reference/dhan_instruments_master.csv`; 241,994 rows; use to resolve Dhan ATM offset to absolute strike |
| **NSE F&O Bhavcopy EOD (NIFTY options)** | EOD OHLC, settle, volume, OI, underlying spot | Free | 2008-01-01–2026-04 | **VALIDATED** — 8.07M rows, OHLC PASS, 0 errors; cleaned parquet at `data/processed/nse/bhavcopy/fo/nifty_options_eod_{year}.parquet`; audit at `data/audit/nse_bhavcopy_audit.csv`; no bid/ask; use for VRP/IV-proxy studies and EOD settle cross-checks |
| NSE option chain live JSON | Live bid/ask, OI, IV per strike (15-min scrape) | Free | Today forward | **BUILT** — run `scrape_nse_option_chain.py --loop` during market hours |
| Yahoo Finance `^INDIAVIX` | Daily VIX OHLC | Free | Multi-year | **SKIP** — `INDIA VIX_day.csv` already covers this fully |
| True Data API | 1-sec L1 bid/ask + option chain with Greeks | Quote on contact | Historical + live | **LOW PRIORITY** — only if Dhan IV quality proves insufficient |
| `broker_sim.py` spread proxy | Conservative half-spread (0.3% of mid or 1 tick) added to fill_price | N/A | N/A | **DONE** — active in all backtest runs immediately |

---

## Implementation Roadmap — Priority Order

### Phase 1 — Backtest Correctness (Do First, Nothing Else Matters Until Done)

1. **Fix expiry multiplication.** One signal date → one expiry. Nearest weekly with DTE ≥ 1.
2. **Fix calendar-day exit.** `next_day_exit` must use next trading day from spot calendar.
3. **Fix debit stop gate.** `entry_credit <= 0` path gets: 50% premium stop, spot-invalidation stop, time-based exit.
4. **Update STT to 2026 rates.** `broker_sim.py`: options sell = 0.15% × premium, exercise = 0.15% × intrinsic.
5. **Re-run all three variants.** Expect trade counts to drop 7–9×. Accept that results may change dramatically.

### Phase 2 — Exit Window Study (One Coding Session)

6. **Intraday return decomposition on signal days.** Using `data/processed/market_archive_cleaned/NIFTY 50_15minute.csv`, compute average move from open to 10:00 AM, 10:30 AM, 11:00 AM, 13:00, and EOD — on the 462 signal days only. This data is already in hand. Confirm where the edge is realised before testing option exits.
7. **Test three exits in backtest.** Level touch (prior 3 PM close), 11:00 AM time stop, EOD fallback. Compare net PnL, win rate, avg hold time across variants. Option data: use `data/raw/options/shoonya/nifty/` for 2024+ and `data/raw/options/dhan/nifty/week/` for 2021–2023 (after Dhan validation passes).

### Phase 3 — Structure (After Clean Exit Window Is Confirmed)

8. **Replace naked long put with put debit spread.** ATM put bought + OTM put sold (50–100 points OTM on NIFTY).
9. **Test call credit spread as alternative.** Different cost profile; credits received offset theta.
10. **Add India VIX regime gate.** Enter only in VIX 13–25. Skip < 13 and > 25. Reduce size at VIX > 25.
11. **Build option feature table.** For each trade: premium, DTE, moneyness, volume, OI, extrinsic value, India VIX at entry. Save to CSV. This becomes the audit trail for all future analysis.

### Phase 4 — Data Integration and Validation

12. **Validate Dhan row counts.** Parse 3 sample JSON files from `data/raw/options/dhan/nifty/week/expiry_code_1/call/ATM/` — confirm non-zero row counts. The manifest shows blank row counts for all Dhan entries. Block 2021–2023 backtest work until this passes.
13. **Cross-validate Shoonya vs Dhan on 2024–2025 overlap.** For 10 randomly sampled NIFTY expiry dates, compare ATM close at 09:15, 12:00, 15:20. Divergence > 5 ticks = data quality flag. Use `data/raw/options/dhan/nifty/week/expiry_code_1/call/ATM/` vs Shoonya ATM folder for the same date.
14. **Resolve Dhan ATM offset → absolute strike.** Dhan data uses relative ATM offsets (ATM, ATMp1, ATMm1…), not absolute strike prices. Use `data/raw/reference/dhan_instruments_master.csv` (SEM_STRIKE_PRICE + SEM_CUSTOM_SYMBOL columns) to build a lookup table: expiry_date + offset → strike. Required before the 2021–2023 data can be used in the backtest engine.
15. **Use Dhan IV field directly.** Dhan API responses embed IV per bar. Extract this into the processed dataset as `iv` column. This is the primary source for VIX-regime entry filters and VRP ratio calculations — avoids deriving IV from OHLC.
16. **Add NSE Bhavcopy EOD downloader.** Gives daily IV proxy and bid/ask for 2015–2024 — needed for Track 2 surface snapshot. Run `python scripts/download/download_nse_bhavcopy.py --from-date 2015-01-01`.
17. **Add bid/ask proxy to broker_sim.** Use `close ± max(0.05, close × 0.003)` as interim spread model. Already done in broker_sim.py.
18. **Validate India VIX timestamps.** Merge `INDIA VIX_15minute.csv` against `NIFTY 50_15minute.csv`. Confirm no bar-shift before wiring VIX into entry filter.

### Phase 5 — Short-Vol Track (Separate Build, After Phase 3 Is Done)

16. **Build daily surface snapshot pipeline.** ATM straddle IV, put/call skew per DTE bucket, from NSE Bhavcopy + Dhan data.
17. **Implement iron condor / short OTM strangle strategy.** Entry filters: VIX 13–25, IV/RV ratio > 1.2, no near-term event.
18. **Implement hybrid Kelly × VIX sizing** per `2508.16598`. Hard cap at 2% capital max loss per trade. Hard stop at 2× credit received.
19. **Walk-forward OOS validation.** At least 12 months OOS before considering live capital on short-vol track.

### Phase 6 — ML Layer (Only After 3+ Years Clean OOS Exists)

20. Train on option returns (not spot labels). Features: DTE, moneyness, IV proxy, volume, OI, spread proxy, time of day, India VIX, realised vol, gap size, trend, event proximity. Turnover penalty in loss function. Locked walk-forward splits only.

---

## Bottom Line

The 3 PM signal is academically grounded. The losses in all three variants are structural — wrong expression, wrong exit window, wrong cost model, and three correctness bugs inflating the trade count. None of these require a new signal.

**Data-wise, we are in good shape.** The core data stack is in hand: NIFTY 50 spot 1-min (2015–2026), India VIX 1-min and daily (2015–2026), Shoonya strike-level option data (2024–2026), Dhan rolling-ATM option data with embedded IV (2021–2026), and the Dhan instruments master for strike resolution. The remaining work is integration and validation — not acquisition.

**The two blockers before serious backtesting begins:**
1. Fix the three correctness bugs (expiry multiplication, calendar-day exit, debit stop gate).
2. Validate Dhan JSON data (row counts, ATM offset → strike resolution, cross-check vs Shoonya on overlap period).

After those pass: change the exit to intraday, switch to debit spreads, add a VIX gate. The short-vol track is the stronger long-run business but requires surface data infrastructure (NSE Bhavcopy bulk + Dhan IV normalisation) that is partially available but not yet wired up. Build the directional track first, validate it, then add short-vol.

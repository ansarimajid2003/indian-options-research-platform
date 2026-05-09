# Indian Index Options Strategy Validation - 2026-05-05

## Scope

This memo validates the current cross-index short-premium research pack after adding the missing Dhan monthly data for non-NIFTY indices and refreshing India VIX through the latest available Dhan bar.

Validated indices:

- NIFTY
- BANKNIFTY
- FINNIFTY
- MIDCPNIFTY

Primary validation window: `2022-02-01` to `2026-04-30`.

## Data Acquisition

Monthly Dhan expired-options data was pulled with `expiryFlag=MONTH`, `ATM +/- 10`, and processed into the canonical Dhan parquet layout.

| Index | Security ID | Monthly parquets | Clean rows | Min timestamp | Max timestamp |
|---|---:|---:|---:|---|---|
| BANKNIFTY | 25 | 42 | 18,319,477 | 2021-08-04 10:00:00+05:30 | 2026-05-04 15:29:00+05:30 |
| FINNIFTY | 27 | 42 | 10,551,464 | 2021-08-05 14:34:00+05:30 | 2026-05-04 15:29:00+05:30 |
| MIDCPNIFTY | 442 | 42 | 8,294,072 | 2022-01-31 09:33:00+05:30 | 2026-05-04 15:29:00+05:30 |

India VIX was refreshed from Dhan and rebuilt at:

- `data/processed/spot/indiavix_1min_DHAN.csv`
- Rows: 437,281
- Coverage: `2021-08-04 10:00:00` to `2026-05-04 15:29:00`
- Last close: `18.27`

## Data Audit

Options audit:

- Files processed: 7,686
- Files with errors: 1,363
- Clean bars: 38,494,290
- OHLC failures: 0
- Duplicate timestamps dropped: 0
- Zero-close bars: 0
- Zero-volume bars kept and flagged: 12,944,003
- IV spikes above 200 percent flagged: 3,175
- IV=0 bars flagged: 845,455

The option parse-error count is not an OHLC integrity problem. The failures are sparse/empty Dhan responses for no-data option buckets, mainly early-history and far-offset contracts. The parser preserved those as audit failures instead of silently inventing bars.

VIX audit:

- Files processed: 22
- Files with errors: 1
- Clean rows: 448,531 before final processed output
- Bad OHLC rows: 3

Verdict: data is good enough for regime-filter validation, but the sparse Dhan no-data buckets should stay visible in audit reports.

## Engine Changes

Implemented VIX regime filtering in the backtest engine:

- No-future-peek lookup: entry uses the latest VIX observation at or before the entry timestamp.
- Missing VIX defaults to skip.
- Thresholds are min-inclusive and max-exclusive.
- Ledger now includes `vix_entry`, `vix_bucket`, and `vix_timestamp`.
- CLI and batch runners now accept `--vix-path`, `--vix-min`, `--vix-max`, and `--vix-missing-policy`.
- The refreshed Dhan VIX file is the validation source, not stale archive VIX.

Verification passed:

- `python -m compileall -q options_backtest scripts tests`
- `python -m unittest discover -s tests`
- Unit suite: 54 tests passed

## Short Strangle Validation

These are the current focused short-strangle contributors. This is still a naked short-premium research expression, not a live deployment structure.

| Variant | Trades | Net PnL | Charges | Win rate | Profit factor | Portfolio Sharpe | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| No VIX baseline | 2,907 | 504,340 | 225,083 | 64.8% | 1.222 | 1.065 | -104,154 |
| 13 <= VIX < 22 | 1,665 | 381,159 | 131,308 | 64.9% | 1.261 | 0.902 | -90,886 |
| 10 <= VIX < 25 | 2,820 | 457,339 | 216,817 | 64.8% | 1.207 | 0.971 | -116,280 |
| 10 <= VIX < 30 | 2,861 | 468,152 | 221,306 | 64.6% | 1.208 | 0.987 | -114,755 |

Baseline contributors:

| Symbol | Trades | Net PnL | Sharpe | Profit factor | Max DD% |
|---|---:|---:|---:|---:|---:|
| NIFTY | 818 | 46,606 | 0.224 | 1.072 | -7.49% |
| BANKNIFTY | 876 | 123,086 | 0.738 | 1.172 | -7.75% |
| FINNIFTY | 676 | 93,687 | 0.703 | 1.192 | -3.48% |
| MIDCPNIFTY | 537 | 240,961 | 2.216 | 1.584 | -1.96% |

## VIX Bucket Attribution

Bucket attribution on the no-VIX baseline, using the refreshed Dhan VIX as-of entry timestamp:

| VIX bucket | Trades | Net PnL | Avg net/trade | Profit factor |
|---|---:|---:|---:|---:|
| <10 | 46 | 36,188 | 787 | 3.007 |
| 10-13 | 1,061 | 8,645 | 8 | 1.012 |
| 13-17 | 1,205 | 319,118 | 265 | 1.325 |
| 17-22 | 460 | 62,041 | 135 | 1.129 |
| 22-30 | 135 | 78,348 | 580 | 1.826 |

The main practical finding is that `10-13` carries many trades with almost no edge. The strict `13-22` gate improves profit factor and reduces drawdown, but it also gives up too much absolute PnL and lowers portfolio Sharpe. The wider `10-25` and `10-30` filters do not improve risk-adjusted results versus the baseline.

## OOS Split

Split used here:

- Train: `2022-02-01` to `2024-12-31`
- OOS: `2025-01-01` to `2026-04-30`

| Variant | Split | Trades | Net PnL | Profit factor |
|---|---|---:|---:|---:|
| No VIX | Train | 1,890 | 333,529 | 1.270 |
| No VIX | OOS | 1,017 | 170,810 | 1.166 |
| 13 <= VIX < 22 | Train | 1,160 | 249,498 | 1.297 |
| 13 <= VIX < 22 | OOS | 505 | 131,661 | 1.211 |
| 10 <= VIX < 25 | Train | 1,864 | 324,242 | 1.267 |
| 10 <= VIX < 25 | OOS | 956 | 133,097 | 1.134 |
| 10 <= VIX < 30 | Train | 1,890 | 333,529 | 1.270 |
| 10 <= VIX < 30 | OOS | 971 | 134,622 | 1.133 |

Baseline symbol OOS check:

| Symbol | Train net PnL | OOS net PnL | OOS PF |
|---|---:|---:|---:|
| BANKNIFTY | 141,934 | -18,848 | 0.941 |
| FINNIFTY | 59,768 | 33,918 | 1.178 |
| MIDCPNIFTY | 142,087 | 98,874 | 1.418 |
| NIFTY | -10,259 | 56,865 | 1.199 |

## Smoke Tests

One-month VIX-gated short strangle smoke, `2026-04-01` to `2026-04-30`, `13 <= VIX < 22`:

| Symbol | Trades | Net PnL | Sharpe |
|---|---:|---:|---:|
| NIFTY | 13 | 3,177 | 2.272 |
| BANKNIFTY | 15 | -7,591 | -2.826 |
| FINNIFTY | 2 | 10,043 | 16.739 |
| MIDCPNIFTY | 7 | 2,007 | 1.029 |
| Combined | 37 | 7,636 | n/a |

One-month monthly iron-condor smoke, `2026-04-01` to `2026-04-30`, `13 <= VIX < 22`:

| Symbol | Trades | Net PnL | Sharpe |
|---|---:|---:|---:|
| NIFTY | 19 | -12,328 | -15.880 |
| BANKNIFTY | 19 | -16,052 | -20.510 |
| FINNIFTY | 2 | 89 | 3.383 |
| MIDCPNIFTY | 5 | -2,137 | -5.252 |

This monthly iron-condor smoke validates wiring and monthly-data access only. The current strategy implementation still enters daily against the monthly expiry; it is not yet the intended one-entry monthly 18-24 DTE iron-condor research design.

## Verdict

Short strangle family:

- Validated across all four indices with refreshed Dhan VIX support.
- The no-VIX baseline remains the highest absolute PnL and Sharpe in the tested focused pack.
- The `13 <= VIX < 22` filter is useful as a risk-control candidate because it improves profit factor and lowers drawdown, but it is not a clear standalone improvement.
- The `10 <= VIX < 25` and `10 <= VIX < 30` filters are not worth adopting as default gates; they mostly remove some trades without improving risk-adjusted performance.
- The most useful next filter experiment is not a broad min/max gate. It is a targeted skip or size reduction for the `10-13` bucket.

Iron condor family:

- Monthly data is now available for all four indices.
- The engine can load monthly contracts and apply VIX gates.
- Current results are not a fair validation of the research thesis because the strategy needs a proper monthly entry selector before full conclusions.

Recommended next implementation:

1. Add a monthly short-premium scheduler that opens only one structure per monthly cycle, with configurable 18-24 DTE entry.
2. Convert the naked short-strangle contributor pack into defined-risk spreads or iron condors before considering live deployment.
3. Replace the current broad VIX gate with bucket-aware sizing or a `10-13` skip/downsize rule.
4. Keep VIX fields in every ledger so future attribution remains reproducible.

## Artifacts

Data and audit:

- `data/processed/options/dhan/banknifty/month/`
- `data/processed/options/dhan/finnifty/month/`
- `data/processed/options/dhan/midcpnifty/month/`
- `data/processed/spot/indiavix_1min_DHAN.csv`
- `data/audit/dhan_options_summary_banknifty_finnifty_midcpnifty.txt`
- `data/audit/dhan_spot_summary.txt`

Validation ledgers:

- `reports/backtests/options/focused/20260505_validation_short_strangle_no_vix_focused_contributors_summary.md`
- `reports/backtests/options/focused/20260505_validation_short_strangle_vix_13_22_focused_contributors_summary.md`
- `reports/backtests/options/focused/20260505_validation_short_strangle_vix_10_25_focused_contributors_summary.md`
- `reports/backtests/options/focused/20260505_validation_short_strangle_vix_10_30_focused_contributors_summary.md`

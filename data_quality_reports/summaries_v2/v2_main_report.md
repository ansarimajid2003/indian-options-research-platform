# NIFTY 50 3 PM Focused Alpha Candidate

Dataset: `NIFTY 50 15min (2015-01-09 to 2024-04-07)` | Period: `2015-01-09` to `2024-04-04` | N = `2,276` days

## Baseline

- Gap-up rate: **69.3%**
- Next-day open-to-close up rate: **46.9%**
- Average next-day O-C: **-0.0769%**

## Kept Candidate

The only active alpha candidate is:

`3 PM bullish candle -> 3:15 bearish candle -> next-day gap-up`

This is the late-session failed-strength setup. It keeps the idea simple: price showed strength into 3 PM, failed by 3:15, then opened higher next day. The test is whether that gap is faded intraday, with the prior 3 PM close acting as the decision level.

| Slice | N | Gap up | Next day up | Avg gap | Avg O-C | Median O-C | Touch rate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline all days | 2276 | 69.3% | 46.9% | 0.126% | -0.077% | -0.055% | 64.0% |
| all gap up days | 1578 | 100.0% | 46.6% | 0.384% | -0.087% | -0.071% | 65.2% |
| bullish then bearish all gaps | 645 | 71.6% | 42.0% | 0.134% | -0.131% | -0.113% | 69.6% |
| main candidate | 462 | 100.0% | 40.0% | 0.345% | -0.155% | -0.138% | 73.4% |

## Candidate Touch Split

The prior 3 PM close is retained as the execution/diagnostic level, not as another curve-fit parameter.

| Touched prior 3 PM close | N | Next day up | Avg O-C | Median O-C | Close>level |
| --- | --- | --- | --- | --- | --- |
| no | 123 | 74.8% | 0.242% | 0.305% | 90.2% |
| yes | 339 | 27.4% | -0.299% | -0.288% | 40.7% |

## Period Stability

This is the quick anti-overfit check. The candidate should stay directionally similar across time, not live only in one pocket of history.

| Period | N | Next day up | Avg O-C | Median O-C | Touch rate |
| --- | --- | --- | --- | --- | --- |
| 2015-2018 | 221 | 37.1% | -0.198% | -0.167% | 75.6% |
| 2019-2022 | 183 | 43.2% | -0.098% | -0.116% | 69.9% |
| 2023+ | 58 | 41.4% | -0.171% | -0.092% | 75.9% |

## Touch Mechanic Context

Across all gap-up/gap-down days, the same level behavior shows why the candidate is worth focusing on.

| Gap | Touched prior 3 PM close | N | Next day up | Avg O-C | Median O-C |
| --- | --- | --- | --- | --- | --- |
| gap down | no | 270 | 21.5% | -0.509% | -0.394% |
| gap down | yes | 426 | 64.3% | 0.238% | 0.225% |
| gap up | no | 549 | 77.4% | 0.364% | 0.334% |
| gap up | yes | 1029 | 30.1% | -0.328% | -0.297% |

## Combo Context Only

This table is kept only to compare the chosen setup against the other final-two-candle combinations. It is not a request to add more filters.

| 3 PM + 3:15 combo | Gap | N | Next day up | Avg O-C | Median O-C | Touch rate |
| --- | --- | --- | --- | --- | --- | --- |
| bearish then bearish | gap down | 161 | 45.3% | -0.124% | -0.059% | 46.6% |
| bearish then bearish | gap up | 361 | 48.5% | -0.054% | -0.036% | 68.4% |
| bearish then bullish | gap down | 180 | 43.9% | -0.076% | -0.106% | 63.9% |
| bearish then bullish | gap up | 393 | 50.6% | -0.050% | 0.018% | 57.5% |
| bullish then bearish | gap down | 181 | 47.5% | -0.064% | -0.043% | 60.2% |
| bullish then bearish | gap up | 462 | 40.0% | -0.155% | -0.138% | 73.4% |
| bullish then bullish | gap down | 172 | 53.5% | 0.047% | 0.063% | 72.7% |
| bullish then bullish | gap up | 357 | 48.7% | -0.066% | -0.019% | 59.9% |

## Removed From v2

The broad parameter sweep has been removed from this report. The v2 output now keeps only the selected setup, its touch-level behavior, and period stability.

## Files

- Focused daily observations: `../../data_quality_reports\v2_daily_observations.csv`
- Focused edge tables: `../../data_quality_reports\v2_edge_tables.csv`
- Candidate table: `../../data_quality_reports\v2_candidate_edges.csv`

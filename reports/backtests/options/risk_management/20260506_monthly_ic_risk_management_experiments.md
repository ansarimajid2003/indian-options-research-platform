# Risk Management Experiments - 20260506_monthly_ic

Window: `2022-02-01` to `2026-04-30`
IC expiry: `month` | Naked source: `20260505_024624`

Purpose: reduce naked short-strangle tail risk and concentration after the lot-sizing study.

## Research Priors

- Kelly sizing maximizes long-run log growth, but full Kelly can create severe short-term drawdowns; fractional Kelly is the practical version for noisy estimates.
- For option books, defined-risk spreads are a cleaner input to Kelly/CVaR sizing because the loss distribution is bounded by construction.
- CVaR/expected shortfall is a better optimization target than VaR for short-vol strategies because it measures average tail loss beyond the quantile.
- The indexed paper `2508.16598` is directly aligned with this project: combine Kelly sizing with VIX regime scaling for short-dated option selling rather than fixed lots.

Sources: MacLean, Thorp, Ziemba on Kelly/fractional Kelly (SSRN https://ssrn.com/abstract=1797366); Nekrasov multivariate fractional Kelly (SSRN https://ssrn.com/abstract=2259133); Rockafellar/Uryasev CVaR optimization (Journal of Risk https://www.risk.net/journal-of-risk/technical-paper/2161159/optimization-conditional-value-risk); Wysocki `2508.16598` hybrid Kelly x VIX put-writing summary (RePEc https://ideas.repec.org/p/arx/papers/2508.16598.html).

## VIX Bucket Attribution

Naked short-strangle x1 contributors, annotated with refreshed Dhan India VIX as-of entry timestamp.

| VIX bucket | Trades | Net PnL | Avg/trade | PF | Max DD% |
|---|---:|---:|---:|---:|---:|
| <10 | 46 | +INR 36,188 | +INR 787 | 3.007 | -1.58% |
| 10-13 | 1,061 | +INR 8,645 | +INR 8 | 1.012 | -8.16% |
| 13-17 | 1,205 | +INR 319,118 | +INR 265 | 1.325 | -5.65% |
| 17-22 | 460 | +INR 62,041 | +INR 135 | 1.129 | -6.17% |
| 22-30 | 135 | +INR 78,348 | +INR 580 | 1.826 | -1.68% |

## Portfolio Comparison

| Portfolio | Lots | Source | Trades | Net PnL | CAGR | Sharpe | Max DD% | PF | CVaR95/day | OOS Net | OOS PF | Worst open risk |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Naked baseline 4x1 | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | strangle | 2,907 | +INR 504,340 | +10.11% | 1.065 | -10.42% | 1.222 | -INR 17,696 | +INR 170,810 | 1.166 | unlimited |
| Naked old Port A | `BANKNIFTY:1 MIDCPNIFTY:7` | strangle | 1,413 | +INR 1,961,893 | +29.18% | 1.986 | -14.48% | 1.552 | -INR 36,676 | +INR 743,222 | 1.382 | unlimited |
| Naked old Port B | `BANKNIFTY:2 MIDCPNIFTY:5` | strangle | 1,413 | +INR 1,593,711 | +25.20% | 1.944 | -16.54% | 1.462 | -INR 29,555 | +INR 517,941 | 1.289 | unlimited |
| Naked skip VIX 10-13 4x1 | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | strangle | 1,846 | +INR 495,695 | +9.96% | 1.168 | -9.17% | 1.315 | -INR 19,115 | +INR 177,942 | 1.268 | unlimited |
| Naked half VIX 10-13 4x1 | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | strangle | 2,907 | +INR 500,017 | +10.03% | 1.144 | -9.68% | 1.260 | -INR 16,377 | +INR 174,376 | 1.206 | unlimited |
| Naked capped tilt skip 10-13 | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:2` | strangle | 1,846 | +INR 677,777 | +12.98% | 1.357 | -10.07% | 1.374 | -INR 21,467 | +INR 265,834 | 1.339 | unlimited |
| Naked BN/FN/MCP tilt skip 10-13 | `BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:2` | strangle | 1,300 | +INR 622,764 | +12.09% | 1.632 | -9.40% | 1.469 | -INR 15,494 | +INR 201,134 | 1.338 | unlimited |
| Naked risk-parity skip 10-13 | `NIFTY:1 BANKNIFTY:1 FINNIFTY:2 MIDCPNIFTY:2` | strangle | 1,846 | +INR 803,476 | +14.92% | 1.356 | -12.59% | 1.377 | -INR 25,596 | +INR 296,259 | 1.323 | unlimited |
| Naked quarter-Kelly skip 10-13 | `BANKNIFTY:4 FINNIFTY:4 MIDCPNIFTY:4` | strangle | 1,300 | +INR 1,762,724 | +27.08% | 1.467 | -33.61% | 1.405 | -INR 50,886 | +INR 452,968 | 1.238 | unlimited |
| Naked MCP:3 BN:1 FN:1 skip 10-13 | `BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:3` | strangle | 1,300 | +INR 804,846 | +14.94% | 1.697 | -10.41% | 1.513 | -INR 19,306 | +INR 289,025 | 1.404 | unlimited |
| Naked MCP:4 BN:1 FN:1 skip 10-13 | `BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:4` | strangle | 1,300 | +INR 986,929 | +17.57% | 1.723 | -11.41% | 1.546 | -INR 23,334 | +INR 376,917 | 1.451 | unlimited |
| Naked MCP:3 BN:2 FN:1 skip 10-13 | `BANKNIFTY:2 FINNIFTY:1 MIDCPNIFTY:3` | strangle | 1,300 | +INR 965,169 | +17.27% | 1.642 | -15.14% | 1.462 | -INR 24,183 | +INR 292,446 | 1.313 | unlimited |
| Naked MCP:4 BN:2 FN:1 skip 10-13 | `BANKNIFTY:2 FINNIFTY:1 MIDCPNIFTY:4` | strangle | 1,300 | +INR 1,147,251 | +19.74% | 1.694 | -16.15% | 1.493 | -INR 27,652 | +INR 380,338 | 1.361 | unlimited |
| Naked MCP:5 BN:2 FN:1 skip 10-13 | `BANKNIFTY:2 FINNIFTY:1 MIDCPNIFTY:5` | strangle | 1,300 | +INR 1,385,784 | +22.76% | 1.798 | -16.75% | 1.544 | -INR 31,317 | +INR 492,396 | 1.423 | unlimited |
| Naked MCP:6 BN:2 FN:1 skip 10-13 | `BANKNIFTY:2 FINNIFTY:1 MIDCPNIFTY:6` | strangle | 1,300 | +INR 1,511,416 | +24.25% | 1.738 | -18.15% | 1.538 | -INR 35,439 | +INR 556,121 | 1.430 | unlimited |
| IC wing 2 all VIX | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | iron_condor_wing2 | 2,477 | -INR 1,081,405 | -100.00% | -15.401 | -108.10% | 0.050 | -INR 3,702 | -INR 567,256 | 0.046 | +INR 17,769 |
| IC wing 2 skip VIX 10-13 | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | iron_condor_wing2 | 1,577 | -INR 717,671 | -25.79% | -10.949 | -71.72% | 0.051 | -INR 3,670 | -INR 336,548 | 0.053 | +INR 17,769 |
| IC wing 4 all VIX | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | iron_condor_wing4 | 2,353 | -INR 808,510 | -32.28% | -9.279 | -80.82% | 0.190 | -INR 3,925 | -INR 403,683 | 0.171 | +INR 40,771 |
| IC wing 4 skip VIX 10-13 | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | iron_condor_wing4 | 1,505 | -INR 521,877 | -15.97% | -6.990 | -52.15% | 0.201 | -INR 3,735 | -INR 229,190 | 0.206 | +INR 40,771 |
| IC wing 6 all VIX | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | iron_condor_wing6 | 2,060 | -INR 359,444 | -9.97% | -3.741 | -36.01% | 0.489 | -INR 2,971 | -INR 185,821 | 0.454 | +INR 66,465 |
| IC wing 6 skip VIX 10-13 | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | iron_condor_wing6 | 1,284 | -INR 198,965 | -5.10% | -2.407 | -19.96% | 0.551 | -INR 2,931 | -INR 92,238 | 0.541 | +INR 66,465 |
| IC6 MCP:2 BN:1 FN:1 skip 10-13 | `BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:2` | iron_condor_wing6 | 718 | -INR 102,277 | -2.52% | -1.499 | -10.24% | 0.642 | -INR 3,047 | -INR 68,961 | 0.554 | +INR 60,863 |
| IC6 MCP:4 BN:1 FN:1 skip 10-13 | `BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:4` | iron_condor_wing6 | 718 | -INR 139,280 | -3.48% | -1.404 | -13.93% | 0.626 | -INR 5,171 | -INR 96,889 | 0.545 | +INR 89,101 |
| IC6 MCP:4 BN:2 FN:1 skip 10-13 | `BANKNIFTY:2 FINNIFTY:1 MIDCPNIFTY:4` | iron_condor_wing6 | 718 | -INR 229,398 | -5.97% | -1.889 | -22.94% | 0.575 | -INR 5,778 | -INR 143,518 | 0.506 | +INR 105,817 |
| IC6 MCP:6 BN:2 FN:1 skip 10-13 | `BANKNIFTY:2 FINNIFTY:1 MIDCPNIFTY:6` | iron_condor_wing6 | 718 | -INR 266,401 | -7.06% | -1.746 | -26.64% | 0.575 | -INR 7,894 | -INR 171,447 | 0.508 | +INR 134,055 |
| IC6 quarter-Kelly BN:4 FN:4 MCP:4 skip 10-13 | `BANKNIFTY:4 FINNIFTY:4 MIDCPNIFTY:4` | iron_condor_wing6 | 718 | -INR 335,103 | -9.19% | -1.506 | -33.93% | 0.655 | -INR 8,548 | -INR 219,986 | 0.561 | +INR 186,975 |

## Sizing Diagnostics

| Symbol | Raw full-Kelly lots | Quarter-Kelly capped lots | Risk-parity lots |
|---|---:|---:|---:|
| NIFTY | 0.00 | 0 | 1 |
| BANKNIFTY | 74.73 | 4 | 1 |
| FINNIFTY | 39.63 | 4 | 2 |
| MIDCPNIFTY | 95.86 | 4 | 2 |

## Read

- The profitable naked book is not a deployable risk shape. Historical max DD is an observation, not a bound.
- The first VIX control to keep is targeted `10-13` skip/downsize. Broad `13-22` gating is too blunt for the current contributor pack.
- Treat full Kelly as a diagnostic only. Use quarter Kelly or smaller, cap by defined-risk max loss, and recompute on rolling train windows.
- Prefer portfolio risk budget by worst-case spread loss/CVaR, not by contract count. A seven-lot MIDCPNIFTY concentration is exactly what this layer should prevent.

## Files

- Portfolio table: `20260506_monthly_ic_portfolio_comparison.csv`
- VIX bucket attribution: `20260506_monthly_ic_vix_bucket_attribution.csv`
- Report: `20260506_monthly_ic_risk_management_experiments.md`
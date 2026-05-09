# Risk Management Experiments - 20260506_mixed_expiry

Window: `2022-02-01` to `2026-04-30`
IC/spread expiry: NIFTY=W BANKNIFTY=M FINNIFTY=M MIDCPNIFTY=M (W=weekly M=monthly) | Naked source: `20260505_024624`

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
| IC wing 2 all VIX | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | iron_condor_wing2 | 2,333 | -INR 901,398 | -42.09% | -12.735 | -90.18% | 0.100 | -INR 3,733 | -INR 475,479 | 0.098 | +INR 17,769 |
| IC wing 2 skip VIX 10-13 | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | iron_condor_wing2 | 1,490 | -INR 594,460 | -19.17% | -9.489 | -59.48% | 0.100 | -INR 3,647 | -INR 277,139 | 0.111 | +INR 17,769 |
| IC wing 4 all VIX | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | iron_condor_wing4 | 2,223 | -INR 626,399 | -20.72% | -6.591 | -62.86% | 0.339 | -INR 4,288 | -INR 315,914 | 0.329 | +INR 40,771 |
| IC wing 4 skip VIX 10-13 | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | iron_condor_wing4 | 1,430 | -INR 395,086 | -11.18% | -5.021 | -39.73% | 0.358 | -INR 4,071 | -INR 168,966 | 0.383 | +INR 40,771 |
| IC wing 6 all VIX | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | iron_condor_wing6 | 1,966 | -INR 61,340 | -1.48% | -0.632 | -7.05% | 0.900 | -INR 3,127 | -INR 35,989 | 0.882 | +INR 66,465 |
| IC wing 6 skip VIX 10-13 | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | iron_condor_wing6 | 1,245 | +INR 9,054 | +0.21% | 0.073 | -3.61% | 1.023 | -INR 3,115 | +INR 5,738 | 1.032 | +INR 66,465 |
| IC wing 8 all VIX | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | iron_condor_wing8 | 696 | +INR 216,002 | +4.72% | 4.198 | -0.69% | 3.272 | -INR 1,405 | +INR 64,205 | 2.173 | +INR 77,561 |
| IC wing 8 skip VIX 10-13 | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | iron_condor_wing8 | 400 | +INR 130,341 | +2.93% | 3.064 | -0.79% | 2.989 | -INR 1,711 | +INR 26,245 | 1.728 | +INR 71,111 |
| IC6 mcp2 bn1 fn1 skip 10-13 | `BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:2` | iron_condor_wing6 | 718 | -INR 102,277 | -2.52% | -1.499 | -10.24% | 0.642 | -INR 3,047 | -INR 68,961 | 0.554 | +INR 60,863 |
| IC6 mcp4 bn1 fn1 skip 10-13 | `BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:4` | iron_condor_wing6 | 718 | -INR 139,280 | -3.48% | -1.404 | -13.93% | 0.626 | -INR 5,171 | -INR 96,889 | 0.545 | +INR 89,101 |
| IC6 mcp4 bn2 fn1 skip 10-13 | `BANKNIFTY:2 FINNIFTY:1 MIDCPNIFTY:4` | iron_condor_wing6 | 718 | -INR 229,398 | -5.97% | -1.889 | -22.94% | 0.575 | -INR 5,778 | -INR 143,518 | 0.506 | +INR 105,817 |
| IC6 mcp6 bn2 fn1 skip 10-13 | `BANKNIFTY:2 FINNIFTY:1 MIDCPNIFTY:6` | iron_condor_wing6 | 718 | -INR 266,401 | -7.06% | -1.746 | -26.64% | 0.575 | -INR 7,894 | -INR 171,447 | 0.508 | +INR 134,055 |
| IC6 qkelly skip 10-13 | `BANKNIFTY:4 FINNIFTY:4 MIDCPNIFTY:4` | iron_condor_wing6 | 718 | -INR 335,103 | -9.19% | -1.506 | -33.93% | 0.655 | -INR 8,548 | -INR 219,986 | 0.561 | +INR 186,975 |
| IC8 mcp2 bn1 fn1 skip 10-13 | `BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:2` | iron_condor_wing8 | 202 | +INR 26,621 | +0.63% | 0.750 | -1.16% | 1.495 | -INR 2,054 | -INR 938 | 0.968 | +INR 64,362 |
| IC8 mcp4 bn1 fn1 skip 10-13 | `BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:4` | iron_condor_wing8 | 202 | +INR 39,683 | +0.93% | 0.680 | -1.77% | 1.563 | -INR 3,547 | +INR 8,768 | 1.213 | +INR 105,200 |
| IC8 mcp4 bn2 fn1 skip 10-13 | `BANKNIFTY:2 FINNIFTY:1 MIDCPNIFTY:4` | iron_condor_wing8 | 202 | +INR 36,336 | +0.85% | 0.549 | -2.32% | 1.351 | -INR 4,108 | -INR 3,930 | 0.931 | +INR 128,723 |
| IC8 mcp6 bn2 fn1 skip 10-13 | `BANKNIFTY:2 FINNIFTY:1 MIDCPNIFTY:6` | iron_condor_wing8 | 202 | +INR 49,398 | +1.15% | 0.555 | -2.93% | 1.411 | -INR 5,589 | +INR 5,775 | 1.084 | +INR 169,561 |
| IC8 qkelly skip 10-13 | `BANKNIFTY:4 FINNIFTY:4 MIDCPNIFTY:4` | iron_condor_wing8 | 202 | +INR 80,360 | +1.86% | 0.759 | -3.43% | 1.442 | -INR 5,478 | -INR 23,161 | 0.754 | +INR 175,770 |
| CS put ±2/±6 all VIX | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | credit_spread_put_2x6 | 2,350 | -INR 579,686 | -18.48% | -2.897 | -58.58% | 0.561 | -INR 9,811 | -INR 341,322 | 0.493 | +INR 50,278 |
| CS put ±2/±6 skip VIX 10-13 | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | credit_spread_put_2x6 | 1,513 | -INR 430,464 | -12.43% | -2.633 | -43.66% | 0.522 | -INR 9,595 | -INR 207,202 | 0.468 | +INR 49,444 |
| CS put ±2/±8 all VIX | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | credit_spread_put_2x8 | 2,179 | -INR 771,208 | -29.38% | -3.067 | -78.07% | 0.521 | -INR 12,841 | -INR 455,599 | 0.443 | +INR 76,422 |
| CS put ±2/±8 skip VIX 10-13 | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | credit_spread_put_2x8 | 1,402 | -INR 586,720 | -18.81% | -2.822 | -59.62% | 0.474 | -INR 12,907 | -INR 285,745 | 0.410 | +INR 76,422 |
| CS call ±2/±6 all VIX | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | credit_spread_call_2x6 | 2,417 | -INR 565,360 | -17.84% | -2.671 | -56.79% | 0.623 | -INR 9,169 | -INR 287,050 | 0.613 | +INR 47,327 |
| CS call ±2/±6 skip VIX 10-13 | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | credit_spread_call_2x6 | 1,552 | -INR 348,948 | -9.62% | -2.020 | -35.14% | 0.641 | -INR 9,137 | -INR 173,048 | 0.605 | +INR 47,327 |
| CS call ±2/±8 all VIX | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | credit_spread_call_2x8 | 2,278 | -INR 857,387 | -36.82% | -3.234 | -86.16% | 0.543 | -INR 12,517 | -INR 440,962 | 0.534 | +INR 74,043 |
| CS call ±2/±8 skip VIX 10-13 | `NIFTY:1 BANKNIFTY:1 FINNIFTY:1 MIDCPNIFTY:1` | credit_spread_call_2x8 | 1,455 | -INR 553,939 | -17.33% | -2.533 | -55.81% | 0.550 | -INR 12,627 | -INR 271,624 | 0.522 | +INR 74,043 |

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

- Portfolio table: `20260506_mixed_expiry_portfolio_comparison.csv`
- VIX bucket attribution: `20260506_mixed_expiry_vix_bucket_attribution.csv`
- Report: `20260506_mixed_expiry_risk_management_experiments.md`
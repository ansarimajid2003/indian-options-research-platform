# DTE-Filtered Iron Condor Comparison (Monthly, Apples-to-Apples)

Run: `20260506`  |  Baseline: `20260506_mixed_expiry`  |  Window: 2022-02-01 to 2026-04-30

**Setup**: baseline and filtered runs both use `expiry_type=month` for FINNIFTY/MIDCPNIFTY.
The DTE<=7 filter restricts entry to the final 7 calendar days of the monthly expiry cycle.
This isolates the pure filter effect without inflating trade count via pre-discontinuation weekly entries.

NIFTY uses `expiry_type=week` throughout (always weekly); filter tested: `VIX >= 13` floor.

IS split: 2022-2024 | OOS split: 2025-2026

---
## FINNIFTY

Both baseline and filtered use `expiry_type=month` — apples-to-apples comparison. `DTE <= 7` = enter only in the final week of the monthly expiry cycle.

### Wing-4

| Strategy | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | IS PnL | IS Sh | OOS PnL | OOS Sh |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline (unrestricted, monthly) | 256 | -52,087 | -1.4% | -2.109 | -4.074 | -1.496 | -0.250 | -5.5% | 0.481 | 38% | -22,821 | -1.501 | -29,266 | -3.134 |
| DTE<=7 | 176 | -11,506 | -0.3% | -0.565 | -1.256 | -0.315 | -0.200 | -1.5% | 0.793 | 49% | -8,504 | -0.667 | -3,002 | -0.405 |

#### DTE<=7 — DTE breakdown

| DTE | Trades | Avg PnL | Total PnL | Win% |
|---|---:|---:|---:|---:|
| 0-2 | 47.0 | +14 | +635 | 53% |
| 3-5 | 72.0 | -4 | -254 | 60% |
| 6-10 | 57.0 | -209 | -11,887 | 33% |

#### DTE<=7 — VIX bucket breakdown

| VIX | Trades | Avg PnL | Total PnL | Win% |
|---|---:|---:|---:|---:|
| <10 | 4 | +318 | +1,270 | 75% |
| 10-13 | 73 | -200 | -14,586 | 44% |
| 13-17 | 78 | -7 | -539 | 53% |
| 17-22 | 18 | +55 | +982 | 44% |
| 22-30 | 3 | +455 | +1,365 | 100% |

### Wing-6

| Strategy | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | IS PnL | IS Sh | OOS PnL | OOS Sh |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline (unrestricted, monthly) | 195 | +24,784 | 0.7% | 1.023 | 2.003 | 0.946 | 0.613 | -1.1% | 1.496 | 52% | +16,702 | 1.239 | +8,081 | 0.782 |
| DTE<=7 | 148 | +39,919 | 1.1% | 1.808 | 3.633 | 1.518 | 2.917 | -0.4% | 2.360 | 60% | +19,501 | 1.578 | +20,418 | 2.227 |

#### DTE<=7 — DTE breakdown

| DTE | Trades | Avg PnL | Total PnL | Win% |
|---|---:|---:|---:|---:|
| 0-2 | 39.0 | +530 | +20,682 | 62% |
| 3-5 | 65.0 | +341 | +22,134 | 69% |
| 6-10 | 44.0 | -66 | -2,898 | 45% |

#### DTE<=7 — VIX bucket breakdown

| VIX | Trades | Avg PnL | Total PnL | Win% |
|---|---:|---:|---:|---:|
| <10 | 3 | +558 | +1,675 | 67% |
| 10-13 | 61 | +27 | +1,677 | 52% |
| 13-17 | 67 | +445 | +29,800 | 66% |
| 17-22 | 14 | +351 | +4,913 | 57% |
| 22-30 | 3 | +618 | +1,853 | 100% |

### Wing-8

| Strategy | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | IS PnL | IS Sh | OOS PnL | OOS Sh |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline (unrestricted, monthly) | 60 | +27,685 | 0.8% | 2.104 | 4.012 | 2.312 | 5.857 | -0.1% | 5.561 | 67% | +21,920 | 2.412 | +5,765 | 1.450 |
| DTE<=7 | 48 | +30,596 | 1.0% | 2.527 | 4.546 | 3.996 | 20.200 | -0.1% | 13.429 | 81% | +23,449 | 2.611 | +7,148 | 2.397 |

#### DTE<=7 — DTE breakdown

| DTE | Trades | Avg PnL | Total PnL | Win% |
|---|---:|---:|---:|---:|
| 0-2 | 13.0 | +1,359 | +17,667 | 92% |
| 3-5 | 19.0 | +443 | +8,423 | 79% |
| 6-10 | 16.0 | +282 | +4,507 | 75% |

#### DTE<=7 — VIX bucket breakdown

| VIX | Trades | Avg PnL | Total PnL | Win% |
|---|---:|---:|---:|---:|
| 10-13 | 24 | +485 | +11,647 | 83% |
| 13-17 | 20 | +800 | +16,009 | 85% |
| 17-22 | 3 | +218 | +654 | 33% |
| 22-30 | 1 | +2,286 | +2,286 | 100% |

---
## MIDCPNIFTY

Both baseline and filtered use `expiry_type=month` — apples-to-apples comparison. `DTE <= 7` = enter only in the final week of the monthly expiry cycle.

### Wing-4

| Strategy | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | IS PnL | IS Sh | OOS PnL | OOS Sh |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline (unrestricted, monthly) | 254 | -104,470 | -3.8% | -5.081 | -7.325 | -4.644 | -0.349 | -10.9% | 0.206 | 23% | -19,820 | -2.760 | -84,650 | -7.107 |
| DTE<=7 | 126 | -14,048 | -0.5% | -1.092 | -1.806 | -0.643 | -0.258 | -1.9% | 0.627 | 39% | -5,902 | -1.080 | -8,146 | -1.172 |

#### DTE<=7 — DTE breakdown

| DTE | Trades | Avg PnL | Total PnL | Win% |
|---|---:|---:|---:|---:|
| 0-2 | 24.0 | +271 | +6,504 | 71% |
| 3-5 | 59.0 | -142 | -8,385 | 36% |
| 6-10 | 43.0 | -283 | -12,167 | 26% |

#### DTE<=7 — VIX bucket breakdown

| VIX | Trades | Avg PnL | Total PnL | Win% |
|---|---:|---:|---:|---:|
| <10 | 4 | +58 | +234 | 75% |
| 10-13 | 46 | +25 | +1,139 | 46% |
| 13-17 | 61 | -183 | -11,154 | 34% |
| 17-22 | 10 | -130 | -1,300 | 30% |
| 22-30 | 5 | -593 | -2,967 | 20% |

### Wing-6

| Strategy | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | IS PnL | IS Sh | OOS PnL | OOS Sh |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline (unrestricted, monthly) | 183 | -27,765 | -1.1% | -1.443 | -1.991 | -1.313 | -0.289 | -3.6% | 0.624 | 36% | +478 | 0.074 | -28,243 | -2.383 |
| DTE<=7 | 97 | +23,983 | 0.9% | 1.669 | 2.661 | 1.081 | 3.870 | -0.2% | 2.331 | 60% | +8,605 | 1.803 | +15,378 | 1.771 |

#### DTE<=7 — DTE breakdown

| DTE | Trades | Avg PnL | Total PnL | Win% |
|---|---:|---:|---:|---:|
| 0-2 | 19.0 | +835 | +15,868 | 74% |
| 3-5 | 52.0 | +213 | +11,078 | 65% |
| 6-10 | 26.0 | -114 | -2,963 | 38% |

#### DTE<=7 — VIX bucket breakdown

| VIX | Trades | Avg PnL | Total PnL | Win% |
|---|---:|---:|---:|---:|
| <10 | 4 | +797 | +3,187 | 100% |
| 10-13 | 34 | +379 | +12,874 | 71% |
| 13-17 | 46 | +204 | +9,376 | 54% |
| 17-22 | 9 | +9 | +83 | 33% |
| 22-30 | 4 | -384 | -1,536 | 50% |

### Wing-8

| Strategy | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | IS PnL | IS Sh | OOS PnL | OOS Sh |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline (unrestricted, monthly) | 48 | +8,820 | 0.3% | 0.680 | 1.128 | 0.401 | 0.559 | -0.6% | 1.608 | 50% | +5,199 | 1.477 | +3,620 | 0.437 |
| DTE<=7 | 28 | +20,051 | 0.8% | 1.762 | 3.037 | 1.102 | 7.500 | -0.1% | 9.581 | 79% | +6,498 | 2.274 | +13,552 | 2.061 |

#### DTE<=7 — DTE breakdown

| DTE | Trades | Avg PnL | Total PnL | Win% |
|---|---:|---:|---:|---:|
| 0-2 | 9.0 | +1,448 | +13,034 | 78% |
| 3-5 | 11.0 | +516 | +5,673 | 91% |
| 6-10 | 8.0 | +168 | +1,344 | 62% |

#### DTE<=7 — VIX bucket breakdown

| VIX | Trades | Avg PnL | Total PnL | Win% |
|---|---:|---:|---:|---:|
| <10 | 2 | +697 | +1,393 | 100% |
| 10-13 | 9 | +818 | +7,359 | 89% |
| 13-17 | 13 | +579 | +7,529 | 69% |
| 17-22 | 2 | +2,304 | +4,607 | 100% |
| 22-30 | 2 | -419 | -839 | 50% |

---
## NIFTY

NIFTY weekly throughout. Filter: `VIX >= 13` floor removes ultra-calm low-premium entries.

### Wing-6

| Strategy | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | IS PnL | IS Sh | OOS PnL | OOS Sh |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline (unrestricted, monthly) | 798 | +93,023 | 2.1% | 1.566 | 3.047 | 2.049 | 1.269 | -1.7% | 1.396 | 58% | +24,235 | 0.814 | +68,788 | 2.636 |
| VIX>=13 | 515 | +91,271 | 2.1% | 1.831 | 3.515 | 2.046 | 1.405 | -1.5% | 1.663 | 58% | +32,095 | 1.266 | +59,176 | 2.708 |

#### VIX>=13 — DTE breakdown

| DTE | Trades | Avg PnL | Total PnL | Win% |
|---|---:|---:|---:|---:|
| 0-2 | 253.0 | +284 | +71,904 | 62% |
| 3-5 | 149.0 | +104 | +15,423 | 56% |
| 6-10 | 113.0 | +35 | +3,945 | 55% |

#### VIX>=13 — VIX bucket breakdown

| VIX | Trades | Avg PnL | Total PnL | Win% |
|---|---:|---:|---:|---:|
| 13-17 | 311 | +216 | +67,093 | 61% |
| 17-22 | 152 | +108 | +16,406 | 51% |
| 22-30 | 52 | +149 | +7,773 | 63% |

### Wing-8

| Strategy | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | IS PnL | IS Sh | OOS PnL | OOS Sh |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline (unrestricted, monthly) | 326 | +182,745 | 4.0% | 5.200 | 10.635 | 3.575 | 6.516 | -0.6% | 9.065 | 86% | +112,344 | 6.373 | +70,400 | 4.512 |
| VIX>=13 | 194 | +108,390 | 2.5% | 3.475 | 6.743 | 1.847 | 3.968 | -0.6% | 6.389 | 83% | +78,216 | 4.807 | +30,174 | 2.227 |

#### VIX>=13 — DTE breakdown

| DTE | Trades | Avg PnL | Total PnL | Win% |
|---|---:|---:|---:|---:|
| 0-2 | 103.0 | +651 | +67,086 | 89% |
| 3-5 | 49.0 | +419 | +20,534 | 73% |
| 6-10 | 42.0 | +495 | +20,771 | 79% |

#### VIX>=13 — VIX bucket breakdown

| VIX | Trades | Avg PnL | Total PnL | Win% |
|---|---:|---:|---:|---:|
| 13-17 | 123 | +561 | +69,021 | 86% |
| 17-22 | 53 | +569 | +30,183 | 77% |
| 22-30 | 18 | +510 | +9,187 | 78% |

---
## Baseline DTE Breakdown (wing-6, for reference)

### FINNIFTY wing-6 baseline

| DTE | Trades | Avg PnL | Total PnL | Win% |
|---|---:|---:|---:|---:|
| 0-2 | 39.0 | +530 | +20,682 | 62% |
| 3-5 | 65.0 | +341 | +22,134 | 69% |
| 6-10 | 53.0 | -51 | -2,686 | 47% |
| 11-20 | 23.0 | -240 | -5,530 | 26% |
| 21+ | 15.0 | -654 | -9,817 | 7% |

### MIDCPNIFTY wing-6 baseline

| DTE | Trades | Avg PnL | Total PnL | Win% |
|---|---:|---:|---:|---:|
| 0-2 | 19.0 | +835 | +15,868 | 74% |
| 3-5 | 52.0 | +213 | +11,078 | 65% |
| 6-10 | 40.0 | -259 | -10,361 | 30% |
| 11-20 | 37.0 | -603 | -22,317 | 3% |
| 21+ | 35.0 | -630 | -22,034 | 14% |

### NIFTY wing-6 baseline

| DTE | Trades | Avg PnL | Total PnL | Win% |
|---|---:|---:|---:|---:|
| 0-2 | 376.0 | +239 | +89,705 | 63% |
| 3-5 | 234.0 | +52 | +12,256 | 55% |
| 6-10 | 188.0 | -48 | -8,938 | 52% |

---
## Read

- **t-stat > 1.96** = statistically significant (weekly bucket resampling).
  t-stat dropping significantly from baseline to filtered = filter removed signal, not noise.
  t-stat increasing = filter improved the signal-to-noise ratio.
- **OOS (2025-2026)**: only 1.5 years; treat as directionally informative.
  Strong OOS degradation when filter applied suggests overfitting to IS thresholds.
- **DTE ≤ 7 on monthly**: enters only in expiry week. 15-30 DTE entries are completely excluded.
  This is structurally different from the unrestricted run — fewer but higher-theta trades.
- **MIDCPNIFTY weekly discontinued Nov 2024**: after that, max_dte=7 means expiry-week monthly
  entries only. Pre-Nov 2024, weekly entries were naturally ≤7 DTE anyway.
- If filter gives fewer trades with similar/better Sharpe AND t-stat stays ≥ 1.96: real edge.
  If t-stat drops below 1.96 on filtered run: statistically indistinguishable from noise.
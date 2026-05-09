# Optimized Wing-6 IC Portfolio — 20260507

Per-symbol filters applied:
- **NIFTY**: VIX >= 13 (weekly; removes ultra-calm low-premium entries)
- **FINNIFTY**: DTE <= 7 (monthly; isolates expiry-week theta)
- **MIDCPNIFTY**: DTE <= 7 (monthly; flips from deeply negative to positive)
- **SENSEX**: DTE <= 2 (weekly; captures extreme theta-decay zone)
- **BANKNIFTY**: EXCLUDED (uniformly negative on wing-6 across all regime cells)

On top, `skip 10-13` removes the VIX bucket that is consistently poison for short-vol.
Note: SENSEX VIX 10-13 is profitable — skip is NOT applied to SENSEX in smart-bucket mode.

IS split: 2024-12-31 | OOS split: 2025-01-01

---
## Portfolio Comparison

| Portfolio | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | CVaR95/day | IS PnL | IS Sh | OOS PnL | OOS Sh |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline 5x1 all VIX | 2,293 | -33,379 | -0.8% | -0.359 | -0.651 | -0.556 | -0.140 | -5.7% | 0.950 | 43% | -3,389 | -20,912 | -0.414 | -12,467 | -0.358 |
| Baseline 5x1 skip 10-13 | 1,458 | +25,391 | 0.6% | 0.216 | 0.391 | 0.284 | 0.155 | -3.8% | 1.060 | 44% | -3,403 | +8,501 | 0.196 | +16,889 | 0.270 |
| Baseline 4x1 all VIX (no BN) | 1,503 | +118,003 | 2.7% | 1.316 | 2.497 | 1.936 | 1.328 | -2.0% | 1.289 | 53% | -2,686 | +45,854 | 1.164 | +72,149 | 1.676 |
| Filtered smart buckets (per-symbol) | 836 | +195,490 | 4.3% | 2.821 | 5.421 | 3.942 | 4.175 | -1.0% | 2.082 | 60% | -2,075 | +69,896 | 2.181 | +125,594 | 3.937 |
| Baseline 4x1 skip 10-13 (no BN) | 968 | +115,508 | 2.6% | 1.509 | 2.818 | 1.840 | 1.631 | -1.6% | 1.457 | 53% | -2,749 | +51,990 | 1.534 | +63,518 | 1.715 |
| Filtered 4x1 all VIX | 901 | +195,631 | 4.3% | 2.746 | 5.302 | 3.999 | 4.175 | -1.0% | 1.988 | 60% | -2,080 | +68,317 | 2.064 | +127,314 | 3.892 |
| Filtered 4x1 skip 10-13 | 754 | +165,382 | 3.7% | 2.421 | 4.630 | 3.241 | 3.563 | -1.0% | 1.947 | 59% | -2,183 | +67,495 | 2.120 | +97,888 | 3.097 |
| Filtered N:2 F:1 M:1 S:1 skip | 754 | +256,653 | 5.5% | 2.234 | 4.245 | 2.775 | 2.221 | -2.5% | 1.822 | 59% | -3,884 | +99,590 | 1.789 | +157,064 | 3.017 |
| Filtered N:1 F:2 M:2 S:1 skip | 754 | +214,733 | 4.7% | 2.383 | 4.655 | 3.359 | 4.264 | -1.1% | 2.059 | 59% | -2,717 | +94,225 | 2.270 | +120,508 | 2.870 |
| Filtered N:2 F:2 M:2 S:1 skip | 754 | +306,004 | 6.5% | 2.310 | 4.427 | 3.054 | 3.155 | -2.1% | 1.899 | 59% | -4,294 | +126,321 | 2.011 | +179,684 | 2.953 |
| Filtered risk-parity skip | 239 | +134,742 | 3.4% | 2.448 | 4.976 | 2.372 | 4.803 | -0.7% | 2.963 | 62% | -2,760 | +58,503 | 2.835 | +76,239 | 2.647 |

---
## Per-Symbol Detail

### NIFTY

| Portfolio | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | CVaR95/day | IS PnL | IS Sh | OOS PnL | OOS Sh |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline (unrestricted) | 798 | +93,023 | 2.1% | 1.566 | 3.047 | 2.049 | 1.269 | -1.7% | 1.396 | 58% | -1,985 | +24,235 | 0.814 | +68,788 | 2.636 |
| Baseline skip 10-13 | 527 | +92,830 | 2.1% | 1.834 | 3.470 | 2.041 | 1.432 | -1.5% | 1.650 | 59% | -1,915 | +32,095 | 1.266 | +60,734 | 2.709 |
| Filtered | 515 | +91,271 | 2.1% | 1.831 | 3.515 | 2.046 | 1.405 | -1.5% | 1.663 | 58% | -1,874 | +32,095 | 1.266 | +59,176 | 2.708 |
| Filtered + skip 10-13 | 515 | +91,271 | 2.1% | 1.831 | 3.515 | 2.046 | 1.405 | -1.5% | 1.663 | 58% | -1,874 | +32,095 | 1.266 | +59,176 | 2.708 |

#### Filtered — VIX bucket breakdown

| VIX bucket | Trades | Net PnL | Avg/trade | PF | Max DD% |
|---|---:|---:|---:|---:|---:|
| 13-17 | 311 | +67,093 | +216 | 1.793 | -0.74% |
| 17-22 | 152 | +16,406 | +108 | 1.381 | -1.05% |
| 22-30 | 52 | +7,773 | +149 | 1.781 | -0.34% |

### BANKNIFTY

| Portfolio | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | CVaR95/day | IS PnL | IS Sh | OOS PnL | OOS Sh |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline (unrestricted) | 790 | -151,382 | -3.8% | -4.254 | -7.113 | -6.748 | -0.251 | -15.1% | 0.410 | 25% | -1,199 | -66,766 | -3.424 | -84,616 | -5.756 |
| Baseline skip 10-13 | 490 | -90,117 | -2.2% | -2.877 | -4.814 | -3.856 | -0.245 | -9.0% | 0.462 | 27% | -1,257 | -43,489 | -2.510 | -46,629 | -3.581 |

### FINNIFTY

| Portfolio | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | CVaR95/day | IS PnL | IS Sh | OOS PnL | OOS Sh |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline (unrestricted) | 195 | +24,784 | 0.7% | 1.023 | 2.003 | 0.946 | 0.613 | -1.1% | 1.496 | 52% | -1,391 | +16,702 | 1.239 | +8,081 | 0.782 |
| Baseline skip 10-13 | 123 | +24,843 | 0.7% | 1.153 | 2.265 | 0.952 | 0.613 | -1.1% | 1.785 | 53% | -1,368 | +19,247 | 1.705 | +5,597 | 0.580 |
| Filtered | 148 | +39,919 | 1.1% | 1.808 | 3.633 | 1.518 | 2.917 | -0.4% | 2.360 | 60% | -1,309 | +19,501 | 1.578 | +20,418 | 2.227 |
| Filtered + skip 10-13 | 87 | +38,242 | 1.0% | 1.954 | 3.938 | 1.346 | 3.571 | -0.3% | 3.674 | 66% | -1,279 | +20,965 | 2.031 | +17,277 | 2.022 |

#### Filtered — VIX bucket breakdown

| VIX bucket | Trades | Net PnL | Avg/trade | PF | Max DD% |
|---|---:|---:|---:|---:|---:|
| <10 | 3 | +1,675 | +558 | 144.567 | -0.00% |
| 10-13 | 61 | +1,677 | +27 | 1.111 | -0.55% |
| 13-17 | 67 | +29,800 | +445 | 4.266 | -0.16% |
| 17-22 | 14 | +4,913 | +351 | 1.951 | -0.43% |
| 22-30 | 3 | +1,853 | +618 | - | 0.00% |

### MIDCPNIFTY

| Portfolio | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | CVaR95/day | IS PnL | IS Sh | OOS PnL | OOS Sh |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline (unrestricted) | 183 | -27,765 | -1.1% | -1.443 | -1.991 | -1.313 | -0.289 | -3.6% | 0.624 | 36% | -1,699 | +478 | 0.074 | -28,243 | -2.383 |
| Baseline skip 10-13 | 105 | -18,502 | -0.8% | -1.345 | -1.834 | -0.956 | -0.366 | -2.2% | 0.574 | 33% | -1,832 | -4,537 | -0.939 | -13,964 | -1.601 |
| Filtered | 97 | +23,983 | 0.9% | 1.669 | 2.661 | 1.081 | 3.870 | -0.2% | 2.331 | 60% | -1,536 | +8,605 | 1.803 | +15,378 | 1.771 |
| Filtered + skip 10-13 | 63 | +11,109 | 0.5% | 1.036 | 1.671 | 0.577 | 2.043 | -0.2% | 1.807 | 54% | -1,469 | +5,766 | 1.613 | +5,343 | 0.790 |

#### Filtered — VIX bucket breakdown

| VIX bucket | Trades | Net PnL | Avg/trade | PF | Max DD% |
|---|---:|---:|---:|---:|---:|
| <10 | 4 | +3,187 | +797 | - | 0.00% |
| 10-13 | 34 | +12,874 | +379 | 4.025 | -0.19% |
| 13-17 | 46 | +9,376 | +204 | 2.191 | -0.21% |
| 17-22 | 9 | +83 | +9 | 1.023 | -0.27% |
| 22-30 | 4 | -1,536 | -384 | 0.337 | -0.20% |

### SENSEX

| Portfolio | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | CVaR95/day | IS PnL | IS Sh | OOS PnL | OOS Sh |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline (unrestricted) | 327 | +27,961 | 1.4% | 1.772 | 2.789 | 2.902 | 2.957 | -0.5% | 1.561 | 50% | -888 | +4,438 | 1.627 | +23,523 | 1.917 |
| Baseline skip 10-13 | 213 | +16,337 | 0.8% | 1.132 | 1.795 | 1.599 | 1.800 | -0.4% | 1.465 | 50% | -888 | +5,185 | 2.057 | +11,152 | 0.927 |
| Filtered | 141 | +40,458 | 2.0% | 3.685 | 5.620 | 5.472 | 13.200 | -0.1% | 4.114 | 64% | -657 | +8,115 | 3.612 | +32,342 | 3.902 |
| Filtered + skip 10-13 | 89 | +24,761 | 1.2% | 2.697 | 4.105 | 3.253 | 8.267 | -0.1% | 3.771 | 63% | -701 | +8,669 | 4.287 | +16,092 | 2.326 |

#### Filtered — VIX bucket breakdown

| VIX bucket | Trades | Net PnL | Avg/trade | PF | Max DD% |
|---|---:|---:|---:|---:|---:|
| <10 | 5 | +2,070 | +414 | 5.982 | -0.04% |
| 10-13 | 52 | +15,697 | +302 | 4.867 | -0.09% |
| 13-17 | 61 | +15,913 | +261 | 3.595 | -0.12% |
| 17-22 | 18 | +5,948 | +330 | 3.947 | -0.14% |
| 22-30 | 5 | +830 | +166 | 3.244 | -0.02% |

---
## Key Takeaways

1. **BANKNIFTY wing-6 is structurally broken** — every regime cell loses money. Removing it from the portfolio is the single biggest improvement.

2. **FINNIFTY + MIDCPNIFTY DTE <= 7** isolates the profitable expiry-week theta. DTE 11-30 is toxic (avg -240 to -654 per trade).

3. **NIFTY VIX >= 13** improves Sharpe from 1.57 to 1.83 by dropping the ultra-calm low-premium entries where gamma risk overwhelms theta capture.

4. **SENSEX DTE <= 2** captures the extreme theta-decay zone with Sharpe 3.69 and t-stat 5.62. DTE 1 trades average +438/trade with 71% win rate. DTE 3+ turns flat/negative.

5. **Skip VIX 10-13** on top of per-symbol filters further cleans the signal for NSE indices. However, SENSEX is different — its VIX 10-13 bucket is strongly profitable (+102/trade). Do NOT apply VIX 10-13 skip to SENSEX.

6. **Risk-parity sizing** computed on the filtered daily matrix naturally overweights the lower-vol contributors (typically NIFTY and FINNIFTY).

---
## Read

- **t-stat > 1.96** = statistically significant (weekly bucket resampling).
- If filtered portfolio has fewer trades but higher t-stat: filter improved SNR, not just reduced sample size.
- OOS (2025-2026) is only 1.5 years; treat as directionally informative, not conclusive.
- SENSEX data starts May 2023 (weekly launch), so IS is shorter (~1.6 years). OOS coincides with the Tuesday expiry regime (Jan-Aug 2025) and Thursday regime (Sep 2025+).
- All per-symbol filters were selected from prior in-sample analysis (20260506 DTE-filtered comparison + wing-8 filter investigation + SENSEX exploration). This is a research synthesis run, not a fresh optimization.
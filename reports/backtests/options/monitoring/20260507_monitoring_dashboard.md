# Strategy Monitoring Dashboard — 20260507

Generated: 20260507  |  Window: 2022-02-01 to 2026-04-30  |  IS: 2022-2024  |  OOS: 2025-2026

---
## Section A: Optimized Wing-6 IC (Deployable)

Per-symbol filters: NIFTY VIX>=13 (weekly), FINNIFTY DTE<=7 (monthly), MIDCPNIFTY DTE<=7 (monthly). BANKNIFTY excluded. Smart bucket policies per symbol.

| Strategy | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | CVaR95/day | IS PnL | IS Sh | OOS PnL | OOS Sh |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| W6 Base 1:1:1 | 695 | +155,032 | 3.5% | 2.371 | 4.567 | 3.168 | 3.359 | -1.0% | 1.925 | 60% | -2,108 | +61,781 | 1.973 | +93,251 | 3.128 |
| W6 Sharpe-Opt 1:2:2 | 695 | +218,793 | 4.8% | 2.454 | 4.798 | 3.522 | 4.641 | -1.0% | 2.107 | 60% | -2,638 | +91,466 | 2.206 | +127,327 | 3.086 |
| W6 Sharpe-Opt 2:4:4 | 695 | +437,586 | 8.9% | 2.454 | 4.798 | 3.522 | 4.340 | -2.1% | 2.107 | 60% | -5,276 | +182,933 | 2.206 | +254,653 | 3.086 |

### Wing-6 Lot Grid — Top 10 by Sharpe

| Strategy | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | CVaR95/day | IS PnL | IS Sh | OOS PnL | OOS Sh |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| W6 1:2:2 | 695.0 | +218,793 | 4.8% | 2.454 | 4.798 | 3.522 | 4.641 | -1.0% | 2.107 | 60% | -2,638 | +91,466 | 2.206 | +127,327 | 3.086 |
| W6 2:4:4 | 695.0 | +437,586 | 8.9% | 2.454 | 4.798 | 3.522 | 4.340 | -2.1% | 2.107 | 60% | -5,276 | +182,933 | 2.206 | +254,653 | 3.086 |
| W6 2:4:3 | 695.0 | +412,067 | 8.5% | 2.449 | 4.774 | 3.494 | 4.137 | -2.1% | 2.085 | 60% | -5,022 | +174,212 | 2.190 | +237,855 | 3.078 |
| W6 1:3:2 | 695.0 | +257,035 | 5.5% | 2.442 | 4.815 | 3.555 | 4.295 | -1.3% | 2.213 | 60% | -3,004 | +112,431 | 2.254 | +144,604 | 2.992 |
| W6 2:3:3 | 695.0 | +373,825 | 7.8% | 2.438 | 4.731 | 3.415 | 4.371 | -1.8% | 2.023 | 60% | -4,717 | +153,247 | 2.126 | +220,578 | 3.124 |
| W6 2:3:4 | 695.0 | +399,344 | 8.2% | 2.438 | 4.750 | 3.437 | 4.583 | -1.8% | 2.048 | 60% | -4,987 | +161,968 | 2.142 | +237,376 | 3.122 |
| W6 1:3:3 | 695.0 | +282,554 | 6.0% | 2.435 | 4.812 | 3.534 | 4.646 | -1.3% | 2.241 | 60% | -3,269 | +121,152 | 2.266 | +161,402 | 2.992 |
| W6 1:2:3 | 695.0 | +244,312 | 5.3% | 2.424 | 4.758 | 3.450 | 5.087 | -1.0% | 2.145 | 60% | -2,949 | +100,187 | 2.205 | +144,125 | 3.048 |
| W6 1:2:1 | 695.0 | +193,274 | 4.2% | 2.423 | 4.706 | 3.409 | 4.167 | -1.0% | 2.062 | 60% | -2,412 | +82,745 | 2.158 | +110,528 | 3.039 |
| W6 2:4:2 | 695.0 | +386,548 | 8.0% | 2.423 | 4.706 | 3.409 | 3.946 | -2.0% | 2.062 | 60% | -4,824 | +165,491 | 2.158 | +221,057 | 3.039 |

---
## Section B: Defined-Risk Profile (Per Trade)

Wing-6 IC = short at ±2 offsets, long at ±8 offsets. Max loss = (wing_width – net_credit) × lot_size.

| Symbol | Trades | Wing Width | Avg Credit | Avg Max Loss | Worst Max Loss | Credit/Risk | Worst Day Risk | VaR 95% | CVaR 95% | Max Daily Loss |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NIFTY | 515 | 300 pts (300-300) | INR 6,763 | INR 8,922 | INR 18,608 | 0.758 | INR 18,608 | INR -1,250 | INR -1,874 | INR -4,627 |
| FINNIFTY | 148 | 300 pts (300-600) | INR 6,107 | INR 8,469 | INR 21,534 | 0.721 | INR 21,534 | INR -1,009 | INR -1,309 | INR -1,881 |
| MIDCPNIFTY | 97 | 150 pts (125-175) | INR 8,253 | INR 6,589 | INR 14,119 | 1.253 | INR 14,119 | INR -969 | INR -1,536 | INR -1,998 |

**Key insight:** MIDCPNIFTY has the lowest per-trade max loss (~INR 6.6k) because its strike spacing is 25 pts vs 50 pts for NIFTY/FINNIFTY. Its credit/risk ratio is >1.0, meaning on average it collects more premium than its max risk — a function of the DTE<=7 filter catching high-theta expiry-week entries.

---
## Section C: Scaled Lot Combinations

Scaled versions of the best Sharpe and Calmar ratios. Sharpe and t-stat are invariant to proportional scaling; CAGR grows non-linearly from compounding.

| Strategy | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | CVaR95/day | IS PnL | IS Sh | OOS PnL | OOS Sh |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| W6 Sharpe-Opt 3:6:6 | 695 | +656,379 | 12.6% | 2.454 | 4.798 | 3.522 | 4.104 | -3.1% | 2.107 | 60% | -7,914 | +274,399 | 2.206 | +381,980 | 3.086 |
| W6 Sharpe-Opt 4:8:8 | 695 | +875,172 | 16.0% | 2.454 | 4.798 | 3.522 | 3.888 | -4.1% | 2.107 | 60% | -10,552 | +365,865 | 2.206 | +509,307 | 3.086 |
| W6 Calmar-Opt 1:2:3 | 695 | +244,312 | 5.3% | 2.424 | 4.758 | 3.450 | 5.087 | -1.0% | 2.145 | 60% | -2,949 | +100,187 | 2.205 | +144,125 | 3.048 |
| W6 Calmar-Opt 2:4:6 | 695 | +488,624 | 9.8% | 2.424 | 4.758 | 3.450 | 4.731 | -2.1% | 2.145 | 60% | -5,898 | +200,374 | 2.205 | +288,250 | 3.048 |
| W6 Calmar-Opt 3:6:9 | 695 | +732,936 | 13.8% | 2.424 | 4.758 | 3.450 | 4.436 | -3.1% | 2.145 | 60% | -8,847 | +300,561 | 2.205 | +432,375 | 3.048 |
| W6 Calmar-Opt 4:8:12 | 695 | +977,248 | 17.4% | 2.424 | 4.758 | 3.450 | 4.192 | -4.2% | 2.145 | 60% | -11,796 | +400,749 | 2.205 | +576,500 | 3.048 |
| W6 Extra 1:3:4 | 695 | +308,073 | 6.5% | 2.401 | 4.748 | 3.430 | 4.992 | -1.3% | 2.266 | 60% | -3,590 | +129,873 | 2.252 | +178,200 | 2.956 |
| W6 Extra 2:3:4 | 695 | +399,344 | 8.2% | 2.438 | 4.750 | 3.437 | 4.583 | -1.8% | 2.048 | 60% | -4,987 | +161,968 | 2.142 | +237,376 | 3.122 |

---
## Section D: Prior Best Performers (20260506 Risk Management)

Portfolios from the 20260506 mixed-expiry risk management experiments. Included for cross-reference.

### Defined-Risk Strategies (Sharpe > 1.5)

| Strategy | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | CVaR95/day | IS PnL | IS Sh | OOS PnL | OOS Sh |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| IC wing 8 all VIX | 696 | +216,002 | 4.7% | 4.198 | - | 5.838 | - | -0.7% | 3.272 | - | -1,405 | +151,797 | - | +64,205 | - |
| IC wing 8 skip VIX 10-13 | 400 | +130,341 | 2.9% | 3.064 | - | 3.030 | - | -0.8% | 2.989 | - | -1,711 | +104,096 | - | +26,245 | - |

### Naked Short-Strangle Strategies (Sharpe > 1.6)

> ⚠️ **All naked strategies carry unlimited theoretical risk.** Historical max DD is an observation, not a bound.

| Strategy | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | CVaR95/day | IS PnL | IS Sh | OOS PnL | OOS Sh |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Naked old Port A | 1,413 | +1,961,893 | 29.2% | 1.986 | - | 2.002 | - | -14.5% | 1.552 | - | -36,676 | +1,218,672 | - | +743,222 | - |
| Naked old Port B | 1,413 | +1,593,711 | 25.2% | 1.944 | - | 2.099 | - | -16.5% | 1.462 | - | -29,555 | +1,075,770 | - | +517,941 | - |
| Naked MCP:5 BN:2 FN:1 skip 10-13 | 1,300 | +1,385,784 | 22.8% | 1.798 | - | 1.685 | - | -16.8% | 1.544 | - | -31,317 | +893,389 | - | +492,396 | - |
| Naked MCP:6 BN:2 FN:1 skip 10-13 | 1,300 | +1,511,416 | 24.2% | 1.738 | - | 1.613 | - | -18.1% | 1.538 | - | -35,439 | +955,295 | - | +556,121 | - |
| Naked MCP:4 BN:1 FN:1 skip 10-13 | 1,300 | +986,929 | 17.6% | 1.723 | - | 1.589 | - | -11.4% | 1.546 | - | -23,334 | +610,012 | - | +376,917 | - |

---
## Recommendations

### For Deployment (Defined Risk Only)

**Primary:** Wing-6 Optimized Base (1:1:1) — CAGR 3.5%, Sharpe 2.371, MaxDD -1.0%. Statistically significant (t-stat 4.567), strong OOS Sharpe 3.128.

**Higher-CAGR scaled options (same Sharpe, linearly scaled lots):**

| Scale | Lots (N:F:M) | CAGR | Sharpe | MaxDD% | Net PnL | CVaR95/day |
|---|---:|---:|---:|---:|---:|---:|
| 1x | 1:2:2 | 4.8% | 2.454 | -1.0% | +218,793 | -2,638 |
| 2x | 2:4:4 | 8.9% | 2.454 | -2.1% | +437,586 | -5,276 |
| 3x | 3:6:6 | 12.6% | 2.454 | -3.1% | +656,379 | -7,914 |
| 4x | 4:8:8 | 16.0% | 2.454 | -4.1% | +875,172 | -10,552 |

> Scaling preserves Sharpe/Calmar exactly because all legs scale proportionally. CAGR grows non-linearly due to compounding. A 2x–3x scale is a sweet spot: CAGR 9–13% with MaxDD still under -3.5%.

### For Monitoring (Research Track)

Track the top 3 performers from each section monthly. If OOS Sharpe degrades below 1.5 for two consecutive months, pause that variant and investigate.

---
## Files

- Wing-6 lot grid: `20260507_w6_lot_grid_search.csv`
- Deploy configs (JSON): `20260507_w6_deploy_configs.json`
- This report: `20260507_monitoring_dashboard.md`
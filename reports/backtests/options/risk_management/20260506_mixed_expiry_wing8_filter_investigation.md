# Wing-8 Filter Investigation

**Question**: the wing-8 IC only entered on ~18–41% of available days because
ATM±10 strike data was absent on the rest. Those traded days had significantly
lower VIX and intraday range. Can we turn this accidental filter into a
deliberate, pre-market observable filter?

**Key finding from audit**: skipped days were *worse* for ICs across all 4 indices
(lower W6 PnL, higher range, higher VIX). So the data gap was inadvertently
selecting the *right* days to trade.

**Filter candidates tested** (all pre-market or at-entry observable):
- `vix_entry`: VIX at 9:20 AM on entry day
- `range_prev`: prior trading day intraday range% (fully pre-market)
- `hv5`: 5-day rolling avg of daily range% over prior 5 sessions
- `dte_at_entry`: days to expiry

**Terminology**:
- TPR (True Positive Rate): % of actual wing-8 trade days captured by the filter
- FPR (False Positive Rate): % of skipped (bad) days the filter lets through
- Precision: of days the filter passes, % that are genuine wing-8 shared days
- W6 PnL shown (wing-6 is the fill-proxy for would-be wing-8 trades on those days)

---
## NIFTY

### Feature Distributions: Shared vs Skipped

n_shared=326, n_skipped=472, availability=40.9%

| Feature | Shared median | Skipped median | Shared IQR | Skipped IQR | Delta |
|---|---:|---:|---|---|---:|
| VIX at entry | 13.69 | 14.39 | 11.90–15.88 | 12.40–17.92 | -0.70 |
| Intraday range% (same day) | 0.70 | 0.93 | 0.54–0.94 | 0.70–1.28 | -0.23 |
| Prior-day range% | 0.79 | 0.93 | 0.57–1.16 | 0.68–1.25 | -0.15 |
| 5-day HV (rolling avg range%) | 0.84 | 0.94 | 0.69–1.12 | 0.76–1.20 | -0.11 |
| DTE at entry | 2.00 | 3.00 | 1.00–5.00 | 2.00–5.00 | -1.00 |

### Best Single-Feature Thresholds (F1 for shared=1)

| Feature | Best threshold | Direction | F1 |
|---|---:|---|---:|
| VIX at entry | 20.66 | low | 0.573 |
| Prior-day range% | 1.62 | low | 0.582 |
| 5-day avg range% | 1.22 | low | 0.574 |
| DTE at entry | 6.00 | low | 0.580 |

> F1 measures how well each single threshold separates traded days from skipped days.
> Perfect = 1.0. Random = ~0.5 at equal class size.

### Availability by (VIX, Prior-day Range) Cell

(Shows what % of entries in each regime cell were shared — the 'calm quadrant')

| VIX | Prev-day range | Shared | Skipped | Avail% |
|---|---|---:|---:|---:|
| <13 | <0.6% | 57 | 50 | 53.3% |
| <13 | 0.6-0.9% | 54 | 55 | 49.5% |
| <13 | 0.9-1.3% | 18 | 36 | 33.3% |
| <13 | >1.3% | 3 | 10 | 23.1% |
| 13-17 | <0.6% | 26 | 26 | 50.0% |
| 13-17 | 0.6-0.9% | 43 | 50 | 46.2% |
| 13-17 | 0.9-1.3% | 31 | 67 | 31.6% |
| 13-17 | >1.3% | 23 | 45 | 33.8% |
| 17-22 | <0.6% | 4 | 8 | 33.3% |
| 17-22 | 0.6-0.9% | 13 | 23 | 36.1% |
| 17-22 | 0.9-1.3% | 12 | 35 | 25.5% |
| 17-22 | >1.3% | 23 | 27 | 46.0% |
| 22+ | <0.6% | 2 | 0 | 100.0% |
| 22+ | 0.6-0.9% | 1 | 2 | 33.3% |
| 22+ | 0.9-1.3% | 3 | 9 | 25.0% |
| 22+ | >1.3% | 12 | 23 | 34.3% |

### W6 PnL by (VIX, Prior-day Range) Regime Cell

(Wing-6 PnL as proxy — shows which regime cells are actually profitable for ICs)

| VIX | Prev-day range | N trades | Avg PnL | Total PnL | Win% |
|---|---|---:|---:|---:|---:|
| <13 | <0.6% | 107 | INR-11 | INR-1,227 | 55% |
| <13 | 0.6-0.9% | 109 | INR-30 | INR-3,316 | 58% |
| <13 | 0.9-1.3% | 54 | INR+80 | INR+4,318 | 56% |
| <13 | >1.3% | 13 | INR+152 | INR+1,977 | 62% |
| 13-17 | <0.6% | 52 | INR+278 | INR+14,479 | 71% |
| 13-17 | 0.6-0.9% | 93 | INR+98 | INR+9,154 | 53% |
| 13-17 | 0.9-1.3% | 98 | INR+184 | INR+18,022 | 63% |
| 13-17 | >1.3% | 68 | INR+374 | INR+25,437 | 63% |
| 17-22 | <0.6% | 12 | INR-280 | INR-3,365 | 50% |
| 17-22 | 0.6-0.9% | 36 | INR+197 | INR+7,092 | 50% |
| 17-22 | 0.9-1.3% | 47 | INR-41 | INR-1,911 | 45% |
| 17-22 | >1.3% | 50 | INR+333 | INR+16,625 | 60% |
| 22+ | <0.6% | 2 | INR+273 | INR+546 | 100% |
| 22+ | 0.6-0.9% | 3 | INR+626 | INR+1,877 | 100% |
| 22+ | 0.9-1.3% | 12 | INR+251 | INR+3,010 | 83% |
| 22+ | >1.3% | 35 | INR+67 | INR+2,340 | 51% |

### Filter Comparison

(All filters applied to wing-6 universe — shows what W6 would earn if we applied the filter)

| Filter | Capture% | TPR | FPR | Precision | W6 PnL/trade | W6 total PnL | Win rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| VIX at entry low 20.66 | 90% | 92% | 89% | 42% | INR+120 | INR+86,049 | 58% |
| Prior-day range% low 1.62 | 89% | 93% | 87% | 42% | INR+91 | INR+64,761 | 57% |
| 5-day avg range% low 1.22 | 79% | 84% | 76% | 43% | INR+86 | INR+54,277 | 57% |
| DTE at entry low 6.00 | 100% | 100% | 100% | 41% | INR+117 | INR+93,023 | 58% |
| VIX + prior-range (combined) | 83% | 86% | 81% | 42% | INR+92 | INR+61,493 | 57% |
| VIX + hv5 (combined) | 77% | 82% | 73% | 44% | INR+91 | INR+55,607 | 57% |

### Actual Wing-8 Trades — Best/Worst 15 with Market Conditions

| entry_date | expiry | dte_at_entry | vix_entry | range_today | range_prev | hv5 | net_pnl |
|---|---|---|---|---|---|---|---|
| 2025-02-04 | 2025-02-06 | 2.00 | 14.21 | 1.44 | 0.68 | 0.97 | -3,893 |
| 2025-01-01 | 2025-01-02 | 1.00 | 14.46 | 1.10 | 0.97 | 0.90 | -2,365 |
| 2024-12-31 | 2025-01-02 | 2.00 | 14.26 | 0.97 | 1.33 | 0.89 | -2,106 |
| 2024-12-30 | 2025-01-02 | 3.00 | 13.70 | 1.33 | 0.58 | 1.07 | -1,742 |
| 2025-04-08 | 2025-04-09 | 1.00 | 20.10 | 1.90 | 2.35 | 1.45 | -1,197 |
| 2025-05-21 | 2025-05-22 | 1.00 | 17.48 | 1.05 | 1.36 | 1.18 | -1,141 |
| 2026-01-30 | 2026-02-03 | 4.00 | 13.73 | 0.62 | 1.18 | 1.10 | -1,050 |
| 2026-04-15 | 2026-04-21 | 6.00 | 18.29 | - | - | - | -890 |
| 2022-07-27 | 2022-07-28 | 1.00 | 18.52 | 1.29 | 1.03 | 0.83 | -597 |
| 2022-10-31 | 2022-11-03 | 3.00 | 15.88 | 0.68 | 0.65 | 0.71 | -578 |
| 2025-04-16 | 2025-04-17 | 1.00 | 15.43 | 0.77 | 0.69 | 1.29 | -566 |
| 2022-11-01 | 2022-11-03 | 2.00 | 15.48 | 0.63 | 0.68 | 0.68 | -546 |
| 2024-09-18 | 2024-09-19 | 1.00 | 12.67 | 0.77 | 0.35 | 0.84 | -536 |
| 2026-03-04 | 2026-03-10 | 6.00 | 20.03 | 1.22 | 1.56 | 1.13 | -434 |
| 2022-03-14 | 2022-03-17 | 3.00 | 25.83 | 1.68 | 1.34 | 2.20 | -361 |
| 2025-10-13 | 2025-10-14 | 1.00 | 11.12 | 0.46 | 0.69 | 0.71 | +1,848 |
| 2022-03-02 | 2022-03-03 | 1.00 | 29.98 | 1.20 | 2.78 | 2.05 | +2,059 |
| 2025-10-23 | 2025-10-28 | 5.00 | 11.81 | 0.93 | 0.42 | 0.76 | +2,084 |
| 2025-07-08 | 2025-07-10 | 2.00 | 12.56 | 0.49 | 0.32 | 0.58 | +2,126 |
| 2025-01-14 | 2025-01-16 | 2.00 | 15.76 | 0.56 | 1.27 | 0.97 | +2,315 |
| 2024-12-06 | 2024-12-12 | 6.00 | 14.89 | 0.53 | 2.29 | 1.25 | +2,743 |
| 2025-08-29 | 2025-09-02 | 4.00 | 11.91 | 0.69 | 0.90 | 0.72 | +2,756 |
| 2025-06-04 | 2025-06-05 | 1.00 | 16.17 | 0.46 | 1.38 | 0.86 | +2,781 |
| 2025-02-07 | 2025-02-13 | 6.00 | 14.48 | 1.06 | 0.91 | 0.98 | +3,002 |
| 2022-02-01 | 2022-02-03 | 2.00 | 21.84 | 2.15 | 0.84 | 2.12 | +3,106 |
| 2025-01-07 | 2025-01-09 | 2.00 | 15.21 | 0.66 | 2.24 | 1.44 | +3,152 |
| 2025-05-05 | 2025-05-08 | 3.00 | 18.49 | 0.51 | 1.44 | 1.26 | +3,199 |
| 2025-02-25 | 2025-02-27 | 2.00 | 14.37 | 0.49 | 0.66 | 0.78 | +3,219 |
| 2025-03-28 | 2025-04-03 | 6.00 | 13.03 | 0.84 | 1.00 | 1.13 | +3,269 |
| 2025-01-22 | 2025-01-23 | 1.00 | 17.20 | 0.81 | 1.92 | 0.97 | +5,084 |

---
## BANKNIFTY

### Feature Distributions: Shared vs Skipped

n_shared=253, n_skipped=537, availability=32.0%

| Feature | Shared median | Skipped median | Shared IQR | Skipped IQR | Delta |
|---|---:|---:|---|---|---:|
| VIX at entry | 13.02 | 14.00 | 11.48–15.35 | 12.20–17.64 | -0.98 |
| Intraday range% (same day) | 0.89 | 1.08 | 0.65–1.20 | 0.82–1.52 | -0.18 |
| Prior-day range% | 0.94 | 1.13 | 0.71–1.39 | 0.81–1.51 | -0.19 |
| 5-day HV (rolling avg range%) | 1.05 | 1.16 | 0.87–1.34 | 0.91–1.48 | -0.11 |
| DTE at entry | 13.00 | 14.00 | 7.00–20.00 | 7.00–21.00 | -1.00 |

### Best Single-Feature Thresholds (F1 for shared=1)

| Feature | Best threshold | Direction | F1 |
|---|---:|---|---:|
| VIX at entry | 17.82 | low | 0.506 |
| Prior-day range% | 2.00 | low | 0.485 |
| 5-day avg range% | 1.63 | low | 0.494 |
| DTE at entry | 27.00 | low | 0.485 |

> F1 measures how well each single threshold separates traded days from skipped days.
> Perfect = 1.0. Random = ~0.5 at equal class size.

### Availability by (VIX, Prior-day Range) Cell

(Shows what % of entries in each regime cell were shared — the 'calm quadrant')

| VIX | Prev-day range | Shared | Skipped | Avail% |
|---|---|---:|---:|---:|
| <13 | <0.6% | 34 | 34 | 50.0% |
| <13 | 0.6-0.9% | 44 | 72 | 37.9% |
| <13 | 0.9-1.3% | 36 | 60 | 37.5% |
| <13 | >1.3% | 11 | 22 | 33.3% |
| 13-17 | <0.6% | 5 | 11 | 31.2% |
| 13-17 | 0.6-0.9% | 24 | 45 | 34.8% |
| 13-17 | 0.9-1.3% | 24 | 65 | 27.0% |
| 13-17 | >1.3% | 35 | 81 | 30.2% |
| 17-22 | <0.6% | 1 | 0 | 100.0% |
| 17-22 | 0.6-0.9% | 6 | 10 | 37.5% |
| 17-22 | 0.9-1.3% | 7 | 34 | 17.1% |
| 17-22 | >1.3% | 19 | 69 | 21.6% |
| 22+ | <0.6% | 0 | 1 | 0.0% |
| 22+ | 0.6-0.9% | 0 | 4 | 0.0% |
| 22+ | 0.9-1.3% | 0 | 1 | 0.0% |
| 22+ | >1.3% | 7 | 28 | 20.0% |

### W6 PnL by (VIX, Prior-day Range) Regime Cell

(Wing-6 PnL as proxy — shows which regime cells are actually profitable for ICs)

| VIX | Prev-day range | N trades | Avg PnL | Total PnL | Win% |
|---|---|---:|---:|---:|---:|
| <13 | <0.6% | 68 | INR-197 | INR-13,421 | 24% |
| <13 | 0.6-0.9% | 116 | INR-207 | INR-24,041 | 23% |
| <13 | 0.9-1.3% | 96 | INR-169 | INR-16,246 | 23% |
| <13 | >1.3% | 33 | INR-107 | INR-3,517 | 27% |
| 13-17 | <0.6% | 16 | INR+52 | INR+824 | 44% |
| 13-17 | 0.6-0.9% | 69 | INR-231 | INR-15,906 | 20% |
| 13-17 | 0.9-1.3% | 89 | INR-63 | INR-5,632 | 31% |
| 13-17 | >1.3% | 116 | INR-127 | INR-14,748 | 25% |
| 17-22 | <0.6% | 1 | INR-434 | INR-434 | 0% |
| 17-22 | 0.6-0.9% | 16 | INR-324 | INR-5,181 | 19% |
| 17-22 | 0.9-1.3% | 41 | INR-380 | INR-15,599 | 17% |
| 17-22 | >1.3% | 88 | INR-334 | INR-29,418 | 23% |
| 22+ | <0.6% | 1 | INR+66 | INR+66 | 100% |
| 22+ | 0.6-0.9% | 4 | INR-199 | INR-797 | 50% |
| 22+ | 0.9-1.3% | 1 | INR+917 | INR+917 | 100% |
| 22+ | >1.3% | 35 | INR-236 | INR-8,250 | 34% |

### Filter Comparison

(All filters applied to wing-6 universe — shows what W6 would earn if we applied the filter)

| Filter | Capture% | TPR | FPR | Precision | W6 PnL/trade | W6 total PnL | Win rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| VIX at entry low 17.82 | 80% | 88% | 76% | 35% | INR-163 | INR-102,769 | 25% |
| Prior-day range% low 2.00 | 90% | 92% | 89% | 33% | INR-195 | INR-138,616 | 24% |
| 5-day avg range% low 1.63 | 85% | 90% | 82% | 34% | INR-182 | INR-122,303 | 25% |
| DTE at entry low 27.00 | 93% | 95% | 92% | 33% | INR-171 | INR-125,951 | 27% |
| VIX + prior-range (combined) | 75% | 83% | 71% | 36% | INR-169 | INR-100,619 | 24% |
| VIX + hv5 (combined) | 75% | 84% | 71% | 36% | INR-166 | INR-98,520 | 25% |

### Actual Wing-8 Trades — Best/Worst 15 with Market Conditions

| entry_date | expiry | dte_at_entry | vix_entry | range_today | range_prev | hv5 | net_pnl |
|---|---|---|---|---|---|---|---|
| 2026-04-02 | 2026-04-28 | 26.00 | 26.23 | 3.51 | 1.73 | 2.19 | -2,250 |
| 2022-03-15 | 2022-03-31 | 16.00 | 25.78 | 2.64 | 2.29 | 2.86 | -1,284 |
| 2026-04-30 | 2026-05-26 | 26.00 | 18.28 | 1.22 | 1.60 | 1.32 | -1,242 |
| 2025-04-08 | 2025-04-24 | 16.00 | 20.10 | 1.51 | 2.57 | 1.58 | -994 |
| 2022-03-22 | 2022-03-31 | 9.00 | 24.86 | 2.99 | 1.84 | 1.69 | -909 |
| 2025-05-30 | 2025-06-26 | 27.00 | 16.23 | 0.82 | 1.23 | 1.05 | -896 |
| 2025-10-29 | 2025-11-25 | 27.00 | 11.97 | 0.66 | 0.94 | 0.95 | -826 |
| 2022-06-14 | 2022-06-30 | 16.00 | 22.10 | 1.48 | 1.63 | 1.37 | -802 |
| 2025-06-06 | 2025-06-26 | 20.00 | 15.01 | 2.08 | 0.77 | 0.91 | -769 |
| 2025-01-31 | 2025-02-27 | 27.00 | 17.88 | 1.31 | 0.80 | 1.10 | -733 |
| 2025-07-04 | 2025-07-31 | 27.00 | 12.47 | 0.79 | 0.75 | 0.87 | -706 |
| 2026-01-30 | 2026-02-24 | 25.00 | 13.73 | 0.68 | 1.21 | 1.46 | -687 |
| 2025-02-06 | 2025-02-27 | 21.00 | 14.22 | 0.80 | 0.61 | 1.30 | -682 |
| 2025-01-02 | 2025-01-30 | 28.00 | 14.73 | 1.33 | 1.65 | 1.42 | -673 |
| 2026-03-04 | 2026-03-30 | 26.00 | 20.03 | 1.14 | 1.74 | 1.08 | -666 |
| 2023-06-26 | 2023-06-27 | 1.00 | 11.84 | 0.52 | 0.69 | 0.88 | +990 |
| 2022-11-22 | 2022-11-24 | 2.00 | 15.19 | 0.37 | 0.43 | 0.69 | +992 |
| 2022-04-26 | 2022-04-28 | 2.00 | 19.87 | 0.92 | 2.05 | 1.88 | +1,066 |
| 2025-01-22 | 2025-01-30 | 8.00 | 17.20 | 1.45 | 2.25 | 1.55 | +1,105 |
| 2023-02-21 | 2023-02-23 | 2.00 | 13.94 | 1.07 | 1.73 | 1.28 | +1,170 |
| 2022-08-24 | 2022-08-25 | 1.00 | 19.08 | 1.47 | 2.41 | 1.54 | +1,340 |
| 2025-07-29 | 2025-07-31 | 2.00 | 12.14 | 0.81 | 1.03 | 1.02 | +1,441 |
| 2024-03-26 | 2024-03-27 | 1.00 | 13.02 | 0.54 | 0.86 | 1.15 | +1,547 |
| 2025-10-23 | 2025-10-28 | 5.00 | 11.81 | 1.07 | 0.46 | 0.77 | +1,701 |
| 2023-03-27 | 2023-03-29 | 2.00 | 15.79 | 1.06 | 1.19 | 1.28 | +1,728 |
| 2022-02-23 | 2022-02-24 | 1.00 | 25.17 | 1.20 | 2.05 | 1.89 | +1,741 |
| 2025-07-30 | 2025-07-31 | 1.00 | 11.64 | 0.46 | 0.81 | 0.97 | +1,865 |
| 2022-12-28 | 2022-12-29 | 1.00 | 15.51 | 0.78 | 1.23 | 2.04 | +1,902 |
| 2024-04-30 | 2024-05-29 | 29.00 | 12.77 | 1.47 | 2.34 | 1.34 | +2,159 |
| 2024-12-23 | 2024-12-24 | 1.00 | 14.93 | 0.76 | 1.98 | 1.36 | +4,360 |

---
## FINNIFTY

### Feature Distributions: Shared vs Skipped

n_shared=51, n_skipped=144, availability=26.2%

| Feature | Shared median | Skipped median | Shared IQR | Skipped IQR | Delta |
|---|---:|---:|---|---|---:|
| VIX at entry | 13.87 | 13.81 | 11.88–15.43 | 12.04–14.96 | +0.06 |
| Intraday range% (same day) | 0.87 | 1.04 | 0.67–1.18 | 0.77–1.42 | -0.18 |
| Prior-day range% | 1.07 | 1.07 | 0.72–1.38 | 0.80–1.43 | +0.00 |
| 5-day HV (rolling avg range%) | 1.08 | 1.12 | 0.88–1.28 | 0.91–1.33 | -0.04 |
| DTE at entry | 5.00 | 5.50 | 2.00–6.00 | 4.00–9.00 | -0.50 |

### Best Single-Feature Thresholds (F1 for shared=1)

| Feature | Best threshold | Direction | F1 |
|---|---:|---|---:|
| VIX at entry | 17.01 | low | 0.407 |
| Prior-day range% | 1.74 | low | 0.425 |
| 5-day avg range% | 1.49 | low | 0.434 |
| DTE at entry | 7.00 | low | 0.462 |

> F1 measures how well each single threshold separates traded days from skipped days.
> Perfect = 1.0. Random = ~0.5 at equal class size.

### Availability by (VIX, Prior-day Range) Cell

(Shows what % of entries in each regime cell were shared — the 'calm quadrant')

| VIX | Prev-day range | Shared | Skipped | Avail% |
|---|---|---:|---:|---:|
| <13 | <0.6% | 4 | 9 | 30.8% |
| <13 | 0.6-0.9% | 7 | 21 | 25.0% |
| <13 | 0.9-1.3% | 9 | 15 | 37.5% |
| <13 | >1.3% | 2 | 9 | 18.2% |
| 13-17 | <0.6% | 1 | 3 | 25.0% |
| 13-17 | 0.6-0.9% | 3 | 19 | 13.6% |
| 13-17 | 0.9-1.3% | 7 | 22 | 24.1% |
| 13-17 | >1.3% | 13 | 31 | 29.5% |
| 17-22 | 0.6-0.9% | 1 | 1 | 50.0% |
| 17-22 | 0.9-1.3% | 1 | 6 | 14.3% |
| 17-22 | >1.3% | 2 | 6 | 25.0% |
| 22+ | <0.6% | 0 | 1 | 0.0% |
| 22+ | 0.9-1.3% | 0 | 1 | 0.0% |
| 22+ | >1.3% | 1 | 0 | 100.0% |

### W6 PnL by (VIX, Prior-day Range) Regime Cell

(Wing-6 PnL as proxy — shows which regime cells are actually profitable for ICs)

| VIX | Prev-day range | N trades | Avg PnL | Total PnL | Win% |
|---|---|---:|---:|---:|---:|
| <13 | <0.6% | 13 | INR-119 | INR-1,546 | 38% |
| <13 | 0.6-0.9% | 28 | INR+83 | INR+2,327 | 54% |
| <13 | 0.9-1.3% | 24 | INR-18 | INR-436 | 46% |
| <13 | >1.3% | 11 | INR+70 | INR+768 | 64% |
| 13-17 | <0.6% | 4 | INR+89 | INR+355 | 50% |
| 13-17 | 0.6-0.9% | 22 | INR+207 | INR+4,546 | 55% |
| 13-17 | 0.9-1.3% | 29 | INR+124 | INR+3,586 | 48% |
| 13-17 | >1.3% | 44 | INR+166 | INR+7,305 | 50% |
| 17-22 | 0.6-0.9% | 2 | INR-840 | INR-1,679 | 50% |
| 17-22 | 0.9-1.3% | 7 | INR+391 | INR+2,736 | 57% |
| 17-22 | >1.3% | 8 | INR+621 | INR+4,970 | 62% |
| 22+ | <0.6% | 1 | INR+219 | INR+219 | 100% |
| 22+ | 0.9-1.3% | 1 | INR+289 | INR+289 | 100% |
| 22+ | >1.3% | 1 | INR+1,345 | INR+1,345 | 100% |

### Filter Comparison

(All filters applied to wing-6 universe — shows what W6 would earn if we applied the filter)

| Filter | Capture% | TPR | FPR | Precision | W6 PnL/trade | W6 total PnL | Win rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| VIX at entry low 17.01 | 90% | 90% | 90% | 26% | INR+97 | INR+16,904 | 50% |
| Prior-day range% low 1.74 | 90% | 94% | 88% | 27% | INR+104 | INR+18,220 | 51% |
| 5-day avg range% low 1.49 | 90% | 96% | 88% | 28% | INR+117 | INR+20,518 | 51% |
| DTE at entry low 7.00 | 76% | 90% | 71% | 31% | INR+270 | INR+39,919 | 60% |
| VIX + prior-range (combined) | 81% | 88% | 78% | 28% | INR+78 | INR+12,291 | 50% |
| VIX + hv5 (combined) | 82% | 88% | 80% | 28% | INR+110 | INR+17,658 | 51% |

### Actual Wing-8 Trades — Best/Worst 15 with Market Conditions

| entry_date | expiry | dte_at_entry | vix_entry | range_today | range_prev | hv5 | net_pnl |
|---|---|---|---|---|---|---|---|
| 2025-01-14 | 2025-01-30 | 16.00 | 15.76 | 1.35 | 1.34 | 1.36 | -768 |
| 2024-12-05 | 2024-12-31 | 26.00 | 14.60 | 2.19 | 1.24 | 1.21 | -665 |
| 2026-02-04 | 2026-02-24 | 20.00 | 12.98 | 1.04 | 2.59 | 2.00 | -623 |
| 2023-06-21 | 2023-06-27 | 6.00 | 11.15 | 0.64 | 1.14 | 1.04 | -476 |
| 2024-11-29 | 2024-12-31 | 32.00 | 15.29 | 0.76 | 2.16 | 1.29 | -432 |
| 2023-03-22 | 2023-03-28 | 6.00 | 14.71 | 0.61 | 1.32 | 1.75 | -413 |
| 2025-04-16 | 2025-04-24 | 8.00 | 15.43 | 0.89 | 1.18 | 1.66 | -379 |
| 2024-04-26 | 2024-04-30 | 4.00 | 11.00 | 1.09 | 1.61 | 1.30 | -360 |
| 2024-11-22 | 2024-11-26 | 4.00 | 15.73 | 1.52 | 1.42 | 1.30 | -353 |
| 2023-08-23 | 2023-08-29 | 6.00 | 11.69 | 1.18 | 0.65 | 0.69 | -289 |
| 2025-01-23 | 2025-01-30 | 7.00 | 17.04 | 0.69 | 1.38 | 1.49 | -276 |
| 2022-10-20 | 2022-10-25 | 5.00 | 17.51 | 0.97 | 1.07 | 1.15 | -222 |
| 2024-12-11 | 2024-12-31 | 20.00 | 13.69 | 0.68 | 0.63 | 1.22 | -210 |
| 2025-07-03 | 2025-07-31 | 28.00 | 12.34 | 0.87 | 1.58 | 1.14 | -166 |
| 2025-03-12 | 2025-03-27 | 15.00 | 14.03 | 0.71 | 1.41 | 1.04 | -144 |
| 2024-02-23 | 2024-02-27 | 4.00 | 15.35 | 0.71 | 1.30 | 1.12 | +900 |
| 2023-03-24 | 2023-03-28 | 4.00 | 14.31 | 1.18 | 1.40 | 1.37 | +937 |
| 2024-03-19 | 2024-03-26 | 7.00 | 14.17 | 0.48 | 1.38 | 1.36 | +947 |
| 2024-03-22 | 2024-03-26 | 4.00 | 12.64 | 0.85 | 0.80 | 1.02 | +996 |
| 2023-02-23 | 2023-02-28 | 5.00 | 15.50 | 1.22 | 1.59 | 1.28 | +1,010 |
| 2025-01-24 | 2025-01-30 | 6.00 | 17.04 | 1.23 | 0.69 | 1.49 | +1,152 |
| 2024-10-28 | 2024-10-29 | 1.00 | 14.86 | 0.87 | 1.84 | 1.47 | +1,203 |
| 2022-10-21 | 2022-10-25 | 4.00 | 16.96 | 1.02 | 0.97 | 1.07 | +1,295 |
| 2023-07-24 | 2023-07-25 | 1.00 | 11.85 | 0.67 | 0.83 | 1.18 | +1,536 |
| 2023-03-27 | 2023-03-28 | 1.00 | 15.79 | 0.97 | 1.18 | 1.23 | +1,752 |
| 2025-07-30 | 2025-07-31 | 1.00 | 11.64 | 0.54 | 0.92 | 0.97 | +1,761 |
| 2025-06-25 | 2025-06-26 | 1.00 | 13.34 | 0.47 | 1.11 | 1.00 | +1,843 |
| 2025-09-29 | 2025-09-30 | 1.00 | 11.64 | 0.87 | 0.96 | 0.79 | +2,127 |
| 2022-09-26 | 2022-09-27 | 1.00 | 22.03 | 1.71 | 2.49 | 1.67 | +2,286 |
| 2024-11-25 | 2024-11-26 | 1.00 | 16.04 | 0.85 | 1.52 | 1.11 | +3,281 |

---
## MIDCPNIFTY

### Feature Distributions: Shared vs Skipped

n_shared=34, n_skipped=149, availability=18.6%

| Feature | Shared median | Skipped median | Shared IQR | Skipped IQR | Delta |
|---|---:|---:|---|---|---:|
| VIX at entry | 13.37 | 13.07 | 11.82–14.90 | 11.28–14.60 | +0.30 |
| Intraday range% (same day) | 0.99 | 1.18 | 0.76–1.25 | 0.89–1.69 | -0.19 |
| Prior-day range% | 0.98 | 1.30 | 0.74–1.60 | 0.91–1.92 | -0.32 |
| 5-day HV (rolling avg range%) | 1.22 | 1.28 | 1.04–1.65 | 1.02–1.69 | -0.05 |
| DTE at entry | 5.00 | 8.00 | 2.25–7.00 | 4.00–20.00 | -3.00 |

### Best Single-Feature Thresholds (F1 for shared=1)

| Feature | Best threshold | Direction | F1 |
|---|---:|---|---:|
| VIX at entry | 16.25 | low | 0.293 |
| Prior-day range% | 1.24 | low | 0.365 |
| 5-day avg range% | 1.75 | low | 0.344 |
| DTE at entry | 7.00 | low | 0.397 |

> F1 measures how well each single threshold separates traded days from skipped days.
> Perfect = 1.0. Random = ~0.5 at equal class size.

### Availability by (VIX, Prior-day Range) Cell

(Shows what % of entries in each regime cell were shared — the 'calm quadrant')

| VIX | Prev-day range | Shared | Skipped | Avail% |
|---|---|---:|---:|---:|
| <13 | <0.6% | 2 | 7 | 22.2% |
| <13 | 0.6-0.9% | 5 | 17 | 22.7% |
| <13 | 0.9-1.3% | 4 | 23 | 14.8% |
| <13 | >1.3% | 2 | 26 | 7.1% |
| 13-17 | <0.6% | 2 | 2 | 50.0% |
| 13-17 | 0.6-0.9% | 4 | 10 | 28.6% |
| 13-17 | 0.9-1.3% | 4 | 13 | 23.5% |
| 13-17 | >1.3% | 7 | 39 | 15.2% |
| 17-22 | <0.6% | 0 | 1 | 0.0% |
| 17-22 | 0.6-0.9% | 1 | 0 | 100.0% |
| 17-22 | 0.9-1.3% | 0 | 2 | 0.0% |
| 17-22 | >1.3% | 1 | 7 | 12.5% |
| 22+ | <0.6% | 1 | 0 | 100.0% |
| 22+ | 0.9-1.3% | 0 | 1 | 0.0% |
| 22+ | >1.3% | 1 | 1 | 50.0% |

### W6 PnL by (VIX, Prior-day Range) Regime Cell

(Wing-6 PnL as proxy — shows which regime cells are actually profitable for ICs)

| VIX | Prev-day range | N trades | Avg PnL | Total PnL | Win% |
|---|---|---:|---:|---:|---:|
| <13 | <0.6% | 9 | INR-122 | INR-1,100 | 56% |
| <13 | 0.6-0.9% | 22 | INR-191 | INR-4,198 | 27% |
| <13 | 0.9-1.3% | 27 | INR-98 | INR-2,655 | 44% |
| <13 | >1.3% | 28 | INR-32 | INR-902 | 43% |
| 13-17 | <0.6% | 4 | INR-449 | INR-1,796 | 25% |
| 13-17 | 0.6-0.9% | 14 | INR-79 | INR-1,100 | 43% |
| 13-17 | 0.9-1.3% | 17 | INR-253 | INR-4,309 | 18% |
| 13-17 | >1.3% | 46 | INR-132 | INR-6,054 | 35% |
| 17-22 | <0.6% | 1 | INR+54 | INR+54 | 100% |
| 17-22 | 0.6-0.9% | 1 | INR+2,912 | INR+2,912 | 100% |
| 17-22 | 0.9-1.3% | 2 | INR+260 | INR+520 | 50% |
| 17-22 | >1.3% | 8 | INR-950 | INR-7,602 | 0% |
| 22+ | <0.6% | 1 | INR-318 | INR-318 | 0% |
| 22+ | 0.9-1.3% | 1 | INR+202 | INR+202 | 100% |
| 22+ | >1.3% | 2 | INR-710 | INR-1,420 | 50% |

### Filter Comparison

(All filters applied to wing-6 universe — shows what W6 would earn if we applied the filter)

| Filter | Capture% | TPR | FPR | Precision | W6 PnL/trade | W6 total PnL | Win rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| VIX at entry low 16.25 | 90% | 85% | 91% | 18% | INR-132 | INR-21,691 | 36% |
| Prior-day range% low 1.24 | 50% | 68% | 46% | 25% | INR-114 | INR-10,520 | 39% |
| 5-day avg range% low 1.75 | 80% | 91% | 77% | 21% | INR-93 | INR-13,608 | 40% |
| DTE at entry low 7.00 | 53% | 76% | 48% | 27% | INR+247 | INR+23,983 | 60% |
| VIX + prior-range (combined) | 46% | 59% | 44% | 24% | INR-157 | INR-13,358 | 36% |
| VIX + hv5 (combined) | 72% | 79% | 70% | 20% | INR-85 | INR-11,177 | 39% |

### Actual Wing-8 Trades — Best/Worst 15 with Market Conditions

| entry_date | expiry | dte_at_entry | vix_entry | range_today | range_prev | hv5 | net_pnl |
|---|---|---|---|---|---|---|---|
| 2025-08-07 | 2025-08-28 | 21.00 | 11.87 | 1.97 | 1.75 | 1.68 | -1,856 |
| 2024-12-04 | 2024-12-30 | 26.00 | 14.52 | 1.21 | 0.66 | 0.97 | -1,446 |
| 2026-02-25 | 2026-03-30 | 33.00 | 13.45 | 0.99 | 1.33 | 1.36 | -1,380 |
| 2026-03-04 | 2026-03-30 | 26.00 | 20.03 | 1.40 | 3.17 | 1.59 | -1,147 |
| 2025-11-27 | 2025-12-30 | 33.00 | 11.89 | 0.70 | 1.51 | 1.04 | -1,064 |
| 2026-03-24 | 2026-03-30 | 6.00 | 25.71 | 2.81 | 2.76 | 2.10 | -1,008 |
| 2025-10-09 | 2025-10-28 | 19.00 | 10.38 | 1.01 | 1.23 | 1.06 | -824 |
| 2025-06-24 | 2025-06-26 | 2.00 | 13.48 | 0.86 | 1.74 | 1.73 | -806 |
| 2025-12-10 | 2025-12-30 | 20.00 | 10.94 | 2.21 | 2.11 | 1.60 | -767 |
| 2025-03-07 | 2025-03-27 | 20.00 | 13.77 | 1.25 | 1.21 | 2.20 | -734 |
| 2025-11-13 | 2025-11-25 | 12.00 | 11.75 | 1.11 | 1.15 | 1.34 | -578 |
| 2025-12-15 | 2025-12-30 | 15.00 | 10.62 | 0.83 | 1.18 | 1.91 | -543 |
| 2025-11-28 | 2025-12-30 | 32.00 | 11.06 | 0.49 | 0.70 | 1.04 | -367 |
| 2024-12-26 | 2024-12-30 | 4.00 | 13.39 | 1.12 | 1.11 | 1.71 | -283 |
| 2025-01-16 | 2025-01-30 | 14.00 | 15.03 | 0.76 | 1.67 | 2.02 | -263 |
| 2025-12-29 | 2025-12-30 | 1.00 | 9.66 | 1.00 | 1.21 | 1.05 | +588 |
| 2023-11-23 | 2023-11-24 | 1.00 | 11.81 | 0.39 | 0.89 | 0.84 | +618 |
| 2026-01-22 | 2026-01-27 | 5.00 | 13.32 | 1.65 | 2.72 | 1.79 | +619 |
| 2024-02-21 | 2024-02-26 | 5.00 | 16.20 | 1.79 | 0.70 | 1.16 | +705 |
| 2025-12-23 | 2025-12-30 | 7.00 | 9.67 | 0.66 | 0.92 | 1.11 | +805 |
| 2025-11-20 | 2025-11-25 | 5.00 | 11.88 | 0.71 | 1.01 | 0.93 | +826 |
| 2023-09-27 | 2023-10-30 | 33.00 | 11.39 | 0.75 | 0.81 | 1.41 | +855 |
| 2024-08-19 | 2024-08-26 | 7.00 | 14.78 | 1.27 | 1.28 | 1.27 | +895 |
| 2023-12-19 | 2023-12-22 | 3.00 | 13.87 | 1.21 | 0.83 | 1.11 | +1,059 |
| 2023-11-22 | 2023-11-24 | 2.00 | 12.39 | 0.89 | 0.53 | 0.77 | +1,128 |
| 2024-06-21 | 2024-06-24 | 3.00 | 13.35 | 0.97 | 1.35 | 1.12 | +1,164 |
| 2025-06-25 | 2025-06-26 | 1.00 | 13.34 | 0.46 | 0.86 | 1.65 | +1,696 |
| 2025-04-23 | 2025-04-24 | 1.00 | 15.77 | 1.92 | 1.70 | 1.67 | +2,205 |
| 2025-07-30 | 2025-07-31 | 1.00 | 11.64 | 0.86 | 1.39 | 1.53 | +3,323 |
| 2025-05-28 | 2025-05-29 | 1.00 | 18.92 | 1.08 | 0.89 | 1.08 | +4,307 |

---
## Cross-Symbol Synthesis

### Summary of best thresholds found

| Symbol | Best VIX threshold | Best range_prev threshold | Best hv5 threshold |
|---|---:|---:|---:|
| NIFTY | ≤ 20.7 | ≤ 1.62% | ≤ 1.22% |
| BANKNIFTY | ≤ 17.8 | ≤ 2.00% | ≤ 1.63% |
| FINNIFTY | ≤ 17.0 | ≤ 1.74% | ≤ 1.49% |
| MIDCPNIFTY | ≤ 16.2 | ≤ 1.24% | ≤ 1.75% |

### Practical Filter Recommendation

If thresholds are consistent across symbols, a single rule set can serve all 4 indices.
The combined (VIX + prior-range) filter precision column shows: of days we'd enter
on, what fraction coincides with the days wing-8 naturally selected (the good days).

**Critical caveats**:
1. These thresholds are fit on in-sample data (2021–2026). They MUST be validated
   on a held-out period before deployment.
2. The filter's value may already be captured by wing-8's natural selection — running
   wing-8 with explicit data check is equivalent. A filter adds value only if you
   want to trade wing-6/wing-4 but only on the high-quality subset of days.
3. The regime cells with highest W6 PnL (low VIX, low prior-range) represent the
   'calm sell' environment where short-vol is most reliable. This is not news — but
   the specific threshold values are now data-backed for this strategy and dataset.
4. Prior-day range is likely more useful than same-day VIX because it is fully
   pre-market and not subject to gap-open VIX spikes that normalise by 9:20.
# DTE<=7 Monthly IC — Skip Diagnosis (v2)

Candidate days = all trading days within DTE 1–7 of a monthly expiry, 2022-02 to 2026-04.
Reads `month/expiry_code_1/` parquets — same path as engine with `expiry_type=month`.
Liquidity gates mirror engine defaults: min_rows=50, min_total_volume=200, min_max_oi=500.

Skip categories:
- **traded**: appeared in the DTE<=7 ledger (engine executed the trade)
- **atm_or_short_missing**: ATM or ATM+/-2 data absent in month/ parquet for that (date, expiry)
- **wing_data_missing**: ATM/short present but the long-leg offset (ATMp6 etc.) absent
- **short_illiquid**: short legs present but fail liquidity gate (rows/volume/OI)
- **wing_illiquid**: all data present but long legs fail liquidity gate
- **no_entry_bar**: data and liquidity pass but no non-zero-volume bar at exact entry timestamp for one or more legs
  (sub-reason shows which leg: short_call/short_put/long_call/long_put + why: no_bar_at_entry_time or zero_vol_at_entry)
- **unexplained**: all checks pass including entry bar — truly unknown; likely exit bar missing or edge case

---
## FINNIFTY

### FINNIFTY wing-4 — 239 candidate days (monthly DTE 1–7)

| Outcome | Days | % |
|---|---:|---:|
| traded | 176 | 73.6% |
| atm_or_short_missing | 15 | 6.3% |
| wing_data_missing | 2 | 0.8% |
| short_illiquid → low_oi: 1, low_oi: 1, low_oi: 1, low_volume: 1, low_volume: 1 | 5 | 2.1% |
| wing_illiquid | 1 | 0.4% |
| no_entry_bar | 36 | 15.1% |
| unexplained | 4 | 1.7% |

#### By DTE

| DTE | traded | atm_missing | wing_missing | short_illiquid | wing_illiquid | no_entry_bar | unexplained |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 39 | 0 | 0 | 1 | 0 | 3 | 0 |
| 2 | 8 | 1 | 0 | 0 | 0 | 0 | 0 |
| 3 | 8 | 1 | 0 | 0 | 0 | 1 | 0 |
| 4 | 35 | 1 | 0 | 1 | 0 | 4 | 0 |
| 5 | 29 | 1 | 0 | 1 | 1 | 6 | 1 |
| 6 | 33 | 5 | 0 | 2 | 0 | 8 | 1 |
| 7 | 24 | 6 | 2 | 0 | 0 | 14 | 2 |

#### By Year

| Year | traded | atm_missing | wing_missing | short_illiquid | wing_illiquid | no_entry_bar | unexplained | trade_rate% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2022 | 17 | 12 | 2 | 5 | 1 | 16 | 1 | 31% |
| 2023 | 54 | 0 | 0 | 0 | 0 | 1 | 0 | 98% |
| 2024 | 50 | 1 | 0 | 0 | 0 | 5 | 0 | 89% |
| 2025 | 46 | 1 | 0 | 0 | 0 | 5 | 3 | 84% |
| 2026 | 9 | 1 | 0 | 0 | 0 | 9 | 0 | 47% |

### FINNIFTY wing-6 — 239 candidate days (monthly DTE 1–7)

| Outcome | Days | % |
|---|---:|---:|
| traded | 148 | 61.9% |
| atm_or_short_missing | 15 | 6.3% |
| wing_data_missing | 4 | 1.7% |
| short_illiquid → low_oi: 1, low_oi: 1, low_volume: 1 | 3 | 1.3% |
| wing_illiquid | 1 | 0.4% |
| no_entry_bar | 43 | 18.0% |
| unexplained | 25 | 10.5% |

#### By DTE

| DTE | traded | atm_missing | wing_missing | short_illiquid | wing_illiquid | no_entry_bar | unexplained |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 33 | 0 | 0 | 1 | 0 | 4 | 5 |
| 2 | 6 | 1 | 0 | 0 | 0 | 0 | 2 |
| 3 | 6 | 1 | 0 | 0 | 0 | 1 | 2 |
| 4 | 32 | 1 | 0 | 1 | 0 | 5 | 2 |
| 5 | 27 | 1 | 1 | 0 | 1 | 6 | 3 |
| 6 | 29 | 5 | 1 | 1 | 0 | 8 | 5 |
| 7 | 15 | 6 | 2 | 0 | 0 | 19 | 6 |

#### By Year

| Year | traded | atm_missing | wing_missing | short_illiquid | wing_illiquid | no_entry_bar | unexplained | trade_rate% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2022 | 12 | 12 | 4 | 3 | 1 | 17 | 5 | 22% |
| 2023 | 47 | 0 | 0 | 0 | 0 | 5 | 3 | 85% |
| 2024 | 47 | 1 | 0 | 0 | 0 | 6 | 2 | 84% |
| 2025 | 37 | 1 | 0 | 0 | 0 | 7 | 10 | 67% |
| 2026 | 5 | 1 | 0 | 0 | 0 | 8 | 5 | 26% |

### FINNIFTY wing-8 — 239 candidate days (monthly DTE 1–7)

| Outcome | Days | % |
|---|---:|---:|
| traded | 48 | 20.1% |
| atm_or_short_missing | 15 | 6.3% |
| wing_data_missing | 2 | 0.8% |
| short_illiquid → low_oi: 1, low_oi: 1, low_oi: 1, low_oi: 1, low_volume: 1, low_volume: 1 | 6 | 2.5% |
| wing_illiquid → low_oi: 1, sparse_rows: 1, sparse_rows: 1 | 3 | 1.3% |
| no_entry_bar | 62 | 25.9% |
| unexplained | 103 | 43.1% |

#### By DTE

| DTE | traded | atm_missing | wing_missing | short_illiquid | wing_illiquid | no_entry_bar | unexplained |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 13 | 0 | 0 | 1 | 0 | 9 | 20 |
| 2 | 0 | 1 | 0 | 0 | 0 | 3 | 5 |
| 3 | 1 | 1 | 0 | 0 | 0 | 4 | 4 |
| 4 | 11 | 1 | 1 | 1 | 0 | 6 | 21 |
| 5 | 7 | 1 | 0 | 1 | 1 | 10 | 19 |
| 6 | 10 | 5 | 0 | 2 | 1 | 8 | 23 |
| 7 | 6 | 6 | 1 | 1 | 1 | 22 | 11 |

#### By Year

| Year | traded | atm_missing | wing_missing | short_illiquid | wing_illiquid | no_entry_bar | unexplained | trade_rate% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2022 | 4 | 12 | 2 | 6 | 3 | 19 | 8 | 7% |
| 2023 | 27 | 0 | 0 | 0 | 0 | 5 | 23 | 49% |
| 2024 | 10 | 1 | 0 | 0 | 0 | 8 | 37 | 18% |
| 2025 | 7 | 1 | 0 | 0 | 0 | 17 | 30 | 13% |
| 2026 | 0 | 1 | 0 | 0 | 0 | 13 | 5 | 0% |

---
## MIDCPNIFTY

### MIDCPNIFTY wing-4 — 238 candidate days (monthly DTE 1–7)

| Outcome | Days | % |
|---|---:|---:|
| traded | 124 | 52.1% |
| atm_or_short_missing | 81 | 34.0% |
| wing_data_missing | 4 | 1.7% |
| no_entry_bar | 14 | 5.9% |
| unexplained | 15 | 6.3% |

#### By DTE

| DTE | traded | atm_missing | wing_missing | short_illiquid | wing_illiquid | no_entry_bar | unexplained |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 13 | 14 | 1 | 0 | 0 | 1 | 3 |
| 2 | 10 | 3 | 1 | 0 | 0 | 0 | 2 |
| 3 | 21 | 2 | 0 | 0 | 0 | 1 | 3 |
| 4 | 21 | 13 | 0 | 0 | 0 | 1 | 1 |
| 5 | 16 | 16 | 1 | 0 | 0 | 2 | 1 |
| 6 | 25 | 16 | 1 | 0 | 0 | 1 | 1 |
| 7 | 18 | 17 | 0 | 0 | 0 | 8 | 4 |

#### By Year

| Year | traded | atm_missing | wing_missing | short_illiquid | wing_illiquid | no_entry_bar | unexplained | trade_rate% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2022 | 0 | 51 | 0 | 0 | 0 | 0 | 0 | 0% |
| 2023 | 17 | 26 | 4 | 0 | 0 | 8 | 3 | 29% |
| 2024 | 47 | 2 | 0 | 0 | 0 | 4 | 2 | 85% |
| 2025 | 47 | 1 | 0 | 0 | 0 | 0 | 7 | 85% |
| 2026 | 13 | 1 | 0 | 0 | 0 | 2 | 3 | 68% |

### MIDCPNIFTY wing-6 — 238 candidate days (monthly DTE 1–7)

| Outcome | Days | % |
|---|---:|---:|
| traded | 97 | 40.8% |
| atm_or_short_missing | 81 | 34.0% |
| wing_data_missing | 2 | 0.8% |
| no_entry_bar | 28 | 11.8% |
| unexplained | 30 | 12.6% |

#### By DTE

| DTE | traded | atm_missing | wing_missing | short_illiquid | wing_illiquid | no_entry_bar | unexplained |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 13 | 14 | 0 | 0 | 0 | 3 | 2 |
| 2 | 6 | 3 | 0 | 0 | 0 | 1 | 6 |
| 3 | 19 | 2 | 0 | 0 | 0 | 2 | 4 |
| 4 | 18 | 13 | 0 | 0 | 0 | 2 | 3 |
| 5 | 15 | 16 | 0 | 0 | 0 | 3 | 2 |
| 6 | 17 | 16 | 0 | 0 | 0 | 3 | 8 |
| 7 | 9 | 17 | 2 | 0 | 0 | 14 | 5 |

#### By Year

| Year | traded | atm_missing | wing_missing | short_illiquid | wing_illiquid | no_entry_bar | unexplained | trade_rate% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2022 | 0 | 51 | 0 | 0 | 0 | 0 | 0 | 0% |
| 2023 | 14 | 26 | 2 | 0 | 0 | 12 | 4 | 24% |
| 2024 | 38 | 2 | 0 | 0 | 0 | 9 | 6 | 69% |
| 2025 | 37 | 1 | 0 | 0 | 0 | 2 | 15 | 67% |
| 2026 | 8 | 1 | 0 | 0 | 0 | 5 | 5 | 42% |

### MIDCPNIFTY wing-8 — 238 candidate days (monthly DTE 1–7)

| Outcome | Days | % |
|---|---:|---:|
| traded | 28 | 11.8% |
| atm_or_short_missing | 81 | 34.0% |
| wing_data_missing | 1 | 0.4% |
| wing_illiquid → low_oi: 1, low_oi: 1, low_oi: 1, sparse_rows: 1 | 4 | 1.7% |
| no_entry_bar | 38 | 16.0% |
| unexplained | 86 | 36.1% |

#### By DTE

| DTE | traded | atm_missing | wing_missing | short_illiquid | wing_illiquid | no_entry_bar | unexplained |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 6 | 14 | 0 | 0 | 1 | 3 | 8 |
| 2 | 3 | 3 | 0 | 0 | 0 | 2 | 8 |
| 3 | 3 | 2 | 0 | 0 | 0 | 3 | 19 |
| 4 | 4 | 13 | 0 | 0 | 1 | 4 | 14 |
| 5 | 4 | 16 | 0 | 0 | 0 | 5 | 11 |
| 6 | 5 | 16 | 0 | 0 | 1 | 7 | 15 |
| 7 | 3 | 17 | 1 | 0 | 1 | 14 | 11 |

#### By Year

| Year | traded | atm_missing | wing_missing | short_illiquid | wing_illiquid | no_entry_bar | unexplained | trade_rate% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2022 | 0 | 51 | 0 | 0 | 0 | 0 | 0 | 0% |
| 2023 | 5 | 26 | 1 | 0 | 4 | 13 | 9 | 9% |
| 2024 | 9 | 2 | 0 | 0 | 0 | 12 | 32 | 16% |
| 2025 | 10 | 1 | 0 | 0 | 0 | 5 | 39 | 18% |
| 2026 | 4 | 1 | 0 | 0 | 0 | 8 | 6 | 21% |

---
## Availability at each wing offset (all candidate days)

### FINNIFTY — 239 total candidate days

| Metric | Days | % |
|---|---:|---:|
| ATM data present | 231 | 96.7% |
| ATM±2 (short legs) present | 224 | 93.7% |
| ATM±4 present | 227 | 95.0% |
| ATM±6 present | 224 | 93.7% |
| ATM±8 present | 224 | 93.7% |

### MIDCPNIFTY — 238 total candidate days

| Metric | Days | % |
|---|---:|---:|
| ATM data present | 172 | 72.3% |
| ATM±2 (short legs) present | 157 | 66.0% |
| ATM±4 present | 154 | 64.7% |
| ATM±6 present | 155 | 65.1% |
| ATM±8 present | 157 | 66.0% |

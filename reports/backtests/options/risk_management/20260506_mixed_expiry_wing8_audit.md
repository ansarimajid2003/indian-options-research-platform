# Wing-8 IC Data Availability Audit

Source: `20260506_mixed_expiry` IC ledgers (wing-6 and wing-8).

**Bias question**: is wing-8's high Sharpe (4.198) driven by real edge, or by
only entering on calm days where ATM±10 data is available (skipping the volatile
days that would hit the IC)?

**Test**: compare days wing-8 skipped (±10 data missing) vs days it entered, using
wing-6 as the universe denominator.  Wing-6 (long at ±8) fills in almost all
cases; its entry dates are the full opportunity set.

## Availability Summary

| Symbol | Expiry type | W6 trades | W8 trades | Shared | Skipped | Availability% |
|---|---|---:|---:|---:|---:|---:|
| NIFTY | weekly | 798 | 326 | 326 | 472 | 40.9% |
| BANKNIFTY | monthly | 790 | 262 | 253 | 537 | 32.0% |
| FINNIFTY | monthly | 195 | 60 | 51 | 144 | 26.2% |
| MIDCPNIFTY | monthly | 183 | 48 | 34 | 149 | 18.6% |

## PnL Counterfactual

Wing-6 PnL split by whether wing-8 also traded that day.
If skipped days are the **losing** days → wing-8 is dodging bad trades (no bias, or anti-bias).
If skipped days are the **winning** days → wing-8 is cherry-picking good trades (upward bias).

| Symbol | W6 avg PnL (shared) | W6 avg PnL (skipped) | W6 total PnL (shared) | W6 total PnL (skipped) | Verdict |
|---|---:|---:|---:|---:|---|
| NIFTY | INR+182 | INR+72 | INR+59,176 | INR+33,847 | skipped worse → mild anti-bias |
| BANKNIFTY | INR-159 | INR-207 | INR-40,235 | INR-111,147 | skipped worse → mild anti-bias |
| FINNIFTY | INR+251 | INR+83 | INR+12,776 | INR+12,007 | skipped worse → mild anti-bias |
| MIDCPNIFTY | INR+180 | INR-227 | INR+6,131 | INR-33,896 | skipped=losers → NO BIAS (wing-8 missing bad days) |

## VIX Distribution: Shared vs Skipped

If skipped days cluster in **low-VIX** buckets, wing-8 is missing calm days (fine — less premium anyway).
If they cluster in **high-VIX**, wing-8 is missing the high-IV profitable entries (anti-bias — result understated).
If they cluster in **high-VIX and high-move**, wing-8 is missing the dangerous days (upward bias).

### NIFTY

| VIX bucket | Shared (traded) | Skipped | Skipped% |
|---|---:|---:|---:|
| <10 | 4 | 8 | 67% |
| 10-13 | 128 | 143 | 53% |
| 13-17 | 123 | 188 | 60% |
| 17-22 | 53 | 99 | 65% |
| 22-30 | 18 | 34 | 65% |

VIX MW test:   shared median=13.69, n=326 | skipped median=14.39, n=472 | shared is lower | MW p=0.0019

### BANKNIFTY

| VIX bucket | Shared (traded) | Skipped | Skipped% |
|---|---:|---:|---:|
| <10 | 5 | 8 | 62% |
| 10-13 | 120 | 180 | 60% |
| 13-17 | 88 | 202 | 70% |
| 17-22 | 33 | 113 | 77% |
| 22-30 | 7 | 34 | 83% |

VIX MW test:   shared median=13.02, n=253 | skipped median=14.00, n=537 | shared is lower | MW p=0.0000

### FINNIFTY

| VIX bucket | Shared (traded) | Skipped | Skipped% |
|---|---:|---:|---:|
| <10 | 0 | 4 | 100% |
| 10-13 | 22 | 50 | 69% |
| 13-17 | 24 | 75 | 76% |
| 17-22 | 4 | 13 | 76% |
| 22-30 | 1 | 2 | 67% |

VIX MW test:   shared median=13.87, n=51 | skipped median=13.81, n=144 | shared is higher | MW p=0.9241

### MIDCPNIFTY

| VIX bucket | Shared (traded) | Skipped | Skipped% |
|---|---:|---:|---:|
| <10 | 2 | 6 | 75% |
| 10-13 | 11 | 67 | 86% |
| 13-17 | 17 | 64 | 79% |
| 17-22 | 2 | 10 | 83% |
| 22-30 | 2 | 2 | 50% |

VIX MW test:   shared median=13.37, n=34 | skipped median=13.07, n=149 | shared is higher | MW p=0.3273

## Intraday Range: Shared vs Skipped

Daily range = (day_high − day_low) / day_open × 100.
High range → volatile day.  If skipped days have lower range → wing-8 entering on move days → potential loss bias.

### NIFTY

Range MW test:   shared median=0.70, n=325 | skipped median=0.93, n=466 | shared is lower | MW p=0.0000
  Shared  range quartiles: {0.25: 0.539, 0.5: 0.705, 0.75: 0.938}
  Skipped range quartiles: {0.25: 0.699, 0.5: 0.933, 0.75: 1.282}

### BANKNIFTY

Range MW test:   shared median=0.89, n=253 | skipped median=1.08, n=537 | shared is lower | MW p=0.0000
  Shared  range quartiles: {0.25: 0.645, 0.5: 0.892, 0.75: 1.195}
  Skipped range quartiles: {0.25: 0.821, 0.5: 1.076, 0.75: 1.521}

### FINNIFTY

Range MW test:   shared median=0.87, n=51 | skipped median=1.04, n=144 | shared is lower | MW p=0.0114
  Shared  range quartiles: {0.25: 0.674, 0.5: 0.866, 0.75: 1.178}
  Skipped range quartiles: {0.25: 0.774, 0.5: 1.045, 0.75: 1.418}

### MIDCPNIFTY

Range MW test:   shared median=0.99, n=34 | skipped median=1.18, n=149 | shared is lower | MW p=0.0352
  Shared  range quartiles: {0.25: 0.755, 0.5: 0.989, 0.75: 1.25}
  Skipped range quartiles: {0.25: 0.893, 0.5: 1.181, 0.75: 1.69}

## Availability by Year

### NIFTY

| Year | Shared | Skipped | Availability% |
|---|---:|---:|---:|
| 2022 | 73 | 105 | 41% |
| 2023 | 98 | 92 | 52% |
| 2024 | 75 | 109 | 41% |
| 2025 | 67 | 120 | 36% |
| 2026 | 13 | 46 | 22% |

### BANKNIFTY

| Year | Shared | Skipped | Availability% |
|---|---:|---:|---:|
| 2022 | 41 | 114 | 26% |
| 2023 | 88 | 117 | 43% |
| 2024 | 34 | 117 | 23% |
| 2025 | 74 | 142 | 34% |
| 2026 | 16 | 47 | 25% |

### FINNIFTY

| Year | Shared | Skipped | Availability% |
|---|---:|---:|---:|
| 2022 | 5 | 10 | 33% |
| 2023 | 26 | 24 | 52% |
| 2024 | 11 | 46 | 19% |
| 2025 | 9 | 57 | 14% |
| 2026 | 0 | 7 | 0% |

### MIDCPNIFTY

| Year | Shared | Skipped | Availability% |
|---|---:|---:|---:|
| 2023 | 6 | 12 | 33% |
| 2024 | 11 | 44 | 20% |
| 2025 | 13 | 77 | 14% |
| 2026 | 4 | 16 | 20% |

## Availability by DTE at Entry

Key question: does wing-8 drop out at specific DTE ranges?
Monthly expiries (BN/FN/MCP) enter at DTE 15-30; ±10 data should be
available if Dhan loads full chain for all DTE — or only near expiry.

### NIFTY

| DTE at entry | Shared | Skipped | Availability% |
|---|---:|---:|---:|
| 0-2 | 170 | 206 | 45.2% |
| 3-5 | 83 | 151 | 35.5% |
| 6-10 | 73 | 115 | 38.8% |

### BANKNIFTY

| DTE at entry | Shared | Skipped | Availability% |
|---|---:|---:|---:|
| 0-2 | 30 | 51 | 37.0% |
| 3-5 | 11 | 40 | 21.6% |
| 6-10 | 67 | 125 | 34.9% |
| 11-20 | 82 | 168 | 32.8% |
| 21+ | 63 | 153 | 29.2% |

### FINNIFTY

| DTE at entry | Shared | Skipped | Availability% |
|---|---:|---:|---:|
| 0-2 | 13 | 26 | 33.3% |
| 3-5 | 19 | 46 | 29.2% |
| 6-10 | 15 | 38 | 28.3% |
| 11-20 | 3 | 20 | 13.0% |
| 21+ | 1 | 14 | 6.7% |

### MIDCPNIFTY

| DTE at entry | Shared | Skipped | Availability% |
|---|---:|---:|---:|
| 0-2 | 9 | 10 | 47.4% |
| 3-5 | 10 | 42 | 19.2% |
| 6-10 | 8 | 32 | 20.0% |
| 11-20 | 3 | 34 | 8.1% |
| 21+ | 4 | 31 | 11.4% |

## Verdict

**NIFTY**: availability 40.9%, skipped W6 avg INR+72 vs shared INR+182 → **MODERATE CAUTION — one bias indicator triggered; result plausible but needs live validation**

**BANKNIFTY**: availability 32.0%, skipped W6 avg INR-207 vs shared INR-159 → **MODERATE CAUTION — one bias indicator triggered; result plausible but needs live validation**

**FINNIFTY**: availability 26.2%, skipped W6 avg INR+83 vs shared INR+251 → **MODERATE CAUTION — one bias indicator triggered; result plausible but needs live validation**

**MIDCPNIFTY**: availability 18.6%, skipped W6 avg INR-227 vs shared INR+180 → **MODERATE CAUTION — one bias indicator triggered; result plausible but needs live validation**

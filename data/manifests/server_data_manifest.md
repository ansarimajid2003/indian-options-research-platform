# Server Data Manifest

**Generated:** 2026-05-11T21:10:05Z  |  **Server:** zimaos  |  **WD base:** `/media/WD-Storage/indian-markets-data`

**Total on WD storage:** 992 files, 6.4 GB  |  **Datasets:** 24

## Coverage

| ID | Name | Vendor | Freq | From | To | Rows / Files | MB |
|---|---|---|---|---|---|---|---|
| `spot_nifty` | NIFTY Spot 1-min | Shoonya + Dhan (merged/canonical) | 1-min | 2015-01-09 | 2026-05-11 | 1,044,460 | 60.6 |
| `spot_banknifty` | BANKNIFTY Spot 1-min | Dhan | 1-min | 2021-08-04 | 2026-04-30 | 436,667 | 27.4 |
| `spot_finnifty` | FINNIFTY Spot 1-min | Dhan | 1-min | 2021-08-04 | 2026-05-11 | 439,502 | 27.6 |
| `spot_midcpnifty` | MIDCPNIFTY Spot 1-min | Dhan | 1-min | 2022-01-31 | 2026-05-11 | 395,556 | 24.0 |
| `spot_sensex` | SENSEX Spot 1-min | Dhan | 1-min | 2023-04-03 | 2026-05-11 | 286,398 | 17.7 |
| `spot_india_vix` | INDIA_VIX Spot 1-min | Dhan | 1-min | 2021-08-04 | 2026-05-04 | 437,281 | 21.4 |
| `vix_archive_1min` | India VIX 1-min (archive fallback) | NSE/Upstox archive — cleaned | 1-min | 2015-01-09 | 2026-04-08 | 1,038,983 | 48.4 |
| `vix_archive_1day` | India VIX 1-day (archive fallback) | NSE/Upstox archive — cleaned | 1-day | 2015-01-01 | 2026-04-08 | 2,790 | 0.1 |
| `vix_archive_5min` | India VIX 5-min (archive fallback) | NSE/Upstox archive — cleaned | 5-min | 2015-01-09 | 2026-04-08 | 207,825 | 9.7 |
| `vix_archive_15min` | India VIX 15-min (archive fallback) | NSE/Upstox archive — cleaned | 15-min | 2015-01-09 | 2026-04-08 | 69,285 | 3.2 |
| `vix_archive_30min` | India VIX 30-min (archive fallback) | NSE/Upstox archive — cleaned | 30-min | 2015-01-09 | 2026-04-08 | 36,036 | 1.7 |
| `vix_archive_60min` | India VIX 60-min (archive fallback) | NSE/Upstox archive — cleaned | 60-min | 2015-01-09 | 2026-04-08 | 19,406 | 0.9 |
| `dhan_options_banknifty_month` | BANKNIFTY Options 1-min (month expiry) | Dhan | 1-min | 2021-08-04 | 2026-05-04 | 42 files (ATM: 437,423 rows) | 833.9 |
| `dhan_options_banknifty_week` | BANKNIFTY Options 1-min (week expiry) | Dhan | 1-min | 2021-08-04 | 2026-04-30 | 42 files (ATM: 437,106 rows) | 884.4 |
| `dhan_options_finnifty_month` | FINNIFTY Options 1-min (month expiry) | Dhan | 1-min | 2021-08-17 | 2026-05-04 | 42 files (ATM: 346,502 rows) | 357.2 |
| `dhan_options_finnifty_week` | FINNIFTY Options 1-min (week expiry) | Dhan | 1-min | 2021-08-04 | 2026-04-30 | 42 files (ATM: 409,476 rows) | 596.7 |
| `dhan_options_midcpnifty_month` | MIDCPNIFTY Options 1-min (month expiry) | Dhan | 1-min | 2022-01-31 | 2026-05-04 | 42 files (ATM: 257,966 rows) | 315.8 |
| `dhan_options_midcpnifty_week` | MIDCPNIFTY Options 1-min (week expiry) | Dhan | 1-min | 2022-01-31 | 2026-04-30 | 42 files (ATM: 281,630 rows) | 458.3 |
| `dhan_options_nifty_month` | NIFTY Options 1-min (month expiry) | Dhan | 1-min | 2021-05-03 | 2026-04-30 | 42 files (ATM: 462,936 rows) | 853.5 |
| `dhan_options_nifty_week` | NIFTY Options 1-min (week expiry) | Dhan | 1-min | 2021-01-01 | 2026-04-30 | 42 files (ATM: 492,921 rows) | 919.8 |
| `dhan_options_sensex_month` | SENSEX Options 1-min (month expiry) | Dhan | 1-min | 2023-05-15 | 2026-05-06 | 42 files (ATM: 221,253 rows) | 274.4 |
| `dhan_options_sensex_week` | SENSEX Options 1-min (week expiry) | Dhan | 1-min | 2023-05-15 | 2026-05-06 | 42 files (ATM: 275,241 rows) | 523.3 |
| `nse_bhavcopy_fo` | NSE Bhavcopy F&O EOD | NSE (official end-of-day settlement) | 1-day (EOD settlement) | 2008-01-01 | 2026-04-29 | 8,074,841 | 80.6 |
| `backtest_reports_legacy` | Backtest Report Archive (legacy) | internal — Wing-6 engine | CSV + Markdown | 2026-05-05 | 2026-05-07 | 485 CSV + 55 MD | 62.6 |

## Symlink Map (zimaos)

| Repo path | Points to |
|---|---|
| `data/processed/spot/` | repo dir (health-monitor writes here live) |
| `data/processed/options` | `WD:processed/options` |
| `data/processed/nse` | `WD:processed/nse` |
| `data/processed/market_archive_cleaned` | `WD:processed/market_archive_cleaned` |
| `reports/backtests/options` | `WD:reports/backtests/options` |
| `reports/backtests/dashboard_runs/` | `WD:reports/backtests/dashboard_runs/` (empty — canonical new runs) |

## Vendor Overlap Notes

| Data | Primary | Overlap / Secondary | Resolution |
|---|---|---|---|
| NIFTY spot 1-min | `nifty50_1min_CANONICAL.csv` (Shoonya + Dhan merged) | `market_archive_cleaned/NIFTY 50_minute.csv` | Use canonical exclusively |
| BANKNIFTY spot | `banknifty_1min_DHAN.csv` | `market_archive_cleaned/NIFTY BANK_minute.csv` | Use spot/ file |
| India VIX 1-min | `indiavix_1min_DHAN.csv` (2021+) | `market_archive_cleaned/INDIA VIX_minute.csv` (2015+) | Dhan primary; archive fallback for pre-2021 |
| Options 1-min | Dhan parquet (ATM±10, all 5 symbols, 2021+) | Shoonya raw CSV (NIFTY only, 2024+, absolute strikes) | Dashboard uses Dhan. Shoonya feeds backtest engine only |
| Bhavcopy | `nse/bhavcopy/fo/nifty_options_eod_YYYY.parquet` | none | NIFTY only; no BN/FN/MCP/SENSEX equivalent yet |

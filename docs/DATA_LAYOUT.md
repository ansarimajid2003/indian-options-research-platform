# Data Layout

This workspace uses a raw/processed/report split so source data remains
untouched and research outputs do not masquerade as data quality reports.

## Canonical Paths

- `data/raw/` stores untouched vendor, exchange, and archive inputs.
- `data/processed/` stores cleaned or normalized datasets derived from raw data.
- `data/manifests/data_inventory_manifest.csv` is the canonical inventory.
- `reports/data_quality/` stores structural data-quality audits only.
- `reports/research/` stores strategy research outputs.
- `reports/backtests/` stores generated trade ledgers and backtest summaries.
- `scripts/` stores runnable one-off and operational scripts.
- `market_data/` stores reusable data-access code with the OOS lock.
- `docs/` stores human-facing design, setup, and research notes.

## Source Policy

Raw files should be moved or appended only by downloader/collector scripts, not
edited in place. Cleaned datasets should be regenerated from raw sources. The
manifest should be rebuilt after material data changes with:

```powershell
python scripts/data/build_data_inventory.py
```

## Current Canonical Data

- Historical index/VIX source of truth: `data/raw/market_archive/`
- Cleaned index/VIX research files: `data/processed/market_archive_cleaned/`
- NIFTY 1-minute live/research master: `data/processed/spot/nifty50_1min_CANONICAL.csv`
- Raw options sources: `data/raw/options/shoonya/` and `data/raw/options/dhan/`
- EOD options cross-check source: `data/raw/nse/bhavcopy/fo/`

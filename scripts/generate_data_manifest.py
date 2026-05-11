#!/usr/bin/env python3
"""
Generate data/manifests/server_data_manifest.{json,md} from actual file probes.
Run from repo root after data is synced.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
WD_BASE = "/media/WD-Storage/indian-markets-data"
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

datasets = []

# ─────────────────────────────────────────────────────────────────────────────
# 1. SPOT 1-min CSVs
# ─────────────────────────────────────────────────────────────────────────────
SPOT_META = [
    ("NIFTY",      "nifty50_1min_CANONICAL.csv",  "Shoonya + Dhan (merged/canonical)", True),
    ("BANKNIFTY",  "banknifty_1min_DHAN.csv",      "Dhan",  False),
    ("FINNIFTY",   "finnifty_1min_DHAN.csv",        "Dhan",  True),
    ("MIDCPNIFTY", "midcpnifty_1min_DHAN.csv",      "Dhan",  True),
    ("SENSEX",     "sensex_1min_DHAN.csv",           "Dhan",  True),
    ("INDIA_VIX",  "indiavix_1min_DHAN.csv",         "Dhan",  False),
]
for sym, fname, vendor, live in SPOT_META:
    f = BASE / "data/processed/spot" / fname
    ts = pd.read_csv(f, usecols=[0], parse_dates=[0]).iloc[:, 0]
    datasets.append({
        "id": f"spot_{sym.lower()}",
        "name": f"{sym} Spot 1-min",
        "vendor": vendor,
        "type": "spot",
        "path": f"processed/spot/{fname}",
        "format": "CSV",
        "frequency": "1-min",
        "date_from": str(ts.min())[:10],
        "date_to":   str(ts.max())[:10],
        "rows": int(len(ts)),
        "size_mb": round(f.stat().st_size / 1e6, 1),
        "server_location": "repo:data/processed/spot/",
        "live_updated": live,
    })
    print(f"  spot/{fname}")

# ─────────────────────────────────────────────────────────────────────────────
# 2. India VIX archive fallbacks
# ─────────────────────────────────────────────────────────────────────────────
VIX_ARCHIVE = [
    ("INDIA VIX_minute.csv",   "1-min"),
    ("INDIA VIX_day.csv",      "1-day"),
    ("INDIA VIX_5minute.csv",  "5-min"),
    ("INDIA VIX_15minute.csv", "15-min"),
    ("INDIA VIX_30minute.csv", "30-min"),
    ("INDIA VIX_60minute.csv", "60-min"),
]
for fname, freq in VIX_ARCHIVE:
    f = BASE / "data/processed/market_archive_cleaned" / fname
    ts = pd.read_csv(f, usecols=[0], parse_dates=[0]).iloc[:, 0]
    datasets.append({
        "id": f"vix_archive_{freq.replace('-', '')}",
        "name": f"India VIX {freq} (archive fallback)",
        "vendor": "NSE/Upstox archive — cleaned",
        "type": "vix_archive",
        "path": f"processed/market_archive_cleaned/{fname}",
        "format": "CSV",
        "frequency": freq,
        "date_from": str(ts.min())[:10],
        "date_to":   str(ts.max())[:10],
        "rows": int(len(ts)),
        "size_mb": round(f.stat().st_size / 1e6, 1),
        "server_location": "WD -> repo symlink:data/processed/market_archive_cleaned/",
        "live_updated": False,
        "note": "VIX fallback for pre-2021 queries; primary is indiavix_1min_DHAN.csv",
    })
    print(f"  vix/{fname}")

# ─────────────────────────────────────────────────────────────────────────────
# 3. Dhan options 1-min parquet (probed values cached inline)
# ─────────────────────────────────────────────────────────────────────────────
DHAN_COVERAGE = {
    "banknifty": {
        "month": ("2021-08-04", "2026-05-04", 437423),
        "week":  ("2021-08-04", "2026-04-30", 437106),
    },
    "finnifty": {
        "month": ("2021-08-17", "2026-05-04", 346502),
        "week":  ("2021-08-04", "2026-04-30", 409476),
    },
    "midcpnifty": {
        "month": ("2022-01-31", "2026-05-04", 257966),
        "week":  ("2022-01-31", "2026-04-30", 281630),
    },
    "nifty": {
        "month": ("2021-05-03", "2026-04-30", 462936),
        "week":  ("2021-01-01", "2026-04-30", 492921),
    },
    "sensex": {
        "month": ("2023-05-15", "2026-05-06", 221253),
        "week":  ("2023-05-15", "2026-05-06", 275241),
    },
}
ATM_OFFSETS = (
    ["ATMm10","ATMm9","ATMm8","ATMm7","ATMm6","ATMm5","ATMm4","ATMm3","ATMm2","ATMm1",
     "ATM",
     "ATMp1","ATMp2","ATMp3","ATMp4","ATMp5","ATMp6","ATMp7","ATMp8","ATMp9","ATMp10"]
)
DHAN_COLS = [
    "timestamp", "open", "high", "low", "close", "volume", "oi",
    "iv", "strike", "spot", "iv_raw", "iv_spike_flag", "iv_zero_flag", "iv_clean",
]

DHAN = BASE / "data/processed/options/dhan"
for sym_dir in sorted(DHAN.iterdir()):
    sym = sym_dir.name
    for et_dir in sorted(sym_dir.iterdir()):
        et = et_dir.name
        all_pqs = list(et_dir.rglob("*.parquet"))
        total_sz = round(sum(p.stat().st_size for p in all_pqs) / 1e6, 1)
        d_from, d_to, rows_per_file = DHAN_COVERAGE[sym][et]
        datasets.append({
            "id": f"dhan_options_{sym}_{et}",
            "name": f"{sym.upper()} Options 1-min ({et} expiry)",
            "vendor": "Dhan",
            "type": "options_1min",
            "path": f"processed/options/dhan/{sym}/{et}/expiry_code_1/{{call|put}}/{{offset}}.parquet",
            "format": "Parquet",
            "frequency": "1-min",
            "date_from": d_from,
            "date_to":   d_to,
            "rows_per_file": rows_per_file,
            "files": len(all_pqs),
            "size_mb": total_sz,
            "atm_offsets": ATM_OFFSETS,
            "opt_types": ["call", "put"],
            "columns": DHAN_COLS,
            "server_location": "WD -> repo symlink:data/processed/options/dhan/",
            "live_updated": False,
        })
        print(f"  dhan/{sym}/{et}  ({len(all_pqs)} files, {total_sz} MB)")

# ─────────────────────────────────────────────────────────────────────────────
# 4. NSE Bhavcopy EOD
# ─────────────────────────────────────────────────────────────────────────────
BHAV = BASE / "data/processed/nse/bhavcopy/fo"
bhav_dfs = [pd.read_parquet(f, columns=["trade_date"]) for f in sorted(BHAV.glob("*.parquet"))]
bhav_all = pd.concat(bhav_dfs)
bhav_files = list(BHAV.glob("*.parquet"))
datasets.append({
    "id": "nse_bhavcopy_fo",
    "name": "NSE Bhavcopy F&O EOD",
    "vendor": "NSE (official end-of-day settlement)",
    "type": "bhavcopy_eod",
    "path": "processed/nse/bhavcopy/fo/nifty_options_eod_YYYY.parquet",
    "format": "Parquet",
    "frequency": "1-day (EOD settlement)",
    "date_from": str(bhav_all.trade_date.min())[:10],
    "date_to":   str(bhav_all.trade_date.max())[:10],
    "rows": int(len(bhav_all)),
    "files": len(bhav_files),
    "size_mb": round(sum(f.stat().st_size for f in bhav_files) / 1e6, 1),
    "columns": ["trade_date","expiry_date","strike","option_type","open","high","low","close","settle_price","oi","volume"],
    "server_location": "WD -> repo symlink:data/processed/nse/bhavcopy/fo/",
    "live_updated": False,
    "note": "NIFTY only in current dataset. 19 yearly parquet files.",
})
print(f"  bhavcopy  {len(bhav_all):,} rows")

# ─────────────────────────────────────────────────────────────────────────────
# 5. Backtest report archive
# ─────────────────────────────────────────────────────────────────────────────
bt_root = BASE / "reports/backtests/options"
bt_all = [f for f in bt_root.rglob("*") if f.is_file()]
bt_csvs = [f for f in bt_all if f.suffix == ".csv"]
bt_mds  = [f for f in bt_all if f.suffix == ".md"]
subdirs = sorted({f.parent.relative_to(bt_root).parts[0]
                  for f in bt_all if f.parent != bt_root})
datasets.append({
    "id": "backtest_reports_legacy",
    "name": "Backtest Report Archive (legacy)",
    "vendor": "internal — Wing-6 engine",
    "type": "backtest_artifacts",
    "path": "reports/backtests/options/",
    "format": "CSV + Markdown",
    "date_from": "2026-05-05",
    "date_to":   "2026-05-07",
    "files_csv": len(bt_csvs),
    "files_md":  len(bt_mds),
    "size_mb": round(sum(f.stat().st_size for f in bt_all) / 1e6, 1),
    "subdirs": subdirs,
    "server_location": "WD -> repo symlink:reports/backtests/options/",
    "live_updated": False,
    "note": "Trade-ledger CSVs and summary MDs. dashboard_runs/ is empty until save_backtest.py is used.",
})
print(f"  backtests  {len(bt_csvs)} CSVs, {len(bt_mds)} MDs")

# ─────────────────────────────────────────────────────────────────────────────
# Build manifest
# ─────────────────────────────────────────────────────────────────────────────
manifest = {
    "schema_version": "1",
    "generated_at": now,
    "server": "zimaos",
    "wd_storage_base": WD_BASE,
    "repo_path": "/DATA/live-paper/indian-markets",
    "total_files_on_wd": 992,
    "total_size_wd_gb": 6.4,
    "summary": {
        "spot_symbols": ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "INDIA_VIX"],
        "options_symbols": ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"],
        "options_expiry_types": ["week", "month"],
        "options_atm_offsets_count": 21,
        "bhavcopy_years": list(range(2008, 2027)),
        "backtest_legacy_subdirs": subdirs,
    },
    "datasets": datasets,
}

out_json = BASE / "data/manifests/server_data_manifest.json"
out_json.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
print(f"\nwrote {out_json}  ({len(datasets)} datasets, {out_json.stat().st_size // 1024} KB)")

# ─────────────────────────────────────────────────────────────────────────────
# Markdown table
# ─────────────────────────────────────────────────────────────────────────────
md_lines = [
    "# Server Data Manifest",
    "",
    f"**Generated:** {now}  |  **Server:** zimaos  |  **WD base:** `{WD_BASE}`",
    "",
    f"**Total on WD storage:** 992 files, 6.4 GB  |  **Datasets:** {len(datasets)}",
    "",
    "## Coverage",
    "",
    "| ID | Name | Vendor | Freq | From | To | Rows / Files | MB |",
    "|---|---|---|---|---|---|---|---|",
]
for d in datasets:
    if "rows" in d:
        r = f"{d['rows']:,}"
    elif "rows_per_file" in d:
        r = f"{d['files']} files (ATM: {d['rows_per_file']:,} rows)"
    elif "files_csv" in d:
        r = f"{d['files_csv']} CSV + {d['files_md']} MD"
    else:
        r = str(d.get("files",""))
    md_lines.append(
        f"| `{d['id']}` | {d['name']} | {d['vendor']} "
        f"| {d['frequency'] if 'frequency' in d else d['format']} "
        f"| {d['date_from']} | {d['date_to']} | {r} | {d['size_mb']} |"
    )

md_lines += [
    "",
    "## Symlink Map (zimaos)",
    "",
    "| Repo path | Points to |",
    "|---|---|",
    "| `data/processed/spot/` | repo dir (health-monitor writes here live) |",
    "| `data/processed/options` | `WD:processed/options` |",
    "| `data/processed/nse` | `WD:processed/nse` |",
    "| `data/processed/market_archive_cleaned` | `WD:processed/market_archive_cleaned` |",
    "| `reports/backtests/options` | `WD:reports/backtests/options` |",
    "| `reports/backtests/dashboard_runs/` | `WD:reports/backtests/dashboard_runs/` (empty — canonical new runs) |",
    "",
    "## Vendor Overlap Notes",
    "",
    "| Data | Primary | Overlap / Secondary | Resolution |",
    "|---|---|---|---|",
    "| NIFTY spot 1-min | `nifty50_1min_CANONICAL.csv` (Shoonya + Dhan merged) | `market_archive_cleaned/NIFTY 50_minute.csv` | Use canonical exclusively |",
    "| BANKNIFTY spot | `banknifty_1min_DHAN.csv` | `market_archive_cleaned/NIFTY BANK_minute.csv` | Use spot/ file |",
    "| India VIX 1-min | `indiavix_1min_DHAN.csv` (2021+) | `market_archive_cleaned/INDIA VIX_minute.csv` (2015+) | Dhan primary; archive fallback for pre-2021 |",
    "| Options 1-min | Dhan parquet (ATM±10, all 5 symbols, 2021+) | Shoonya raw CSV (NIFTY only, 2024+, absolute strikes) | Dashboard uses Dhan. Shoonya feeds backtest engine only |",
    "| Bhavcopy | `nse/bhavcopy/fo/nifty_options_eod_YYYY.parquet` | none | NIFTY only; no BN/FN/MCP/SENSEX equivalent yet |",
]

out_md = BASE / "data/manifests/server_data_manifest.md"
out_md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
print(f"wrote {out_md}")

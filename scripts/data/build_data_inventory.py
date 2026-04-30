"""
Build the canonical data inventory manifest.

The manifest is intentionally dataset-level, not every-file-level. Raw options
downloads can contain tens of thousands of files, so each logical partition gets
one row with path, source, file count, date span, quality notes, and overlap
links to comparable datasets.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_DIR = ROOT / "data" / "manifests"
MANIFEST_CSV = MANIFEST_DIR / "data_inventory_manifest.csv"
SUMMARY_MD = MANIFEST_DIR / "data_inventory_summary.md"

DATE_RE = re.compile(r"(20\d{2}[-_]?\d{2}[-_]?\d{2})")


@dataclass
class DatasetRecord:
    dataset_id: str
    stage: str
    asset_class: str
    instrument: str
    frequency: str
    source: str
    path: str
    format: str
    file_count: int
    total_bytes: int
    row_count: str = ""
    start_date: str = ""
    end_date: str = ""
    columns: str = ""
    canonical_role: str = ""
    quality_status: str = ""
    overlap_key: str = ""
    overlaps_with: str = ""
    notes: str = ""


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def normalize_instrument(value: str) -> str:
    cleaned = value.upper().replace("_", " ").strip()
    aliases = {
        "NIFTY 50": "NIFTY",
        "NIFTY BANK": "BANKNIFTY",
        "NIFTY FIN SERVICE": "FINNIFTY",
        "INDIA VIX": "INDIAVIX",
    }
    return aliases.get(cleaned, cleaned.replace(" ", ""))


def infer_frequency(path: Path) -> str:
    stem = path.stem.lower()
    if stem.endswith("_minute"):
        return "1min"
    if stem.endswith("_5minute"):
        return "5min"
    if stem.endswith("_15minute"):
        return "15min"
    if stem.endswith("_30minute"):
        return "30min"
    if stem.endswith("_60minute"):
        return "60min"
    if stem.endswith("_day"):
        return "daily"
    return "unknown"


def infer_instrument(path: Path) -> str:
    stem = path.stem
    for suffix in ("_15minute", "_30minute", "_5minute", "_60minute", "_minute", "_day"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def extract_dates_from_names(paths: list[Path]) -> tuple[str, str]:
    dates: list[str] = []
    for path in paths:
        for match in DATE_RE.findall(path.name):
            dates.append(match.replace("_", "-"))
    if not dates:
        return "", ""
    return min(dates), max(dates)


def csv_profile(path: Path) -> tuple[str, str, str, str]:
    row_count = 0
    header: list[str] = []
    first_row: list[str] | None = None
    last_row: list[str] | None = None

    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return "0", "", "", ""
        for row in reader:
            if not row:
                continue
            row_count += 1
            if first_row is None:
                first_row = row
            last_row = row

    def date_from(row: list[str] | None) -> str:
        if not row:
            return ""
        lower = [col.strip().lower() for col in header]
        if "datetime" in lower:
            value = row[lower.index("datetime")]
            return value[:10]
        if "date" in lower:
            value = row[lower.index("date")]
            return value[:10]
        matches = DATE_RE.findall(",".join(row[:3]))
        return matches[0].replace("_", "-") if matches else ""

    return str(row_count), date_from(first_row), date_from(last_row), "|".join(header)


def file_stats(paths: list[Path]) -> tuple[int, int]:
    return len(paths), sum(path.stat().st_size for path in paths if path.exists())


def add_market_archive(records: list[DatasetRecord], base: Path, stage: str, source: str) -> None:
    if not base.exists():
        return
    for path in sorted(base.glob("*.csv")):
        instrument = infer_instrument(path)
        normalized = normalize_instrument(instrument)
        frequency = infer_frequency(path)
        row_count, start, end, columns = csv_profile(path)
        asset_class = "volatility_index" if normalized == "INDIAVIX" else "index_spot"
        role = ""
        if stage == "processed" and normalized in {"NIFTY", "BANKNIFTY", "INDIAVIX"}:
            role = "research_ready_cleaned"
        records.append(
            DatasetRecord(
                dataset_id=f"{stage}_{slug(source)}_{slug(normalized)}_{frequency}",
                stage=stage,
                asset_class=asset_class,
                instrument=normalized,
                frequency=frequency,
                source=source,
                path=rel(path),
                format="csv",
                file_count=1,
                total_bytes=path.stat().st_size,
                row_count=row_count,
                start_date=start,
                end_date=end,
                columns=columns,
                canonical_role=role,
                quality_status="raw_untouched" if stage == "raw" else "cleaned_non_destructive",
                overlap_key=f"{asset_class}|{normalized}|{frequency}|ohlcv",
                notes="Archive-derived index/VIX OHLCV file.",
            )
        )


def add_single_csv(
    records: list[DatasetRecord],
    path: Path,
    dataset_id: str,
    stage: str,
    asset_class: str,
    instrument: str,
    frequency: str,
    source: str,
    role: str,
    quality: str,
    notes: str,
) -> None:
    if not path.exists():
        return
    row_count, start, end, columns = csv_profile(path)
    records.append(
        DatasetRecord(
            dataset_id=dataset_id,
            stage=stage,
            asset_class=asset_class,
            instrument=normalize_instrument(instrument),
            frequency=frequency,
            source=source,
            path=rel(path),
            format="csv",
            file_count=1,
            total_bytes=path.stat().st_size,
            row_count=row_count,
            start_date=start,
            end_date=end,
            columns=columns,
            canonical_role=role,
            quality_status=quality,
            overlap_key=f"{asset_class}|{normalize_instrument(instrument)}|{frequency}|ohlcv",
            notes=notes,
        )
    )


def add_directory_group(
    records: list[DatasetRecord],
    dataset_id: str,
    stage: str,
    asset_class: str,
    instrument: str,
    frequency: str,
    source: str,
    path: Path,
    pattern: str,
    fmt: str,
    role: str,
    quality: str,
    notes: str,
) -> None:
    if not path.exists():
        return
    files = sorted(path.glob(pattern))
    if not files:
        return
    count, size = file_stats(files)
    start, end = extract_dates_from_names(files)
    records.append(
        DatasetRecord(
            dataset_id=dataset_id,
            stage=stage,
            asset_class=asset_class,
            instrument=normalize_instrument(instrument),
            frequency=frequency,
            source=source,
            path=rel(path),
            format=fmt,
            file_count=count,
            total_bytes=size,
            start_date=start,
            end_date=end,
            canonical_role=role,
            quality_status=quality,
            overlap_key=f"{asset_class}|{normalize_instrument(instrument)}|{frequency}|{fmt}",
            notes=notes,
        )
    )


def add_raw_spot(records: list[DatasetRecord]) -> None:
    base = ROOT / "data" / "raw" / "spot"
    add_directory_group(
        records,
        "raw_dhan_spot_daily",
        "raw",
        "index_spot",
        "multiple",
        "daily",
        "dhan_charts_historical",
        base / "daily",
        "*.json",
        "json",
        "cross_check",
        "raw_vendor_response",
        "Dhan daily spot/VIX JSON responses grouped by symbol.",
    )
    intraday = base / "intraday"
    if intraday.exists():
        for symbol_dir in sorted(p for p in intraday.iterdir() if p.is_dir()):
            add_directory_group(
                records,
                f"raw_dhan_spot_intraday_{slug(symbol_dir.name)}",
                "raw",
                "index_spot",
                symbol_dir.name,
                "1min",
                "dhan_charts_intraday",
                symbol_dir,
                "*.json",
                "json",
                "cross_check",
                "raw_vendor_response",
                "Dhan intraday spot/VIX JSON responses.",
            )


def add_options(records: list[DatasetRecord]) -> None:
    shoonya = ROOT / "data" / "raw" / "options" / "shoonya" / "nifty"
    if shoonya.exists():
        expiry_dirs = sorted(p for p in shoonya.iterdir() if p.is_dir())
        files = [f for d in expiry_dirs for f in d.glob("*.csv")]
        count, size = file_stats(files)
        records.append(
            DatasetRecord(
                dataset_id="raw_shoonya_nifty_options_1min",
                stage="raw",
                asset_class="index_options",
                instrument="NIFTY",
                frequency="1min",
                source="shoonya_public_expired_options",
                path=rel(shoonya),
                format="csv",
                file_count=count,
                total_bytes=size,
                start_date=min((d.name for d in expiry_dirs), default=""),
                end_date=max((d.name for d in expiry_dirs), default=""),
                canonical_role="first_options_research_source",
                quality_status="raw_public_archive",
                overlap_key="index_options|NIFTY|1min|option_ohlcv",
                notes="Expiry-folder option-chain CSVs; no bid/ask; useful overlap with Dhan NIFTY options.",
            )
        )

    dhan = ROOT / "data" / "raw" / "options" / "dhan"
    if not dhan.exists():
        return
    for leaf in sorted(p for p in dhan.rglob("*") if p.is_dir()):
        files = sorted(leaf.glob("*.json"))
        if not files:
            continue
        parts = leaf.relative_to(dhan).parts
        if len(parts) < 5:
            continue
        symbol, expiry_flag, expiry_code, side, strike = parts[:5]
        count, size = file_stats(files)
        start, end = extract_dates_from_names(files)
        records.append(
            DatasetRecord(
                dataset_id=f"raw_dhan_{slug(symbol)}_{expiry_flag}_{expiry_code}_{side}_{slug(strike)}",
                stage="raw",
                asset_class="index_options",
                instrument=symbol.upper(),
                frequency="1min",
                source="dhan_rollingoption",
                path=rel(leaf),
                format="json",
                file_count=count,
                total_bytes=size,
                start_date=start,
                end_date=end,
                canonical_role="options_cross_check_source",
                quality_status="raw_vendor_response_missing_bid_ask",
                overlap_key=f"index_options|{symbol.upper()}|1min|option_ohlcv",
                notes=f"{expiry_flag}/{expiry_code}/{side}/{strike}; contains OHLCV/OI/IV/spot when API returned data.",
            )
        )


def add_nse(records: list[DatasetRecord]) -> None:
    bhav = ROOT / "data" / "raw" / "nse" / "bhavcopy" / "fo"
    if bhav.exists():
        for year_dir in sorted(p for p in bhav.iterdir() if p.is_dir()):
            add_directory_group(
                records,
                f"raw_nse_fo_bhavcopy_{year_dir.name}",
                "raw",
                "index_options",
                "NIFTY",
                "daily_eod",
                "nse_fo_bhavcopy",
                year_dir,
                "*.csv",
                "csv",
                "eod_cross_check",
                "raw_exchange_archive_filtered",
                "NSE F&O bhavcopy files; useful for EOD option OHLC/OI/volume checks.",
            )

    add_directory_group(
        records,
        "raw_nse_live_option_chain",
        "raw",
        "index_options",
        "NIFTY",
        "live_snapshot",
        "nse_option_chain",
        ROOT / "data" / "raw" / "nse" / "option_chain",
        "*.ndjson",
        "ndjson",
        "bid_ask_snapshot_source",
        "raw_exchange_snapshot",
        "Live option-chain snapshots with bid/ask when scraper is run.",
    )

    add_directory_group(
        records,
        "raw_nse_index_archive_upto_2024",
        "raw",
        "index_spot",
        "multiple",
        "mixed",
        "nse_index_archive_folder",
        ROOT / "data" / "raw" / "nse" / "index_archive_upto_2024",
        "*",
        "mixed",
        "legacy_reference",
        "raw_untouched_may_include_partial_files",
        "Legacy NSE index-data folder preserved as-is.",
    )


def add_reference(records: list[DatasetRecord]) -> None:
    add_single_csv(
        records,
        ROOT / "data" / "raw" / "reference" / "dhan_instruments_master.csv",
        "raw_dhan_instruments_master",
        "raw",
        "reference",
        "multiple",
        "point_in_time",
        "dhan_instruments_master",
        "symbol_reference",
        "raw_vendor_reference",
        "Dhan instrument master used to resolve tokens/security IDs.",
    )


def apply_overlaps(records: list[DatasetRecord]) -> None:
    groups: dict[str, list[DatasetRecord]] = defaultdict(list)
    for record in records:
        if record.overlap_key:
            groups[record.overlap_key].append(record)
    for group in groups.values():
        if len(group) <= 1:
            continue
        for record in group:
            comparable = [
                other
                for other in group
                if other.dataset_id != record.dataset_id
                and (other.source != record.source or other.stage != record.stage)
            ]
            if len(comparable) > 12:
                counts: dict[str, int] = defaultdict(int)
                for other in comparable:
                    counts[f"{other.stage}/{other.source}"] += 1
                record.overlaps_with = ";".join(
                    f"{key}:{counts[key]}_datasets" for key in sorted(counts)
                )
            else:
                record.overlaps_with = ";".join(other.dataset_id for other in comparable)


def write_manifest(records: list[DatasetRecord]) -> None:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    records = sorted(records, key=lambda row: (row.asset_class, row.instrument, row.frequency, row.dataset_id))
    apply_overlaps(records)
    fieldnames = list(asdict(records[0]).keys()) if records else list(DatasetRecord.__dataclass_fields__.keys())
    with MANIFEST_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(asdict(record))

    by_stage = defaultdict(int)
    by_asset = defaultdict(int)
    overlap_rows = 0
    total_files = 0
    total_bytes = 0
    for record in records:
        by_stage[record.stage] += 1
        by_asset[record.asset_class] += 1
        overlap_rows += 1 if record.overlaps_with else 0
        total_files += record.file_count
        total_bytes += record.total_bytes

    lines = [
        "# Data Inventory Summary",
        "",
        f"Manifest: `{rel(MANIFEST_CSV)}`",
        f"Datasets: `{len(records):,}`",
        f"Files represented: `{total_files:,}`",
        f"Approx bytes represented: `{total_bytes:,}`",
        f"Rows with overlap links: `{overlap_rows:,}`",
        "",
        "## By Stage",
        "",
    ]
    for key in sorted(by_stage):
        lines.append(f"- `{key}`: `{by_stage[key]:,}`")
    lines.extend(["", "## By Asset Class", ""])
    for key in sorted(by_asset):
        lines.append(f"- `{key}`: `{by_asset[key]:,}`")
    lines.extend(
        [
            "",
            "## Canonical Routing",
            "",
            "- Raw market archive: `data/raw/market_archive/`",
            "- Cleaned market archive: `data/processed/market_archive_cleaned/`",
            "- Canonical live NIFTY minute file: `data/processed/spot/nifty50_1min_CANONICAL.csv`",
            "- Raw options: `data/raw/options/`",
            "- NSE F&O bhavcopy: `data/raw/nse/bhavcopy/fo/`",
            "- Backtest outputs: `reports/backtests/options/`",
        ]
    )
    SUMMARY_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    records: list[DatasetRecord] = []
    add_market_archive(records, ROOT / "data" / "raw" / "market_archive", "raw", "market_archive")
    add_market_archive(
        records,
        ROOT / "data" / "processed" / "market_archive_cleaned",
        "processed",
        "market_archive_cleaned",
    )
    add_single_csv(
        records,
        ROOT / "data" / "processed" / "spot" / "nifty50_1min_CANONICAL.csv",
        "processed_nifty_1min_canonical",
        "processed",
        "index_spot",
        "NIFTY",
        "1min",
        "market_archive_plus_recent",
        "canonical_live_master",
        "cleaned_deduplicated_no_bad_ohlc_in_last_audit",
        "Primary NIFTY 1-minute research/live collector target.",
    )
    add_raw_spot(records)
    add_options(records)
    add_nse(records)
    add_reference(records)
    write_manifest(records)
    print(f"Wrote {MANIFEST_CSV} and {SUMMARY_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

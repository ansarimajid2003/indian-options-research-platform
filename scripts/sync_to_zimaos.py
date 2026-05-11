#!/usr/bin/env python3
"""
One-way sync of v2 dashboard data from laptop to zimaos WD Storage.

Skips files already on the server with matching size.
Safe to re-run — idempotent.

Usage:
    python scripts/sync_to_zimaos.py
    python scripts/sync_to_zimaos.py --dry-run
"""

import argparse
import sys
from pathlib import Path

import paramiko
from tqdm import tqdm

REPO_ROOT = Path(__file__).resolve().parent.parent

ZIMAOS_HOST = "192.168.0.254"
ZIMAOS_USER = "root"
KEY_PATH = Path.home() / ".ssh" / "id_ed25519"
DEST_BASE = "/media/WD-Storage/indian-markets-data"

# (local dir, remote subpath under DEST_BASE)
DIR_SOURCES = [
    (REPO_ROOT / "data/processed/spot",
     "processed/spot"),
    (REPO_ROOT / "data/processed/options/dhan",
     "processed/options/dhan"),
    (REPO_ROOT / "data/processed/nse/bhavcopy/fo",
     "processed/nse/bhavcopy/fo"),
    (REPO_ROOT / "reports/backtests/options",
     "reports/backtests/options"),
]

VIX_DEST = "processed/market_archive_cleaned"


def collect_tasks() -> list[tuple[Path, str]]:
    tasks: list[tuple[Path, str]] = []

    for local_dir, remote_sub in DIR_SOURCES:
        if not local_dir.exists():
            print(f"  WARNING source not found, skipping: {local_dir}")
            continue
        for f in sorted(local_dir.rglob("*")):
            if f.is_file():
                rel = f.relative_to(local_dir).as_posix()
                tasks.append((f, f"{DEST_BASE}/{remote_sub}/{rel}"))

    vix_src = REPO_ROOT / "data/processed/market_archive_cleaned"
    for f in sorted(vix_src.glob("INDIA VIX_*.csv")):
        tasks.append((f, f"{DEST_BASE}/{VIX_DEST}/{f.name}"))

    return tasks


def _ensure_remote_dir(sftp: paramiko.SFTPClient, path: str) -> None:
    parts = [p for p in path.split("/") if p]
    current = ""
    for part in parts:
        current += "/" + part
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


def _remote_size(sftp: paramiko.SFTPClient, path: str) -> int | None:
    try:
        return sftp.stat(path).st_size
    except FileNotFoundError:
        return None


def run(dry_run: bool = False) -> None:
    tasks = collect_tasks()
    total_bytes = sum(f.stat().st_size for f, _ in tasks)

    print(f"\n{'='*60}")
    print(f"  SYNC TO ZIMAOS  {'(DRY RUN) ' if dry_run else ''}")
    print(f"  Files  : {len(tasks):,}")
    print(f"  Total  : {total_bytes / 1e9:.2f} GB")
    print(f"  Dest   : {DEST_BASE}")
    print(f"{'='*60}\n")

    if dry_run:
        for local, remote in tasks:
            print(f"  {local.relative_to(REPO_ROOT)}  ->  {remote}")
        print(f"\n  {len(tasks):,} files, {total_bytes / 1e9:.2f} GB (dry run, nothing transferred)")
        return

    key = paramiko.Ed25519Key.from_private_key_file(str(KEY_PATH))
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"  connecting to {ZIMAOS_HOST}…")
    client.connect(ZIMAOS_HOST, username=ZIMAOS_USER, pkey=key, timeout=15)
    sftp = client.open_sftp()
    _ensure_remote_dir(sftp, DEST_BASE)

    uploaded = skipped = errors = 0
    bytes_sent = 0

    with tqdm(total=total_bytes, unit="B", unit_scale=True,
              unit_divisor=1024, desc="syncing", ncols=72) as pbar:
        for local_path, remote_path in tasks:
            local_size = local_path.stat().st_size
            pbar.set_postfix_str(local_path.name[:28], refresh=False)

            remote_sz = _remote_size(sftp, remote_path)
            if remote_sz == local_size:
                skipped += 1
                pbar.update(local_size)
                continue

            parent = remote_path.rsplit("/", 1)[0]
            _ensure_remote_dir(sftp, parent)

            try:
                sftp.put(str(local_path), remote_path)
                uploaded += 1
                bytes_sent += local_size
                pbar.update(local_size)
            except Exception as exc:
                errors += 1
                pbar.update(local_size)
                tqdm.write(f"  ERROR  {local_path.name}: {exc}")

    sftp.close()
    client.close()

    print(f"\n{'='*60}")
    print(f"  uploaded : {uploaded:,}  ({bytes_sent / 1e9:.2f} GB sent)")
    print(f"  skipped  : {skipped:,}  (already on server, same size)")
    print(f"  errors   : {errors:,}")
    print(f"{'='*60}\n")

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="list files without transferring")
    args = parser.parse_args()
    run(dry_run=args.dry_run)

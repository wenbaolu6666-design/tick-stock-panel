# -*- coding: utf-8 -*-
"""Repair duplicate (symbol, date) rows in TSP daily parquet partitions.

Dry-run by default. With --apply, each changed partition is copied to
`<data_dir>/.repair_backups/duplicate-<timestamp>/` before an atomic rewrite.

This tool targets the legacy corruption that made the backtest matrix prewarm
fail with: MarketDataMatrix requires unique date/symbol rows.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

import polars as pl

DEFAULT_TABLES = ("kline_daily", "kline_daily_enriched")
KEY_COLUMNS = ("symbol", "date")


def _partition_files(root: Path) -> list[Path]:
    return sorted(root.glob("date=*/part.parquet"))


def _repair_table(root: Path, backup_root: Path, *, apply: bool) -> dict:
    files = _partition_files(root)
    changed: list[dict] = []
    duplicate_rows = 0
    duplicate_symbols: set[str] = set()
    backup_bytes = 0

    for file in files:
        frame = pl.read_parquet(file)
        if not set(KEY_COLUMNS).issubset(frame.columns):
            continue
        deduplicated = frame.unique(
            subset=list(KEY_COLUMNS), keep="last", maintain_order=True,
        )
        removed = frame.height - deduplicated.height
        if removed <= 0:
            continue

        duplicate_keys = frame.filter(
            frame.select(KEY_COLUMNS).is_duplicated()
        )
        symbols = sorted(str(value) for value in duplicate_keys["symbol"].unique())
        duplicate_symbols.update(symbols)
        duplicate_rows += removed
        item = {
            "file": str(file.relative_to(root)),
            "rows_before": frame.height,
            "rows_after": deduplicated.height,
            "duplicate_rows_removed": removed,
            "symbols": symbols,
        }

        if apply:
            relative = file.relative_to(root)
            backup = backup_root / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, backup)
            backup_bytes += backup.stat().st_size

            temporary = file.with_name(f".{file.name}.{uuid.uuid4().hex}.tmp")
            try:
                deduplicated.write_parquet(temporary, compression="zstd")
                with temporary.open("r+b") as stream:
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, file)
            finally:
                temporary.unlink(missing_ok=True)

            verified = pl.read_parquet(file, columns=list(KEY_COLUMNS))
            if verified.height != deduplicated.height:
                raise RuntimeError(
                    f"post-write row count mismatch: {file} "
                    f"{verified.height} != {deduplicated.height}"
                )
            if verified.height != verified.unique(KEY_COLUMNS).height:
                raise RuntimeError(f"duplicates remain after repair: {file}")

        changed.append(item)

    return {
        "table": root.name,
        "partitions_scanned": len(files),
        "partitions_changed": len(changed),
        "duplicate_rows_removed": duplicate_rows,
        "symbols": sorted(duplicate_symbols),
        "backup_bytes": backup_bytes,
        "changes": changed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("TSP_DATA_DIR", r"E:\tsp_data")),
    )
    parser.add_argument("--table", action="append", choices=DEFAULT_TABLES)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    data_dir: Path = args.data_dir.resolve()
    tables = tuple(args.table or DEFAULT_TABLES)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_base = data_dir / ".repair_backups" / f"duplicate-{stamp}"
    results = []
    for table in tables:
        root = data_dir / table
        if not root.exists():
            raise FileNotFoundError(root)
        result = _repair_table(
            root,
            backup_base / table,
            apply=args.apply,
        )
        results.append(result)
        print(
            f"{table}: partitions={result['partitions_scanned']} "
            f"changed={result['partitions_changed']} "
            f"duplicate_rows={result['duplicate_rows_removed']} "
            f"symbols={result['symbols']}",
            flush=True,
        )

    report = {
        "mode": "apply" if args.apply else "dry-run",
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_dir": str(data_dir),
        "results": results,
    }
    if args.apply:
        backup_base.mkdir(parents=True, exist_ok=True)
        report_path = backup_base / "report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"report={report_path}", flush=True)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

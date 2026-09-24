# -*- coding: utf-8 -*-
"""TQ 分钟K补齐: 用 tdx_quant(TQ 网关)拉指定日期范围全市场分钟K并落盘。

背景: vipdoc 分钟只到 2026-09-04, 库内分钟缺 2026-09-10 起; TQ 网关在线时
可补(注意 TQ 分钟同样是不复权契约, dividend_type=none)。
用法: python tools_tq_minute_backfill.py <start> <end>   (YYYY-MM-DD)
"""
import sys
import time
from datetime import date, datetime
from pathlib import Path

import polars as pl

DATA_DIR = Path("..", "data").resolve()
BATCH = 200


def main() -> None:
    start = date.fromisoformat(sys.argv[1])
    end = date.fromisoformat(sys.argv[2])

    from app.tickflow.repository import DataStore, KlineRepository
    from app.services import kline_sync
    from app.plugins.tdx_quant import provider as tq

    repo = KlineRepository(DataStore(DATA_DIR))
    minute_dir = DATA_DIR / "kline_minute"

    inst = pl.read_parquet(DATA_DIR / "instruments" / "instruments.parquet", columns=["symbol"])
    universe = sorted(set(inst["symbol"].to_list()))
    index_set = repo.get_index_symbol_set()
    universe = [s for s in universe if s not in index_set]

    existing = {p.name.removeprefix("date=") for p in minute_dir.glob("date=*")}
    todo = []
    d = start
    while d <= end:
        if d.isoformat() not in existing:
            todo.append(d)
        d = date.fromordinal(d.toordinal() + 1)

    prov = tq.TdxQuantProvider()
    print(f"universe {len(universe)} | 待补 {len(todo)} 天: {[x.isoformat() for x in todo]}", flush=True)

    t0 = time.time()
    total = 0
    for di, d in enumerate(todo, 1):
        d0 = datetime(d.year, d.month, d.day)
        d1 = d0.replace(hour=23, minute=59)
        BATCH = 200
        for bi in range(0, len(universe), BATCH):
            chunk = universe[bi:bi + BATCH]
            df = prov.get_minute(chunk, start_time=d0, end_time=d1)
            if df is not None and not df.is_empty():
                with repo._write_lock:
                    total += kline_sync._write_minute_partition(df, minute_dir)
        print(f"  [{di}/{len(todo)}] {d} done (累计 {total} 行, {time.time()-t0:.0f}s)", flush=True)

    try:
        repo.db.execute(
            f"""CREATE OR REPLACE VIEW kline_minute AS
                SELECT * FROM read_parquet('{DATA_DIR.as_posix()}/kline_minute/**/*.parquet',
                union_by_name=true)"""
        )
    except Exception as e:  # noqa: BLE001
        print(f"view refresh warn: {e}", flush=True)
    print(f"完成: {len(todo)} 天 {total} 行 {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()

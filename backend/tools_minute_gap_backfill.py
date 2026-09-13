# -*- coding: utf-8 -*-
"""分钟K中间空洞回补: 显式区间全市场拉取并 merge-upsert 落盘.
用法: python tools_minute_gap_backfill.py <start:YYYY-MM-DD> <end:YYYY-MM-DD>"""
import sys
import time
from datetime import datetime
from pathlib import Path

DATA_DIR = Path("..", "data").resolve()

def main():
    start = datetime.fromisoformat(sys.argv[1])
    end = datetime.fromisoformat(sys.argv[2]).replace(hour=23, minute=59)
    from app.tickflow.repository import DataStore, KlineRepository
    from app.services import kline_sync
    import polars as pl

    repo = KlineRepository(DataStore(DATA_DIR))
    inst = pl.read_parquet(DATA_DIR / "instruments" / "instruments.parquet", columns=["symbol"])
    universe = sorted(set(inst["symbol"].to_list()))
    index_set = repo.get_index_symbol_set()
    universe = [s for s in universe if s not in index_set]
    print(f"universe: {len(universe)} | window: {start} ~ {end}", flush=True)

    minute_dir = DATA_DIR / "kline_minute"
    written = [0]

    def _persist(seg_df: pl.DataFrame) -> None:
        with repo._write_lock:
            written[0] += kline_sync._write_minute_partition(seg_df, minute_dir)
        print(f"  persisted seg: +{written[0]} rows so far", flush=True)

    t0 = time.time()
    # 按标的分批 (每批 400 只 × 全区间): 防 custom 路径长区间内存峰值 (98天×5567只曾 OOM)
    BATCH = 400
    for bi in range(0, len(universe), BATCH):
        chunk = universe[bi:bi + BATCH]
        kline_sync.sync_minute_batch(
            chunk, start_time=start, end_time=end,
            on_segment=_persist, asset_type="stock",
        )
        print(f"  batch {bi//BATCH + 1}/{(len(universe)+BATCH-1)//BATCH} done ({len(chunk)} syms), {time.time()-t0:.0f}s", flush=True)
    # 刷新视图
    try:
        d = DATA_DIR.as_posix()
        repo.db.execute(
            f"""CREATE OR REPLACE VIEW kline_minute AS
                SELECT * FROM read_parquet('{d}/kline_minute/**/*.parquet', union_by_name=true)"""
        )
    except Exception as e:
        print("view refresh failed:", e, flush=True)
    print(f"DONE: {written[0]} rows, {time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__":
    main()

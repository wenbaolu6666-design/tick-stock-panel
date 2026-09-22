# -*- coding: utf-8 -*-
"""分钟K vipdoc 全量导入 v2: 每只只读一次(全窗口) + 小批次 + 符号级断点。

v1(按天循环)每只每天重读整个 .lc1 冗余致命; 400 只全窗口一次 concat 曾
OOM(16GB 内存实测)。本版:
- BATCH=30(峰值 ~1110万行 DataFrame, 安全);
- 断点续跑: 已完成的 symbol 记录在 data/minutes_full_import.done, 重跑跳过;
- 窗口缺省 2020-01-02 ~ 2026-09-04(vipdoc .lc1 实际覆盖; 不用日K分区
  定交易日, 否则漏 2020-2021.9 无日K分区的分钟)。

用法: python tools_minute_full_import.py [start] [end]
"""
import sys
import time
from datetime import datetime
from pathlib import Path

import polars as pl

DATA_DIR = Path("..", "data").resolve()
BATCH = 30
DONE_FILE = DATA_DIR / "minutes_full_import.done"


def main() -> None:
    start = datetime.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime(2020, 1, 2)
    end = datetime.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else datetime(2026, 9, 4, 23, 59)

    from app.tickflow.repository import DataStore, KlineRepository
    from app.services import kline_sync

    repo = KlineRepository(DataStore(DATA_DIR))
    minute_dir = DATA_DIR / "kline_minute"

    inst = pl.read_parquet(DATA_DIR / "instruments" / "instruments.parquet", columns=["symbol"])
    universe = sorted(set(inst["symbol"].to_list()))
    index_set = repo.get_index_symbol_set()
    universe = [s for s in universe if s not in index_set]

    done: set[str] = set()
    if DONE_FILE.exists():
        done = {ln.strip() for ln in DONE_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()}
    todo = [s for s in universe if s not in done]
    print(f"universe: {len(universe)} | 已完成 {len(done)} | 待导入 {len(todo)}", flush=True)

    t0 = time.time()
    total_rows = 0
    nbatch = (len(todo) + BATCH - 1) // BATCH
    for bi in range(0, len(todo), BATCH):
        chunk = todo[bi:bi + BATCH]
        box: list[int] = [0]
        ok_syms: list[str] = []

        def _persist(seg_df: pl.DataFrame) -> None:
            with repo._write_lock:
                box[0] += kline_sync._write_minute_partition(seg_df, minute_dir)

        try:
            kline_sync.sync_minute_batch(chunk, start_time=start, end_time=end, on_segment=_persist, asset_type="stock")
        except Exception as e:  # noqa: BLE001
            print(f"  batch {bi//BATCH + 1}/{nbatch} 异常: {str(e)[:100]}", flush=True)
        # 该批落盘非空即视为完成(provider 内部已逐股 try, 单股失败只 warning)
        with DONE_FILE.open("a", encoding="utf-8") as fh:
            for s in chunk:
                fh.write(s + "\n")
        total_rows += box[0]
        if (bi // BATCH + 1) % 10 == 0 or bi + BATCH >= len(todo):
            print(f"  [{bi//BATCH + 1}/{nbatch}] +{box[0]} 行 (总 {total_rows}, {time.time()-t0:.0f}s)", flush=True)

    try:
        repo.db.execute(
            f"""CREATE OR REPLACE VIEW kline_minute AS
                SELECT * FROM read_parquet('{DATA_DIR.as_posix()}/kline_minute/**/*.parquet',
                union_by_name=True)"""
        )
    except Exception as e:  # noqa: BLE001
        print(f"view refresh warn: {e}", flush=True)
    print(f"完成: {len(todo)} 只, {total_rows} 行, 耗时 {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()

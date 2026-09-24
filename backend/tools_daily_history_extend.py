# -*- coding: utf-8 -*-
"""日线历史扩展导入: 把 vipdoc 里早于 TSP 库现有起点的日线导入 kline_daily。

背景: vipdoc .day 有 1991(sh)/2001(sh600519) 起的完整日线, TSP 库只有
2021-09-03 起(当初同步起点)。本工具按 (symbol, date) merge-upsert 幂等
导入更早历史, 与现有数据口径一致(tdx_local 不复权原始价)。

用法: python tools_daily_history_extend.py [start:YYYY-MM-DD] [end]
  缺省 start=1990-01-01, end=2026-09-23(全窗口, 已有行同键覆盖, 幂等)。
"""
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

DATA_DIR = Path("..", "data").resolve()
BATCH = 20000   # 一次全量: 避免每批触发 5000 个日期分区 upsert(实测慢)


def main() -> None:
    start = datetime.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime(1990, 1, 1)
    end = datetime.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else datetime(2026, 9, 23, 23, 59)

    from app.tickflow.repository import DataStore, KlineRepository
    from app.plugins.tdx_local import provider as tdxp

    repo = KlineRepository(DataStore(DATA_DIR))

    inst = pl.read_parquet(DATA_DIR / "instruments" / "instruments.parquet", columns=["symbol"])
    universe = sorted(set(inst["symbol"].to_list()))
    index_set = repo.get_index_symbol_set()
    universe = [s for s in universe if s not in index_set]
    print(f"universe: {len(universe)} | window: {start.date()} ~ {end.date()}", flush=True)

    # 年级断点: 已完成的年份跳过(读+append 都跳)
    done_years: set[int] = set()
    ydone_p = DATA_DIR / "daily_extend_years.done"
    if ydone_p.exists():
        done_years = {int(x) for x in ydone_p.read_text(encoding="utf-8").split() if x.strip()}
    years = [y for y in range(start.year, end.year + 1) if y not in done_years]
    print(f"待导入年份: {years}", flush=True)

    t0 = time.time()
    for yi, y in enumerate(years, 1):
        s = max(datetime(y, 1, 1), start)
        e = min(datetime(y, 12, 31, 23, 59), end)
        if s > e:
            continue
        df = tdxp.TdxLocalProvider().get_daily(universe, start_time=s, end_time=e)
        if df is None or df.is_empty():
            print(f"  [{yi}/{len(years)}] {y}: 无数据(跳过)", flush=True)
            continue
        # pyarrow 直写分区(绕开 polars 大 DataFrame 写路径死锁), schema 对齐现有库
        pdf = df.to_pandas()
        pdf["quote_ts"] = 0
        pdf["date"] = pdf["date"].astype(str)
        for dstr, g in pdf.groupby("date"):
            out = os.path.join(DATA_DIR, "kline_daily", f"date={dstr}", "part.parquet")
            tbl = pa.Table.from_pandas(
                g[["symbol", "close", "open", "high", "low", "volume", "amount", "quote_ts", "date"]],
                preserve_index=False,
            )
            os.makedirs(os.path.dirname(out), exist_ok=True)
            if os.path.exists(out):
                old_tbl = pq.read_table(out)
                import pandas as pd
                merged = pd.concat([old_tbl.to_pandas(), tbl.to_pandas()]).drop_duplicates(
                    subset=["symbol", "date"], keep="last")
                tbl = pa.Table.from_pandas(merged, schema=tbl.schema, preserve_index=False)
            tmp = out + ".tmp"
            pq.write_table(tbl, tmp)
            os.replace(tmp, out)
        with ydone_p.open("a", encoding="utf-8") as fh:
            fh.write(str(y) + chr(10))
        print(f"  [{yi}/{len(years)}] {y}: {df.height:,} 行 ({time.time()-t0:.0f}s)", flush=True)
    print(f"完成: {len(years)} 年 {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()

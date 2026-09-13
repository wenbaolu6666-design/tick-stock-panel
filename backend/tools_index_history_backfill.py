# -*- coding: utf-8 -*-
"""指数日线历史回补(本地 vipdoc .day): 2021-01-01 起 + enriched 落盘."""
import gc, time
from datetime import datetime
from pathlib import Path
import polars as pl

from app.plugins.tdx_local.provider import _path_for, _read_day
from app.indicators.pipeline import compute_enriched

DATA_DIR = Path("..", "data").resolve()
START = datetime(2021, 1, 1)

def main():
    from app.tickflow.repository import DataStore, KlineRepository
    repo = KlineRepository(DataStore(DATA_DIR))
    inst = repo.get_index_instruments()
    symbols = sorted(set(inst["symbol"].to_list()))
    print("index universe:", len(symbols), flush=True)

    # 参考收盘: 库内最新 (指数 .day 为 ×100 刻度, 与股票一致, 仍逐标的校准防意外)
    import duckdb
    con = duckdb.connect()
    ref = con.execute("""
        SELECT symbol, arg_max(close, date) AS rc
        FROM read_parquet('../data/kline_index_daily/**/*.parquet', union_by_name=true)
        GROUP BY symbol
    """).pl()
    con.close()
    ref_map = dict(zip(ref["symbol"].to_list(), ref["rc"].to_list()))

    done = skipped = 0
    frames: list[pl.DataFrame] = []
    t0 = time.time()
    def flush(frames):
        raw = pl.concat(frames, how="diagonal_relaxed").unique(subset=["symbol","date"], keep="last").sort(["symbol","date"])
        enriched = compute_enriched(raw, factors=None, instruments=None)
        repo.append_index_daily(raw)
        repo.append_index_enriched(enriched)
        gc.collect()

    for sym in symbols:
        p = _path_for(sym)
        try:
            if p is None or not p.exists():
                skipped += 1
                continue
            rows = _read_day(p, sym)
        except Exception as e:
            print("  read fail", sym, e, flush=True)
            continue
        if not rows:
            skipped += 1
            continue
        df = pl.DataFrame(rows).with_columns(
            pl.col("date").cast(pl.Int32).cast(pl.Utf8).str.to_date("%Y%m%d", strict=False)
        ).filter(pl.col("date") >= START.date())
        if df.is_empty():
            continue
        rc = ref_map.get(sym)
        if rc and rc > 0:
            ratio = df["close"][-1] / rc
            scale = min((1, 10, 100), key=lambda k: abs(ratio - k))
            if abs(ratio - scale) > 0.05 * scale:
                print(f"  scale异常 {sym}: ratio={ratio:.3f} 跳过", flush=True)
                continue
            if scale != 1:
                df = df.with_columns([(pl.col(c)/scale).alias(c) for c in ("open","high","low","close")])
        frames.append(df.select(["symbol","date","open","high","low","close","volume","amount"]))
        done += 1
        if len(frames) >= 120:
            flush(frames); frames = []
            print(f"progress: {done} indexes, {time.time()-t0:.0f}s", flush=True)
    if frames:
        flush(frames)
    print(f"BACKFILL DONE: {done} indexes (skip {skipped}), {time.time()-t0:.0f}s", flush=True)
    import glob
    parts = sorted(glob.glob(str(DATA_DIR / "kline_index_daily" / "date=*")))
    print("index daily partitions:", len(parts), parts[0].split("=")[-1], "->", parts[-1].split("=")[-1], flush=True)

if __name__ == "__main__":
    main()

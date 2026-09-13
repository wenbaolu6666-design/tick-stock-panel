# -*- coding: utf-8 -*-
"""ETF 日线历史回补(本地 vipdoc .day): 逐标的刻度校准(×100/×1000) + enriched 计算 + merge-upsert."""
import gc
import time
from datetime import datetime
from pathlib import Path

import polars as pl

from app.indicators.pipeline import compute_enriched

DATA_DIR = Path("..", "data").resolve()
START = datetime(2021, 1, 1)

def main():
    from app.plugins.tdx_local.provider import _path_for, _read_day
    from app.tickflow.repository import DataStore, KlineRepository
    from app.services.index_sync import _load_etf_factors

    repo = KlineRepository(DataStore(DATA_DIR))
    instruments = repo.get_etf_instruments()
    symbols = sorted(set(instruments["symbol"].to_list()))
    print(f"ETF universe: {len(symbols)}", flush=True)

    # 参考收盘: 库内每标的最新 close (tickflow 权威刻度)
    import duckdb
    con = duckdb.connect()
    ref = con.execute("""
        SELECT symbol, arg_max(close, date) AS ref_close, max(date) AS ref_date
        FROM read_parquet('../data/kline_etf_daily/**/*.parquet', union_by_name=true)
        GROUP BY symbol
    """).pl()
    con.close()
    ref_map = dict(zip(ref["symbol"].to_list(), ref["ref_close"].to_list()))
    print("reference closes:", len(ref_map), flush=True)

    factors = _load_etf_factors(repo)
    print("adj factors:", factors.height, flush=True)

    done = 0
    t0 = time.time()
    batch: list[pl.DataFrame] = []
    BATCH_N = 150
    for sym in symbols:
        path = _path_for(sym)
        try:
            if path is None or not path.exists():
                continue
            rows = _read_day(path, sym)
        except Exception as e:  # noqa: BLE001
            print(f"  read fail {sym}: {e}", flush=True)
            continue
        if not rows:
            continue
        df = pl.DataFrame(rows).with_columns(
            pl.col("date").cast(pl.Int32).cast(pl.Utf8).str.to_date("%Y%m%d", strict=False)
        ).filter(pl.col("date") >= START.date())
        if df.is_empty():
            continue
        # 刻度校准: 末根 close / 参考close → 取最近的 {1,10,100}
        rc = ref_map.get(sym)
        if rc and rc > 0:
            ratio = df["close"][-1] / rc
            scale = min((1, 10, 100), key=lambda k: abs(ratio - k))
            if abs(ratio - scale) > 0.05 * scale:
                print(f"  scale异常 {sym}: ratio={ratio:.3f} 跳过", flush=True)
                continue
            if scale != 1:
                df = df.with_columns([
                    (pl.col(c) / scale).alias(c) for c in ("open", "high", "low", "close")
                ])
        batch.append(df.select(["symbol", "date", "open", "high", "low", "close", "volume", "amount"]))
        done += 1
        if len(batch) >= BATCH_N:
            _flush(repo, batch, factors)
            batch = []
            print(f"progress: {done} symbols, {time.time()-t0:.0f}s", flush=True)
    if batch:
        _flush(repo, batch, factors)
    print(f"BACKFILL DONE: {done} symbols, {time.time()-t0:.0f}s", flush=True)

    parts = sorted((DATA_DIR / "kline_etf_daily").glob("date=*"))
    print("etf daily partitions:", len(parts), parts[0].name, "->", parts[-1].name, flush=True)

def _flush(repo, frames: list[pl.DataFrame], factors: pl.DataFrame) -> None:
    raw = pl.concat(frames, how="diagonal_relaxed").unique(subset=["symbol", "date"], keep="last").sort(["symbol", "date"])
    syms = raw["symbol"].unique().to_list()
    f = factors.filter(pl.col("symbol").is_in(syms)) if not factors.is_empty() else factors
    enriched = compute_enriched(raw, factors=f, instruments=None)
    repo.append_etf_daily(raw)
    repo.append_etf_enriched(enriched)
    gc.collect()

if __name__ == "__main__":
    main()

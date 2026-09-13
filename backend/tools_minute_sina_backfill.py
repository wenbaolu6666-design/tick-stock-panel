# -*- coding: utf-8 -*-
"""新浪 1m 分钟库回补 (09-07~09-09): 低并发+退避重试+断点续传, 腾讯兜底当日.
用法: python tools_minute_sina_backfill.py"""
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import polars as pl

DATA_DIR = Path("..", "data").resolve()
T0 = datetime(2026, 9, 7, 9, 25)
T1 = datetime(2026, 9, 9, 15, 5)
WORKERS = 3
DAYS = ("2026-09-07", "2026-09-08", "2026-09-09")


def _existing_syms(dirpath: Path) -> dict[str, set]:
    out = {}
    for d in DAYS:
        p = dirpath / f"date={d}" / "part.parquet"
        if p.exists():
            out[d] = set(pl.read_parquet(p, columns=["symbol"])["symbol"].to_list())
        else:
            out[d] = set()
    return out


def main():
    from app.data_providers import custom as cs
    from app.services.kline_sync import _write_minute_partition
    from app.tickflow.repository import DataStore, KlineRepository

    repo = KlineRepository(DataStore(DATA_DIR))
    sina = cs.get_provider("sina_ext")
    tencent = cs.get_provider("tencent_quote")

    stocks = pl.read_parquet(DATA_DIR / "instruments" / "instruments.parquet", columns=["symbol"])["symbol"].to_list()
    etfs = pl.read_parquet(DATA_DIR / "instruments_etf" / "instruments_etf.parquet", columns=["symbol"])["symbol"].to_list()
    dirs = {"stock": DATA_DIR / "kline_minute", "etf": DATA_DIR / "kline_etf_minute"}
    have = {at: _existing_syms(d) for at, d in dirs.items()}

    jobs = []
    for sym, at in [(s, "stock") for s in sorted(set(stocks))] + [(s, "etf") for s in sorted(set(etfs))]:
        missing = [d for d in DAYS if sym not in have[at][d]]
        if missing:
            jobs.append((sym, at, missing))
    print(f"pending: {len(jobs)} syms (already complete: {len(stocks)+len(etfs)-len(jobs)})", flush=True)

    written = [0]
    done = [0]
    errs = []
    lock = __import__("threading").Lock()
    # _write_minute_partition 读改写非线程安全: 并发写同一日期分区会撞坏 parquet
    # (正规调用方 sync_and_persist_minute 均包 repo._write_lock, 此脚本必须同锁)
    write_lock = repo._write_lock

    def fetch_with_retry(sym, at, attempts=4):
        for k in range(attempts):
            try:
                df = sina.get_minute([sym], start_time=T0, end_time=T1, freq="1m")
                if df is not None and not df.is_empty():
                    return df
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1.5 * (k + 1))
        # 腾讯兜底: 只有最近一日 (09-09)
        if "2026-09-09" in missing_of(sym, at):
            try:
                df = tencent.get_minute([sym], start_time=T0, end_time=T1, freq="1m")
                if df is not None and not df.is_empty():
                    return df
            except Exception:  # noqa: BLE001
                pass
        return None

    def missing_of(sym, at):
        return dict(jobs_map).get((sym, at), [])

    jobs_map = {(s, a): m for s, a, m in jobs}

    def one(job):
        sym, at, missing = job
        df = fetch_with_retry(sym, at)
        if df is None or df.is_empty():
            with lock:
                errs.append(sym)
        else:
            with write_lock:
                n = _write_minute_partition(df, dirs[at])
            with lock:
                written[0] += n
        with lock:
            done[0] += 1
            if done[0] % 400 == 0:
                print(f"  {done[0]}/{len(jobs)} syms, +{written[0]} rows, {time.time()-t0:.0f}s", flush=True)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(one, jobs))
    print(f"DONE: {done[0]} syms, {written[0]} rows, {time.time()-t0:.0f}s, errs={len(errs)}", flush=True)
    for e in errs[:8]:
        print("  miss:", e, flush=True)

    try:
        d = DATA_DIR.as_posix()
        repo.db.execute(f"""CREATE OR REPLACE VIEW kline_minute AS
            SELECT * FROM read_parquet('{d}/kline_minute/**/*.parquet', union_by_name=true)""")
        repo.db.execute(f"""CREATE OR REPLACE VIEW kline_etf_minute AS
            SELECT * FROM read_parquet('{d}/kline_etf_minute/**/*.parquet', union_by_name=true)""")
        print("views refreshed", flush=True)
    except Exception as e:
        print("view refresh failed:", e, flush=True)


if __name__ == "__main__":
    main()

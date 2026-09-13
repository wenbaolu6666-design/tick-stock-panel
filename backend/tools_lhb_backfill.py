# -*- coding: utf-8 -*-
"""龙虎榜历史回补: 按交易日拉东财整榜, 落盘 data/ext_data/lhb_history.parquet."""
import time
from datetime import date, timedelta
from pathlib import Path

import polars as pl

DATA_DIR = Path("..", "data").resolve()
OUT = DATA_DIR / "ext_data" / "lhb_history.parquet"
START = date(2025, 1, 1)

def main():
    from app.plugins.astock_ext.provider import AstockExtProvider
    from app.services.mining_preflight import enriched_partition_dates

    prov = AstockExtProvider()
    raw_days = enriched_partition_dates(DATA_DIR, "stock")
    days = [d if isinstance(d, date) else date.fromisoformat(str(d)[:10]) for d in raw_days]
    days = [d for d in days if d >= START]
    print(f"trading days to pull: {len(days)} ({days[0]} -> {days[-1]})", flush=True)

    frames = []
    if OUT.exists():
        old = pl.read_parquet(OUT)
        frames.append(old)
        have = set(old["date"].unique().to_list())
        days = [d for d in days if d.isoformat() not in have]
        print(f"already have {len(have)} days, pulling {len(days)}", flush=True)

    ok = fail = 0
    for i, d in enumerate(days):
        df = prov.get_lhb_board(d.isoformat())
        if not df.is_empty():
            frames.append(df)
            ok += 1
        else:
            fail += 1
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(days)} ok={ok} fail={fail} {time.time()-_t0:.0f}s", flush=True)
        time.sleep(0.8)

    if frames:
        out = pl.concat(frames, how="diagonal_relaxed").unique(subset=["symbol", "date", "reason"], keep="last").sort(["date", "net_buy"], descending=True)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        tmp = OUT.with_suffix(".parquet.tmp")
        out.write_parquet(tmp)
        tmp.replace(OUT)
        print(f"DONE: {out.height} rows, {out['date'].n_unique()} days, ok={ok} fail={fail}", flush=True)
    else:
        print("no data pulled", flush=True)

_t0 = time.time()
if __name__ == "__main__":
    main()

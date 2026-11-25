# -*- coding: utf-8 -*-
"""东财系历史分钟 CSV 导入 v2: 按年 zip → TSP kline_minute 分区库。

v1 教训: 按"标的"循环 + 单标的 merge = 每标的读写全市场分区文件(O(n²), 实测
CPU 烧 176 分钟零产出卡死)。v2 反转: 先把一年 ~26000 万行按月聚合内存化,
再按"日期分区"一次性合并所有标的 → 每分区只读写 1 次(~250 次写盘/年)。

内存: 单月全市场 ≈ 130 万行 × 8 列 f64/i64 ≈ 100MB, 安全。
每天一天 fail-loud: 源 csv 的 <时间> 已按 1 分钟粒度保证。

用法: python tools_minute_history_import.py <start_year> [end_year]
幂等: _write_minute_partition 的 merge(只对触及 symbol 去重)保证重跑安全。
"""
import csv
import io
import os
import sys
import time
import zipfile
from pathlib import Path

import polars as pl

# 源 zip 目录: 东财按年汇总。E:/a 已于 2026-11-21 清理, 重新导入前需先下载新源,
# 并用环境变量 TSP_MINUTE_SRC 指向新目录(默认值仅为历史路径)。
ZIP_DIR = Path(os.environ.get("TSP_MINUTE_SRC", r"E:/a/A股_分时数据_沪深/1分钟_按年汇总"))
# 目标数据根: 与后端 settings.data_dir 一致(现为 E:/tsp_data), 可用 TSP_DATA_DIR 覆盖。
DATA_DIR = Path(os.environ.get("TSP_DATA_DIR", r"E:/tsp_data"))


def read_year_to_monthly(z: zipfile.ZipFile, year: int) -> dict[int, pl.DataFrame]:
    """读全年 csv → {月份: 长表}。行转列用向量化构造, 逐文件解析。"""
    monthly: dict[int, list[pl.DataFrame]] = {}
    for name in sorted(z.namelist()):
        base = name.rsplit("_", 1)[0]
        mkt, code = base[:2], base[2:]
        if mkt not in ("sz", "sh") or not code.isdigit():
            continue
        symbol = f"{code}.{mkt.upper()}"
        text = z.read(name).decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        ts, o, h, l, c, v, a = [], [], [], [], [], [], []
        for r in reader:
            if not r.get("时间"):
                continue
            ts.append(r["时间"])
            o.append(float(r["开盘价"]))
            h.append(float(r["最高价"]))
            l.append(float(r["最低价"]))
            c.append(float(r["收盘价"]))
            v.append(int(float(r["成交量"])) * 100)   # 手 → 股
            a.append(float(r["成交额"]))
        if not ts:
            continue
        df = pl.DataFrame({
            "symbol": [symbol] * len(ts),
            "datetime": ts,
            "open": o, "high": h, "low": l, "close": c,
            "volume": v, "amount": a,
        }).with_columns(
            pl.col("datetime").str.to_datetime("%Y-%m-%d %H:%M:%S"),
            pl.col("open", "high", "low", "close", "amount").cast(pl.Float64),
        ).filter(pl.col("datetime").dt.year() == year)
        if df.is_empty():
            continue
        m = df["datetime"].dt.month().item(0) if False else None
        for part in df.with_columns(pl.col("datetime").dt.month().alias("_m")).partition_by("_m"):
            monthly.setdefault(int(part["_m"][0]), []).append(part.drop("_m"))
    return {m: pl.concat(parts) for m, parts in monthly.items()}


def main() -> None:
    if not ZIP_DIR.exists():
        print(
            f"[FATAL] 源 zip 目录不存在: {ZIP_DIR}\n"
            "        E:/a 已于 2026-11-21 清理。重新导入前请从东财网页端下载按年汇总 zip\n"
            "        (http://www.dyyboard.ltd, 每日盘后更新), 并用环境变量 TSP_MINUTE_SRC 指向新目录。",
            flush=True,
        )
        sys.exit(2)
    y0 = int(sys.argv[1])
    y1 = int(sys.argv[2]) if len(sys.argv) > 2 else y0
    from app.services import kline_sync
    from app.tickflow.repository import DataStore, KlineRepository

    repo = KlineRepository(DataStore(DATA_DIR))
    minute_dir = DATA_DIR / "kline_minute"

    for year in range(y0, y1 + 1):
        zp = ZIP_DIR / f"{year}_1min.zip"
        if not zp.exists():
            print(f"{year}: zip 不存在, 跳过", flush=True)
            continue
        t0 = time.time()
        monthly = read_year_to_monthly(zipfile.ZipFile(zp), year)
        print(f"{year}: 解析出 {sum(df.height for df in monthly.values())} 行 / {len(monthly)} 个月 | {time.time()-t0:.0f}s", flush=True)

        written = 0
        for mon in sorted(monthly):
            df_m = monthly[mon].with_columns(pl.col("datetime").dt.date().alias("_d"))
            # 关键: 按"日期"一次性聚合所有标的 → 每分区一次 merge(而不是每标的一次)
            with repo._write_lock:
                for day_df in df_m.partition_by("_d"):
                    written += kline_sync._write_minute_partition(day_df.drop("_d"), minute_dir)
            print(f"  {mon} 月写入推进至 {written} (+{df_m.height}) | {time.time()-t0:.0f}s", flush=True)
        print(f"{year} 完成: merge 净写 {written} | {time.time()-t0:.0f}s", flush=True)

    print("分区总数:", len(list(minute_dir.glob("date=*"))), flush=True)


if __name__ == "__main__":
    main()

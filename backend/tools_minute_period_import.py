# -*- coding: utf-8 -*-
"""东财系多周期历史分钟 CSV 导入: 按年 zip → TSP kline_minute_{p} 分区库。

覆盖三类此前未接入 TSP 的数据:
  1. 沪深 5/15/30/60 分钟 (2000-2025)  → data/kline_minute_5m|15m|30m|60m
  2. 京市(北交所) 1/5/15/30/60 (2020-2025) → 同上 (symbol 后缀 .BJ)
  3. 指数 1 分钟 (2017-2021)           → data/kline_index_minute
  4. 东财 price_zip 5 分钟 (2010-2016, 2026) → data/kline_minute_5m

与 tools_minute_history_import.py 同一套分区约定:
  data/<table>/date=YYYY-MM-DD/part.parquet, 列 symbol/datetime/open/high/low/close/volume/amount。

用法:
  python tools_minute_period_import.py 5m 15m 30m 60m index
  python tools_minute_period_import.py pz5m           # 东财 price_zip 5 分钟补缺口
幂等: 按 (symbol, datetime) 去重, 重跑安全。
pz5m 只写现表缺失的日期分区, 不覆盖已有数据。
"""
from __future__ import annotations

import csv
import io
import os
import sys
import time
import zipfile
from pathlib import Path

import polars as pl

# 源数据根: 东财按年 zip。E:/a 已于 2026-11-21 清理, 重新导入前需先下载新源,
# 并用环境变量 TSP_SRC_ROOT 指向它(默认值仅为历史路径)。
SRC = Path(os.environ.get("TSP_SRC_ROOT", r"E:/a"))
# 目标数据根: 与后端 settings.data_dir 一致, 可用 TSP_DATA_DIR 覆盖。
DATA_DIR = Path(os.environ.get("TSP_DATA_DIR", r"E:/tsp_data"))

# 周期 → (源目录, 目标表名, zip 内文件名周期标记)
PERIODS: dict[str, tuple[str, str, str]] = {
    "5m":  ("A股_分时数据_沪深/5分钟_按年汇总",  "kline_minute_5m",  "5min"),
    "15m": ("A股_分时数据_沪深/15分钟_按年汇总", "kline_minute_15m", "15min"),
    "30m": ("A股_分时数据_沪深/30分钟_按年汇总", "kline_minute_30m", "30min"),
    "60m": ("A股_分时数据_沪深/60分钟_按年汇总", "kline_minute_60m", "60min"),
}

# 北交所(京市)目录同样按周期分子目录, 目标表与沪深共用
BJ_DIRS: dict[str, str] = {
    "5m": "A股_分时数据_京市/5分钟_按年汇总",
    "15m": "A股_分时数据_京市/15分钟_按年汇总",
    "30m": "A股_分时数据_京市/30分钟_按年汇总",
    "60m": "A股_分时数据_京市/60分钟_按年汇总",
    "1m": "A股_分时数据_京市/1分钟_按年汇总",
}

# 东财 price_zip: 每日一个 parquet, 全市场, 49 根(含 09:30 集合竞价)
PZ5M_DIR = "A股分钟数据/5m_price_zip"
PZ5M_TABLE = "kline_minute_5m"

INDEX_DIR = "A股_分时数据_指数/1分钟_按年汇总"
INDEX_TABLE = "kline_index_minute"

MARKETS = ("sz", "sh", "bj")


def _symbol_of(base: str) -> str | None:
    """sh600000_2017 / bj920010_2020 / sh600000 → 600000.SH。"""
    mkt, code = base[:2], base[2:]
    if mkt not in MARKETS or not code.isdigit():
        return None
    return f"{code}.{mkt.upper()}"


def _index_symbol(code: str) -> str | None:
    """指数代码 → symbol。000/899/930 → SH, 399 → SZ, 8/920/43 → BJ。"""
    if not code.isdigit() or len(code) != 6:
        return None
    if code.startswith(("399",)):
        return f"{code}.SZ"
    if code.startswith(("8", "920", "43")):
        return f"{code}.BJ"
    return f"{code}.SH"


def _year_span(zip_name: str) -> tuple[int, int] | None:
    """2000_2005_5min.zip → (2000, 2005); 2017_5min.zip → (2017, 2017)。"""
    head = zip_name.split("_")[0]
    if not head.isdigit():
        return None
    if "_" in zip_name:
        tail = zip_name.split("_")[1]
        if tail.isdigit() and len(tail) == 4:
            return int(head), int(tail)
    return int(head), int(head)


def _read_zip(zp: Path, y0: int, y1: int, *, index: bool) -> dict[int, list[pl.DataFrame]]:
    """读一个 zip → {月份: [长表]}。"""
    monthly: dict[int, list[pl.DataFrame]] = {}
    with zipfile.ZipFile(zp) as z:
        for name in sorted(z.namelist()):
            if not name.endswith(".csv"):
                continue
            base = name.rsplit("_", 1)[0] if "_" in name else name[:-4]
            if index:
                symbol = _index_symbol(base)
            else:
                symbol = _symbol_of(base)
            if symbol is None:
                continue
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
                v.append(int(float(r["成交量"])) * 100 if r.get("成交量") else None)
                a.append(float(r["成交额"]))
            if not ts:
                continue
            # volume 显式 cast Float64: 与 kline_minute / kline_index_daily 口径一致;
            # 指数源 CSV 无成交量列时全为 None, 不 cast 会得到 Null 类型列,
            # 下游任何 volume 运算 (比较/求和) 都会 NotImplementedError。
            df = pl.DataFrame({
                "symbol": [symbol] * len(ts),
                "datetime": ts,
                "open": o, "high": h, "low": l, "close": c,
                "volume": v, "amount": a,
            }).with_columns(
                pl.col("datetime").str.to_datetime("%Y-%m-%d %H:%M:%S"),
                pl.col("open", "high", "low", "close", "amount", "volume").cast(pl.Float64),
            ).filter(
                (pl.col("datetime").dt.year() >= y0) & (pl.col("datetime").dt.year() <= y1)
            )
            if df.is_empty():
                continue
            for part in df.with_columns(pl.col("datetime").dt.month().alias("_m")).partition_by("_m"):
                monthly.setdefault(int(part["_m"][0]), []).append(part.drop("_m"))
    return monthly


def _merge_auction_bar(df: pl.DataFrame) -> pl.DataFrame:
    """09:30 集合竞价根并入 09:35, 得到与既有 kline_minute_5m 一致的 48 根口径。

    依据: price_zip 每日 49 根 (09:30..15:00), 既有表 48 根 (09:35..15:00);
    合并后 OHLC 与既有表逐根吻合, 成交量差 ~0.4% 属源差异。
    集合竞价根时序在前, 故取其 open 作为合并根 open, close 取 09:35 收盘。
    """
    hhmm = pl.col("datetime").dt.strftime("%H:%M")
    auc = df.filter(hhmm == "09:30")
    if auc.is_empty():
        return df
    auc = auc.with_columns(
        pl.col("datetime").dt.offset_by("35m").alias("datetime"),
        pl.lit(0).alias("_seq"),
    )
    rest = df.filter(hhmm != "09:30").with_columns(pl.lit(1).alias("_seq"))
    return (
        pl.concat([auc, rest], how="diagonal_relaxed")
        .sort(["symbol", "datetime", "_seq"])
        .group_by(["symbol", "datetime"], maintain_order=True)
        .agg(
            [
                pl.col("open").first(),
                pl.col("high").max(),
                pl.col("low").min(),
                pl.col("close").last(),
                pl.col("volume").sum(),
                pl.col("amount").sum(),
            ]
        )
    )


def _read_price_zip(zp: Path) -> dict[int, list[pl.DataFrame]]:
    """读东财 price_zip 的一个年份 zip -> {月份: [长表]}。

    每日一个 parquet, 全市场 (SZ/SH/BJ), 列 code/trade_time/OHLC/vol/amount,
    Float32 精度。只补 kline_minute_5m 缺失的日期 (调用方 only_missing)。
    """
    monthly: dict[int, list[pl.DataFrame]] = {}
    with zipfile.ZipFile(zp) as z:
        for name in sorted(z.namelist()):
            if not name.endswith(".parquet"):
                continue
            df = pl.read_parquet(io.BytesIO(z.read(name)))
            if df.is_empty():
                continue
            df = (
                df.rename({"code": "symbol", "trade_time": "datetime", "vol": "volume"})
                .select("symbol", "datetime", "open", "high", "low", "close", "volume", "amount")
                .with_columns(
                    pl.col("datetime").str.to_datetime("%Y-%m-%d %H:%M:%S"),
                    pl.col("open", "high", "low", "close", "volume", "amount").cast(pl.Float64),
                )
            )
            df = _merge_auction_bar(df)
            if df.is_empty():
                continue
            for part in df.with_columns(pl.col("datetime").dt.month().alias("_m")).partition_by("_m"):
                monthly.setdefault(int(part["_m"][0]), []).append(part.drop("_m"))
    return monthly


def _write_partitions(
    monthly: dict[int, list[pl.DataFrame]], table_dir: Path, *, only_missing: bool = False
) -> int:
    """按日期分区原子合并写入, 每分区一次读写。

    only_missing=True 时跳过已存在的日期分区 (用于 price_zip 补缺口,
    避免用另一数据源覆盖已入库的数据)。
    """
    written = 0
    for mon in sorted(monthly):
        df_m = pl.concat(monthly[mon]).with_columns(pl.col("datetime").dt.date().alias("_d"))
        for day_df in df_m.partition_by("_d"):
            d = day_df["_d"][0]
            out = table_dir / f"date={d}" / "part.parquet"
            if only_missing and out.exists():
                continue
            incoming = day_df.drop("_d").unique(subset=["symbol", "datetime"], keep="last")
            if out.exists():
                existing = pl.read_parquet(out)
                merged = pl.concat([existing, incoming], how="diagonal_relaxed").unique(
                    subset=["symbol", "datetime"], keep="last"
                )
            else:
                merged = incoming
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + ".tmp")
            merged.sort(["symbol", "datetime"]).write_parquet(tmp)
            tmp.replace(out)
            written += incoming.height
    return written


def _run_dir(sub: str, table: str, tag: str, *, only_missing: bool = False) -> None:
    base = SRC / sub
    if not base.exists():
        print(f"[{tag}] 源目录不存在: {base}", flush=True)
        return
    table_dir = DATA_DIR / table
    zips = sorted(base.glob("*.zip"))
    if not zips:
        print(f"[{tag}] 无 zip: {base}", flush=True)
        return
    for zp in zips:
        span = _year_span(zp.stem)
        if span is None:
            print(f"[{tag}] 跳过无法解析年份: {zp.name}", flush=True)
            continue
        t0 = time.time()
        monthly = _read_zip(zp, span[0], span[1], index=(table == INDEX_TABLE))
        rows = sum(sum(f.height for f in v) for v in monthly.values())
        if not rows:
            print(f"[{tag}] {zp.name}: 0 行, 跳过", flush=True)
            continue
        written = _write_partitions(monthly, table_dir, only_missing=only_missing)
        print(f"[{tag}] {zp.name}: 解析 {rows} 行 / {len(monthly)} 月, 净写 {written} | {time.time()-t0:.0f}s", flush=True)
    n = len(list(table_dir.glob("date=*"))) if table_dir.exists() else 0
    print(f"[{tag}] 完成, {table} 分区数 {n}", flush=True)


def _run_price_zip() -> None:
    """东财 price_zip 5 分钟 (2010-2016, 2026) 补 kline_minute_5m 缺口。

    只写现表缺失的日期分区: 与既有 2017-2025 数据源不同, 不覆盖。
    """
    base = SRC / PZ5M_DIR
    if not base.exists():
        print(f"[pz5m] 源目录不存在: {base}", flush=True)
        return
    table_dir = DATA_DIR / PZ5M_TABLE
    for zp in sorted(base.glob("*.zip")):
        t0 = time.time()
        monthly = _read_price_zip(zp)
        rows = sum(sum(f.height for f in v) for v in monthly.values())
        if not rows:
            print(f"[pz5m] {zp.name}: 0 行, 跳过", flush=True)
            continue
        written = _write_partitions(monthly, table_dir, only_missing=True)
        print(
            f"[pz5m] {zp.name}: 解析 {rows} 行 / {len(monthly)} 月, 净写 {written} | {time.time()-t0:.0f}s",
            flush=True,
        )
    n = len(list(table_dir.glob("date=*"))) if table_dir.exists() else 0
    print(f"[pz5m] 完成, {PZ5M_TABLE} 分区数 {n}", flush=True)


def main() -> None:
    if not SRC.exists():
        print(
            f"[FATAL] 源数据根不存在: {SRC}\n"
            "        E:/a 已于 2026-11-21 清理。重新导入前请下载新源(东财网页端\n"
            "        http://www.dyyboard.ltd), 并用环境变量 TSP_SRC_ROOT 指向它。",
            flush=True,
        )
        sys.exit(2)
    targets = sys.argv[1:] or ["5m", "15m", "30m", "60m", "index"]
    for t in targets:
        if t == "pz5m":
            _run_price_zip()
        elif t == "index":
            _run_dir(INDEX_DIR, INDEX_TABLE, "index-1m")
        elif t in PERIODS:
            sub, table, _ = PERIODS[t]
            _run_dir(sub, table, f"hs-{t}")
            bj = BJ_DIRS.get(t)
            if bj:
                _run_dir(bj, table, f"bj-{t}")
        else:
            print(f"未知目标: {t} (可选 5m/15m/30m/60m/index/pz5m)", flush=True)


if __name__ == "__main__":
    main()

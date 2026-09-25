# -*- coding: utf-8 -*-
"""股票全量数据.zip(估值/股本日频历史) → TSP `data/valuation_history/part.parquet`。

zip 内 5789 个 UTF-8 CSV(每股一个, 600519.SH.csv 形式), 30 列(中文列名),
覆盖 1991~2025 日频。与 TSP 报告期口径的 financials/shares 语义不同, 独立成表不动原表。
列名见 CSV_COLS; 全列按 Utf8 读入(CSV 内各列混整数/浮点, 推断会失败)。
"""
import sys
import time
import zipfile
from pathlib import Path

import polars as pl

SRC = r'D:\BaiduNetdiskDownload\股票全量数据.zip'
DATA_DIR = Path('..', 'data').resolve()
OUT = DATA_DIR / 'valuation_history' / 'part.parquet'

CSV_COLS = [
    '股票代码', '股票名称', '交易日', '开盘价', '最高价', '最低价', '收盘价', '前收盘价',
    '涨跌额', '涨跌幅（%）', '成交量（手）', '成交额（千元）', '换手率（%）', '换手率（%，自由流通股）',
    '量比', '市盈率（亏损为空）', '市盈率（TTM，亏损为空）', '市净率', '市销率', '市销率（TTM）',
    '股息率（%）', '股息率（%，TTM）', '总股本（万股）', '流通股本（万股）', '自由流通股本（万）',
    '总市值（万元）', '流通市值（万元）', '复权因子', '当日涨停价', '当日跌停价',
]
RENAME = {
    '股票代码': 'symbol', '股票名称': 'name', '交易日': 'date',
    '开盘价': 'open', '最高价': 'high', '最低价': 'low', '收盘价': 'close',
    '前收盘价': 'preclose', '涨跌额': 'change', '涨跌幅（%）': 'change_pct',
    '成交量（手）': 'volume', '成交额（千元）': 'amount', '换手率（%）': 'turnover_rate',
    '量比': 'volume_ratio', '市盈率（亏损为空）': 'pe', '市盈率（TTM，亏损为空）': 'pe_ttm',
    '市净率': 'pb', '市销率': 'ps', '市销率（TTM）': 'ps_ttm',
    '股息率（%）': 'dividend_yield', '股息率（%，TTM）': 'dividend_yield_ttm',
    '总股本（万股）': 'total_shares_wan', '流通股本（万股）': 'float_shares_wan',
    '自由流通股本（万）': 'freefloat_shares_wan', '总市值（万元）': 'total_value_wan',
    '流通市值（万元）': 'float_value_wan', '复权因子': 'adj_factor',
    '当日涨停价': 'limit_up', '当日跌停价': 'limit_down',
}
DTYPES = {c: pl.Utf8 for c in CSV_COLS}


def main() -> None:
    import time
    t0 = time.time()
    total = 0
    done: set = set()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    batch: list = []
    shard = 0
    with zipfile.ZipFile(SRC) as zf:
        names = sorted(n for n in zf.namelist() if n.lower().endswith('.csv'))
        print(f"zip 内 {len(names)} 个 CSV", flush=True)
        for i, n in enumerate(names, 1):
            try:
                df = pl.read_csv(
                    zf.read(n), dtypes=DTYPES, truncate_ragged_lines=True,
                    try_parse_dates=False, null_values=['', 'None'],
                )
                if df.is_empty() or '股票代码' not in df.columns or '交易日' not in df.columns:
                    continue
                df = df.rename(RENAME)
                float_cols = ('open', 'high', 'low', 'close', 'preclose', 'change',
                              'change_pct', 'amount', 'volume_ratio', 'pe', 'pe_ttm', 'pb',
                              'ps', 'ps_ttm', 'dividend_yield', 'dividend_yield_ttm',
                              'total_shares_wan', 'float_shares_wan', 'freefloat_shares_wan',
                              'total_value_wan', 'float_value_wan', 'adj_factor',
                              'limit_up', 'limit_down', 'volume', 'turnover_rate')
                for c in float_cols:
                    if c in df.columns:
                        df = df.with_columns(pl.col(c).cast(pl.Float64, strict=False))
                df = df.with_columns(pl.col('date').str.to_date('%Y%m%d', strict=False)).drop_nulls(subset=['date'])
                if df.is_empty():
                    continue
                cols = ['symbol', 'date'] + [v for k, v in RENAME.items() if k not in ('股票代码', '交易日', '股票名称') and v in df.columns]
                batch.append(df.select(cols))
                total += df.height
                done.update(df['symbol'].unique().to_list())
            except Exception as e:  # noqa: BLE001
                print(f"  {n} 失败: {str(e)[:70]}", flush=True)
            if i % 500 == 0 and batch:
                shard += 1
                pl.concat(batch, how='diagonal_relaxed').write_parquet(OUT.with_name(f'part-{shard:03d}.parquet'))
                batch = []
                print(f"  分片 {shard} 写出 ({total:,} 行, {len(done)} 股, {time.time()-t0:.0f}s)", flush=True)
            if i % 500 == 0 or i == len(names):
                print(f"  [{i}/{len(names)}] {total:,} 行 {len(done)} 股 ({time.time()-t0:.0f}s)", flush=True)
    if batch:
        shard += 1
        pl.concat(batch, how='diagonal_relaxed').write_parquet(OUT.with_name(f'part-{shard:03d}.parquet'))
    print(f"完成: {total:,} 行 {len(done)} 股 {shard} 分片 ({time.time()-t0:.0f}s)", flush=True)



if __name__ == '__main__':
    main()

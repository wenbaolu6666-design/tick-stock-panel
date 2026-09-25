# -*- coding: utf-8 -*-
"""通达信板块分时导入: 百度盘『通达信板块_分时数据』→ TSP board_minute 表。

结构: 百度盘/{周期}_按月归档/YYYY-MM/YYYYMMDD_{周期}.zip → 内层每板块
一个 880xxx.csv(列: 时间,代码,名称,开盘价,昨收价,最高价,最低价,成交量,
成交额,振幅,均价; GBK; 1 分钟 09:31 起)。
产出: data/board_minute/{period}/date=YYYY-MM-DD/part.parquet
      cols: datetime(naive 北京墙钟) board_code board_name open preclose high low volume amount amplitude avg
幂等: 已存在的 (period, date) 分区跳过。
"""
import os
import sys
import time
import zipfile
from datetime import datetime

import polars as pl

SRC = r'D:\BaiduNetdiskDownload\通达信板块_分时数据'
DATA_DIR = Path('..', 'data') if False else None
from pathlib import Path
DATA_DIR = Path('..', 'data').resolve()

PERIODS = ['1分钟', '5分钟', '15分钟', '30分钟', '60分钟']
PERIOD_DIR = {'1分钟': 'm1', '5分钟': 'm5', '15分钟': 'm15', '30分钟': 'm30', '60分钟': 'm60'}


def month_dirs(period_dir: str, only: list[str] | None = None) -> list[str]:
    base = os.path.join(SRC, f'{period_dir}_按月归档')
    if not os.path.isdir(base):
        return []
    months = sorted(os.listdir(base))
    if only:
        months = [m for m in months if m in only]
    return [os.path.join(base, m) for m in months]


def process_day_zip(zip_path: str, day: str, period: str) -> pl.DataFrame | int:
    out_dir = DATA_DIR / 'board_minute' / PERIOD_DIR[period]
    done_dir = DATA_DIR / 'board_minute' / PERIOD_DIR[period] / f"date={day}"
    part = done_dir / 'part.parquet'
    if part.exists():
        return 0
    frames = []
    with zipfile.ZipFile(zip_path) as zf:
        for n in zf.namelist():
            if not n.lower().endswith('.csv'):
                continue
            raw = zf.read(n)
            text = raw.decode('gbk', errors='replace')
            lines = text.splitlines()
            if len(lines) < 2:
                continue
            board = None
            name = None
            for ln in lines[1:]:
                ln = ln.strip()
                if not ln:
                    continue
                f = ln.split(',')
                if len(f) < 10:
                    continue
                board = f[1].strip()
                name = f[2].strip()
                try:
                    # 时间列自带完整日期: '2011/11/03 09:31'(GBK 无 BOM 老包同款)
                    dt = datetime.strptime(f[0].strip(), "%Y/%m/%d %H:%M")
                except ValueError:
                    continue
                if not board:
                    continue
                frames.append({
                    "datetime": dt, "board_code": board, "board_name": name,
                    "open": _fn(f[3]), "preclose": _fn(f[4]), "high": _fn(f[5]),
                    "low": _fn(f[6]), "volume": _fi(f[7]), "amount": _fn(f[8]),
                    "amplitude": _fn(f[9]), "avg": _fn(f[10]) if len(f) > 10 else None,
                })
    if not frames:
        return 0
    df = pl.DataFrame(frames)
    part.parent.mkdir(parents=True, exist_ok=True)
    tmp = part.with_suffix('.parquet.tmp')
    df.write_parquet(tmp)
    tmp.replace(part)
    return df.height


def _fn(v: str):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fi(v: str):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def main() -> None:
    only_periods = sys.argv[1:] if len(sys.argv) > 1 else None
    t0 = time.time()
    for period in PERIODS:
        if only_periods and period not in only_periods:
            continue
        total_rows = 0
        total_days = 0
        for mdir in month_dirs(period):
            for f in sorted(os.scandir(mdir), key=lambda x: x.name):
                if not f.is_file() or not f.name.endswith('.zip'):
                    continue
                day = f.name.split('_')[0]
                try:
                    rows = process_day_zip(f.path, day, period)
                except Exception as e:
                    print(f"  [{PERIOD_DIR[period]}] {day} 失败: {str(e)[:60]}", flush=True)
                    continue
                if isinstance(rows, int):
                    total_rows += rows
                    if rows:
                        total_days += 1
            print(f"  [{PERIOD_DIR[period]}] {os.path.basename(mdir)} 完成 {total_days} 天 {total_rows} 行 ({time.time()-t0:.0f}s)", flush=True)
        print(f"== {period}: {total_days} 天 {total_rows} 行 ({time.time()-t0:.0f}s)", flush=True)
    print(f"全部完成 {time.time()-t0:.0f}s", flush=True)


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""修复 board_minute 的 board_name 乱码: 源 CSV 是 UTF-8(BOM), 旧导入器误按 GBK 解码。

方案: 板块名不随时间变化 — 从百度盘任一日(取最近一日)包读 (board_code, board_name)
映射(306 条, utf-8-sig 解码), 再用映射批量校正 data/board_minute/*/date=*/part.parquet
的 board_name 列(pyarrow 读-改-写)。幂等: 已是正确中文的跳过。
"""
import os
import sys
import time
import zipfile
from pathlib import Path

import pyarrow.parquet as pq
import pyarrow as pa

SRC = r'D:\BaiduNetdiskDownload\通达信板块_分时数据'
BM = Path('..', 'data', 'board_minute').resolve()
PERIOD_DIR = {'1分钟': 'm1', '5分钟': 'm5', '15分钟': 'm15', '30分钟': 'm30', '60分钟': 'm60'}


def build_name_map() -> dict[str, str]:
    # 取 1 分钟周期最近的月份中最近一天的包
    base = os.path.join(SRC, '1分钟_按月归档')
    months = sorted(os.listdir(base))
    latest_month = months[-1]
    mdir = os.path.join(base, latest_month)
    zips = sorted(f for f in os.listdir(mdir) if f.endswith('.zip'))
    zpath = os.path.join(mdir, zips[-1])
    mapping: dict[str, str] = {}
    with zipfile.ZipFile(zpath) as zf:
        for n in zf.namelist():
            if not n.lower().endswith('.csv'):
                continue
            lines = zf.read(n).decode('utf-8-sig', errors='replace').splitlines()
            for ln in lines[1:]:
                f = ln.split(',')
                if len(f) > 2:
                    code, name = f[1].strip(), f[2].strip()
                    if code:
                        mapping[code] = name
                    break
    return mapping


def main() -> None:
    t0 = time.time()
    mapping = build_name_map()
    print(f"name map: {len(mapping)} 板块, 样例: {list(mapping.items())[:3]}", flush=True)
    total = 0
    changed = 0
    for period, pdir in PERIOD_DIR.items():
        root = BM / pdir
        if not root.exists():
            continue
        files = sorted(root.glob('date=*/part.parquet'))
        print(f"[{pdir}] {len(files)} 分区", flush=True)
        for i, f in enumerate(files, 1):
            try:
                table = pq.read_table(f)
                if table.num_rows == 0 or 'board_name' not in table.column_names:
                    continue
                names = table.column('board_name').to_pylist()
                codes = table.column('board_code').to_pylist()
                new_names = [mapping.get(c, o) for c, o in zip(codes, names)]
                if new_names != names:
                    for j in range(table.num_columns):
                        if table.column_names[j] == 'board_name':
                            arr = pa.chunked_array([pa.array(new_names)])
                            table = table.set_column(j, 'board_name', arr)
                    tmp = f.with_suffix('.parquet.fix')
                    pq.write_table(table, tmp)
                    tmp.replace(f)
                    changed += 1
                total += 1
            except Exception as e:  # noqa: BLE001
                print(f"  {f} 失败: {str(e)[:60]}", flush=True)
            if i % 1000 == 0:
                print(f"  [{pdir} {i}/{len(files)}] 已改 {changed} ({time.time()-t0:.0f}s)", flush=True)
    print(f"完成: 检查 {total} 分区, 修正 {changed} 分区 ({time.time()-t0:.0f}s)", flush=True)


if __name__ == '__main__':
    main()

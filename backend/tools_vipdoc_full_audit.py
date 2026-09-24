# -*- coding: utf-8 -*-
"""vipdoc 全量逐文件审计: 日线/分钟/5分钟 全市场每只解码首尾日期与记录数。

输出(写 data/ 下):
- vipdoc_audit_lday.txt   日线全量明细+分布
- vipdoc_audit_minline.txt 分钟全量明细+分布(含段内缺口比例)
- vipdoc_audit_fzline.txt 5分钟明细
- vipdoc_audit_summary.txt 汇总(异常/断尾清单)
"""
import os
import struct
import sys
import time
from collections import Counter
from datetime import date

import numpy as np

ROOT = r'E:\tdx\vipdoc'
OUT = r'E:\WenPaul\Documents\量化炒股\tick-stock-panel\data'
LC_DT = np.dtype([('pack', '<u4'), ('o', '<f4'), ('h', '<f4'), ('l', '<f4'),
                  ('c', '<f4'), ('amt', '<f4'), ('vol', '<u4'), ('rsv', '<u4')])
DAY_REC = struct.Struct('<IIIIIfII')
TODAY_INT = 20260923
RECENT_CUT = 20260918  # 晚于此=已更新到最新交易日附近


def dg2int(dg: int) -> int:
    y = 2004 + dg // 2048
    md = dg % 2048
    return y * 10000 + md // 100 + md % 100


def scan_lday(mkt: str):
    base = os.path.join(ROOT, mkt, 'lday')
    rows = []
    for f in os.scandir(base):
        if not f.is_file():
            continue
        data = f.path and open(f.path, 'rb').read()
        n = len(data) // 32
        first = last = 0
        if n:
            first = struct.unpack_from('<I', data, 0)[0]
            last = struct.unpack_from('<I', data, (n - 1) * 32)[0]
        rows.append((os.path.basename(f.path), n, first, last))
    return rows


def scan_lc(mkt: str, kind: str, per_day: int):
    base = os.path.join(ROOT, mkt, kind)
    rows = []
    for f in os.scandir(base):
        if not f.is_file():
            continue
        data = open(f.path, 'rb').read()
        n = len(data) // 32
        first_dg = last_dg = 0
        if n:
            pack0 = struct.unpack_from('<I', data, 0)[0]
            pack1 = struct.unpack_from('<I', data, (n - 1) * 32)[0]
            first_dg = pack0 & 0xFFFF
            last_dg = pack1 & 0xFFFF
        rows.append((os.path.basename(f.path), n, dg2int(first_dg), dg2int(last_dg), n // per_day))
    return rows


def main() -> None:
    t0 = time.time()
    # ============ 日线 ============
    lday_rows = []
    for mkt in ('sh', 'sz', 'bj'):
        lday_rows += scan_lday(mkt)
    with open(os.path.join(OUT, 'vipdoc_audit_lday.txt'), 'w', encoding='utf-8') as fh:
        for name, n, first, last in sorted(lday_rows):
            flag = ''
            if n == 0:
                flag = ' EMPTY'
            elif last < RECENT_CUT:
                flag = f' STALE({last})'
            fh.write(f"{name}\t{n}\t{first}\t{last}{flag}\n")
    last_dist = Counter(r[3] // 10000 * 100 + (r[3] % 10000) // 100 for r in lday_rows)  # 按月
    stale = [r for r in lday_rows if r[2] and r[3] < RECENT_CUT and r[3] > 0]
    empty = [r for r in lday_rows if r[1] == 0]
    with open(os.path.join(OUT, 'vipdoc_audit_summary.txt'), 'w', encoding='utf-8') as fh:
        fh.write(f"== 日线 lday 全量: {len(lday_rows)} 文件 ==\n")
        fh.write(f"空文件: {len(empty)} | 断尾(尾日期<20260918): {len(stale)}\n")
        for r in stale[:40]:
            fh.write(f"  STALE {r[0]} n={r[1]} {r[2]}~{r[3]}\n")
        fh.write('尾日期按月分布: ' + str(sorted(last_dist.items())) + '\n')
        fh.write('首日期按年分布: ' + str(sorted(Counter(r[2] // 10000 for r in lday_rows).items())) + '\n')
    print(f"lday done {len(lday_rows)} ({time.time()-t0:.0f}s)", flush=True)

    # ============ 分钟 minline ============
    min_rows = []
    for mkt in ('sh', 'sz', 'bj'):
        min_rows += scan_lc(mkt, 'minline', 240)
    with open(os.path.join(OUT, 'vipdoc_audit_minline.txt'), 'w', encoding='utf-8') as fh:
        for name, n, first, last, days in sorted(min_rows):
            fh.write(f"{name}\t{n}\t{first}\t{last}\t约{days}天\n")
    # 分布: 尾日期按月; 段内缺口估算(自然日跨度/5*7 ≈ 交易日 vs n/240)
    gaps = []
    for name, n, first, last, days in min_rows:
        if first and last and n:
            span_days = (date(last // 10000, last // 100 % 100, last % 100) -
                         date(first // 10000, first // 100 % 100, first % 100)).days
            theory = max(1, int(span_days * 5 / 7))
            cover = days
            if cover < theory * 0.8:  # 覆盖不足理论 80% → 段内空洞
                gaps.append((name, first, last, days, theory))
    last_m = Counter(r[3] // 10000 * 100 + (r[3] % 10000) // 100 for r in min_rows)
    first_y = Counter(r[2] // 10000 for r in min_rows)
    with open(os.path.join(OUT, 'vipdoc_audit_summary.txt'), 'a', encoding='utf-8') as fh:
        fh.write(f"\n== 分钟 minline 全量: {len(min_rows)} 文件 ==\n")
        fh.write(f"尾日期按月分布: {str(sorted(last_m.items()))}\n")
        fh.write(f"首日期按年分布: {str(sorted(first_y.items()))}\n")
        fh.write(f"段内覆盖不足 80%(空洞)文件数: {len(gaps)}\n")
        for g in gaps[:60]:
            fh.write(f"  GAP {g[0]} {g[1]}~{g[2]} 实{g[3]}天/理论{g[4]}天\n")
    print(f"minline done {len(min_rows)} ({time.time()-t0:.0f}s)", flush=True)

    # ============ 5分钟 fzline ============
    fz_rows = []
    for mkt in ('sh', 'sz', 'bj'):
        fz_rows += scan_lc(mkt, 'fzline', 48)
    with open(os.path.join(OUT, 'vipdoc_audit_fzline.txt'), 'w', encoding='utf-8') as fh:
        for name, n, first, last, days in sorted(fz_rows):
            fh.write(f"{name}\t{n}\t{first}\t{last}\t约{days}天\n")
    print(f"fzline done {len(fz_rows)} ({time.time()-t0:.0f}s)", flush=True)

    # ============ 汇总尾部 ============
    with open(os.path.join(OUT, 'vipdoc_audit_summary.txt'), 'a', encoding='utf-8') as fh:
        fh.write(f"\n== 5分钟 fzline: {len(fz_rows)} 文件(明细见 fzline.txt) ==\n")
        last_m = Counter(r[3] // 10000 * 100 + (r[3] % 10000) // 100 for r in fz_rows)
        fh.write(f"尾日期按月分布: {str(sorted(last_m.items()))}\n")
    print(f"ALL DONE ({time.time()-t0:.0f}s)", flush=True)


if __name__ == '__main__':
    main()

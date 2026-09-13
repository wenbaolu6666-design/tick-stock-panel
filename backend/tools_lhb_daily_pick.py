# -*- coding: utf-8 -*-
"""龙虎榜每日参考清单: 最新上榜日整榜 + 各档位实测胜率参考.
定位=参考情报(上榜原因/净买强度/历史同档胜率), 非入场信号(整体负期望已验证).
用法: python tools_lhb_daily_pick.py [date:YYYY-MM-DD]  (缺省=库里最新一天)"""
import sys
from pathlib import Path
import polars as pl

DATA_DIR = Path("..", "data").resolve()

# 实测参考 (2025-01→2026-09, 非ST+净买入, T+1开盘买持3日): 按上榜原因桶
BUCKET_REF = {
    "涨幅15%": 0.472, "振幅15%": 0.456, "跌幅7%": 0.478, "三日涨幅20%": 0.429,
    "换手20%": 0.427, "换手30%": 0.408, "涨幅偏离7%": 0.432, "三日涨幅30%": 0.343,
}

def bucket_of(reason: str) -> str:
    r = reason or ""
    if "跌幅偏离值达到7%" in r: return "跌幅7%"
    if "涨幅达到15%" in r: return "涨幅15%"
    if "振幅" in r and "15%" in r: return "振幅15%"
    if "换手率达到30%" in r: return "换手30%"
    if "换手率" in r and "20%" in r: return "换手20%"
    if "涨幅偏离值累计达到30%" in r: return "三日涨幅30%"
    if "涨幅偏离值累计达到20%" in r: return "三日涨幅20%"
    if "涨幅偏离值达到7%" in r: return "涨幅偏离7%"
    return "其他"

def main():
    lhb = pl.read_parquet(DATA_DIR / "ext_data" / "lhb_history.parquet")
    lhb = lhb.with_columns(pl.col("date").cast(pl.Utf8).str.slice(0, 10).alias("d"))
    day = sys.argv[1] if len(sys.argv) > 1 else lhb["d"].max()
    day_rows = lhb.filter(pl.col("d") == day).sort("net_buy", descending=True)
    # 每股每日留净买额最大一条
    day_rows = day_rows.unique(subset=["symbol"], keep="first")
    print(f"=== 龙虎榜参考清单 {day} ({day_rows.height} 只) ===")
    print(f"{'代码':<8}{'名称':<10}{'净买(万)':>10}{'强度%':>7}  {'上榜原因':<30}{'同档历史胜率':>10}")
    for r in day_rows.to_dicts():
        nb = (r.get("net_buy") or 0) / 1e4
        strength = ""
        ref = BUCKET_REF.get(bucket_of(r.get("reason") or ""), 0.42)
        mark = "▲" if ref >= 0.46 else ("△" if ref >= 0.43 else "▽")
        print(f"{r['symbol']:<8}{str(r.get('name') or ''):<10}{nb:>10.0f}{strength:>7}  {str(r.get('reason') or '')[:28]:<30}{mark} {ref:.0%}")
    print("\n说明: 整体上榜次日买入为负期望(实测胜率42.5%), ▲档仅供参考非信号;")
    print("      强势环境下接力的历史表现显著优于弱势环境, 出手前先看市场环境页。")

if __name__ == "__main__":
    main()

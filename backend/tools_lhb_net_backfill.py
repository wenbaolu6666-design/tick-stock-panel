# -*- coding: utf-8 -*-
"""龙虎榜净买额(lhb_net 扩展数据)补齐: astock_ext 东财整榜 → lhb_net 分区。

背景: lhb_net 的 ext pull 配置丢失(9/8 后断拉), 本工具绕开 pull, 直接调
astock_ext.get_lhb_board(东财 datacenter 整榜)并按 lhb_net config 的
fields(symbol/股票简称/上榜原因/净买额/换手率/涨跌幅 + code=strip_exchange)
写入 data/ext_data/lhb_net/timeseries/date=YYYY-MM-DD/part.parquet。

用法: python tools_lhb_net_backfill.py <start:YYYY-MM-DD> <end:YYYY-MM-DD>
幂等: 已存在且非空的日期分区跳过。
"""
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import polars as pl

DATA_DIR = Path("..", "data").resolve()
OUT_DIR = DATA_DIR / "ext_data" / "lhb_net" / "timeseries"


def _suffix(code: str) -> str:
    """6 位代码 → 带交易所后缀(SH/BJ/SZ), 与 lhb_net 存量数据一致。"""
    c = code.zfill(6)
    if c.startswith(("6", "9", "5")):
        return f"{c}.SH"
    if c.startswith(("4", "8", "92")):
        return f"{c}.BJ"
    return f"{c}.SZ"


def main() -> None:
    start = date.fromisoformat(sys.argv[1])
    end = date.fromisoformat(sys.argv[2])

    from app.plugins.astock_ext.provider import AstockExtProvider

    prov = AstockExtProvider()

    # 交易日: 本地日K分区(已存在的日期 = 已同步过日线的交易日)
    days: list[date] = []
    for part in (DATA_DIR / "kline_daily").glob("date=*"):
        try:
            days.append(date.fromisoformat(part.name.removeprefix("date=")))
        except ValueError:
            continue
    days = sorted(d for d in days if start <= d <= end)
    print(f"候选交易日: {len(days)} ({days[0] if days else '-'} ~ {days[-1] if days else '-'})", flush=True)

    done, skipped, empty_days = 0, 0, []
    for i, d in enumerate(days, 1):
        out = OUT_DIR / f"date={d.isoformat()}" / "part.parquet"
        if out.exists():
            try:
                if not pl.read_parquet(out).is_empty():
                    skipped += 1
                    continue
            except Exception:
                pass
        try:
            board = prov.get_lhb_board(d.isoformat())
        except Exception as e:  # noqa: BLE001
            print(f"  {d} 拉取失败: {e}", flush=True)
            empty_days.append(d.isoformat())
            time.sleep(1)
            continue
        if board is None or board.is_empty():
            empty_days.append(d.isoformat())
            print(f"  {d} 当日无整榜(非上榜日或上游未就绪)", flush=True)
            continue
        rows = []
        for r in board.to_dicts():
            sym = _suffix(str(r.get("symbol") or ""))
            rows.append({
                "symbol": sym,
                "股票简称": r.get("name"),
                "上榜原因": r.get("reason"),
                "净买额": r.get("net_buy"),
                "换手率": r.get("turnover"),
                "涨跌幅": r.get("change_pct"),
                "code": sym.split(".")[0],
            })
        df = pl.DataFrame(rows)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(out)
        done += 1
        if i % 5 == 0 or i == len(days):
            print(f"  [{i}/{len(days)}] {d} 写入 {df.height} 行 (累计 {done})", flush=True)
        time.sleep(0.3)
    print(f"完成: 写入 {done} 天, 跳过已有 {skipped} 天, 无整榜 {len(empty_days)} 天 {empty_days}", flush=True)


if __name__ == "__main__":
    main()

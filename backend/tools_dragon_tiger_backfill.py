# -*- coding: utf-8 -*-
"""龙虎榜(dragon_tiger 三榜缓存)缺日补齐: fuyao 按日拉取落 JSON 缓存。

背景: data/dragon_tiger/date=*.json 是按日缓存(前端龙虎榜页读), 只有被
访问/拉取过的日期才有文件(历史上稀疏)。本工具按本地日K交易日逐日补齐。
用法: python tools_dragon_tiger_backfill.py <start:YYYY-MM-DD> <end:YYYY-MM-DD>
幂等: 已存在的日期跳过。
"""
import sys
from datetime import date
from pathlib import Path

DATA_DIR = Path("..", "data").resolve()


def main() -> None:
    start = date.fromisoformat(sys.argv[1])
    end = date.fromisoformat(sys.argv[2])

    from app.services import dragon_tiger as dt

    days: list[date] = []
    for part in (DATA_DIR / "kline_daily").glob("date=*"):
        try:
            days.append(date.fromisoformat(part.name.removeprefix("date=")))
        except ValueError:
            continue
    days = sorted(d for d in days if start <= d <= end)
    print(f"候选交易日: {len(days)}", flush=True)

    done, skipped, failed = 0, 0, []
    for i, d in enumerate(days, 1):
        out = dt._cache_path(DATA_DIR, d)
        if out.exists():
            try:
                if dt._load_cache(out):
                    skipped += 1
                    continue
            except Exception:
                pass
        try:
            payload = dt.get_dragon_tiger(DATA_DIR, d)
            if payload.get("state") == "ok":
                done += 1
            else:
                failed.append(f"{d}({payload.get('state')})")
        except Exception as e:  # noqa: BLE001
            failed.append(f"{d}({str(e)[:40]})")
        if i % 5 == 0 or i == len(days):
            print(f"  [{i}/{len(days)}] {d} 完成 {done} 跳过 {skipped} 失败 {len(failed)}", flush=True)
    print(f"完成: 写入 {done} 天, 跳过 {skipped}, 异常 {failed or '无'}", flush=True)


if __name__ == "__main__":
    main()

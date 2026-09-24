# -*- coding: utf-8 -*-
"""vipdoc 分钟下载监控 + 自动补齐: 轮询 vipdoc 分钟最新日期, 达到目标
(2026-09-23, 当前最新交易日)即自动跑增量回补(tools_minute_gap_backfill),
完成后写完成标记并退出。每 60 秒探测一次, 上限 720 次(12 小时)。
日志: data/vipdoc_watch_fill.log
"""
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.plugins.tdx_local import provider as tdxp

DATA_DIR = Path("..", "data").resolve()
LOG = DATA_DIR / "vipdoc_watch_fill.log"
TARGET_YMD = 20260923          # 目标: 分钟数据覆盖到 2026-09-23
BACKFILL_START = "2026-09-05"
BACKFILL_END = "2026-09-23"
DONE_FLAG = DATA_DIR / "vipdoc_fill_0923.done"
MAX_POLLS = 720


def log(msg: str) -> None:
    line = f"{datetime.now().strftime('%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def vipdoc_latest_minute() -> int:
    prov = tdxp.TdxLocalProvider()
    df = prov.get_minute(
        list(tdxp.__dict__.get("_PROBE_SYMBOLS", ("SZ000001", "SH600000", "830799.BJ")))
        or ["SZ000001", "SH600000", "830799.BJ"],
        start_time=datetime(2026, 9, 1), end_time=datetime(2026, 9, 30), freq="1m",
    )
    if df.is_empty():
        return 0
    latest = df["datetime"].max()
    return latest.year * 10000 + latest.month * 100 + latest.day


def main() -> None:
    log("监控启动: 目标分钟覆盖 >= 20260923")
    for i in range(1, MAX_POLLS + 1):
        try:
            latest = vipdoc_latest_minute()
        except Exception as e:  # noqa: BLE001
            log(f"[{i}] 探测异常: {e}")
            latest = 0
        if latest >= TARGET_YMD:
            log(f"[{i}] vipdoc 分钟已覆盖 {latest} >= {TARGET_YMD}, 启动增量回补")
            r = subprocess.run(
                [sys.executable, "tools_minute_gap_backfill.py", BACKFILL_START, BACKFILL_END],
                cwd=str(Path(__file__).resolve().parent),
            )
            if r.returncode == 0:
                DONE_FLAG.write_text(
                    f"completed {datetime.now().isoformat()} latest={latest}", encoding="utf-8")
                log("增量回补完成, 监控退出")
                return
            log(f"[{i}] 回补异常退出码 {r.returncode}, 继续监控重试")
        else:
            if i % 10 == 1:
                log(f"[{i}] vipdoc 分钟最新 {latest or 0} < {TARGET_YMD}, 等待下载...")
        time.sleep(60)
    log("超过监控上限(12h)未达标, 退出")


if __name__ == "__main__":
    main()

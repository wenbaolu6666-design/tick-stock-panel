"""vipdoc 增量同步守护: 日线锁死通达信本地(tdx_local)后的自动同步。

背景: 用户日线数据来自本地 vipdoc(百度盘下载包/通达信盘后数据下载),
更新动作发生在 TSP 之外; 此前需要手动触发同步才会进库。本守护定时
(每 30 分钟)探测 vipdoc 最新数据日期, 一旦超过库内最新日期即自动跑
盘后管道增量(override_start_date=库最新+1), 经 tdx_local provider
读入并 upsert, 口径与全库一致(不复权原始价, enriched 层统一复权)。
"""

from __future__ import annotations

import logging
import struct
from datetime import date, timedelta

logger = logging.getLogger(__name__)

# 通达信 .day 定长记录: date(int32 yyyymmdd) open/high/low/close(float32×4)
# amount(float32) volume(int32) reserved(int32) = 32 字节
_REC_SIZE = 32

# 代表标的(沪深京各一): 用于以极小成本探测 vipdoc 最新数据日期
_PROBE_SYMBOLS = ("000001.SZ", "600000.SH", "830799.BJ")


def vipdoc_latest_date(root: object = None) -> date | None:
    """vipdoc 日线最新数据日期(代表股 .day 文件尾记录, 取最大)。

    读不到任何代表股时返回 None(守护静默跳过, 不报错)。
    """
    from app.plugins.tdx_local import provider as tdx_local

    latest: date | None = None
    for symbol in _PROBE_SYMBOLS:
        path = tdx_local._path_for(symbol)
        if path is None or not path.exists():
            continue
        try:
            data = path.read_bytes()
            if len(data) < _REC_SIZE:
                continue
            (raw,) = struct.unpack_from("<i", data, len(data) - _REC_SIZE)
            if raw <= 0:
                continue
            y, md = divmod(raw, 10000)
            m, d = divmod(md, 100)
            dt = date(y, m, d)
            if latest is None or dt > latest:
                latest = dt
        except Exception as e:  # noqa: BLE001
            logger.warning("vipdoc_watch probe %s failed: %s", symbol, e)
    return latest


def check_and_sync(repo, capset) -> dict:
    """对比 vipdoc 与库最新日期, vipdoc 更新则自动增量同步。

    返回 dict(status=...) 供日志与将来 API 化; 任何失败只记录不抛出
    (守护不能影响主流程)。
    """
    from app.jobs.daily_pipeline import run_now

    vipdoc_dt = vipdoc_latest_date()
    repo_dt = repo.latest_daily_date()
    if vipdoc_dt is None or repo_dt is None:
        return {"status": "skip", "reason": "vipdoc 或库无数据", "vipdoc": str(vipdoc_dt), "repo": str(repo_dt)}
    if vipdoc_dt <= repo_dt:
        return {"status": "up-to-date", "vipdoc": str(vipdoc_dt), "repo": str(repo_dt)}
    start = repo_dt + timedelta(days=1)
    logger.info("vipdoc_watch: vipdoc=%s > repo=%s, 自动增量同步 start=%s", vipdoc_dt, repo_dt, start)
    try:
        run_now(repo, capset, override_start_date=start)
    except Exception as e:  # noqa: BLE001
        logger.warning("vipdoc_watch 增量同步失败(start=%s): %s", start, e)
        return {"status": "failed", "start": str(start), "error": str(e)}
    after = repo.latest_daily_date()
    logger.info("vipdoc_watch: 增量同步完成 repo_latest %s -> %s", repo_dt, after)
    return {"status": "synced", "vipdoc": str(vipdoc_dt), "from": str(repo_dt), "to": str(after)}


def register_vipdoc_watch(scheduler, repo, capset) -> None:
    """注册定时守护(每 30 分钟) + 启动后延迟一次(错开启动高峰与 warmup)。

    misfire_grace_time 放大: 增量同步本身可能跑几分钟, 错过窗口仍应执行。
    """

    def _job():
        from app.jobs.daily_pipeline import _get_app_state

        app_state = _get_app_state()
        capset_live = getattr(app_state, "capabilities", None) if app_state is not None else None
        result = check_and_sync(repo, capset_live or capset)
        if result.get("status") not in ("up-to-date",):
            logger.info("vipdoc_watch tick: %s", result)

    from datetime import datetime as _dt

    from apscheduler.triggers.date import DateTrigger

    scheduler.add_job(
        _job,
        trigger=DateTrigger(run_date=_dt.now() + timedelta(seconds=120)),
        id="vipdoc_watch_boot",
        misfire_grace_time=3600,
        replace_existing=True,
    )
    scheduler.add_job(
        _job,
        trigger="interval",
        minutes=30,
        id="vipdoc_watch",
        misfire_grace_time=3600,
        replace_existing=True,
    )

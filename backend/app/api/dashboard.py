"""北向资金分钟累计快照 — 看板右栏小组件数据源。

固定使用 ths_basic(同花顺 hsgtApi) 的北向分钟累计, 不走能力路由
(该数据为市场级情绪指标, 唯一来源; 历史日度上游 2024-08 起停更, 仅当日分钟有效)。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from fastapi import APIRouter
import polars as pl

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _latest_trade_day() -> str:
    """最近的交易日(周末回退到周五); 以本地日K最新分区为权威, 回退到工作日推算。"""
    try:
        from app.config import settings
        kdir = settings.data_dir / "kline_daily"
        dirs = sorted(p.name for p in kdir.glob("date=*"))
        if dirs:
            return dirs[-1].split("=", 1)[1]
    except Exception:  # noqa: BLE001
        pass
    d = date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


@router.get("/northbound")
def northbound() -> dict:
    """当日北向资金分钟累计: 沪股通/深股通净买(亿元) 序列 + 最新汇总。"""
    try:
        from app.data_providers import custom as custom_sources
        provider = custom_sources.get_provider("ths_basic")
        if provider is None:
            return {"ok": False, "rows": [], "error": "ths_basic 插件未启用"}
        df = provider.get_northbound()
        if df.is_empty():
            return {"ok": False, "rows": [], "error": "北向数据暂不可用(非交易时段或上游异常)"}
        rows = [
            {
                "time": r.get("time"),
                "hgt": r.get("hgt_net"),
                "sgt": r.get("sgt_net"),
            }
            for r in df.to_dicts()
        ]
        last = rows[-1]
        total = (last.get("hgt") or 0) + (last.get("sgt") or 0)
        return {
            "ok": True,
            "rows": rows,
            "latest": {"time": last.get("time"), "hgt": last.get("hgt"), "sgt": last.get("sgt"), "total": round(total, 2)},
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("northbound widget failed: %s", e)
        return {"ok": False, "rows": [], "error": str(e)[:120]}


@router.get("/lhb")
def lhb(limit: int = 10) -> dict:
    """最近交易日全市场龙虎榜整榜(净买额降序 top N)。

    走能力路由的龙虎榜源; 源无整榜方法时回退 astock_ext.get_lhb_board。
    """
    try:
        from app.services import preferences
        from app.data_providers import custom as custom_sources

        day = _latest_trade_day()
        provider = custom_sources.get_provider(preferences.get_dragon_tiger_data_provider())
        df = pl.DataFrame()
        if provider is not None and hasattr(provider, "get_lhb_board"):
            df = provider.get_lhb_board(day)
        if (df is None or df.is_empty()) and provider is not None and hasattr(provider, "dragon_tiger"):
            try:
                raw = provider.dragon_tiger("all", day)
                if isinstance(raw, pl.DataFrame) and not raw.is_empty():
                    df = raw
            except Exception:  # noqa: BLE001
                pass
        if df is None or df.is_empty():
            fb = custom_sources.get_provider("astock_ext")
            if fb is not None and hasattr(fb, "get_lhb_board"):
                df = fb.get_lhb_board(day)
        if df is None or df.is_empty():
            return {"ok": False, "rows": [], "date": day, "error": "龙虎榜暂不可用(非交易日或上游异常)"}
        rows_all = df.to_dicts()
        rows_all.sort(key=lambda r: (r.get("net_buy") or 0), reverse=True)
        rows = []
        for r in rows_all[: max(1, min(limit, 30))]:
            sym = str(r.get("symbol") or "")
            if sym and "." not in sym:
                sym = f"{sym.zfill(6)}.{'SH' if sym.startswith(('6', '9', '5')) else 'BJ' if sym.startswith(('4', '8', '92')) else 'SZ'}"
            rows.append({
                "symbol": sym,
                "name": r.get("name"),
                "reason": r.get("reason"),
                "net_buy": r.get("net_buy"),
                "change_pct": r.get("change_pct"),
            })
        return {"ok": True, "date": day, "rows": rows}
    except Exception as e:  # noqa: BLE001
        logger.warning("lhb widget failed: %s", e)
        return {"ok": False, "rows": [], "error": str(e)[:120]}

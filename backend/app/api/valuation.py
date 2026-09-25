# -*- coding: utf-8 -*-
"""估值股本日频历史 API(本地 valuation_local)。"""
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/valuation", tags=["valuation-history"])


@router.get("/history")
def valuation_history(
    symbol: str = Query(..., description="如 600519.SH"),
    days: int = Query(250, ge=1, le=10000),
) -> dict:
    from app.data_providers import custom as custom_sources
    from app.services import preferences

    provider = custom_sources.get_provider(preferences.get_valuation_history_data_provider())
    if provider is None or not hasattr(provider, "get_valuation_history"):
        raise HTTPException(503, "valuation_history 源不可用(需 valuation_local 插件且有导入数据)")
    try:
        df = provider.get_valuation_history(symbol, days=days)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))
    return {
        "provider": preferences.get_valuation_history_data_provider(),
        "symbol": symbol,
        "rows": [] if df.is_empty() else df.to_dicts(),
    }

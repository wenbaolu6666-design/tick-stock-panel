# -*- coding: utf-8 -*-
"""板块分时 API: 板块分钟K/板块清单/周期清单(走能力路由, 默认 board_local)。"""
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/board", tags=["board-minute"])


@router.get("/minute")
def board_minute(
    board_code: str = Query(..., description="通达信板块代码, 如 880201"),
    date: str | None = Query(None, description="YYYY-MM-DD, 缺省最近有数据日期"),
    period: str = Query("m1", description="m1/m5/m15/m30/m60"),
    days: int = Query(1, ge=1, le=60, description="向前取 N 个有数据日期"),
) -> dict:
    from app.data_providers import custom as custom_sources
    from app.services import preferences

    provider = custom_sources.get_provider(preferences.get_board_minute_data_provider())
    if provider is None or not hasattr(provider, "get_board_minute"):
        raise HTTPException(503, "board_minute 源不可用(需 board_local 插件且有导入数据)")
    try:
        df = provider.get_board_minute(board_code, date=date, period=period, days=days)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))
    return {
        "provider": preferences.get_board_minute_data_provider(),
        "board_code": board_code,
        "period": period,
        "rows": [] if df.is_empty() else df.to_dicts(),
    }


@router.get("/boards")
def board_list(period: str = Query("m1")) -> dict:
    """板块清单(code+name), 默认取 1 分钟最新分区。"""
    from app.data_providers import custom as custom_sources
    from app.services import preferences

    provider = custom_sources.get_provider(preferences.get_board_minute_data_provider())
    if provider is None or not hasattr(provider, "get_board_minute"):
        raise HTTPException(503, "board_minute 源不可用")
    try:
        rows = provider.get_board_minute.__self__ and None  # noqa: F841
    except Exception:
        pass
    from app.plugins.board_local.provider import list_boards

    boards = list_boards(period)
    return {"period": period, "count": len(boards), "boards": boards[:2000]}


@router.get("/periods")
def board_periods() -> dict:
    from app.data_providers import custom as custom_sources
    from app.services import preferences

    provider = custom_sources.get_provider(preferences.get_board_minute_data_provider())
    periods = provider.list_periods() if provider is not None and hasattr(provider, "list_periods") else []
    return {"periods": periods}

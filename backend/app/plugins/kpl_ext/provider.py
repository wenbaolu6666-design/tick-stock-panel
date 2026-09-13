"""开盘啦 (KaiPanLa) built-in plugin via公开 HTTP API.

端点: POST https://apphis.longhuvip.com/w1/api/index.php (form-urlencoded, a=接口名 c=分类)
实测: RealRankingInfo (板块强度排行) 无需 Token 即可访问; 需要 Token 的接口使用
抓包捕获的公共 Token (失效时对应接口返回空, 不影响 sector 主数据集)。
参考: github.com/Rainynitesky/kaipanla-data-parser
"""
from __future__ import annotations

import logging
import os

import httpx
import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("sector",)

_API_URL = "https://apphis.longhuvip.com/w1/api/index.php"
_DEVICE_ID = os.getenv("KPL_DEVICE_ID", "80ca7d1b-2a24-3cd0-a915-99b61f6f88aa")
_VERSION = os.getenv("KPL_VERSION", "5.23.0.4")
_API_VERSION = os.getenv("KPL_APIV", "w44")
_PHONE_OS = os.getenv("KPL_PHONE_OS", "1")
_USER_ID = os.getenv("KPL_USER_ID", "7079210")
_TOKEN = os.getenv("KPL_TOKEN", "8a553e54a7fba11665f33d745f30e204")

_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "User-Agent": "KPL/5.23.0.4 (com.aiyu.kaipanla; build:1; iOS 16.6.0) Alamofire/4.9.1",
}

_BASE_PARAMS = {
    "PhoneOSNew": _PHONE_OS,
    "DeviceID": _DEVICE_ID,
    "VerSion": _VERSION,
    "apiv": _API_VERSION,
}


def availability() -> tuple[bool, str]:
    """探测板块强度接口是否可达。"""
    try:
        df = _fetch_sector_ranking()
        if not df.is_empty():
            return True, "ok (apphis.longhuvip.com RealRankingInfo)"
        return False, "RealRankingInfo 返回空"
    except Exception as e:  # noqa: BLE001
        return False, f"开盘啦接口不可达: {e}"


def _post(params: dict) -> str:
    body = "&".join(f"{k}={v}" for k, v in params.items()) + "&"
    r = httpx.post(_API_URL, content=body, headers=_HEADERS, timeout=15)
    r.raise_for_status()
    return r.text


def _fetch_sector_ranking(date: str | None = None) -> pl.DataFrame:
    """板块强度排行 (概念+行业), 免 Token。列: PlateID/名称/强度/成交额/涨跌%/主力净额。"""
    import json as _json

    from datetime import date as _date, timedelta as _td
    if not date:
        # 缺省取最近交易日 (周末回退周五); 当日盘中传当日也有效
        today = _date.today()
        date = today.isoformat()
        if today.weekday() == 0:
            date = (today - _td(days=3)).isoformat()
        elif today.weekday() == 6:
            date = (today - _td(days=2)).isoformat()
    params = {
        "Order": "1", "a": "RealRankingInfo", "st": "30", "c": "ZhiShuRanking",
        **_BASE_PARAMS, "Index": "0",
        "Date": date, "Type": "1", "ZSType": "7",
    }
    txt = _post(params)
    data = _json.loads(txt)
    items = data.get("list") or []
    rows = []
    for it in items:
        if not isinstance(it, list) or len(it) < 10:
            continue
        # 行结构 (对照 09-11 通信板): [PlateID, 名称, 强度值, 涨跌幅%, ?, 市值, ?, ?, 主力净额, 量比?, ...]
        # 语义仅前4项经人工核验; 其余保留原始值待抓包对表后细化
        rows.append({
            "PlateID": str(it[0]),
            "板块名称": str(it[1]),
            "强度值": _f(it[2]),
            "涨跌幅%": _f(it[3]),
            "原始行": _json.dumps(it, ensure_ascii=False)[:500],
        })
    return pl.DataFrame(rows)


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class KaiPanLaProvider:
    name = "kpl_ext"
    display_name = "开盘啦（情绪/板块强度）"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=20)

    def close(self) -> None:
        pass

    def get_sector(self, symbols: list[str] | None = None, date: str | None = None) -> list[dict]:
        """板块强度排行 → TSP sector 契约行 (TDX 板块结构兼容)。"""
        df = _fetch_sector_ranking(date)
        out = []
        for r in df.to_dicts():
            out.append({
                "board_type": "kpl_concept",
                "board_code": r["PlateID"],
                "board_name": r["板块名称"],
                "change_pct": (r.get("涨跌幅%") or 0) / 100.0,
                "main_net": r.get("主力净额"),
                "amount": r.get("成交额"),
                "strength": r.get("强度"),
            })
        return out

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        if dataset == "sector":
            df = _fetch_sector_ranking()
            return {"provider": self.name, "dataset": dataset, "rows": df.height,
                    "columns": df.columns, "preview": df.head(3).to_dicts()}
        raise ValueError(f"kpl_ext 不支持数据集: {dataset}")

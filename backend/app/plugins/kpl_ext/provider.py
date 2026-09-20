"""开盘啦 (KaiPanLa) built-in plugin via公开 HTTP API.

端点: POST https://apphis.longhuvip.com/w1/api/index.php (form-urlencoded, a=接口名 c=分类)
实测 (2026-09-20):
- RealRankingInfo 板块强度排行 (免 Token, sector 主数据集)
- SonPlate_Info 子板块谱系 / ZhiShuStockList_W8 板块成分股 / GetVolTurIncremental 板块分时量额
- BKFenShiZhiBo 板块异动原因 (anomaly 数据集, 含 Boolean is_important 与题材归因)
- GetPanKou 板块盘口 / GetDayBaseFaceListZDEvnArt 板块日基面(异动新闻)
需要 Token 的接口使用抓包捕获的公共 Token (失效时返回空, 不影响 sector 主数据集)。
参考: github.com/Rainynitesky/kaipanla-data-parser
"""
from __future__ import annotations

import json as _json
import logging
import os

import httpx
import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("sector", "anomaly", "news")

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
    if not date:
        date = _last_trading_date()
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


def _last_trading_date() -> str:
    """最近交易日(周末回退周五); 当日盘中传当日也有效。"""
    from datetime import date as _date, timedelta as _td

    today = _date.today()
    if today.weekday() == 0:
        return (today - _td(days=3)).isoformat()
    if today.weekday() == 6:
        return (today - _td(days=2)).isoformat()
    return today.isoformat()


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

    # ---- sector 扩展: 子板块谱系 / 成分股 / 板块分时量额 ----
    def get_board_sons(self, plate_id: str, date: str | None = None) -> pl.DataFrame:
        """子板块谱系 (SonPlate_Info): [PlateID, 名称, 强度值]。"""
        params = {
            "a": "SonPlate_Info", "c": "ZhiShuRanking", "IsShow": "1",
            "Date": date or _last_trading_date(), "PlateID": plate_id, **_BASE_PARAMS,
        }
        data = _json.loads(_post(params))
        rows = []
        for it in data.get("List") or []:
            if not isinstance(it, list) or len(it) < 3:
                continue
            rows.append({
                "plate_id": str(it[0]),
                "board_name": str(it[1]),
                "strength": _f(it[2]),
            })
        return pl.DataFrame(rows)

    def get_board_stocks(self, plate_id: str, date: str | None = None) -> pl.DataFrame:
        """板块成分股 (ZhiShuStockList_W8): 首 3 项语义经人工核验, 其余保留 raw 待对表。"""
        params = {
            "Order": "1", "TSZB": "0", "a": "ZhiShuStockList_W8", "st": "30", "c": "ZhiShuRanking",
            "old": "1", "IsZZ": "0", "Token": _TOKEN, "Index": "0",
            "Date": date or _last_trading_date(), "Type": "6", "IsKZZType": "0",
            "UserID": _USER_ID, "PlateID": plate_id, "TSZB_Type": "0", "filterType": "0",
            **_BASE_PARAMS,
        }
        data = _json.loads(_post(params))
        rows = []
        for it in data.get("list") or []:
            if not isinstance(it, list) or len(it) < 8:
                continue
            rows.append({
                "stock_id": str(it[0]),
                "stock_name": str(it[1]),
                "price": _f(it[5]),
                "change_pct": _f(it[6]),
                "amount": _f(it[7]),
                "raw": _json.dumps(it, ensure_ascii=False)[:400],
            })
        return pl.DataFrame(rows)

    def get_board_timeline(self, plate_id: str, date: str | None = None) -> pl.DataFrame:
        """板块分时量额 (GetVolTurIncremental): [HH:MM, 量, 额, ?]。"""
        params = {
            "a": "GetVolTurIncremental", "c": "ZhiShuL2Data",
            "StockID": plate_id, "Day": date or _last_trading_date(), **_BASE_PARAMS,
        }
        data = _json.loads(_post(params))
        rows = []
        for it in data.get("volumeturnover") or []:
            if not isinstance(it, list) or len(it) < 3:
                continue
            rows.append({"time": str(it[0]), "volume": _f(it[1]), "amount": _f(it[2])})
        return pl.DataFrame(rows)

    # ---- anomaly 数据集: 板块异动原因 (BKFenShiZhiBo) ----
    def get_anomaly(self, symbols: list[str] | None = None) -> pl.DataFrame:
        """板块异动原因 (开盘啦独家, fuyao 无此角度)。

        symbols 解释为 **PlateID 列表**; 空则聚合当日板块强度榜前 10 个板块。
        返回行: plate_id/symbol/stock_name/reason/is_important/time(IsImportant=1 为核心异动)。
        """
        date = _last_trading_date()
        plate_ids = symbols[:10] if symbols else [
            str(r["PlateID"]) for r in _fetch_sector_ranking(date).to_dicts()[:10]
        ]
        out = []
        for pid in plate_ids:
            try:
                params = {
                    "a": "BKFenShiZhiBo", "c": "HisConceptionPoint",
                    "Date": date, "PlateID": pid, "Token": _TOKEN, "UserID": _USER_ID,
                    **_BASE_PARAMS,
                }
                data = _json.loads(_post(params))
                for it in data.get("list") or []:
                    if not isinstance(it, dict):
                        continue
                    out.append({
                        "plate_id": str(pid),
                        "symbol": str(it.get("StockID")),
                        "stock_name": it.get("StockName"),
                        "reason": it.get("Reason"),
                        "is_important": str(it.get("IsImportant")) == "1",
                        "time": it.get("Time"),
                    })
            except Exception as e:  # noqa: BLE001
                logger.warning("kpl_ext BKFenShiZhiBo %s failed: %s", pid, e)
        return pl.DataFrame(out)

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

    # ---- news 数据集: 板块日基面新闻/异动归因 (GetDayBaseFaceListZDEvnArt) ----
    def get_news(self, limit: int = 50) -> pl.DataFrame:
        """板块日基面新闻与归因 (news): 聚合当日强度榜前 10 板块, 按 ID 去重。

        行: title/content/publish_time/importance/source/plate_id/zt_num/lz_info
        (zt_num 板块涨停家数, lz_info 连板梯队明细)。
        """
        date = _last_trading_date()
        plate_ids = [
            str(r["PlateID"]) for r in _fetch_sector_ranking(date).to_dicts()[:10]
        ]
        seen: set = set()
        out: list[dict] = []
        for pid in plate_ids:
            if len(out) >= limit:
                break
            try:
                params = {
                    "a": "GetDayBaseFaceListZDEvnArt", "c": "ZhiShuKLine",
                    "Token": _TOKEN, "StockID": pid, "UserID": _USER_ID,
                    "Index": "0", "st": "10", "Type": "0", "IsBoom": "0",
                    **_BASE_PARAMS,
                }
                items = _json.loads(_post(params)).get("List") or []
            except Exception as e:  # noqa: BLE001
                logger.warning("kpl_ext GetDayBaseFaceListZDEvnArt %s failed: %s", pid, e)
                continue
            for it in items:
                if not isinstance(it, dict) or it.get("ID") in seen:
                    continue
                seen.add(it.get("ID"))
                out.append({
                    "title": str(it.get("Title") or "")[:120],
                    "content": it.get("BoomReason") or "",
                    "publish_time": it.get("Date"),
                    "importance": bool(it.get("IsBoom")),
                    "source": "开盘啦",
                    "plate_id": str(pid),
                    "zt_num": it.get("ZTNum"),
                    "lz_info": it.get("LZInfo"),
                })
                if len(out) >= limit:
                    break
        return pl.DataFrame(out)

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        if dataset == "sector":
            df = _fetch_sector_ranking()
            return {"provider": self.name, "dataset": dataset, "rows": df.height,
                    "columns": df.columns, "preview": df.head(3).to_dicts()}
        if dataset == "anomaly":
            prov = KaiPanLaProvider()
            try:
                df = prov.get_anomaly()
            finally:
                prov.close()
            return {"provider": self.name, "dataset": dataset, "rows": df.height,
                    "columns": df.columns, "preview": df.head(3).to_dicts()}
        if dataset == "news":
            prov = KaiPanLaProvider()
            try:
                df = prov.get_news()
            finally:
                prov.close()
            return {"provider": self.name, "dataset": dataset, "rows": df.height,
                    "columns": df.columns, "preview": df.head(3).to_dicts()}
        raise ValueError(f"kpl_ext 不支持数据集: {dataset}")

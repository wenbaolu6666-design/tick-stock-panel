"""TdxQuant extended data plugin: dragon_tiger / limit_updown / market_overview / consensus / sector.

Uses the same TQ HTTP gateway (127.0.0.1:17709) as tdx_quant but exposes
GP (龙虎榜/大宗/融资融券), ZDT (涨跌停), SC (市场总览), GO (一致预期/业绩预告/解禁),
BK (板块) data series.
"""
from __future__ import annotations

import logging
from datetime import datetime

import httpx
import polars as pl
from app.plugins.tdx_quant.provider import availability as _tq_availability

logger = logging.getLogger(__name__)

_DATASETS = ("dragon_tiger", "limit_updown", "market_overview", "consensus", "sector", "survey", "pledge", "score", "margin", "block_trade", "corp_events")
_URL = "http://127.0.0.1:17709/"

# GP fields for dragon_tiger (龙虎榜/大宗交易/融资融券)
_GP_DRAGON_TIGER = [
    "GP1", "GP2", "GP3", "GP4", "GP5", "GP6", "GP7", "GP8",
    "GP9", "GP10", "GP11", "GP12", "GP13", "GP14", "GP15",
]

# SC fields for market_overview
_SC_MARKET = [
    "SC1", "SC2", "SC3", "SC4", "SC5", "SC6", "SC7", "SC8",
    "SC9", "SC10", "SC11", "SC12", "SC13", "SC14", "SC15",
    "SC31", "SC33", "SC34", "SC35", "SC36", "SC37", "SC38",
    "SC39", "SC40", "SC41", "SC42",
]

# GO fields for consensus (一致预期/业绩预告/解禁)
_GO_CONSENSUS = [
    "GO1", "GO2", "GO3", "GO4", "GO5", "GO6", "GO7", "GO8",
    "GO9", "GO10", "GO11", "GO12", "GO13", "GO14", "GO15",
    "GO16", "GO17", "GO18", "GO19", "GO20", "GO21", "GO22",
    "GO23", "GO24", "GO25",
    "GO26", "GO27",  # 解禁
    "GO28", "GO29", "GO30", "GO31", "GO32", "GO33", "GO34",
    "GO35", "GO36", "GO37", "GO38", "GO39",  # 业绩预告
    "GO40", "GO41",  # 业绩快报
    "GO42", "GO43",  # 分红募资
    "GO44", "GO45", "GO46", "GO47",
]


def _f(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def availability() -> tuple[bool, str]:
    # 委托 tdx_quant：共享 TQ 探活 + 自动拉起 TdxW 逻辑
    return _tq_availability()

def _call(method: str, params: dict, timeout: float = 30.0) -> dict:
    payload = {"id": 1, "method": method, "params": params}
    r = httpx.post(_URL, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    result = data.get("result") or {}
    if str(result.get("ErrorId", "0")) != "0":
        raise RuntimeError(f"TdxQuant {method} error: {result.get('ErrorId')}")
    return result


class TdxExtProvider:
    name = "tdx_ext"
    builtin = True

    def __init__(self) -> None:
        self.datasets = dict.fromkeys(_DATASETS)

    def close(self) -> None:
        pass

    # ==================== dragon_tiger ====================

    def get_dragon_tiger(
        self,
        symbols: list[str],
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> pl.DataFrame:
        if not symbols:
            return pl.DataFrame()
        params = {
            "stock_list": symbols,
            "field_list": _GP_DRAGON_TIGER,
            "start_time": (start_time or datetime(2020, 1, 1)).strftime("%Y%m%d"),
            "end_time": (end_time or datetime.now()).strftime("%Y%m%d"),
        }
        try:
            result = _call("get_gpjy_value", params, timeout=60)
            value = result.get("Value") or {}
            rows = []
            for symbol, records in value.items():
                if not isinstance(records, list):
                    continue
                for rec in records:
                    if not isinstance(rec, dict):
                        continue
                    row = {"symbol": symbol}
                    for k, v in rec.items():
                        if k in ("date", "Date", "time", "Time"):
                            row["date"] = str(v)
                        elif k.startswith("GP"):
                            row[k.lower()] = _f(v)
                    rows.append(row)
            if not rows:
                return pl.DataFrame()
            df = pl.DataFrame(rows)
            if "date" in df.columns:
                df = df.with_columns(pl.col("date").str.to_date("%Y%m%d", strict=False))
            return df
        except Exception as e:
            logger.warning("tdx_ext dragon_tiger failed: %s", e)
            return pl.DataFrame()

    # ==================== limit_updown ====================

    def get_limit_updown(self, date: str | None = None) -> pl.DataFrame:
        """date: YYYYMMDD, default today."""
        d = date or datetime.now().strftime("%Y%m%d")
        rows = []
        for zdt_type in ("up", "down"):
            try:
                result = _call("get_zdt_data", {"date": d, "type": zdt_type}, timeout=15)
                value = result.get("Value") or []
                if isinstance(value, dict):
                    value = value.get("Value") or value.get("List") or []
                for rec in value:
                    if not isinstance(rec, dict):
                        continue
                    code = rec.get("Code") or rec.get("code") or ""
                    rows.append({
                        "date": d,
                        "symbol": code,
                        "type": zdt_type,
                        "price": _f(rec.get("Price") or rec.get("price")),
                        "volume": _f(rec.get("Volume") or rec.get("volume")),
                        "amount": _f(rec.get("Amount") or rec.get("amount")),
                        "turnover_rate": _f(rec.get("TurnoverRate") or rec.get("turnoverRate")),
                        "days": int(_f(rec.get("Days") or rec.get("days")) or 1),
                        "seal_amount": _f(rec.get("SealAmount") or rec.get("sealAmount")),
                    })
            except Exception as e:
                logger.warning("tdx_ext limit_updown %s failed: %s", zdt_type, e)
        if not rows:
            return pl.DataFrame()
        df = pl.DataFrame(rows)
        if "date" in df.columns:
            df = df.with_columns(pl.col("date").str.to_date("%Y%m%d", strict=False))
        return df

    # ==================== market_overview ====================

    def get_market_overview(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> pl.DataFrame:
        params = {
            "field_list": _SC_MARKET,
            "start_time": (start_time or datetime(2020, 1, 1)).strftime("%Y%m%d"),
            "end_time": (end_time or datetime.now()).strftime("%Y%m%d"),
        }
        try:
            result = _call("get_scjy_value", params, timeout=60)
            value = result.get("Value") or {}
            rows = []
            records = value if isinstance(value, list) else value.get("Value") or []
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                row = {}
                for k, v in rec.items():
                    if k in ("date", "Date", "time", "Time"):
                        row["date"] = str(v)
                    elif k.startswith("SC"):
                        row[k.lower()] = _f(v)
                rows.append(row)
            if not rows:
                return pl.DataFrame()
            df = pl.DataFrame(rows)
            if "date" in df.columns:
                df = df.with_columns(pl.col("date").str.to_date("%Y%m%d", strict=False))
            return df
        except Exception as e:
            logger.warning("tdx_ext market_overview failed: %s", e)
            return pl.DataFrame()

    # ==================== consensus ====================

    def get_consensus(self, symbols: list[str]) -> pl.DataFrame:
        if not symbols:
            return pl.DataFrame()
        try:
            result = _call("get_gp_one_data", {
                "stock_list": symbols,
                "field_list": _GO_CONSENSUS,
            }, timeout=30)
            value = result.get("Value") or {}
            rows = []
            for symbol in symbols:
                rec = value.get(symbol) or {}
                if not isinstance(rec, dict):
                    continue
                row = {"symbol": symbol}
                for k, v in rec.items():
                    if k.startswith("GO"):
                        row[k.lower()] = _f(v)
                rows.append(row)
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:
            logger.warning("tdx_ext consensus failed: %s", e)
            return pl.DataFrame()

    # ==================== sector ====================

    def get_sector_list(self) -> pl.DataFrame:
        try:
            result = _call("get_sector_list", {"list_type": 1}, timeout=15)
            val = result.get("Value") or []
            rows = []
            for item in val:
                if isinstance(item, dict):
                    rows.append({
                        "sector_code": item.get("Code") or "",
                        "sector_name": item.get("Name") or "",
                    })
                elif isinstance(item, str):
                    rows.append({"sector_code": item, "sector_name": ""})
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:
            logger.warning("tdx_ext sector_list failed: %s", e)
            return pl.DataFrame()

    def get_sector_stocks(self, sector_code: str) -> list[str]:
        try:
            result = _call("get_stock_list_in_sector", {
                "block_code": sector_code, "list_type": 0,
            }, timeout=15)
            val = result.get("Value") or []
            return [str(x) for x in val if x]
        except Exception as e:
            logger.warning("tdx_ext sector_stocks %s failed: %s", sector_code, e)
            return []

    def get_sector_data(
        self,
        sector_codes: list[str] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> pl.DataFrame:
        codes = sector_codes or []
        if not codes:
            sl = self.get_sector_list()
            codes = sl["sector_code"].to_list() if not sl.is_empty() else []
        if not codes:
            return pl.DataFrame()
        params = {
            "sector_list": codes[:50],
            "field_list": ["BK1", "BK2", "BK3", "BK4", "BK5", "BK6", "BK7", "BK8"],
            "start_time": (start_time or datetime(2020, 1, 1)).strftime("%Y%m%d"),
            "end_time": (end_time or datetime.now()).strftime("%Y%m%d"),
        }
        try:
            result = _call("get_bkjy_value", params, timeout=60)
            value = result.get("Value") or {}
            rows = []
            for sector, records in value.items():
                if not isinstance(records, list):
                    continue
                for rec in records:
                    if not isinstance(rec, dict):
                        continue
                    row = {"sector_code": sector}
                    for k, v in rec.items():
                        if k in ("date", "Date", "time", "Time"):
                            row["date"] = str(v)
                        elif k.startswith("BK"):
                            row[k.lower()] = _f(v)
                    rows.append(row)
            if not rows:
                return pl.DataFrame()
            df = pl.DataFrame(rows)
            if "date" in df.columns:
                df = df.with_columns(pl.col("date").str.to_date("%Y%m%d", strict=False))
            return df
        except Exception as e:
            logger.warning("tdx_ext sector_data failed: %s", e)
            return pl.DataFrame()

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        symbols = symbols or ["600519.SH"]
        if dataset == "dragon_tiger":
            df = self.get_dragon_tiger(symbols)
        elif dataset == "limit_updown":
            df = self.get_limit_updown()
        elif dataset == "market_overview":
            df = self.get_market_overview()
        elif dataset == "consensus":
            df = self.get_consensus(symbols)
        elif dataset == "sector":
            df = self.get_sector_list()
        else:
            raise ValueError(f"tdx_ext 不支持数据集: {dataset}")
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns,
            "preview": df.head(5).to_dicts() if not df.is_empty() else [],
        }

    # ==================== survey / pledge / score (GP 系列, 按上游扩容) ====================

    def _gp_snapshot(self, symbols: list[str], fields: list[str]) -> pl.DataFrame:
        """GP 单日快照通用查询: 字段小写透传 + symbol 列。"""
        if not symbols:
            return pl.DataFrame()
        params = {
            "stock_list": symbols,
            "field_list": fields,
            "start_time": datetime.now().strftime("%Y%m%d"),
            "end_time": datetime.now().strftime("%Y%m%d"),
        }
        result = _call("get_gpjy_value", params, timeout=30)
        value = result.get("Value") or {}
        rows: list[dict] = []
        for symbol, records in value.items():
            if not isinstance(records, list):
                records = [records]
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                row = {"symbol": symbol, "date": datetime.now().strftime("%Y-%m-%d")}
                for k, v in rec.items():
                    if k in ("date", "Date", "Time", "time"):
                        continue
                    row[str(k).lower()] = _f(v) if isinstance(v, str) and v.replace(".", "", 1).replace("-", "", 1).isdigit() else v
                rows.append(row)
        return pl.DataFrame(rows) if rows else pl.DataFrame()

    def get_survey(self, symbols: list[str]) -> pl.DataFrame:
        """机构调研（GP10）：近 3 月调研次数/接待机构家数。"""
        try:
            return self._gp_snapshot(symbols, ["GP10"])
        except Exception as e:  # noqa: BLE001
            logger.warning("tdx_ext survey %s failed: %s", symbols, e)
            return pl.DataFrame()

    def get_pledge(self, symbols: list[str]) -> pl.DataFrame:
        """股权质押（GP19 质押数量 / GP20 质押比例）。"""
        try:
            return self._gp_snapshot(symbols, ["GP19", "GP20"])
        except Exception as e:  # noqa: BLE001
            logger.warning("tdx_ext pledge %s failed: %s", symbols, e)
            return pl.DataFrame()

    def get_score(self, symbols: list[str]) -> pl.DataFrame:
        """综合评分（GP44 综合评分 / GP45 评级系数）。"""
        try:
            return self._gp_snapshot(symbols, ["GP44", "GP45"])
        except Exception as e:  # noqa: BLE001
            logger.warning("tdx_ext score %s failed: %s", symbols, e)
            return pl.DataFrame()

    # ==================== margin / block_trade / corp_events (GP/GO 系列, 按上游扩容) ====================

    def get_margin(self, symbols: list[str]) -> pl.DataFrame:
        """融资融券（GP3 余额/余量、GP11 融资买入/偿还、GP12 融券卖出/偿还、GP13 净买/净卖）。"""
        try:
            return self._gp_snapshot(symbols, ["GP3", "GP11", "GP12", "GP13"])
        except Exception as e:  # noqa: BLE001
            logger.warning("tdx_ext margin %s failed: %s", symbols, e)
            return pl.DataFrame()

    def get_block_trade(self, symbols: list[str]) -> pl.DataFrame:
        """大宗交易（GP4：成交价/量/额）。"""
        try:
            return self._gp_snapshot(symbols, ["GP4"])
        except Exception as e:  # noqa: BLE001
            logger.warning("tdx_ext block_trade %s failed: %s", symbols, e)
            return pl.DataFrame()

    def get_corp_events(self, symbols: list[str]) -> pl.DataFrame:
        """公司事件（TQ 口径）：GO26/27 解禁日与数量 + GP1 股东户数 + GP30 分红送转。"""
        if not symbols:
            return pl.DataFrame()
        rows: list[dict] = []
        try:
            result = _call("get_gp_one_data", {
                "stock_list": symbols,
                "field_list": ["GO26", "GO27"],
            }, timeout=30)
            value = result.get("Value") or {}
            for symbol in symbols:
                rec = value.get(symbol) or {}
                if isinstance(rec, dict) and rec:
                    row: dict = {"symbol": symbol, "kind": "lift"}
                    for k, v in rec.items():
                        if k.startswith("GO"):
                            row[k.lower()] = _f(v)
                    rows.append(row)
        except Exception as e:  # noqa: BLE001
            logger.warning("tdx_ext corp_events GO failed: %s", e)
        try:
            gp = self._gp_snapshot(symbols, ["GP1", "GP30"])
            for r in gp.to_dicts():
                r["kind"] = "holders_dividend"
                rows.append(r)
        except Exception as e:  # noqa: BLE001
            logger.warning("tdx_ext corp_events GP failed: %s", e)
        return pl.DataFrame(rows) if rows else pl.DataFrame()

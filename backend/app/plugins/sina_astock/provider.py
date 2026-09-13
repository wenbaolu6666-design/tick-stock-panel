"""Sina A-share built-in plugin: qfq adjustment factors + financial statements.

Converts Sina's cumulative qfq factor series into the TSP single-event
pre/post ratio (`ex_factor`) used by the enriched pipeline, and maps Sina's
中文三大报表 to TSP canonical financial columns.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

import httpx
import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("adj_factor", "financial")
_BASE = "https://finance.sina.com.cn/realstock/company/{prefix}{code}/qfq.js"
_FIN_BASE = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://finance.sina.com.cn/",
}
_SENTINEL = "1900-01-01"

_FIN_SOURCES = {"income": "lrb", "balance_sheet": "fzb", "cash_flow": "llb"}

_INCOME_MAP = {
    "营业收入": "revenue",
    "营业成本": "operating_cost",
    "销售费用": "selling_expense",
    "管理费用": "admin_expense",
    "研发费用": "rd_expense",
    "营业利润": "operating_profit",
    "财务费用": "financial_expense",
    "利润总额": "total_profit",
    "所得税费用": "income_tax",
    "净利润": "net_income",
    "归属于母公司所有者的净利润": "net_income_attributable",
    "基本每股收益": "basic_eps",
}
_BALANCE_MAP = {
    "流动资产合计": "total_current_assets",
    "非流动资产合计": "total_non_current_assets",
    "资产总计": "total_assets",
    "货币资金": "cash_and_equivalents",
    "应收账款": "accounts_receivable",
    "负债合计": "total_liabilities",
    "所有者权益合计": "total_equity",
    "归属于母公司所有者权益合计": "total_equity",
}
_CASHFLOW_MAP = {
    "经营活动产生的现金流量净额": "net_operating_cash_flow",
    "投资活动产生的现金流量净额": "net_investing_cash_flow",
    "筹资活动产生的现金流量净额": "net_financing_cash_flow",
    "购建固定资产、无形资产和其他长期资产所支付的现金": "capex",
    "现金及现金等价物净增加额": "net_cash_change",
}


def _prefix(symbol: str) -> str | None:
    s = symbol.upper()
    for sfx, pre in ((".SH", "sh"), (".SZ", "sz"), (".BJ", "bj")):
        if s.endswith(sfx):
            return pre
    code = "".join(ch for ch in s if ch.isdigit())
    if code.startswith(("60", "68", "90", "5")):
        return "sh"
    if code.startswith(("00", "30", "20", "1", "39")):
        return "sz"
    if code.startswith(("4", "8", "92")):
        return "bj"
    return "sz"


def _code(symbol: str) -> str:
    s = symbol.upper()
    for sfx in (".SH", ".SZ", ".BJ"):
        if s.endswith(sfx):
            s = s[: -len(sfx)]
            break
    return "".join(ch for ch in s if ch.isdigit()).zfill(6)


def _fetch_qfq(symbol: str, client: httpx.Client) -> list[dict]:
    pre = _prefix(symbol)
    code = _code(symbol)
    if pre is None or pre == "bj":
        return []
    url = _BASE.format(prefix=pre, code=code)
    r = client.get(url, headers=_HEADERS, timeout=15)
    r.raise_for_status()
    text = r.text
    brace = text.find("{")
    if brace < 0:
        raise RuntimeError(f"新浪复权因子响应无 JSON: {symbol}")
    payload, _ = json.JSONDecoder().raw_decode(text[brace:])
    return payload.get("data") or []


def _cumulative_to_events(symbol: str, rows: list[dict]) -> list[dict]:
    factors: list[tuple[str, float]] = []
    for it in rows:
        d = str(it.get("d") or "")
        try:
            f = float(it.get("f"))
        except (TypeError, ValueError):
            continue
        if d == _SENTINEL or d == "1900-01-01" or not d:
            continue
        factors.append((d, f))
    factors.sort(key=lambda x: x[0])
    events: list[dict] = []
    for i in range(1, len(factors)):
        prev_d, prev_f = factors[i - 1]
        cur_d, cur_f = factors[i]
        if cur_f <= 0 or prev_f <= 0:
            continue
        ratio = prev_f / cur_f
        if ratio <= 1 + 1e-9:
            continue
        events.append({
            "symbol": symbol,
            "trade_date": cur_d,
            "ex_factor": round(ratio, 6),
        })
    return events


def _fetch_financial(
    symbol: str, source: str, num: int, client: httpx.Client
) -> list[dict]:
    """Return [{period_end, announce_date, <item_title>: value}, ...]."""
    pre = _prefix(symbol)
    code = _code(symbol)
    if pre is None:
        return []
    r = client.get(
        _FIN_BASE,
        params={
            "paperCode": f"{pre}{code}",
            "source": _FIN_SOURCES[source],
            "type": "0",
            "page": "1",
            "num": str(num),
        },
        headers=_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    report_list = ((r.json() or {}).get("result") or {}).get("data") or {}
    report_list = report_list.get("report_list") or {}
    out = []
    for period, obj in report_list.items():
        period_iso = f"{period[:4]}-{period[4:6]}-{period[6:8]}"
        pub = str(obj.get("publish_date") or "") or None
        announce = (
            f"{pub[:4]}-{pub[4:6]}-{pub[6:8]}" if pub and len(pub) >= 8 else None
        )
        rec: dict = {"symbol": symbol, "period_end": period_iso, "announce_date": announce}
        for it in obj.get("data") or []:
            title = it.get("item_title")
            if title and it.get("item_value") is not None:
                rec[title] = it.get("item_value")
        out.append(rec)
    return out




def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso(s) -> str | None:
    if s is None:
        return None
    return str(s)


def availability() -> tuple[bool, str]:
    return True, "ok"


@dataclass
class _SinaConfig:
    name: str = "sina_astock"
    display_name: str = "新浪复权因子+财报"
    datasets: dict = field(default_factory=lambda: dict.fromkeys(_DATASETS))
    path: None = None
    builtin: bool = True


class SinaAstockProvider:
    name = "sina_astock"
    builtin = True

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.config = _SinaConfig()
        self._client = client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client()
        return self._client

    def get_adj_factors(
        self,
        symbols: list[str],
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        asset_type: str = "stock",  # noqa: ARG002
        on_chunk_done=None,
    ) -> pl.DataFrame:
        if not symbols:
            return pl.DataFrame()
        client = self._get_client()
        rows: list[dict] = []
        for i, symbol in enumerate(symbols):
            try:
                qfq = _fetch_qfq(symbol, client)
                events = _cumulative_to_events(symbol, qfq)
                if start_time:
                    events = [e for e in events if e["trade_date"] >= start_time.date().isoformat()]
                if end_time:
                    events = [e for e in events if e["trade_date"] <= end_time.date().isoformat()]
                rows.extend(events)
            except Exception as e:  # noqa: BLE001
                logger.warning("sina adj_factor %s failed: %s", symbol, e)
            if on_chunk_done:
                on_chunk_done(i + 1, len(symbols))
        if not rows:
            return pl.DataFrame()
        df = pl.DataFrame(rows).with_columns(
            pl.col("trade_date").str.to_date("%Y-%m-%d", strict=False),
            pl.col("ex_factor").cast(pl.Float64, strict=False),
        )
        return df.select(["symbol", "trade_date", "ex_factor"]).drop_nulls()

    def get_financials(
        self,
        table: str,
        symbols: list[str],
        latest_only: bool = True,
    ) -> pl.DataFrame:
        if not symbols or table not in _FIN_SOURCES:
            return pl.DataFrame()
        client = self._get_client()
        field_map = {
            "income": _INCOME_MAP,
            "balance_sheet": _BALANCE_MAP,
            "cash_flow": _CASHFLOW_MAP,
        }[table]
        out: list[dict] = []
        for symbol in symbols:
            try:
                reports = _fetch_financial(symbol, table, 1 if latest_only else 8, client)
                for rec in reports:
                    row: dict = {
                        "symbol": symbol,
                        "period_end": _iso(rec.get("period_end")),
                        "announce_date": _iso(rec.get("announce_date")),
                    }
                    for title, dst in field_map.items():
                        if title in rec:
                            row[dst] = _to_float(rec.get(title))
                    out.append(row)
            except Exception as e:  # noqa: BLE001
                logger.warning("sina financial %s/%s failed: %s", table, symbol, e)
        return pl.DataFrame(out) if out else pl.DataFrame()

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        symbols = symbols or ["600519.SH"]
        if dataset == "adj_factor":
            df = self.get_adj_factors(symbols, None, None)
        elif dataset == "financial":
            df = self.get_financials("income", symbols, latest_only=True)
        else:
            raise ValueError(f"sina_astock 不支持数据集: {dataset}")
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns,
            "preview": df.head(5).to_dicts() if not df.is_empty() else [],
        }

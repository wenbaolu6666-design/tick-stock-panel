"""THS (同花顺) web-surface data plugin — 按上游拆分自 astock_ext。

上游全部为同花顺网页/数据接口 (basic.10jqka.com.cn / data.10jqka.com.cn / data.hexin.cn):
- research: 个股盈利预测 EPS/净利润 (worth.html)
- limit_updown: 涨停揭秘 (涨停原因/板型/封单)
- money_flow: 北向资金分钟累计 (沪深股通 hsgtApi)

All public endpoints, no API key required.
"""
from __future__ import annotations

import io
import json
import logging
import time
from datetime import datetime

import httpx
import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("money_flow", "research", "limit_updown", "daily", "financial", "consensus")
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"


def availability():
    try:
        r = httpx.get(
            "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool",
            params={"page": 1, "limit": 1, "field": "10", "filter": "HS,GEM2STAR"},
            headers={"User-Agent": _UA, "Referer": "https://data.10jqka.com.cn/"},
            timeout=10,
        )
        return True, f"ok ({r.status_code})" if r.status_code == 200 else f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, f"同花顺数据不可达: {e}"


class ThsBasicProvider:
    name = "ths_basic"
    builtin = True

    def __init__(self) -> None:
        self.datasets = dict.fromkeys(_DATASETS)
        self._client = httpx.Client(
            headers={"User-Agent": _UA},
            timeout=15,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    # ==================== research (盈利预测) ====================

    def get_ths_eps(self, symbol: str) -> pl.DataFrame:
        """同花顺一致预期 EPS/净利润预测（worth.html 表格）。"""
        import pandas as pd

        code = "".join(ch for ch in symbol if ch.isdigit()).zfill(6)
        try:
            r = self._client.get(
                f"https://basic.10jqka.com.cn/new/{code}/worth.html",
                headers={"User-Agent": _UA, "Referer": "https://basic.10jqka.com.cn/"},
                timeout=15,
            )
            r.raise_for_status()
            tables = pd.read_html(io.StringIO(r.text))
            if tables:
                df = pl.from_pandas(tables[0])
                return df.with_columns(pl.lit(symbol).alias("symbol"))
        except Exception as e:  # noqa: BLE001
            logger.warning("ths_basic ths_eps %s failed: %s", symbol, e)
        return pl.DataFrame()

    # ==================== limit_updown (涨停揭秘) ====================

    def get_ths_limit_secret(self, date: str | None = None) -> pl.DataFrame:
        """同花顺涨停揭秘：涨停原因/板型/封板成功率/炸板次数/封单额。date=YYYYMMDD。"""
        d = date or datetime.now().strftime("%Y%m%d")
        try:
            r = self._client.get(
                "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool",
                params={
                    "page": 1, "limit": 200,
                    "field": "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004",
                    "filter": "HS,GEM2STAR", "order_field": "330324", "order_type": "0", "date": d,
                },
                headers={"User-Agent": _UA, "Referer": "https://data.10jqka.com.cn/"},
                timeout=15,
            )
            rows = []
            for it in ((r.json().get("data") or {}).get("info") or []):
                if not isinstance(it, dict):
                    continue
                ft = it.get("first_limit_up_time")
                rows.append({
                    "symbol": str(it.get("code", "")).zfill(6),
                    "name": it.get("name", ""),
                    "price": it.get("latest"),
                    "change_pct": it.get("change_rate"),
                    "reason": it.get("reason_type", ""),
                    "board_type": it.get("limit_up_type", ""),
                    "seal_rate": it.get("limit_up_suc_rate"),
                    "break_times": it.get("open_num", 0),
                    "seal_amount": it.get("order_amount"),
                    "high_days": it.get("high_days", ""),
                    "first_time": datetime.fromtimestamp(int(ft)).strftime("%H:%M:%S") if ft else "",
                    "is_again": it.get("is_again_limit"),
                    "date": d,
                })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("ths_basic ths_limit_secret failed: %s", e)
            return pl.DataFrame()

    # ==================== money_flow (北向资金) ====================

    def get_northbound(self) -> pl.DataFrame:
        """北向资金（沪深股通）分钟级累计净买入（亿元）。来源: 同花顺 hsgtApi。"""
        try:
            r = self._client.get(
                "https://data.hexin.cn/market/hsgtApi/method/dayChart/",
                headers={
                    "User-Agent": _UA,
                    "Host": "data.hexin.cn",
                    "Referer": "https://data.hexin.cn/",
                },
            )
            d = r.json()
            times = d.get("time") or []
            hgt = d.get("hgt") or []
            sgt = d.get("sgt") or []
            rows = []
            for i, t in enumerate(times):
                rows.append({
                    "time": t,
                    "hgt_net": float(hgt[i]) if i < len(hgt) and hgt[i] else None,
                    "sgt_net": float(sgt[i]) if i < len(sgt) and sgt[i] else None,
                })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("ths_basic northbound failed: %s", e)
            return pl.DataFrame()

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        symbols = symbols or ["600519.SH"]
        if dataset == "research":
            df = self.get_ths_eps(symbols[0])
        elif dataset == "limit_updown":
            df = self.get_ths_limit_secret()
        elif dataset == "money_flow":
            df = self.get_northbound()
        elif dataset == "daily":
            df = self.get_daily(symbols[:1])
        elif dataset == "financial":
            df = self.get_financial(symbols[0], report="debt")
        elif dataset == "consensus":
            df = self.get_consensus(symbols[0])
        else:
            raise ValueError(f"ths_basic 不支持数据集: {dataset}")
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns,
            "preview": df.head(3).to_dicts() if not df.is_empty() else [],
        }

    # ==================== daily / financial (按上游扩容, 同花顺口径) ====================

    @staticmethod
    def _ths_code6(symbol: str) -> str:
        return "".join(ch for ch in symbol if ch.isdigit()).zfill(6)

    @staticmethod
    def _parse_ths_kline(payload: str, symbol: str) -> list[dict]:
        """last.js/年度js 的 data 字段: date,open,high,low,close,volume(股),amount(元) 分号分隔。"""
        rows = []
        for line in payload.split(";"):
            parts = line.split(",")
            if len(parts) < 7:
                continue
            try:
                rows.append({
                    "symbol": symbol,
                    "date": parts[0],
                    "open": float(parts[1]),
                    "high": float(parts[2]),
                    "low": float(parts[3]),
                    "close": float(parts[4]),
                    "volume": round(float(parts[5]) / 100.0),  # 股 → 手
                    "amount": float(parts[6]),
                })
            except (TypeError, ValueError):
                continue
        return rows

    def get_daily(
        self,
        symbols: list[str],
        start_time: "datetime | None" = None,
        end_time: "datetime | None" = None,
        asset_type: str = "stock",  # noqa: ARG002
        on_chunk_done=None,
    ) -> pl.DataFrame:
        """同花顺 K 线全历史（2001 起，按年分文件）。原始价，volume 股→手。"""
        client = self._client
        now = datetime.now()
        start_year = start_time.year if start_time else 2001
        end_year = end_time.year if end_time else now.year
        years = list(range(max(start_year, 2001), end_year + 1))
        frames: list[pl.DataFrame] = []
        syms = symbols or []
        for i, symbol in enumerate(syms):
            code6 = self._ths_code6(symbol)
            recs: list[dict] = []
            for y in years:
                try:
                    r = client.get(
                        f"https://d.10jqka.com.cn/v6/line/hs_{code6}/01/{y}.js",
                        headers={"User-Agent": _UA, "Referer": "https://data.10jqka.com.cn/"},
                        timeout=15,
                    )
                    t = r.text
                    payload = t[t.index("(") + 1:t.rindex(")")] if "(" in t and ")" in t else ""
                    data = json.loads(payload).get("data") or ""
                    recs.extend(self._parse_ths_kline(data, symbol))
                except Exception:  # noqa: BLE001
                    continue
                time.sleep(0.15)
            if recs:
                df = pl.DataFrame(recs)
                if start_time is not None:
                    df = df.filter(pl.col("date") >= start_time.strftime("%Y-%m-%d"))
                if end_time is not None:
                    df = df.filter(pl.col("date") <= end_time.strftime("%Y-%m-%d"))
                if not df.is_empty():
                    frames.append(df)
            if on_chunk_done:
                on_chunk_done(i + 1, len(syms))
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    def get_financial(self, symbol: str, report: str = "debt") -> pl.DataFrame:
        """同花顺 F10 三表 JSON（_debt 资产负债 / _benefit 利润 / _cash 现金流）。

        返回长表: item(科目) × report_date(报告期) 值矩阵。
        """
        code6 = self._ths_code6(symbol)
        try:
            r = self._client.get(
                f"https://basic.10jqka.com.cn/api/stock/finance/{code6}_{report}.json",
                headers={"User-Agent": _UA, "Referer": "https://basic.10jqka.com.cn/"},
                timeout=20,
            )
            fd = json.loads(r.json().get("flashData") or "{}")
            title = fd.get("title") or []
            report_rows = fd.get("report") or []
            if not report_rows:
                return pl.DataFrame()
            dates = report_rows[0] or []
            rows: list[dict] = []
            for idx, values in enumerate(report_rows[1:]):
                name = ""
                if idx + 1 < len(title):
                    t = title[idx + 1]
                    name = t[0] if isinstance(t, list) else str(t)
                rec: dict = {"symbol": symbol, "item": name, "unit": (title[idx + 1][1] if isinstance(title[idx + 1], list) and len(title[idx + 1]) > 1 else "")}
                for j, d in enumerate(dates):
                    try:
                        rec[str(d)[:10]] = float(values[j]) if j < len(values) and values[j] not in ("", None) else None
                    except (TypeError, ValueError):
                        rec[str(d)[:10]] = values[j] if j < len(values) else None
                rows.append(rec)
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("ths_basic financial %s failed: %s", symbol, e)
            return pl.DataFrame()

    # ==================== consensus (一致预期 EPS) ====================

    def get_consensus(self, symbol: str) -> pl.DataFrame:
        """同花顺一致预期：worth.html 的年度 EPS 预测均值/最低/最高/机构数。"""
        try:
            df = self.get_ths_eps(symbol)
            if df.is_empty():
                return df
            return df.with_columns(pl.lit(datetime.now().strftime("%Y-%m-%d")).alias("date"))
        except Exception as e:  # noqa: BLE001
            logger.warning("ths_basic consensus %s failed: %s", symbol, e)
            return pl.DataFrame()

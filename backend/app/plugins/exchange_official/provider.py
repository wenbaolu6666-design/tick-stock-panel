"""Exchange-official (交易所一手) plugin — 沪深交易所官方接口。

- depth5: 上交所 yunhq.sse.com.cn:32041 snap + 深交所 szse.cn ssjjhq getTimeData
- dragon_tiger: 深交所竞价交易公开信息 (ShowReport CATALOGID=1842_xxpl, 整榜)

零鉴权；沪五档为 HTTP(非 HTTPS)；深市接口需带官网 Referer。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

import httpx
import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("depth5", "dragon_tiger")
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"}


def _normalize(code: str) -> str:
    return "".join(ch for ch in code if ch.isdigit()).zfill(6)


def availability() -> tuple[bool, str]:
    try:
        r = httpx.get(
            "http://yunhq.sse.com.cn:32041/v1/sh1/snap/000001",
            headers=_UA, timeout=10,
        )
        return True, f"ok ({r.status_code})" if r.status_code == 200 else f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, f"交易所官方接口不可达: {e}"


class ExchangeOfficialProvider:
    name = "exchange_official"
    builtin = True

    def __init__(self) -> None:
        self.datasets = dict.fromkeys(_DATASETS)
        self._client = httpx.Client(
            headers=_UA, timeout=12, verify=False,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    # ==================== depth5 (交易所官方五档) ====================

    def get_depth5(self, symbols: list[str]) -> pl.DataFrame:
        """沪深交易所官方五档快照（沪 yunhq snap / 深 szse getTimeData）。"""
        rows: list[dict] = []
        for symbol in symbols or []:
            code = _normalize(symbol)
            try:
                if symbol.upper().endswith(".SH") or code.startswith(("6", "9", "5")):
                    row = self._depth5_sse(code, symbol)
                else:
                    row = self._depth5_szse(code, symbol)
                if row:
                    rows.append(row)
            except Exception as e:  # noqa: BLE001
                logger.warning("exchange_official depth5 %s failed: %s", symbol, e)
            time.sleep(0.1)
        return pl.DataFrame(rows) if rows else pl.DataFrame()

    @staticmethod
    def _five_from_pairs(vals: list) -> tuple[list, list]:
        """(价,量)×10 交错序列 → 买五档/卖五档。"""
        prices = [float(x) for x in vals[0::2]]
        vols = [float(x) for x in vals[1::2]]
        return prices[:5], vols[:5]

    def _depth5_sse(self, code: str, symbol: str) -> dict | None:
        """沪 snap: [名称,现,昨收,高,低,开,涨跌,涨跌%,量,额,..., [买五档 价量交错×5]]（无卖档）。"""
        r = self._client.get(f"http://yunhq.sse.com.cn:32041/v1/sh1/snap/{code}", headers=_UA, timeout=10)
        snap = r.json().get("snap") or []
        if len(snap) < 15:
            return None
        row: dict = {
            "symbol": symbol,
            "timestamp": int(time.time() * 1000),
            "last_price": snap[1] if len(snap) > 1 else None,
            "prev_close": snap[2] if len(snap) > 2 else None,
        }
        bids = snap[14] if len(snap) > 14 and isinstance(snap[14], list) else []
        bp = [float(x) for x in bids[0::2]][:5]
        bv = [float(x) for x in bids[1::2]][:5]
        for i in range(5):
            row[f"bid{i + 1}_price"] = bp[i] if i < len(bp) else None
            row[f"bid{i + 1}_vol"] = bv[i] if i < len(bv) else None
            row[f"ask{i + 1}_price"] = None
            row[f"ask{i + 1}_vol"] = None
        return row

    def _depth5_szse(self, code: str, symbol: str) -> dict | None:
        """深 ssjjhq: data.sellbuy5 = [{price,volume}×10]，前 5 卖档、后 5 买档。"""
        r = self._client.get(
            "https://www.szse.cn/api/market/ssjjhq/getTimeData",
            params={"code": code, "marketId": 1},
            headers={**_UA, "Referer": "https://www.szse.cn/"},
            timeout=10,
        )
        data = (r.json().get("data") or {})
        if not data:
            return None
        row: dict = {
            "symbol": symbol,
            "timestamp": int(time.time() * 1000),
            "last_price": data.get("now"),
            "prev_close": data.get("close"),
        }
        sb = data.get("sellbuy5") or []
        if isinstance(sb, list) and len(sb) >= 10:
            asks = sb[0:5]
            bids = sb[5:10]
            for i in range(5):
                row[f"ask{i + 1}_price"] = float(asks[i].get("price") or 0) or None
                row[f"ask{i + 1}_vol"] = float(asks[i].get("volume") or 0) or None
                row[f"bid{i + 1}_price"] = float(bids[i].get("price") or 0) or None
                row[f"bid{i + 1}_vol"] = float(bids[i].get("volume") or 0) or None
        return row

    # ==================== dragon_tiger (深交所官方整榜) ====================

    def get_dragon_tiger(self, date: str | None = None) -> pl.DataFrame:
        """深交所竞价交易公开信息（官方一手龙虎榜整榜）。date=YYYY-MM-DD。"""
        d = date or datetime.now().strftime("%Y-%m-%d")
        try:
            r = self._client.get(
                "https://www.szse.cn/api/report/ShowReport/data",
                params={"SHOWTYPE": "JSON", "CATALOGID": "1842_xxpl", "PAGENO": 1,
                        "TXTQUERYDATE": d, "random": int(time.time() * 1000)},
                headers={**_UA, "Referer": "https://www.szse.cn/disclosure/listed/lhb/"},
                timeout=15,
            )
            blocks = r.json() or []
            rows: list[dict] = []
            for block in blocks:
                for x in (block.get("data") or []):
                    if not isinstance(x, dict):
                        continue
                    rec = {str(k).lower(): v for k, v in x.items()}
                    rows.append({
                        "symbol": _normalize(str(rec.get("gpdm") or rec.get("zqdm") or "")),
                        "name": rec.get("zqmc") or rec.get("gpjc"),
                        "reason": rec.get("lhyd") or rec.get("cbyd"),
                        "raw": rec,
                        "date": d,
                        "exchange": "SZSE",
                    })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("exchange_official dragon_tiger %s failed: %s", d, e)
            return pl.DataFrame()

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        symbols = symbols or ["600519.SH"]
        if dataset == "depth5":
            df = self.get_depth5(symbols[:3])
        elif dataset == "dragon_tiger":
            df = self.get_dragon_tiger()
        else:
            raise ValueError(f"exchange_official 不支持数据集: {dataset}")
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns,
            "preview": df.head(3).to_dicts() if not df.is_empty() else [],
        }

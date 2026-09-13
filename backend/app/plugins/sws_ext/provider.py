"""SWS (申万研究所) 行业变迁史 plugin — 申万行业分类历次调整。

- sector: StockClassifyUse_stock.xls（12910 行：股票代码/计入日期/行业代码/更新日期）
  历史时点的行业归属用「计入日期 ≤ t < 退出(更新)日期」消除前视偏差。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime

import httpx
import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("sector",)
_URL = "https://www.swsresearch.com/swindex/pdf/SwClass2021/StockClassifyUse_stock.xls"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"}

_cache: pl.DataFrame | None = None
_cache_lock = threading.Lock()


def _load_table() -> pl.DataFrame:
    """下载并解析 XLS（进程内缓存）。列: symbol / in_date / sw_code / update_date。"""
    global _cache
    with _cache_lock:
        if _cache is not None:
            return _cache
        import xlrd
        r = httpx.get(_URL, headers=_UA, timeout=40, follow_redirects=True, verify=False)
        r.raise_for_status()
        wb = xlrd.open_workbook(file_contents=r.content)
        sh = wb.sheet_by_index(0)
        rows = []
        for i in range(1, sh.nrows):
            code = str(sh.cell_value(i, 0)).zfill(6)
            in_d = _xlrd_date(wb, sh.cell_value(i, 1))
            sw_code = str(sh.cell_value(i, 2))
            up_d = _xlrd_date(wb, sh.cell_value(i, 3)) if sh.cell_value(i, 3) else None
            if not code or not sw_code:
                continue
            rows.append({
                "symbol": f"{code}.{'SH' if code.startswith(('6', '9', '5')) else 'SZ'}",
                "code6": code,
                "in_date": in_d,
                "sw_code": sw_code,
                "sw_level1": sw_code[:1],  # 申万一级行业码首位
                "update_date": up_d,
            })
        _cache = pl.DataFrame(rows) if rows else pl.DataFrame()
        return _cache


def _xlrd_date(wb, serial) -> str | None:
    if not serial:
        return None
    try:
        from xlrd.xldate import xldate_as_datetime
        return xldate_as_datetime(serial, wb.datemode).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return None


def availability() -> tuple[bool, str]:
    try:
        t = _load_table()
        return True, f"ok ({t.height} 行变迁记录)" if not t.is_empty() else "空表"
    except Exception as e:  # noqa: BLE001
        return False, f"申万行业变迁表不可用: {e}"


class SwsExtProvider:
    name = "sws_ext"
    builtin = True

    def __init__(self) -> None:
        self.datasets = dict.fromkeys(_DATASETS)

    def close(self) -> None:
        pass

    # ==================== sector (申万行业变迁史) ====================

    def get_sector(self, symbol: str | None = None, as_of: str | None = None) -> pl.DataFrame:
        """申万行业变迁史。symbol 过滤单只；as_of=YYYY-MM-DD 取该时点有效归属（消除前视偏差）。"""
        try:
            df = _load_table()
        except Exception as e:  # noqa: BLE001
            logger.warning("sws_ext sector load failed: %s", e)
            return pl.DataFrame()
        if df.is_empty():
            return df
        if symbol:
            code6 = "".join(ch for ch in symbol if ch.isdigit()).zfill(6)
            df = df.filter(pl.col("code6") == code6)
        if as_of:
            df = df.filter(
                (pl.col("in_date") <= as_of)
                & (pl.col("update_date").is_null() | (pl.col("update_date") > as_of))
            )
        return df.sort(["symbol", "in_date"])

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        if dataset != "sector":
            raise ValueError(f"sws_ext 不支持数据集: {dataset}")
        df = self.get_sector((symbols or ["600519.SH"])[0])
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns,
            "preview": df.head(3).to_dicts() if not df.is_empty() else [],
        }

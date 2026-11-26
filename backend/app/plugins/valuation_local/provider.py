# -*- coding: utf-8 -*-
"""估值股本本地插件 (valuation_local): 读 股票全量数据.zip 导入的日频估值/股本历史。

数据来源: 百度盘『股票全量数据.zip』(5789 股 × 1991-2025 日频, 30 列:
PE(TTM)/PB/PS/股息率/总股本/流通股本/总市值/流通市值/复权因子/涨停跌停价),
经 tools_valuation_import.py 导入 data/valuation_history/part-*.parquet。
与报告期口径的 financials/shares 语义不同, 独立表独立插件。
"""
from __future__ import annotations

import glob
import logging
import os
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("valuation_history",)


def _root() -> Path:
    from app.config import settings

    return settings.data_dir / "valuation_history"


def availability() -> tuple[bool, str]:
    try:
        files = glob.glob(str(_root() / "part-*.parquet"))
        if files:
            return True, f"ok (data/valuation_history {len(files)} 分片)"
        return False, "valuation_history 无数据(先运行 tools_valuation_import.py 导入)"
    except Exception as e:  # noqa: BLE001
        return False, f"valuation_history 不可用: {e}"


class ValuationLocalProvider:
    name = "valuation_local"
    display_name = "估值股本(本地历史)"

    def __init__(self, client=None) -> None:
        self._client = client

    def close(self) -> None:
        pass

    def get_valuation_history(self, symbol: str, days: int = 250) -> pl.DataFrame:
        """个股估值/股本日频历史(最近 days 个交易日, 升序)。"""
        files = sorted(glob.glob(str(_root() / "part-*.parquet")))
        if not files:
            return pl.DataFrame()
        out = []
        for p in files:
            try:
                df = pl.scan_parquet(p).filter(pl.col("symbol") == symbol).collect()
                if not df.is_empty():
                    out.append(df)
            except Exception as e:  # noqa: BLE001
                logger.warning("valuation_local read %s failed: %s", p, e)
        if not out:
            return pl.DataFrame()
        df = pl.concat(out).sort("date")
        return df.tail(days)

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        if dataset != "valuation_history":
            raise ValueError(f"valuation_local 不支持数据集: {dataset}")
        sym = (symbols or ["600519.SH"])[0]
        df = self.get_valuation_history(sym, days=5)
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns if not df.is_empty() else [],
            "preview": df.to_dicts() if not df.is_empty() else [],
        }

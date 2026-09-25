# -*- coding: utf-8 -*-
"""板块分时本地插件 (board_local): 读 TSP 落盘的板块分钟分时 parquet。

数据来源: 百度盘『通达信板块_分时数据』(2011-11~2026-01, 1/5/15/30/60 五个周期),
由 tools_board_minute_import.py 导入 data/board_minute/{m1,m5,m15,m30,m60}/date=YYYY-MM-DD/part.parquet。
通达信 880xxx 板块代码体系(880201=钨/概念/行业板块)。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("board_minute",)

_PERIODS = ("m1", "m5", "m15", "m30", "m60")


def _root() -> Path:
    from app.config import settings

    return settings.data_dir / "board_minute"


def _root_path() -> Path:
    return _root()


from pathlib import Path  # noqa: E402


def availability() -> tuple[bool, str]:
    try:
        root = _root()
        n = 0
        for p in _PERIODS:
            d = root / p
            if d.exists():
                n += sum(1 for _ in d.glob("date=*/part.parquet"))
        if n:
            return True, f"ok (data/board_minute {n} 分区)"
        return False, "board_minute 无数据(先运行 tools_board_minute_import.py 导入)"
    except Exception as e:  # noqa: BLE001
        return False, f"board_minute 不可用: {e}"


def list_boards(period: str = "m1") -> list[dict]:
    """板块清单(从最近一个分区取样)。返回 [{code, name}]。"""
    root = _root()
    d = root / period
    if not d.exists():
        return []
    files = sorted(d.glob("date=*/part.parquet"))
    if not files:
        return []
    df = pl.read_parquet(files[-1], columns=["board_code", "board_name"])
    out = (df.unique(subset=["board_code"], keep="first").sort("board_code").to_dicts())
    return out


class BoardLocalProvider:
    name = "board_local"
    display_name = "板块分时(通达信本地)"

    def __init__(self, client=None) -> None:
        self._client = client

    def close(self) -> None:
        pass

    def get_board_minute(
        self,
        board_code: str,
        date: str | None = None,
        period: str = "m1",
        days: int = 1,
    ) -> pl.DataFrame:
        """板块分钟分时。board_code=880xxx; date 缺省取最近有数据日期; days 向前 N 日。

        返回列: datetime / board_code / board_name / open / preclose / high / low /
        volume / amount / amplitude / avg。
        """
        root = _root()
        pdir = root / (period if period in _PERIODS else "m1")
        parts = sorted(pdir.glob("date=*/part.parquet")) if pdir.exists() else []
        if not parts:
            return pl.DataFrame()
        if date:
            want = datetime.fromisoformat(date).date()
            parts = [p for p in parts if datetime.fromisoformat(p.parent.name.removeprefix("date=")).date() <= want][-days:]
        else:
            parts = parts[-days:]
        frames = []
        for p in parts:
            try:
                df = pl.read_parquet(p, columns=[
                    "datetime", "board_code", "board_name", "open", "preclose",
                    "high", "low", "volume", "amount", "amplitude", "avg",
                ])
                frames.append(df)
            except Exception as e:  # noqa: BLE001
                logger.warning("board_local read %s failed: %s", p, e)
        if not frames:
            return pl.DataFrame()
        df = pl.concat(frames)
        if board_code:
            df = df.filter(pl.col("board_code") == board_code)
        return df.sort("datetime")

    def list_periods(self) -> list[str]:
        root = _root()
        out = [p for p in _PERIODS if (root / p).exists() and any((root / p).glob("date=*"))]
        return out

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        if dataset != "board_minute":
            raise ValueError(f"board_local 不支持数据集: {dataset}")
        code = (symbols or ["880201"])[0]
        df = self.get_board_minute(code)
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns if not df.is_empty() else [],
            "preview": df.head(3).to_dicts() if not df.is_empty() else [],
        }

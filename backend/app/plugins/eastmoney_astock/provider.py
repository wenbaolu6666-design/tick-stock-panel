"""Eastmoney A-share built-in plugin (raw daily/minute + full-market snapshot).

Direct HTTP, no API key. Eastmoney aggressively rate-limits by IP, so every
request is throttled (default 1s + jitter) and all requests stay serialized.
"""
from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime

import httpx
import polars as pl

from app.tickflow.rate_limits import chunked

logger = logging.getLogger(__name__)

_DATASETS = ("daily", "minute", "full_minute", "realtime")
_KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_CLIST = "https://push2.eastmoney.com/api/qt/clist/get"
_ULIST = "https://push2.eastmoney.com/api/qt/ulist.np/get"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://quote.eastmoney.com/",
}
_KLT = {"daily": 101, "minute": 1}
# A-share stock universe (SH + SZ + BJ)
_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
_MIN_INTERVAL = float(os.getenv("EASTMONEY_MIN_INTERVAL", "1.0"))
_PAGE = 1000
_MAX_PAGES = 20
_MINUTE_CANONICAL = ["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"]


def _f(value) -> float | None:
    if value in (None, "-", ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _secid(symbol: str) -> str | None:
    s = symbol.upper()
    if s.endswith(".SH"):
        return f"1.{''.join(ch for ch in s if ch.isdigit()).zfill(6)}"
    if s.endswith((".SZ", ".BJ")):
        return f"0.{''.join(ch for ch in s if ch.isdigit()).zfill(6)}"
    code = "".join(ch for ch in s if ch.isdigit()).zfill(6)
    if code.startswith(("60", "68", "90", "5")):
        return f"1.{code}"
    return f"0.{code}"


def _symbol_from_code(code: str, market: str | None = None) -> str:
    code = "".join(ch for ch in str(code) if ch.isdigit()).zfill(6)
    if market == "1" or code.startswith(("60", "68", "90", "5")):
        return f"{code}.SH"
    if code.startswith(("4", "8", "92")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def availability() -> tuple[bool, str]:
    return True, "ok"


def _throttle() -> None:
    time.sleep(_MIN_INTERVAL + random.uniform(0, 0.3))


@dataclass
class _EMConfig:
    name: str = "eastmoney_astock"
    display_name: str = "东方财富 A股"
    datasets: dict = field(default_factory=lambda: dict.fromkeys(_DATASETS))
    path: None = None
    builtin: bool = True


class EastmoneyAstockProvider:
    name = "eastmoney_astock"
    builtin = True
    minute_history_days = 30

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.config = _EMConfig()
        self._client = client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=20.0)
        return self._client

    # ---- kline ----
    def _kline(
        self, symbol: str, klt: int, beg: str = "0", end: str = "20500101"
    ) -> list[dict]:
        client = self._get_client()
        secid = _secid(symbol)
        if not secid:
            return []
        r = client.get(
            _KLINE,
            params={
                "secid": secid,
                "klt": klt,
                "fqt": 0,
                "beg": beg,
                "end": end,
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57",
                "lmt": "1000000",
            },
            headers=_HEADERS,
        )
        r.raise_for_status()
        data = r.json().get("data") or {}
        klines = data.get("klines") or []
        rows = []
        for line in klines:
            parts = str(line).split(",")
            if len(parts) < 7:
                continue
            rows.append({
                "datetime": parts[0],
                "open": _f(parts[1]),
                "close": _f(parts[2]),
                "high": _f(parts[3]),
                "low": _f(parts[4]),
                "volume": _f(parts[5]),
                "amount": _f(parts[6]),
            })
        return rows

    def get_daily(
        self,
        symbols: list[str],
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        asset_type: str = "stock",  # noqa: ARG002
        on_chunk_done=None,
    ) -> pl.DataFrame:
        if not symbols:
            return pl.DataFrame()
        frames: list[pl.DataFrame] = []
        for i, symbol in enumerate(symbols):
            try:
                beg = start_time.strftime("%Y%m%d") if start_time else "0"
                end = end_time.strftime("%Y%m%d") if end_time else "20500101"
                rows = self._kline(symbol, _KLT["daily"], beg=beg, end=end)
                df = self._kline_df(rows, symbol, daily=True)
            except Exception as e:  # noqa: BLE001
                logger.warning("eastmoney daily %s failed: %s", symbol, e)
                df = pl.DataFrame()
            if not df.is_empty():
                frames.append(df)
            _throttle()
            if on_chunk_done:
                on_chunk_done(i + 1, len(symbols))
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    def get_minute(
        self,
        symbols: list[str],
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        asset_type: str = "stock",  # noqa: ARG002
        freq: str = "1m",
        on_chunk_done=None,
    ) -> pl.DataFrame:
        if not symbols:
            return pl.DataFrame()
        frames: list[pl.DataFrame] = []
        for i, symbol in enumerate(symbols):
            try:
                rows = self._kline(symbol, _KLT["minute"])
                df = self._kline_df(rows, symbol, daily=False)
                if start_time:
                    df = df.filter(pl.col("datetime") >= start_time)
                if end_time:
                    df = df.filter(pl.col("datetime") <= end_time)
            except Exception as e:  # noqa: BLE001
                logger.warning("eastmoney minute %s failed: %s", symbol, e)
                df = pl.DataFrame()
            if not df.is_empty():
                frames.append(df)
            _throttle()
            if on_chunk_done:
                on_chunk_done(i + 1, len(symbols))
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    def get_intraday_batch(
        self, symbols: list[str], count: int = 300, asset_type: str = "stock"  # noqa: ARG002
    ) -> pl.DataFrame:
        if not symbols:
            return pl.DataFrame()
        frames: list[pl.DataFrame] = []
        for symbol in symbols:
            try:
                rows = self._kline(symbol, _KLT["minute"])
                df = self._kline_df(rows, symbol, daily=False)
                if not df.is_empty():
                    latest = df["datetime"].max()
                    df = df.filter(pl.col("datetime").dt.date() == latest.date())
            except Exception as e:  # noqa: BLE001
                logger.warning("eastmoney intraday %s failed: %s", symbol, e)
                df = pl.DataFrame()
            if not df.is_empty():
                frames.append(df)
            _throttle()
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    @staticmethod
    def _kline_df(rows: list[dict], symbol: str, daily: bool) -> pl.DataFrame:
        if not rows:
            return pl.DataFrame()
        df = pl.DataFrame(rows)
        if daily:
            df = df.with_columns(
                pl.col("datetime").str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False).alias("date"),
                pl.lit(symbol).alias("symbol"),
            ).drop("datetime").rename({"close": "close"})
            keep = ["symbol", "date", "open", "high", "low", "close", "volume", "amount"]
            df = df.select([c for c in keep if c in df.columns]).rename({"date": "date"})
        else:
            df = df.with_columns(
                pl.col("datetime").str.to_datetime("%Y-%m-%d %H:%M:%S", strict=False).alias("datetime"),
                pl.lit(symbol).alias("symbol"),
            )
            keep = [c for c in _MINUTE_CANONICAL if c in df.columns]
            df = df.select(keep)
        return df.drop_nulls(subset=["open", "high", "low", "close"])

    # ---- realtime ----
    def _clist(self, fields: str) -> list[dict]:
        client = self._get_client()
        rows: list[dict] = []
        for page in range(1, _MAX_PAGES + 1):
            r = client.get(
                _CLIST,
                params={
                    "pn": page,
                    "pz": _PAGE,
                    "po": 1,
                    "np": 1,
                    "fltt": 2,
                    "invt": 2,
                    "fid": "f12",
                    "fs": _FS,
                    "fields": fields,
                },
                headers=_HEADERS,
            )
            r.raise_for_status()
            diff = (r.json().get("data") or {}).get("diff") or []
            if not diff:
                break
            rows.extend(diff)
            if len(diff) < _PAGE:
                break
            time.sleep(0.2)
        return rows

    def get_realtime(self) -> list[dict]:
        rows = self._clist("f12,f13,f14,f2,f18,f15,f16,f17,f5,f6,f3,f4")
        out = []
        for d in rows:
            out.append(self._quote(d))
        return out

    def get_realtime_indices(self, symbols: list[str]) -> list[dict]:
        if not symbols:
            return []
        secids = [_secid(s) for s in symbols if _secid(s)]
        if not secids:
            return []
        client = self._get_client()
        r = client.get(
            _ULIST,
            params={
                "secids": ",".join(secids),
                "fltt": 2,
                "invt": 2,
                "fields": "f12,f13,f14,f2,f18,f15,f16,f17,f5,f6,f3,f4",
            },
            headers=_HEADERS,
        )
        r.raise_for_status()
        out = []
        for d in (r.json().get("data") or {}).get("diff") or []:
            out.append(self._quote(d))
        return out

    @staticmethod
    def _quote(d: dict) -> dict:
        symbol = _symbol_from_code(d.get("f12"), d.get("f13"))
        price = _f(d.get("f2"))
        prev = _f(d.get("f18"))
        return {
            "symbol": symbol,
            "name": d.get("f14"),
            "last_price": price,
            "prev_close": prev,
            "open": _f(d.get("f17")),
            "high": _f(d.get("f15")),
            "low": _f(d.get("f16")),
            "volume": _f(d.get("f5")),
            "amount": _f(d.get("f6")),
            "change_pct": (_f(d.get("f3")) / 100.0) if _f(d.get("f3")) is not None else None,
            "change_amount": _f(d.get("f4")),
            "amplitude": None,
            "turnover_rate": None,
            "timestamp": int(time.time() * 1000),
            "session": None,
        }

    # ---- instruments ----
    def get_instruments(self, asset_type: str = "stock") -> list[dict]:
        if asset_type != "stock":
            return []
        rows = self._clist("f12,f13,f14")
        out = []
        for d in rows:
            code = "".join(ch for ch in str(d.get("f12") or "") if ch.isdigit()).zfill(6)
            if not code:
                continue
            out.append({
                "symbol": _symbol_from_code(code, d.get("f13")),
                "name": d.get("f14") or "",
                "code": code,
                "exchange": _symbol_from_code(code, d.get("f13")).split(".")[-1],
                "region": "CN",
                "type": "stock",
                "ext": {},
            })
        return out

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        symbols = symbols or ["600519.SH"]
        if dataset == "daily":
            df = self.get_daily(symbols)
        elif dataset == "minute":
            df = self.get_minute(symbols)
        elif dataset == "full_minute":
            df = self.get_intraday_batch(symbols)
        elif dataset == "realtime":
            rows = self.get_realtime()
            head = rows[:5]
            return {
                "provider": self.name,
                "dataset": dataset,
                "rows": len(rows),
                "columns": list(head[0].keys()) if head else [],
                "preview": head,
            }
        else:
            raise ValueError(f"eastmoney_astock 不支持数据集: {dataset}")
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns,
            "preview": df.head(5).to_dicts() if not df.is_empty() else [],
        }

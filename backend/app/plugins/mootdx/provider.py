"""Mootdx/TDX built-in A-share data source plugin.

Raw (unadjusted) daily/minute bars + full-market realtime quotes via the
TDX binary protocol. Depends on optional `mootdx` package; plugin loader
keeps it unavailable until a reachable TDX server is found.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

import polars as pl

from app.data_providers.normalizer import normalize_daily
from app.tickflow.rate_limits import chunked

logger = logging.getLogger(__name__)

_DATASETS = ("daily", "minute", "full_minute", "realtime", "depth5")
_BATCH = 40
_MINUTE_CANONICAL = ["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"]
# TDX minute history is shallow (a few trading days for 1m bars).
_MINUTE_HISTORY_DAYS = 5

# Community-maintained public TDX servers (a-stock-data 2026-06 list).
_TDX_SERVERS = [
    ("119.97.185.59", 7709),
    ("124.70.133.119", 7709),
    ("116.205.183.150", 7709),
    ("123.60.73.44", 7709),
    ("116.205.163.254", 7709),
    ("121.36.225.169", 7709),
    ("123.60.70.228", 7709),
    ("124.71.9.153", 7709),
    ("110.41.147.114", 7709),
    ("124.71.187.122", 7709),
]

_STOCK_PREFIX = {
    "SH": ("60", "68", "90"),
    "SZ": ("00", "30", "20"),
}


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_lots(value: Any) -> float | None:
    """TDX volume is in shares; TSP A-share contract uses hands."""
    v = _f(value)
    if v is None:
        return None
    return int(v // 100.0)


def _fmt(dt: datetime | None, default: datetime | None = None) -> str | None:
    dt = dt or default
    return dt.strftime("%Y%m%d") if dt else None


def _suffix_of(symbol: str) -> str:
    s = symbol.upper()
    for sfx in (".SH", ".SZ", ".BJ"):
        if s.endswith(sfx):
            return sfx[1:]
    # bare 6-digit code -> guess exchange by prefix
    code = "".join(ch for ch in s if ch.isdigit())
    if code.startswith(("60", "68", "90", "5")):
        return "SH"
    if code.startswith(("00", "30", "20", "1", "39")):
        return "SZ"
    return "SZ"


def _code_of(symbol: str) -> str:
    s = symbol.upper()
    for sfx in (".SH", ".SZ", ".BJ"):
        if s.endswith(sfx):
            s = s[: -len(sfx)]
            break
    return "".join(ch for ch in s if ch.isdigit()).zfill(6)


def _records_from_frame(df: Any) -> list[dict]:
    """Convert a mootdx/pandas frame (or list) into plain records."""
    if df is None:
        return []
    if isinstance(df, list):
        return [dict(r) for r in df]
    if not hasattr(df, "to_dict"):
        return []
    try:
        import pandas as pd
        if not isinstance(df.index, pd.RangeIndex):
            df = df.reset_index()
            if df.columns[0] in (None, "index"):
                df = df.rename(columns={df.columns[0]: "datetime"})
        records = df.to_dict("records")
    except Exception:
        return []
    return [dict(r) for r in records]


_DISCOVERY_COOLDOWN_S = 300.0
_discovery_failed_until = 0.0
_last_discovery_err = ""


def _safe_close(client) -> None:
    """Best-effort close a half-initialized mootdx client to avoid socket leaks."""
    for name in ("close", "disconnect", "exit"):
        fn = getattr(client, name, None)
        if callable(fn):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
            return


def _new_client():
    """Create a validated TDX client (imports mootdx lazily).

    Failed candidates are closed before continuing, and a failed discovery
    trip is cached for 5 minutes so pollers do not re-dial every cycle.
    """
    global _discovery_failed_until, _last_discovery_err
    if time.monotonic() < _discovery_failed_until:
        raise RuntimeError(f"no reachable TDX server (cooldown): {_last_discovery_err}")

    from mootdx.quotes import Quotes

    def probe(ip, port, timeout=2.0):
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return True
        except OSError:
            return False

    def validate(client) -> bool:
        try:
            df = client.bars(symbol="000001", frequency=9, offset=1)
            return df is not None and len(df) > 0
        except Exception:
            return False

    last_err = None
    for ip, port in _TDX_SERVERS:
        if not probe(ip, port):
            continue
        client = None
        try:
            client = Quotes.factory(market="std", server=(ip, port))
            if validate(client):
                return client
            _safe_close(client)
        except Exception as e:  # noqa: BLE001
            last_err = e
            if client is not None:
                _safe_close(client)
            continue
    # fall back to mootdx's own bestip discovery (may require config)
    for kwargs in ({"bestip": True}, {}):
        client = None
        try:
            client = Quotes.factory(market="std", **kwargs)
            if validate(client):
                return client
            _safe_close(client)
        except Exception as e:  # noqa: BLE001
            last_err = e
            if client is not None:
                _safe_close(client)
            continue
    _last_discovery_err = str(last_err)
    _discovery_failed_until = time.monotonic() + _DISCOVERY_COOLDOWN_S
    raise RuntimeError(f"no reachable TDX server: {last_err}")


def availability() -> tuple[bool, str]:
    """Plugin gating: only require the optional dependency to be importable.

    TDX server reachability is validated lazily on first real fetch, so a
    temporary network failure never blocks startup or plugin reload.
    """
    import importlib.util

    if importlib.util.find_spec("mootdx") is None:
        return False, "未安装 mootdx（pip install mootdx）"
    return True, "ok"
@dataclass
class _MootdxConfig:
    name: str = "mootdx"
    display_name: str = "通达信/Mootdx A股"
    datasets: dict = field(default_factory=lambda: dict.fromkeys(_DATASETS))
    path: None = None
    builtin: bool = True


class MootdxProvider:
    name = "mootdx"
    builtin = True
    minute_history_days = _MINUTE_HISTORY_DAYS

    def __init__(self, client: Any = None) -> None:
        self.config = _MootdxConfig()
        self._client = client
        self._lock = threading.Lock()

    def close(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    self._client = _new_client()
        return self._client

    # ---- daily ----
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
        client = self._get_client()
        frames: list[pl.DataFrame] = []
        start = _fmt(start_time)
        end = _fmt(end_time)
        for i, symbol in enumerate(symbols):
            code = _code_of(symbol)
            try:
                # get_k_data is the ranged daily API when available
                getter = getattr(client, "get_k_data", None)
                if getter is not None and start and end:
                    df = getter(code=code, start_date=start, end_date=end)
                else:
                    df = client.bars(symbol=code, frequency=9, offset=900)
                rows = _records_from_frame(df)
                daily = self._daily_rows(rows, symbol)
            except Exception as e:  # noqa: BLE001
                logger.warning("mootdx daily %s failed: %s", symbol, e)
                daily = []
            if daily:
                frame = normalize_daily(daily, source=self.name)
                if not frame.is_empty():
                    frames.append(frame)
            if on_chunk_done:
                on_chunk_done(i + 1, len(symbols))
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    def _daily_rows(self, rows: list[dict], symbol: str) -> list[dict]:
        out: list[dict] = []
        for r in rows:
            dt = r.get("datetime") or r.get("date") or r.get("trade_date")
            if dt is None:
                continue
            if isinstance(dt, datetime):
                date_s = dt.strftime("%Y-%m-%d")
            else:
                date_s = str(dt)[:10]
            out.append({
                "ts_code": symbol,
                "trade_date": date_s,
                "open": _f(r.get("open")),
                "high": _f(r.get("high")),
                "low": _f(r.get("low")),
                "close": _f(r.get("close")),
                "vol": _to_lots(r.get("vol") or r.get("volume")),
                "amount": _f(r.get("amount")),
            })
        return out

    # ---- minute ----
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
        client = self._get_client()
        frames: list[pl.DataFrame] = []
        for i, symbol in enumerate(symbols):
            code = _code_of(symbol)
            try:
                df = client.bars(symbol=code, frequency=8, offset=_bars_to_fetch(start_time))
                rows = _records_from_frame(df)
                frame = self._minute_df(rows, symbol)
                frame = self._filter_window(frame, start_time, end_time)
            except Exception as e:  # noqa: BLE001
                logger.warning("mootdx minute %s failed: %s", symbol, e)
                frame = pl.DataFrame()
            if not frame.is_empty():
                frames.append(frame)
            if on_chunk_done:
                on_chunk_done(i + 1, len(symbols))
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    def get_intraday_batch(
        self,
        symbols: list[str],
        count: int = 300,
        asset_type: str = "stock",  # noqa: ARG002
    ) -> pl.DataFrame:
        if not symbols:
            return pl.DataFrame()
        client = self._get_client()
        frames: list[pl.DataFrame] = []
        for symbol in symbols:
            code = _code_of(symbol)
            try:
                df = client.bars(symbol=code, frequency=8, offset=max(240, int(count)))
                rows = _records_from_frame(df)
                frame = self._minute_df(rows, symbol)
                if not frame.is_empty():
                    latest = frame["datetime"].max()
                    frame = frame.filter(pl.col("datetime").dt.date() == latest.date())
            except Exception as e:  # noqa: BLE001
                logger.warning("mootdx intraday %s failed: %s", symbol, e)
                frame = pl.DataFrame()
            if not frame.is_empty():
                frames.append(frame)
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    @staticmethod
    def _minute_df(rows: list[dict], symbol: str) -> pl.DataFrame:
        if not rows:
            return pl.DataFrame()
        recs = []
        for r in rows:
            dt = r.get("datetime") or r.get("date")
            if dt is None:
                continue
            if isinstance(dt, datetime):
                ts = dt.replace(tzinfo=None)
            else:
                ts = datetime.fromisoformat(str(dt).replace("T", " "))
            recs.append({
                "symbol": symbol,
                "datetime": ts,
                "open": _f(r.get("open")),
                "high": _f(r.get("high")),
                "low": _f(r.get("low")),
                "close": _f(r.get("close")),
                "volume": _to_lots(r.get("vol") or r.get("volume")),
                "amount": _f(r.get("amount")),
            })
        if not recs:
            return pl.DataFrame()
        df = pl.DataFrame(recs)
        keep = [c for c in _MINUTE_CANONICAL if c in df.columns]
        return df.select(keep) if "datetime" in keep else pl.DataFrame()

    @staticmethod
    def _filter_window(frame: pl.DataFrame, start_time, end_time) -> pl.DataFrame:
        if frame.is_empty():
            return frame
        if start_time:
            frame = frame.filter(pl.col("datetime") >= start_time)
        if end_time:
            frame = frame.filter(pl.col("datetime") <= end_time)
        return frame

    # ---- realtime ----
    def get_realtime(self) -> list[dict]:
        client = self._get_client()
        instruments = self.get_instruments("stock", client=client)
        if not instruments:
            return []
        codes = [i["code"] for i in instruments]
        symbol_by_code = {i["code"]: i["symbol"] for i in instruments}
        rows: list[dict] = []
        for chunk in chunked(codes, _BATCH):
            try:
                df = client.quotes(symbol=chunk)
                rows.extend(self._quote_rows(_records_from_frame(df), symbol_by_code))
            except Exception as e:  # noqa: BLE001
                logger.warning("mootdx quotes chunk failed: %s", e)
        return rows

    def get_realtime_indices(self, symbols: list[str]) -> list[dict]:
        client = self._get_client()
        if not symbols:
            return []
        symbol_by_code = {_code_of(s): s for s in symbols}
        codes = list(symbol_by_code)
        rows: list[dict] = []
        for chunk in chunked(codes, _BATCH):
            try:
                df = client.quotes(symbol=chunk)
                rows.extend(self._quote_rows(_records_from_frame(df), symbol_by_code))
            except Exception as e:  # noqa: BLE001
                logger.warning("mootdx index quotes chunk failed: %s", e)
        return rows

    @staticmethod
    def _quote_rows(records: list[dict], symbol_by_code: dict[str, str]) -> list[dict]:
        out = []
        for r in records:
            code = str(r.get("code") or "").zfill(6)
            symbol = symbol_by_code.get(code)
            if not symbol:
                continue
            price = _f(r.get("price"))
            prev = _f(r.get("last_close"))
            out.append({
                "symbol": symbol,
                "name": r.get("name"),
                "last_price": price,
                "prev_close": prev,
                "open": _f(r.get("open")),
                "high": _f(r.get("high")),
                "low": _f(r.get("low")),
                "volume": _to_lots(r.get("vol") or r.get("volume")),
                "amount": _f(r.get("amount")),
                "change_pct": (price - prev) / prev if (price is not None and prev) else None,
                "change_amount": (price - prev) if (price is not None and prev is not None) else None,
                "amplitude": None,
                "turnover_rate": None,
                "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
                "session": None,
            })
        return out

    # ---- instruments ----
    def get_instruments(self, asset_type: str = "stock", client: Any = None) -> list[dict]:
        if asset_type != "stock":
            return []
        client = client or self._get_client()
        rows: list[dict] = []
        for market, suffix in ((0, "SZ"), (1, "SH")):
            try:
                df = client.stocks(market=market)
                for r in _records_from_frame(df):
                    code = str(r.get("code") or r.get("symbol") or "").split(".")[0].zfill(6)
                    if not self._is_stock(code, suffix):
                        continue
                    rows.append({
                        "symbol": f"{code}.{suffix}",
                        "name": r.get("name") or r.get("code_name") or "",
                        "code": code,
                        "exchange": suffix,
                        "region": "CN",
                        "type": "stock",
                        "ext": {},
                    })
            except Exception as e:  # noqa: BLE001
                logger.warning("mootdx stocks(market=%s) failed: %s", market, e)
        return rows

    @staticmethod
    def _is_stock(code: str, suffix: str) -> bool:
        prefixes = _STOCK_PREFIX.get(suffix, ())
        return any(code.startswith(p) for p in prefixes)

    # ---- depth5 ----
    def get_depth5(self, symbols: list[str]) -> pl.DataFrame:
        """五档盘口（TCP quotes：bid1..5/ask1..5 价量）。逐票调用，适合小批量场景。"""
        if not symbols:
            return pl.DataFrame()
        client = self._get_client()
        rows: list[dict] = []
        quotes_fn = getattr(client, "quotes", None)
        for symbol in symbols:
            code = _code_of(symbol)
            try:
                q = quotes_fn(symbol=[code]) if quotes_fn else None
                if q is None or len(q) == 0:
                    continue
                rec = q.iloc[0].to_dict() if hasattr(q, "iloc") else (q[0] if isinstance(q, list) else {})
                row: dict = {"symbol": symbol, "timestamp": int(time.time() * 1000)}
                for i in range(1, 6):
                    bp = rec.get(f"bid{i}") or rec.get(f"buy_price{i}")
                    bv = rec.get(f"bid_vol{i}") or rec.get(f"buy_vol{i}")
                    ap = rec.get(f"ask{i}") or rec.get(f"sale_price{i}")
                    av = rec.get(f"ask_vol{i}") or rec.get(f"sale_vol{i}")
                    row[f"bid{i}_price"] = _f(bp)
                    row[f"bid{i}_vol"] = _f(bv)
                    row[f"ask{i}_price"] = _f(ap)
                    row[f"ask{i}_vol"] = _f(av)
                rows.append(row)
            except Exception as e:  # noqa: BLE001
                logger.warning("mootdx depth5 %s failed: %s", symbol, e)
            time.sleep(0.08)
        return pl.DataFrame(rows) if rows else pl.DataFrame()

    # ---- test ----
    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        symbols = symbols or ["600519.SH"]
        if dataset == "daily":
            df = self.get_daily(symbols, None, None)
        elif dataset == "minute":
            df = self.get_minute(symbols, None, None)
        elif dataset == "depth5":
            df = self.get_depth5(symbols[:3])
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
            raise ValueError(f"mootdx 不支持数据集: {dataset}")
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns,
            "preview": df.head(5).to_dicts() if not df.is_empty() else [],
        }


def _bars_to_fetch(start_time: datetime | None) -> int:
    if start_time is None:
        return 240 * _MINUTE_HISTORY_DAYS
    days = (datetime.now() - start_time).days
    return max(240, min(days + 2, 240 * 30)) * 1

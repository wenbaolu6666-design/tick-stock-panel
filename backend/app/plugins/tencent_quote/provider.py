"""Tencent Finance realtime-quote built-in plugin.

Full-market A-share snapshot using the public qt.gtimg.cn batch endpoint.
The instrument universe comes from TSP's own data/instruments parquet, so the
plugin is only meaningful once instruments have been synced (daily source).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import polars as pl

from app.config import settings
from app.tickflow.rate_limits import chunked

logger = logging.getLogger(__name__)

_DATASETS = ("realtime", "daily", "minute", "valuation")
_BASE = "https://qt.gtimg.cn/q="
_FQKLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_MKLINE = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
_BATCH = 80
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_TZ = ZoneInfo("Asia/Shanghai")


def _tencent_code(symbol: str) -> str:
    s = symbol.upper()
    code = "".join(ch for ch in s if ch.isdigit()).zfill(6)
    if s.endswith(".SH") or code.startswith(("60", "68", "90", "5")):
        return f"sh{code}"
    if s.endswith(".BJ") or code.startswith(("4", "8", "92")):
        return f"bj{code}"
    return f"sz{code}"


def _symbol_from_key(key: str, fallback_code: str) -> str:
    # key like "sh600519" or "v_sh600519" (we strip prefix in caller)
    prefix = key[:2]
    code = "".join(ch for ch in key[2:] if ch.isdigit()).zfill(6)
    suffix = {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(prefix, "SZ")
    return f"{code}.{suffix}" if code else fallback_code


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_ms(value) -> int | None:
    if not value:
        return None
    try:
        dt = datetime.strptime(str(value).strip(), "%Y%m%d%H%M%S").replace(tzinfo=_TZ)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


def _instrument_symbols() -> list[str]:
    """Read TSP's instrument parquet for the realtime universe (仅 A 股股票, 排除 ETF/债券/指数)."""
    path = Path(settings.data_dir) / "instruments" / "instruments.parquet"
    if not path.exists():
        return []
    try:
        df = pl.read_parquet(path)
    except Exception as e:  # noqa: BLE001
        logger.warning("tencent_quote read instruments failed: %s", e)
        return []
    if "symbol" not in df.columns:
        return []

    def is_a_share(s: str) -> bool:
        import re as _re
        m = _re.match(r"^(\d{6})\.(SH|SZ|BJ)$", s)
        if not m:
            return False
        c, suf = m.group(1), m.group(2)
        if suf == "SH":
            return c[:2] in ("60", "68")
        if suf == "SZ":
            return c[:2] in ("00", "30")
        return c[:2] in ("43", "83", "87", "88", "92")

    syms = [str(s) for s in df["symbol"].to_list() if s and is_a_share(str(s))]
    return sorted(set(syms))


def availability() -> tuple[bool, str]:
    path = Path(settings.data_dir) / "instruments" / "instruments.parquet"
    if not path.exists():
        return False, "未找到标的维表 instruments.parquet（先同步一次日K/标的）"
    return True, "ok"


@dataclass
class _TencentConfig:
    name: str = "tencent_quote"
    display_name: str = "腾讯实时行情"
    datasets: dict = field(default_factory=lambda: dict.fromkeys(_DATASETS))
    path: None = None
    builtin: bool = True


class TencentQuoteProvider:
    name = "tencent_quote"
    builtin = True

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.config = _TencentConfig()
        self._client = client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=15.0)
        return self._client

    def get_realtime(self) -> list[dict]:
        symbols = _instrument_symbols()
        if not symbols:
            return []
        return self._fetch(symbols)

    def get_realtime_indices(self, symbols: list[str]) -> list[dict]:
        return self._fetch(symbols)

    def _fetch(self, symbols: list[str]) -> list[dict]:
        client = self._get_client()
        out: list[dict] = []
        for chunk in chunked(symbols, _BATCH):
            codes = [_tencent_code(s) for s in chunk]
            url = _BASE + ",".join(codes)
            try:
                r = client.get(url, headers=_HEADERS)
                r.raise_for_status()
                text = r.content.decode("gbk", errors="ignore")
                out.extend(self._parse(text, chunk))
            except Exception as e:  # noqa: BLE001
                logger.warning("tencent_quote chunk failed (%d codes): %s", len(chunk), e)
            time.sleep(0.05)
        return out

    @staticmethod
    def _parse(text: str, chunk: list[str]) -> list[dict]:
        by_code: dict[str, str] = {}
        for s in chunk:
            by_code[_tencent_code(s)[2:]] = s
        rows: list[dict] = []
        for line in text.split(";"):
            line = line.strip()
            if "=" not in line or '"' not in line:
                continue
            key = line.split("=")[0].strip()
            key = key.rsplit("_", 1)[-1] if "_" in key else key  # v_sh600519 -> sh600519
            vals = line.split('"', 1)[1].split('"', 1)[0].split("~")
            if len(vals) < 50:
                continue
            code6 = "".join(ch for ch in key[2:] if ch.isdigit()).zfill(6)
            symbol = by_code.get(code6) or _symbol_from_key(key, code6)
            price = _to_float(vals[3])
            prev = _to_float(vals[4])
            ts = _to_ms(vals[30]) if len(vals) > 30 else None
            rows.append({
                "symbol": symbol,
                "name": vals[1],
                "last_price": price,
                "prev_close": prev,
                "open": _to_float(vals[5]),
                "high": _to_float(vals[33]),
                "low": _to_float(vals[34]),
                "volume": _to_float(vals[6]),
                "amount": (_to_float(vals[37]) * 10000) if _to_float(vals[37]) is not None else None,
                "change_pct": (_to_float(vals[32]) / 100.0) if _to_float(vals[32]) is not None else None,
                "change_amount": _to_float(vals[31]),
                "amplitude": None,
                "turnover_rate": None,
                "timestamp": ts or int(time.time() * 1000),
                "session": None,
            })
        return rows

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        if dataset == "realtime":
            rows = self.get_realtime()
        elif dataset == "daily":
            df = self.get_daily(symbols or ["600519.SH"])
            rows = df.to_dicts()[:5]
        elif dataset == "minute":
            df = self.get_minute(symbols or ["600519.SH"])
            rows = df.to_dicts()[:5]
        elif dataset == "valuation":
            rows = self.get_valuation(symbols or ["600519.SH", "000001.SZ"])
        else:
            raise ValueError(f"tencent_quote 不支持数据集: {dataset}")
        head = rows[:5] if isinstance(rows, list) else rows
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": len(rows) if isinstance(rows, list) else len(head),
            "columns": list(head[0].keys()) if head else [],
            "preview": head,
        }

    # ==================== valuation (估值, 从实时快照字段派生) ====================

    def get_valuation(self, symbols: list[str]) -> list[dict]:
        """估值快照：PE-TTM(39)/PE静(52)/PB(46)/流通市值(44)/总市值(45)，取自实时报价字段。"""
        client = self._get_client()
        out: list[dict] = []
        for chunk in chunked(symbols or [], _BATCH):
            codes = [_tencent_code(s) for s in chunk]
            try:
                r = client.get(_BASE + ",".join(codes), headers=_HEADERS)
                text = r.content.decode("gbk", errors="ignore")
                for line in text.split(";"):
                    line = line.strip()
                    if "=" not in line or '"' not in line:
                        continue
                    key = line.split("=")[0].strip()
                    key = key.rsplit("_", 1)[-1] if "_" in key else key
                    vals = line.split('"', 1)[1].split('"', 1)[0].split("~")
                    if len(vals) < 53:
                        continue
                    code6 = "".join(ch for ch in key[2:] if ch.isdigit()).zfill(6)
                    suffix = {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(key[:2], "SZ")
                    out.append({
                        "symbol": f"{code6}.{suffix}",
                        "name": vals[1],
                        "pe_ttm": _to_float(vals[39]),
                        "pe_static": _to_float(vals[52]),
                        "pb_mrq": _to_float(vals[46]),
                        "float_mv": (_to_float(vals[44]) or 0) * 1e8 if _to_float(vals[44]) else None,
                        "total_mv": (_to_float(vals[45]) or 0) * 1e8 if _to_float(vals[45]) else None,
                        "date": datetime.now(_TZ).strftime("%Y-%m-%d"),
                    })
            except Exception as e:  # noqa: BLE001
                logger.warning("tencent valuation chunk failed: %s", e)
            time.sleep(0.05)
        return out

    # ==================== daily / minute (按上游扩容) ====================

    def get_daily(
        self,
        symbols: list[str],
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        asset_type: str = "stock",  # noqa: ARG002
        on_chunk_done=None,
    ) -> pl.DataFrame:
        """前复权日K（fqkline）。

        腾讯行序 [date, open, close, high, low, volume(手)]，无成交额字段（置 None）；
        单次窗口 ≤320 根。
        """
        client = self._get_client()
        frames: list[pl.DataFrame] = []
        syms = symbols or []
        for i, symbol in enumerate(syms):
            code = _tencent_code(symbol)
            start = start_time.strftime("%Y-%m-%d") if start_time else ""
            end = end_time.strftime("%Y-%m-%d") if end_time else ""
            try:
                r = client.get(
                    _FQKLINE,
                    params={"param": f"{code},day,{start},{end},320,qfq"},
                    headers={**_HEADERS, "Referer": "https://gu.qq.com/"},
                )
                node = ((r.json().get("data") or {}).get(code) or {})
                bars = node.get("qfqday") or node.get("day") or []
                recs = []
                for bar in bars:
                    if not isinstance(bar, list) or len(bar) < 6:
                        continue
                    recs.append({
                        "symbol": symbol,
                        "date": str(bar[0]),
                        "open": _to_float(bar[1]),
                        "high": _to_float(bar[3]),
                        "low": _to_float(bar[4]),
                        "close": _to_float(bar[2]),
                        "volume": _to_float(bar[5]),
                        "amount": None,
                    })
                if recs:
                    frames.append(pl.DataFrame(recs))
            except Exception as e:  # noqa: BLE001
                logger.warning("tencent daily %s failed: %s", symbol, e)
            time.sleep(0.05)
            if on_chunk_done:
                on_chunk_done(i + 1, len(syms))
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    def get_minute(
        self,
        symbols: list[str],
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        asset_type: str = "stock",  # noqa: ARG002
        freq: str = "5m",
        on_chunk_done=None,
    ) -> pl.DataFrame:
        """分钟K（mkline）：m1/m5/m15/m30/m60，≤320 根。行序同日K，datetime 为北京时间墙钟。"""
        client = self._get_client()
        m = {"1m": "m1", "5m": "m5", "15m": "m15", "30m": "m30", "60m": "m60", "1": "m1", "5": "m5"}.get(str(freq), "m5")
        # 调用方可能传 tz-aware 窗口; mkline 条线为 naive 北京墙钟, 统一去 tz 再比较
        # (与 tdx_local.get_minute 同语义, 否则 polars 比较 naive vs UTC 直接抛错)
        if start_time is not None and start_time.tzinfo is not None:
            start_time = start_time.astimezone(CN_TZ).replace(tzinfo=None)
        if end_time is not None and end_time.tzinfo is not None:
            end_time = end_time.astimezone(CN_TZ).replace(tzinfo=None)
        frames: list[pl.DataFrame] = []
        syms = symbols or []
        for i, symbol in enumerate(syms):
            code = _tencent_code(symbol)
            try:
                r = client.get(
                    _MKLINE,
                    params={"param": f"{code},{m},,320"},
                    headers={**_HEADERS, "Referer": "https://gu.qq.com/"},
                )
                bars = ((r.json().get("data") or {}).get(code) or {}).get(m) or []
                recs = []
                for bar in bars:
                    if not isinstance(bar, list) or len(bar) < 6:
                        continue
                    try:
                        dt = datetime.strptime(str(bar[0]).strip(), "%Y%m%d%H%M")
                    except ValueError:
                        continue
                    recs.append({
                        "symbol": symbol,
                        "datetime": dt,
                        "open": _to_float(bar[1]),
                        "high": _to_float(bar[3]),
                        "low": _to_float(bar[4]),
                        "close": _to_float(bar[2]),
                        "volume": _to_float(bar[5]),
                        "amount": None,
                    })
                if recs:
                    df = pl.DataFrame(recs)
                    if start_time is not None:
                        df = df.filter(pl.col("datetime") >= start_time)
                    if end_time is not None:
                        df = df.filter(pl.col("datetime") <= end_time)
                    if not df.is_empty():
                        frames.append(df)
            except Exception as e:  # noqa: BLE001
                logger.warning("tencent minute %s failed: %s", symbol, e)
            time.sleep(0.05)
            if on_chunk_done:
                on_chunk_done(i + 1, len(syms))
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

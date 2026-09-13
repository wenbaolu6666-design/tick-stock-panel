"""Local TDX vipdoc built-in plugin: reads .day daily bars + .lc1/.lc5 minute bars from disk.

Uses the downloaded 通达信 data packs (hsjday.zip -> E:\\tdx\\vipdoc). No
network, no TDX server, no TQ client required for daily bars. Volume is
converted from shares to lots to match TSP A-share contract.

分钟线 (.lc1 一分钟 / .lc5 五分钟): 本地只到上一交易日 (通达信盘后下载约 16:30 后
才含当天)。记录 32 字节: u32 打包(高16=分钟, 低16=日组号) + f32 OHLC + f32 额
+ u32 量(股) + u32 保留; 日组自解码日期 ((年-2004)*2048+月*100+日), 异常组退回 .day 序列锚定。
"""
from __future__ import annotations

import logging
import os
import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("daily", "minute")
_ROOT = Path(os.getenv("TDX_DATA_DIR", r"E:\tdx\vipdoc"))
_REC = struct.Struct("<IIIIIfII")
_REC_SIZE = 32
_LC_REC = struct.Struct("<IfffffII")  # pack, open, high, low, close, amount, volume(股), 保留
_LC_SIZE = 32


def _f(value) -> float | None:
    return float(value) if value is not None else None


def _path_for(symbol: str) -> Path | None:
    s = symbol.upper()
    code = "".join(ch for ch in s if ch.isdigit()).zfill(6)
    if s.endswith(".SH") or code.startswith(("60", "68", "90", "5")):
        return _ROOT / "sh" / "lday" / f"sh{code}.day"
    if s.endswith(".BJ") or code.startswith(("4", "8", "92")):
        return _ROOT / "bj" / "lday" / f"bj{code}.day"
    return _ROOT / "sz" / "lday" / f"sz{code}.day"


def _read_day(path: Path, symbol: str) -> list[dict]:
    data = path.read_bytes()
    rows: list[dict] = []
    for i in range(0, len(data) // _REC_SIZE):
        date, op, hi, lo, cl, amt, vol, _rsv = _REC.unpack_from(data, i * _REC_SIZE)
        if date <= 0:
            continue
        rows.append({
            "symbol": symbol,
            "date": date,
            "open": op / 100.0,
            "high": hi / 100.0,
            "low": lo / 100.0,
            "close": cl / 100.0,
            "volume": int(vol // 100),  # shares -> lots
            "amount": amt,
        })
    return rows


def _decode_day_group(dg: int) -> int | None:
    """通达信分钟 day_group 自解码: (年-2004)*2048 + 月*100 + 日.

    实测钉死 (6标的×98组 价格区间唯一匹配全对): 45472→20260416, 45960→20260904。
    返回 yyyymmdd 整数; 解出不合理日期返回 None (调用方退回 .day 锚定)。
    """
    if not (0 < dg < 200_000):
        return None
    year_off, md = divmod(dg, 2048)
    year = 2004 + year_off
    month, day = divmod(md, 100)
    if not (1 <= month <= 12 and 1 <= day <= 31 and 2004 <= year <= 2099):
        return None
    return year * 10000 + month * 100 + day



def availability() -> tuple[bool, str]:
    for sub in ("sh", "sz", "bj"):
        p = _ROOT / sub / "lday"
        if p.is_dir() and any(p.glob("*.day")):
            return True, f"ok ({_ROOT})"
    return False, f"未找到通达信日线数据目录 {_ROOT}（请先解压 hsjday.zip 到 D:\\new_tdx_mock\\vipdoc）"


@dataclass
class _TdxLocalConfig:
    name: str = "tdx_local"
    display_name: str = "通达信本地（日线/分钟线）"
    datasets: dict = field(default_factory=lambda: dict.fromkeys(_DATASETS))
    path: None = None
    builtin: bool = True


class TdxLocalProvider:
    name = "tdx_local"
    builtin = True

    def __init__(self) -> None:
        self.config = _TdxLocalConfig()

    def close(self) -> None:
        pass

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
            path = _path_for(symbol)
            try:
                if path is None or not path.exists():
                    rows = []
                else:
                    rows = _read_day(path, symbol)
                df = self._daily_df(rows)
                if start_time:
                    df = df.filter(pl.col("date") >= start_time.date())
                if end_time:
                    df = df.filter(pl.col("date") <= end_time.date())
            except Exception as e:  # noqa: BLE001
                logger.warning("tdx_local daily %s failed: %s", symbol, e)
                df = pl.DataFrame()
            if not df.is_empty():
                frames.append(df)
            if on_chunk_done:
                on_chunk_done(i + 1, len(symbols))
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    @staticmethod
    def _daily_df(rows: list[dict]) -> pl.DataFrame:
        if not rows:
            return pl.DataFrame()
        df = pl.DataFrame(rows).with_columns(
            pl.col("date").cast(pl.Int32).cast(pl.Utf8).str.to_date("%Y%m%d", strict=False),
        )
        keep = ["symbol", "date", "open", "high", "low", "close", "volume", "amount"]
        return df.select([c for c in keep if c in df.columns]).drop_nulls(subset=["date"])

    def get_instruments(self, asset_type: str = "stock") -> list[dict]:
        if asset_type != "stock":
            return []
        out: list[dict] = []
        for sub, suffix in (("sh", "SH"), ("sz", "SZ"), ("bj", "BJ")):
            p = _ROOT / sub / "lday"
            if not p.is_dir():
                continue
            for f in p.glob(f"{sub}*.day"):
                code = f.stem[2:].zfill(6)
                out.append({
                    "symbol": f"{code}.{suffix}",
                    "name": "",
                    "code": code,
                    "exchange": suffix,
                    "region": "CN",
                    "type": "stock",
                    "ext": {},
                })
        return out

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        if dataset == "minute":
            df = self.get_minute(symbols or ["600519.SH"])
        else:
            df = self.get_daily(symbols or ["600519.SH"])
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns,
            "preview": df.head(5).to_dicts() if not df.is_empty() else [],
        }

    # ---- minute (.lc1 一分钟 / .lc5 五分钟, 本地 vipdoc) ----

    def _lc_path(self, symbol: str, lc_kind: str) -> Path | None:
        s = symbol.upper()
        code = "".join(ch for ch in s if ch.isdigit()).zfill(6)
        if s.endswith(".SH") or code.startswith(("60", "68", "90", "5")):
            sub = "sh"
        elif s.endswith(".BJ") or code.startswith(("4", "8", "92")):
            sub = "bj"
        else:
            sub = "sz"
        folder = "minline" if lc_kind == "lc1" else "fzline"
        p = _ROOT / sub / folder / f"{sub}{code}.{lc_kind}"
        return p if p.exists() else None

    def get_minute(
        self,
        symbols: list[str],
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        asset_type: str = "stock",  # noqa: ARG002
        freq: str = "5m",
        on_chunk_done=None,
    ) -> pl.DataFrame:
        """本地通达信分钟线。

        freq='1m' 读 minline/.lc1 (240 根/日); 其余读 fzline/.lc5 (原生 5 分钟,
        48 根/日, 约 98 个交易日)。本地只到上一交易日, 当天需等盘后下载(~16:30)。
        volume 股→手; 日期优先 day_group 自解码 (实测钉死), 失败退回 .day 倒序锚定。
        """
        lc_kind = "lc1" if str(freq) in ("1m", "1") else "lc5"
        per_day = 240 if lc_kind == "lc1" else 48
        frames: list[pl.DataFrame] = []
        syms = symbols or []
        for i, symbol in enumerate(syms):
            try:
                lc_path = self._lc_path(symbol, lc_kind)
                day_path = _path_for(symbol)
                if lc_path is None or day_path is None or not day_path.exists():
                    continue
                # 日组→日期: 优先自解码 ((y-2004)*2048+m*100+d, 实测钉死);
                # 自解码失败的组退回 .day 收盘序列倒序锚定 (旧逻辑, 兼容异常编码)。
                daily = _read_day(day_path, symbol)
                daily_dates = [r["date"] for r in daily]
                if not daily_dates:
                    continue

                data = lc_path.read_bytes()
                groups: dict[int, list[tuple]] = {}
                for j in range(0, len(data) // _LC_SIZE):
                    pack, o, hi, lo, cl, amt, vol, _rsv = _LC_REC.unpack_from(data, j * _LC_SIZE)
                    minutes, day_group = pack >> 16, pack & 0xFFFF
                    groups.setdefault(day_group, []).append((minutes, o, hi, lo, cl, amt, vol))

                recs: list[dict] = []
                day_idx = len(daily_dates) - 1
                for day_group in sorted(groups, reverse=True):
                    date_int = _decode_day_group(day_group)
                    if date_int is None:
                        # 自解码失败 → 锚定回退 (与旧行为一致)
                        if day_idx < 0:
                            break
                        date_int = daily_dates[day_idx]
                        day_idx -= 1
                    else:
                        # 自解码成功时同步消费锚定指针, 保持两组路径语义一致
                        if day_idx >= 0 and daily_dates[day_idx] == date_int:
                            day_idx -= 1
                    ds = str(date_int)
                    ymd = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"
                    yy, mo, dd2 = int(ymd[:4]), int(ymd[5:7]), int(ymd[8:10])
                    for minutes, o, hi, lo, cl, amt, vol in sorted(groups[day_group]):
                        hh, mm = divmod(minutes, 60)
                        recs.append({
                            "symbol": symbol,
                            "datetime": datetime(yy, mo, dd2, hh, mm),
                            "open": o,
                            "high": hi,
                            "low": lo,
                            "close": cl,
                            "volume": int(vol // 100),  # 股 → 手
                            "amount": amt,
                        })
                    day_idx -= 1
                df = pl.DataFrame(recs) if recs else pl.DataFrame()
                if not df.is_empty():
                    # 调用方(如 fetch_minute_single)可能传 tz-aware 窗口; 本地条线为 naive 北京墙钟, 统一去 tz 再比较
                    s_naive = start_time.replace(tzinfo=None) if start_time is not None and start_time.tzinfo is not None else start_time
                    e_naive = end_time.replace(tzinfo=None) if end_time is not None and end_time.tzinfo is not None else end_time
                    if s_naive is not None:
                        df = df.filter(pl.col("datetime") >= s_naive)
                    if e_naive is not None:
                        df = df.filter(pl.col("datetime") <= e_naive)
                    if not df.is_empty():
                        frames.append(df)
            except Exception as e:  # noqa: BLE001
                logger.warning("tdx_local minute %s failed: %s", symbol, e)
            if on_chunk_done:
                on_chunk_done(i + 1, len(syms))
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

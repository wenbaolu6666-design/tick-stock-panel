"""Local TDX vipdoc built-in plugin: reads .day daily bars + .lc1/.lc5 minute bars from disk.

Uses the local 通达信 client vipdoc (D:\\new_tdx_hd_test\\vipdoc). No
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

import numpy as np
import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("daily", "minute")
_ROOT = Path(os.getenv("TDX_DATA_DIR", r"D:\new_tdx_hd_test\vipdoc"))
_REC = struct.Struct("<IIIIIfII")
_REC_SIZE = 32
_LC_REC = struct.Struct("<IfffffII")  # pack, open, high, low, close, amount, volume(股), 保留
_LC_SIZE = 32
# 向量化 .lc1 解析(等价 _LC_REC): 整文件一次 frombuffer, 37万条记录无 Python 逐条循环
_DAY_DT = np.dtype([("date", "<u4"), ("o", "<u4"), ("h", "<u4"), ("l", "<u4"),
                    ("c", "<u4"), ("amt", "<f4"), ("vol", "<u4"), ("rsv", "<u4")])
_LC_DT = np.dtype([("pack", "<u4"), ("o", "<f4"), ("h", "<f4"), ("l", "<f4"),
                   ("c", "<f4"), ("amt", "<f4"), ("vol", "<u4"), ("rsv", "<u4")])


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
    n = len(data) // _REC_SIZE
    if n == 0:
        return []
    # 向量化: numpy 一次解出全部记录, 8千条/只不再逐条 Python unpack
    arr = np.frombuffer(data, dtype=_DAY_DT, count=n)
    dates = arr["date"]
    valid = dates > 0
    idxs = np.nonzero(valid)[0]
    rows: list[dict] = []
    o = arr["o"].tolist(); h = arr["h"].tolist(); l = arr["l"].tolist()
    c = arr["c"].tolist(); v = arr["vol"].tolist(); a = arr["amt"].tolist()
    for i in idxs.tolist():
        rows.append({
            "symbol": symbol,
            "date": int(dates[i]),
            "open": o[i] / 100.0,
            "high": h[i] / 100.0,
            "low": l[i] / 100.0,
            "close": c[i] / 100.0,
            "volume": int(v[i] // 100),  # shares -> lots
            "amount": a[i],
        })
    return rows


def _read_day_df(path: Path, symbol: str) -> pl.DataFrame | None:
    """向量化读取 .day → DataFrame(symbol/date/ohlc/volume/amount), date 为 date 类型。"""
    if path is None or not path.exists():
        return None
    data = path.read_bytes()
    n = len(data) // _REC_SIZE
    if n == 0:
        return None
    arr = np.frombuffer(data, dtype=_DAY_DT, count=n)
    df = pl.DataFrame({
        "date": arr["date"].astype(np.int64),
        "open": arr["o"].astype(np.float64) / 100.0,
        "high": arr["h"].astype(np.float64) / 100.0,
        "low": arr["l"].astype(np.float64) / 100.0,
        "close": arr["c"].astype(np.float64) / 100.0,
        "volume": (arr["vol"] // 100).astype(np.int64),   # shares -> lots
        "amount": arr["amt"].astype(np.float64),
    }).filter(pl.col("date") > 0)
    df = df.with_columns(
        pl.col("date").cast(pl.Int32).cast(pl.Utf8).str.to_date("%Y%m%d", strict=False),
    )
    return df.with_columns(pl.lit(symbol).alias("symbol"))


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
    return False, f"未找到通达信日线数据目录 {_ROOT}（请先解压 hsjday.zip 到 D:\\new_tdx_hd_test\\vipdoc）"


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
                df = _read_day_df(path, symbol)
                if df is None or df.is_empty():
                    continue
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
                # vipdoc 同时包含指数/板块(.day), 只有真实股票代码才进入股票池。
                if suffix == "SH" and not code.startswith(("600", "601", "603", "605", "688", "689")):
                    continue
                if suffix == "SZ" and not code.startswith(("000", "001", "002", "003", "300", "301")):
                    continue
                if suffix == "BJ" and not code.startswith(("4", "8", "92")):
                    continue
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
                n = len(data) // _LC_SIZE
                if n == 0:
                    continue
                # 向量化解析(37万条记录无 Python 逐条循环): numpy 一次解出
                # minutes/day_group/OHLC/amt/vol, 日期向量解码, 窗口先过滤,
                # Python 只构造窗口内记录的 datetime(数百条)。
                arr = np.frombuffer(data, dtype=_LC_DT, count=n)
                pack = arr["pack"].astype(np.uint64)
                minutes = (pack >> np.uint64(16)).astype(np.int64)
                day_groups = (pack & np.uint64(0xFFFF)).astype(np.int64)
                y_off = day_groups // 2048
                md = day_groups % 2048
                mon = md // 100
                dd = md % 100
                valid = (mon >= 1) & (mon <= 12) & (dd >= 1) & (dd <= 31)
                ymds = (2004 + y_off) * 10000 + mon * 100 + dd

                # 窗口先在日期层粗过滤(start/end 为 naive 北京墙钟 → ymd_int)
                ymd_lo, ymd_hi = 0, 99999999
                s_naive = start_time.replace(tzinfo=None) if start_time is not None and start_time.tzinfo is not None else start_time
                e_naive = end_time.replace(tzinfo=None) if end_time is not None and end_time.tzinfo is not None else end_time
                if s_naive is not None:
                    ymd_lo = s_naive.year * 10000 + s_naive.month * 100 + s_naive.day
                if e_naive is not None:
                    ymd_hi = e_naive.year * 10000 + e_naive.month * 100 + e_naive.day
                in_window = valid & (ymds >= ymd_lo) & (ymds <= ymd_hi)
                # 精确到时刻(与旧 datetime 过滤语义一致): 首日/末日的分钟边界
                if s_naive is not None:
                    smin = s_naive.hour * 60 + s_naive.minute
                    in_window &= (ymds > ymd_lo) | (minutes >= smin)
                if e_naive is not None:
                    emin = e_naive.hour * 60 + e_naive.minute
                    in_window &= (ymds < ymd_hi) | (minutes <= emin)
                idxs = np.nonzero(in_window)[0]
                if idxs.size == 0:
                    continue

                # 无效 day_group(罕见): 退回 .day 序列倒序锚定(旧行为)
                anchor: dict[int, int] = {}
                if not valid.all():
                    inv_groups = sorted({int(g) for g in day_groups[~valid]}, reverse=True)
                    day_idx = len(daily_dates) - 1
                    for g in inv_groups:
                        if day_idx < 0:
                            break
                        anchor[g] = daily_dates[day_idx]
                        day_idx -= 1

                o_list = arr["o"].tolist()
                h_list = arr["h"].tolist()
                l_list = arr["l"].tolist()
                c_list = arr["c"].tolist()
                amt_list = arr["amt"].tolist()
                vol_list = arr["vol"].tolist()
                recs: list[dict] = []
                for i in idxs.tolist():
                    g = int(day_groups[i])
                    if bool(valid[i]):
                        ymd = int(ymds[i])
                    elif g in anchor:
                        ymd = anchor[g]
                    else:
                        continue
                    mnt = int(minutes[i])
                    hh, mm = divmod(mnt, 60)
                    if hh > 23 or mm > 59:
                        continue
                    ds = f"{ymd:08d}"
                    recs.append({
                        "symbol": symbol,
                        "datetime": datetime(int(ds[:4]), int(ds[4:6]), int(ds[6:8]), hh, mm),
                        "open": o_list[i],
                        "high": h_list[i],
                        "low": l_list[i],
                        "close": c_list[i],
                        "volume": int(vol_list[i] // 100),  # 股 → 手
                        "amount": amt_list[i],
                    })
                if not recs:
                    continue
                frames.append(pl.DataFrame(recs).sort("symbol", "datetime"))
            except Exception as e:  # noqa: BLE001
                logger.warning("tdx_local minute %s failed: %s", symbol, e)
            if on_chunk_done:
                on_chunk_done(i + 1, len(syms))
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

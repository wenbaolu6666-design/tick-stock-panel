"""TdxQuant (official 通达信量化平台) built-in plugin via local HTTP 17709.

Requires a running 通达信金融终端/量化模拟 with TQ enabled.
P0: financial (income/balance_sheet/cash_flow/metrics/shares) via get_financial_data + get_gb_info
P1: get_instruments (get_stock_list), get_adj_factors (ForwardFactor), get_realtime (get_market_snapshot)
"""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime

import httpx
import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("daily", "minute", "full_minute", "financial", "depth5", "valuation", "kzz", "ipo", "adj_factor")
_URL = "http://127.0.0.1:17709/"
_MINUTE_CANONICAL = ["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"]

# ---------------------------------------------------------------------------
# TQ FN field -> TSP canonical financial column mapping
# ---------------------------------------------------------------------------
_FN_INCOME = {
    "FN230": "revenue",
    "FN231": "operating_profit",
    "FN134": "net_income",
    "FN232": "net_income_attributable",
    "FN1": "basic_eps",
}
_FN_BALANCE = {
    "FN40": "total_assets",
    "FN21": "total_current_assets",
    "FN39": "total_non_current_assets",
    "FN8": "cash_and_equivalents",
    "FN11": "accounts_receivable",
    "FN17": "inventory",
    "FN63": "total_liabilities",
    "FN54": "total_current_liabilities",
    "FN72": "total_equity",
}
_FN_CASHFLOW = {
    "FN107": "net_operating_cash_flow",
    "FN119": "net_investing_cash_flow",
    "FN128": "net_financing_cash_flow",
    "FN114": "capex",
    "FN131": "net_cash_change",
}
_FN_METRICS = {
    "FN1": "eps",
    "FN4": "bps",
    "FN6": "roe",
    "FN202": "gross_margin",
    "FN201": "net_margin",
    "FN183": "revenue_yoy",
    "FN184": "net_income_yoy",
    "FN210": "debt_to_asset_ratio",
    "FN230": "revenue",
    "FN134": "net_income",
    "FN40": "total_assets",
    "FN72": "total_equity",
}
_FIN_TABLE_FIELDS = {
    "income": _FN_INCOME,
    "balance_sheet": _FN_BALANCE,
    "cash_flow": _FN_CASHFLOW,
    "metrics": _FN_METRICS,
}
_FIN_TABLES = frozenset(_FIN_TABLE_FIELDS) | {"shares"}


def _f(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _lots(value) -> float | None:
    v = _f(value)
    return int(v // 100.0) if v is not None else None


def _dt_parse(date_s: str, time_s: str | None) -> datetime | None:
    if not date_s:
        return None
    t = time_s or "000000"
    try:
        if len(t) == 4:
            return datetime.strptime(f"{date_s} {t}", "%Y%m%d %H%M")
        return datetime.strptime(f"{date_s} {t}", "%Y%m%d %H%M%S")
    except ValueError:
        return None


_TDX_EXE_KNOWN = Path(r"D:\new_tdx_hd_test\TdxW.exe")
_ENSURE_ATTEMPTED = False


def _tq_alive() -> bool:
    try:
        _call("get_market_data", {
            "stock_list": ["000001.SZ"],
            "count": 1,
            "dividend_type": "none",
            "period": "1d",
        }, timeout=2.0)
        return True
    except Exception:  # noqa: BLE001
        return False


def _tdx_process_running() -> bool:
    try:
        # bytes 匹配: tasklist 输出为 ANSI 代码页编码, 而 PYTHONUTF8=1 下 text=True
        # 会按 UTF-8 严格解码导致 reader 线程崩溃、stdout 为空、恒判"未运行"。
        # 映像名在本机 tasklist 输出为小写 tdxw.exe, 统一小写后匹配, 与代码页/大小写无关。
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq TdxW.exe"],
            capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,  # 后端分离运行,避免 tasklist 闪控制台窗
        )
        return b"tdxw" in (out.stdout or b"").lower()
    except Exception:  # noqa: BLE001
        return False


def _find_tdx_exe() -> Path | None:
    if _TDX_EXE_KNOWN.exists():
        return _TDX_EXE_KNOWN
    try:
        import winreg
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0, winreg.KEY_READ | view) as root:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(root, i)
                        i += 1
                    except OSError:
                        break
                    if "通达信" not in sub:
                        continue
                    try:
                        with winreg.OpenKey(root, sub) as k:
                            loc = winreg.QueryValueEx(k, "InstallLocation")[0]
                    except OSError:
                        continue
                    cand = Path(loc) / "TdxW.exe"
                    if cand.exists():
                        return cand
    except Exception:  # noqa: BLE001
        pass
    return None


def _settle_tdx_windows(pid: int, timeout_seconds: float = 300.0) -> None:
    """TdxW 前台初始化完成后收尾：最小化主窗口、关闭广告/盘后下载弹窗。

    保守策略：
    - 主窗口（标题含"通达信金融终端"）→ SW_MINIMIZE，绝不发 WM_CLOSE；
    - class=#32770 的可见对话框（广告/盘后数据下载等弹窗）→ WM_CLOSE 关闭；
    - 未识别的窗口一律不动。
    """
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # noqa: BLE001
        return
    user32 = ctypes.windll.user32
    WM_CLOSE = 0x0010
    SW_MINIMIZE = 6
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _scan():
        rows: list[tuple[int, str, str]] = []
        def cb(hwnd, lparam):
            wpid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            if wpid.value == pid and user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                cls = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls, 256)
                rows.append((hwnd, buf.value, cls.value))
            return True
        user32.EnumWindows(proto(cb), 0)
        return rows

    minimized = False
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        closed_any = False
        for hwnd, title, cls in _scan():
            if "通达信金融终端" in title:
                if not user32.IsIconic(hwnd):
                    user32.ShowWindowAsync(hwnd, SW_MINIMIZE)
                minimized = True
            elif cls == "#32770" and title and "通达信金融终端" not in title:
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                closed_any = True
        if minimized and not closed_any:
            logger.info("tdx_quant: TdxW 窗口已收尾（主窗口最小化、弹窗已关闭）")
            return
        time.sleep(5)
    if minimized:
        logger.info("tdx_quant: TdxW 主窗口已最小化（弹窗未全部关闭）")

def _kick_login_automation() -> None:
    """后台启动登录自动化 v7（autostart-tdx-login.ps1）：自动点登录/关风险揭示
    弹窗/关公告，网关上线后由 _settle_tdx_windows 最小化主窗。

    与开机自启链 (autostart-all.ps1 step1b) 共用同一脚本与互斥锁，重复启动
    自动让位；前台拉起路径原先不带它，登录窗会停在前台等人工——此处补齐，
    使后端自愈拉起与开机自启行为一致。"""
    ps1 = Path(__file__).resolve().parents[4] / "autostart-tdx-login.ps1"
    if not ps1.exists():
        return
    for shell in ("pwsh", "powershell"):
        try:
            subprocess.Popen(
                [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
                cwd=str(ps1.parent), creationflags=0x00000008, close_fds=True,
            )
            return
        except FileNotFoundError:
            continue
        except Exception:  # noqa: BLE001
            return


def ensure_tq_running(wait_seconds: float = 90.0) -> bool:
    """TQ 网关不可用时前台拉起 TdxW（可见窗口完成自动登录/初始化），
    网关上线后自动把窗口最小化。每进程一次；pytest 跳过。
    TdxW 已在运行但网关死亡（僵尸）时不重复拉起——重启客户端需用户操作。
    """
    global _ENSURE_ATTEMPTED
    if _tq_alive():
        return True
    if "pytest" in sys.modules or _ENSURE_ATTEMPTED:
        return False
    _ENSURE_ATTEMPTED = True
    if _tdx_process_running():
        return False
    # Race guard: autostart-all.ps1 may have JUST sent Start-Process for TdxW,
    # but the process has not appeared in tasklist yet. Wait and recheck before
    # double-launching (which would trigger the conflict dialog).
    for _ in range(3):
        time.sleep(4)
        if _tq_alive():
            return True
        if _tdx_process_running():
            return False
    exe = _find_tdx_exe()
    if exe is None:
        return False
    launched_pid = 0
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 1  # SW_SHOWNORMAL：前台启动，窗口渲染 + 自动登录后 TQ 引擎才初始化
        proc = subprocess.Popen(
            [str(exe)], cwd=str(exe.parent),
            creationflags=0x00000008,  # DETACHED_PROCESS
            startupinfo=si, close_fds=True,
        )
        launched_pid = proc.pid
        _kick_login_automation()
    except Exception:  # noqa: BLE001
        return False

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        time.sleep(2)
        if _tq_alive():
            _settle_tdx_windows(launched_pid)
            return True

    # 网关迟到（客户端仍在登录/初始化）：后台继续探测，上线即最小化窗口并
    # 自动重载数据源完成插件注册，全程无需手动操作。
    def _late_register() -> None:
        deadline2 = time.time() + 900
        while time.time() < deadline2:
            time.sleep(5)
            if not _tq_alive():
                continue
            _settle_tdx_windows(launched_pid)
            try:
                from app.data_providers.custom import loader as _loader
                _loader.load_all()
                logger.info("tdx_quant: TQ 网关迟到上线，已自动重载数据源完成插件注册")
            except Exception:  # noqa: BLE001
                logger.exception("tdx_quant: late register reload failed")
            return

    threading.Thread(target=_late_register, daemon=True, name="tq-late-register").start()
    return False

def availability() -> tuple[bool, str]:
    if _tq_alive():
        return True, "ok"
    if ensure_tq_running(wait_seconds=40.0):
        return True, "ok (已自动拉起通达信)"
    if _tdx_process_running():
        return False, "TdxW 已运行但 TQ 网关无响应，请重启通达信客户端后在数据源页重载"
    return False, "未检测到通达信客户端，已尝试自动拉起未成功，请手动启动通达信后重载"

def _call(method: str, params: dict, timeout: float = 30.0) -> dict:
    payload = {"id": 1, "method": method, "params": params}
    r = httpx.post(_URL, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    result = data.get("result") or {}
    if str(result.get("ErrorId", "0")) != "0":
        raise RuntimeError(f"TdxQuant {method} error: {result.get('ErrorId')}")
    return result


@dataclass
class _TdxConfig:
    name: str = "tdx_quant"
    display_name: str = "通达信量化平台"
    datasets: dict = field(default_factory=lambda: dict.fromkeys(_DATASETS))
    path: None = None
    builtin: bool = True


class TdxQuantProvider:
    name = "tdx_quant"
    builtin = True
    minute_history_days = 30

    def __init__(self) -> None:
        self.config = _TdxConfig()

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
            try:
                params = {
                    "stock_list": [symbol],
                    "count": -1,
                    "dividend_type": "none",
                    "period": "1d",
                    "start_time": (start_time or datetime(1990, 1, 1)).strftime("%Y%m%d"),
                    "end_time": (end_time or datetime.now()).strftime("%Y%m%d"),
                }
                result = _call("get_market_data", params)
                rows = self._parse_kline(result, symbol)
                df = self._daily_df(rows)
                if start_time:
                    df = df.filter(pl.col("date") >= start_time.date())
                if end_time:
                    df = df.filter(pl.col("date") <= end_time.date())
            except Exception as e:  # noqa: BLE001
                logger.warning("tdx_quant daily %s failed: %s", symbol, e)
                df = pl.DataFrame()
            if not df.is_empty():
                frames.append(df)
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
        period = "1m" if str(freq).lower() in ("1m", "1min", "minute") else freq
        frames: list[pl.DataFrame] = []
        for i, symbol in enumerate(symbols):
            try:
                params = {
                    "stock_list": [symbol],
                    "count": 10000,
                    "dividend_type": "none",
                    "period": period,
                    "fill_data": False,
                }
                result = _call("get_market_data", params)
                rows = self._parse_kline(result, symbol)
                df = self._minute_df(rows)
                if start_time:
                    df = df.filter(pl.col("datetime") >= start_time)
                if end_time:
                    df = df.filter(pl.col("datetime") <= end_time)
            except Exception as e:  # noqa: BLE001
                logger.warning("tdx_quant minute %s failed: %s", symbol, e)
                df = pl.DataFrame()
            if not df.is_empty():
                frames.append(df)
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
                result = _call("get_market_data", {
                    "stock_list": [symbol],
                    "count": max(240, int(count)),
                    "dividend_type": "none",
                    "period": "1m",
                    "fill_data": False,
                })
                rows = self._parse_kline(result, symbol)
                df = self._minute_df(rows)
                if not df.is_empty():
                    latest = df["datetime"].max()
                    df = df.filter(pl.col("datetime").dt.date() == latest.date())
            except Exception as e:  # noqa: BLE001
                logger.warning("tdx_quant intraday %s failed: %s", symbol, e)
                df = pl.DataFrame()
            if not df.is_empty():
                frames.append(df)
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    # ==================== P0: financial ====================

    def get_financials(
        self,
        table: str,
        symbols: list[str],
        latest_only: bool = True,
    ) -> pl.DataFrame:
        if not symbols or table not in _FIN_TABLES:
            return pl.DataFrame()
        if table == "shares":
            return self._fetch_shares(symbols, latest_only)
        return self._fetch_financial_table(table, symbols, latest_only)

    def _fetch_financial_table(self, table: str, symbols: list[str], latest_only: bool) -> pl.DataFrame:
        fn_map = _FIN_TABLE_FIELDS[table]
        field_list = list(fn_map.keys())
        out_rows: list[dict] = []
        # Gateway pages 100 stocks/call; pass the FULL list once and walk stock_page_index
        # (much faster than one call per 50-symbol chunk: ~6min vs ~25min per table).
        n_pages = max(1, (len(symbols) + 99) // 100)
        for page_index in range(n_pages + 1):  # +1 catches the last partial page
            try:
                result = _call("get_financial_data", {
                    "stock_list": symbols,
                    "table_list": field_list,
                    "report_type": "report_time",
                    "start_time": "19900101",
                    "end_time": "20991231",
                    "stock_page_index": page_index,
                }, timeout=120)
            except Exception as e:  # noqa: BLE001
                logger.warning("tdx_quant financial %s page %d failed: %s", table, page_index, e)
                break
            if page_index > 0:
                time.sleep(0.5)  # gentle pacing; gateway degrades under rapid sequential calls
            value = result.get("Value") or {}
            if not value:
                break  # page out of range
            for symbol, rec in value.items():
                if not isinstance(rec, dict):
                    continue
                tags = rec.get("tag_time") or []
                if isinstance(tags, dict):
                    tags = list(tags.values())
                if not isinstance(tags, list) or not tags:
                    continue
                anns = rec.get("announce_time") or []
                if isinstance(anns, dict):
                    anns = list(anns.values())
                n_all = len(tags)
                n = 1 if latest_only else n_all
                # values arrays stay full-length; index by ABSOLUTE position so
                # vals[i] aligns with tags[i] (slicing tags alone misaligns).
                for i in range(n_all - n, n_all):
                    row: dict = {"symbol": symbol}
                    row["period_end"] = str(int(tags[i])) if tags[i] else None
                    row["announce_date"] = str(int(anns[i])) if i < len(anns) and anns[i] else None
                    for fn_key, col_name in fn_map.items():
                        vals = rec.get(fn_key) or []
                        row[col_name] = _f(vals[i]) if isinstance(vals, list) and i < len(vals) else None
                    out_rows.append(row)
        if not out_rows:
            return pl.DataFrame()
        return pl.DataFrame(out_rows)

    def _fetch_shares(self, symbols: list[str], latest_only: bool) -> pl.DataFrame:
        # Derive from pro-data FN238 (???) / FN239 (????) via the paged call;
        # get_gb_info is ~13s/stock, infeasible for the full market.
        out_rows: list[dict] = []
        n_pages = max(1, (len(symbols) + 99) // 100)
        for page_index in range(n_pages + 1):
            try:
                result = _call("get_financial_data", {
                    "stock_list": symbols,
                    "table_list": ["FN238", "FN239"],
                    "report_type": "report_time",
                    "start_time": "19900101",
                    "end_time": "20991231",
                    "stock_page_index": page_index,
                }, timeout=120)
            except Exception as e:  # noqa: BLE001
                logger.warning("tdx_quant shares page %d failed: %s", page_index, e)
                break
            if page_index > 0:
                time.sleep(0.5)  # gentle pacing
            value = result.get("Value") or {}
            if not value:
                break
            for symbol, rec in value.items():
                if not isinstance(rec, dict):
                    continue
                dates = rec.get("tag_time") or []
                if isinstance(dates, dict):
                    dates = list(dates.values())
                if not isinstance(dates, list) or not dates:
                    continue
                n_all = len(dates)
                n = 1 if latest_only else n_all
                for i in range(n_all - n, n_all):
                    ts = _f((rec.get("FN238") or [None])[i]) if isinstance(rec.get("FN238"), list) and i < len(rec["FN238"]) else None
                    fs = _f((rec.get("FN239") or [None])[i]) if isinstance(rec.get("FN239"), list) and i < len(rec["FN239"]) else None
                    out_rows.append({
                        "symbol": symbol,
                        "trade_date": str(int(dates[i])) if dates[i] else None,
                        "total_shares": ts,
                        "float_shares": fs,
                    })
        if not out_rows:
            return pl.DataFrame()
        df = pl.DataFrame(out_rows).with_columns(
            pl.col("trade_date").str.to_date("%Y%m%d", strict=False)
        )
        return df.select(["symbol", "trade_date", "total_shares", "float_shares"]).drop_nulls(subset=["trade_date"])

    # ==================== P1: instruments ====================

    def get_instruments(self, asset_type: str = "stock") -> pl.DataFrame:
        if asset_type != "stock":
            return pl.DataFrame()
        try:
            result = _call("get_stock_list", {"market": "5", "list_type": 1}, timeout=30)
            val = result.get("Value") or []
            rows: list[dict] = []
            for item in val:
                if isinstance(item, dict):
                    code = str(item.get("Code") or "")
                    name = str(item.get("Name") or "")
                elif isinstance(item, str):
                    code = item
                    name = ""
                else:
                    continue
                if not code:
                    continue
                code6 = code.split(".")[0] if "." in code else code
                prefix = code6[:2]
                if prefix in ("60", "68", "90"):
                    exchange = "SH"
                elif prefix in ("00", "30", "20"):
                    exchange = "SZ"
                elif prefix in ("43", "83", "87", "92"):
                    exchange = "BJ"
                else:
                    exchange = ""
                rows.append({
                    "symbol": code,
                    "name": name,
                    "code": code6,
                    "exchange": exchange,
                    "asset_type": "stock",
                    "source": "tdx_quant",
                })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("tdx_quant get_instruments failed: %s", e)
            return pl.DataFrame()

    # ==================== P1: adj_factor ====================

    def get_adj_factors(
        self,
        symbols: list[str],
        start_time: datetime | None = None,  # noqa: ARG002
        end_time: datetime | None = None,
        asset_type: str = "stock",  # noqa: ARG002
        on_chunk_done=None,
    ) -> pl.DataFrame:
        if not symbols:
            return pl.DataFrame()
        rows: list[dict] = []
        for i, symbol in enumerate(symbols):
            try:
                params: dict = {
                    "stock_list": [symbol],
                    "count": -1,
                    "dividend_type": "none",
                    "period": "1d",
                }
                if end_time:
                    params["end_time"] = end_time.strftime("%Y%m%d")
                result = _call("get_market_data", params, timeout=30)
                value = (result.get("Value") or {}).get(symbol) or {}
                dates = value.get("Date") or []
                ffs = value.get("ForwardFactor") or []
                if not ffs or len(ffs) != len(dates):
                    continue
                prev_ff: float | None = None
                for j in range(len(dates)):
                    ff = _f(ffs[j])
                    if ff is None or ff == 0:
                        continue
                    if prev_ff is not None and abs(ff - prev_ff) > 1e-8:
                        rows.append({
                            "symbol": symbol,
                            "trade_date": str(dates[j]),
                            "ex_factor": prev_ff / ff,
                        })
                    prev_ff = ff
            except Exception as e:  # noqa: BLE001
                logger.warning("tdx_quant adj_factor %s failed: %s", symbol, e)
            if on_chunk_done:
                on_chunk_done(i + 1, len(symbols))
        if not rows:
            return pl.DataFrame()
        df = pl.DataFrame(rows).with_columns(
            pl.col("trade_date").str.to_date("%Y%m%d", strict=False),
            pl.col("ex_factor").cast(pl.Float64, strict=False),
        )
        return df.select(["symbol", "trade_date", "ex_factor"]).drop_nulls()

    # ==================== P1: realtime ====================

    def get_realtime(
        self,
        universes: list[str] | None = None,  # noqa: ARG002
        symbols: list[str] | None = None,
    ) -> pl.DataFrame:
        target = symbols or []
        if not target:
            return pl.DataFrame()
        out: list[dict] = []
        for symbol in target:
            try:
                result = _call("get_market_snapshot", {"stock_code": symbol}, timeout=10)
                val = result.get("Value") or {}
                if not val or str(val.get("ErrorId", "0")) != "0":
                    continue
                last = _f(val.get("Now"))
                prev = _f(val.get("LastClose"))
                if last is None or prev is None or prev == 0:
                    continue
                out.append({
                    "symbol": symbol,
                    "last_price": last,
                    "prev_close": prev,
                    "open": _f(val.get("Open")),
                    "high": _f(val.get("Max")),
                    "low": _f(val.get("Min")),
                    "volume": _f(val.get("Volume")),
                    "amount": _f(val.get("Amount")),
                    "change_pct": (last - prev) / prev,
                    "timestamp": int(time.time() * 1000),
                })
            except Exception as e:  # noqa: BLE001
                logger.warning("tdx_quant realtime %s failed: %s", symbol, e)
        return pl.DataFrame(out) if out else pl.DataFrame()

    # ==================== helpers ====================

    @staticmethod
    def _parse_kline(result: dict, symbol: str) -> list[dict]:
        value = (result.get("Value") or {}).get(symbol) or {}
        dates = value.get("Date") or []
        ff_raw = value.get("ForwardFactor") or []
        rows = []
        for i in range(len(dates)):
            rows.append({
                "symbol": symbol,
                "date": str(dates[i]),
                "time": str((value.get("Time") or [""] * len(dates))[i]) if value.get("Time") else "",
                "open": _f((value.get("Open") or [None] * len(dates))[i]),
                "high": _f((value.get("High") or [None] * len(dates))[i]),
                "low": _f((value.get("Low") or [None] * len(dates))[i]),
                "close": _f((value.get("Close") or [None] * len(dates))[i]),
                "volume": _lots((value.get("Volume") or [None] * len(dates))[i]),
                "amount": (_f((value.get("Amount") or [None] * len(dates))[i]) or 0) * 10000,
                "forward_factor": _f(ff_raw[i]) if i < len(ff_raw) else None,
            })
        return rows

    @staticmethod
    def _daily_df(rows: list[dict]) -> pl.DataFrame:
        if not rows:
            return pl.DataFrame()
        df = pl.DataFrame(rows).with_columns(
            pl.col("date").str.to_date("%Y%m%d", strict=False)
        )
        keep = ["symbol", "date", "open", "high", "low", "close", "volume", "amount"]
        df = df.select([c for c in keep if c in df.columns]).drop_nulls(subset=["date"])
        return df

    @staticmethod
    def _minute_df(rows: list[dict]) -> pl.DataFrame:
        recs = []
        for r in rows:
            dt = _dt_parse(r["date"], r.get("time"))
            if dt is None:
                continue
            recs.append({
                "symbol": r["symbol"],
                "datetime": dt,
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["volume"],
                "amount": r["amount"],
            })
        if not recs:
            return pl.DataFrame()
        df = pl.DataFrame(recs)
        keep = [c for c in _MINUTE_CANONICAL if c in df.columns]
        return df.select(keep) if "datetime" in keep else pl.DataFrame()

    # ==================== depth5 ====================

    def get_depth5(self, symbols: list[str]) -> pl.DataFrame:
        """五档盘口快照（get_market_snapshot 的 Buyp/Buyv/Sellp/Sellv）。

        返回宽表：symbol/timestamp/bid1..5_price,bid1..5_vol/ask1..5_price,ask1..5_vol。
        逐票调用（网关快照为单票接口），适合涨跌停名单等小批量场景。
        """
        rows: list[dict] = []
        for symbol in symbols:
            try:
                result = _call("get_market_snapshot", {"stock_code": symbol}, timeout=10)
                val = result.get("Value") or {}
                buyp = [_f(x) for x in (val.get("Buyp") or [])]
                buyv = [_f(x) for x in (val.get("Buyv") or [])]
                sellp = [_f(x) for x in (val.get("Sellp") or [])]
                sellv = [_f(x) for x in (val.get("Sellv") or [])]
                if not buyp and not sellp:
                    continue
                row: dict = {"symbol": symbol, "timestamp": int(time.time() * 1000)}
                for i in range(5):
                    row[f"bid{i + 1}_price"] = buyp[i] if i < len(buyp) else None
                    row[f"bid{i + 1}_vol"] = buyv[i] if i < len(buyv) else None
                    row[f"ask{i + 1}_price"] = sellp[i] if i < len(sellp) else None
                    row[f"ask{i + 1}_vol"] = sellv[i] if i < len(sellv) else None
                rows.append(row)
            except Exception as e:  # noqa: BLE001
                logger.warning("tdx_quant depth5 %s failed: %s", symbol, e)
        return pl.DataFrame(rows) if rows else pl.DataFrame()

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        symbols = symbols or ["600519.SH"]
        if dataset == "daily":
            df = self.get_daily(symbols)
        elif dataset == "minute":
            df = self.get_minute(symbols)
        elif dataset == "full_minute":
            df = self.get_intraday_batch(symbols)
        elif dataset == "financial":
            df = self.get_financials("metrics", symbols, latest_only=True)
        elif dataset == "adj_factor":
            df = self.get_adj_factors(symbols)
        elif dataset == "realtime":
            rt = self.get_realtime(symbols=symbols)
            return {"provider": self.name, "dataset": dataset, "rows": rt.height, "columns": rt.columns, "preview": rt.head(3).to_dicts()}
        elif dataset == "instruments":
            df = self.get_instruments()
        elif dataset == "valuation":
            df = self.get_valuation(symbols or [])
        elif dataset == "kzz":
            df = self.get_kzz()
        elif dataset == "ipo":
            df = self.get_ipo()
        elif dataset == "adj_factor":
            df = self.get_adj_factors(symbols or [])
        else:
            raise ValueError(f"tdx_quant 不支持数据集: {dataset}")
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns,
            "preview": df.head(5).to_dicts() if not df.is_empty() else [],
        }

    # ==================== valuation / kzz / ipo (按上游扩容) ====================

    @staticmethod
    def _flatten_value(value) -> dict:
        """TQ result.Value 兼容: dict 直接用, list 取首个 dict。"""
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            for x in value:
                if isinstance(x, dict):
                    return x
        return {}

    def get_valuation(self, symbols: list[str]) -> pl.DataFrame:
        """估值快照（get_more_info 估值字段组）：动态PE/静态PE-TTM/PB-MRQ/股息率/总流通市值。"""
        fields = ["DynaPE", "StaticPE_TTM", "PB_MRQ", "DYRatio", "Zsz", "Ltsz"]
        rows: list[dict] = []
        for symbol in symbols or []:
            try:
                result = _call("get_more_info", {"stock_code": symbol, "field_list": fields}, timeout=15)
                v = self._flatten_value(result.get("Value") or {})
                if not v:
                    continue
                rows.append({
                    "symbol": symbol,
                    "pe_ttm": _f(v.get("StaticPE_TTM")),
                    "pe_dyn": _f(v.get("DynaPE")),
                    "pb_mrq": _f(v.get("PB_MRQ")),
                    "dividend_yield": _f(v.get("DYRatio")),
                    "total_mv": _f(v.get("Zsz")),
                    "float_mv": _f(v.get("Ltsz")),
                    "date": datetime.now().strftime("%Y-%m-%d"),
                })
            except Exception as e:  # noqa: BLE001
                logger.warning("tdx_quant valuation %s failed: %s", symbol, e)
        return pl.DataFrame(rows) if rows else pl.DataFrame()

    def get_kzz(self, symbol: str | None = None) -> pl.DataFrame:
        """可转债信息（get_kzz_info）：转股价/溢价率/纯债价值/强赎回售触发/双评级/剩余规模。"""
        params: dict = {}
        if symbol:
            params["stock_code"] = symbol
        try:
            result = _call("get_kzz_info", params, timeout=20)
            value = result.get("Value") or {}
            items = value if isinstance(value, list) else [value]
            rows: list[dict] = []
            for v in items:
                if not isinstance(v, dict) or not v.get("KZZCode"):
                    continue
                rows.append({
                    "kzz_code": v.get("KZZCode"),
                    "hs_code": v.get("HSCode"),
                    "strike_price": _f(v.get("ZGPrice")),
                    "premium_rate": _f(v.get("KZZYj")),
                    "pure_bond_value": _f(v.get("RealValue")),
                    "expire_yield": _f(v.get("ExpireYield")),
                    "force_redeem_price": _f(v.get("ForceRedeem")),
                    "put_back_price": _f(v.get("PutBack")),
                    "remain_size": _f(v.get("RestScope")),
                    "kzz_price": _f(v.get("KZZPrice")),
                    "hs_price": _f(v.get("AGPrice")),
                    "zg_value": _f(v.get("ZGValue")),
                    "rating": v.get("KZZScore"),
                    "hs_rating": v.get("HSScore"),
                })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("tdx_quant kzz failed: %s", e)
            return pl.DataFrame()

    def get_ipo(self, ipo_type: int = 2, include_future: int = 1) -> pl.DataFrame:
        """新股/新债申购（get_ipo_info）。ipo_type: 0新股/1新债/2全部; include_future: 1=今天及以后。"""
        try:
            result = _call("get_ipo_info", {"ipo_type": ipo_type, "ipo_date": include_future}, timeout=15)
            items = result.get("Value") or []
            if isinstance(items, dict):
                items = [items]
            rows: list[dict] = []
            for v in items:
                if not isinstance(v, dict):
                    continue
                rows.append({
                    "code": v.get("Code"),
                    "name": v.get("Name"),
                    "sg_code": v.get("SGCode"),
                    "sg_date": v.get("SGDate"),
                    "sg_price": _f(v.get("SGPrice")),
                    "pe_issue": _f(v.get("PE_Issue")),
                    "max_sg": v.get("MaxSG"),
                })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("tdx_quant ipo failed: %s", e)
            return pl.DataFrame()

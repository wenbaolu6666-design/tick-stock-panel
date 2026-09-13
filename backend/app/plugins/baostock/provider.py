"""Baostock plugin — 免注册 TCP 数据源 (估值历史/行业快照)。

- valuation: 日频 PE-TTM/PB-MRQ/PS-TTM/PCF-NCF-TTM + 换手率 (query_history_k_data_plus, 2016 起)
- sector:    全市场行业归属快照 (query_stock_industry, 证监会口径)

限制: 不支持北交所 (登录前拦截)；baostock 会话非线程安全 (模块级单次登录)。
"""
from __future__ import annotations

import logging
import threading

import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("valuation", "sector", "corp_events")
_VAL_FIELDS = "date,code,close,peTTM,pbMRQ,psTTM,pcfNcfTTM,turn"

_lock = threading.Lock()
_logged_in = False


def _ensure_login():
    global _logged_in
    with _lock:
        if not _logged_in:
            import baostock as bs
            lg = bs.login()
            if lg.error_code != "0":
                raise RuntimeError(f"baostock 登录失败: {lg.error_msg}")
            _logged_in = True


def _bs_symbol(symbol: str) -> str:
    c = "".join(ch for ch in symbol if ch.isdigit()).zfill(6)
    suffix = "sh" if symbol.upper().endswith(".SH") or c.startswith(("6", "9", "5")) else "sz"
    return f"{suffix}.{c}"


def availability() -> tuple[bool, str]:
    try:
        _ensure_login()
        import baostock as bs
        rs = bs.query_trade_dates(start_date="2026-01-01", end_date="2026-01-15")
        ok = rs.error_code == "0"
        return (True, "ok (login success)" if ok else f"query error {rs.error_code}")
    except Exception as e:  # noqa: BLE001
        return False, f"baostock 不可用: {e}"


def _rows_of(rs) -> list[dict]:
    out = []
    while rs.error_code == "0" and rs.next():
        out.append(rs.get_row_data())
    return out


class BaostockProvider:
    name = "baostock"
    builtin = True

    def __init__(self) -> None:
        self.datasets = dict.fromkeys(_DATASETS)

    def close(self) -> None:  # 会话进程级复用, 不主动登出
        pass

    # ==================== valuation (估值历史) ====================

    def get_valuation(
        self,
        symbols: list[str],
        start_time=None,
        end_time=None,
    ) -> pl.DataFrame:
        """日频估值历史：pe_ttm/pb_mrq/ps_ttm/pcf_ttm/换手率（2016 起，不支持北交所）。"""
        import baostock as bs
        from datetime import datetime

        end = end_time or datetime.now()
        start = start_time or end.replace(year=max(end.year - 1, 2016))
        rows: list[dict] = []
        for symbol in symbols or []:
            if symbol.upper().endswith(".BJ"):
                logger.warning("baostock 不支持北交所 %s，跳过", symbol)
                continue
            try:
                _ensure_login()
                rs = bs.query_history_k_data_plus(
                    _bs_symbol(symbol), _VAL_FIELDS,
                    start_date=start.strftime("%Y-%m-%d"),
                    end_date=end.strftime("%Y-%m-%d"),
                    frequency="d", adjustflag="3",
                )
                for r in _rows_of(rs):
                    def _f(i):
                        try:
                            return float(r[i]) if r[i] not in ("", None) else None
                        except (TypeError, ValueError, IndexError):
                            return None
                    rows.append({
                        "symbol": symbol,
                        "date": r[0],
                        "close": _f(2),
                        "pe_ttm": _f(3),
                        "pb_mrq": _f(4),
                        "ps_ttm": _f(5),
                        "pcf_ttm": _f(6),
                        "turnover": _f(7),
                    })
            except Exception as e:  # noqa: BLE001
                logger.warning("baostock valuation %s failed: %s", symbol, e)
        return pl.DataFrame(rows) if rows else pl.DataFrame()

    # ==================== sector (行业归属快照) ====================

    def get_sector(self) -> pl.DataFrame:
        """全市场行业归属快照（证监会口径，非申万；申万变迁见 sws_ext）。"""
        import baostock as bs
        try:
            _ensure_login()
            rs = bs.query_stock_industry()
            rows = []
            for r in _rows_of(rs):
                if len(r) < 5:
                    continue
                code = r[1]  # sh.600519
                c, _, suffix = code.partition(".")
                rows.append({
                    "symbol": f"{c.zfill(6)}.{suffix.upper()}",
                    "name": r[2],
                    "industry": r[3],
                    "industry_source": r[4],
                    "update_date": r[0],
                })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("baostock sector failed: %s", e)
            return pl.DataFrame()

    # ==================== corp_events (分红送转) ====================

    def get_corp_events(self, symbol: str, year: int | None = None) -> pl.DataFrame:
        """分红送转（query_dividend_data，baostock 口径）。year 默认近 3 年。"""
        import baostock as bs
        from datetime import datetime as _dt

        code = _bs_symbol(symbol)
        now_y = (_dt.now().year)
        rows: list[dict] = []
        try:
            _ensure_login()
            for y in range(year or now_y - 2, now_y + 1):
                rs = bs.query_dividend_data(code=code, year=str(y), yearType="operate")
                for r in _rows_of(rs):
                    if len(r) < 15:
                        continue
                    rows.append({
                        "symbol": symbol,
                        "kind": "dividend",
                        "code": r[1],
                        "divid_date": r[4] or None,     # 派息日
                        "divid_stock": r[6] or None,    # 送股比例
                        "divid_cash": r[9] or None,     # 派息(税前)
                        "divid_stock_to_stock": r[12] or None,  # 转增比例
                        "year": str(y),
                    })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("baostock corp_events %s failed: %s", symbol, e)
            return pl.DataFrame()

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        symbols = symbols or ["600519.SH"]
        if dataset == "valuation":
            df = self.get_valuation(symbols[:1])
        elif dataset == "sector":
            df = self.get_sector()
        elif dataset == "corp_events":
            df = self.get_corp_events(symbols[0])
        else:
            raise ValueError(f"baostock 不支持数据集: {dataset}")
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns,
            "preview": df.head(3).to_dicts() if not df.is_empty() else [],
        }

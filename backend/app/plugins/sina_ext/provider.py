"""Sina Finance options plugin — 按上游拆分自 astock_ext。

上游全部为新浪财经 (hq.sinajs.cn / stock.finance.sina.com.cn):
- options: ETF 期权合约月份 / T型报价 / 希腊字母

All public endpoints, no API key required.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

import httpx
import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("options", "money_flow", "news", "daily", "minute", "realtime")
_SINA_OPT_HDR = {"Referer": "https://stock.finance.sina.com.cn/",
                 "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"}
_CATE = {"510050": "50ETF", "510300": "300ETF", "588000": "科创50ETF", "510500": "500ETF"}


def _opt_f(x):
    try:
        return float(x)
    except Exception:
        return x


def availability():
    try:
        r = httpx.get(
            "https://stock.finance.sina.com.cn/futures/api/openapi.php/StockOptionService.getStockName",
            params={"exchange": "null", "cate": "50ETF"},
            headers=_SINA_OPT_HDR, timeout=10,
        )
        ok = r.status_code == 200 and "result" in r.text
        return (True, "ok (期权月份可取)" if ok else f"HTTP {r.status_code}")
    except Exception as e:  # noqa: BLE001
        return False, f"新浪期权接口不可达: {e}"


class SinaExtProvider:
    name = "sina_ext"
    builtin = True

    def __init__(self) -> None:
        self.datasets = dict.fromkeys(_DATASETS)
        self._client = httpx.Client(
            headers=_SINA_OPT_HDR,
            timeout=15,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def _sina_opt_list(self, param: str) -> list:
        try:
            r = self._client.get(f"https://hq.sinajs.cn/list={param}", headers=_SINA_OPT_HDR, timeout=10)
            t = r.text.split("=", 1)[-1]
            return t.split('"')[1].split(",") if '"' in t else []
        except Exception:  # noqa: BLE001
            return []

    # ==================== options (ETF 期权) ====================

    def get_option_months(self, underlying: str = "510050", call: bool = True) -> dict:
        """ETF 期权合约清单 {月份YYMM: [合约代码]}。"""
        cate = _CATE.get(underlying, "50ETF")
        try:
            r = self._client.get(
                "https://stock.finance.sina.com.cn/futures/api/openapi.php/StockOptionService.getStockName",
                params={"exchange": "null", "cate": cate},
                headers=_SINA_OPT_HDR, timeout=10,
            )
            months = r.json()["result"]["data"]["contractMonth"]
        except Exception as e:  # noqa: BLE001
            logger.warning("sina_ext option months failed: %s", e)
            return {}
        months = [m.replace("-", "")[2:] for m in months[1:]]
        flag = "OP_UP_" if call else "OP_DOWN_"
        out = {}
        for m in months:
            codes = [c.replace("CON_OP_", "") for c in self._sina_opt_list(f"{flag}{underlying}{m}")
                     if c.startswith("CON_OP_")]
            if codes:
                out[m] = codes
        return out

    def get_option_tquote(self, code: str) -> pl.DataFrame:
        """期权 T 型报价（买卖五档/行权价/持仓量/成交量）。"""
        v = self._sina_opt_list(f"CON_OP_{code}")
        if len(v) < 43:
            return pl.DataFrame()
        return pl.DataFrame([{
            "code": code,
            "bid_vol": _opt_f(v[0]), "bid": _opt_f(v[1]), "last": _opt_f(v[2]),
            "ask": _opt_f(v[3]), "ask_vol": _opt_f(v[4]), "open_interest": _opt_f(v[5]),
            "pct": _opt_f(v[6]), "strike": _opt_f(v[7]), "prev_close": _opt_f(v[8]),
            "open": _opt_f(v[9]), "limit_up": _opt_f(v[10]), "limit_down": _opt_f(v[11]),
            "name": v[37], "amplitude": _opt_f(v[38]), "high": _opt_f(v[39]),
            "low": _opt_f(v[40]), "volume": _opt_f(v[41]), "amount": _opt_f(v[42]),
        }])

    def get_option_greeks(self, code: str) -> pl.DataFrame:
        """期权希腊字母（Delta/Gamma/Theta/Vega/隐含波动率）。"""
        raw = self._sina_opt_list(f"CON_SO_{code}")
        if len(raw) < 16:
            return pl.DataFrame()
        v = [raw[0]] + raw[4:]
        return pl.DataFrame([{
            "code": code,
            "name": v[0], "volume": _opt_f(v[1]), "delta": _opt_f(v[2]),
            "gamma": _opt_f(v[3]), "theta": _opt_f(v[4]), "vega": _opt_f(v[5]),
            "iv": _opt_f(v[6]), "high": _opt_f(v[7]), "low": _opt_f(v[8]),
            "trade_code": v[9], "strike": _opt_f(v[10]), "last": _opt_f(v[11]),
            "theory": _opt_f(v[12]),
        }])

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        if dataset == "options":
            months = self.get_option_months()
            first = next(iter(months.values()), [])
            df = self.get_option_tquote(first[0]) if first else pl.DataFrame()
        elif dataset == "money_flow":
            df = self.get_money_flow((symbols or ["sh600519"])[0])
        elif dataset == "news":
            df = self.get_news_feed(limit=5)
        else:
            raise ValueError(f"sina_ext 不支持数据集: {dataset}")
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns,
            "preview": df.head(3).to_dicts() if not df.is_empty() else [],
        }

    # ==================== money_flow / news (按上游扩容, 新浪口径) ====================

    def get_money_flow(self, symbol: str, num: int = 20) -> pl.DataFrame:
        """个股日度资金流（新浪口径）：四档单净额 + 收盘/换手。symbol 形如 sh600519/sz000001。"""
        code = symbol.lower().strip()
        if "." in code:
            c, _, suffix = code.partition(".")
            code = ("sh" if suffix.upper() == "SH" else "sz") + c
        try:
            r = self._client.get(
                "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_qsfx_zjlrqs",
                params={"page": 1, "num": num, "sort": "opendate", "asc": 0, "daima": code},
                headers={"Referer": "https://finance.sina.com.cn/"},
                timeout=15,
            )
            items = r.json() or []
            rows = []
            for x in items:
                if not isinstance(x, dict):
                    continue
                rows.append({
                    "symbol": symbol,
                    "date": x.get("opendate"),
                    "close": _opt_f(x.get("trade")),
                    "change_pct": _opt_f(x.get("changeratio")),
                    "turnover": _opt_f(x.get("turnover")),
                    "net_amount": _opt_f(x.get("netamount")),
                    "r0_net": _opt_f(x.get("r0_net")),
                    "r0_ratio": _opt_f(x.get("r0_ratio")),
                    "r3_net": _opt_f(x.get("r3_net")),
                    "r5_net": _opt_f(x.get("r5_net")),
                    "r10_net": _opt_f(x.get("r10_net")),
                })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("sina_ext money_flow %s failed: %s", symbol, e)
            return pl.DataFrame()

    def get_news_feed(self, limit: int = 30) -> pl.DataFrame:
        """新浪 7×24 滚播快讯（zhibo_id=152），ext.stocks 带个股关联。"""
        try:
            r = self._client.get(
                "https://zhibo.sina.com.cn/api/zhibo/feed",
                params={"page": 1, "page_size": limit, "zhibo_id": 152, "tag_id": 0},
                headers={"Referer": "https://finance.sina.com.cn/"},
                timeout=15,
            )
            feed = (((r.json().get("result") or {}).get("data") or {}).get("feed") or {})
            rows = []
            for it in (feed.get("list") or []):
                if not isinstance(it, dict):
                    continue
                ext = {}
                try:
                    import json as _json
                    ext = _json.loads(it.get("ext") or "{}") if isinstance(it.get("ext"), str) else (it.get("ext") or {})
                except Exception:  # noqa: BLE001
                    ext = {}
                stocks = ",".join(
                    str(s.get("code", ""))
                    for s in (ext.get("stocks") or []) if isinstance(s, dict)
                )
                rows.append({
                    "title": (it.get("rich_title") or it.get("name") or "")[:120],
                    "content": it.get("rich_text") or it.get("text") or "",
                    "publish_time": it.get("create_time"),
                    "stocks": stocks,
                    "source": "新浪7x24",
                })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("sina_ext news_feed failed: %s", e)
            return pl.DataFrame()

    # ==================== daily / minute / realtime (按上游扩容, 新浪行情) ====================

    _KLINE_URL = "https://quotes.sina.com.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"

    @staticmethod
    def _sina_symbol(symbol: str) -> str:
        c = "".join(ch for ch in symbol if ch.isdigit()).zfill(6)
        suffix = "sh" if symbol.upper().endswith(".SH") or c.startswith(("6", "9", "5")) else "sz"
        return f"{suffix}{c}"

    @staticmethod
    def _universe_symbols() -> list[str]:
        from pathlib import Path
        try:
            from app.config import settings as _settings
            path = Path(_settings.data_dir) / "instruments" / "instruments.parquet"
            if not path.exists():
                return []
            df = pl.read_parquet(path)
            return [str(s) for s in df["symbol"].to_list() if s]
        except Exception:  # noqa: BLE001
            return []

    def get_daily(
        self,
        symbols: list[str],
        start_time=None,
        end_time=None,
        asset_type: str = "stock",  # noqa: ARG002
        on_chunk_done=None,
    ) -> pl.DataFrame:
        """新浪日K（getKLineData scale=240）：不复权，volume 股→手，无成交额；datalen≤800。"""
        frames: list[pl.DataFrame] = []
        syms = symbols or []
        for i, symbol in enumerate(syms):
            try:
                r = self._client.get(
                    self._KLINE_URL,
                    params={"symbol": self._sina_symbol(symbol), "scale": 240, "ma": "no", "datalen": 800},
                    headers={"Referer": "https://finance.sina.com.cn/"},
                    timeout=15,
                )
                items = r.json() or []
                recs = []
                for x in items:
                    if not isinstance(x, dict):
                        continue
                    recs.append({
                        "symbol": symbol,
                        "date": str(x.get("day", ""))[:10],
                        "open": _opt_f(x.get("open")),
                        "high": _opt_f(x.get("high")),
                        "low": _opt_f(x.get("low")),
                        "close": _opt_f(x.get("close")),
                        "volume": (v / 100.0 if (v := _opt_f(x.get("volume"))) else None),
                        "amount": None,
                    })
                df = pl.DataFrame(recs) if recs else pl.DataFrame()
                if not df.is_empty():
                    if start_time is not None:
                        df = df.filter(pl.col("date") >= start_time.strftime("%Y-%m-%d"))
                    if end_time is not None:
                        df = df.filter(pl.col("date") <= end_time.strftime("%Y-%m-%d"))
                    if not df.is_empty():
                        frames.append(df)
            except Exception as e:  # noqa: BLE001
                logger.warning("sina_ext daily %s failed: %s", symbol, e)
            time.sleep(0.2)
            if on_chunk_done:
                on_chunk_done(i + 1, len(syms))
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    def get_minute(
        self,
        symbols: list[str],
        start_time=None,
        end_time=None,
        asset_type: str = "stock",  # noqa: ARG002
        freq: str = "5m",
        on_chunk_done=None,
    ) -> pl.DataFrame:
        """新浪分钟K（getKLineData scale=1/5/15/30/60），volume 股→手。

        scale=1 实测 datalen 可到 ~1970 (约 9 个交易日全天), 供当日/近日分时回退用;
        其余档位 ≤800 根。
        """
        scale = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60}.get(str(freq), 5)
        datalen = 1970 if scale == 1 else 800
        frames: list[pl.DataFrame] = []
        syms = symbols or []
        for i, symbol in enumerate(syms):
            try:
                r = self._client.get(
                    self._KLINE_URL,
                    params={"symbol": self._sina_symbol(symbol), "scale": scale, "ma": "no", "datalen": datalen},
                    headers={"Referer": "https://finance.sina.com.cn/"},
                    timeout=15,
                )
                items = r.json() or []
                recs = []
                for x in items:
                    if not isinstance(x, dict):
                        continue
                    day = str(x.get("day", ""))
                    try:
                        dt = datetime.strptime(day, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        continue
                    recs.append({
                        "symbol": symbol,
                        "datetime": dt,
                        "open": _opt_f(x.get("open")),
                        "high": _opt_f(x.get("high")),
                        "low": _opt_f(x.get("low")),
                        "close": _opt_f(x.get("close")),
                        "volume": (v / 100.0 if (v := _opt_f(x.get("volume"))) else None),
                        "amount": None,
                    })
                df = pl.DataFrame(recs) if recs else pl.DataFrame()
                if not df.is_empty():
                    if start_time is not None:
                        df = df.filter(pl.col("datetime") >= start_time)
                    if end_time is not None:
                        df = df.filter(pl.col("datetime") <= end_time)
                    if not df.is_empty():
                        frames.append(df)
            except Exception as e:  # noqa: BLE001
                logger.warning("sina_ext minute %s failed: %s", symbol, e)
            time.sleep(0.2)
            if on_chunk_done:
                on_chunk_done(i + 1, len(syms))
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    def get_realtime(self) -> list[dict]:
        """全市场实时快照（hq.sinajs.cn/list= 批量，≤100只/次；标的集取自 instruments 维表）。"""
        from pathlib import Path
        try:
            from app.config import settings as _settings
            path = Path(_settings.data_dir) / "instruments" / "instruments.parquet"
            df = pl.read_parquet(path) if path.exists() else pl.DataFrame()
            universe = [str(s) for s in df["symbol"].to_list() if s] if "symbol" in df.columns else []
        except Exception as e:  # noqa: BLE001
            logger.warning("sina_ext realtime universe failed: %s", e)
            return []
        out: list[dict] = []
        for i in range(0, len(universe), 80):
            chunk = universe[i:i + 80]
            codes = [self._sina_symbol(s) for s in chunk]
            try:
                r = self._client.get(
                    f"https://hq.sinajs.cn/list={','.join(codes)}",
                    headers={"Referer": "https://finance.sina.com.cn/"},
                    timeout=15,
                )
                text = r.content.decode("gbk", errors="ignore")
                by_code = {self._sina_symbol(s): s for s in chunk}
                for line in text.split(";"):
                    line = line.strip()
                    if "=" not in line or '"' not in line:
                        continue
                    key = line.split("=")[0].strip().replace("var hq_str_", "")
                    vals = line.split('"', 1)[1].split('"', 1)[0].split(",")
                    if len(vals) < 32:
                        continue
                    symbol = by_code.get(key, key)
                    out.append({
                        "symbol": symbol,
                        "name": vals[0],
                        "open": _opt_f(vals[1]),
                        "prev_close": _opt_f(vals[2]),
                        "last_price": _opt_f(vals[3]),
                        "high": _opt_f(vals[4]),
                        "low": _opt_f(vals[5]),
                        "volume": (v / 100.0 if (v := _opt_f(vals[8])) else None),
                        "amount": _opt_f(vals[9]),
                        "timestamp": None,
                    })
            except Exception as e:  # noqa: BLE001
                logger.warning("sina_ext realtime chunk failed: %s", e)
            time.sleep(0.2)
        return out

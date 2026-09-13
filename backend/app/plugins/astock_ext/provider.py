"""东方财富系数据插件 (东财 push2 / datacenter / reportapi / mobappconfig)。

- money_flow: 东财 push2 fflow 主力资金流（分钟/日）
- news: 东财 7x24 快讯（市场级快讯流）
- margin/block_trade/corp_events/research/dragon_tiger/anomaly: 东财 datacenter/reportapi 系列
- chip: 本地推演 CYQ（无公开接口, 用 TSP 落盘日K + 换手衰减）
- 预留未接路由: get_concept_blocks(东财 slist 板块归属)/get_monitor_pool(东财监控池)

非东财上游已按来源拆分为独立插件: ths_basic(同花顺网页)/cninfo_ext(巨潮)/
sina_ext(新浪期权)/macro_gov(统计局+央行)/cls_ext(财联社)。
All public endpoints, no API key required.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import httpx
import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("money_flow", "news", "margin", "block_trade", "corp_events", "research", "dragon_tiger", "anomaly", "limit_updown", "hot_list", "sector", "financial")
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"


def _normalize(code: str) -> str:
    return "".join(ch for ch in code if ch.isdigit()).zfill(6)


def _secid(code: str) -> str:
    c = _normalize(code)
    return f"1.{c}" if c.startswith(("6", "9", "5")) else f"0.{c}"


def _f(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def availability():
    try:
        r = httpx.get(
            "https://np-weblist.eastmoney.com/comm/web/getFastNewsList",
            params={"client": "web", "biz": "web_724", "fastColumn": "102", "pageSize": 3},
            headers={"User-Agent": _UA, "Referer": "https://kuaixun.eastmoney.com/"},
            timeout=10,
        )
        items = ((r.json().get("data") or {}).get("fastNewsList")) or []
        return True, f"ok ({len(items)} fast news)"
    except Exception as e:  # noqa: BLE001
        return False, f"东方财富数据不可达: {e}"

class AstockExtProvider:
    name = "astock_ext"
    builtin = True

    def __init__(self) -> None:
        self.datasets = dict.fromkeys(_DATASETS)
        self._client = httpx.Client(
            headers={"User-Agent": _UA},
            timeout=15,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    # ==================== money_flow ====================

    def get_fund_flow(self, symbol: str, days: int = 20) -> pl.DataFrame:
        """东财主力资金流：分钟级今日 + 日级历史（主力/大单/超大单净流入，元）。"""
        secid = _secid(symbol)
        rows: list[dict] = []
        # 分钟级（当日）
        try:
            r = self._client.get(
                "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get",
                headers={"Referer": "https://quote.eastmoney.com/"},
                params={
                    "secid": secid, "klt": 1,
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57",
                },
            )
            klines = ((r.json().get("data") or {}).get("klines")) or []
            for line in klines:
                parts = line.split(",")
                if len(parts) >= 6:
                    rows.append({
                        "symbol": symbol, "period": "minute", "time": parts[0],
                        "main_net": float(parts[1]) if parts[1] else None,
                        "small_net": float(parts[2]) if parts[2] else None,
                        "mid_net": float(parts[3]) if parts[3] else None,
                        "large_net": float(parts[4]) if parts[4] else None,
                        "super_net": float(parts[5]) if parts[5] else None,
                    })
        except Exception as e:  # noqa: BLE001
            logger.warning("astock_ext fund_flow minute %s failed: %s", symbol, e)
        # 日级历史
        try:
            r = self._client.get(
                "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
                headers={"Referer": "https://quote.eastmoney.com/"},
                params={
                    "secid": secid, "klt": 101, "lmt": days,
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57",
                },
            )
            klines = ((r.json().get("data") or {}).get("klines")) or []
            for line in klines:
                parts = line.split(",")
                if len(parts) >= 6:
                    rows.append({
                        "symbol": symbol, "period": "daily", "time": parts[0],
                        "main_net": float(parts[1]) if parts[1] else None,
                        "small_net": float(parts[2]) if parts[2] else None,
                        "mid_net": float(parts[3]) if parts[3] else None,
                        "large_net": float(parts[4]) if parts[4] else None,
                        "super_net": float(parts[5]) if parts[5] else None,
                    })
        except Exception as e:  # noqa: BLE001
            logger.warning("astock_ext fund_flow daily %s failed: %s", symbol, e)
        return pl.DataFrame(rows) if rows else pl.DataFrame()

    # ==================== concept blocks (百度, 预留未接路由) ====================

    def get_concept_blocks(self, symbol: str) -> pl.DataFrame:
        """个股所属板块（行业/概念/地域）：东财 slist。

        注: 原百度股市通 getrelatedblock 接口 2026 实测已失效(ResultCode 10003),
        参照 a-stock-data V3.2.2 改用东财 push2 slist (spt=3)。
        """
        secid = _secid(symbol)
        try:
            r = self._client.get(
                "https://push2.eastmoney.com/api/qt/slist/get",
                params={
                    "secid": secid, "spt": 3, "pn": 1, "pz": 100,
                    "po": 1, "fid": "f3", "fltt": 2, "invt": 2,
                    "fields": "f12,f14,f3",
                    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                },
                headers={"Referer": "https://quote.eastmoney.com/"},
                timeout=15,
            )
            diff = ((r.json().get("data") or {}).get("diff")) or []
            if isinstance(diff, dict):
                diff = list(diff.values())
            rows = []
            for blk in diff:
                if not isinstance(blk, dict):
                    continue
                rows.append({
                    "symbol": symbol,
                    "block_code": blk.get("f12"),
                    "block_name": blk.get("f14"),
                    "change_pct": blk.get("f3"),
                })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("astock_ext slist %s failed: %s", symbol, e)
            return pl.DataFrame()

    # ==================== news (东财 7x24 快讯) ====================

    def get_telegraph(self, limit: int = 30) -> pl.DataFrame:
        """东财 7x24 快讯（市场级快讯流，含相关股票）。"""
        try:
            r = self._client.get(
                "https://np-weblist.eastmoney.com/comm/web/getFastNewsList",
                params={
                    "client": "web", "biz": "web_724",
                    "fastColumn": "102", "sortEnd": "",
                    "pageSize": limit, "req_trace": "1",
                },
                headers={"Referer": "https://kuaixun.eastmoney.com/"},
            )
            items = ((r.json().get("data") or {}).get("fastNewsList")) or []
            rows = []
            for item in items:
                stocks = [
                    s.get("code") or s.get("name")
                    for s in (item.get("stockList") or []) if isinstance(s, dict)
                ]
                rows.append({
                    "title": item.get("title") or "",
                    "content": item.get("summary") or "",
                    "publish_time": item.get("showTime") or "",
                    "stocks": ",".join(x for x in stocks if x),
                    "source": "东财快讯",
                })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("astock_ext telegraph failed: %s", e)
            return pl.DataFrame()
    # ==================== 东财 datacenter 统一查询 ====================

    _EM_DC_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    _EM_LAST = 0.0

    def _em_datacenter(
        self,
        report_name: str,
        filter_str: str = "",
        page_size: int = 50,
        sort_columns: str = "",
        sort_types: str = "-1",
    ) -> list[dict]:
        """东财数据中心统一查询（龙虎榜/解禁/两融/大宗/户数/分红 共用），内置节流。"""
        import time as _t
        gap = _t.time() - AstockExtProvider._EM_LAST
        if gap < 0.6:
            _t.sleep(0.6 - gap)
        AstockExtProvider._EM_LAST = _t.time()
        r = self._client.get(
            self._EM_DC_URL,
            params={
                "reportName": report_name, "columns": "ALL",
                "filter": filter_str, "pageNumber": "1", "pageSize": str(page_size),
                "sortColumns": sort_columns, "sortTypes": sort_types,
                "source": "WEB", "client": "WEB",
            },
            headers={"Referer": "https://data.eastmoney.com/"},
        )
        r.raise_for_status()
        d = r.json()
        result = d.get("result") or {}
        return [x for x in (result.get("data") or []) if isinstance(x, dict)]

    def get_margin(self, symbol: str, days: int = 60) -> pl.DataFrame:
        """融资融券明细（日级）：融资余额/买入/偿还、融券余额/卖出。"""
        code = _normalize(symbol)
        data = self._em_datacenter(
            "RPTA_WEB_RZRQ_GGMX",
            filter_str=f'(SCODE="{code}")',
            page_size=days,
            sort_columns="DATE", sort_types="-1",
        )
        rows = [{
            "symbol": symbol,
            "date": str(x.get("DATE", ""))[:10],
            "rzye": x.get("RZYE", 0),
            "rzmre": x.get("RZMRE", 0),
            "rzche": x.get("RZCHE", 0),
            "rqye": x.get("RQYE", 0),
            "rqmcl": x.get("RQMCL", 0),
            "rzrqye": x.get("RZRQYE", 0),
        } for x in data]
        return pl.DataFrame(rows) if rows else pl.DataFrame()

    def get_block_trade(self, symbol: str, days: int = 40) -> pl.DataFrame:
        """大宗交易记录：成交价/量/额、买卖营业部、折溢价。"""
        code = _normalize(symbol)
        data = self._em_datacenter(
            "RPT_DATA_BLOCKTRADE",
            filter_str=f'(SECURITY_CODE="{code}")',
            page_size=days,
            sort_columns="TRADE_DATE", sort_types="-1",
        )
        rows = []
        for x in data:
            close = x.get("CLOSE_PRICE") or 0
            deal = x.get("DEAL_PRICE") or 0
            rows.append({
                "symbol": symbol,
                "date": str(x.get("TRADE_DATE", ""))[:10],
                "price": deal,
                "close": close,
                "premium_pct": ((deal / close - 1) * 100) if close else None,
                "vol": x.get("DEAL_VOLUME", 0),
                "amount": x.get("DEAL_AMT", 0),
                "buyer": x.get("BUYER_NAME", ""),
                "seller": x.get("SELLER_NAME", ""),
            })
        return pl.DataFrame(rows) if rows else pl.DataFrame()

    def get_corp_events(self, symbol: str) -> pl.DataFrame:
        """公司事件日历（kind 列区分）：lift=限售解禁 / dividend=分红送转 / holders=股东户数。"""
        code = _normalize(symbol)
        rows: list[dict] = []
        try:
            for x in self._em_datacenter(
                "RPT_LIFT_STAGE", filter_str=f'(SECURITY_CODE="{code}")',
                page_size=20, sort_columns="FREE_DATE", sort_types="-1",
            ):
                rows.append({
                    "symbol": symbol, "kind": "lift",
                    "date": str(x.get("FREE_DATE", ""))[:10],
                    "shares": x.get("FREE_SHARES"),
                    "type": x.get("FREE_SHARES_TYPE", ""),
                    "ratio": x.get("TOTAL_RATIO"),
                })
        except Exception as e:  # noqa: BLE001
            logger.warning("astock_ext lift %s failed: %s", symbol, e)
        try:
            for x in self._em_datacenter(
                "RPT_SHAREBONUS_DET", filter_str=f'(SECURITY_CODE="{code}")',
                page_size=20, sort_columns="REPORT_DATE", sort_types="-1",
            ):
                rows.append({
                    "symbol": symbol, "kind": "dividend",
                    "date": str(x.get("EX_DIVIDEND_DATE", "") or x.get("REPORT_DATE", ""))[:10],
                    "shares": None,
                    "type": x.get("IMPL_PLAN_PROFILE", ""),
                    "ratio": None,
                })
        except Exception as e:  # noqa: BLE001
            logger.warning("astock_ext dividend %s failed: %s", symbol, e)
        try:
            for x in self._em_datacenter(
                "RPT_HOLDERNUMLATEST", filter_str=f'(SECURITY_CODE="{code}")',
                page_size=12, sort_columns="END_DATE", sort_types="-1",
            ):
                rows.append({
                    "symbol": symbol, "kind": "holders",
                    "date": str(x.get("END_DATE", ""))[:10],
                    "shares": x.get("HOLDER_NUM"),
                    "type": f"环比{x.get('HOLDER_NUM_RATIO', 0)}%",
                    "ratio": None,
                })
        except Exception as e:  # noqa: BLE001
            logger.warning("astock_ext holders %s failed: %s", symbol, e)
        return pl.DataFrame(rows) if rows else pl.DataFrame()

    # ==================== research ====================

    _REPORT_API = "https://reportapi.eastmoney.com/report/list"
    _REPORT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"

    def get_reports(self, symbol: str, max_pages: int = 2) -> pl.DataFrame:
        """东财个股研报列表（标题/机构/评级/盈利预测）。"""
        code = _normalize(symbol)
        rows: list[dict] = []
        for page in range(1, max_pages + 1):
            try:
                r = self._client.get(
                    self._REPORT_API,
                    params={
                        "industryCode": "*", "pageSize": "100", "industry": "*",
                        "pageNo": str(page), "fields": "", "qType": "0", "code": code,
                    },
                    headers={"User-Agent": self._REPORT_UA, "Referer": "https://data.eastmoney.com/"},
                )
                d = r.json()
                total = d.get("TotalPage") or 1
                for x in (d.get("data") or []):
                    if not isinstance(x, dict):
                        continue
                    rows.append({
                        "symbol": symbol,
                        "title": x.get("title", ""),
                        "org": x.get("orgSName", ""),
                        "rating": x.get("emRatingName", "") or x.get("sRatingName", ""),
                        "publish_date": str(x.get("publishDate", ""))[:10],
                        "info_code": x.get("infoCode", ""),
                        "eps_y1": x.get("predictThisYearEps"),
                        "eps_y2": x.get("predictNextYearEps"),
                    })
                if page >= total:
                    break
            except Exception as e:  # noqa: BLE001
                logger.warning("astock_ext reports %s p%d failed: %s", symbol, page, e)
        return pl.DataFrame(rows) if rows else pl.DataFrame()

    def get_monitor_pool(self) -> pl.DataFrame:
        """东财重点监控池。"""
        try:
            r = self._client.get(
                "https://mobappconfig.securities.eastmoney.com/emcfg/stock_monitor.json",
                headers={"Referer": "https://vipmoney.eastmoney.com/"},
                timeout=15,
            )
            today = datetime.now().strftime("%Y%m%d")
            rows = []
            for x in (r.json().get("data") or []):
                if not isinstance(x, dict):
                    continue
                end = str(x.get("VALIDATEENDDATE", ""))
                if end and today > end:
                    continue
                mkt = str(x.get("MARKET", "")).upper()
                rows.append({
                    "symbol": str(x.get("STKCODE", "")).zfill(6),
                    "name": x.get("STKNAME", ""),
                    "market": {"1": "SH", "0": "SZ", "B": "BJ"}.get(mkt, mkt),
                    "link": x.get("LINK_URL", ""),
                    "valid_to": end,
                })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("astock_ext monitor_pool failed: %s", e)
            return pl.DataFrame()

    def get_anomaly_pool(self, page_size: int = 200) -> pl.DataFrame:
        """东财日内异动池（严重异常波动）。"""
        try:
            r = self._client.get(
                "https://dycalchis.eastmoney.com/price-anomaly/list",
                params={"team": "h5", "product": "EastMoney", "client": "WAP",
                        "pageSize": str(page_size), "pageNo": "1"},
                headers={"User-Agent": self._REPORT_UA, "Referer": "https://quote.eastmoney.com/"},
                timeout=15,
            )
            d = r.json()
            if d.get("result") != 0:
                logger.warning("astock_ext anomaly rejected: %s", d.get("msg"))
                return pl.DataFrame()
            rows = []
            for x in (d.get("data") or []):
                if not isinstance(x, dict):
                    continue
                e = x.get("e")
                s = x.get("s")
                key = e * 10 if (s == 6 and e in (4, 5, 6, 7)) else e
                rows.append({
                    "symbol": str(x.get("c", "")).zfill(6),
                    "name": x.get("n", ""),
                    "change_pct": x.get("a"),
                    "deviation": x.get("x"),
                    "days": x.get("d"),
                    "board": s,
                    "rule_code": key,
                    "is_today": x.get("o") != 2,
                    "date": str(d.get("date", "")),
                })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("astock_ext anomaly_pool failed: %s", e)
            return pl.DataFrame()

    def get_lhb_detail(self, symbol: str, look_back: int = 30) -> pl.DataFrame:
        """龙虎榜席位明细：上榜记录 + 买卖前五席位。kind 列区分。"""
        code = _normalize(symbol)
        rows: list[dict] = []
        today = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=look_back)).strftime("%Y-%m-%d")
        try:
            records = self._em_datacenter(
                "RPT_DAILYBILLBOARD_DETAILSNEW",
                filter_str=f"(TRADE_DATE>='{start}')(TRADE_DATE<='{today}')(SECURITY_CODE=\"{code}\")",
                page_size=20, sort_columns="TRADE_DATE", sort_types="-1",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("astock_ext lhb records %s failed: %s", symbol, e)
            records = []
        for x in records:
            rows.append({
                "symbol": symbol, "kind": "record",
                "date": str(x.get("TRADE_DATE", ""))[:10],
                "reason": x.get("EXPLANATION", ""),
                "net_buy": x.get("BILLBOARD_NET_AMT"),
                "buy_amt": x.get("BILLBOARD_BUY_AMT"),
                "sell_amt": x.get("BILLBOARD_SELL_AMT"),
            })
        latest = str(records[0].get("TRADE_DATE", ""))[:10] if records else ""
        if latest:
            for report, side in (("RPT_BILLBOARD_DAILYDETAILSBUY", "buy_seat"),
                                 ("RPT_BILLBOARD_DAILYDETAILSSELL", "sell_seat")):
                try:
                    seats = self._em_datacenter(
                        report,
                        filter_str=f"(TRADE_DATE='{latest}')(SECURITY_CODE=\"{code}\")",
                        page_size=5,
                        sort_columns="BUY" if side == "buy_seat" else "SELL",
                        sort_types="-1",
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("astock_ext lhb seats %s failed: %s", symbol, e)
                    seats = []
                for s in seats:
                    rows.append({
                        "symbol": symbol, "kind": side,
                        "date": latest,
                        "seat": s.get("OPERATEDEPT_NAME", ""),
                        "amt": s.get("BUY" if side == "buy_seat" else "SELL"),
                    })
        return pl.DataFrame(rows) if rows else pl.DataFrame()

    # ==================== limit_updown / hot_list / dragon_tiger / sector 扩容 ====================

    _ZT_UT = "7eea3edcaed734bea9cbfc24409ed989"

    def get_limit_pools(self, date: str | None = None) -> pl.DataFrame:
        """东财涨停/炸板/跌停/昨日涨停四池（push2ex，零鉴权）。

        date=YYYYMMDD，默认今日；上游仅对最新交易日返回完整 pool，历史日期 pool 为空。
        """
        d = date or datetime.now().strftime("%Y%m%d")
        pools = (("zt", "getTopicZTPool"), ("zb", "getTopicZBPool"),
                 ("dt", "getTopicDTPool"), ("yesterday", "getYesterdayZTPool"))
        rows: list[dict] = []
        for tag, api in pools:
            try:
                r = self._client.get(
                    f"https://push2ex.eastmoney.com/{api}",
                    params={"ut": self._ZT_UT, "dpt": "wz.ztzt", "page": 1,
                            "pagesize": 500, "sort": "fbt:asc", "date": d},
                    headers={"Referer": "https://quote.eastmoney.com/"},
                    timeout=15,
                )
                pool = ((r.json().get("data") or {}).get("pool")) or []
                for x in pool:
                    if not isinstance(x, dict):
                        continue
                    tj = x.get("zttj") or {}
                    rows.append({
                        "symbol": str(x.get("c", "")).zfill(6),
                        "name": x.get("n"),
                        "pool": tag,
                        "price": (_f(x.get("p")) / 1000.0 if _f(x.get("p")) else None),
                        "change_pct": (_f(x.get("zdp")) / 100.0 if _f(x.get("zdp")) else None),
                        "lbc": x.get("lbc"),
                        "zt_days": tj.get("days"),
                        "zt_count": tj.get("ct"),
                        "fund": _f(x.get("fund")),
                        "first_seal_time": x.get("fbt"),
                        "last_seal_time": x.get("lbt"),
                        "break_times": x.get("zbc"),
                        "turnover": _f(x.get("hs")),
                        "industry": x.get("hybk"),
                        "date": d,
                    })
            except Exception as e:  # noqa: BLE001
                logger.warning("astock_ext %s %s failed: %s", api, d, e)
        return pl.DataFrame(rows) if rows else pl.DataFrame()

    def get_hot_rank(self, page: int = 1, page_size: int = 50) -> pl.DataFrame:
        """东财人气榜 TOP（emappdata POST，零鉴权）：排名/排名变化，push2 ulist 补名称与价格。"""
        try:
            r = self._client.post(
                "https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
                json={"appId": "appId01", "globalId": "786e4c21-70dc-435a-93bb-38",
                      "marketType": "", "pageNo": page, "pageSize": page_size},
                headers={"User-Agent": _UA},
                timeout=15,
            )
            items = r.json().get("data") or []
            secids = []
            for x in items:
                sc = str(x.get("sc", ""))
                if len(sc) >= 8:
                    mkt = "1" if sc.startswith("SH") else "0"
                    secids.append(f"{mkt}.{sc[2:]}")
            names: dict[str, dict] = {}
            if secids:
                try:
                    r2 = self._client.get(
                        "https://push2.eastmoney.com/api/qt/ulist.np/get",
                        params={"secids": ",".join(secids), "fields": "f12,f14,f2,f3", "fltt": 2, "invt": 2},
                        headers={"Referer": "https://quote.eastmoney.com/"},
                        timeout=12,
                    )
                    diff = ((r2.json().get("data") or {}).get("diff")) or []
                    if isinstance(diff, dict):
                        diff = list(diff.values())
                    for d in diff:
                        if isinstance(d, dict):
                            names[str(d.get("f12")).zfill(6)] = {
                                "name": d.get("f14"), "price": _f(d.get("f2")), "change_pct": _f(d.get("f3")),
                            }
                except Exception:  # noqa: BLE001
                    pass
            rows = []
            for x in items:
                sc = str(x.get("sc", ""))
                code6 = sc[2:].zfill(6)
                info = names.get(code6, {})
                rows.append({
                    "symbol": f"{code6}.{'SH' if sc.startswith('SH') else 'SZ'}",
                    "rank": x.get("rk"),
                    "rank_change": x.get("rc"),
                    "rank_change_hist": x.get("hisRc"),
                    "name": info.get("name"),
                    "price": info.get("price"),
                    "change_pct": info.get("change_pct"),
                    "date": datetime.now().strftime("%Y-%m-%d"),
                })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("astock_ext hot_rank failed: %s", e)
            return pl.DataFrame()

    def get_lhb_board(self, date: str | None = None, page_size: int = 500) -> pl.DataFrame:
        """全市场龙虎榜整榜（datacenter 按日，净买额降序）。date=YYYY-MM-DD，默认今日。"""
        d = date or datetime.now().strftime("%Y-%m-%d")
        try:
            data = self._em_datacenter(
                "RPT_DAILYBILLBOARD_DETAILSNEW",
                filter_str=f"(TRADE_DATE='{d}')",
                page_size=page_size, sort_columns="BILLBOARD_NET_AMT", sort_types="-1",
            )
            rows = []
            for x in data:
                rows.append({
                    "symbol": str(x.get("SECURITY_CODE", "")).zfill(6),
                    "name": x.get("SECURITY_NAME_ABBR"),
                    "reason": x.get("EXPLANATION"),
                    "close": _f(x.get("CLOSE_PRICE")),
                    "change_pct": _f(x.get("CHANGE_RATE")),
                    "net_buy": _f(x.get("BILLBOARD_NET_AMT")),
                    "buy_amt": _f(x.get("BILLBOARD_BUY_AMT")),
                    "sell_amt": _f(x.get("BILLBOARD_SELL_AMT")),
                    "turnover": _f(x.get("TURN_OVER_RATE")),
                    "date": d,
                })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("astock_ext lhb_board %s failed: %s", d, e)
            return pl.DataFrame()

    def get_sector_money_flow(self, board_type: str = "industry") -> pl.DataFrame:
        """板块资金流（push2 clist）：行业(t:2)/概念(t:3)/地域(t:1) × 今日/5日 主力净流入。"""
        fs = {"industry": "m:90+t:2", "concept": "m:90+t:3", "region": "m:90+t:1"}.get(board_type, "m:90+t:2")
        fields = ("f12,f14,f2,f3,f62,f184,f66,f72,f78,f84,f164,f165,f109,f257")
        try:
            r = self._client.get(
                "https://push2.eastmoney.com/api/qt/clist/get",
                params={"pn": 1, "pz": 500, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                        "fid": "f62", "fs": fs, "fields": fields},
                headers={"Referer": "https://quote.eastmoney.com/"},
                timeout=15,
            )
            diff = ((r.json().get("data") or {}).get("diff")) or []
            if isinstance(diff, dict):
                diff = list(diff.values())
            rows = []
            for x in diff:
                if not isinstance(x, dict):
                    continue
                rows.append({
                    "board_type": board_type,
                    "board_code": x.get("f12"),
                    "board_name": x.get("f14"),
                    "change_pct": _f(x.get("f3")),
                    "main_net": _f(x.get("f62")),
                    "main_net_pct": _f(x.get("f184")),
                    "super_net": _f(x.get("f66")),
                    "large_net": _f(x.get("f72")),
                    "mid_net": _f(x.get("f78")),
                    "small_net": _f(x.get("f84")),
                    "main_net_5d": _f(x.get("f164")),
                    "main_net_pct_5d": _f(x.get("f165")),
                    "main_net_10d": _f(x.get("f109")),
                    "main_net_pct_10d": _f(x.get("f257")),
                    "date": datetime.now().strftime("%Y-%m-%d"),
                })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("astock_ext sector_money_flow %s failed: %s", board_type, e)
            return pl.DataFrame()

    # ==================== financial (东财 F10 报表族) ====================

    _F10_REPORTS = {"balance": "RPT_F10_FINANCE_GBALANCE", "income": "RPT_F10_FINANCE_GINCOME",
                    "cashflow": "RPT_F10_FINANCE_GCASHFLOW", "main": "RPT_F10_FINANCE_MAINFINADATA"}

    def get_financial(self, symbol: str, report: str = "balance") -> pl.DataFrame:
        """东财 F10 三大报表/主要指标（datacenter securities 接口, source=F10）。"""
        rep = self._F10_REPORTS.get(report, self._F10_REPORTS["balance"])
        code = _normalize(symbol)
        try:
            r = self._client.get(
                "https://datacenter.eastmoney.com/securities/api/data/v1/get",
                params={
                    "reportName": rep, "columns": "ALL",
                    "quoteColumns": "", "filter": f'(SECUCODE="{code}.SH")' if symbol.upper().endswith(".SH") or code.startswith(("6", "9", "5")) else f'(SECUCODE="{code}.SZ")',
                    "pageNumber": 1, "pageSize": 12, "sortTypes": "-1",
                    "sortColumns": "REPORT_DATE" if report != "main" else "STD_REPORT_DATE",
                    "source": "F10", "client": "PC",
                },
                headers={"Referer": "https://emweb.securities.eastmoney.com/"},
                timeout=20,
            )
            result = (r.json().get("result") or {})
            data = [x for x in (result.get("data") or []) if isinstance(x, dict)]
            rows = []
            for x in data:
                rec = {k.lower(): v for k, v in x.items()}
                rec["symbol"] = symbol
                rows.append(rec)
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("astock_ext f10 %s %s failed: %s", symbol, report, e)
            return pl.DataFrame()

    def get_news(self, symbols: list[str] | None = None, limit: int = 30) -> pl.DataFrame:
        """news 数据集入口：市场级东财 7x24 快讯（个股新闻已由 eastmoney_news 提供）。"""
        return self.get_telegraph(limit)

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        symbols = symbols or ["600519.SH"]
        if dataset == "money_flow":
            df = self.get_fund_flow(symbols[0])
        elif dataset == "news":
            df = self.get_telegraph()
        elif dataset == "margin":
            df = self.get_margin(symbols[0])
        elif dataset == "block_trade":
            df = self.get_block_trade(symbols[0])
        elif dataset == "corp_events":
            df = self.get_corp_events(symbols[0])
        elif dataset == "research":
            df = self.get_reports(symbols[0])
        elif dataset == "dragon_tiger":
            df = self.get_lhb_detail(symbols[0])
        elif dataset == "anomaly":
            df = self.get_anomaly_pool()
        else:
            raise ValueError(f"astock_ext 不支持数据集: {dataset}")
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns,
            "preview": df.head(3).to_dicts() if not df.is_empty() else [],
        }

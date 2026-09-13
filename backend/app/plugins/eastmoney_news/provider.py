"""EastMoney stock news plugin via public search API (no API key required).

Uses search-api-web.eastmoney.com/search/jsonp to search stock-related news
articles. Pattern adapted from TradingAgents-CN akshare provider.
"""
from __future__ import annotations

import json
import logging
import re
import time

import httpx
import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("news",)
_URL = "https://search-api-web.eastmoney.com/search/jsonp"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://so.eastmoney.com/",
}


def _build_params(keyword: str, limit: int = 10, page: int = 1) -> dict:
    param = {
        "uid": "",
        "keyword": keyword,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": page,
                "pageSize": limit,
                "preTag": "<em>",
                "postTag": "</em>",
            }
        },
    }
    return {
        "cb": f"jQuery{int(time.time() * 1000)}",
        "param": json.dumps(param, ensure_ascii=False),
        "_": str(int(time.time() * 1000)),
    }


def _parse_jsonp(text: str) -> dict:
    text = text.strip()
    if text.startswith("jQuery"):
        start = text.index("(")
        end = text.rindex(")")
        text = text[start + 1 : end]
    return json.loads(text)


def availability():
    try:
        r = httpx.get(_URL, params=_build_params("贵州茅台", limit=1), headers=_HEADERS, timeout=5)
        r.raise_for_status()
        data = _parse_jsonp(r.text)
        articles = ((data.get("result") or {}).get("cmsArticleWebOld") or [])
        return True, f"ok ({len(articles)} articles)"
    except Exception as e:
        return False, f"东财新闻搜索不可用: {e}"


def _clean_html(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()


class EastmoneyNewsProvider:
    name = "eastmoney_news"
    builtin = True

    def __init__(self) -> None:
        self.datasets = dict.fromkeys(_DATASETS)
        self._client = httpx.Client(headers=_HEADERS, timeout=10)

    def close(self) -> None:
        self._client.close()

    def get_news(
        self,
        symbols: list[str] | None = None,
        limit: int = 10,
    ) -> pl.DataFrame:
        """Fetch news for given stock symbols (6-digit codes or full symbols)."""
        if not symbols:
            return pl.DataFrame()
        rows: list[dict] = []
        for symbol in symbols:
            code6 = "".join(ch for ch in symbol if ch.isdigit()).zfill(6)
            try:
                r = self._client.get(_URL, params=_build_params(code6, limit=limit))
                r.raise_for_status()
                data = _parse_jsonp(r.text)
                articles = ((data.get("result") or {}).get("cmsArticleWebOld") or [])
                for art in articles:
                    rows.append({
                        "symbol": symbol,
                        "title": _clean_html(art.get("title") or ""),
                        "content": _clean_html(art.get("content") or ""),
                        "source": art.get("mediaName") or "",
                        "publish_time": art.get("date") or "",
                        "url": art.get("url") or "",
                    })
            except Exception as e:
                logger.warning("eastmoney_news %s failed: %s", symbol, e)
            time.sleep(0.3)
        return pl.DataFrame(rows) if rows else pl.DataFrame()

    def get_market_news(self, limit: int = 20) -> pl.DataFrame:
        """Fetch general market news (keyword: A股)."""
        try:
            r = self._client.get(_URL, params=_build_params("A股", limit=limit))
            r.raise_for_status()
            data = _parse_jsonp(r.text)
            articles = ((data.get("result") or {}).get("cmsArticleWebOld") or [])
            rows = []
            for art in articles:
                rows.append({
                    "symbol": "",
                    "title": _clean_html(art.get("title") or ""),
                    "content": _clean_html(art.get("content") or ""),
                    "source": art.get("mediaName") or "",
                    "publish_time": art.get("date") or "",
                    "url": art.get("url") or "",
                })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:
            logger.warning("eastmoney_news market_news failed: %s", e)
            return pl.DataFrame()

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        if dataset != "news":
            raise ValueError(f"eastmoney_news 不支持数据集: {dataset}")
        symbols = symbols or ["600519"]
        df = self.get_news(symbols, limit=5)
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns,
            "preview": df.head(3).to_dicts() if not df.is_empty() else [],
        }

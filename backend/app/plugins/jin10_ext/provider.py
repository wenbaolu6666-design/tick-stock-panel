"""Jin10 (金十数据) flash plugin — 7×24 快讯第三来源。

- news: www.jin10.com/flash_newest.js (var newest = [...])

零鉴权；市场级快讯，content/title/importance。
"""
from __future__ import annotations

import json
import logging

import httpx
import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("news",)
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"}


def availability() -> tuple[bool, str]:
    try:
        r = httpx.get("https://www.jin10.com/flash_newest.js", headers=_UA, timeout=10)
        n = r.text.count('"id"')
        return True, f"ok ({n} flashes)" if n else f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, f"金十快讯不可达: {e}"


class Jin10ExtProvider:
    name = "jin10_ext"
    builtin = True

    def __init__(self) -> None:
        self.datasets = dict.fromkeys(_DATASETS)
        self._client = httpx.Client(headers=_UA, timeout=12, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    # ==================== news (金十快讯) ====================

    def get_news(self, limit: int = 50) -> pl.DataFrame:
        """金十 7×24 快讯（flash_newest.js，市场级）。"""
        try:
            r = self._client.get("https://www.jin10.com/flash_newest.js", timeout=12)
            t = r.text
            payload = t[t.index("["):t.rindex("]") + 1]
            items = json.loads(payload)
            rows = []
            for x in items:
                if not isinstance(x, dict):
                    continue
                data = x.get("data") or {}
                if not isinstance(data, dict):
                    data = {}
                content = data.get("content") or data.get("title") or ""
                if not content:
                    continue
                rows.append({
                    "title": (data.get("title") or content)[:120],
                    "content": content,
                    "publish_time": x.get("time"),
                    "importance": x.get("important"),
                    "source": "金十",
                })
                if len(rows) >= limit:
                    break
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("jin10_ext news failed: %s", e)
            return pl.DataFrame()

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        if dataset != "news":
            raise ValueError(f"jin10_ext 不支持数据集: {dataset}")
        df = self.get_news(limit=5)
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns,
            "preview": df.head(3).to_dicts() if not df.is_empty() else [],
        }

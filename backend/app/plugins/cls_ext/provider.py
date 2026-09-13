"""CLS (财联社) telegraph plugin — 按上游拆分自 astock_ext。

上游: www.cls.cn 7x24 电报 (v1 API + 本地签名 md5(sha1(sorted qs)), 零 key)。
- news: 财联社电报快讯流
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime

import httpx
import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("news",)
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"


def _signed_url(limit: int) -> str:
    params = {"appName": "CailianpressWeb", "os": "web", "sv": "7.7.5",
              "last_time": "", "refresh_type": "1", "rn": str(limit)}
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sign = hashlib.md5(hashlib.sha1(qs.encode()).hexdigest().encode()).hexdigest()
    return f"https://www.cls.cn/v1/roll/get_roll_list?{qs}&sign={sign}"


def availability():
    try:
        r = httpx.get(_signed_url(5), headers={"User-Agent": _UA, "Referer": "https://www.cls.cn/"}, timeout=10)
        n = len(((r.json().get("data") or {}).get("roll_data") or [])) if r.status_code == 200 else 0
        return True, f"ok ({n} items)" if n else f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, f"财联社不可达: {e}"


class ClsExtProvider:
    name = "cls_ext"
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

    # ==================== news (财联社电报) ====================

    def get_cls_telegraph(self, limit: int = 50) -> pl.DataFrame:
        """财联社 7x24 电报（签名版，待实测）。"""
        try:
            r = self._client.get(
                _signed_url(limit),
                headers={"User-Agent": _UA, "Referer": "https://www.cls.cn/"},
                timeout=15,
            )
            rows = []
            for it in ((r.json().get("data") or {}).get("roll_data") or []):
                if not isinstance(it, dict):
                    continue
                ts = it.get("ctime")
                t = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
                rows.append({
                    "title": it.get("title") or it.get("brief") or "",
                    "content": it.get("content") or it.get("brief") or "",
                    "publish_time": t,
                    "source": "财联社",
                })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("cls_ext telegraph failed: %s", e)
            return pl.DataFrame()

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        if dataset == "news":
            df = self.get_cls_telegraph()
        else:
            raise ValueError(f"cls_ext 不支持数据集: {dataset}")
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns,
            "preview": df.head(3).to_dicts() if not df.is_empty() else [],
        }

"""Government macro statistics plugin — 按上游拆分自 astock_ext。

上游全部为官方发布渠道:
- macro: 国家统计局 PMI (制造业/非制造业) + 人民银行社会融资规模增量

注意: 官方页面爬取 + 正则/xlsx 解析, 页面改版即失效 (脆弱源)。
All public endpoints, no API key required.
"""
from __future__ import annotations

import io
import logging
import re

import httpx
import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("macro",)
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"


def availability():
    try:
        r = httpx.get("https://www.stats.gov.cn/sj/zxfb/", timeout=12, headers={"User-Agent": _UA})
        return True, f"ok ({r.status_code})" if r.status_code == 200 else f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, f"统计局官网不可达: {e}"


class MacroGovProvider:
    name = "macro_gov"
    builtin = True

    def __init__(self) -> None:
        self.datasets = dict.fromkeys(_DATASETS)
        self._client = httpx.Client(
            headers={"User-Agent": _UA},
            timeout=20,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    # ==================== macro (统计局 PMI) ====================

    def get_macro_pmi(self) -> pl.DataFrame:
        """国家统计局 PMI（制造业/非制造业）。页面结构敏感，失败返回空。"""
        try:
            idx = self._client.get("https://www.stats.gov.cn/sj/zxfb/", timeout=20).text
            m = re.search(r'href="([^"]+)"[^>]*>([^<]*采购经理[^<]*)<', idx)
            if not m:
                return pl.DataFrame()
            url = m.group(1)
            if not url.startswith("http"):
                url = "https://www.stats.gov.cn" + url.lstrip("./")
            html = self._client.get(url, timeout=20).text

            def grab(pat):
                mm = re.search(pat, html)
                return float(mm.group(1)) if mm else None

            return pl.DataFrame([{
                "indicator": "PMI",
                "mfg_pmi": grab(r"制造业PMI为?[^0-9]*([\d.]+)%"),
                "non_mfg_pmi": grab(r"非制造业商务活动指数为?[^0-9]*([\d.]+)%"),
                "source_url": url,
            }])
        except Exception as e:  # noqa: BLE001
            logger.warning("macro_gov pmi failed: %s", e)
            return pl.DataFrame()

    # ==================== macro (央行社融) ====================

    def get_macro_social_financing(self) -> pl.DataFrame:
        """人民银行社会融资规模增量（xlsx 下载解析，失败返回空）。"""
        try:
            idx = self._client.get(
                "https://www.pbc.gov.cn/diaochatongjisi/116219/116319/index.html", timeout=20).text
            links = re.findall(r'href=["\']([^"\']+\.xlsx?)["\']', idx)
            if not links:
                return pl.DataFrame()
            url = links[-1]
            if not url.startswith("http"):
                url = "https://www.pbc.gov.cn" + url
            xb = self._client.get(url, timeout=30).content
            import pandas as pd
            sheet = pd.read_excel(io.BytesIO(xb), sheet_name=None, header=None)
            df = next(iter(sheet.values())).dropna(how="all")
            return pl.from_pandas(df).head(60) if not df.empty else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("macro_gov social_financing failed: %s", e)
            return pl.DataFrame()

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        if dataset == "macro":
            df = self.get_macro_pmi()
        else:
            raise ValueError(f"macro_gov 不支持数据集: {dataset}")
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns,
            "preview": df.head(3).to_dicts() if not df.is_empty() else [],
        }

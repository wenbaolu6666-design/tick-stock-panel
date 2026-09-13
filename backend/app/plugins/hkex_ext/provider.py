"""HKEX northbound daily-stat plugin — 港交所官方北向日统计。

- money_flow: 北向成交总额/额度余额/十大成交活跃股 (data_tab_daily_{date}c.js)

注意: HKEX 站点 TLS 握手不稳定(实测间歇 SSL EOF), 已关证书校验 + 由调用方容忍空返回。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

import httpx
import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("money_flow",)
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36",
       "Referer": "https://www.hkex.com.hk/"}


def availability() -> tuple[bool, str]:
    for _ in range(2):
        try:
            r = httpx.get(
                "https://www.hkex.com.hk/chi/csm/DailyStat/data_tab_daily_20260904c.js",
                headers=_UA, timeout=15, verify=False,
            )
            if r.status_code == 200 and "tabData" in r.text:
                return True, "ok (tabData 可取)"
            return True, f"HTTP {r.status_code}"
        except Exception:  # noqa: BLE001
            time.sleep(1)
    return False, "HKEX 不可达 (TLS 间歇失败)"


class HkexExtProvider:
    name = "hkex_ext"
    builtin = True

    def __init__(self) -> None:
        self.datasets = dict.fromkeys(_DATASETS)
        self._client = httpx.Client(headers=_UA, timeout=20, verify=False, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    # ==================== money_flow (北向日统计) ====================

    def get_money_flow(self, date: str | None = None) -> pl.DataFrame:
        """HKEX 北向日统计：成交总额/额度余额/十大成交活跃股（内容透传）。date=YYYYMMDD。"""
        d = date or datetime.now().strftime("%Y%m%d")
        last_err = ""
        for _ in range(3):
            try:
                r = self._client.get(
                    f"https://www.hkex.com.hk/chi/csm/DailyStat/data_tab_daily_{d}c.js",
                    headers=_UA, timeout=20, verify=False,
                )
                t = r.text
                payload = t[t.index("["):t.rindex("]") + 1]
                blocks = __import__("json").loads(payload)
                rows: list[dict] = []
                for block in blocks:
                    market = block.get("market")
                    for x in (block.get("content") or []):
                        if not isinstance(x, dict):
                            continue
                        rec = {str(k).lower(): v for k, v in x.items()}
                        rows.append({
                            "market": market,
                            "date": block.get("date") or d,
                            "raw": rec,
                            **{k: v for k, v in list(rec.items())[:12]},
                        })
                if rows:
                    return pl.DataFrame(rows)
                return pl.DataFrame()
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
                time.sleep(1.5)
        logger.warning("hkex_ext money_flow %s failed: %s", d, last_err)
        return pl.DataFrame()

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        if dataset != "money_flow":
            raise ValueError(f"hkex_ext 不支持数据集: {dataset}")
        df = self.get_money_flow()
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns,
            "preview": df.head(3).to_dicts() if not df.is_empty() else [],
        }

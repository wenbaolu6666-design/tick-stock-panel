"""CNINFO (巨潮资讯/深交所) plugin — 按上游拆分自 astock_ext。

上游全部为巨潮资讯/深交所互动易 (www.cninfo.com.cn / irm.cninfo.com.cn):
- announcements: 巨潮公告列表 + 互动易问答 (get_qa)

All public endpoints, no API key required.
"""
from __future__ import annotations

import logging
from datetime import datetime

import httpx
import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("announcements", "research")
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"
_CNINFO_QUERY = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_CNINFO_ORGIDS: dict | None = None


def _normalize(code: str) -> str:
    return "".join(ch for ch in code if ch.isdigit()).zfill(6)


def availability():
    try:
        r = httpx.get(
            "http://www.cninfo.com.cn/new/data/szse_stock.json", timeout=20,
            headers={"User-Agent": _UA}, follow_redirects=True,
        )
        n = len((r.json().get("stockList") or [])) if r.status_code == 200 else 0
        return True, f"ok ({n} stocks)" if n else f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, f"巨潮资讯不可达: {e}"


class CninfoExtProvider:
    name = "cninfo_ext"
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

    def _cninfo_orgid(self, code: str) -> str:
        global _CNINFO_ORGIDS
        code6 = _normalize(code)
        if CninfoExtProvider._CNINFO_ORGIDS is None:
            try:
                r = self._client.get("http://www.cninfo.com.cn/new/data/szse_stock.json", timeout=10)
                CninfoExtProvider._CNINFO_ORGIDS = {
                    str(s.get("code", "")).zfill(6): s.get("orgId", "")
                    for s in (r.json().get("stockList") or []) if isinstance(s, dict)
                }
            except Exception:  # noqa: BLE001
                CninfoExtProvider._CNINFO_ORGIDS = {}
        org = (CninfoExtProvider._CNINFO_ORGIDS or {}).get(code6)
        if org:
            return org
        prefix = "sh" if code6.startswith(("6", "5", "9")) else "sz" if code6.startswith(("0", "3", "2")) else "bj"
        return f"gs{prefix}0{code6}"

    # ==================== announcements (巨潮公告) ====================

    def get_announcements(self, symbol: str, page_size: int = 30) -> pl.DataFrame:
        """巨潮公告（标题/类型/日期/链接）。"""
        code = _normalize(symbol)
        org = self._cninfo_orgid(code)
        try:
            r = self._client.post(
                _CNINFO_QUERY,
                data={
                    "pageNum": "1", "pageSize": str(page_size), "column": "szse",
                    "tabName": "fulltext", "plate": "", "stock": f"{code},{org}",
                    "searchkey": "", "secid": "", "category": "", "trade": "",
                    "seDate": "", "sortName": "", "sortType": "", "isHLtitle": "true",
                },
                headers={"User-Agent": _UA, "Referer": "http://www.cninfo.com.cn/"},
                timeout=15,
            )
            rows = []
            for x in (r.json().get("announcements") or []):
                ts = x.get("announcementTime")
                date = str(ts)[:10] if ts else ""
                if ts and len(str(ts)) > 10:
                    date = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d")
                rows.append({
                    "symbol": symbol,
                    "title": x.get("announcementTitle", ""),
                    "date": date,
                    "url": "http://static.cninfo.com.cn/" + (x.get("adjunctUrl") or ""),
                })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("cninfo_ext announcements %s failed: %s", symbol, e)
            return pl.DataFrame()

    # ==================== announcements (互动易问答) ====================

    def get_qa(self, symbol: str, page_size: int = 20) -> pl.DataFrame:
        """互动易问答（投资者提问 + 公司回复）。待修: queryKeyboardInfo 需 keyWord 参数。"""
        code = _normalize(symbol)
        try:
            r1 = self._client.post(
                "https://irm.cninfo.com.cn/newircs/index/queryKeyboardInfo",
                data={"pageNum": "1", "pageSize": "10", "searchTypes": "11,", "searchkey": code},
                headers={"User-Agent": _UA},
                timeout=15,
            )
            d1 = r1.json().get("data") or []
            if not d1 or not isinstance(d1[0], dict):
                return pl.DataFrame()
            org_id = d1[0].get("secid")
            if not org_id:
                return pl.DataFrame()
            r2 = self._client.post(
                "https://irm.cninfo.com.cn/newircs/company/question",
                params={"_t": "1", "stockcode": code, "orgId": org_id,
                        "pageSize": str(page_size), "page": "1"},
                headers={"User-Agent": _UA},
                timeout=15,
            )
            rows = []
            for x in (r2.json().get("rows") or []):
                if not isinstance(x, dict):
                    continue
                ts = x.get("pubDate") or x.get("pubdate")
                date = str(ts)[:10] if ts else ""
                rows.append({
                    "symbol": symbol,
                    "question": (x.get("mainContent") or x.get("question") or ""),
                    "answer": (x.get("attachedContent") or x.get("answer") or ""),
                    "date": date,
                })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("cninfo_ext qa %s failed: %s", symbol, e)
            return pl.DataFrame()

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        symbols = symbols or ["600519.SH"]
        if dataset == "announcements":
            df = self.get_announcements(symbols[0])
        elif dataset == "research":
            df = self.get_ratings()
        else:
            raise ValueError(f"cninfo_ext 不支持数据集: {dataset}")
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns,
            "preview": df.head(3).to_dicts() if not df.is_empty() else [],
        }

    # ==================== research (巨潮评级/目标价) ====================

    def get_ratings(self, date: str | None = None) -> pl.DataFrame:
        """巨潮券商评级/目标价（webapi p_sysapi1089，Accept-Enckey = AES-128-CBC(unix秒)）。

        date=YYYY-MM-DD，返回当日全市场评级记录（字段透传）。
        """
        import base64
        import time as _t
        from Crypto.Cipher import AES

        d = date or datetime.now().strftime("%Y-%m-%d")
        ts = str(int(_t.time()))
        pad = 16 - len(ts) % 16
        cipher = AES.new(b"1234567887654321", AES.MODE_CBC, b"1234567887654321")
        enckey = base64.b64encode(cipher.encrypt(ts.encode() + bytes([pad]) * pad)).decode()
        try:
            r = self._client.get(
                "http://webapi.cninfo.com.cn/api/sysapi/p_sysapi1089",
                params={"tdate": d},
                headers={
                    "Accept-Enckey": enckey,
                    "Referer": "http://webapi.cninfo.com.cn/",
                    "User-Agent": _UA,
                },
                timeout=20,
            )
            dd = r.json()
            records = dd.get("records") or []
            if isinstance(records, dict):
                records = list(records.values())
            rows = []
            for x in records:
                if not isinstance(x, dict):
                    continue
                rec = {str(k).lower(): v for k, v in x.items()}
                rows.append({
                    "symbol": _normalize(str(rec.get("seccode") or rec.get("code") or "")),
                    "name": rec.get("secname"),
                    "rating": rec.get("f008v") or rec.get("f004v"),
                    "rating_change": rec.get("f007v"),
                    "analyst": rec.get("f003v"),
                    "declare_date": rec.get("declaredate"),
                    "raw": rec,
                    "date": d,
                })
            return pl.DataFrame(rows) if rows else pl.DataFrame()
        except Exception as e:  # noqa: BLE001
            logger.warning("cninfo_ext ratings %s failed: %s", d, e)
            return pl.DataFrame()

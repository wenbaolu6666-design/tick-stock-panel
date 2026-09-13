"""Fuyao (同花顺数据) special-data plugin: dragon_tiger / limit_updown / hot_list / anomaly / auction.

Reuses the fuyao plugin API key (secrets.json fuyao_api_key or FUYAO_API_KEY env).
API convention: base https://fuyao.aicubes.cn, header X-api-key, envelope
{code, message, request_id, data} — code 0 = ok, HTTP is always 200.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import httpx
import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("dragon_tiger", "limit_updown", "hot_list", "anomaly", "auction")
_BASE = "https://fuyao.aicubes.cn"


def get_api_key() -> str:
    from app import secrets_store
    return secrets_store.get_env_backed_secret("fuyao_api_key", "FUYAO_API_KEY")


def availability():
    key = get_api_key()
    if not key:
        return False, f"缺少 API Key(与 fuyao 插件共用,配置环境变量 FUYAO_API_KEY 或在 fuyao 卡片填写)"
    try:
        d = _get("/api/a-share/special-data/hot-stock-list", {"page": 1, "size": 1})
        return True, f"ok ({len(d.get('item') or [])} items)"
    except Exception as e:  # noqa: BLE001
        return False, f"同花顺特色数据不可用: {e}"


def _get(path: str, params: dict | None = None) -> dict:
    key = get_api_key()
    if not key:
        raise RuntimeError("缺少 FUYAO API Key")
    r = httpx.get(_BASE + path, params=params or {}, headers={"X-api-key": key}, timeout=30)
    r.raise_for_status()
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"fuyao {path} code={d.get('code')}: {d.get('message')}")
    return d.get("data") or {}


def _rows(payload: dict, list_key: str = "item") -> list[dict]:
    """Extract list-of-dict rows from the ApiResponse data payload."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        v = payload.get(list_key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
    return []


def _lower_keys(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append({k.lower(): v for k, v in r.items() if not isinstance(v, (list, dict))})
    return out


def _to_df(rows: list[dict]) -> pl.DataFrame:
    rows = _lower_keys(rows)
    return pl.DataFrame(rows) if rows else pl.DataFrame()


class FuyaoExtProvider:
    name = "fuyao_ext"
    builtin = True

    def __init__(self) -> None:
        self.datasets = dict.fromkeys(_DATASETS)

    def close(self) -> None:
        pass

    # ==================== dragon_tiger ====================

    def get_dragon_tiger(
        self,
        date: str | None = None,  # YYYY-MM-DD
        board_type: str = "all",  # all | org | hot_money
    ) -> pl.DataFrame:
        params: dict = {"board_type": board_type}
        if date:
            params["date"] = date
        payload = _get("/api/a-share/special-data/dragon-tiger-list", params)
        rows = payload.get("stock_items") or []
        for r in rows:
            if isinstance(r, dict):
                concepts = r.get("concept_list") or []
                r["concepts"] = ",".join(c.get("name", "") for c in concepts if isinstance(c, dict))
                r["board_type"] = payload.get("board_type") or board_type
                r["trade_date"] = payload.get("trade_date") or date
        return _to_df(rows)

    # ==================== limit_updown ====================

    def _pool(self, path: str, pool: str, date_ms: int | None, size: int = 200) -> list[dict]:
        params: dict = {"page": 1, "size": size}
        if date_ms:
            params["date_ms"] = date_ms
        payload = _get(path, params)
        rows = _rows(payload)
        for r in rows:
            r["pool"] = pool
        return rows

    def get_limit_updown(self, date: str | None = None) -> pl.DataFrame:
        """涨停/跌停/炸板 三池，pool 列区分。date: YYYY-MM-DD，缺省今天，空则回退上一交易日。"""
        candidates: list[str] = []
        if date:
            candidates.append(date)
        else:
            candidates.append(datetime.now().strftime("%Y-%m-%d"))
            candidates.append((datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"))
        df = pl.DataFrame()
        for d in candidates:
            date_ms = int(datetime.strptime(d, "%Y-%m-%d").timestamp() * 1000)
            rows: list[dict] = []
            for path, pool in (
                ("/api/a-share/special-data/limit-up-pool", "limit_up"),
                ("/api/a-share/special-data/limit-down-pool", "limit_down"),
                ("/api/a-share/special-data/limit-break-pool", "limit_break"),
            ):
                try:
                    rows.extend(self._pool(path, pool, date_ms))
                except Exception as e:  # noqa: BLE001
                    logger.warning("fuyao_ext %s failed: %s", pool, e)
            df = _to_df(rows)
            if not df.is_empty():
                break
        if not df.is_empty() and "thscode" in df.columns:
            df = df.with_columns(pl.col("thscode").alias("symbol"))
        return df

    def get_limit_ladder(self) -> pl.DataFrame:
        """近 30 个交易日连板天梯矩阵。"""
        payload = _get("/api/a-share/special-data/limit-up-ladder")
        rows = _rows(payload)
        return _to_df(rows)

    # ==================== hot_list ====================

    def get_hot_list(self, kind: str = "hot") -> pl.DataFrame:
        """kind: hot=热股榜 / skyrocket=飙升榜。"""
        path = ("/api/a-share/special-data/hot-stock-list" if kind == "hot"
                else "/api/a-share/special-data/skyrocket-list")
        payload = _get(path, {"page": 1, "size": 50})
        return _to_df(_rows(payload))

    def get_hot_history(self, date: str | None = None) -> pl.DataFrame:
        params = {"page": 1, "size": 50}
        if date:
            params["date"] = date
        payload = _get("/api/a-share/special-data/hot-stock-list-history", params)
        return _to_df(_rows(payload))

    # ==================== anomaly ====================

    def get_anomaly(self, symbols: list[str] | None = None) -> pl.DataFrame:
        """symbols 为同花顺代码列表（如 600519.SH）；空则返回全市场当日异动。"""
        if symbols:
            rows: list[dict] = []
            for sym in symbols[:50]:
                try:
                    payload = _get("/api/a-share/special-data/anomaly-analysis-stock",
                                   {"thscodes": sym})
                    for r in _rows(payload):
                        r["symbol"] = sym
                    rows.extend(_rows(payload))
                except Exception as e:  # noqa: BLE001
                    logger.warning("fuyao_ext anomaly %s failed: %s", sym, e)
            return _to_df(rows)
        payload = _get("/api/a-share/special-data/anomaly-analysis-list", {"page": 1, "size": 200})
        return _to_df(_rows(payload))

    # ==================== auction ====================

    def get_auction(self, symbols: list[str] | None = None) -> pl.DataFrame:
        """集合竞价快照。symbols: 同花顺代码列表（缺省取自 TSP instruments，限 100 只）。"""
        if not symbols:
            symbols = self._default_symbols()
        if not symbols:
            return pl.DataFrame()
        payload = _get("/api/a-share/auction/snapshot", {"thscodes": ",".join(symbols[:100])})
        df = _to_df(_rows(payload))
        if not df.is_empty() and "thscode" in df.columns:
            df = df.with_columns(pl.col("thscode").alias("symbol"))
        return df

    @staticmethod
    def _default_symbols() -> list[str]:
        """从 TSP 已同步的 instruments 取自选股池（watchlist 优先，回退前 100 只）。"""
        try:
            from app.config import settings
            import polars as pl
            p = settings.data_dir / "instruments" / "instruments.parquet"
            if p.exists():
                df = pl.read_parquet(p, columns=["symbol"])
                return df["symbol"].to_list()[:100]
        except Exception:  # noqa: BLE001
            pass
        return []

    def get_auction_benchmark(self) -> pl.DataFrame:
        payload = _get("/api/a-share/auction/short-term-benchmark")
        rows = _rows(payload)
        if not rows and isinstance(payload, dict):
            payload_flat = {k: v for k, v in payload.items() if not isinstance(v, (list, dict))}
            rows = [payload_flat] if payload_flat else []
        return _to_df(rows)

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        if dataset == "dragon_tiger":
            df = self.get_dragon_tiger()
        elif dataset == "limit_updown":
            df = self.get_limit_updown()
        elif dataset == "hot_list":
            df = self.get_hot_list()
        elif dataset == "anomaly":
            df = self.get_anomaly()
        elif dataset == "auction":
            df = self.get_auction()
        else:
            raise ValueError(f"fuyao_ext 不支持数据集: {dataset}")
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns,
            "preview": df.head(3).to_dicts() if not df.is_empty() else [],
        }

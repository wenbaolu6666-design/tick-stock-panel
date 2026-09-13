"""标的维表同步服务。

盘前 9:10 调用 tf.exchanges.get_instruments("SH"/"SZ"/"BJ", type="stock")
获取全量标的元数据，flatten ext 字段，写入 instruments.parquet。

Starter+ 盘后可用 quotes.get(universes) 顺便补充 name。
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import polars as pl

from app.tickflow.client import get_client

logger = logging.getLogger(__name__)

_EXCHANGES = ["SH", "SZ", "BJ"]


def _flatten_instruments(items: list[dict]) -> list[dict]:
    """把 SDK 返回的 Instrument 列表 flatten 成扁平行。"""
    rows = []
    for item in items:
        row = {
            "symbol": item.get("symbol"),
            "name": item.get("name"),
            "code": item.get("code"),
            "exchange": item.get("exchange"),
            "region": item.get("region"),
            "type": item.get("type"),
        }
        ext = item.get("ext") or {}
        row["listing_date"] = ext.get("listing_date")
        row["total_shares"] = ext.get("total_shares")
        row["float_shares"] = ext.get("float_shares")
        row["tick_size"] = ext.get("tick_size")
        row["limit_up"] = ext.get("limit_up")
        row["limit_down"] = ext.get("limit_down")
        rows.append(row)
    return rows


def _fetch_instruments_via_provider() -> list[dict] | None:
    """拉标的维表: 优先日K路由源, 其无 get_instruments(或失败)时按候选链找可用源。

    候选链(本地优先): tdx_quant → eastmoney_astock → mootdx → fuyao。
    全部未命中(仍应走 tickflow)时返回 None。
    标的维表跟随日K数据源(二者天然耦合, 无独立偏好项), 但日K源(如 tdx_local 读
    vipdoc 文件)未必能供维表 — 此时不得静默降级到 TickFlow free(其列表无名且含杂类)。
    """
    from app.services import preferences
    from app.data_providers import custom as custom_sources

    provider_name = preferences.get_daily_data_provider()
    candidates: list[str] = []
    if provider_name != "tickflow" and custom_sources.is_custom_provider(provider_name):
        candidates.append(provider_name)
    candidates += ["tdx_quant", "eastmoney_astock", "mootdx", "fuyao"]

    for name in candidates:
        provider = custom_sources.get_provider(name)
        if provider is None or not hasattr(provider, "get_instruments"):
            continue
        try:
            items = provider.get_instruments("stock")
        except Exception as e:  # noqa: BLE001
            logger.warning("provider %s get_instruments 失败: %s", name, e)
            continue
        if items is None:
            continue
        if hasattr(items, "to_dicts"):  # polars DataFrame → list[dict]
            items = items.to_dicts()
        if not items:
            continue
        rows = _flatten_instruments(items)
        if rows:
            # 质检: 名称覆盖率过低(如 tdx_local 读 .day 文件, 无名称) → 视为不合格, 换下一源
            named = sum(1 for r in rows if str(r.get("name") or "").strip())
            if len(rows) >= 100 and named < len(rows) * 0.5:
                logger.warning(
                    "provider %s get_instruments 名称覆盖率过低(%d/%d), 跳过换下一候选源",
                    name, named, len(rows),
                )
                continue
            logger.info("instruments via %s: %d stocks", name, len(rows))
            return rows
    return None


def sync_instruments(data_dir: Path) -> int:
    """全量同步标的维表 → data/instruments/instruments.parquet。

    返回写入的行数。
    """
    all_rows = _fetch_instruments_via_provider()
    if all_rows is None:
        # 未命中非 tickflow provider → 走 tickflow 直连
        tf = get_client()
        all_rows = []
        for ex in _EXCHANGES:
            try:
                items = tf.exchanges.get_instruments(ex, instrument_type="stock")
                if items:
                    all_rows.extend(_flatten_instruments(items))
                    logger.info("instruments %s: %d stocks", ex, len(items))
            except Exception as e:
                logger.warning("get_instruments(%s) failed: %s", ex, e)

    if not all_rows:
        return 0

    df = pl.DataFrame(all_rows)
    df = df.with_columns(pl.lit(date.today()).alias("as_of"))

    # 合并保留: 新源缺失的扩展列(float_shares/limit_up/limit_down/total_shares/name)
    # 从现有维表继承, 防止换源同步把回填过的估值/涨跌停数据清零
    out = data_dir / "instruments" / "instruments.parquet"
    if out.exists() and not df.is_empty():
        try:
            existing = pl.read_parquet(out)
            inherit = [c for c in ("float_shares", "total_shares", "limit_up", "limit_down", "name")
                       if c in existing.columns]
            for c in inherit:
                if c not in df.columns:
                    continue
                old_map = dict(zip(existing["symbol"].to_list(), existing[c].to_list()))
                vals = []
                for sym in df["symbol"].to_list():
                    v = df[c][df["symbol"].to_list().index(sym)] if c in df.columns else None
                    vals.append(v)
                # 新值全空/缺失时用旧值补
                col_vals = df[c].to_list() if c in df.columns else [None] * df.height
                merged = []
                old_by_sym = dict(zip(existing["symbol"].to_list(), existing[c].to_list()))
                for sym, v in zip(df["symbol"].to_list(), col_vals):
                    if v is None or v == "" or v == 0:
                        merged.append(old_by_sym.get(sym))
                    else:
                        merged.append(v)
                df = df.with_columns(pl.Series(c, merged))
        except Exception as e:
            logger.warning("instruments 合并保留旧扩展列失败(忽略): %s", e)

    out.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out)

    logger.info("instruments synced: %d rows → %s", df.height, out)
    return df.height


def enrich_names_from_quotes(
    data_dir: Path,
    quotes_data: list[dict],
) -> int:
    """从 quotes 响应中提取 name，更新 instruments 维表（兜底补充）。

    盘后 quotes.get(universes) 返回的数据中包含 ext.name，
    用来补充 instruments 中可能缺失的 name。
    """
    if not quotes_data:
        return 0

    # 构建 symbol → name 映射
    name_map: dict[str, str] = {}
    for q in quotes_data:
        symbol = q.get("symbol", "")
        ext = q.get("ext") or {}
        name = ext.get("name") or q.get("name", "")
        if symbol and name:
            name_map[symbol] = name

    if not name_map:
        return 0

    inst_path = data_dir / "instruments" / "instruments.parquet"
    if not inst_path.exists():
        return 0

    df = pl.read_parquet(inst_path)

    # 只更新空 name 的行
    updates = pl.DataFrame({
        "symbol": list(name_map.keys()),
        "_new_name": list(name_map.values()),
    })
    df = df.join(updates, on="symbol", how="left")
    df = df.with_columns(
        pl.when(pl.col("name").is_null() | (pl.col("name") == ""))
        .then(pl.col("_new_name"))
        .otherwise(pl.col("name"))
        .alias("name"),
    ).drop("_new_name")

    df.write_parquet(inst_path)
    logger.info("instruments name enriched from quotes: %d names", len(name_map))
    return len(name_map)

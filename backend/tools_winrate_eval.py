"""高胜率短线评估器: 横截面按日 Top-N 选股, 统计 3 日前瞻胜率 (按年分解).
用法: python _winrate_eval.py <asset_type: stock|etf>"""
import json, sys
from pathlib import Path
from datetime import date
import polars as pl
import numpy as np

DATA_DIR = Path("..", "data").resolve()

FACTORS = ["momentum_5d","momentum_10d","momentum_20d","momentum_30d","momentum_60d",
"change_pct","ma5_bias","ma10_bias","ma20_bias","ma30_bias","ma60_bias",
"ema5_bias","ema10_bias","ema20_bias","ema30_bias","ema60_bias",
"rsi_6","rsi_14","rsi_24","macd_hist","macd_dif_pct","macd_dea_pct","macd_hist_pct",
"kdj_k","kdj_d","kdj_j","boll_position","amplitude",
"vol_ratio_5d","vol_ratio_10d","vol_trend_5_10","turnover_rate","turnover_ratio_5d",
"log_amount","amount_ratio_5d","gap_return","intraday_return","close_position",
"distance_to_high_60d","distance_from_low_60d","vwap_bias","max_ret_20d","ret_skew_20d","up_days_20d"]


def _panel_range(asset_type):
    from app.services.mining_preflight import enriched_partition_dates
    ds = enriched_partition_dates(DATA_DIR, asset_type)
    from datetime import date as _date
    first, last = ds[0], ds[-1]
    first = first.isoformat() if hasattr(first, "isoformat") else str(first)
    last = last.isoformat() if hasattr(last, "isoformat") else str(last)
    return _date(*map(int, first.split("-"))), _date(*map(int, last.split("-")))

def main():
    asset_type = sys.argv[1] if len(sys.argv) > 1 else "stock"
    top_n = 3          # 小资金 2 万: 每日最多 2-3 只
    horizon = 3        # 短线 3 日

    from app.backtest.engine import BacktestEngine
    from app.backtest.factor import FactorBacktestService, FactorBatchConfig
    from app.backtest.mining_runtime import _load_compact_factor_panel
    from app.tickflow.repository import DataStore, KlineRepository

    repo = KlineRepository(DataStore(DATA_DIR))
    service = FactorBacktestService(BacktestEngine(repo))
    dates = service.engine.enriched_partition_dates(asset_type) if hasattr(service.engine, "enriched_partition_dates") else None
    cfg = FactorBatchConfig(
        factor_names=FACTORS, symbols=None,
        start=_panel_range(asset_type)[0], end=_panel_range(asset_type)[1], asset_type=asset_type,
        commission_pct=0.0, stamp_tax_pct=0.0, slippage_bps=0.0,
    )
    print("loading factor panel ...", flush=True)
    panel = _load_compact_factor_panel(service, cfg, FACTORS, expected_generation=None, cancel_check=None)
    print("panel:", panel.height, "rows", panel["date"].n_unique(), "dates", flush=True)

    # 前瞻 horizon 日收益 (按 symbol 排序后 shift)
    panel = panel.sort(["symbol", "date"])
    panel = panel.with_columns(
        (pl.col("close").shift(-horizon) / pl.col("close") - 1.0).over("symbol").alias("_fwd")
    )
    panel = panel.filter(pl.col("_fwd").is_not_null() & pl.col("close").is_not_null())

    results = []
    for f in FACTORS:
        if f not in panel.columns:
            continue
        sub = panel.select(["date", "symbol", "_fwd", f]).filter(pl.col(f).is_not_null())
        if sub.height < 50000:
            continue
        for direction in ("high", "low"):
            # 每日 Top-N
            s = sub.sort(["date", f], descending=[False, direction == "low"])
            top = s.group_by("date", maintain_order=True).head(top_n)
            stats = top.select([
                pl.col("_fwd").mean().alias("avg_ret"),
                (pl.col("_fwd") > 0).mean().alias("win_rate"),
                pl.len().alias("n"),
            ]).row(0)
            # 分年胜率
            yearly = top.with_columns(pl.col("date").cast(pl.Utf8).str.slice(0,4).alias("yr")).group_by("yr").agg([
                (pl.col("_fwd") > 0).mean().alias("win_rate"),
                pl.col("_fwd").mean().alias("avg_ret"),
                pl.len().alias("n"),
            ]).sort("yr")
            yr_win = {r["yr"]: round(r["win_rate"], 3) for r in yearly.to_dicts()}
            results.append({
                "factor": f, "direction": direction,
                "win_rate": round(stats[1], 4), "avg_ret": round(stats[0], 5),
                "n": stats[2],
                "yearly_win": yr_win,
                "min_year_win": min(yr_win.values()) if yr_win else None,
            })
    results.sort(key=lambda r: (-r["win_rate"], -(r["min_year_win"] or 0)))
    out = Path(f"_winrate_{asset_type}.json")
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"== {asset_type} top 20 by win_rate (top_{top_n}/day, {horizon}d fwd) ==")
    for r in results[:20]:
        print(json.dumps(r, ensure_ascii=False))

if __name__ == "__main__":
    main()

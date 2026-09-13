"""本地筹码分布推演插件 — 无任何外部上游 (纯本地计算, 不受任何源封 IP 影响)。

- chip: 三角分布播种 + 换手衰减递推
  输入 = TSP 已落盘 enriched 日K (high/low/close/turnover_rate, 由当前路由的日K源供给);
  算法与 akshare stock_cyq_em 同源 (东财无公开 CYQ 接口);
  唯一官方直供为 tushare cyq_perf/cyq_chips (需 5000 积分, 用户决策未采用)。
"""
from __future__ import annotations

import logging

import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("chip",)


def availability() -> tuple[bool, str]:
    try:
        from app.config import settings
        kdir = settings.data_dir / "kline_daily_enriched"
        if not kdir.exists():
            return False, "缺少 enriched 日K（先在数据页完成一次日K同步+指标计算）"
        n = len(list(kdir.glob("date=*/part.parquet")))
        return True, f"ok ({n} 个交易日分区)"
    except Exception as e:  # noqa: BLE001
        return False, f"本地筹码推演不可用: {e}"


class ChipLocalProvider:
    name = "chip_local"
    builtin = True

    def __init__(self) -> None:
        self.datasets = dict.fromkeys(_DATASETS)

    def close(self) -> None:
        pass

    # ==================== chip（本地推演 CYQ） ====================

    def get_chip(self, symbol: str, days: int = 250, decay: float = 1.5) -> pl.DataFrame:
        """筹码分布本地推演（东财无公开 CYQ 接口）。

        输入: TSP 已落盘 enriched 日K（high/low/close/turnover_rate，小数制）。
        算法: 三角分布 + 换手衰减递推（首日播种全量流通筹码）。
        返回单行汇总: 获利比例/平均成本/90与70成本区间及集中度/筹码峰。
        """
        import numpy as np
        from app.config import settings
        kdir = settings.data_dir / "kline_daily_enriched"
        if not kdir.exists():
            return pl.DataFrame()
        files = sorted(kdir.glob("date=*/part.parquet"))[-days:]
        if not files:
            return pl.DataFrame()
        dfs = []
        for fp in files:
            try:
                d = pl.read_parquet(fp)
                d = d.filter(pl.col("symbol") == symbol)
                if not d.is_empty():
                    dfs.append(d.select([c for c in ("date", "high", "low", "close", "turnover_rate") if c in d.columns]))
            except Exception:  # noqa: BLE001
                continue
        if not dfs:
            return pl.DataFrame()
        d = pl.concat(dfs).sort("date")
        # 只要求价格字段非空; 换手率缺失按 1.0% 兜底 (与列缺失时的默认一致)
        d = d.drop_nulls(subset=["high", "low", "close"]).filter(pl.col("high") > 0)
        if "turnover_rate" in d.columns:
            d = d.with_columns(pl.col("turnover_rate").fill_null(1.0))
        else:
            d = d.with_columns(pl.lit(1.0).alias("turnover_rate"))
        if d.height < 10:
            return pl.DataFrame()
        hi_arr = d["high"].to_numpy()
        lo_arr = d["low"].to_numpy()
        cl_arr = d["close"].to_numpy()
        # turnover_rate 小数制 → 百分数
        turn = (d["turnover_rate"].to_numpy() * 100.0) if "turnover_rate" in d.columns else np.full(d.height, 1.0)

        lo, hi = float(lo_arr.min()), float(hi_arr.max())
        pad = (hi - lo) * 0.02 or max(lo * 0.02, 0.01)
        grid = np.linspace(lo - pad, hi + pad, 300)
        chips = None
        for i in range(d.height):
            t = min(max(float(turn[i]) * decay, 0.0), 1.0)
            avg = (hi_arr[i] + lo_arr[i] + cl_arr[i]) / 3.0
            w = np.where(
                (grid >= lo_arr[i]) & (grid <= hi_arr[i]),
                1.0 - np.abs(grid - avg) / np.maximum(hi_arr[i] - lo_arr[i], 1e-9),
                0.0,
            )
            if w.sum() <= 0:
                continue
            if chips is None:
                chips = w.copy()
                continue
            chips = chips * (1.0 - t) + w * t
        if chips is None or chips.sum() <= 0:
            return pl.DataFrame()
        chips = chips / chips.sum()
        price = float(cl_arr[-1])
        cum = np.cumsum(chips)

        def _p(q: float) -> float:
            return float(np.interp(q, cum, grid))

        p05, p15, p85, p95 = _p(0.05), _p(0.15), _p(0.85), _p(0.95)
        peak_i = int(np.argmax(chips))
        out = {
            "symbol": symbol,
            "price": price,
            "profit_ratio": float(chips[grid <= price].sum()),
            "avg_cost": float((grid * chips).sum()),
            "cost_90_low": p05, "cost_90_high": p95,
            "cost_70_low": p15, "cost_70_high": p85,
            "concentration_90": float((p95 - p05) / (p95 + p05)) if (p95 + p05) else None,
            "concentration_70": float((p85 - p15) / (p85 + p15)) if (p85 + p15) else None,
            "peak_price": float(grid[peak_i]),
        }
        return pl.DataFrame([out])

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        symbols = symbols or ["600519.SH"]
        if dataset != "chip":
            raise ValueError(f"chip_local 不支持数据集: {dataset}")
        df = self.get_chip(symbols[0])
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": df.height,
            "columns": df.columns,
            "preview": df.head(3).to_dicts() if not df.is_empty() else [],
        }

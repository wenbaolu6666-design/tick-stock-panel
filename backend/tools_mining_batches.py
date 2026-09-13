"""挖掘批次运行器: python _run_mining_batches.py [stock-shortterm|etf-shortterm|all]"""
import json, sys, time
from pathlib import Path
from types import SimpleNamespace

DATA_DIR = Path("..", "data").resolve()
TERMINAL = {"succeeded", "succeeded_with_budget_exhausted", "failed", "cancelled"}

FACTORS = ["momentum_5d","momentum_10d","momentum_20d","momentum_30d","momentum_60d",
"change_pct","ma5_bias","ma10_bias","ma20_bias","ma30_bias","ma60_bias",
"ema5_bias","ema10_bias","ema20_bias","ema30_bias","ema60_bias",
"rsi_6","rsi_14","rsi_24","macd_hist","macd_dif_pct","macd_dea_pct","macd_hist_pct",
"kdj_k","kdj_d","kdj_j","boll_position","amplitude",
"vol_ratio_5d","vol_ratio_10d","vol_trend_5_10","turnover_rate","turnover_ratio_5d",
"log_amount","amount_ratio_5d","gap_return","intraday_return","close_position",
"distance_to_high_60d","distance_from_low_60d","vwap_bias","max_ret_20d","ret_skew_20d","up_days_20d"]

STOCK_STRATS = ["active_limit_gene","boll_breakout","breakout_new_high_60d",
                "broken_board_recovery","bullish_alignment","consecutive_limit_ups",
                "high_turnover_surge","limit_up_momentum"]
ETF_STRATS = ["boll_breakout","breakout_new_high_60d","bullish_alignment",
              "macd_golden","oversold_bounce"]

def wait_run(manager, run_id):
    while True:
        time.sleep(30)
        m = manager.store.get(run_id)
        status = m.get("status")
        print("  status:", status, flush=True)
        if status in TERMINAL:
            return m

def main():
    only = sys.argv[1] if len(sys.argv) > 1 else "all"
    from app.tickflow.repository import DataStore, KlineRepository
    from app.strategy import config as strategy_config
    from app.strategy.engine import StrategyEngine
    from app.backtest.worker import _strategy_dirs
    from app.services.mining_manager import MiningJobManager
    from app.services.mining_schedule import build_data_fingerprint

    repo = KlineRepository(DataStore(DATA_DIR))
    strategy_engine = StrategyEngine(
        strategy_dirs=_strategy_dirs(DATA_DIR),
        override_loader=lambda sid: strategy_config.load_override(DATA_DIR, sid),
    )
    app_state = SimpleNamespace(strategy_engine=strategy_engine)
    manager = MiningJobManager(DATA_DIR)

    batches = {
        "stock-shortterm": {
            "asset_type": "stock",
            "factor_names": FACTORS,
            "strategy_ids": STOCK_STRATS,
            "symbols": None, "start": None, "end": None,
            "budget_profile": "strict",
            "commission_pct": 0.0005,
            "stamp_tax_pct": 0.0005,
            "slippage_bps": 10.0,
            "correlation_threshold": 0.75,
            "max_combination_factors": 4,
            "beam_width": 32,
            "max_finalists": 8,
            "forward_horizon": 3,
        },
        "etf-shortterm": {
            "asset_type": "etf",
            "factor_names": FACTORS,
            "strategy_ids": ETF_STRATS,
            "symbols": None, "start": None, "end": None,
            "budget_profile": "balanced",
            "commission_pct": 0.0002,
            "stamp_tax_pct": 0.0,
            "slippage_bps": 10.0,
            "correlation_threshold": 0.75,
            "max_combination_factors": 4,
            "beam_width": 16,
            "max_finalists": 8,
            "forward_horizon": 3,
        },
    }
    targets = list(batches) if only == "all" else [only]

    for label in targets:
        req = batches[label]
        print(f"=== BATCH {label} starting ===", flush=True)
        try:
            fingerprint = build_data_fingerprint(repo, app_state, req)
            manifest = manager.start(req, fingerprint, force=True, source=f"manual-{label}")
            rid = manifest["run_id"]
            print(f"  run_id: {rid}", flush=True)
            final = wait_run(manager, rid)
            print(f"=== BATCH {label} FINAL: {final.get('status')} err={final.get('error')} ===", flush=True)
        except Exception as exc:
            print(f"=== BATCH {label} LAUNCH FAILED: {exc} ===", flush=True)

    print("ALL DONE", flush=True)
    manager.shutdown()

if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""高胜率短线策略筛选: 真实撮合回测 × 环境闸门 × 多因子组合 (股票+ETF).
串行 (重任务限流), 输出按 win_rate 排序的候选。
用法: python tools_strategy_screen.py"""
import json, time, httpx
from pathlib import Path

BASE = "http://127.0.0.1:3018"
OUT = Path("../strategy_screen.json").resolve()

# 2万小资金口径
STOCK_COST = {"commission_pct": 0.0005, "stamp_tax_pct": 0.0005, "slippage_bps": 10}
ETF_COST = {"commission_pct": 0.0002, "stamp_tax_pct": 0.0, "slippage_bps": 10}
GATE = {"states": ["strong", "lean_strong"]}

# 因子组合 (短线取向: 超卖反弹/放量/动量, 剔纯低波)
COMBOS = [
    ("kdj_k低", {"kdj_k": 1.0}, {"kdj_k": "low"}),
    ("rsi_14低", {"rsi_14": 1.0}, {"rsi_14": "low"}),
    ("turnover_ratio_5d放量", {"turnover_ratio_5d": 1.0}, {"turnover_ratio_5d": "high"}),
    ("momentum_5d强", {"momentum_5d": 1.0}, {"momentum_5d": "high"}),
    ("kdj_k低+放量", {"kdj_k": 1.0, "turnover_ratio_5d": 0.6}, {"kdj_k": "low", "turnover_ratio_5d": "high"}),
    ("rsi低+kdj低", {"rsi_14": 1.0, "kdj_k": 0.6}, {"rsi_14": "low", "kdj_k": "low"}),
    ("close_position高", {"close_position": 1.0}, {"close_position": "high"}),
    ("gap_return高", {"gap_return": 1.0}, {"gap_return": "high"}),
]

def run_combo(asset, start, hold, maxpos, cost, scoring, directions, gate):
    body = {
        "strategy_id": "factor_rank_research", "start": start, "end": "2026-09-09",
        "asset_type": asset, "mode": "position", "holding_days": hold,
        "max_positions": maxpos, "initial_capital": 20000,
        "entry_fill": "open_t+1", "exit_fill": "open_t+1",
        "commission_pct": cost["commission_pct"], "stamp_tax_pct": cost["stamp_tax_pct"],
        "slippage_bps": cost["slippage_bps"],
        "params": {**{"scoring": scoring, "directions": directions}, "entry_score": 70, "exit_score": 40, "top_rank": maxpos},
    }
    if gate:
        body["regime_filter"] = gate
    r = httpx.post(f"{BASE}/api/backtest/strategy/run", json=body, timeout=900)
    d = r.json()
    s = d.get("stats") or {}
    if d.get("error"):
        return {"error": d["error"][:60]}
    return {"ret": s.get("total_return"), "win": s.get("win_rate"), "n": s.get("n_trades"),
            "sharpe": s.get("sharpe"), "dd": s.get("max_drawdown"), "pf": s.get("profit_factor")}

def main():
    results = []
    with httpx.Client() as _c:  # keepalive
        pass
    for asset, start, hold, maxpos, cost in (
        ("stock", "2021-09-06", 3, 3, STOCK_COST),
        ("etf", "2025-09-02", 3, 3, ETF_COST),
    ):
        for gate_on in (True, False):
            for label, scoring, directions in COMBOS:
                tag = f"{asset}|{label}|{'闸门' if gate_on else '无闸门'}"
                t0 = time.time()
                r = run_combo(asset, start, hold, maxpos, cost, scoring, directions, GATE if gate_on else None)
                r["tag"] = tag
                results.append(r)
                line = " ".join(f"{k}={r[k]}" for k in ("ret","win","n","sharpe","dd","pf") if k in r)
                print(f"{tag}: {r.get('error','')} {line} ({time.time()-t0:.0f}s)", flush=True)
                OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n=== TOP by win_rate (n>=40) ===", flush=True)
    good = [x for x in results if x.get("win") and (x.get("n") or 0) >= 40]
    for x in sorted(good, key=lambda z: -z["win"])[:12]:
        print(f"  {x['tag']}: win={x['win']:.3f} ret={x['ret']:+.3f} sharpe={x['sharpe']} dd={x['dd']} n={x['n']}", flush=True)

if __name__ == "__main__":
    main()

"""批次收尾: 读取挖掘产物, 对因子候选按胜率重评并排序.
用法: python _eval_candidates.py <run_id> [top_n]"""
import json, sys
from datetime import date
from pathlib import Path

DATA_DIR = Path("..", "data").resolve()

def main():
    run_id = sys.argv[1]
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    import polars as pl
    run_dir = DATA_DIR / "research/mining/runs" / run_id
    cands = pl.read_parquet(run_dir / "candidates.parquet")
    folds = pl.read_parquet(run_dir / "folds.parquet")
    manifest = json.load(open(run_dir / "manifest.json", encoding="utf-8"))

    factor_rows = cands.filter(~pl.col("kind").str.contains("existing"))
    print(f"== {run_id}: {factor_rows.height} factor candidates, {folds.height} fold rows ==")
    for row in factor_rows.sort("oos_positive_fold_ratio", descending=True).head(top_n).to_dicts():
        print(json.dumps({k: row.get(k) for k in (
            "signature","name","oos_return","oos_sharpe","oos_max_drawdown",
            "oos_n_trades","oos_positive_fold_ratio","valid_folds","skipped_folds","confidence",
        )}, ensure_ascii=False))
        print("   definition:", row.get("definition_json"))

    # 策略 benchmark 的折级表现
    bench = folds.filter(pl.col("evaluation_kind") == "benchmark").filter(~pl.col("skipped"))
    if bench.height:
        print("\n== benchmark folds (valid) ==")
        agg = bench.group_by("candidate_signature").agg([
            pl.col("total_return").mean().alias("avg_ret"),
            pl.col("sharpe").mean().alias("avg_sharpe"),
            pl.col("n_trades").sum().alias("sum_trades"),
            pl.col("skipped").count().alias("folds"),
        ]).sort("avg_sharpe", descending=True)
        for row in agg.to_dicts():
            print(json.dumps(row, ensure_ascii=False))

if __name__ == "__main__":
    main()

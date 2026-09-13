# -*- coding: utf-8 -*-
"""多数据源接入后全模块兼容审计: API面 / 数据店schema / 插件层 / 能力矩阵 / ETF实时信号路径."""
import json, time, traceback
from datetime import date, timedelta
from pathlib import Path
import httpx

BASE = "http://127.0.0.1:3018"
DATA = Path("..", "data").resolve()
issues: list[str] = []
ok_count = 0

def check(label: str, fn):
    global ok_count
    try:
        r = fn()
        if r is True or (isinstance(r, tuple) and r[0]):
            ok_count += 1
            print(f"  [OK] {label}" + (f" {r[1]}" if isinstance(r, tuple) and r[1] else ""), flush=True)
        else:
            msg = r[1] if isinstance(r, tuple) else ""
            issues.append(f"{label}: {msg}")
            print(f"  [FAIL] {label} -> {msg[:160]}", flush=True)
    except Exception as e:
        issues.append(f"{label}: EXC {e}")
        print(f"  [EXC] {label} -> {str(e)[:160]}", flush=True)

print("== 1. API 面冒烟 ==", flush=True)
c = httpx.Client(timeout=60)
GETS = [
    "/api/backtest/status", "/api/backtest/factor/columns", "/api/backtest/candidates",
    "/api/backtest/mining/config", "/api/backtest/mining/runs?limit=3",
    "/api/pipeline/jobs",
    "/api/settings/data-sources", "/api/settings/capability-matrix",
    "/api/financials/status",
    "/api/monitor-rules", "/api/monitor-rules/options",
    "/api/regime/latest", "/api/regime/states", "/api/regime/coverage", "/api/regime/phases",
    "/api/rps/rotation?days=12&kind=concept", "/api/rps/rotation?days=12&kind=industry&level=2",
    "/api/ext-data", "/api/watchlist",
    "/api/strategies",
]
for path in GETS:
    def mk(p):
        def f():
            r = c.get(BASE + p)
            if r.status_code != 200:
                return (False, f"HTTP {r.status_code} {r.text[:120]}")
            body = r.json()
            return (True, str(body)[:60])
        return f
    check("GET " + path.split("?")[0], mk(path))

print("== 2. 轮动/总览/信号 数据实质 ==", flush=True)
def rot_concept():
    d = c.get(BASE + "/api/rps/rotation?days=12&kind=concept").json()
    n = d.get("concept_count", 0); dates = d.get("dates", [])
    return (n > 0 and len(dates) > 0, f"concepts={n} dates={len(dates)}")
check("概念轮动有数据", rot_concept)
def rot_ind():
    d = c.get(BASE + "/api/rps/rotation?days=12&kind=industry&level=2").json()
    n = d.get("concept_count", 0); dates = d.get("dates", [])
    return (n > 0 and len(dates) > 0, f"industries={n} dates={len(dates)}")
check("行业轮动有数据", rot_ind)
def strat_list():
    d = c.get(BASE + "/api/strategies").json()
    items = d if isinstance(d, list) else d.get("strategies") or d.get("items") or []
    return (len(items) >= 25, f"strategies={len(items)}")
check("策略库列表", strat_list)
def factor_cols():
    d = c.get(BASE + "/api/backtest/factor/columns").json()
    cols = d.get("columns") if isinstance(d, dict) else d
    return (len(cols) >= 40, f"factor cols={len(cols)}")
check("因子目录", factor_cols)

print("== 3. 数据店 schema/范围 一致性 ==", flush=True)
import polars as pl
def store_range(label, root, expect_min_date):
    parts = sorted(DATA.glob(root + "/date=*"))
    if not parts:
        issues.append(f"{label}: 无分区"); print(f"  [FAIL] {label}: 无分区", flush=True); return
    first = parts[0].name.split("=")[1]; last = parts[-1].name.split("=")[1]
    df = pl.read_parquet(parts[-1] / "part.parquet")
    n = df.height
    ok = date.fromisoformat(last) >= expect_min_date and n > 0
    print(f"  [{'OK' if ok else 'FAIL'}] {label}: {len(parts)}分区 {first}~{last} 末日{n}行", flush=True)
    if not ok:
        issues.append(f"{label}: last={last} rows={n}")
today = date.today()
recent = today - timedelta(days=4)
for label, root in (("股票日K","kline_daily"),("ETF日K","kline_etf_daily"),("指数日K","kline_index_daily"),
                    ("股票enriched","kline_daily_enriched"),("ETF enriched","kline_etf_enriched")):
    store_range(label, root, recent)
def schema_drift():
    # 同目录内分区列漂移 (如 quote_ts 只在部分分区)
    drifts = []
    for root in ("kline_daily","kline_etf_daily","kline_daily_enriched","kline_etf_enriched"):
        parts = sorted(DATA.glob(root + "/date=*/part.parquet"))
        if len(parts) < 3: continue
        base_cols = set(pl.read_parquet(parts[0]).columns)
        for p in (parts[len(parts)//2], parts[-1]):
            cols = set(pl.read_parquet(p).columns)
            if cols != base_cols:
                drifts.append(f"{root}@{p.parent.name}: +{sorted(cols-base_cols)[:3]} -{sorted(base_cols-cols)[:3]}")
    return (not drifts, "; ".join(drifts[:4]))
check("分区列一致性", schema_drift)

print("== 4. 插件层 ==", flush=True)
def plugins():
    d = c.get(BASE + "/api/settings/data-sources").json()
    ps = d.get("plugins", [])
    dead = [p["name"] for p in ps if not p.get("available")]
    return (not dead and len(ps) >= 20, f"{len(ps)}插件 不可用:{dead[:4]}")
check("23插件可用性", plugins)
def matrix():
    d = c.get(BASE + "/api/settings/capability-matrix").json()
    caps = d.get("capabilities", [])
    unusable = [x["id"] for x in caps if not x.get("usable")]
    return (len(caps) >= 32, f"{len(caps)}插槽 不可用:{unusable[:6]}")
check("32插槽矩阵", matrix)

print("== 5. 数据抽取抽检(每源一查) ==", flush=True)
def fin_status():
    d = c.get(BASE + "/api/financials/status").json()
    tables = d.get("tables", d)
    s = json.dumps(tables)
    return ("rows" in s and "0\"rows\": 0" not in s.replace("0, ", "0,\""), s[:80])
check("财务五表", fin_status)

print()
print(f"==== 审计完成: OK {ok_count}, 问题 {len(issues)} ====")
for i in issues:
    print(" -", i)
Path("../compat_audit_result.json").write_text(json.dumps({"ok": ok_count, "issues": issues}, ensure_ascii=False, indent=1), encoding="utf-8")

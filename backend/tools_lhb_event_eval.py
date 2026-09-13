# -*- coding: utf-8 -*-
"""龙虎榜事件驱动评估: 上榜次日开盘买入, 持3日, 胜率分解 (净买方向/原因/换手)."""
from pathlib import Path
import polars as pl

DATA_DIR = Path("..", "data").resolve()

def main():
    lhb = pl.read_parquet(DATA_DIR / "ext_data" / "lhb_history.parquet")
    print("lhb rows:", lhb.height, "days:", lhb["date"].n_unique(), flush=True)
    # 只留每股每日净买额最大的一条(同一股票同日多个上榜原因去重)
    lhb = lhb.with_columns(
        pl.col("date").cast(pl.Utf8).str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False).alias("date")
    ).sort(["date", "net_buy"], descending=True).unique(subset=["symbol", "date"], keep="first")
    lhb = lhb.with_columns(
        pl.when(pl.col("symbol").str.starts_with("6")).then(pl.lit("SH"))
          .when(pl.col("symbol").str.contains(r"^[849]")).then(pl.lit("BJ"))
          .otherwise(pl.lit("SZ")).alias("_sfx")
    ).with_columns(
        (pl.col("symbol") + pl.lit(".") + pl.col("_sfx")).alias("symbol_full")
    ).drop("_sfx")

    # 日线: symbol/date/open + 未来3日收盘
    daily = pl.scan_parquet(DATA_DIR / "kline_daily" / "**" / "part.parquet", missing_columns="insert", extra_columns="ignore").select(["symbol","date","open","close","amount"]).collect()
    daily = daily.filter(pl.col("date") >= pl.date(2025, 1, 1))
    daily = daily.sort(["symbol", "date"])
    daily = daily.with_columns([
        pl.col("open").shift(-1).over("symbol").alias("_next_open"),
        pl.col("close").shift(-3).over("symbol").alias("_close_p3"),
        pl.col("open").shift(-1).over("symbol").alias("_o1"),
    ])
    # T+1 开盘买, T+4 收盘卖: ret = close[t+3]/open[t+1] - 1
    daily = daily.with_columns(
        (pl.col("close").shift(-4).over("symbol") / pl.col("open").shift(-1).over("symbol") - 1).alias("_fwd")
    )

    ev = lhb.join(
        daily.select(["symbol", "date", "_fwd", "_next_open", "amount"]).rename({"symbol": "symbol_full", "date": "date", "amount": "day_amount"}),
        on=["symbol_full", "date"], how="inner",
    ).filter(pl.col("_fwd").is_not_null() & pl.col("_next_open").is_not_null())
    print("matched events:", ev.height, flush=True)

    def agg(df: pl.DataFrame, by: str) -> pl.DataFrame:
        return df.group_by(by).agg([
            pl.len().alias("n"),
            (pl.col("_fwd") > 0).mean().alias("win_rate"),
            pl.col("_fwd").mean().alias("avg_ret"),
            pl.col("_fwd").median().alias("med_ret"),
        ]).sort("win_rate", descending=True)

    print("\n== 按净买方向 ==", flush=True)
    ev2 = ev.with_columns(
        pl.when(pl.col("net_buy") > 0).then(pl.lit("净买入"))
          .when(pl.col("net_buy") < 0).then(pl.lit("净卖出"))
          .otherwise(pl.lit("持平")).alias("方向")
    )
    print(agg(ev2, "方向").to_pandas().to_string())

    print("\n== 按上榜原因(top10样本) ==", flush=True)
    print(agg(ev, "reason").filter(pl.col("n") >= 100).to_pandas().to_string()[:1600])

    print("\n== 净买入按强度(占当日成交额比) ==", flush=True)
    ev3 = ev.filter(pl.col("net_buy") > 0).with_columns(
        (pl.col("net_buy") / pl.col("day_amount")).alias("强度")
    ).with_columns(
        pl.when(pl.col("强度") > 0.15).then(pl.lit(">15%"))
          .when(pl.col("强度") > 0.08).then(pl.lit("8-15%"))
          .otherwise(pl.lit("<8%")).alias("档")
    )
    print(agg(ev3, "档").to_pandas().to_string())

    print("\n== 净买入+非ST+强度>8% 精选档 ==", flush=True)
    pick = ev3.filter(pl.col("档") != "<8%")
    s = pick.select([
        (pl.col("_fwd") > 0).mean().alias("win_rate"),
        pl.col("_fwd").mean().alias("avg_ret"),
        pl.col("_fwd").quantile(0.25).alias("p25"),
        pl.len().alias("n"),
    ]).row(0)
    print(f"win={s[0]:.3f} avg={s[1]:.4f} p25={s[2]:.4f} n={s[3]}")

if __name__ == "__main__":
    main()

"""Contract tests for the built-in mootdx plugin (no real TDX network)."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import polars as pl

from app.plugins.mootdx.provider import MootdxProvider


class _FakeQuotes:
    def __init__(self) -> None:
        self.k_calls: list[dict] = []
        self.bar_calls: list[dict] = []

    def get_k_data(self, code, start_date=None, end_date=None):
        self.k_calls.append({"code": code, "start": start_date, "end": end_date})
        idx = pd.DatetimeIndex(["2026-08-26", "2026-08-27"], name="date")
        return pd.DataFrame(
            {
                "open": [11.0, 11.1],
                "close": [11.05, 11.15],
                "high": [11.1, 11.2],
                "low": [10.9, 11.0],
                "vol": [1_612_611, 1_600_000],
                "amount": [1.78e8, 1.76e8],
            },
            index=idx,
        )

    def bars(self, symbol, frequency=9, offset=800):
        self.bar_calls.append({"symbol": symbol, "frequency": frequency, "offset": offset})
        return pd.DataFrame(columns=[])

    def stocks(self, market=0):
        rows = []
        if market == 0:
            rows = [{"code": "000001", "name": "平安银行"}, {"code": "399001", "name": "深证成指"}]
        else:
            rows = [{"code": "600519", "name": "贵州茅台"}, {"code": "510300", "name": "沪深300ETF"}]
        return pd.DataFrame(rows)

    def quotes(self, symbol=None):
        df = pd.DataFrame(
            {
                "code": symbol or [],
                "name": ["平安银行", "贵州茅台"],
                "price": [11.2, 1300.0],
                "last_close": [11.0, 1290.0],
                "open": [11.05, 1295.0],
                "high": [11.3, 1305.0],
                "low": [10.95, 1288.0],
                "vol": [1_234_500, 2_030_000],
                "amount": [1.36e8, 2.63e9],
            }
        )
        return df


def test_daily_volume_shares_to_lots():
    fake = _FakeQuotes()
    provider = MootdxProvider(client=fake)
    df = provider.get_daily(["000001.SZ"], datetime(2026, 8, 1), datetime(2026, 8, 31))
    assert not df.is_empty()
    assert set(df.columns) == {"symbol", "date", "open", "high", "low", "close", "volume", "amount"}
    # 1,612,611 shares -> 16,126 lots
    assert df["volume"].to_list() == [16126.0, 16000.0]
    assert df["symbol"].unique().to_list() == ["000001.SZ"]


def test_realtime_uses_stock_universe_and_converts_lots():
    fake = _FakeQuotes()
    provider = MootdxProvider(client=fake)
    rows = provider.get_realtime()
    assert len(rows) == 2
    assert {r["symbol"] for r in rows} == {"000001.SZ", "600519.SH"}
    # 1,234,500 shares -> 12,345 lots
    by = {r["symbol"]: r for r in rows}
    assert by["000001.SZ"]["volume"] == 12345.0
    assert abs(by["000001.SZ"]["change_pct"] - 20.0 / 1100.0) < 1e-12  # (11.2-11.0)/11.0

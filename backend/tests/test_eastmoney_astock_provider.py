"""Contract tests for the built-in eastmoney_astock plugin."""
from __future__ import annotations

from app.plugins.eastmoney_astock.provider import EastmoneyAstockProvider, _symbol_from_code


def test_symbol_from_code():
    assert _symbol_from_code("600519", "1") == "600519.SH"
    assert _symbol_from_code("000001", "0") == "000001.SZ"
    assert _symbol_from_code("920982", "0") == "920982.BJ"


def test_kline_df_daily():
    rows = [
        {"datetime": "2026-09-02", "open": 1302.8, "close": 1297.5, "high": 1303.0,
         "low": 1291.2, "volume": 20308, "amount": 2634084231},
    ]
    df = EastmoneyAstockProvider._kline_df(rows, "600519.SH", daily=True)
    assert df.columns == ["symbol", "date", "open", "high", "low", "close", "volume", "amount"]
    assert df["symbol"].to_list() == ["600519.SH"]
    assert str(df["date"][0]) == "2026-09-02"
    assert df["volume"].to_list() == [20308.0]


def test_kline_df_minute():
    rows = [
        {"datetime": "2026-09-02 09:31:00", "open": 1300.0, "close": 1300.5,
         "high": 1300.6, "low": 1299.8, "volume": 100, "amount": 130000},
    ]
    df = EastmoneyAstockProvider._kline_df(rows, "600519.SH", daily=False)
    assert "datetime" in df.columns and "symbol" in df.columns
    assert df["symbol"].to_list() == ["600519.SH"]


def test_quote_maps_fields():
    row = {"f12": "600519", "f13": "1", "f14": "贵州茅台", "f2": 1297.5, "f18": 1299.56,
           "f17": 1302.8, "f15": 1303.0, "f16": 1291.2, "f5": 20308, "f6": 2634084231,
           "f3": -0.16, "f4": -2.06}
    q = EastmoneyAstockProvider._quote(row)
    assert q["symbol"] == "600519.SH"
    assert q["last_price"] == 1297.5
    assert q["amount"] == 2634084231
    assert abs(q["change_pct"] - (-0.16 / 100)) < 1e-9

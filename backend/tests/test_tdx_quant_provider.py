"""Contract tests for the built-in tdx_quant plugin (no running TDX client)."""
from __future__ import annotations

from unittest.mock import patch

import polars as pl
import pytest

from app.plugins.tdx_quant.provider import TdxQuantProvider


def _paged(mock, page_data):
    """side_effect: return page_data for page 0, empty Value for later pages."""
    def _side(method, params, **kw):
        idx = int(params.get("stock_page_index", 0) or 0)
        if idx == 0:
            return page_data
        return {"ErrorId": "0", "Value": {}}
    mock.side_effect = _side
    return mock



def _result():
    return {
        "ErrorId": "0",
        "Value": {
            "600519.SH": {
                "Date": ["20260901", "20260902"],
                "Time": ["0", "0"],
                "Open": ["1302.80", "1297.50"],
                "High": ["1303.00", "1297.50"],
                "Low": ["1291.20", "1297.50"],
                "Close": ["1297.50", "1297.50"],
                "Volume": ["2030800", "2000000"],
                "Amount": ["263408", "260000"],
            }
        },
    }


def test_parse_kline_converts_units():
    rows = TdxQuantProvider._parse_kline(_result(), "600519.SH")
    assert len(rows) == 2
    assert rows[0]["volume"] == 20308  # shares -> lots
    assert rows[0]["amount"] == 263408 * 10000  # 万 -> 元
    assert rows[0]["date"] == "20260901"


def test_daily_df_columns():
    rows = TdxQuantProvider._parse_kline(_result(), "600519.SH")
    df = TdxQuantProvider._daily_df(rows)
    assert set(df.columns) == {"symbol", "date", "open", "high", "low", "close", "volume", "amount"}
    assert df.height == 2


def test_minute_df_builds_datetime():
    rows = [{"symbol": "600519.SH", "date": "20260902", "time": "093100",
             "open": 1297.0, "high": 1297.5, "low": 1296.5, "close": 1297.2,
             "volume": 100, "amount": 1297200}]
    df = TdxQuantProvider._minute_df(rows)
    assert df.height == 1
    assert df["datetime"][0].strftime("%H:%M:%S") == "09:31:00"


# ==================== P0: financial ====================

def _fin_result():
    return {
        "ErrorId": "0",
        "Value": {
            "600519.SH": {
                "tag_time": [20231231],
                "announce_time": [20240328],
                "FN1": [66.41], "FN4": [172.35], "FN6": [34.19],
                "FN40": [27260000.0], "FN63": [5300000.0], "FN72": [21960000.0],
                "FN107": [665600.0], "FN134": [7473000.0],
                "FN183": [18.56], "FN184": [19.16], "FN202": [91.96], "FN201": [52.49], "FN210": [19.44],
                "FN230": [14794000.0], "FN231": [9500000.0], "FN232": [7473000.0],
            }
        },
    }


@patch("app.plugins.tdx_quant.provider._call")
def test_get_financials_income(mock_call):
    _paged(mock_call, _fin_result())
    p = TdxQuantProvider()
    df = p.get_financials("income", ["600519.SH"])
    assert df.height == 1
    assert df["symbol"][0] == "600519.SH"
    assert df["revenue"][0] == pytest.approx(14794000.0)
    assert df["net_income"][0] == pytest.approx(7473000.0)
    assert df["basic_eps"][0] == pytest.approx(66.41)
    assert df["period_end"][0] == "20231231"


@patch("app.plugins.tdx_quant.provider._call")
def test_get_financials_balance_sheet(mock_call):
    _paged(mock_call, _fin_result())
    p = TdxQuantProvider()
    df = p.get_financials("balance_sheet", ["600519.SH"])
    assert df.height == 1
    assert df["total_assets"][0] == pytest.approx(27260000.0)
    assert df["total_equity"][0] == pytest.approx(21960000.0)


@patch("app.plugins.tdx_quant.provider._call")
def test_get_financials_metrics(mock_call):
    _paged(mock_call, _fin_result())
    p = TdxQuantProvider()
    df = p.get_financials("metrics", ["600519.SH"])
    assert df.height == 1
    assert df["eps"][0] == pytest.approx(66.41)
    assert df["bps"][0] == pytest.approx(172.35)
    assert df["roe"][0] == pytest.approx(34.19)
    assert df["gross_margin"][0] == pytest.approx(91.96)
    assert df["debt_to_asset_ratio"][0] == pytest.approx(19.44)


@patch("app.plugins.tdx_quant.provider._call")
def test_get_financials_cash_flow(mock_call):
    _paged(mock_call, _fin_result())
    p = TdxQuantProvider()
    df = p.get_financials("cash_flow", ["600519.SH"])
    assert df.height == 1
    assert df["net_operating_cash_flow"][0] == pytest.approx(665600.0)


@patch("app.plugins.tdx_quant.provider._call")
def test_get_financials_shares(mock_call):
    _paged(mock_call, {
        "ErrorId": "0",
        "Value": {
            "600519.SH": {
                "tag_time": [20250101],
                "FN238": [1256197800.0],
                "FN239": [1256197800.0],
            }
        },
    })
    p = TdxQuantProvider()
    df = p.get_financials("shares", ["600519.SH"])
    assert df.height == 1
    assert df["total_shares"][0] == pytest.approx(1256197800.0)
    assert df["float_shares"][0] == pytest.approx(1256197800.0)
    assert str(df["trade_date"][0]) == "2025-01-01"


def test_get_financials_invalid_table():
    p = TdxQuantProvider()
    df = p.get_financials("nonexistent", ["600519.SH"])
    assert df.is_empty()


# ==================== P1: instruments ====================

@patch("app.plugins.tdx_quant.provider._call")
def test_get_instruments(mock_call):
    mock_call.return_value = {
        "ErrorId": "0",
        "Value": [
            {"Code": "600519.SH", "Name": "贵州茅台"},
            {"Code": "000001.SZ", "Name": "平安银行"},
            {"Code": "430047.BJ", "Name": "诺思兰德"},
        ],
    }
    p = TdxQuantProvider()
    df = p.get_instruments()
    assert df.height == 3
    assert df["name"][0] == "贵州茅台"
    assert df["exchange"][0] == "SH"
    assert df["exchange"][1] == "SZ"
    assert df["exchange"][2] == "BJ"
    assert set(df.columns) == {"symbol", "name", "code", "exchange", "asset_type", "source"}


# ==================== P1: adj_factor ====================

@patch("app.plugins.tdx_quant.provider._call")
def test_get_adj_factors(mock_call):
    mock_call.return_value = {
        "ErrorId": "0",
        "Value": {
            "600519.SH": {
                "Date": ["20260101", "20260615", "20260616", "20260901"],
                "ForwardFactor": ["1.0", "1.0", "0.9091", "0.9091"],
            }
        },
    }
    p = TdxQuantProvider()
    df = p.get_adj_factors(["600519.SH"])
    assert df.height == 1
    assert df["ex_factor"][0] == pytest.approx(1.0 / 0.9091, rel=1e-4)


# ==================== P1: realtime ====================

@patch("app.plugins.tdx_quant.provider._call")
def test_get_realtime(mock_call):
    mock_call.return_value = {
        "ErrorId": "0",
        "Value": {
            "LastClose": "34.21", "Open": "33.78", "Max": "36.49",
            "Min": "32.50", "Now": "35.06", "Volume": "122881",
            "Amount": "43068.48",
        },
    }
    p = TdxQuantProvider()
    df = p.get_realtime(symbols=["600519.SH"])
    assert df.height == 1
    assert df["last_price"][0] == pytest.approx(35.06)
    assert df["prev_close"][0] == pytest.approx(34.21)
    assert df["change_pct"][0] == pytest.approx((35.06 - 34.21) / 34.21, rel=1e-4)


def test_get_realtime_empty():
    p = TdxQuantProvider()
    df = p.get_realtime(symbols=[])
    assert df.is_empty()

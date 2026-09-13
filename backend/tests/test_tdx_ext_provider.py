"""Contract tests for tdx_ext plugin (no running TDX client)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.plugins.tdx_ext.provider import TdxExtProvider


@patch("app.plugins.tdx_ext.provider._call")
def test_get_dragon_tiger(mock_call):
    mock_call.return_value = {
        "ErrorId": "0",
        "Value": {
            "600519.SH": [
                {"date": "20260901", "GP1": 1.0, "GP2": 2.0, "GP3": 3.0},
            ]
        },
    }
    p = TdxExtProvider()
    df = p.get_dragon_tiger(["600519.SH"])
    assert df.height == 1
    assert df["symbol"][0] == "600519.SH"
    assert df["gp1"][0] == pytest.approx(1.0)


@patch("app.plugins.tdx_ext.provider._call")
def test_get_limit_updown(mock_call):
    def side_effect(method, params, **kw):
        if params.get("type") == "up":
            return {"ErrorId": "0", "Value": [{"Code": "600519.SH", "Price": "1700.0", "Days": "2"}]}
        return {"ErrorId": "0", "Value": []}
    mock_call.side_effect = side_effect
    p = TdxExtProvider()
    df = p.get_limit_updown("20260903")
    assert df.height == 1
    assert df["type"][0] == "up"
    assert df["symbol"][0] == "600519.SH"


@patch("app.plugins.tdx_ext.provider._call")
def test_get_market_overview(mock_call):
    mock_call.return_value = {
        "ErrorId": "0",
        "Value": [{"date": "20260903", "SC1": 100.0, "SC33": 55.0, "SC40": 1200.0}],
    }
    p = TdxExtProvider()
    df = p.get_market_overview()
    assert df.height == 1
    assert df["sc1"][0] == pytest.approx(100.0)


@patch("app.plugins.tdx_ext.provider._call")
def test_get_consensus(mock_call):
    mock_call.return_value = {
        "ErrorId": "0",
        "Value": {"600519.SH": {"GO3": 1800.0, "GO5": 70.0, "GO23": 25.0}},
    }
    p = TdxExtProvider()
    df = p.get_consensus(["600519.SH"])
    assert df.height == 1
    assert df["go3"][0] == pytest.approx(1800.0)


@patch("app.plugins.tdx_ext.provider._call")
def test_get_sector_list(mock_call):
    mock_call.return_value = {
        "ErrorId": "0",
        "Value": [{"Code": "BK0707", "Name": "白酒"}],
    }
    p = TdxExtProvider()
    df = p.get_sector_list()
    assert df.height == 1
    assert df["sector_code"][0] == "BK0707"


@patch("app.plugins.tdx_ext.provider._call")
def test_get_sector_stocks(mock_call):
    mock_call.return_value = {"ErrorId": "0", "Value": ["600519.SH", "000858.SZ"]}
    p = TdxExtProvider()
    stocks = p.get_sector_stocks("BK0707")
    assert stocks == ["600519.SH", "000858.SZ"]

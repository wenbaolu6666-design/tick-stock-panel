"""Contract tests for fuyao_ext plugin (mocked HTTP)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.plugins.fuyao_ext.provider import FuyaoExtProvider


def _payload(items, **extra):
    return {"code": 0, "message": "", "request_id": "", "data": {"item": items, **extra}}


@patch("app.plugins.fuyao_ext.provider._get")
def test_get_dragon_tiger(mock_get):
    mock_get.return_value = {
        "board_type": "all", "trade_date": "2026-09-04",
        "stock_items": [
            {"thscode": "003040.SZ", "name": "楚天龙", "net_value": 671060551.16,
             "limit_reason": "R1+R2", "range_days": 1,
             "concept_list": [{"name": "A"}, {"name": "B"}]},
        ],
    }
    p = FuyaoExtProvider()
    df = p.get_dragon_tiger()
    assert df.height == 1
    assert df["symbol" if "symbol" in df.columns else "thscode"][0] == "003040.SZ"
    assert df["concepts"][0] == "A,B"


@patch("app.plugins.fuyao_ext.provider._get")
def test_get_limit_updown_pools(mock_get):
    def side(path, params=None):
        pool = {"limit-up-pool": "limit_up", "limit-down-pool": "limit_down",
                "limit-break-pool": "limit_break"}[path.rsplit("/", 1)[-1]]
        return {"item": [{"thscode": "600519.SH", "name": "贵州茅台", "pool": pool}]}
    with patch("app.plugins.fuyao_ext.provider._get", side_effect=lambda path, params=None: side(path, params)):
        p = FuyaoExtProvider()
        df = p.get_limit_updown(date="2026-09-04")
    assert sorted(df["pool"].unique().to_list()) == ["limit_break", "limit_down", "limit_up"]
    assert "symbol" in df.columns


@patch("app.plugins.fuyao_ext.provider._get")
def test_get_hot_list(mock_get):
    mock_get.return_value = {"item": [{"thscode": "601086.SH", "name": "国芳集团", "rank": 1}]}
    p = FuyaoExtProvider()
    df = p.get_hot_list()
    assert df.height == 1
    assert df["name"][0] == "国芳集团"


def test_get_auction_requires_symbols():
    p = FuyaoExtProvider()
    with patch.object(p, "_default_symbols", return_value=[]):
        assert p.get_auction().is_empty()


@patch("app.plugins.fuyao_ext.provider._get")
def test_get_anomaly_by_symbols(mock_get):
    mock_get.return_value = {"item": [{"reason": "XR概念"}]}
    p = FuyaoExtProvider()
    df = p.get_anomaly(["600519.SH"])
    assert df.height == 1

"""Contract tests for the built-in sina_astock adj-factor plugin."""
from __future__ import annotations

import polars as pl

from app.plugins.sina_astock.provider import SinaAstockProvider, _cumulative_to_events, _code, _prefix

# latest-first, same shape as Sina qfq endpoint (1900 sentinel included)
SAMPLE = [
    {"d": "2026-06-26", "f": 1.0},
    {"d": "2025-12-19", "f": 1.0236675985693},
    {"d": "2025-06-26", "f": 1.0410970621031},
    {"d": "2001-08-27", "f": 8.8825126710204},
    {"d": "1900-01-01", "f": 8.8825126710204},
]


def test_code_and_prefix():
    assert _prefix("600519.SH") == "sh"
    assert _prefix("000001.SZ") == "sz"
    assert _code("600519.SH") == "600519"


def test_cumulative_to_events_converts_to_single_event_ratios():
    events = _cumulative_to_events("600519.SH", SAMPLE)
    by_date = {e["trade_date"]: e["ex_factor"] for e in events}
    # event on 2026-06-26 = previous 1.0236675985693 / current 1.0
    assert abs(by_date["2026-06-26"] - 1.0236675985693) < 1e-6
    # event on 2025-12-19 = 1.0410970621031 / 1.0236675985693
    assert abs(by_date["2025-12-19"] - (1.0410970621031 / 1.0236675985693)) < 1e-6
    # sentinel removed
    assert "1900-01-01" not in by_date


class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _FakeHttp:
    def __init__(self):
        self.client = self

    def get(self, url, headers=None, timeout=None):
        return _Resp('var sh600519qfq={"data":' + __import__("json").dumps(SAMPLE) + "}")


def test_provider_get_adj_factors():
    provider = SinaAstockProvider(client=_FakeHttp())
    df = provider.get_adj_factors(["600519.SH"])
    assert isinstance(df, pl.DataFrame)
    assert "ex_factor" in df.columns
    assert df["symbol"].unique().to_list() == ["600519.SH"]


class _FinResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FinFakeHttp:
    def __init__(self):
        self.client = self
        from app.plugins.sina_astock.provider import _INCOME_MAP
        self.titles = list(_INCOME_MAP)

    def get(self, url, params=None, headers=None, timeout=None):
        if "getFinanceReport2022" in url:
            return _FinResp({
                "result": {"data": {"report_list": {
                    "20260331": {
                        "publish_date": "20260420",
                        "data": [
                            {"item_title": self.titles[0], "item_value": "100.0"},
                            {"item_title": self.titles[1], "item_value": "60.0"},
                            {"item_title": self.titles[9], "item_value": "25.0"},
                        ],
                    }
                }}}
            })
        return _FinResp({})


def test_provider_get_financials_maps_canonical_columns():
    provider = SinaAstockProvider(client=_FinFakeHttp())
    df = provider.get_financials("income", ["600519.SH"], latest_only=True)
    assert df["symbol"].to_list() == ["600519.SH"]
    assert df["period_end"].to_list() == ["2026-03-31"]
    assert df["announce_date"].to_list() == ["2026-04-20"]
    assert df["revenue"].to_list() == [100.0]
    assert df["net_income"].to_list() == [25.0]

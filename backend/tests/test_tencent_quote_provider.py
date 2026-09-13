"""Contract tests for the built-in tencent_quote plugin (no real network)."""
from __future__ import annotations

from app.plugins.tencent_quote.provider import TencentQuoteProvider, _tencent_code

_FIELDS = [
    "1", "贵州茅台", "600519", "1297.50", "1299.56", "1302.80", "20308", "10081", "10227",
    "1297.50", "7", "1297.40", "8", "1297.37", "1", "1297.33", "1", "1297.20", "1",
    "1297.54", "3", "1297.55", "2", "1297.60", "1", "1297.61", "1", "1297.66", "2", "",
    "20260902161444", "-2.06", "-0.16", "1303.00", "1291.20",
    "1297.50/20308/2634084231", "20308", "263408", "0.16", "19.92", "",
    "1303.00", "1291.20", "0.91", "16219.81", "16219.81", "6.46", "1429.52", "1169.60",
    "0.86", "9", "1297.04", "18.22", "19.70", "",
]
SAMPLE = 'v_sh600519="' + "~".join(_FIELDS) + '";'


def test_tencent_code():
    assert _tencent_code("600519.SH") == "sh600519"
    assert _tencent_code("000001.SZ") == "sz000001"


def test_parse_maps_units():
    rows = TencentQuoteProvider._parse(SAMPLE, ["600519.SH"])
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol"] == "600519.SH"
    assert r["last_price"] == 1297.5
    assert r["prev_close"] == 1299.56
    assert r["open"] == 1302.8
    assert r["high"] == 1303.0
    assert r["low"] == 1291.2
    assert r["volume"] == 20308  # already in lots
    assert r["amount"] == 263408 * 10000  # 万元 -> 元
    assert abs(r["change_pct"] - (-0.16 / 100)) < 1e-9


def test_provider_fetch_uses_batch_and_returns_rows():
    class _Resp:
        content = SAMPLE.encode("gbk")

        def raise_for_status(self):
            pass

    class _FakeClient:
        def __init__(self):
            self.urls = []

        def get(self, url, headers=None):
            self.urls.append(url)
            return _Resp()

    client = _FakeClient()
    provider = TencentQuoteProvider(client=client)
    rows = provider._fetch(["600519.SH", "000001.SZ"])
    assert len(rows) == 1
    assert "qt.gtimg.cn" in client.urls[0]

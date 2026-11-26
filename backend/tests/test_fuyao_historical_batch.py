"""Fuyao 股票批量历史日K: 原价契约、按需路由与旧接口兜底。"""
from __future__ import annotations

import itertools
from datetime import date, datetime

import httpx
import polars as pl
import pytest

from app.data_providers.base import ProviderRequestError
from app.plugins.fuyao import client as fc
from app.plugins.fuyao import provider as fp
from app.services import kline_sync
from app.tickflow.repository import DataStore, KlineRepository

DAY = date(2026, 11, 6)
START = fp._ms_of_date(DAY)


def _bar(day=DAY, close=11.63):
    return {
        "date_ms": fp._ms_of_date(day), "open_price": close - 0.1,
        "high_price": close + 0.2, "low_price": close - 0.2,
        "close_price": close, "volume": 1612611, "turnover": 18754665.93,
    }


def _payload(groups, **changes):
    return {"code": 0, "data": {"interval": "1d", "adjust": "none", "item": groups, **changes}}


def _group(symbol, bars=None):
    return {"thscode": symbol, "timestamp": START, "item": [_bar()] if bars is None else bars}


def _http_client(monkeypatch, payload, status=200):
    calls = []
    real_client = httpx.Client

    def respond(request):
        calls.append(request)
        if callable(payload):
            return payload(request)
        return httpx.Response(status, json=payload)

    monkeypatch.setattr(fc.httpx, "Client", lambda **kw: real_client(
        **kw, transport=httpx.MockTransport(respond),
    ))
    return fc.FuyaoClient(api_key="test-key"), calls


def test_client_batch_preserves_group_identity_and_empty_vs_missing(monkeypatch):
    payload = _payload([_group("600519.SH", []), _group("000001.SZ")])
    client, calls = _http_client(monkeypatch, payload)
    try:
        result = client.historical_kline_batch(["000001.SZ", "600519.SH", "000002.SZ"], START, START)
    finally:
        client.close()
    assert result == {"600519.SH": [], "000001.SZ": [_bar()]}
    assert "000002.SZ" not in result
    assert calls[0].url.path == "/api/a-share/prices/historical-batch"
    assert dict(calls[0].url.params) == {
        "thscodes": "000001.SZ,600519.SH,000002.SZ", "interval": "1d",
        "adjust": "none", "start": str(START), "end": str(START),
    }
    assert calls[0].headers["X-api-key"] == "test-key"


def test_client_batch_enforces_ten_symbol_limit_without_truncating(monkeypatch):
    client, calls = _http_client(monkeypatch, _payload([]))
    try:
        assert client.historical_kline_batch([], START, START) == {}
        client.historical_kline_batch([f"{i:06d}.SZ" for i in range(10)], START, START)
        with pytest.raises(fc.FuyaoError, match="10"):
            client.historical_kline_batch([f"{i:06d}.SZ" for i in range(11)], START, START)
    finally:
        client.close()
    assert len(calls) == 1
    assert len(calls[0].url.params["thscodes"].split(",")) == 10


@pytest.mark.parametrize("changes", [
    {"adjust": "forward"}, {"adjust": "backward"}, {"adjust": None},
    {"interval": "1w"}, {"interval": None}, {"item": None}, {"item": {}},
])
def test_client_batch_rejects_wrong_price_contract(monkeypatch, changes):
    client, _ = _http_client(monkeypatch, _payload([_group("000001.SZ")], **changes))
    try:
        with pytest.raises(fc.FuyaoError):
            client.historical_kline_batch(["000001.SZ"], START, START)
    finally:
        client.close()


@pytest.mark.parametrize("groups", [
    [None], [_group("600519.SH")], [_group("000001.SZ"), _group("000001.SZ")],
    [{"thscode": "000001.SZ", "item": None}],
    [_group("000001.SZ", [None])],
    [_group("000001.SZ", [{"date_ms": START}])],
    [_group("000001.SZ", [_bar(date(2026, 11, 5))])],
])
def test_client_batch_rejects_malformed_or_unrequested_rows(monkeypatch, groups):
    client, _ = _http_client(monkeypatch, _payload(groups))
    try:
        with pytest.raises(fc.FuyaoError):
            client.historical_kline_batch(["000001.SZ"], START, START)
    finally:
        client.close()


@pytest.mark.parametrize(("status", "payload"), [
    (403, {"code": 2002}), (404, {}), (200, {"code": 4001, "message": "限频", "data": None}),
    (200, {"code": 0, "data": None}),
])
def test_client_batch_errors_are_not_successful_empty_data(monkeypatch, status, payload):
    client, _ = _http_client(monkeypatch, payload, status)
    try:
        with pytest.raises(fc.FuyaoError):
            client.historical_kline_batch(["000001.SZ"], START, START)
    finally:
        client.close()


def test_client_batch_network_failure_is_explicit(monkeypatch):
    def respond(request):
        raise httpx.ConnectError("connection lost", request=request)

    client, _ = _http_client(monkeypatch, respond)
    try:
        with pytest.raises(fc.FuyaoError, match="网络请求失败"):
            client.historical_kline_batch(["000001.SZ"], START, START)
    finally:
        client.close()


def test_provider_recent_window_probes_dump_then_uses_raw_single_api(monkeypatch):
    def respond(request):
        if request.url.path.endswith("/download-url"):
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"code": 0, "data": {"item": [_bar()]}})

    client, calls = _http_client(monkeypatch, respond)
    provider = _provider(monkeypatch, client)
    provider._daily_dump_info = lambda: None
    try:
        result = provider.get_daily(
            ["000001.SZ"], datetime(2026, 11, 6), datetime(2026, 11, 6)
        )
    finally:
        client.close()
    assert result["close"].to_list() == [11.63]
    assert result["volume"].to_list() == [16126.0]
    assert [r.url.path for r in calls] == [
        "/api/dump/market-dumps/daily-k-10d/download-url",
        "/api/a-share/prices/historical",
    ]
    assert calls[-1].url.params["adjust"] == "none"


class _BatchClient:
    def __init__(self, bars, *, missing=(), errors=(), batch_error=False):
        self.bars = bars
        self.missing = set(missing)
        self.errors = set(errors)
        self.batch_error = batch_error
        self.batch_calls = []
        self.single_calls = []

    def historical_kline_batch(self, symbols, start_ms, end_ms):
        assert len(symbols) <= 10
        self.batch_calls.append((list(symbols), start_ms, end_ms))
        if self.batch_error:
            raise fc.FuyaoError("批量端点不可用")
        return {symbol: [b for b in self.bars.get(symbol, []) if start_ms <= b["date_ms"] <= end_ms]
                for symbol in reversed(symbols) if symbol not in self.missing}

    def historical_kline(self, symbol, start_ms, end_ms, adjust="none"):
        self.single_calls.append((symbol, start_ms, end_ms, adjust))
        if symbol in self.errors:
            raise fc.FuyaoError("旧接口无权限")
        return [b for b in self.bars.get(symbol, []) if start_ms <= b["date_ms"] <= end_ms]

    def dump_download_url(self, dump_kind):
        raise fc.FuyaoError("测试环境无 dump")


def _provider(monkeypatch, client):
    provider = fp.FuyaoProvider()
    provider._client = client
    monkeypatch.setattr(fp, "_HIST_INTERVAL_S", 0)
    return provider


def _no_dump(*args):
    raise fc.FuyaoError("测试环境无 dump")


def test_small_query_uses_single_api_and_preserves_raw_price_units_and_date(monkeypatch):
    bars = {"000001.SZ": [_bar(close=11.63)], "600519.SH": [_bar(close=1480.12)]}
    client = _BatchClient(bars)
    provider = _provider(monkeypatch, client)
    provider._ensure_dump = _no_dump  # type: ignore[assignment]
    provider._daily_dump_info = lambda: None
    progress = []
    df = provider.get_daily(list(bars), datetime(2026, 11, 6), datetime(2026, 11, 6),
                            on_chunk_done=lambda cur, total: progress.append((cur, total)))
    assert df.columns == ["symbol", "date", "open", "high", "low", "close", "volume", "amount"]
    assert df.schema["date"] == pl.Date
    assert dict(zip(df["symbol"], df["close"], strict=True)) == {"000001.SZ": 11.63, "600519.SH": 1480.12}
    assert df["volume"].to_list() == [16126.0, 16126.0]
    assert df["amount"].to_list() == [18754665.93, 18754665.93]
    assert df["date"].to_list() == [DAY, DAY]
    assert client.single_calls == [
        (symbol, START, START, "none") for symbol in bars
    ]
    assert client.batch_calls == []
    assert progress == [(1, 1)]


def test_duplicate_symbols_are_fetched_and_emitted_once(monkeypatch):
    client = _BatchClient({"000001.SZ": [_bar()], "600519.SH": [_bar()]})
    provider = _provider(monkeypatch, client)
    provider._ensure_dump = _no_dump  # type: ignore[assignment]
    provider._daily_dump_info = lambda: None
    df = provider.get_daily(["600519.SH", "000001.SZ", "600519.SH"], datetime(2026, 11, 6), datetime(2026, 11, 6))
    assert [call[0] for call in client.single_calls] == ["600519.SH", "000001.SZ"]
    assert client.batch_calls == []
    assert df.height == 2


@pytest.mark.parametrize("count", [10, 11, 51])
def test_api_fallback_is_chunked_and_frames_remain_bounded(monkeypatch, count):
    symbols = [f"{i + 1:06d}.SZ" for i in range(count)]
    client = _BatchClient({symbol: [_bar()] for symbol in symbols})
    provider = _provider(monkeypatch, client)
    provider._ensure_dump = _no_dump  # type: ignore[assignment]
    provider._daily_dump_info = lambda: None
    progress = []
    frames = list(provider.iter_daily(symbols, datetime(2026, 10, 1), datetime(2026, 11, 6),
                                     on_chunk_done=lambda cur, total: progress.append((cur, total))))
    assert [call[0] for call in client.single_calls] == symbols
    assert client.batch_calls == []
    assert max(frame.height for frame in frames) <= fp._HIST_SYMBOL_BATCH
    assert sum(frame.height for frame in frames) == count
    assert progress[-1] == (len(frames), len(frames))


def test_single_api_queries_each_symbol_and_keeps_empty_series(monkeypatch):
    client = _BatchClient({"000001.SZ": [_bar()], "600519.SH": [_bar(close=1480.12)]})
    provider = _provider(monkeypatch, client)
    provider._ensure_dump = _no_dump  # type: ignore[assignment]
    provider._daily_dump_info = lambda: None
    df = provider.get_daily(
        ["000001.SZ", "600519.SH"], datetime(2026, 11, 6), datetime(2026, 11, 6)
    )
    assert df.height == 2
    assert [c[0] for c in client.single_calls] == ["000001.SZ", "600519.SH"]
    assert client.batch_calls == []
    assert all(c[3] == "none" for c in client.single_calls)

    empty = _BatchClient({"000001.SZ": []})
    empty_provider = _provider(monkeypatch, empty)
    empty_provider._ensure_dump = _no_dump  # type: ignore[assignment]
    empty_provider._daily_dump_info = lambda: None
    result = empty_provider.get_daily(
        ["000001.SZ"], datetime(2026, 11, 6), datetime(2026, 11, 6)
    )
    assert result.is_empty()
    assert [c[0] for c in empty.single_calls] == ["000001.SZ"]


def test_settings_daily_preview_surfaces_terminal_fallback_error(monkeypatch):
    client = _BatchClient({}, missing=["000001.SZ"], errors=["000001.SZ"])
    result = _provider(monkeypatch, client).test_dataset("daily", ["000001.SZ"])
    assert result["rows"] == 0
    assert "000001.SZ" in result["error"]
    assert "旧接口无权限" in result["error"]


def test_deep_single_api_window_splits_without_overlap_or_duplicate_days(monkeypatch):
    client = _BatchClient({"000001.SZ": [_bar(date(2001, 1, 1)), _bar()]})
    provider = _provider(monkeypatch, client)
    provider._ensure_dump = _no_dump  # type: ignore[assignment]
    provider._daily_dump_info = lambda: None
    df = provider.get_daily(["000001.SZ"], datetime(2001, 1, 1), datetime(2026, 11, 6))
    assert len(client.single_calls) == 3
    assert all(end - start < fp._HIST_MAX_SPAN_MS for _, start, end, _ in client.single_calls)
    assert all(a[2] + 1 == b[1] for a, b in itertools.pairwise(client.single_calls))
    assert df["date"].to_list() == [date(2001, 1, 1), DAY]


@pytest.mark.parametrize("asset_type", ["index", "etf"])
def test_non_stock_daily_routing_is_unchanged(monkeypatch, asset_type):
    client = _BatchClient({})
    provider = _provider(monkeypatch, client)
    assert provider.get_daily(["000001.SH"], None, None, asset_type=asset_type).is_empty()
    assert client.batch_calls == client.single_calls == []


def test_bulk_recent_query_keeps_dump_route(monkeypatch):
    symbols = [f"{i + 1:06d}.SZ" for i in range(11)]
    client = _BatchClient({})
    provider = _provider(monkeypatch, client)
    dump = pl.DataFrame([{"thscode": s, "adjusted": "none", **_bar()} for s in symbols])
    provider._daily10_for_window = lambda *_: dump
    provider._daily_dump_info = lambda: None
    result = provider.get_daily(symbols, datetime(2026, 11, 6), datetime(2026, 11, 6))
    assert result.height == len(symbols)
    assert client.batch_calls == client.single_calls == []


def test_terminal_fallback_failure_rolls_back_staging_and_keeps_existing_data(monkeypatch, tmp_path):
    symbols = [f"{i + 1:06d}.SZ" for i in range(51)]
    client = _BatchClient({symbol: [_bar()] for symbol in symbols}, missing=[symbols[-1]], errors=[symbols[-1]])
    provider = _provider(monkeypatch, client)
    provider._ensure_dump = _no_dump  # type: ignore[assignment]
    provider._daily_dump_info = lambda: None
    monkeypatch.setattr(kline_sync.preferences, "get_daily_data_provider", lambda: "fuyao")
    from app.data_providers import custom
    monkeypatch.setattr(custom, "provider_has_dataset", lambda *_: True)
    monkeypatch.setattr(custom, "get_provider", lambda _: provider)
    repo = KlineRepository(DataStore(tmp_path))
    target = tmp_path / "kline_daily" / f"date={DAY}" / "part.parquet"
    target.parent.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600519.SH"], "date": [DAY], "close": [1480.12]}).write_parquet(target)
    before = target.read_bytes()
    with pytest.raises(ProviderRequestError, match=symbols[-1]):
        kline_sync.sync_and_persist_daily_batch(symbols, repo, object(),
                                               start_date=datetime(2026, 10, 1), end_date=datetime(2026, 11, 6))
    assert len(client.single_calls) == 51
    assert client.batch_calls == []
    assert target.read_bytes() == before
    assert not (tmp_path / ".daily_sync_staging").exists()


def _unknown_symbol_response(request):
    unknown = "000004.SZ"
    if "/api/dump/" in request.url.path:
        return httpx.Response(404, json={})
    if request.url.path.endswith("/historical") and request.url.params["thscode"] != unknown:
        bar = {**_bar(), "date_ms": int(request.url.params["start"])}
        return httpx.Response(200, json={"code": 0, "data": {"item": [bar]}})
    return httpx.Response(200, json={"code": 1002, "message": f"Unknown thscode: {unknown}"})


def test_unknown_symbol_does_not_discard_other_stocks(monkeypatch, caplog):
    client, calls = _http_client(monkeypatch, _unknown_symbol_response)
    try:
        df = _provider(monkeypatch, client).get_daily(
            ["000004.SZ", "000001.SZ"], datetime(2026, 11, 6), datetime(2026, 11, 6),
        )
    finally:
        client.close()
    assert df["symbol"].to_list() == ["000001.SZ"]
    assert df["close"].to_list() == [11.63]
    assert df["volume"].to_list() == [16126.0]
    assert "000004.SZ" in caplog.text and "未覆盖" in caplog.text
    api_calls = [r for r in calls if r.url.path.endswith("/historical")]
    assert len(api_calls) == 2  # 未覆盖标的跳过, 不连坐同一批次中的其他股票。


def test_unknown_symbol_is_not_retried_in_later_history_windows(monkeypatch):
    client, calls = _http_client(monkeypatch, _unknown_symbol_response)
    try:
        df = _provider(monkeypatch, client).get_daily(
            ["000004.SZ", "000001.SZ"], datetime(1990, 1, 1), datetime(2026, 11, 6),
        )
    finally:
        client.close()
    assert set(df["symbol"]) == {"000001.SZ"}
    api_calls = [r for r in calls if r.url.path.endswith("/historical")]
    unknown_calls = [r for r in api_calls if r.url.params.get("thscode") == "000004.SZ"]
    known_calls = [r for r in api_calls if r.url.params.get("thscode") == "000001.SZ"]
    assert len(unknown_calls) == 1
    assert len(known_calls) == 4  # 1990–2026 共四个 10 年窗口; 未知标的后续窗口不重试


@pytest.mark.parametrize(("code", "message"), [
    (1002, "invalid start parameter"),
    (1002, "Unknown thscode: 600519.SH"),
    (1001, "Unknown thscode: 000004.SZ"),
    (4001, "频率超限"),
])
def test_other_errors_are_not_misclassified_as_unsupported_symbols(monkeypatch, code, message):
    client, _ = _http_client(monkeypatch, {"code": code, "message": message})
    try:
        with pytest.raises(ProviderRequestError, match="补查失败"):
            _provider(monkeypatch, client).get_daily(
                ["000004.SZ"], datetime(2026, 11, 6), datetime(2026, 11, 6),
            )
    finally:
        client.close()


@pytest.mark.parametrize("code", [1002, "1002"])
def test_single_history_retains_structured_unknown_symbol_error(monkeypatch, code):
    client, _ = _http_client(monkeypatch, {"code": code, "message": "Unknown thscode: 000004.SZ"})
    try:
        with pytest.raises(fc.FuyaoUnknownSymbolError) as caught:
            client.historical_kline("000004.SZ", START, START)
    finally:
        client.close()
    assert caught.value.symbol == "000004.SZ"
    assert "Unknown thscode: 000004.SZ" in str(caught.value)


def test_unknown_symbol_preserves_local_history_and_reports_zero_rows(monkeypatch, tmp_path):
    from polars.testing import assert_frame_equal

    client, _ = _http_client(monkeypatch, _unknown_symbol_response)
    repo = KlineRepository(DataStore(tmp_path))
    provider = _provider(monkeypatch, client)
    monkeypatch.setattr(kline_sync.preferences, "get_daily_data_provider", lambda: "fuyao")
    monkeypatch.setattr("app.data_providers.custom.provider_has_dataset", lambda *_: True)
    monkeypatch.setattr("app.data_providers.custom.get_provider", lambda _: provider)
    target = tmp_path / "kline_daily" / f"date={DAY}" / "part.parquet"
    target.parent.mkdir(parents=True)
    old = pl.DataFrame({"symbol": ["000004.SZ"], "date": [DAY], "close": [9.0]})
    old.write_parquet(target)
    zero, failed = [], []
    try:
        written = kline_sync.sync_and_persist_daily_batch(
            ["000004.SZ", "000001.SZ"], repo, object(),
            start_date=datetime(2026, 11, 6), end_date=datetime(2026, 11, 6),
            zero_row_out=zero, failed_out=failed,
        )
    finally:
        client.close()
    assert written == 1 and zero == ["000004.SZ"] and failed == []
    stored = pl.concat([pl.read_parquet(p) for p in target.parent.glob("*.parquet")], how="diagonal_relaxed")
    assert set(stored["symbol"]) == {"000004.SZ", "000001.SZ"}
    assert_frame_equal(stored.filter(pl.col("symbol") == "000004.SZ").select(old.columns), old)

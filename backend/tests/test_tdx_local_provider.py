"""Contract tests for the local TDX vipdoc .day daily plugin."""
from __future__ import annotations

import struct
from datetime import datetime

import polars as pl

import app.plugins.tdx_local.provider as mod
from app.plugins.tdx_local.provider import TdxLocalProvider


def _write_day(path, records):
    import os
    os.makedirs(path.parent, exist_ok=True)
    with open(path, "wb") as f:
        for r in records:
            f.write(struct.pack("<IIIIIfII", r[0], r[1], r[2], r[3], r[4], r[5], r[6], 0))


def test_read_day_converts_lots_and_filters(monkeypatch, tmp_path):
    root = tmp_path / "vipdoc"
    monkeypatch.setattr(mod, "_ROOT", root)
    p = root / "sh" / "lday" / "sh600519.day"
    _write_day(p, [
        (20260824, 127101, 131380, 127033, 130466, 6.29e9, 4843957),
        (20260825, 131189, 131700, 130111, 130400, 2.75e9, 2111118),
    ])
    provider = TdxLocalProvider()
    df = provider.get_daily(["600519.SH"], datetime(2026, 8, 25), datetime(2026, 8, 25))
    assert df.height == 1
    assert df["symbol"].to_list() == ["600519.SH"]
    assert df["volume"].to_list() == [21111]  # 2,111,118 shares -> 21,111 lots
    assert df["close"].to_list() == [1304.0]


def test_availability_ok_when_day_files_exist(monkeypatch, tmp_path):
    root = tmp_path / "vipdoc"
    monkeypatch.setattr(mod, "_ROOT", root)
    (root / "sh" / "lday").mkdir(parents=True)
    (root / "sh" / "lday" / "sh000001.day").write_bytes(b"\x00" * 32)
    ok, _ = mod.availability()
    assert ok

"""Contract tests for eastmoney_news plugin."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.plugins.eastmoney_news.provider import EastmoneyNewsProvider, _parse_jsonp


def test_parse_jsonp():
    raw = 'jQuery1234567890({"result": {"cmsArticleWebOld": [{"title": "test"}]}});'
    data = _parse_jsonp(raw)
    assert data["result"]["cmsArticleWebOld"][0]["title"] == "test"


def test_parse_jsonp_bare():
    raw = '{"result": {}}'
    data = _parse_jsonp(raw)
    assert data == {"result": {}}


@patch("app.plugins.eastmoney_news.provider.httpx.Client")
def test_get_news(mock_client_cls):
    mock_response = MagicMock()
    mock_response.text = 'jQuery1({"result": {"cmsArticleWebOld": [{"title": "<em>茅台</em>大涨", "content": "内容", "mediaName": "证券时报", "date": "2026-09-03 10:00:00", "url": "https://example.com"}]}})'
    mock_response.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.get.return_value = mock_response
    mock_client_cls.return_value = mock_client

    p = EastmoneyNewsProvider()
    # Override client to use mock
    p._client = mock_client
    df = p.get_news(["600519"], limit=1)
    assert df.height == 1
    assert df["symbol"][0] == "600519"
    assert "茅台" in df["title"][0]
    assert "<em>" not in df["title"][0]  # HTML cleaned

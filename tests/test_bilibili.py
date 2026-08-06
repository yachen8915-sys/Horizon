from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from src.models import BilibiliConfig
from src.scrapers.bilibili import BilibiliScraper


def test_bilibili_search_maps_public_metrics_and_filters_exact_author() -> None:
    payload = {
        "code": 0,
        "data": {
            "result": [
                {
                    "bvid": "BV1wanted",
                    "title": "<em class=\"keyword\">AI</em> 新玩法",
                    "description": "可直接录屏演示",
                    "author": "数字生命卡兹克",
                    "mid": 313468110,
                    "pubdate": 1785479400,
                    "play": 12000,
                    "like": 800,
                    "review": 90,
                    "favorites": 500,
                    "video_review": 20,
                },
                {
                    "bvid": "BV1noise",
                    "title": "提到了数字生命卡兹克",
                    "description": "noise",
                    "author": "其他账号",
                    "mid": 1,
                    "pubdate": 1785479400,
                    "play": 999999,
                },
            ]
        },
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["search_type"] == "video"
        assert request.url.params["order"] == "pubdate"
        return httpx.Response(200, json=payload)

    config = BilibiliConfig.model_validate(
        {
            "enabled": True,
            "queries": [
                {
                    "query": "数字生命卡兹克",
                    "author": "数字生命卡兹克",
                    "profile": "pangmen-topic-radar",
                }
            ],
        }
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        items = asyncio.run(
            BilibiliScraper(config, client).fetch(
                datetime(2026, 7, 25, tzinfo=timezone.utc)
            )
        )
    finally:
        asyncio.run(client.aclose())

    assert [item.id for item in items] == ["bilibili:video:BV1wanted"]
    item = items[0]
    assert item.title == "AI 新玩法"
    assert item.author == "数字生命卡兹克"
    assert item.metadata["engagement"] == {
        "views": 12000,
        "likes": 800,
        "comments": 90,
        "favorites": 500,
        "danmaku": 20,
    }


def test_bilibili_search_drops_items_older_than_requested_window() -> None:
    payload = {
        "code": 0,
        "data": {
            "result": [
                {
                    "bvid": "BV1old",
                    "title": "old",
                    "author": "AI磊叔",
                    "mid": 2,
                    "pubdate": 1700000000,
                    "play": 100,
                }
            ]
        },
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    config = BilibiliConfig.model_validate(
        {"enabled": True, "queries": [{"query": "AI磊叔", "author": "AI磊叔"}]}
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        items = asyncio.run(
            BilibiliScraper(config, client).fetch(
                datetime(2026, 8, 1, tzinfo=timezone.utc)
            )
        )
    finally:
        asyncio.run(client.aclose())

    assert items == []


def test_bilibili_retries_one_412_with_configured_delay(monkeypatch) -> None:
    attempts = 0
    delays = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(412, text="rate limited")
        return httpx.Response(200, json={"code": 0, "data": {"result": []}})

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("src.scrapers.bilibili.asyncio.sleep", fake_sleep)
    config = BilibiliConfig.model_validate(
        {
            "enabled": True,
            "retry_delay_seconds": 2.5,
            "request_interval_seconds": 0,
            "queries": [{"query": "AI"}],
        }
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        items = asyncio.run(
            BilibiliScraper(config, client).fetch(
                datetime(2026, 8, 1, tzinfo=timezone.utc)
            )
        )
    finally:
        asyncio.run(client.aclose())

    assert items == []
    assert attempts == 2
    assert delays == [2.5]

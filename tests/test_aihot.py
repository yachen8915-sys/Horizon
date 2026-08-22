import asyncio
from datetime import datetime, timezone

import httpx

from src.models import AIHotConfig
from src.scrapers.aihot import AIHotScraper


def test_aihot_maps_dual_links_and_x_source_kind():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/hot-topics"):
            return httpx.Response(200, json={"topics": []})
        return httpx.Response(200, json={"items": [{
            "id": "item-1", "title": "X上的工作流", "summary": "摘要",
            "source": {"name": "作者"}, "links": {
                "aihot": "https://aihot.virxact.com/items/item-1",
                "original": "https://x.com/user/status/1",
            }, "publishedAt": "2026-08-06T01:00:00Z", "category": "tip"
        }]})

    cfg = AIHotConfig.model_validate({"enabled": True, "fetch_24h": True, "fetch_7d": False, "fetch_hot_topics": False, "request_interval_seconds": 0})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        items = asyncio.run(AIHotScraper(cfg, client).fetch(datetime(2026, 8, 5, tzinfo=timezone.utc)))
    finally:
        asyncio.run(client.aclose())
    assert len(items) == 1
    assert items[0].metadata["source_kind"] == "X 推文"
    assert items[0].metadata["aihot_url"].startswith("https://aihot")


def test_aihot_all_mode_routes_technical_categories_and_deduplicates_modes():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "model-1",
                        "title": "新模型发布",
                        "summary": "官方模型更新说明",
                        "source": {"name": "AI 媒体"},
                        "links": {"original": "https://example.com/model-1"},
                        "publishedAt": "2026-08-06T01:00:00Z",
                        "category": "ai-models",
                        "score": 72,
                    }
                ]
            },
        )

    cfg = AIHotConfig.model_validate(
        {
            "enabled": True,
            "fetch_24h": True,
            "fetch_7d": False,
            "fetch_hot_topics": False,
            "fetch_all_24h": True,
            "request_interval_seconds": 0,
        }
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        scraper = AIHotScraper(cfg, client)
        items = asyncio.run(
            scraper.fetch(
                datetime(2026, 8, 5, tzinfo=timezone.utc)
            )
        )
    finally:
        asyncio.run(client.aclose())

    assert len(items) == 1
    assert items[0].profile == "pangmen-ai-tech-radar"
    assert items[0].metadata["aihot_discovery_mode"] in {
        "selected_24h",
        "all_24h",
    }

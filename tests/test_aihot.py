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

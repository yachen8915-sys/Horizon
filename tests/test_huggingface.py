import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.models import HuggingFaceConfig, SourceType
from src.scrapers.huggingface import HuggingFaceScraper


SINCE = datetime(2026, 8, 8, tzinfo=timezone.utc)


def _response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_huggingface_models_keep_official_trending_signals():
    client = AsyncMock()
    client.get.return_value = _response(
        [
            {
                "id": "MiniMaxAI/MiniMax-H3",
                "pipeline_tag": "image-text-to-video",
                "trendingScore": 2974,
                "downloads": 35295,
                "likes": 3205,
                "lastModified": "2026-08-09T10:05:11Z",
                "tags": ["multimodal", "video-to-video"],
            }
        ]
    )
    scraper = HuggingFaceScraper(
        HuggingFaceConfig(enabled=True, fetch_papers=False), client
    )

    items = asyncio.run(scraper.fetch(SINCE))

    assert len(items) == 1
    item = items[0]
    assert item.source_type == SourceType.HUGGINGFACE
    assert item.profile == "pangmen-ai-tech-radar"
    assert item.metadata["category"] == "ai-tech-model"
    assert item.metadata["trending_score"] == 2974
    assert item.metadata["engagement"] == {"downloads": 35295, "likes": 3205}
    assert item.metadata["source_kind"] == "official"


def test_huggingface_daily_papers_keep_upvotes_and_project_links():
    client = AsyncMock()
    client.get.return_value = _response(
        [
            {
                "paper": {
                    "id": "2608.01492",
                    "title": "Agentic document understanding",
                    "summary": "A new document agent combines OCR and tool use.",
                    "submittedOnDailyAt": "2026-08-09T00:00:00Z",
                    "upvotes": 84,
                    "projectPage": "https://example.com/project",
                },
                "numComments": 12,
            }
        ]
    )
    scraper = HuggingFaceScraper(
        HuggingFaceConfig(enabled=True, fetch_models=False), client
    )

    items = asyncio.run(scraper.fetch(SINCE))

    assert len(items) == 1
    item = items[0]
    assert item.metadata["category"] == "ai-tech-paper"
    assert item.metadata["upvotes"] == 84
    assert item.metadata["project_url"] == "https://example.com/project"
    assert item.metadata["reliability"] == "official_experimental"
    assert str(item.url) == "https://huggingface.co/papers/2608.01492"


def test_huggingface_model_failure_does_not_block_papers():
    client = AsyncMock()
    client.get.side_effect = [
        RuntimeError("models unavailable"),
        _response(
            [
                {
                    "paper": {
                        "id": "2608.00001",
                        "title": "Useful multimodal agent",
                        "summary": "summary",
                        "submittedOnDailyAt": "2026-08-09T00:00:00Z",
                        "upvotes": 20,
                    }
                }
            ]
        ),
    ]
    scraper = HuggingFaceScraper(HuggingFaceConfig(enabled=True), client)

    items = asyncio.run(scraper.fetch(SINCE))

    assert [item.metadata["category"] for item in items] == ["ai-tech-paper"]

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.models import (
    SourceType,
    YouTubeChannelConfig,
    YouTubeDataConfig,
    YouTubeQueryConfig,
)
from src.scrapers.local_cli import LocalCommandResult
from src.scrapers.youtube import YouTubeDataScraper


SINCE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_youtube_search_enriches_native_engagement(monkeypatch) -> None:
    monkeypatch.setenv("YOUTUBE_DATA_API_KEY", "test-key")
    client = AsyncMock()
    client.get.side_effect = [
        _response(
            {
                "items": [
                    {
                        "id": {"videoId": "video-1"},
                        "snippet": {
                            "publishedAt": "2026-09-02T08:00:00Z",
                            "channelId": "channel-1",
                            "channelTitle": "AI Creator",
                            "title": "A useful AI workflow",
                            "description": "Demonstration",
                        },
                    }
                ]
            }
        ),
        _response(
            {
                "items": [
                    {
                        "id": "video-1",
                        "statistics": {
                            "viewCount": "125000",
                            "likeCount": "7200",
                            "commentCount": "430",
                        },
                        "contentDetails": {"duration": "PT8M20S"},
                    }
                ]
            }
        ),
    ]
    config = YouTubeDataConfig(
        enabled=True,
        queries=[
            YouTubeQueryConfig(
                query="AI tools",
                fetch_limit=10,
                category="overseas-ai-video",
                profile="pangmen-topic-radar",
            )
        ],
    )
    scraper = YouTubeDataScraper(config, client)

    items = asyncio.run(scraper.fetch(SINCE))

    assert len(items) == 1
    item = items[0]
    assert item.id == "youtube:video:video-1"
    assert item.source_type == SourceType.YOUTUBE
    assert str(item.url) == "https://www.youtube.com/watch?v=video-1"
    assert item.metadata["views"] == 125000
    assert item.metadata["likes"] == 7200
    assert item.metadata["comments"] == 430
    assert item.metadata["duration"] == "PT8M20S"
    assert scraper.last_source_results[0]["status"] == "healthy"
    search_kwargs = client.get.await_args_list[0].kwargs
    assert search_kwargs["params"]["order"] == "viewCount"
    assert search_kwargs["params"]["publishedAfter"] == "2026-09-01T00:00:00Z"
    assert search_kwargs["params"]["key"] == "test-key"
    assert scraper.units_used == 2


def test_youtube_channel_watchlist_uses_uploads_playlist(monkeypatch) -> None:
    monkeypatch.setenv("YOUTUBE_DATA_API_KEY", "test-key")
    client = AsyncMock()
    client.get.side_effect = [
        _response(
            {
                "items": [
                    {
                        "id": "channel-1",
                        "snippet": {"title": "Trusted AI Creator"},
                        "contentDetails": {
                            "relatedPlaylists": {"uploads": "uploads-1"}
                        },
                    }
                ]
            }
        ),
        _response(
            {
                "items": [
                    {
                        "snippet": {
                            "publishedAt": "2026-09-02T09:00:00Z",
                            "channelId": "channel-1",
                            "channelTitle": "Trusted AI Creator",
                            "title": "A new AI product test",
                            "description": "Hands-on review",
                            "resourceId": {"videoId": "video-2"},
                        }
                    }
                ]
            }
        ),
        _response(
            {
                "items": [
                    {
                        "id": "video-2",
                        "statistics": {
                            "viewCount": "42000",
                            "likeCount": "2800",
                            "commentCount": "190",
                        },
                        "contentDetails": {"duration": "PT11M"},
                    }
                ]
            }
        ),
    ]
    config = YouTubeDataConfig(
        enabled=True,
        channels=[
            YouTubeChannelConfig(
                channel_id="channel-1",
                name="Trusted AI Creator",
                fetch_limit=5,
                category="overseas-ai-video",
                profile="pangmen-topic-radar",
            )
        ],
        queries=[],
        max_units_per_run=10,
    )
    scraper = YouTubeDataScraper(config, client)

    items = asyncio.run(scraper.fetch(SINCE))

    assert [item.id for item in items] == ["youtube:video:video-2"]
    assert items[0].metadata["discovery_mode"] == "channel_uploads"
    assert items[0].metadata["channel_watchlist_name"] == "Trusted AI Creator"
    assert scraper.units_used == 3
    assert scraper.last_source_results[0]["source_type"] == "channel_uploads"
    assert scraper.last_source_results[0]["status"] == "healthy"
    calls = client.get.await_args_list
    assert calls[0].args[0].endswith("/channels")
    assert calls[1].args[0].endswith("/playlistItems")
    assert calls[2].args[0].endswith("/videos")


def test_youtube_missing_credentials_is_visible_without_network(monkeypatch) -> None:
    monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
    client = AsyncMock()
    config = YouTubeDataConfig(
        enabled=True,
        channels=[YouTubeChannelConfig(channel_id="channel-1")],
        queries=[YouTubeQueryConfig(query="AI agents")],
    )
    scraper = YouTubeDataScraper(config, client)

    assert asyncio.run(scraper.fetch(SINCE)) == []
    assert client.get.await_count == 0
    assert len(scraper.last_source_results) == 2
    assert {result["source_type"] for result in scraper.last_source_results} == {
        "channel_uploads",
        "search",
    }
    assert all(result["status"] == "failed" for result in scraper.last_source_results)
    assert all(
        result["reason_code"] == "missing_credentials"
        for result in scraper.last_source_results
    )


def test_youtube_missing_key_uses_ytdlp_channel_fallback(monkeypatch) -> None:
    monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
    client = AsyncMock()
    runner = AsyncMock(
        return_value=LocalCommandResult(
            returncode=0,
            stdout=(
                '{"id":"video-9","title":"Fresh AI tool test",'
                '"webpage_url":"https://www.youtube.com/watch?v=video-9",'
                '"timestamp":1788339600,"view_count":42000,"like_count":2800,'
                '"comment_count":190,"duration":660,"channel_id":"channel-1",'
                '"channel":"Trusted AI Creator","description":"Hands-on"}\n'
            ),
            stderr="",
        )
    )
    config = YouTubeDataConfig(
        enabled=True,
        local_cli_fallback_enabled=True,
        channels=[
            YouTubeChannelConfig(
                channel_id="channel-1",
                name="Trusted AI Creator",
                category="overseas-ai-video",
                profile="pangmen-topic-radar",
            )
        ],
        queries=[],
    )
    scraper = YouTubeDataScraper(config, client, command_runner=runner)

    items = asyncio.run(scraper.fetch(SINCE))

    assert [item.id for item in items] == ["youtube:video:video-9"]
    assert items[0].metadata["source_access"] == "yt_dlp_web"
    assert items[0].metadata["engagement"] == {
        "views": 42000,
        "likes": 2800,
        "comments": 190,
    }
    assert scraper.last_source_results[0]["status"] == "degraded"
    assert scraper.last_source_results[0]["reason_code"] == "fallback_used"
    args = runner.await_args.args[0]
    assert args[0] == "yt-dlp"
    assert args[-1] == "https://www.youtube.com/channel/channel-1/videos"
    assert client.get.await_count == 0


def test_youtube_ytdlp_fallback_supports_bounded_topic_search(monkeypatch) -> None:
    monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
    runner = AsyncMock(
        return_value=LocalCommandResult(
            returncode=0,
            stdout="",
            stderr="",
        )
    )
    config = YouTubeDataConfig(
        enabled=True,
        local_cli_fallback_enabled=True,
        channels=[],
        queries=[YouTubeQueryConfig(query="AI agents", fetch_limit=3)],
    )
    scraper = YouTubeDataScraper(config, AsyncMock(), command_runner=runner)

    assert asyncio.run(scraper.fetch(SINCE)) == []
    args = runner.await_args.args[0]
    assert args[-1] == "ytsearch3:AI agents after:2026-09-01"
    assert scraper.last_source_results[0]["reason_code"] == "fallback_used"


def test_youtube_shadow_source_requires_shadow_mode() -> None:
    from types import SimpleNamespace

    from src.orchestrator import HorizonOrchestrator

    orchestrator = object.__new__(HorizonOrchestrator)
    orchestrator.config = SimpleNamespace(
        collection=SimpleNamespace(source_shadow_enabled=False),
        sources=SimpleNamespace(
            youtube=YouTubeDataConfig(enabled=True, shadow=True)
        ),
    )

    assert orchestrator._youtube_source_enabled() is False

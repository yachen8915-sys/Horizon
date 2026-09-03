from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.models import SourceType, TwitterConfig
from src.scrapers.local_cli import LocalCommandResult
from src.scrapers.x_official import XOfficialScraper


SINCE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_x_recent_search_preserves_native_metrics(monkeypatch) -> None:
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    client = AsyncMock()
    client.get.return_value = _response(
        {
            "data": [
                {
                    "id": "tweet-1",
                    "author_id": "user-1",
                    "created_at": "2026-09-02T08:00:00Z",
                    "text": "A useful new AI workflow",
                    "conversation_id": "conversation-1",
                    "lang": "en",
                    "public_metrics": {
                        "like_count": 9100,
                        "retweet_count": 1200,
                        "reply_count": 340,
                        "quote_count": 280,
                        "impression_count": 820000,
                    },
                }
            ],
            "includes": {
                "users": [
                    {"id": "user-1", "name": "Creator", "username": "creator"}
                ]
            },
        }
    )
    config = TwitterConfig(
        enabled=True,
        mode="official_api",
        shadow=True,
        official_queries=["AI tools -is:retweet lang:en"],
        fetch_limit=50,
    )
    scraper = XOfficialScraper(config, client)

    items = asyncio.run(scraper.fetch(SINCE))

    assert len(items) == 1
    item = items[0]
    assert item.source_type == SourceType.TWITTER
    assert item.author == "Creator"
    assert str(item.url) == "https://x.com/creator/status/tweet-1"
    assert item.metadata["likes"] == 9100
    assert item.metadata["reposts"] == 1200
    assert item.metadata["impressions"] == 820000
    assert scraper.last_source_results[0]["status"] == "healthy"
    kwargs = client.get.await_args.kwargs
    assert kwargs["headers"]["Authorization"] == "Bearer test-token"
    assert kwargs["params"]["sort_order"] == "relevancy"
    assert kwargs["params"]["start_time"] == "2026-09-01T00:00:00Z"
    assert "-is:reply" in kwargs["params"]["query"]


def test_x_missing_credentials_is_visible_without_network(monkeypatch) -> None:
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    client = AsyncMock()
    config = TwitterConfig(
        enabled=True,
        mode="official_api",
        official_queries=["AI agents"],
    )
    scraper = XOfficialScraper(config, client)

    assert asyncio.run(scraper.fetch(SINCE)) == []
    assert client.get.await_count == 0
    assert scraper.last_source_results[0]["reason_code"] == "missing_credentials"


def test_x_missing_credentials_uses_bounded_opencli_fallback(monkeypatch) -> None:
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    client = AsyncMock()
    runner = AsyncMock(
        return_value=LocalCommandResult(
            returncode=0,
            stdout=(
                '[{"id":"2095175498967949359","author":"GoogleDeepMind",'
                '"text":"Two new Gemini models are here",'
                '"created_at":"Wed Sep 02 15:42:20 +0000 2026",'
                '"likes":2019,"views":"331414",'
                '"url":"https://x.com/i/status/2095175498967949359"}]\n'
                "Update available"
            ),
            stderr="",
        )
    )
    config = TwitterConfig(
        enabled=True,
        mode="official_api",
        official_queries=["AI agents -is:retweet lang:en"],
        local_cli_fallback_enabled=True,
        fetch_limit=15,
    )
    scraper = XOfficialScraper(config, client, command_runner=runner)

    items = asyncio.run(scraper.fetch(SINCE))

    assert len(items) == 1
    assert items[0].metadata["source_access"] == "opencli_browser"
    assert items[0].metadata["quality_platform"] == "twitter_opencli"
    assert items[0].metadata["engagement"] == {
        "impressions": 331414,
        "likes": 2019,
        "reposts": None,
        "replies": None,
        "quotes": None,
    }
    assert scraper.last_source_results[0]["status"] == "degraded"
    assert scraper.last_source_results[0]["reason_code"] == "fallback_used"
    args = runner.await_args.args[0]
    assert args[:3] == ["opencli", "twitter", "search"]
    assert "-filter:nativeretweets" in args[3]
    assert "-filter:replies" in args[3]
    assert "--product" in args and "live" in args
    assert client.get.await_count == 0


def test_x_local_fallback_failure_keeps_explicit_gap(monkeypatch) -> None:
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    runner = AsyncMock(
        return_value=LocalCommandResult(
            returncode=1,
            stdout="",
            stderr="AUTH_REQUIRED",
        )
    )
    scraper = XOfficialScraper(
        TwitterConfig(
            enabled=True,
            mode="official_api",
            official_queries=["AI agents"],
            local_cli_fallback_enabled=True,
        ),
        AsyncMock(),
        command_runner=runner,
    )

    assert asyncio.run(scraper.fetch(SINCE)) == []
    assert scraper.last_source_results[0]["status"] == "failed"
    assert scraper.last_source_results[0]["reason_code"] == "fallback_unavailable"


def test_x_official_api_turns_watchlist_users_into_one_bounded_query(monkeypatch) -> None:
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    client = AsyncMock()
    client.get.return_value = _response({"data": [], "includes": {"users": []}})
    config = TwitterConfig(
        enabled=True,
        mode="official_api",
        users=["OpenAI", "AnthropicAI", "karpathy"],
        official_queries=[],
        official_max_requests_per_run=1,
    )
    scraper = XOfficialScraper(config, client)

    assert asyncio.run(scraper.fetch(SINCE)) == []

    query = client.get.await_args.kwargs["params"]["query"]
    assert query == (
        "(from:OpenAI OR from:AnthropicAI OR from:karpathy) "
        "-is:retweet -is:reply"
    )
    assert scraper.last_source_results[0]["source_type"] == "recent_search"


def test_x_shadow_source_requires_shadow_mode() -> None:
    from types import SimpleNamespace

    from src.orchestrator import HorizonOrchestrator

    orchestrator = object.__new__(HorizonOrchestrator)
    orchestrator.config = SimpleNamespace(
        collection=SimpleNamespace(source_shadow_enabled=False),
        sources=SimpleNamespace(
            twitter=TwitterConfig(enabled=True, mode="official_api", shadow=True)
        ),
    )

    assert orchestrator._twitter_source_enabled() is False

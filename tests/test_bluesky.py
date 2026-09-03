from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.models import BlueskyConfig, BlueskyQueryConfig, SourceType
from src.scrapers.bluesky import BlueskyScraper


SINCE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def _post(*, uri: str, text: str = "A useful AI agent release") -> dict:
    return {
        "uri": uri,
        "cid": "bafy-test",
        "author": {
            "did": "did:plc:creator",
            "handle": "creator.bsky.social",
            "displayName": "AI Creator",
        },
        "record": {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": "2026-09-02T08:00:00Z",
            "langs": ["en"],
        },
        "replyCount": 12,
        "repostCount": 40,
        "likeCount": 350,
        "quoteCount": 8,
        "indexedAt": "2026-09-02T08:00:05Z",
    }


def test_bluesky_search_is_anonymous_and_preserves_native_metrics() -> None:
    client = AsyncMock()
    client.get.return_value = _response(
        {
            "posts": [
                _post(
                    uri=(
                        "at://did:plc:creator/app.bsky.feed.post/"
                        "3mexamplepost"
                    )
                )
            ]
        }
    )
    config = BlueskyConfig(
        enabled=True,
        queries=[BlueskyQueryConfig(query="AI agent release")],
    )
    scraper = BlueskyScraper(config, client)

    items = asyncio.run(scraper.fetch(SINCE))

    assert len(items) == 1
    item = items[0]
    assert item.source_type is SourceType.BLUESKY
    assert str(item.url) == (
        "https://bsky.app/profile/creator.bsky.social/post/3mexamplepost"
    )
    assert item.metadata["likes"] == 350
    assert item.metadata["reposts"] == 40
    assert item.metadata["engagement"]["quotes"] == 8
    call = client.get.await_args
    assert call.args[0].endswith("/xrpc/app.bsky.feed.searchPosts")
    assert call.kwargs["params"]["q"] == "AI agent release"
    assert "headers" not in call.kwargs
    assert scraper.last_source_results[0]["status"] == "healthy"


def test_bluesky_actor_feed_skips_reposts_and_deduplicates_query_results() -> None:
    post = _post(
        uri="at://did:plc:creator/app.bsky.feed.post/3mexamplepost"
    )
    client = AsyncMock()
    client.get.side_effect = [
        _response(
            {
                "feed": [
                    {"post": post},
                    {"post": _post(uri="at://did:plc:other/app.bsky.feed.post/repost"), "reason": {"$type": "app.bsky.feed.defs#reasonRepost"}},
                ]
            }
        ),
        _response({"posts": [post]}),
    ]
    config = BlueskyConfig(
        enabled=True,
        actors=["creator.bsky.social"],
        queries=[BlueskyQueryConfig(query="AI agent")],
        max_requests_per_run=2,
    )
    scraper = BlueskyScraper(config, client)

    items = asyncio.run(scraper.fetch(SINCE))

    assert len(items) == 1
    assert client.get.await_args_list[0].kwargs["params"]["actor"] == (
        "creator.bsky.social"
    )
    assert {row["source_type"] for row in scraper.last_source_results} == {
        "author_feed",
        "search",
    }


def test_bluesky_shadow_source_requires_shadow_mode() -> None:
    from types import SimpleNamespace

    from src.orchestrator import HorizonOrchestrator

    orchestrator = object.__new__(HorizonOrchestrator)
    orchestrator.config = SimpleNamespace(
        collection=SimpleNamespace(source_shadow_enabled=False),
        sources=SimpleNamespace(bluesky=BlueskyConfig(enabled=True, shadow=True)),
    )

    assert orchestrator._bluesky_source_enabled() is False


def test_disabled_bluesky_query_is_reported_without_a_request() -> None:
    client = AsyncMock()
    config = BlueskyConfig(
        enabled=True,
        queries=[BlueskyQueryConfig(query="AI tools", enabled=False)],
    )
    scraper = BlueskyScraper(config, client)

    items = asyncio.run(scraper.fetch(SINCE))

    assert items == []
    client.get.assert_not_awaited()
    assert scraper.last_source_results == [
        {
            "source_id": "bluesky:search:AI tools",
            "status": "disabled",
            "reason_code": "disabled",
            "detail": "",
            "source_type": "search",
            "query": "AI tools",
            "requests_used": 0,
        }
    ]

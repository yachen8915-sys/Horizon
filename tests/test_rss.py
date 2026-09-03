from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import httpx

from src.models import RSSSourceConfig
from src.scrapers.rss import RSSScraper

_FEED = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0"><channel><title>Test</title>
  <item>
    <guid>entry-1</guid>
    <title>Item 1</title>
    <link>https://example.com/item-1</link>
    <pubDate>Fri, 24 Apr 2026 12:00:00 GMT</pubDate>
    <description>Short summary from feed.</description>
  </item>
</channel></rss>
"""
_SINCE = datetime(2026, 4, 24, 0, 0, tzinfo=timezone.utc)


def _make_feed_client(feed_text: str) -> AsyncMock:
    response = MagicMock()
    response.text = feed_text
    response.raise_for_status.return_value = None
    client = AsyncMock()
    client.get.return_value = response
    return client


def test_rss_ids_are_deterministic() -> None:
    client = _make_feed_client(_FEED)
    source = RSSSourceConfig(
        name="Test", url="https://example.com/feed.xml", profile="rss-profile"
    )
    scraper = RSSScraper([source], client)

    first_item = asyncio.run(scraper.fetch(_SINCE))[0]
    first = first_item.id
    second = asyncio.run(scraper.fetch(_SINCE))[0].id

    assert first == second
    assert first == "rss:example.com_feed.xml:5e2d5d1e58e94d76"
    assert first_item.profile == "rss-profile"


def _make_registry(name: str, extractor):
    registry = MagicMock()
    registry.get.side_effect = lambda n: extractor if n == name else None
    return registry


def test_content_extractor_replaces_feed_content() -> None:
    client = _make_feed_client(_FEED)
    extractor = AsyncMock()
    extractor.extract.return_value = "Full article text from extractor."

    source = RSSSourceConfig(
        name="Test", url="https://example.com/feed.xml", content_extractor="my-ext"
    )
    scraper = RSSScraper([source], client, extractors=_make_registry("my-ext", extractor))
    items = asyncio.run(scraper.fetch(_SINCE))

    assert len(items) == 1
    assert items[0].content == "Full article text from extractor."
    extractor.extract.assert_awaited_once_with("https://example.com/item-1", client)


def test_content_extractor_falls_back_on_none() -> None:
    client = _make_feed_client(_FEED)
    extractor = AsyncMock()
    extractor.extract.return_value = None  # extraction failed

    source = RSSSourceConfig(
        name="Test", url="https://example.com/feed.xml", content_extractor="my-ext"
    )
    scraper = RSSScraper([source], client, extractors=_make_registry("my-ext", extractor))
    items = asyncio.run(scraper.fetch(_SINCE))

    assert len(items) == 1
    assert items[0].content == "Short summary from feed."


def test_unknown_extractor_name_ignored() -> None:
    client = _make_feed_client(_FEED)
    source = RSSSourceConfig(
        name="Test", url="https://example.com/feed.xml", content_extractor="nonexistent"
    )
    scraper = RSSScraper([source], client, extractors=_make_registry("other", AsyncMock()))
    items = asyncio.run(scraper.fetch(_SINCE))

    assert len(items) == 1
    assert items[0].content == "Short summary from feed."


def test_rss_records_health_for_each_feed_instead_of_hiding_one_failure() -> None:
    response = MagicMock()
    response.text = _FEED
    response.raise_for_status.return_value = None
    client = AsyncMock()
    client.get.side_effect = [httpx.ReadTimeout("slow feed"), response]
    sources = [
        RSSSourceConfig(name="Broken", url="https://example.com/broken.xml"),
        RSSSourceConfig(
            name="Healthy",
            url="https://example.com/healthy.xml",
            expected_cadence_hours=100000,
        ),
    ]
    scraper = RSSScraper(sources, client)

    items = asyncio.run(scraper.fetch(_SINCE))

    assert len(items) == 1
    assert [row["status"] for row in scraper.last_feed_results] == [
        "failed",
        "healthy",
    ]
    assert scraper.last_feed_results[0]["reason_code"] == "transport_error"
    assert scraper.last_feed_results[1]["feed_name"] == "Healthy"


def test_rss_health_detects_structurally_valid_but_stale_feed() -> None:
    scraper = RSSScraper(
        [
            RSSSourceConfig(
                name="Stale",
                url="https://example.com/stale.xml",
                expected_cadence_hours=24,
                stale_after_multiplier=2,
            )
        ],
        _make_feed_client(_FEED),
    )

    asyncio.run(scraper.fetch(_SINCE))

    assert scraper.last_feed_results[0]["status"] == "stale"
    assert scraper.last_feed_results[0]["reason_code"] == "stale_data"


def test_invalid_rss_payload_is_a_schema_failure() -> None:
    scraper = RSSScraper(
        [RSSSourceConfig(name="HTML", url="https://example.com/not-a-feed")],
        _make_feed_client("<html><body>not a feed</body></html>"),
    )

    assert asyncio.run(scraper.fetch(_SINCE)) == []
    assert scraper.last_feed_results[0]["status"] == "failed"
    assert scraper.last_feed_results[0]["reason_code"] == "schema_error"

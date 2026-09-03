from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from src.models import (
    BlueskyConfig,
    GitHubSourceConfig,
    PlatformChangeWatcherConfig,
    PlatformChangesConfig,
    RedditConfig,
    RSSSourceConfig,
    TwitterConfig,
    YouTubeDataConfig,
)
from src.processing.source_shadow import collect_one, create_shadow_scrapers


SINCE = datetime(2026, 9, 1, tzinfo=timezone.utc)


class StubScraper:
    def __init__(self, items, health):
        self.items = items
        self.last_source_results = health

    async def fetch(self, since):
        return self.items


def test_only_explicit_shadow_connectors_are_built() -> None:
    config = SimpleNamespace(
        sources=SimpleNamespace(
            github=[
                GitHubSourceConfig(type="repo_search", query="topic:ai", shadow=True),
                GitHubSourceConfig(type="repo_search", query="topic:ml", shadow=False),
            ],
            bluesky=BlueskyConfig(
                enabled=True,
                shadow=True,
                actors=["simonwillison.net"],
            ),
            rss=[
                RSSSourceConfig(
                    name="Active RSS",
                    url="https://example.com/active.xml",
                    shadow=False,
                ),
                RSSSourceConfig(
                    name="Shadow RSS",
                    url="https://example.com/shadow.xml",
                    shadow=True,
                ),
            ],
            youtube=YouTubeDataConfig(enabled=True, shadow=True),
            reddit=RedditConfig(enabled=True, shadow=True),
            twitter=TwitterConfig(enabled=True, mode="official_api", shadow=True),
            platform_changes=PlatformChangesConfig(
                enabled=True,
                shadow=True,
                state_file="data/platform_change_state.json",
                shadow_state_file="data/shadow/platform_change_state.json",
                watchers=[
                    PlatformChangeWatcherConfig(
                        name="anthropic-news",
                        mode="index",
                        platform="anthropic",
                        url="https://www.anthropic.com/news",
                        source_level="official",
                    )
                ],
            ),
        )
    )

    scrapers = create_shadow_scrapers(config, SimpleNamespace())

    assert [name for name, _ in scrapers] == [
        "GitHub",
        "Bluesky",
        "RSS Feeds",
        "YouTube Data",
        "Reddit",
        "X Official",
        "Official Pages",
    ]
    assert len(scrapers[0][1].config["sources"]) == 1
    assert [source.name for source in scrapers[2][1].config["sources"]] == [
        "Shadow RSS"
    ]
    assert str(scrapers[-1][1].state_path).replace("\\", "/") == (
        "data/shadow/platform_change_state.json"
    )


def test_shadow_outcome_keeps_failed_sub_source_diagnostics() -> None:
    scraper = StubScraper(
        [],
        [
            {
                "source_id": "youtube:search:AI tools",
                "status": "failed",
                "reason_code": "missing_credentials",
            }
        ],
    )

    outcome = asyncio.run(collect_one("YouTube Data", scraper, SINCE))

    assert outcome.status == "failure"
    assert outcome.error == "all configured sub-sources failed"
    assert outcome.health[0]["reason_code"] == "missing_credentials"


def test_watcher_health_is_normalized_to_unified_statuses() -> None:
    scraper = StubScraper(
        [],
        [
            {
                "name": "healthy-page",
                "status": "no_change",
                "health_status": "no_change",
            },
            {
                "name": "broken-page",
                "status": "structure_changed",
                "health_status": "failed",
            },
        ],
    )
    scraper.last_source_results = []
    scraper.last_watcher_results = scraper.items = [
        {
            "name": "healthy-page",
            "status": "no_change",
            "health_status": "no_change",
        },
        {
            "name": "broken-page",
            "status": "structure_changed",
            "health_status": "failed",
        },
    ]
    scraper.items = []

    outcome = asyncio.run(collect_one("Official Pages", scraper, SINCE))

    assert [row["status"] for row in outcome.health] == ["healthy", "failed"]
    assert outcome.health[1]["reason_code"] == "structure_changed"

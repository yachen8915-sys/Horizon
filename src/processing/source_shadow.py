"""Source-only shadow collection that never invokes AI or delivery services."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any

import httpx

from ..scrapers.github import GitHubScraper
from ..scrapers.bluesky import BlueskyScraper
from ..scrapers.platform_changes import PlatformChangesScraper
from ..scrapers.reddit import RedditScraper
from ..scrapers.rss import RSSScraper
from ..scrapers.x_official import XOfficialScraper
from ..scrapers.youtube import YouTubeDataScraper


@dataclass
class ShadowSourceOutcome:
    source_name: str
    status: str
    items: list[Any] = field(default_factory=list)
    health: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source": self.source_name,
            "status": self.status,
            "item_count": len(self.items),
            "health": self.health,
        }
        if self.error:
            result["error"] = self.error
        return result


def create_shadow_scrapers(config: Any, client: httpx.AsyncClient) -> list[tuple[str, Any]]:
    """Build only explicitly enabled shadow connectors."""

    sources = config.sources
    result: list[tuple[str, Any]] = []
    github_sources = [
        source
        for source in getattr(sources, "github", [])
        if source.enabled and source.shadow
    ]
    if github_sources:
        result.append(("GitHub", GitHubScraper(github_sources, client)))

    bluesky = getattr(sources, "bluesky", None)
    if bluesky is not None and bluesky.enabled and bluesky.shadow:
        result.append(("Bluesky", BlueskyScraper(bluesky, client)))

    rss_sources = [
        source
        for source in getattr(sources, "rss", [])
        if source.enabled and source.shadow
    ]
    if rss_sources:
        result.append(("RSS Feeds", RSSScraper(rss_sources, client)))

    youtube = getattr(sources, "youtube", None)
    if youtube is not None and youtube.enabled and youtube.shadow:
        result.append(("YouTube Data", YouTubeDataScraper(youtube, client)))

    reddit = getattr(sources, "reddit", None)
    if reddit is not None and reddit.enabled and reddit.shadow:
        result.append(("Reddit", RedditScraper(reddit, client)))

    twitter = getattr(sources, "twitter", None)
    if (
        twitter is not None
        and twitter.enabled
        and twitter.shadow
        and twitter.mode == "official_api"
    ):
        result.append(("X Official", XOfficialScraper(twitter, client)))

    platform_changes = getattr(sources, "platform_changes", None)
    if (
        platform_changes is not None
        and platform_changes.enabled
        and platform_changes.shadow
    ):
        shadow_config = platform_changes.model_copy(
            update={"state_file": platform_changes.shadow_state_file},
            deep=True,
        )
        result.append(
            ("Official Pages", PlatformChangesScraper(shadow_config, client))
        )
    return result


async def collect_one(name: str, scraper: Any, since: datetime) -> ShadowSourceOutcome:
    try:
        items = await scraper.fetch(since)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        source_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        return ShadowSourceOutcome(
            source_name=name,
            status="failure",
            error=error,
            health=[
                {
                    "source_id": source_id,
                    "status": "failed",
                    "reason_code": "unhandled_error",
                    "detail": error,
                }
            ],
        )

    raw_health = list(
        getattr(scraper, "last_source_results", [])
        or getattr(scraper, "last_feed_results", [])
        or getattr(scraper, "last_watcher_results", [])
        or []
    )
    health = [_normalize_health_row(row) for row in raw_health]
    all_failed = bool(health) and all(
        row.get("status") == "failed" or row.get("health_status") == "failed"
        for row in health
    )
    return ShadowSourceOutcome(
        source_name=name,
        status="failure" if not items and all_failed else ("success" if items else "empty"),
        items=items,
        health=health,
        error="all configured sub-sources failed" if not items and all_failed else None,
    )


def _normalize_health_row(row: dict[str, Any]) -> dict[str, Any]:
    if "health_status" not in row:
        return dict(row)
    normalized = dict(row)
    observed = str(row.get("health_status") or row.get("status") or "failed")
    if observed in {"baseline", "ok", "no_change", "new_items"}:
        status = "healthy"
    elif observed == "degraded":
        status = "degraded"
    else:
        status = "failed"
    normalized["observed_status"] = row.get("status")
    normalized["status"] = status
    normalized.setdefault("source_id", str(row.get("name") or "official-page"))
    normalized.setdefault(
        "reason_code",
        "healthy" if status == "healthy" else str(row.get("status") or status),
    )
    return normalized


async def collect_shadow_sources(
    config: Any, since: datetime, client: httpx.AsyncClient
) -> list[ShadowSourceOutcome]:
    scrapers = create_shadow_scrapers(config, client)
    return list(
        await asyncio.gather(
            *(collect_one(name, scraper, since) for name, scraper in scrapers)
        )
    )

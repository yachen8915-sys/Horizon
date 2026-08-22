"""Bilibili public search scraper.

Uses Bilibili's public web search endpoint. It does not require a login,
cookie, or API token and only reads fields already shown on public pages.
"""

import asyncio
from datetime import datetime, timezone
from html import unescape
import logging
import re
from typing import Any, List

import httpx

from .base import BaseScraper
from ..models import BilibiliConfig, BilibiliQueryConfig, ContentItem, SourceType


logger = logging.getLogger(__name__)


class BilibiliScraper(BaseScraper):
    SEARCH_URL = "https://api.bilibili.com/x/web-interface/search/type"

    def __init__(self, config: BilibiliConfig, http_client: httpx.AsyncClient):
        super().__init__(config.model_dump(), http_client)
        self.bilibili_config = config
        self.last_discovery_diagnostics: dict[str, Any] = {"channels": {}, "queries": {}}

    async def fetch(self, since: datetime) -> List[ContentItem]:
        if not self.bilibili_config.enabled:
            return []

        items: list[ContentItem] = []
        by_id: dict[str, ContentItem] = {}
        self.last_discovery_diagnostics = {"channels": {}, "queries": {}}
        enabled_queries = [query for query in self.bilibili_config.queries if query.enabled]
        requests = [
            (query, channel)
            for query in enabled_queries
            for channel in self.bilibili_config.discovery_channels
        ]
        for index, (query, channel) in enumerate(requests):
            fetched = await self._fetch_query(query, since, channel.name, channel.order)
            self.last_discovery_diagnostics["channels"][channel.name] = (
                self.last_discovery_diagnostics["channels"].get(channel.name, 0)
                + len(fetched)
            )
            query_key = f"{query.query}|{channel.name}|{channel.order}"
            self.last_discovery_diagnostics["queries"][query_key] = len(fetched)
            for item in fetched:
                existing = by_id.get(item.id)
                if existing is None:
                    by_id[item.id] = item
                    items.append(item)
                else:
                    self._merge_discovery(existing, item)
            if self.bilibili_config.request_interval_seconds and index < len(requests) - 1:
                await asyncio.sleep(self.bilibili_config.request_interval_seconds)
        return items

    async def _fetch_query(
        self, query: BilibiliQueryConfig, since: datetime, channel: str, order: str
    ) -> list[ContentItem]:
        try:
            response = await self._request_search(query.query, order)
            if response.status_code == 412:
                await asyncio.sleep(self.bilibili_config.retry_delay_seconds)
                response = await self._request_search(query.query, order)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Bilibili search failed for %s: %s", query.query, exc)
            return []

        if payload.get("code") != 0:
            logger.warning(
                "Bilibili search returned code %s for %s",
                payload.get("code"),
                query.query,
            )
            return []

        rows = (payload.get("data") or {}).get("result") or []
        items: list[ContentItem] = []
        for row in rows[: query.fetch_limit]:
            item = self._row_to_item(row, query, since, channel, order)
            if item is not None:
                items.append(item)
        return items

    async def _request_search(self, query: str, order: str) -> httpx.Response:
        return await self.client.get(
            self.SEARCH_URL,
            params={
                "search_type": "video",
                "keyword": query,
                "order": order,
                "page": 1,
            },
            headers={
                "Accept": "application/json",
                "Referer": "https://search.bilibili.com/",
                "User-Agent": "Mozilla/5.0 Horizon/0.1",
            },
            follow_redirects=True,
        )

    def _row_to_item(
        self, row: dict[str, Any], query: BilibiliQueryConfig, since: datetime,
        channel: str, order: str,
    ) -> ContentItem | None:
        bvid = str(row.get("bvid") or "").strip()
        author = unescape(str(row.get("author") or "")).strip()
        if not bvid or not author:
            return None
        if query.author and author.casefold() != query.author.strip().casefold():
            return None

        try:
            published_at = datetime.fromtimestamp(
                int(row.get("pubdate") or 0), tz=timezone.utc
            )
        except (TypeError, ValueError, OSError):
            return None
        if published_at < self._as_utc(since):
            return None

        engagement_fields = {
            "views": "play",
            "likes": "like",
            "comments": "review",
            "favorites": "favorites",
            "danmaku": "video_review",
            "coins": "coins",
            "shares": "share",
        }
        engagement = {
            target: self._parse_count(row[source])
            for target, source in engagement_fields.items()
            if source in row and row.get(source) not in (None, "", "--")
        }

        title = self._strip_markup(str(row.get("title") or "Untitled"))
        description = self._strip_markup(
            str(row.get("description") or row.get("desc") or "")
        )
        return ContentItem(
            id=self._generate_id(SourceType.BILIBILI.value, "video", bvid),
            source_type=SourceType.BILIBILI,
            title=title,
            url=f"https://www.bilibili.com/video/{bvid}",
            content=description,
            author=author,
            published_at=published_at,
            profile=query.profile,
            metadata={
                "bvid": bvid,
                "author_id": row.get("mid"),
                "query": query.query,
                "discovery_channel": channel,
                "discovery_channels": [channel],
                "search_order": order,
                "search_orders": [order],
                "query_matches": [{"query": query.query, "channel": channel, "order": order}],
                "category": query.category,
                "ai_media_candidate": True,
                "engagement": engagement,
            },
        )

    @staticmethod
    def _merge_discovery(existing: ContentItem, incoming: ContentItem) -> None:
        for key in ("discovery_channels", "search_orders", "query_matches"):
            current = existing.metadata.setdefault(key, [])
            for value in incoming.metadata.get(key, []):
                if value not in current:
                    current.append(value)
        existing_engagement = existing.metadata.setdefault("engagement", {})
        for key, value in incoming.metadata.get("engagement", {}).items():
            existing_engagement[key] = max(int(existing_engagement.get(key, 0)), int(value))

    @staticmethod
    def _strip_markup(value: str) -> str:
        return unescape(re.sub(r"<[^>]+>", "", value)).strip()

    @staticmethod
    def _parse_count(value: Any) -> int:
        text = str(value).strip().lower().replace(",", "")
        multiplier = 1
        if text.endswith("万"):
            multiplier = 10_000
            text = text[:-1]
        elif text.endswith("k"):
            multiplier = 1_000
            text = text[:-1]
        try:
            return int(float(text) * multiplier)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _as_utc(moment: datetime) -> datetime:
        if moment.tzinfo is None:
            return moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

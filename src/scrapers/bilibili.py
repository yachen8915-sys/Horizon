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

    async def fetch(self, since: datetime) -> List[ContentItem]:
        if not self.bilibili_config.enabled:
            return []

        items: list[ContentItem] = []
        seen: set[str] = set()
        enabled_queries = [query for query in self.bilibili_config.queries if query.enabled]
        for index, query in enumerate(enabled_queries):
            for item in await self._fetch_query(query, since):
                if item.id not in seen:
                    seen.add(item.id)
                    items.append(item)
            if (
                self.bilibili_config.request_interval_seconds
                and index < len(enabled_queries) - 1
            ):
                await asyncio.sleep(self.bilibili_config.request_interval_seconds)
        return items

    async def _fetch_query(
        self, query: BilibiliQueryConfig, since: datetime
    ) -> list[ContentItem]:
        try:
            response = await self._request_search(query.query)
            if response.status_code == 412:
                await asyncio.sleep(self.bilibili_config.retry_delay_seconds)
                response = await self._request_search(query.query)
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
            item = self._row_to_item(row, query, since)
            if item is not None:
                items.append(item)
        return items

    async def _request_search(self, query: str) -> httpx.Response:
        return await self.client.get(
            self.SEARCH_URL,
            params={
                "search_type": "video",
                "keyword": query,
                "order": "pubdate",
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
        self, row: dict[str, Any], query: BilibiliQueryConfig, since: datetime
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
                "category": query.category,
                "engagement": engagement,
            },
        )

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

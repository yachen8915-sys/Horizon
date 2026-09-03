"""Anonymous Bluesky AppView discovery for public AI posts."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
from typing import Any

import httpx

from .base import BaseScraper
from ..models import BlueskyConfig, BlueskyQueryConfig, ContentItem, SourceType
from ..processing.source_health import (
    SourceHealthObservation,
    SourceHealthResult,
    SourceHealthStatus,
    assess_source_health,
)


logger = logging.getLogger(__name__)


class BlueskyScraper(BaseScraper):
    """Read public author feeds and search results without authentication."""

    def __init__(self, config: BlueskyConfig, http_client: httpx.AsyncClient):
        super().__init__({"bluesky": config}, http_client)
        self.bluesky_config = config
        self.last_source_results: list[dict[str, Any]] = []
        self.requests_used = 0

    async def fetch(self, since: datetime) -> list[ContentItem]:
        self.last_source_results = []
        self.requests_used = 0
        items_by_uri: dict[str, ContentItem] = {}

        for raw_actor in self.bluesky_config.actors:
            actor = raw_actor.strip().lstrip("@")
            if not actor:
                continue
            if not self._has_budget():
                self._record_budget("author_feed", actor)
                continue
            rows: list[dict[str, Any]] = []
            try:
                rows = await self._fetch_actor(actor)
                parsed = [
                    item
                    for row in rows
                    if not row.get("reason")
                    if (item := self._make_item(row.get("post"), since)) is not None
                ]
                for item in parsed:
                    items_by_uri[str(item.metadata["post_uri"])] = item
                health = self._healthy(self._actor_source_id(actor), parsed)
            except Exception as exc:
                logger.warning("Bluesky actor feed %s failed: %s", actor, exc)
                health = self._failed(self._actor_source_id(actor), exc)
            self._record("author_feed", actor, health)

        for query in self.bluesky_config.queries:
            if not query.enabled:
                self._record(
                    "search",
                    query.query,
                    assess_source_health(
                        SourceHealthObservation(
                            source_id=self._query_source_id(query.query),
                            checked_at=datetime.now(timezone.utc),
                            enabled=False,
                        )
                    ),
                )
                continue
            if not self._has_budget():
                self._record_budget("search", query.query)
                continue
            parsed: list[ContentItem] = []
            try:
                rows = await self._fetch_query(query)
                parsed = [
                    item
                    for row in rows
                    if (item := self._make_item(row, since)) is not None
                ]
                for item in parsed:
                    items_by_uri[str(item.metadata["post_uri"])] = item
                health = self._healthy(self._query_source_id(query.query), parsed)
            except Exception as exc:
                logger.warning("Bluesky search %s failed: %s", query.query, exc)
                health = self._failed(self._query_source_id(query.query), exc)
            self._record("search", query.query, health)

        return list(items_by_uri.values())

    async def _fetch_actor(self, actor: str) -> list[dict[str, Any]]:
        response = await self.client.get(
            self._endpoint("app.bsky.feed.getAuthorFeed"),
            params={
                "actor": actor,
                "filter": "posts_no_replies",
                "limit": self.bluesky_config.actor_fetch_limit,
            },
            follow_redirects=True,
        )
        self.requests_used += 1
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("feed") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("Bluesky author feed response must contain feed")
        return [row for row in rows if isinstance(row, dict)]

    async def _fetch_query(self, query: BlueskyQueryConfig) -> list[dict[str, Any]]:
        response = await self.client.get(
            self._endpoint("app.bsky.feed.searchPosts"),
            params={
                "q": query.query,
                "sort": query.sort,
                "limit": query.fetch_limit,
            },
            follow_redirects=True,
        )
        self.requests_used += 1
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("posts") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("Bluesky search response must contain posts")
        return [row for row in rows if isinstance(row, dict)]

    def _make_item(
        self,
        row: Any,
        since: datetime,
    ) -> ContentItem | None:
        if not isinstance(row, dict):
            return None
        record = row.get("record")
        author = row.get("author")
        if not isinstance(record, dict) or not isinstance(author, dict):
            return None
        uri = str(row.get("uri") or "")
        text = str(record.get("text") or "").strip()
        handle = str(author.get("handle") or "").strip()
        published_at = self._datetime(record.get("createdAt") or row.get("indexedAt"))
        if not uri or not text or not handle or published_at is None:
            return None
        since_utc = self._utc(since)
        if published_at < since_utc:
            return None
        rkey = uri.rsplit("/", 1)[-1]
        digest = hashlib.sha256(uri.encode("utf-8")).hexdigest()[:16]
        title_text = " ".join(text.split())
        if len(title_text) > 72:
            title_text = title_text[:72].rstrip() + "..."
        likes = self._optional_int(row.get("likeCount"))
        reposts = self._optional_int(row.get("repostCount"))
        replies = self._optional_int(row.get("replyCount"))
        quotes = self._optional_int(row.get("quoteCount"))
        return ContentItem(
            id=self._generate_id("bluesky", "post", digest),
            source_type=SourceType.BLUESKY,
            title=f"@{handle}: {title_text}",
            url=f"https://bsky.app/profile/{handle}/post/{rkey}",
            content=text,
            author=str(author.get("displayName") or handle),
            published_at=published_at,
            profile=self.bluesky_config.profile,
            metadata={
                "post_uri": uri,
                "post_cid": row.get("cid"),
                "platform_native_id": uri,
                "author_did": author.get("did"),
                "handle": handle,
                "likes": likes,
                "reposts": reposts,
                "replies": replies,
                "quotes": quotes,
                "engagement": {
                    "likes": likes,
                    "reposts": reposts,
                    "replies": replies,
                    "quotes": quotes,
                },
                "langs": record.get("langs") or [],
                "category": self.bluesky_config.category,
                "source_shadow": self.bluesky_config.shadow,
            },
        )

    def _endpoint(self, method: str) -> str:
        return f"{str(self.bluesky_config.public_api_base_url).rstrip('/')}/xrpc/{method}"

    def _has_budget(self) -> bool:
        return self.requests_used < self.bluesky_config.max_requests_per_run

    def _record_budget(self, source_type: str, label: str) -> None:
        source_id = (
            self._actor_source_id(label)
            if source_type == "author_feed"
            else self._query_source_id(label)
        )
        self._record(
            source_type,
            label,
            SourceHealthResult(
                source_id=source_id,
                status=SourceHealthStatus.DEGRADED,
                reason_code="budget_exhausted",
                detail="Bluesky per-run request budget was exhausted",
            ),
        )

    def _record(
        self,
        source_type: str,
        label: str,
        health: SourceHealthResult,
    ) -> None:
        self.last_source_results.append(
            {
                **health.model_dump(mode="json"),
                "source_type": source_type,
                "actor" if source_type == "author_feed" else "query": label,
                "requests_used": self.requests_used,
            }
        )

    @staticmethod
    def _healthy(source_id: str, items: list[ContentItem]) -> SourceHealthResult:
        return assess_source_health(
            SourceHealthObservation(
                source_id=source_id,
                checked_at=datetime.now(timezone.utc),
                item_count=len(items),
                newest_item_at=max(
                    (item.published_at for item in items),
                    default=None,
                ),
            )
        )

    @staticmethod
    def _failed(source_id: str, exc: Exception) -> SourceHealthResult:
        schema_error = isinstance(exc, ValueError)
        return assess_source_health(
            SourceHealthObservation(
                source_id=source_id,
                checked_at=datetime.now(timezone.utc),
                transport_ok=schema_error,
                schema_ok=not schema_error,
                error=f"{type(exc).__name__}: {exc}",
            )
        )

    @staticmethod
    def _actor_source_id(actor: str) -> str:
        return f"bluesky:actor:{actor}"

    @staticmethod
    def _query_source_id(query: str) -> str:
        return f"bluesky:search:{query}"

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )

    @staticmethod
    def _datetime(value: Any) -> datetime | None:
        if value in (None, ""):
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

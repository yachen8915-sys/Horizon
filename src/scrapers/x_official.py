"""Official X API recent-search discovery."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import os
from typing import Any

import httpx

from .base import BaseScraper
from .local_cli import (
    LocalCommandResult,
    decode_first_json,
    run_local_command,
)
from ..models import ContentItem, SourceType, TwitterConfig
from ..processing.source_health import (
    SourceHealthObservation,
    SourceHealthResult,
    SourceHealthStatus,
    assess_source_health,
)


logger = logging.getLogger(__name__)


class XOfficialScraper(BaseScraper):
    def __init__(
        self,
        config: TwitterConfig,
        http_client: httpx.AsyncClient,
        *,
        command_runner: Callable[
            [list[str], float], Awaitable[LocalCommandResult]
        ]
        | None = None,
    ):
        super().__init__({"twitter": config}, http_client)
        self.twitter_config = config
        self.command_runner = command_runner or run_local_command
        self.last_source_results: list[dict[str, Any]] = []
        self.requests_used = 0
        self.local_cli_requests_used = 0

    async def fetch(self, since: datetime) -> list[ContentItem]:
        self.last_source_results = []
        self.requests_used = 0
        self.local_cli_requests_used = 0
        queries = self._configured_queries()
        token = os.getenv(self.twitter_config.official_bearer_token_env, "").strip()
        if not token:
            if self.twitter_config.local_cli_fallback_enabled:
                return await self._fetch_via_opencli(queries, since)
            for query in queries:
                self._record_health(
                    query,
                    assess_source_health(
                        SourceHealthObservation(
                            source_id=self._source_id(query),
                            checked_at=datetime.now(timezone.utc),
                            credentials_ok=False,
                            error=(
                                "environment variable "
                                f"{self.twitter_config.official_bearer_token_env} is missing"
                            ),
                        )
                    ),
                )
            return []

        items_by_id: dict[str, ContentItem] = {}
        for query in queries:
            if self.requests_used >= self.twitter_config.official_max_requests_per_run:
                self._record_health(
                    query,
                    SourceHealthResult(
                        source_id=self._source_id(query),
                        status=SourceHealthStatus.DEGRADED,
                        reason_code="budget_exhausted",
                        detail="X API per-run request budget was exhausted",
                    ),
                )
                continue
            try:
                query_items = await self._fetch_query(query, since, token)
                for item in query_items:
                    items_by_id[item.id] = item
                health = assess_source_health(
                    SourceHealthObservation(
                        source_id=self._source_id(query),
                        checked_at=datetime.now(timezone.utc),
                        item_count=len(query_items),
                        newest_item_at=max(
                            (item.published_at for item in query_items),
                            default=None,
                        ),
                    )
                )
            except Exception as exc:
                schema_error = isinstance(exc, ValueError)
                logger.warning("X official query failed: %s", exc)
                health = assess_source_health(
                    SourceHealthObservation(
                        source_id=self._source_id(query),
                        checked_at=datetime.now(timezone.utc),
                        transport_ok=schema_error,
                        schema_ok=not schema_error,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
            self._record_health(query, health)
        return list(items_by_id.values())

    async def _fetch_via_opencli(
        self,
        queries: list[str],
        since: datetime,
    ) -> list[ContentItem]:
        items_by_id: dict[str, ContentItem] = {}
        for query in queries:
            normalized_query = query.replace(
                "-is:retweet", "-filter:nativeretweets"
            ).replace("-is:reply", "-filter:replies")
            args = [
                self.twitter_config.local_cli_command,
                "twitter",
                "search",
                normalized_query,
                "--product",
                "live",
                "--limit",
                str(self.twitter_config.fetch_limit),
                "--top-by-engagement",
                str(self.twitter_config.fetch_limit),
                "-f",
                "json",
                "--window",
                "background",
                "--site-session",
                "persistent",
            ]
            try:
                result = await self.command_runner(
                    args,
                    float(self.twitter_config.local_cli_timeout_sec),
                )
                self.local_cli_requests_used += 1
                if result.returncode != 0:
                    raise RuntimeError(
                        self._command_error(result) or "OpenCLI exited with an error"
                    )
                payload = decode_first_json(result.stdout)
                if not isinstance(payload, list):
                    raise ValueError("OpenCLI X search output must be a list")
                query_items = [
                    item
                    for row in payload
                    if (item := self._make_opencli_item(row, query, since))
                    is not None
                ]
                for item in query_items:
                    items_by_id[item.id] = item
                health = SourceHealthResult(
                    source_id=self._source_id(query),
                    status=SourceHealthStatus.DEGRADED,
                    reason_code="fallback_used",
                    detail=(
                        "X bearer token is missing; authenticated OpenCLI browser "
                        "fallback succeeded"
                    ),
                )
            except Exception as exc:
                logger.warning("X OpenCLI fallback failed: %s", exc)
                health = SourceHealthResult(
                    source_id=self._source_id(query),
                    status=SourceHealthStatus.FAILED,
                    reason_code="fallback_unavailable",
                    detail=(
                        "X bearer token is missing and the OpenCLI browser fallback "
                        f"failed: {type(exc).__name__}: {exc}"
                    ),
                )
            self._record_health(query, health)
            self.last_source_results[-1].update(
                {
                    "access_method": "opencli_browser",
                    "local_cli_requests_used": self.local_cli_requests_used,
                }
            )
        return list(items_by_id.values())

    def _make_opencli_item(
        self,
        row: Any,
        query: str,
        since: datetime,
    ) -> ContentItem | None:
        if not isinstance(row, dict):
            return None
        tweet_id = str(row.get("id") or "").strip()
        text = str(row.get("text") or "").strip()
        author = str(row.get("author") or "unknown").strip()
        published_at = self._opencli_datetime(row.get("created_at"))
        if not tweet_id or not text or published_at is None:
            return None
        since_utc = (
            since.replace(tzinfo=timezone.utc)
            if since.tzinfo is None
            else since.astimezone(timezone.utc)
        )
        if published_at < since_utc:
            return None
        impressions = self._metric(row, "views")
        likes = self._metric(row, "likes")
        reposts = self._metric(row, "retweets")
        replies = self._metric(row, "replies")
        quotes = self._metric(row, "quotes")
        title_text = " ".join(text.split())
        if len(title_text) > 72:
            title_text = title_text[:72].rstrip() + "..."
        return ContentItem(
            id=self._generate_id("twitter", "tweet", tweet_id),
            source_type=SourceType.TWITTER,
            title=f"@{author}: {title_text}",
            url=str(row.get("url") or f"https://x.com/i/status/{tweet_id}"),
            content=text,
            author=author,
            published_at=published_at,
            profile=self.twitter_config.profile,
            metadata={
                "tweet_id": tweet_id,
                "username": author,
                "likes": likes,
                "reposts": reposts,
                "replies": replies,
                "quotes": quotes,
                "impressions": impressions,
                "engagement": {
                    "impressions": impressions,
                    "likes": likes,
                    "reposts": reposts,
                    "replies": replies,
                    "quotes": quotes,
                },
                "query": query,
                "category": self.twitter_config.category,
                "source_shadow": self.twitter_config.shadow,
                "source_access": "opencli_browser",
                "quality_platform": "twitter_opencli",
            },
        )

    @staticmethod
    def _opencli_datetime(value: Any) -> datetime | None:
        if value in (None, ""):
            return None
        try:
            parsed = parsedate_to_datetime(str(value))
        except (TypeError, ValueError):
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                return None
        return (
            parsed.replace(tzinfo=timezone.utc)
            if parsed.tzinfo is None
            else parsed.astimezone(timezone.utc)
        )

    @staticmethod
    def _command_error(result: LocalCommandResult) -> str:
        return (result.stderr or result.stdout).strip()[-500:]

    async def _fetch_query(
        self, query: str, since: datetime, token: str
    ) -> list[ContentItem]:
        since_utc = (
            since.replace(tzinfo=timezone.utc)
            if since.tzinfo is None
            else since.astimezone(timezone.utc)
        )
        response = await self.client.get(
            "https://api.x.com/2/tweets/search/recent",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "query": query,
                "max_results": max(10, min(self.twitter_config.fetch_limit, 100)),
                "sort_order": "relevancy",
                "start_time": since_utc.isoformat(timespec="seconds").replace(
                    "+00:00", "Z"
                ),
                "expansions": "author_id",
                "tweet.fields": "created_at,public_metrics,conversation_id,lang",
                "user.fields": "name,username",
            },
            follow_redirects=True,
        )
        self.requests_used += 1
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("X recent search response must be an object")
        rows = payload.get("data", [])
        if not isinstance(rows, list):
            raise ValueError("X recent search data must be a list")
        included_users = (payload.get("includes") or {}).get("users", [])
        users = {
            str(user.get("id")): user
            for user in included_users
            if isinstance(user, dict) and user.get("id")
        }

        items: list[ContentItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                tweet_id = str(row["id"])
                text = str(row["text"])
                published_at = datetime.fromisoformat(
                    str(row["created_at"]).replace("Z", "+00:00")
                )
            except (KeyError, TypeError, ValueError):
                continue
            if published_at < since_utc:
                continue
            user = users.get(str(row.get("author_id")), {})
            username = str(user.get("username") or "unknown")
            author = str(user.get("name") or username)
            metrics = row.get("public_metrics") or {}
            title_text = text.replace("\n", " ").strip()
            if len(title_text) > 72:
                title_text = title_text[:72].rstrip() + "..."
            items.append(
                ContentItem(
                    id=self._generate_id("twitter", "tweet", tweet_id),
                    source_type=SourceType.TWITTER,
                    title=f"@{username}: {title_text}",
                    url=f"https://x.com/{username}/status/{tweet_id}",
                    content=text,
                    author=author,
                    published_at=published_at,
                    profile=self.twitter_config.profile,
                    metadata={
                        "tweet_id": tweet_id,
                        "conversation_id": row.get("conversation_id"),
                        "username": username,
                        "likes": self._metric(metrics, "like_count"),
                        "reposts": self._metric(metrics, "retweet_count"),
                        "replies": self._metric(metrics, "reply_count"),
                        "quotes": self._metric(metrics, "quote_count"),
                        "impressions": self._metric(metrics, "impression_count"),
                        "engagement": {
                            "impressions": self._metric(metrics, "impression_count"),
                            "likes": self._metric(metrics, "like_count"),
                            "reposts": self._metric(metrics, "retweet_count"),
                            "replies": self._metric(metrics, "reply_count"),
                            "quotes": self._metric(metrics, "quote_count"),
                        },
                        "lang": row.get("lang"),
                        "query": query,
                        "category": self.twitter_config.category,
                        "source_shadow": self.twitter_config.shadow,
                    },
                )
            )
        return items

    def _record_health(self, query: str, health: SourceHealthResult) -> None:
        self.last_source_results.append(
            {
                **health.model_dump(mode="json"),
                "source_type": "recent_search",
                "query": query,
                "requests_used": self.requests_used,
            }
        )

    @staticmethod
    def _source_id(query: str) -> str:
        return f"x:recent_search:{query}"

    @staticmethod
    def _metric(metrics: Any, key: str) -> int | None:
        if not isinstance(metrics, dict) or metrics.get(key) is None:
            return None
        try:
            return int(metrics[key])
        except (TypeError, ValueError):
            return None

    def _configured_queries(self) -> list[str]:
        """Build bounded recent-search queries, prioritising trusted accounts."""

        queries: list[str] = []
        terms: list[str] = []
        query_suffix = " -is:retweet -is:reply"
        current_length = len("()") + len(query_suffix)
        for raw_user in self.twitter_config.users:
            username = raw_user.strip().lstrip("@")
            if not username:
                continue
            term = f"from:{username}"
            added = len(term) + (4 if terms else 0)
            if terms and current_length + added > 450:
                queries.append(f"({' OR '.join(terms)}){query_suffix}")
                terms = []
                current_length = len("()") + len(query_suffix)
            terms.append(term)
            current_length += added
        if terms:
            queries.append(f"({' OR '.join(terms)}){query_suffix}")

        for raw_query in self.twitter_config.official_queries:
            query = raw_query.strip()
            if query and "-is:retweet" not in query:
                query += " -is:retweet"
            if query and "-is:reply" not in query:
                query += " -is:reply"
            if query and query not in queries:
                queries.append(query)
        return queries

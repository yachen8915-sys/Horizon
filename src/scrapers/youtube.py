"""Official YouTube Data API discovery and engagement enrichment."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
import logging
import os
from typing import Any

import httpx

from .base import BaseScraper
from .local_cli import LocalCommandResult, decode_json_lines, run_local_command
from ..models import (
    ContentItem,
    SourceType,
    YouTubeChannelConfig,
    YouTubeDataConfig,
    YouTubeQueryConfig,
)
from ..processing.source_health import (
    SourceHealthObservation,
    SourceHealthResult,
    SourceHealthStatus,
    assess_source_health,
)


logger = logging.getLogger(__name__)


class YouTubeDataScraper(BaseScraper):
    # Since June 2026 search.list has its own daily call bucket and costs one
    # unit per call. Keep one shared per-run budget so Horizon stays bounded if
    # Google's quota model changes again.
    SEARCH_UNIT_COST = 1
    CHANNELS_UNIT_COST = 1
    PLAYLIST_ITEMS_UNIT_COST = 1
    VIDEOS_UNIT_COST = 1

    def __init__(
        self,
        config: YouTubeDataConfig,
        http_client: httpx.AsyncClient,
        *,
        command_runner: Callable[
            [list[str], float], Awaitable[LocalCommandResult]
        ]
        | None = None,
    ):
        super().__init__({"youtube": config}, http_client)
        self.youtube_config = config
        self.command_runner = command_runner or run_local_command
        self.last_source_results: list[dict[str, Any]] = []
        self.units_used = 0
        self.local_cli_requests_used = 0

    async def fetch(self, since: datetime) -> list[ContentItem]:
        self.last_source_results = []
        self.units_used = 0
        self.local_cli_requests_used = 0
        api_key = os.getenv(self.youtube_config.api_key_env, "").strip()
        enabled_channels = [
            channel for channel in self.youtube_config.channels if channel.enabled
        ]
        enabled_queries = [
            query for query in self.youtube_config.queries if query.enabled
        ]
        if not api_key:
            if self.youtube_config.local_cli_fallback_enabled:
                return await self._fetch_via_ytdlp(
                    enabled_channels,
                    enabled_queries,
                    since,
                )
            for channel in enabled_channels:
                self._record_channel_health(
                    channel,
                    assess_source_health(
                        SourceHealthObservation(
                            source_id=self._channel_source_id(channel),
                            checked_at=datetime.now(timezone.utc),
                            credentials_ok=False,
                            error=f"environment variable {self.youtube_config.api_key_env} is missing",
                        )
                    ),
                )
            for query in enabled_queries:
                self._record_health(
                    query,
                    assess_source_health(
                        SourceHealthObservation(
                            source_id=self._source_id(query),
                            checked_at=datetime.now(timezone.utc),
                            credentials_ok=False,
                            error=f"environment variable {self.youtube_config.api_key_env} is missing",
                        )
                    ),
                )
            return []

        items_by_id: dict[str, ContentItem] = {}
        if enabled_channels:
            minimum_units = (
                self.CHANNELS_UNIT_COST
                + len(enabled_channels) * self.PLAYLIST_ITEMS_UNIT_COST
                + self.VIDEOS_UNIT_COST
            )
            if self.units_used + minimum_units > self.youtube_config.max_units_per_run:
                for channel in enabled_channels:
                    self._record_channel_health(
                        channel,
                        SourceHealthResult(
                            source_id=self._channel_source_id(channel),
                            status=SourceHealthStatus.DEGRADED,
                            reason_code="budget_exhausted",
                            detail="YouTube API per-run request budget was exhausted",
                        ),
                    )
            else:
                try:
                    channel_items = await self._fetch_channels(
                        enabled_channels, since, api_key
                    )
                    for item in channel_items:
                        items_by_id[item.id] = item
                    counts = {
                        channel.channel_id: sum(
                            item.metadata.get("watchlist_channel_id")
                            == channel.channel_id
                            for item in channel_items
                        )
                        for channel in enabled_channels
                    }
                    for channel in enabled_channels:
                        self._record_channel_health(
                            channel,
                            assess_source_health(
                                SourceHealthObservation(
                                    source_id=self._channel_source_id(channel),
                                    checked_at=datetime.now(timezone.utc),
                                    item_count=counts[channel.channel_id],
                                    newest_item_at=max(
                                        (
                                            item.published_at
                                            for item in channel_items
                                            if item.metadata.get(
                                                "watchlist_channel_id"
                                            )
                                            == channel.channel_id
                                        ),
                                        default=None,
                                    ),
                                )
                            ),
                        )
                except Exception as exc:
                    schema_error = isinstance(exc, ValueError)
                    error = self._safe_api_error(exc)
                    logger.warning("YouTube channel watchlist failed: %s", error)
                    for channel in enabled_channels:
                        self._record_channel_health(
                            channel,
                            assess_source_health(
                                SourceHealthObservation(
                                    source_id=self._channel_source_id(channel),
                                    checked_at=datetime.now(timezone.utc),
                                    transport_ok=schema_error,
                                    schema_ok=not schema_error,
                                    error=error,
                                )
                            ),
                        )

        for query in enabled_queries:
            required_units = self.SEARCH_UNIT_COST + self.VIDEOS_UNIT_COST
            if self.units_used + required_units > self.youtube_config.max_units_per_run:
                self._record_health(
                    query,
                    SourceHealthResult(
                        source_id=self._source_id(query),
                        status=SourceHealthStatus.DEGRADED,
                        reason_code="budget_exhausted",
                        detail="YouTube API per-run quota budget was exhausted",
                    ),
                )
                continue
            query_items: list[ContentItem] = []
            try:
                query_items = await self._fetch_query(query, since, api_key)
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
                error = self._safe_api_error(exc)
                logger.warning("YouTube search failed: %s", error)
                health = assess_source_health(
                    SourceHealthObservation(
                        source_id=self._source_id(query),
                        checked_at=datetime.now(timezone.utc),
                        transport_ok=schema_error,
                        schema_ok=False if schema_error else True,
                        error=error,
                    )
                )
            self._record_health(query, health)
            for item in query_items:
                items_by_id[item.id] = item
        return list(items_by_id.values())

    @staticmethod
    def _safe_api_error(exc: Exception) -> str:
        # HTTPX exception strings include the complete authenticated URL.
        if isinstance(exc, httpx.HTTPStatusError):
            return f"HTTP {exc.response.status_code} {exc.request.url.path}"
        return type(exc).__name__

    async def _fetch_via_ytdlp(
        self,
        channels: list[YouTubeChannelConfig],
        queries: list[YouTubeQueryConfig],
        since: datetime,
    ) -> list[ContentItem]:
        semaphore = asyncio.Semaphore(self.youtube_config.local_cli_concurrency)

        async def collect_route(
            route: YouTubeChannelConfig | YouTubeQueryConfig,
            discovery_mode: str,
        ) -> tuple[
            YouTubeChannelConfig | YouTubeQueryConfig,
            str,
            list[ContentItem],
            SourceHealthResult,
        ]:
            async with semaphore:
                return await self._fetch_ytdlp_route(
                    route,
                    discovery_mode,
                    since,
                )

        tasks = [
            collect_route(channel, "channel_uploads") for channel in channels
        ] + [collect_route(query, "search") for query in queries]
        results = await asyncio.gather(*tasks) if tasks else []
        items_by_id: dict[str, ContentItem] = {}
        for route, discovery_mode, items, health in results:
            for item in items:
                items_by_id[item.id] = item
            if isinstance(route, YouTubeChannelConfig):
                self._record_channel_health(route, health)
            else:
                self._record_health(route, health)
            self.last_source_results[-1].update(
                {
                    "access_method": "yt_dlp_web",
                    "local_cli_requests_used": self.local_cli_requests_used,
                    "discovery_mode": discovery_mode,
                }
            )
        return list(items_by_id.values())

    async def _fetch_ytdlp_route(
        self,
        route: YouTubeChannelConfig | YouTubeQueryConfig,
        discovery_mode: str,
        since: datetime,
    ) -> tuple[
        YouTubeChannelConfig | YouTubeQueryConfig,
        str,
        list[ContentItem],
        SourceHealthResult,
    ]:
        if isinstance(route, YouTubeChannelConfig):
            target = f"https://www.youtube.com/channel/{route.channel_id}/videos"
        else:
            # Plain ytsearch ranks by long-term relevance and often returns old
            # videos that are discarded by Horizon's freshness window. YouTube's
            # search parser honors the `after:` operator, so constrain discovery
            # before downloading metadata and then keep the exact timestamp check
            # in _make_ytdlp_item as the final freshness gate.
            after_date = self._utc(since).date().isoformat()
            target = (
                f"ytsearch{route.fetch_limit}:{route.query} "
                f"after:{after_date}"
            )
        output_template = (
            "%(.{id,title,webpage_url,timestamp,upload_date,view_count,"
            "like_count,comment_count,duration,channel_id,channel,description})j"
        )
        args = [
            self.youtube_config.local_cli_command,
            "--no-update",
            "--skip-download",
            "--playlist-end",
            str(route.fetch_limit),
            "--print",
            output_template,
            target,
        ]
        source_id = (
            self._channel_source_id(route)
            if isinstance(route, YouTubeChannelConfig)
            else self._source_id(route)
        )
        try:
            result = await self.command_runner(
                args,
                float(self.youtube_config.local_cli_timeout_sec),
            )
            self.local_cli_requests_used += 1
            rows = decode_json_lines(result.stdout)
            if result.returncode != 0 and not rows:
                raise RuntimeError(
                    self._command_error(result) or "yt-dlp exited with an error"
                )
            items = [
                item
                for row in rows
                if (
                    item := self._make_ytdlp_item(
                        row,
                        route,
                        discovery_mode,
                        since,
                    )
                )
                is not None
            ]
            health = SourceHealthResult(
                source_id=source_id,
                status=SourceHealthStatus.DEGRADED,
                reason_code="fallback_used",
                detail=(
                    "YouTube Data API key is missing; anonymous yt-dlp web "
                    "fallback succeeded"
                ),
            )
        except Exception as exc:
            logger.warning("YouTube yt-dlp fallback failed: %s", exc)
            items = []
            health = SourceHealthResult(
                source_id=source_id,
                status=SourceHealthStatus.FAILED,
                reason_code="fallback_unavailable",
                detail=(
                    "YouTube Data API key is missing and the yt-dlp fallback "
                    f"failed: {type(exc).__name__}: {exc}"
                ),
            )
        return route, discovery_mode, items, health

    def _make_ytdlp_item(
        self,
        row: dict[str, Any],
        route: YouTubeChannelConfig | YouTubeQueryConfig,
        discovery_mode: str,
        since: datetime,
    ) -> ContentItem | None:
        video_id = str(row.get("id") or "").strip()
        published_at = self._ytdlp_datetime(row)
        if not video_id or published_at is None or published_at < self._utc(since):
            return None
        views = self._optional_int(row.get("view_count"))
        likes = self._optional_int(row.get("like_count"))
        comments = self._optional_int(row.get("comment_count"))
        duration_seconds = self._optional_int(row.get("duration"))
        channel_id = str(row.get("channel_id") or "") or None
        channel = str(row.get("channel") or "YouTube")
        return ContentItem(
            id=self._generate_id("youtube", "video", video_id),
            source_type=SourceType.YOUTUBE,
            title=str(row.get("title") or "Untitled YouTube video"),
            url=str(
                row.get("webpage_url")
                or f"https://www.youtube.com/watch?v={video_id}"
            ),
            content=str(row.get("description") or ""),
            author=channel,
            published_at=published_at,
            profile=route.profile,
            metadata={
                "video_id": video_id,
                "channel_id": channel_id,
                "channel": channel,
                "views": views,
                "likes": likes,
                "comments": comments,
                "engagement": {
                    "views": views,
                    "likes": likes,
                    "comments": comments,
                },
                "duration": (
                    f"PT{duration_seconds}S"
                    if duration_seconds is not None
                    else None
                ),
                "duration_seconds": duration_seconds,
                "query": route.query
                if isinstance(route, YouTubeQueryConfig)
                else None,
                "category": route.category,
                "discovery_mode": discovery_mode,
                "watchlist_channel_id": route.channel_id
                if isinstance(route, YouTubeChannelConfig)
                else None,
                "channel_watchlist_name": route.name
                if isinstance(route, YouTubeChannelConfig)
                else None,
                "source_shadow": self.youtube_config.shadow,
                "source_access": "yt_dlp_web",
            },
        )

    @staticmethod
    def _ytdlp_datetime(row: dict[str, Any]) -> datetime | None:
        timestamp = row.get("timestamp")
        if timestamp is not None:
            try:
                return datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                pass
        upload_date = str(row.get("upload_date") or "")
        if len(upload_date) == 8 and upload_date.isdigit():
            try:
                return datetime.strptime(upload_date, "%Y%m%d").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                return None
        return None

    @staticmethod
    def _command_error(result: LocalCommandResult) -> str:
        return (result.stderr or result.stdout).strip()[-500:]

    async def _fetch_channels(
        self,
        channels: list[YouTubeChannelConfig],
        since: datetime,
        api_key: str,
    ) -> list[ContentItem]:
        response = await self.client.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={
                "part": "snippet,contentDetails",
                "id": ",".join(channel.channel_id for channel in channels),
                "maxResults": len(channels),
                "key": api_key,
            },
            follow_redirects=True,
        )
        self.units_used += self.CHANNELS_UNIT_COST
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("YouTube channels response must contain items")
        details_by_channel = {
            str(row.get("id")): row
            for row in rows
            if isinstance(row, dict) and row.get("id")
        }

        snippets: dict[str, dict[str, Any]] = {}
        routes: dict[str, YouTubeChannelConfig] = {}
        since_utc = self._utc(since)
        for channel in channels:
            details = details_by_channel.get(channel.channel_id)
            uploads_id = (
                ((details or {}).get("contentDetails") or {})
                .get("relatedPlaylists", {})
                .get("uploads")
            )
            if not uploads_id:
                raise ValueError(
                    f"YouTube channel {channel.channel_id} has no uploads playlist"
                )
            playlist_response = await self.client.get(
                "https://www.googleapis.com/youtube/v3/playlistItems",
                params={
                    "part": "snippet",
                    "playlistId": uploads_id,
                    "maxResults": channel.fetch_limit,
                    "key": api_key,
                },
                follow_redirects=True,
            )
            self.units_used += self.PLAYLIST_ITEMS_UNIT_COST
            playlist_response.raise_for_status()
            playlist_payload = playlist_response.json()
            playlist_rows = (
                playlist_payload.get("items")
                if isinstance(playlist_payload, dict)
                else None
            )
            if not isinstance(playlist_rows, list):
                raise ValueError("YouTube playlistItems response must contain items")
            for row in playlist_rows:
                snippet = row.get("snippet") if isinstance(row, dict) else None
                video_id = (
                    (snippet.get("resourceId") or {}).get("videoId")
                    if isinstance(snippet, dict)
                    else None
                )
                published_at = self._published_at(snippet)
                if not video_id or published_at is None or published_at < since_utc:
                    continue
                snippets[str(video_id)] = snippet
                routes[str(video_id)] = channel

        return await self._enrich_videos(
            snippets,
            api_key,
            routes=routes,
            discovery_mode="channel_uploads",
        )

    async def _fetch_query(
        self,
        query: YouTubeQueryConfig,
        since: datetime,
        api_key: str,
    ) -> list[ContentItem]:
        since_utc = (
            since.replace(tzinfo=timezone.utc)
            if since.tzinfo is None
            else since.astimezone(timezone.utc)
        )
        published_after = since_utc.isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
        search_params: dict[str, Any] = {
            "part": "snippet",
            "type": "video",
            "q": query.query,
            "order": query.order,
            "publishedAfter": published_after,
            "maxResults": query.fetch_limit,
            "relevanceLanguage": self.youtube_config.relevance_language,
            "safeSearch": "moderate",
            "videoEmbeddable": "true",
            "key": api_key,
        }
        if self.youtube_config.region_code:
            search_params["regionCode"] = self.youtube_config.region_code
        response = await self.client.get(
            "https://www.googleapis.com/youtube/v3/search",
            params=search_params,
            follow_redirects=True,
        )
        self.units_used += self.SEARCH_UNIT_COST
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("YouTube search response must contain items")

        snippets: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            video_id = (row.get("id") or {}).get("videoId")
            snippet = row.get("snippet")
            if video_id and isinstance(snippet, dict):
                snippets[str(video_id)] = snippet
        if not snippets:
            return []

        return await self._enrich_videos(
            snippets,
            api_key,
            routes={video_id: query for video_id in snippets},
            discovery_mode="search",
        )

    async def _enrich_videos(
        self,
        snippets: dict[str, dict[str, Any]],
        api_key: str,
        *,
        routes: dict[str, YouTubeQueryConfig | YouTubeChannelConfig],
        discovery_mode: str,
    ) -> list[ContentItem]:
        if not snippets:
            return []
        stats_response = await self.client.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "statistics,contentDetails",
                "id": ",".join(snippets),
                "key": api_key,
            },
            follow_redirects=True,
        )
        self.units_used += self.VIDEOS_UNIT_COST
        stats_response.raise_for_status()
        stats_payload = stats_response.json()
        stats_rows = (
            stats_payload.get("items") if isinstance(stats_payload, dict) else None
        )
        if not isinstance(stats_rows, list):
            raise ValueError("YouTube videos response must contain items")
        details_by_id = {
            str(row.get("id")): row
            for row in stats_rows
            if isinstance(row, dict) and row.get("id")
        }

        items: list[ContentItem] = []
        for video_id, snippet in snippets.items():
            published_at = self._published_at(snippet)
            if published_at is None:
                continue
            route = routes[video_id]
            details = details_by_id.get(video_id, {})
            statistics = details.get("statistics") or {}
            content_details = details.get("contentDetails") or {}
            items.append(
                ContentItem(
                    id=self._generate_id("youtube", "video", video_id),
                    source_type=SourceType.YOUTUBE,
                    title=str(snippet.get("title") or "Untitled YouTube video"),
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    content=str(snippet.get("description") or ""),
                    author=str(snippet.get("channelTitle") or "YouTube"),
                    published_at=published_at,
                    profile=route.profile,
                    metadata={
                        "video_id": video_id,
                        "channel_id": snippet.get("channelId"),
                        "channel": snippet.get("channelTitle"),
                        "views": self._optional_int(statistics.get("viewCount")),
                        "likes": self._optional_int(statistics.get("likeCount")),
                        "comments": self._optional_int(
                            statistics.get("commentCount")
                        ),
                        "engagement": {
                            "views": self._optional_int(statistics.get("viewCount")),
                            "likes": self._optional_int(statistics.get("likeCount")),
                            "comments": self._optional_int(
                                statistics.get("commentCount")
                            ),
                        },
                        "duration": content_details.get("duration"),
                        "query": route.query
                        if isinstance(route, YouTubeQueryConfig)
                        else None,
                        "category": route.category,
                        "discovery_mode": discovery_mode,
                        "watchlist_channel_id": route.channel_id
                        if isinstance(route, YouTubeChannelConfig)
                        else None,
                        "channel_watchlist_name": route.name
                        if isinstance(route, YouTubeChannelConfig)
                        else None,
                        "source_shadow": self.youtube_config.shadow,
                    },
                )
            )
        return items

    def _record_channel_health(
        self,
        channel: YouTubeChannelConfig,
        health: SourceHealthResult,
    ) -> None:
        self.last_source_results.append(
            {
                **health.model_dump(mode="json"),
                "source_type": "channel_uploads",
                "channel_id": channel.channel_id,
                "channel_name": channel.name,
                "units_used": self.units_used,
            }
        )

    def _record_health(
        self, query: YouTubeQueryConfig, health: SourceHealthResult
    ) -> None:
        self.last_source_results.append(
            {
                **health.model_dump(mode="json"),
                "source_type": "search",
                "query": query.query,
                "units_used": self.units_used,
            }
        )

    @staticmethod
    def _source_id(query: YouTubeQueryConfig) -> str:
        return f"youtube:search:{query.query}"

    @staticmethod
    def _channel_source_id(channel: YouTubeChannelConfig) -> str:
        return f"youtube:channel:{channel.channel_id}"

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )

    @staticmethod
    def _published_at(snippet: Any) -> datetime | None:
        if not isinstance(snippet, dict):
            return None
        try:
            return datetime.fromisoformat(
                str(snippet["publishedAt"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

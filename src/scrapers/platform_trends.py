"""Configurable public/aggregated platform trend source."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from .base import BaseScraper
from ..models import (
    ContentItem,
    PlatformTrendProviderConfig,
    PlatformTrendsConfig,
    SourceType,
)

logger = logging.getLogger(__name__)


class PlatformTrendsScraper(BaseScraper):
    def __init__(self, config: PlatformTrendsConfig, http_client: httpx.AsyncClient):
        super().__init__({"platform_trends": config}, http_client)
        self.trends_config = config

    async def fetch(self, since: datetime) -> list[ContentItem]:
        if not self.trends_config.enabled:
            return []
        items: list[ContentItem] = []
        for provider in self.trends_config.providers:
            if not provider.enabled:
                continue
            try:
                items.extend(await self._fetch_provider(provider, since))
            except Exception as exc:
                logger.warning(
                    "%s trends via %s unavailable, skipping: %s",
                    provider.platform,
                    provider.provider,
                    exc,
                )
        return items

    async def _fetch_provider(
        self,
        provider: PlatformTrendProviderConfig,
        since: datetime,
    ) -> list[ContentItem]:
        if provider.base_url is None:
            logger.warning(
                "%s trend provider has no base_url, skipping", provider.platform
            )
            return []

        headers: dict[str, str] = {}
        if provider.api_key_env:
            api_key = os.getenv(provider.api_key_env)
            if not api_key:
                logger.warning(
                    "%s not configured, skipping %s trends",
                    provider.api_key_env,
                    provider.platform,
                )
                return []
            prefix = provider.api_key_prefix.strip()
            headers[provider.api_key_header] = f"{prefix} {api_key}".strip()

        params = {"id": provider.source_id} if provider.source_id else None
        response = await self.client.get(
            str(provider.base_url),
            params=params,
            headers=headers,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return []
        if payload.get("code") not in (None, 200):
            logger.warning(
                "%s trends via %s returned provider code %s, skipping",
                provider.platform,
                provider.provider,
                payload.get("code"),
            )
            return []
        rows = payload.get("items") or payload.get("data") or []
        if not isinstance(rows, list):
            return []
        observed_at = self._observed_at(payload)
        since_utc = (
            since.replace(tzinfo=timezone.utc)
            if since.tzinfo is None
            else since.astimezone(timezone.utc)
        )
        if observed_at < since_utc:
            logger.warning(
                "%s trends via %s are stale (%s), skipping",
                provider.platform,
                provider.provider,
                observed_at.isoformat(),
            )
            return []
        items = []
        limit = min(provider.fetch_limit, provider.rank_limit)
        for rank, row in enumerate(rows[:limit], start=1):
            item = self._row_to_item(row, rank, observed_at, provider)
            if item is not None:
                items.append(item)
        return items

    def _row_to_item(
        self,
        row: Any,
        rank: int,
        observed_at: datetime,
        provider: PlatformTrendProviderConfig,
    ) -> ContentItem | None:
        if not isinstance(row, dict):
            return None
        title = str(row.get("title") or row.get("keyword") or "").strip()
        url = str(row.get("url") or row.get("mobileUrl") or "").strip()
        if not title or not url:
            return None
        raw_id = str(row.get("id") or title)
        hot_value = self._hot_value(row)
        content = (
            f"{provider.platform} 榜单第 {rank} 位；数据由 {provider.provider} 聚合。"
        )
        if hot_value is not None:
            content += f" 热度值：{hot_value}。"
        else:
            content += " 来源未提供可靠热度值，仅可视为榜单候选。"
        occurrence = {
            "platform": provider.platform,
            "rank": rank,
            "hot_value": hot_value,
            "url": url,
            "provider": provider.provider,
        }
        return ContentItem(
            id=self._generate_id(
                "platform_trends",
                provider.provider,
                f"{provider.platform}:{raw_id}",
            ),
            source_type=SourceType.PLATFORM_TRENDS,
            title=title,
            url=url,
            content=content,
            author=provider.provider,
            published_at=observed_at,
            profile=provider.profile,
            metadata={
                "category": provider.category,
                "platform": provider.platform,
                "provider": provider.provider,
                "source_name": f"{provider.provider}:{provider.platform}",
                "source_kind": "aggregator",
                "reliability": provider.reliability,
                "original_url": url,
                "rank": rank,
                "hot_value": hot_value,
                "engagement": (
                    {"hot_value": hot_value} if hot_value is not None else {}
                ),
                "platform_occurrences": [occurrence],
            },
        )

    @staticmethod
    def _observed_at(payload: dict[str, Any]) -> datetime:
        raw = payload.get("updatedTime") or payload.get("updateTime")
        try:
            if isinstance(raw, str) and not raw.replace(".", "", 1).isdigit():
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(
                    timezone.utc
                )
            timestamp = float(raw)
            if timestamp > 10_000_000_000:
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return datetime.now(timezone.utc)

    @staticmethod
    def _hot_value(row: dict[str, Any]) -> int | float | None:
        candidates = [
            row.get("hot_value"),
            row.get("hotValue"),
            row.get("hot"),
            row.get("score"),
        ]
        extra = row.get("extra")
        if isinstance(extra, dict):
            candidates.extend(
                [extra.get("hot_value"), extra.get("hotValue"), extra.get("hot")]
            )
        for value in candidates:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return value
            if isinstance(value, str):
                compact = value.replace(",", "").strip()
                try:
                    return float(compact) if "." in compact else int(compact)
                except ValueError:
                    continue
        return None

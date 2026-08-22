"""Configurable public/aggregated platform trend source."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
        self.last_provider_health: list[dict[str, Any]] = []

    async def fetch(self, since: datetime) -> list[ContentItem]:
        if not self.trends_config.enabled:
            self.last_provider_health = []
            return []
        self.last_provider_health = []
        items: list[ContentItem] = []
        for provider in self.trends_config.providers:
            if not provider.enabled:
                continue
            try:
                items.extend(await self._fetch_provider(provider, since))
            except Exception as exc:
                self._record_health(
                    provider,
                    "http_error",
                    error=str(exc),
                )
                logger.warning(
                    "%s trends via %s unavailable, skipping: %s",
                    provider.platform,
                    provider.provider,
                    exc,
                )
        return self._merge_exact_topics(items)

    def _record_health(
        self,
        provider: PlatformTrendProviderConfig,
        status: str,
        *,
        item_count: int = 0,
        latest_visible_at: str | None = None,
        title_count: int = 0,
        url_count: int = 0,
        hot_value_count: int = 0,
        http_status: int | None = None,
        error: str | None = None,
    ) -> None:
        self.last_provider_health.append(
            {
                "provider": provider.provider,
                "provider_name": provider.provider_name or provider.provider,
                "platform": provider.platform,
                "status": status,
                "item_count": item_count,
                "latest_visible_at": latest_visible_at,
                "title_count": title_count,
                "url_count": url_count,
                "hot_value_count": hot_value_count,
                "http_status": http_status,
                "error": error,
            }
        )

    async def _fetch_provider(
        self,
        provider: PlatformTrendProviderConfig,
        since: datetime,
    ) -> list[ContentItem]:
        if provider.base_url is None:
            self._record_health(provider, "schema_changed", error="missing_base_url")
            logger.warning(
                "%s trend provider has no base_url, skipping", provider.platform
            )
            return []

        headers: dict[str, str] = {}
        query_params = dict(provider.query_params)
        body_params = dict(provider.body_params)
        if provider.api_key_env:
            api_key = os.getenv(provider.api_key_env)
            if not api_key:
                self._record_health(provider, "missing_token", error=provider.api_key_env)
                logger.warning(
                    "%s not configured, skipping %s provider.",
                    provider.api_key_env,
                    provider.provider,
                )
                return []
            prefix = provider.api_key_prefix.strip()
            value = f"{prefix} {api_key}".strip()
            if provider.auth_type == "query":
                query_params[provider.api_key_header] = value
            else:
                headers[provider.api_key_header] = value

        if provider.source_id:
            target_params = (
                body_params if provider.request_method == "POST" else query_params
            )
            target_params.setdefault("id", provider.source_id)

        request_url = str(provider.base_url).rstrip("/")
        if provider.endpoint:
            request_url += "/" + provider.endpoint.lstrip("/")
        request_kwargs = {
            "params": query_params or None,
            "headers": headers,
            "follow_redirects": True,
        }
        if provider.request_method == "POST":
            response = await self.client.post(
                request_url,
                json=body_params or None,
                **request_kwargs,
            )
        else:
            response = await self.client.get(request_url, **request_kwargs)
        response.raise_for_status()
        http_status = getattr(response, "status_code", None)
        payload = response.json()
        if not isinstance(payload, dict):
            self._record_health(
                provider,
                "schema_changed",
                http_status=http_status,
                error="payload_not_object",
            )
            return []
        if payload.get("code") not in (None, 200) or payload.get("success") is False:
            self._record_health(
                provider,
                "business_error",
                http_status=http_status,
                error=f"code={payload.get('code')},success={payload.get('success')}",
            )
            logger.warning(
                "%s trends via %s returned provider code %s, skipping",
                provider.platform,
                provider.provider,
                payload.get("code"),
            )
            return []
        adapter_payload = payload
        if provider.response_adapter == "alapi_tophub":
            data = payload.get("data")
            if not isinstance(data, dict):
                self._record_health(
                    provider,
                    "schema_changed",
                    http_status=http_status,
                    error="missing_data_object",
                )
                return []
            rows = data.get("list") or []
            adapter_payload = {
                "updatedTime": data.get("last_update") or data.get("last_time")
            }
        else:
            rows = payload.get("items") or payload.get("data") or []
        if not isinstance(rows, list):
            self._record_health(
                provider,
                "schema_changed",
                http_status=http_status,
                error="rows_not_list",
            )
            return []
        observed_at = self._observed_at(
            adapter_payload, provider.observed_timezone
        )
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
            self._record_health(
                provider,
                "stale",
                http_status=http_status,
                latest_visible_at=observed_at.isoformat(),
                error="snapshot_older_than_since",
            )
            return []
        items = []
        limit = min(provider.fetch_limit, provider.rank_limit)
        for rank, row in enumerate(rows[:limit], start=1):
            item = self._row_to_item(row, rank, observed_at, provider)
            if item is not None:
                items.append(item)
        title_count = sum(
            bool(isinstance(row, dict) and str(row.get("title") or row.get("keyword") or "").strip())
            for row in rows[:limit]
        )
        url_count = sum(
            bool(
                isinstance(row, dict)
                and str(row.get("url") or row.get("mobileUrl") or row.get("link") or "").strip()
            )
            for row in rows[:limit]
        )
        hot_value_count = sum(
            self._hot_value(row) is not None
            for row in rows[:limit]
            if isinstance(row, dict)
        )
        self._record_health(
            provider,
            "ok" if items else ("schema_changed" if rows else "empty"),
            item_count=len(items),
            latest_visible_at=observed_at.isoformat(),
            title_count=title_count,
            url_count=url_count,
            hot_value_count=hot_value_count,
            http_status=http_status,
            error="no_valid_title_url_rows" if rows and not items else None,
        )
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
        url = str(
            row.get("url") or row.get("mobileUrl") or row.get("link") or ""
        ).strip()
        url_is_search_fallback = False
        if title and not url:
            url = self._search_fallback_url(provider.platform, title)
            url_is_search_fallback = bool(url)
        if not title or not url:
            return None
        raw_id = str(row.get("id") or title)
        hot_value = self._hot_value(row)
        provider_name = provider.provider_name or provider.provider
        core_platform = provider.platform in {
            "weibo",
            "douyin",
            "xiaohongshu",
            "wechat",
        }
        source_tier = "core" if core_platform else "supplemental"
        if provider.provider == "alapi_tophub" and core_platform:
            provider_role = "cross_validation"
        elif core_platform:
            provider_role = "core_collection"
        else:
            provider_role = "supplemental_discovery"
        content = f"{provider.platform} 榜单第 {rank} 位；数据由 {provider_name} 聚合。"
        if hot_value is not None:
            content += f" 热度值：{hot_value}。"
        else:
            content += " 来源未提供可靠热度值，仅可视为榜单候选。"
        occurrence = {
            "platform": provider.platform,
            "rank": rank,
            "rank_limit": provider.rank_limit,
            "hot_value": hot_value,
            "url": url,
            "provider": provider_name,
            "provider_id": provider.provider,
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
            author=provider_name,
            published_at=observed_at,
            profile=provider.profile,
            metadata={
                "category": provider.category,
                "platform": provider.platform,
                "provider": provider.provider,
                "provider_name": provider_name,
                "providers": [provider_name],
                "source_name": f"{provider.provider}:{provider.platform}",
                "source_kind": "aggregator",
                "source_tier": source_tier,
                "provider_role": provider_role,
                "reliability": provider.reliability,
                "original_url": url,
                "url_is_search_fallback": url_is_search_fallback,
                "source_url_kind": "search_fallback" if url_is_search_fallback else "original",
                "rank": rank,
                "rank_limit": provider.rank_limit,
                "hot_value": hot_value,
                "engagement": (
                    {"hot_value": hot_value} if hot_value is not None else {}
                ),
                "platform_occurrences": [occurrence],
                "platforms": [provider.platform],
                "cross_platform_count": 1,
            },
        )

    @staticmethod
    def _observed_at(payload: dict[str, Any], timezone_name: str = "UTC") -> datetime:
        raw = payload.get("updatedTime") or payload.get("updateTime")
        try:
            if isinstance(raw, str) and not raw.replace(".", "", 1).isdigit():
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    try:
                        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
                    except ZoneInfoNotFoundError:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
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
            row.get("other"),
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
                unit_match = re.search(r"(-?\d+(?:\.\d+)?)\s*([万亿]?)", compact)
                if unit_match:
                    number = float(unit_match.group(1))
                    multiplier = {"": 1, "万": 10_000, "亿": 100_000_000}[
                        unit_match.group(2)
                    ]
                    parsed = number * multiplier
                    return int(parsed) if parsed.is_integer() else parsed
                try:
                    return float(compact) if "." in compact else int(compact)
                except ValueError:
                    continue
        return None

    @staticmethod
    def _search_fallback_url(platform: str, title: str) -> str | None:
        query = quote(title, safe="")
        hosts = {
            "weibo": f"https://s.weibo.com/weibo?q={query}",
            "douyin": f"https://www.douyin.com/search/{query}",
            "zhihu": f"https://www.zhihu.com/search?q={query}",
            "bilibili": f"https://search.bilibili.com/all?keyword={query}",
            "toutiao": f"https://so.toutiao.com/search?keyword={query}",
            "baidu": f"https://www.baidu.com/s?wd={query}",
            "36kr": f"https://36kr.com/search/articles/{query}",
        }
        return hosts.get(platform)

    @staticmethod
    def _topic_key(title: str) -> str:
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", title.casefold())

    @classmethod
    def _merge_exact_topics(cls, items: list[ContentItem]) -> list[ContentItem]:
        """Merge exact normalized titles before AI analysis.

        Provider confirmation and cross-platform occurrence are stored as
        separate metadata dimensions so two aggregators never masquerade as
        two social platforms.
        """
        merged: list[ContentItem] = []
        by_title: dict[str, ContentItem] = {}
        for item in items:
            key = cls._topic_key(item.title)
            primary = by_title.get(key)
            if not key or primary is None:
                by_title[key] = item
                merged.append(item)
                continue

            rows = [
                *(primary.metadata.get("platform_occurrences") or []),
                *(item.metadata.get("platform_occurrences") or []),
            ]
            unique_rows: list[dict[str, Any]] = []
            seen: set[tuple[str, str, str]] = set()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                occurrence_key = (
                    str(row.get("platform") or ""),
                    str(row.get("provider") or ""),
                    str(row.get("url") or ""),
                )
                if occurrence_key in seen:
                    continue
                seen.add(occurrence_key)
                unique_rows.append(row)

            providers = list(
                dict.fromkeys(
                    str(row.get("provider"))
                    for row in unique_rows
                    if row.get("provider")
                )
            )
            platforms = list(
                dict.fromkeys(
                    str(row.get("platform"))
                    for row in unique_rows
                    if row.get("platform")
                )
            )
            primary.metadata["platform_occurrences"] = unique_rows
            primary.metadata["providers"] = providers
            primary.metadata["platforms"] = platforms
            primary.metadata["cross_platform_count"] = len(platforms)
            if len(providers) > 1:
                primary.content += f" 多个数据来源确认：{' + '.join(providers)}。"
            if len(platforms) > 1:
                primary.content += f" 该话题同时出现在{'、'.join(platforms)}榜单。"
        return merged

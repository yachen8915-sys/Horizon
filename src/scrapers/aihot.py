"""AI HOT v1 anonymous read-only supplement scraper."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..models import AIHotConfig, ContentItem, SourceType
from .base import BaseScraper

logger = logging.getLogger(__name__)


class AIHotScraper(BaseScraper):
    BASE_URL = "https://aihot.virxact.com/api/v1"

    def __init__(self, config: AIHotConfig, http_client: httpx.AsyncClient):
        super().__init__(config.model_dump(), http_client)
        self.cfg = config

    async def fetch(self, since: datetime) -> list[ContentItem]:
        if not self.cfg.enabled:
            return []
        items: list[ContentItem] = []
        seen: set[str] = set()
        windows = ["24h", "7d"] if self.cfg.fetch_24h and self.cfg.fetch_7d else (["24h"] if self.cfg.fetch_24h else ["7d"] if self.cfg.fetch_7d else [])
        for window in windows:
            payload = await self._get("/items", {"mode": "selected", "window": window, "limit": self.cfg.limit})
            for raw in (payload.get("items") or []):
                item = self._to_item(raw, since)
                if item and item.id not in seen:
                    seen.add(item.id); items.append(item)
            await asyncio.sleep(self.cfg.request_interval_seconds)
        for keyword in self.cfg.keywords:
            if not keyword.strip():
                continue
            payload = await self._get("/items", {"mode": "selected", "window": "7d", "q": keyword.strip(), "limit": self.cfg.limit})
            for raw in (payload.get("items") or []):
                item = self._to_item(raw, since)
                if item and item.id not in seen:
                    seen.add(item.id); items.append(item)
            await asyncio.sleep(self.cfg.request_interval_seconds)
        if self.cfg.fetch_hot_topics:
            payload = await self._get("/hot-topics", {})
            for raw in self._topic_items(payload):
                item = self._to_item(raw, since, hot=True)
                if item and item.id not in seen:
                    seen.add(item.id); items.append(item)
        return items

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.client.get(
                self.BASE_URL + path,
                params=params,
                headers={"Accept": "application/json", "User-Agent": "Horizon/0.1"},
                timeout=20.0,
            )
            if response.status_code == 304:
                return {}
            if response.status_code in (429, 503):
                retry_after = response.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    await asyncio.sleep(min(int(retry_after), 60))
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("AI HOT request failed for %s: %s", path, exc)
            return {}

    def _to_item(self, raw: dict[str, Any], since: datetime, hot: bool = False) -> ContentItem | None:
        native_id = str(raw.get("id") or "").strip()
        title = str(raw.get("title") or "").strip()
        links = raw.get("links") or {}
        original = str(links.get("original") or "").strip()
        aihot = str(links.get("aihot") or "").strip()
        published = self._date(raw.get("publishedAt") or raw.get("discoveredAt"))
        if not native_id or not title or not original or not published or published < self._utc(since):
            return None
        source_name = str((raw.get("source") or {}).get("name") or "AI HOT").strip()
        metadata = {
            "feed_name": source_name,
            "category": raw.get("category"),
            "aihot_url": aihot,
            "original_url": original,
            "aihot_summary": raw.get("summary"),
            "aihot_score": raw.get("score"),
            "source_kind": self._source_kind(source_name, original),
            "needs_original_verification": True,
            "hot_topic": hot,
        }
        return ContentItem(
            id=self._generate_id(SourceType.AIHOT.value, "item", native_id),
            source_type=SourceType.AIHOT,
            title=title,
            url=original,
            content=raw.get("summary") or title,
            author=source_name,
            published_at=published,
            profile="pangmen-topic-radar",
            metadata=metadata,
        )

    @staticmethod
    def _topic_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw = payload.get("topics") or payload.get("items") or []
        result = []
        for topic in raw:
            if isinstance(topic, dict):
                result.extend(topic.get("items") or [topic])
        return result

    @staticmethod
    def _date(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    @staticmethod
    def _source_kind(name: str, url: str) -> str:
        host = (urlsplit(url).hostname or "").lower()
        lower = name.lower()
        if host in {"x.com", "twitter.com", "www.x.com", "www.twitter.com"} or name.startswith("x：") or name.startswith("x:"):
            return "X 推文"
        if "公众号" in name or "微信" in name or "mp.weixin" in host:
            return "公众号"
        if any(word in lower for word in ("openai", "anthropic", "google", "microsoft", "figma", "notion", "qwen", "minimax", "kimi", "字节", "官方", "official")):
            return "官方来源"
        if any(word in lower for word in ("blog", "news", "tech", "媒体", "rss", "cloudflare", "github", "huggingface", "verge", "decoder")):
            return "AI 媒体"
        return "个人作者"

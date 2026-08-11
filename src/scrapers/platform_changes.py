"""Public, stateful monitoring for platform rules and product changes."""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import unquote_plus, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from .._file_utils import _atomic_write_text
from ..models import (
    ContentItem,
    GoogleNewsConfig,
    PlatformChangeWatcherConfig,
    PlatformChangesConfig,
    SourceType,
)
from .base import BaseScraper
from .google_news import GoogleNewsScraper

logger = logging.getLogger(__name__)

_CHANGE_SIGNAL = re.compile(
    r"新增|修改|调整|下线|上线|开放|扩大|缩小|门槛|生效|更新|灰度|公示|修订|取消|升级"
)
_CHANGE_TIME_VERB = r"生效|实施|上线|下线|开放|启用|停用|更新|调整|修订|发布"
_DATE_TOKEN = r"(?:20\d{2}[年/.-]\d{1,2}[月/.-]\d{1,2}日?|\d{1,2}月\d{1,2}日)"
_CHANGE_TIME_PATTERNS = (
    re.compile(
        rf"(?:已|将)?(?:于|自|从)?\s*(?P<date>{_DATE_TOKEN})\s*(?:起|开始|正式)?\s*(?:{_CHANGE_TIME_VERB})"
    ),
    re.compile(
        rf"(?:{_CHANGE_TIME_VERB})(?:时间|日期)?(?:为|：|:|于)?\s*(?P<date>{_DATE_TOKEN})"
    ),
)
_TRACKING_QUERY_PARAMETERS = {
    "_ga",
    "dclid",
    "fbclid",
    "gclid",
    "msclkid",
    "ttclid",
}
_REMOVED_TAGS = ("script", "style", "noscript", "svg", "nav", "header", "footer")


def normalize_page_text(html_text: str, ignore_patterns: list[str] | None = None) -> str:
    """Extract stable visible text while ignoring markup and whitespace noise."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    for tag in soup.find_all(_REMOVED_TAGS):
        tag.decompose()
    text = soup.get_text("\n")
    for pattern in ignore_patterns or []:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    lines = []
    for raw_line in text.replace("\u200b", "").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines)


def normalize_monitor_url(url: str) -> str:
    """Return a stable state key while preserving meaningful query parameters."""
    parsed = urlsplit(url)
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold()
    port = parsed.port
    if (scheme, port) in {("http", 80), ("https", 443)}:
        port = None
    netloc = f"{host}:{port}" if port else host
    path = parsed.path.rstrip("/") or "/"
    query_parts = []
    for part in parsed.query.split("&") if parsed.query else []:
        name = unquote_plus(part.partition("=")[0]).casefold()
        if name.startswith("utm_") or name in _TRACKING_QUERY_PARAMETERS:
            continue
        query_parts.append(part)
    return urlunsplit((scheme, netloc, path, "&".join(query_parts), ""))


class PlatformChangesScraper(BaseScraper):
    """Monitor public indexes, long-lived pages, and Google News RSS queries."""

    SOURCE_TYPE = SourceType.PLATFORM_CHANGES

    def __init__(
        self,
        config: PlatformChangesConfig,
        http_client: httpx.AsyncClient,
        *,
        now_provider: Callable[[], datetime] | None = None,
    ):
        super().__init__({"platform_changes": config}, http_client)
        self.pc_config = config
        self.state_path = Path(config.state_file)
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    async def fetch(self, since: datetime) -> list[ContentItem]:
        if not self.pc_config.enabled:
            return []

        state = self._load_state()
        watcher_states = state.setdefault("watchers", {})
        items: list[ContentItem] = []
        for watcher in self.pc_config.watchers:
            if not watcher.enabled:
                continue
            try:
                watcher_state = watcher_states.get(watcher.name)
                if watcher.mode == "index":
                    produced, updated = await self._fetch_index(watcher, watcher_state)
                elif watcher.mode == "page_diff":
                    produced, updated = await self._fetch_page_diff(watcher, watcher_state)
                else:
                    produced, updated = await self._fetch_search_rss(watcher, watcher_state)
                watcher_states[watcher.name] = updated
                items.extend(produced)
            except Exception as exc:
                logger.warning(
                    "Platform change watcher %s failed and was skipped: %s",
                    watcher.name,
                    exc,
                )

        self._save_state(state)
        return items

    async def _fetch_index(
        self,
        watcher: PlatformChangeWatcherConfig,
        watcher_state: dict | None,
    ) -> tuple[list[ContentItem], dict]:
        response = await self.client.get(str(watcher.url), follow_redirects=True)
        response.raise_for_status()
        links = self._index_links(response.text, watcher)
        if not links:
            raise ValueError(
                "no matching public anchors found; page may be JavaScript-rendered, use search_rss fallback"
            )
        now = self._now()
        current = dict(watcher_state or {})
        seen_urls = self._normalized_seen_urls(current.get("seen_urls"))
        is_baseline = watcher_state is None
        produced: list[ContentItem] = []

        for title, url, page_published_at in links[: watcher.fetch_limit]:
            url_key = normalize_monitor_url(url)
            if url_key in seen_urls:
                continue
            seen_urls[url_key] = now.isoformat()
            if is_baseline:
                continue
            content = title
            try:
                detail = await self.client.get(url, follow_redirects=True)
                detail.raise_for_status()
                extracted = normalize_page_text(detail.text)
                if extracted:
                    content = extracted[:12_000]
            except Exception as exc:
                logger.warning(
                    "Platform change detail %s could not be read; keeping index item: %s",
                    url,
                    exc,
                )
            published_at = page_published_at or now
            produced.append(
                self._make_item(
                    watcher,
                    subtype="index",
                    native_id=self._hash(url),
                    title=title,
                    url=url,
                    content=content,
                    published_at=published_at,
                    metadata={
                        "discovery_mode": "index",
                        "published_at_basis": "page"
                        if page_published_at
                        else "first_seen",
                    },
                )
            )

        current.update({"seen_urls": seen_urls, "last_seen": now.isoformat()})
        return produced, current

    async def _fetch_page_diff(
        self,
        watcher: PlatformChangeWatcherConfig,
        watcher_state: dict | None,
    ) -> tuple[list[ContentItem], dict]:
        response = await self.client.get(str(watcher.url), follow_redirects=True)
        response.raise_for_status()
        normalized = normalize_page_text(response.text, watcher.ignore_patterns)
        if len(normalized) < watcher.min_content_chars:
            raise ValueError(
                f"normalized page text is too short ({len(normalized)} chars); likely JS-only or changed DOM"
            )

        now = self._now()
        current_hash = self._hash(normalized)
        if watcher_state is None:
            return [], {
                "normalized_text_hash": current_hash,
                "normalized_text": normalized,
                "last_seen": now.isoformat(),
                "last_changed": None,
            }

        previous_hash = str(watcher_state.get("normalized_text_hash") or "")
        previous_text = str(watcher_state.get("normalized_text") or "")
        updated = dict(watcher_state)
        updated["last_seen"] = now.isoformat()
        if previous_hash == current_hash:
            return [], updated

        diff_lines = list(
            difflib.unified_diff(
                previous_text.splitlines(),
                normalized.splitlines(),
                fromfile="previous",
                tofile="current",
                lineterm="",
            )
        )
        diff_excerpt = "\n".join(diff_lines)[:6_000]
        updated.update(
            {
                "normalized_text_hash": current_hash,
                "normalized_text": normalized,
                "last_changed": now.isoformat(),
            }
        )
        content = (
            f"旧版关键文本：\n{previous_text[:8_000]}\n\n"
            f"新版关键文本：\n{normalized[:8_000]}\n\n"
            f"diff：\n{diff_excerpt}"
        )
        item = self._make_item(
            watcher,
            subtype="page_diff",
            native_id=current_hash[:16],
            title=f"{self._platform_label(watcher.platform)}公开页面发生变化：{watcher.name}",
            url=str(watcher.url),
            content=content,
            published_at=now,
            metadata={
                "discovery_mode": "page_diff",
                "changed_at": now.isoformat(),
                "previous_hash": previous_hash,
                "current_hash": current_hash,
                "diff_excerpt": diff_excerpt,
            },
        )
        return [item], updated

    async def _fetch_search_rss(
        self,
        watcher: PlatformChangeWatcherConfig,
        watcher_state: dict | None,
    ) -> tuple[list[ContentItem], dict]:
        now = self._now()
        lookback_since = now - timedelta(days=self.pc_config.lookback_days)
        google_config = GoogleNewsConfig(
            enabled=True,
            query=watcher.query or "",
            language=watcher.language,
            country=watcher.country,
            ceid=watcher.ceid,
            max_results=watcher.fetch_limit,
            category=watcher.category,
            profile=watcher.profile,
        )
        discovered = await GoogleNewsScraper(google_config, self.client).fetch(
            lookback_since
        )
        current = dict(watcher_state or {})
        seen_urls = self._normalized_seen_urls(current.get("seen_urls"))
        is_baseline = watcher_state is None
        produced: list[ContentItem] = []
        for discovered_item in discovered[: watcher.fetch_limit]:
            if self._ensure_utc(discovered_item.published_at) < lookback_since:
                continue
            text = f"{discovered_item.title}\n{discovered_item.content or ''}"
            if not self._matches_patterns(text, str(discovered_item.url), watcher):
                continue
            if not _CHANGE_SIGNAL.search(text):
                continue
            url = str(discovered_item.url)
            url_key = normalize_monitor_url(url)
            if url_key in seen_urls:
                continue
            seen_urls[url_key] = now.isoformat()
            if is_baseline or watcher.source_level == "unverified":
                continue

            actual_change_at = self._extract_actual_change_at(text, watcher, now)
            if actual_change_at is not None and actual_change_at < lookback_since:
                continue

            source_level, attribution = self._resolve_discovery_level(
                watcher, discovered_item
            )
            metadata = {
                "discovery_mode": "search_rss",
                "discovery_source": "google_news",
                "original_source_type": discovered_item.source_type.value,
                "original_id": discovered_item.id,
                "original_url": url,
                "source_name": discovered_item.author,
                "source_level": source_level,
                "article_published_at": self._ensure_utc(
                    discovered_item.published_at
                ).isoformat(),
                "change_time_confidence": "explicit"
                if actual_change_at is not None
                else "unconfirmed",
            }
            if actual_change_at is not None:
                metadata["actual_change_at"] = actual_change_at.isoformat()
            if attribution:
                metadata["source_attribution"] = attribution
            produced.append(
                self._make_item(
                    watcher,
                    subtype="search_rss",
                    native_id=discovered_item.id,
                    title=discovered_item.title,
                    url=url,
                    content=discovered_item.content or discovered_item.title,
                    published_at=discovered_item.published_at,
                    author=discovered_item.author,
                    metadata=metadata,
                )
            )

        current.update({"seen_urls": seen_urls, "last_seen": now.isoformat()})
        return produced, current

    def _index_links(
        self, html_text: str, watcher: PlatformChangeWatcherConfig
    ) -> list[tuple[str, str, datetime | None]]:
        soup = BeautifulSoup(html_text or "", "html.parser")
        base_url = str(watcher.url)
        base_host = (urlsplit(base_url).hostname or "").casefold()
        rows: list[tuple[str, str, datetime | None]] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            title = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
            if not title:
                continue
            url = urljoin(base_url, str(anchor.get("href")))
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"}:
                continue
            if watcher.same_domain_only and (parsed.hostname or "").casefold() != base_host:
                continue
            if url in seen or not self._matches_patterns(title, url, watcher):
                continue
            seen.add(url)
            rows.append((title, url, self._anchor_timestamp(anchor, watcher)))
        return rows

    @staticmethod
    def _normalized_seen_urls(value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, str] = {}
        for raw_url, first_seen in value.items():
            normalized.setdefault(normalize_monitor_url(str(raw_url)), str(first_seen))
        return normalized

    @staticmethod
    def _extract_actual_change_at(
        text: str,
        watcher: PlatformChangeWatcherConfig,
        now: datetime,
    ) -> datetime | None:
        for pattern in _CHANGE_TIME_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            raw = match.group("date")
            full_match = re.fullmatch(
                r"(?P<year>20\d{2})[年/.-](?P<month>\d{1,2})[月/.-](?P<day>\d{1,2})日?",
                raw,
            )
            if full_match:
                year = int(full_match.group("year"))
                month = int(full_match.group("month"))
                day = int(full_match.group("day"))
            else:
                short_match = re.fullmatch(
                    r"(?P<month>\d{1,2})月(?P<day>\d{1,2})日", raw
                )
                if not short_match:
                    continue
                local_now = now.astimezone(ZoneInfo(watcher.observed_timezone))
                year = local_now.year
                month = int(short_match.group("month"))
                day = int(short_match.group("day"))
            try:
                local_value = datetime(
                    year,
                    month,
                    day,
                    tzinfo=ZoneInfo(watcher.observed_timezone),
                )
            except ValueError:
                continue
            return local_value.astimezone(timezone.utc)
        return None

    @staticmethod
    def _anchor_timestamp(anchor, watcher: PlatformChangeWatcherConfig) -> datetime | None:  # type: ignore[no-untyped-def]
        parent = anchor.find_parent()
        time_tag = parent.find("time") if parent is not None else None
        candidates = []
        if time_tag is not None:
            candidates.extend(
                [time_tag.get("datetime"), time_tag.get("data-time"), time_tag.get_text(" ", strip=True)]
            )
        if parent is not None:
            text = parent.get_text(" ", strip=True)
            match = re.search(r"20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?", text)
            if match:
                candidates.append(match.group(0))
        for raw in candidates:
            value = str(raw or "").strip()
            if not value:
                continue
            normalized = (
                value.replace("年", "-").replace("月", "-").replace("日", "")
            )
            try:
                parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo(watcher.observed_timezone))
            return parsed.astimezone(timezone.utc)
        return None

    @staticmethod
    def _matches_patterns(
        text: str,
        url: str,
        watcher: PlatformChangeWatcherConfig,
    ) -> bool:
        value = f"{text}\n{url}"
        if watcher.include_patterns and not any(
            re.search(pattern, value, flags=re.IGNORECASE)
            for pattern in watcher.include_patterns
        ):
            return False
        if any(
            re.search(pattern, value, flags=re.IGNORECASE)
            for pattern in watcher.exclude_patterns
        ):
            return False
        return True

    @staticmethod
    def _resolve_discovery_level(
        watcher: PlatformChangeWatcherConfig,
        item: ContentItem,
    ) -> tuple[str, str | None]:
        host = (urlsplit(str(item.url)).hostname or "").casefold()
        if any(
            host == domain.casefold() or host.endswith(f".{domain.casefold()}")
            for domain in watcher.official_domains
        ):
            return "official", None

        if watcher.source_level == "official_republished":
            blob = f"{item.title}\n{item.content or ''}"
            keyword = next(
                (value for value in watcher.attribution_keywords if value in blob),
                None,
            )
            if keyword:
                publisher = (item.author or "来源媒体").strip()
                return "official_republished", f"{keyword}，经{publisher}转述"
            return "secondary", None
        return watcher.source_level, None

    def _make_item(
        self,
        watcher: PlatformChangeWatcherConfig,
        *,
        subtype: str,
        native_id: str,
        title: str,
        url: str,
        content: str,
        published_at: datetime,
        metadata: dict,
        author: str | None = None,
    ) -> ContentItem:
        base_metadata = {
            "watcher": watcher.name,
            "platform": watcher.platform,
            "change_types": list(watcher.change_types),
            "source_level": watcher.source_level,
            "category": watcher.category,
        }
        base_metadata.update(metadata)
        return ContentItem(
            id=self._generate_id("platform_changes", subtype, native_id),
            source_type=self.SOURCE_TYPE,
            title=title,
            url=url,
            content=content,
            author=author or watcher.name,
            published_at=self._ensure_utc(published_at),
            profile=watcher.profile,
            metadata=base_metadata,
        )

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return {"version": 1, "watchers": {}}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get("watchers"), dict):
                raise ValueError("invalid state root")
            return value
        except Exception as exc:
            logger.warning("Invalid platform change state; starting a new baseline: %s", exc)
            return {"version": 1, "watchers": {}}

    def _save_state(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(
            self.state_path,
            json.dumps(state, ensure_ascii=False, indent=2),
        )

    def _now(self) -> datetime:
        return self._ensure_utc(self._now_provider())

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _platform_label(platform: str) -> str:
        return {
            "douyin": "抖音",
            "xiaohongshu": "小红书",
            "bilibili": "B站",
            "wechat": "视频号/微信小店",
        }.get(platform, platform)

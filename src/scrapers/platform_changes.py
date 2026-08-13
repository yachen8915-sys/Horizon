"""Public, stateful monitoring for platform rules and product changes."""

from __future__ import annotations

import ast
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
_PUBLIC_PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
}
_RETRYABLE_SEARCH_STATUSES = {
    "resolution_failed",
    "fetch_failed",
    "unconfirmed",
}


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
        self.last_watcher_results: list[dict[str, object]] = []

    async def fetch(self, since: datetime) -> list[ContentItem]:
        if not self.pc_config.enabled:
            return []

        state = self._load_state()
        watcher_states = state.setdefault("watchers", {})
        items: list[ContentItem] = []
        self.last_watcher_results = []
        for watcher in self.pc_config.watchers:
            if not watcher.enabled:
                continue
            try:
                watcher_state = watcher_states.get(watcher.name)
                if watcher.mode == "index":
                    produced, updated = await self._fetch_index(watcher, watcher_state)
                elif watcher.mode == "page_diff":
                    produced, updated = await self._fetch_page_diff(watcher, watcher_state)
                elif watcher.mode == "search_rss":
                    produced, updated = await self._fetch_search_rss(watcher, watcher_state)
                elif watcher.mode == "xiaohongshu_rules":
                    produced, updated = await self._fetch_xiaohongshu_rules(
                        watcher, watcher_state
                    )
                elif watcher.mode == "xiaohongshu_help_api":
                    produced, updated = await self._fetch_xiaohongshu_help_api(
                        watcher, watcher_state
                    )
                else:
                    produced, updated = await self._fetch_bilibili_bundle_diff(
                        watcher, watcher_state
                    )
                watcher_states[watcher.name] = updated
                is_baseline = watcher_state is None
                status = "baseline" if is_baseline else ("no_change" if not produced else "new_items")
                content_count = self._health_content_count(watcher, updated, produced)
                if is_baseline:
                    status = "ok"
                items.extend(produced)
                self.last_watcher_results.append(
                    {
                        "name": watcher.name,
                        "mode": watcher.mode,
                        "status": status,
                        "http_status": "ok",
                        "item_count": len(produced),
                        "content_count": content_count,
                        "new_count": len(produced),
                        "visible_date": self._health_visible_date(updated),
                        "coverage": "configured_scope",
                        "search_rss_fallback": watcher.mode == "search_rss",
                        "health_status": "baseline" if is_baseline else status,
                        "baseline_created": watcher_state is None,
                        **({
                            "url_dedup_count": updated.get("url_dedup_count", 0),
                            "qualified_candidate_count": updated.get("qualified_candidate_count", len(produced)),
                        } if watcher.mode == "search_rss" else {}),
                    }
                )
            except Exception as exc:
                logger.warning(
                    "Platform change watcher %s failed and was skipped: %s",
                    watcher.name,
                    exc,
                )
                self.last_watcher_results.append(
                    {
                        "name": watcher.name,
                        "mode": watcher.mode,
                        "status": self._health_error_status(exc),
                        "item_count": 0,
                        "content_count": 0,
                        "new_count": 0,
                        "visible_date": None,
                        "coverage": "unknown",
                        "search_rss_fallback": watcher.mode == "search_rss",
                        "health_status": "failed",
                        "baseline_created": False,
                        "warning": str(exc),
                        "error_reason": str(exc),
                    }
                )

        self._save_state(state)
        return items

    @staticmethod
    def _health_error_status(exc: Exception) -> str:
        message = str(exc).casefold()
        if "javascript" in message or "shell" in message or "too short" in message:
            return "shell_page"
        if "bundle" in message or "data was not found" in message:
            return "structure_changed"
        if "412" in message or "403" in message or "429" in message:
            return "blocked_or_rate_limited"
        return "warning"

    @staticmethod
    def _health_visible_date(updated: dict) -> str | None:
        for key in ("last_changed", "last_seen"):
            value = updated.get(key)
            if value:
                return str(value)
        return None

    @staticmethod
    def _health_content_count(
        watcher: PlatformChangeWatcherConfig, updated: dict, produced: list[ContentItem]
    ) -> int:
        if produced:
            return len(produced)
        if watcher.mode in {"page_diff", "bilibili_bundle_diff"}:
            return 1 if updated.get("normalized_text_hash") else 0
        if watcher.mode in {"index", "xiaohongshu_rules", "xiaohongshu_help_api", "search_rss"}:
            value = updated.get("visible_count")
            return int(value) if isinstance(value, int) else len(updated.get("seen_urls") or {})
        return 0

    async def _fetch_index(
        self,
        watcher: PlatformChangeWatcherConfig,
        watcher_state: dict | None,
    ) -> tuple[list[ContentItem], dict]:
        response = await self.client.get(str(watcher.url), follow_redirects=True)
        response.raise_for_status()
        if self._looks_like_shell(response.text):
            raise ValueError("page returned a JavaScript shell without public content")
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
        current["visible_count"] = len(links)
        return produced, current

    async def _fetch_xiaohongshu_rules(
        self,
        watcher: PlatformChangeWatcherConfig,
        watcher_state: dict | None,
    ) -> tuple[list[ContentItem], dict]:
        response = await self.client.post(
            str(watcher.url),
            json={"pageNo": 1, "pageSize": watcher.fetch_limit},
            headers={
                **_PUBLIC_PAGE_HEADERS,
                "Referer": "https://school.xiaohongshu.com/newhome",
                "Origin": "https://school.xiaohongshu.com",
            },
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise ValueError("xiaohongshu public rules API returned an invalid response")
        data = payload.get("data")
        rows = data.get("dataList") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ValueError("xiaohongshu public rules API did not return dataList")

        now = self._now()
        current = dict(watcher_state or {})
        seen_urls = self._normalized_seen_urls(current.get("seen_urls"))
        is_baseline = watcher_state is None
        produced: list[ContentItem] = []
        for row in rows[: watcher.fetch_limit]:
            if not isinstance(row, dict):
                continue
            article_id = str(row.get("articleId") or "").strip()
            title = re.sub(r"\s+", " ", str(row.get("title") or "")).strip()
            if not article_id or not title:
                continue
            detail_url = f"https://school.xiaohongshu.com/rule/detail/{article_id}"
            if not self._matches_patterns(title, detail_url, watcher):
                continue
            url_key = normalize_monitor_url(detail_url)
            if url_key in seen_urls:
                continue
            seen_urls[url_key] = now.isoformat()
            if is_baseline:
                continue
            published_at = self._parse_public_date(
                str(row.get("createTime") or ""), watcher
            ) or now
            dates = [
                f"createTime={row.get('createTime')}" if row.get("createTime") else "",
                (
                    f"publishStartTime={row.get('publishStartTime')}"
                    if row.get("publishStartTime")
                    else ""
                ),
                (
                    f"publishEndTime={row.get('publishEndTime')}"
                    if row.get("publishEndTime")
                    else ""
                ),
            ]
            content = "\n".join([title, *(value for value in dates if value)])
            produced.append(
                self._make_item(
                    watcher,
                    subtype="xiaohongshu_rules",
                    native_id=article_id,
                    title=title,
                    url=detail_url,
                    content=content,
                    published_at=published_at,
                    metadata={
                        "discovery_mode": "xiaohongshu_rules",
                        "article_id": article_id,
                        "published_at_basis": "createTime"
                        if row.get("createTime")
                        else "first_seen",
                        "publish_start_time": row.get("publishStartTime"),
                        "publish_end_time": row.get("publishEndTime"),
                    },
                )
            )

        current.update(
            {
                "seen_urls": seen_urls,
                "last_seen": now.isoformat(),
                "snapshot_kind": "xiaohongshu_rules",
                "visible_count": len(rows),
            }
        )
        return produced, current

    async def _fetch_xiaohongshu_help_api(
        self,
        watcher: PlatformChangeWatcherConfig,
        watcher_state: dict | None,
    ) -> tuple[list[ContentItem], dict]:
        response = await self.client.get(
            str(watcher.url),
            params={"role": watcher.api_role},
            headers={**_PUBLIC_PAGE_HEADERS, "Referer": "https://pgy.xiaohongshu.com/faq"},
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("code") not in (None, 0) or payload.get("success") is False:
            raise ValueError("xiaohongshu pgy help menu returned an invalid response")
        data = payload.get("data")
        menu = data.get("menuList") if isinstance(data, dict) else None
        if not isinstance(menu, list):
            raise ValueError("xiaohongshu pgy help menu did not return menuList")

        leaves: list[dict] = []
        def walk(nodes: list[object], directory: list[str] | None = None) -> None:
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                current_directory = list(directory or [])
                value = node.get("directory")
                if isinstance(value, list):
                    current_directory = [str(x) for x in value if str(x).strip()]
                children = node.get("menu")
                if isinstance(children, list) and children:
                    walk(children, current_directory)
                elif node.get("shortcutId"):
                    row = dict(node)
                    row["directory"] = current_directory
                    leaves.append(row)
        walk(menu)
        rows = [row for row in leaves if "规则" in " / ".join(row.get("directory") or []) or "公告" in str(row.get("title") or "")]
        now = self._now()
        previous = dict(watcher_state or {})
        seen = previous.get("seen_items") if isinstance(previous.get("seen_items"), dict) else {}
        seen_items = {str(k): dict(v) for k, v in seen.items() if isinstance(v, dict)}
        baseline = watcher_state is None
        produced: list[ContentItem] = []
        for row in rows[: watcher.fetch_limit]:
            native_id = str(row.get("shortcutId") or "").strip()
            title = re.sub(r"\s+", " ", str(row.get("title") or "")).strip()
            if not native_id or not title:
                continue
            update_time = row.get("updateTime")
            fingerprint = self._hash(json.dumps({"title": title, "updateTime": update_time}, ensure_ascii=False, sort_keys=True))
            old = seen_items.get(native_id)
            changed_fields = [] if not old else [key for key, value in (("title", title), ("update_time", update_time)) if old.get(key) != value]
            detail_url = f"https://pgy.xiaohongshu.com/help/detail?shortcutId={native_id}&userType={watcher.api_role}"
            if baseline:
                seen_items[native_id] = {"title": title, "update_time": update_time, "fingerprint": fingerprint, "url": detail_url}
                continue
            if old is not None and not changed_fields:
                continue
            detail = await self.client.get(
                "https://pgy.xiaohongshu.com/api/pgy/help/doc",
                params={"shortcutId": native_id, "role": watcher.api_role},
                headers={**_PUBLIC_PAGE_HEADERS, "Referer": "https://pgy.xiaohongshu.com/faq"},
                follow_redirects=True,
            )
            detail.raise_for_status()
            detail_payload = detail.json()
            detail_data = detail_payload.get("data") if isinstance(detail_payload, dict) else None
            detail_content = detail_data.get("content") if isinstance(detail_data, dict) else ""
            summary = self._extract_help_content(str(detail_content or ""))
            if not summary:
                summary = title
            content_hash = self._hash(summary)
            if old is not None and changed_fields == ["update_time"] and old.get("content_hash") in (None, content_hash):
                seen_items[native_id] = {**old, "title": title, "update_time": update_time, "fingerprint": fingerprint, "content_hash": content_hash, "url": detail_url}
                continue
            seen_items[native_id] = {"title": title, "update_time": update_time, "fingerprint": fingerprint, "content_hash": content_hash, "url": detail_url}
            published_at = self._epoch_millis_to_datetime(update_time) or now
            produced.append(self._make_item(
                watcher,
                subtype="xiaohongshu_help_api",
                native_id=native_id,
                title=title,
                url=detail_url,
                content=summary,
                published_at=published_at,
                metadata={
                    "discovery_mode": "xiaohongshu_help_api",
                    "shortcut_id": native_id,
                    "update_time": update_time,
                    "changed_fields": changed_fields,
                    "content_hash": content_hash,
                    "published_at_basis": "updateTime" if update_time else "first_seen",
                    "original_url": detail_url,
                },
            ))
        latest = max((row.get("updateTime") for row in rows if isinstance(row.get("updateTime"), (int, float))), default=None)
        current = dict(previous)
        current.update({"seen_items": seen_items, "visible_count": len(rows), "last_seen": now.isoformat(), "last_changed": self._epoch_millis_to_datetime(latest).isoformat() if latest else None, "snapshot_kind": "xiaohongshu_help_api"})
        return produced, current

    @staticmethod
    def _epoch_millis_to_datetime(value: object) -> datetime | None:
        if not isinstance(value, (int, float)):
            return None
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    @staticmethod
    def _extract_help_content(value: str) -> str:
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return normalize_page_text(value)
        texts: list[str] = []
        def walk(node: object) -> None:
            if isinstance(node, dict):
                text = node.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
                for child in node.get("children", []) if isinstance(node.get("children"), list) else []:
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)
        walk(parsed)
        return "\n".join(texts)

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

    async def _fetch_bilibili_bundle_diff(
        self,
        watcher: PlatformChangeWatcherConfig,
        watcher_state: dict | None,
    ) -> tuple[list[ContentItem], dict]:
        page_response = await self.client.get(
            str(watcher.url),
            headers=_PUBLIC_PAGE_HEADERS,
            follow_redirects=True,
        )
        page_response.raise_for_status()
        soup = BeautifulSoup(page_response.text or "", "html.parser")
        script_url = next(
            (
                urljoin(str(watcher.url), str(script.get("src")))
                for script in soup.find_all("script", src=True)
                if re.search(
                    r"/convention/static/js/index\.[^/]+\.js(?:\?|$)",
                    str(script.get("src")),
                )
            ),
            None,
        )
        if not script_url:
            raise ValueError("bilibili convention bundle URL was not found")
        bundle_response = await self.client.get(
            script_url,
            headers=_PUBLIC_PAGE_HEADERS,
            follow_redirects=True,
        )
        bundle_response.raise_for_status()
        normalized = self._extract_bilibili_convention(bundle_response.text)
        if len(normalized) < watcher.min_content_chars:
            raise ValueError(
                f"bilibili convention text is too short ({len(normalized)} chars)"
            )

        return self._diff_snapshot(
            watcher,
            watcher_state,
            normalized=normalized,
            snapshot_kind="bilibili_bundle",
            discovery_mode="bilibili_bundle_diff",
            source_url=script_url,
        )

    def _diff_snapshot(
        self,
        watcher: PlatformChangeWatcherConfig,
        watcher_state: dict | None,
        *,
        normalized: str,
        snapshot_kind: str,
        discovery_mode: str,
        source_url: str,
    ) -> tuple[list[ContentItem], dict]:
        now = self._now()
        current_hash = self._hash(normalized)
        if watcher_state is None or (
            watcher_state.get("snapshot_kind")
            and watcher_state.get("snapshot_kind") != snapshot_kind
        ):
            return [], {
                "normalized_text_hash": current_hash,
                "normalized_text": normalized,
                "last_seen": now.isoformat(),
                "last_changed": None,
                "snapshot_kind": snapshot_kind,
                "source_url": source_url,
            }

        previous_hash = str(watcher_state.get("normalized_text_hash") or "")
        previous_text = str(watcher_state.get("normalized_text") or "")
        updated = dict(watcher_state)
        updated.update(
            {
                "last_seen": now.isoformat(),
                "snapshot_kind": snapshot_kind,
                "source_url": source_url,
            }
        )
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
            subtype=discovery_mode,
            native_id=current_hash[:16],
            title=f"{self._platform_label(watcher.platform)}公开页面发生变化：{watcher.name}",
            url=str(watcher.url),
            content=content,
            published_at=now,
            metadata={
                "discovery_mode": discovery_mode,
                "changed_at": now.isoformat(),
                "previous_hash": previous_hash,
                "current_hash": current_hash,
                "diff_excerpt": diff_excerpt,
                "source_snapshot_url": source_url,
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
        seen_urls = self._normalized_search_entries(current.get("seen_urls"))
        query_fingerprint = self._search_query_fingerprint(watcher)
        query_changed = str(current.get("query_fingerprint") or "") != query_fingerprint
        is_baseline = watcher_state is None
        query_baseline = query_changed
        produced: list[ContentItem] = []
        url_keys: set[str] = set()
        qualified_count = 0
        for discovered_item in discovered[: watcher.fetch_limit]:
            url = str(discovered_item.url)
            url_key = normalize_monitor_url(url)
            url_keys.add(url_key)
            text = f"{discovered_item.title}\n{discovered_item.content or ''}"
            actual_change_at = self._extract_actual_change_at(text, watcher, now)
            fingerprint = self._search_discovery_fingerprint(
                discovered_item, actual_change_at
            )
            previous = seen_urls.get(url_key)
            retryable_previous = isinstance(previous, dict) and str(previous.get("status") or "") in _RETRYABLE_SEARCH_STATUSES
            if query_baseline and not retryable_previous:
                seen_urls[url_key] = {
                    "first_seen": str(previous.get("first_seen") if previous else now.isoformat()),
                    "last_seen": now.isoformat(),
                    "fingerprint": fingerprint,
                    "status": "baseline",
                }
                continue
            if self._ensure_utc(discovered_item.published_at) < lookback_since:
                if previous is None:
                    seen_urls[url_key] = {"first_seen": now.isoformat(), "last_seen": now.isoformat(), "fingerprint": fingerprint, "status": "old_result"}
                continue
            if not self._matches_patterns(text, url, watcher) or not _CHANGE_SIGNAL.search(text):
                if previous is None:
                    seen_urls[url_key] = {"first_seen": now.isoformat(), "last_seen": now.isoformat(), "fingerprint": fingerprint, "status": "irrelevant"}
                continue
            if previous is not None:
                previous_fingerprint = str(previous.get("fingerprint") or "")
                previous_status = str(previous.get("status") or "")
                if not previous_fingerprint:
                    previous.update(
                        {
                            "last_seen": now.isoformat(),
                            "fingerprint": fingerprint,
                            "status": "baseline",
                        }
                    )
                    continue
                if (
                    previous_fingerprint == fingerprint
                    and previous_status not in _RETRYABLE_SEARCH_STATUSES
                ):
                    previous["last_seen"] = now.isoformat()
                    continue

            first_seen = (
                str(previous.get("first_seen") or now.isoformat())
                if previous is not None
                else now.isoformat()
            )
            entry = {
                "first_seen": first_seen,
                "last_seen": now.isoformat(),
                "fingerprint": fingerprint,
                "status": "baseline" if is_baseline else "candidate_emitted",
            }
            seen_urls[url_key] = entry
            if is_baseline or watcher.source_level == "unverified":
                continue

            if actual_change_at is not None and actual_change_at < lookback_since:
                entry["status"] = "old_change"
                continue

            source_level, attribution = self._resolve_discovery_level(
                watcher, discovered_item
            )
            if actual_change_at is None:
                entry["status"] = "unconfirmed"
            qualified_count += 1
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

        current.update({
            "seen_urls": seen_urls,
            "last_seen": now.isoformat(),
            "query_fingerprint": query_fingerprint,
            "url_dedup_count": len(url_keys),
            "qualified_candidate_count": qualified_count,
        })
        current["visible_count"] = len(discovered)
        return produced, current

    @staticmethod
    def _search_query_fingerprint(watcher: PlatformChangeWatcherConfig) -> str:
        return PlatformChangesScraper._hash(json.dumps({
            "query": watcher.query or "",
            "language": watcher.language,
            "country": watcher.country,
            "ceid": watcher.ceid,
        }, ensure_ascii=False, sort_keys=True))

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
    def _looks_like_shell(html_text: str) -> bool:
        normalized = normalize_page_text(html_text)
        return len(normalized) < 120 and bool(
            re.search(r"<div[^>]+id=[\"'](?:app|root)[\"']", html_text or "", re.I)
        )

    @staticmethod
    def _extract_bilibili_convention(bundle_text: str) -> str:
        for match in re.finditer(
            r"JSON\.parse\('(?P<payload>\[\{.*?contentXML.*?\}\])'\)",
            bundle_text or "",
            flags=re.DOTALL,
        ):
            try:
                decoded = ast.literal_eval("'" + match.group("payload") + "'")
                sections = json.loads(decoded)
            except (SyntaxError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(sections, list):
                continue
            parts: list[str] = []
            has_content = False
            for section in sections:
                if not isinstance(section, dict):
                    continue
                title = str(section.get("title") or "").strip()
                if title:
                    parts.append(title)
                children = section.get("children")
                if not isinstance(children, list):
                    continue
                for child in children:
                    if not isinstance(child, dict):
                        continue
                    child_title = str(child.get("title") or "").strip()
                    if child_title:
                        parts.append(child_title)
                    content_xml = str(child.get("contentXML") or "")
                    content = normalize_page_text(content_xml)
                    if content:
                        has_content = True
                        parts.append(content)
            if has_content:
                return "\n".join(parts)
        raise ValueError("bilibili convention data was not found in the public bundle")

    @staticmethod
    def _normalized_seen_urls(value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, str] = {}
        for raw_url, first_seen in value.items():
            normalized.setdefault(normalize_monitor_url(str(raw_url)), str(first_seen))
        return normalized

    @staticmethod
    def _normalized_search_entries(value: object) -> dict[str, dict[str, str]]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, dict[str, str]] = {}
        for raw_url, raw_entry in value.items():
            url_key = normalize_monitor_url(str(raw_url))
            if isinstance(raw_entry, dict):
                entry = {
                    str(key): str(item)
                    for key, item in raw_entry.items()
                    if item is not None
                }
                first_seen = entry.get("first_seen") or entry.get("last_seen") or ""
                entry.setdefault("first_seen", first_seen)
                entry.setdefault("last_seen", first_seen)
                entry.setdefault("status", "candidate_emitted")
            else:
                timestamp = str(raw_entry)
                entry = {
                    "first_seen": timestamp,
                    "last_seen": timestamp,
                    "fingerprint": "",
                    "status": "legacy",
                }
            normalized.setdefault(url_key, entry)
        return normalized

    @classmethod
    def _search_discovery_fingerprint(
        cls,
        item: ContentItem,
        actual_change_at: datetime | None,
    ) -> str:
        payload = {
            "title": re.sub(r"\s+", " ", item.title).strip(),
            "content": normalize_page_text(item.content or ""),
            "article_published_at": cls._ensure_utc(item.published_at).isoformat(),
            "actual_change_at": actual_change_at.isoformat()
            if actual_change_at is not None
            else None,
        }
        return cls._hash(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )

    @staticmethod
    def _parse_public_date(
        value: str, watcher: PlatformChangeWatcherConfig
    ) -> datetime | None:
        match = re.fullmatch(
            r"(?P<year>20\d{2})[年/.-](?P<month>\d{1,2})[月/.-](?P<day>\d{1,2})日?",
            value.strip(),
        )
        if not match:
            return None
        try:
            local_value = datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
                tzinfo=ZoneInfo(watcher.observed_timezone),
            )
        except ValueError:
            return None
        return local_value.astimezone(timezone.utc)

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
            metadata={
                **base_metadata,
                "candidate_trace": {
                    "candidate_id": f"platform_changes:{subtype}:{native_id}",
                    "watcher": watcher.name,
                    "discovery_mode": subtype,
                    "fetch": {"status": "kept", "reason": "watcher_emitted"},
                    "merge": {"status": "pending", "merged_into_id": None},
                    "analyze": {"status": "pending", "reason": None},
                    "threshold": {"status": "pending", "reason": None},
                    "dedup": {"status": "pending", "reason": None},
                    "balance": {"status": "pending", "reason": None},
                    "final": {"status": "pending", "reason": None},
                    "outcome": "pending",
                    "reason": None,
                    "merged_into_id": None,
                },
            },
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

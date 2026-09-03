"""GitHub scraper implementation."""

import logging
import os
import calendar
import hashlib
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import feedparser
import httpx

from .base import BaseScraper
from ..models import ContentItem, SourceType, GitHubSourceConfig
from ..processing.source_health import (
    SourceHealthObservation,
    SourceHealthResult,
    SourceHealthStatus,
    assess_source_health,
)

logger = logging.getLogger(__name__)


class GitHubScraper(BaseScraper):
    """Scraper for GitHub events and releases."""

    def __init__(self, sources: List[GitHubSourceConfig], http_client: httpx.AsyncClient):
        """Initialize GitHub scraper.

        Args:
            sources: List of GitHub source configurations
            http_client: Shared async HTTP client
        """
        super().__init__({"sources": sources}, http_client)
        self.token = os.getenv("GITHUB_TOKEN")
        self.base_url = "https://api.github.com"
        self.last_source_results: list[dict] = []
        self._fallback_sources: set[str] = set()

    def _get_headers(self) -> dict:
        """Get request headers with optional authentication.

        Returns:
            dict: HTTP headers
        """
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Horizon-Aggregator"
        }
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        return headers

    async def fetch(self, since: datetime) -> List[ContentItem]:
        """Fetch GitHub content items.

        Args:
            since: Only fetch items published after this time

        Returns:
            List[ContentItem]: Fetched content items
        """
        items = []
        self.last_source_results = []
        self._fallback_sources = set()
        sources = self.config["sources"]

        for source in sources:
            if not source.enabled:
                continue

            source_items: List[ContentItem] = []
            error: Exception | None = None
            try:
                if source.type == "user_events" and source.username:
                    source_items = await self._fetch_user_events(source, since)
                elif source.type == "repo_releases" and source.owner and source.repo:
                    source_items = await self._fetch_repo_releases(source, since)
                elif source.type == "repo_search" and source.query:
                    source_items = await self._search_repositories(source, since)
                else:
                    raise ValueError("unsupported or incomplete GitHub source config")
            except Exception as exc:
                error = exc
                logger.warning(
                    "Error fetching GitHub source %s: %s",
                    self._source_id(source),
                    exc,
                )

            items.extend(source_items)
            schema_error = isinstance(error, ValueError)
            if self._source_id(source) in self._fallback_sources and error is None:
                health = SourceHealthResult(
                    source_id=self._source_id(source),
                    status=SourceHealthStatus.DEGRADED,
                    reason_code="fallback_used",
                    detail="GitHub API was limited; official release Atom feed succeeded",
                )
            else:
                health = assess_source_health(
                    SourceHealthObservation(
                        source_id=self._source_id(source),
                        checked_at=datetime.now(timezone.utc),
                        transport_ok=error is None or schema_error,
                        schema_ok=error is None,
                        error=(
                            f"{type(error).__name__}: {error}" if error else None
                        ),
                        item_count=len(source_items),
                        newest_item_at=max(
                            (item.published_at for item in source_items),
                            default=None,
                        ),
                    )
                )
            self.last_source_results.append(
                {
                    **health.model_dump(mode="json"),
                    "source_type": source.type,
                }
            )

        return items

    @staticmethod
    def _source_id(source: GitHubSourceConfig) -> str:
        if source.type == "repo_releases":
            identity = f"{source.owner}/{source.repo}"
        elif source.type == "user_events":
            identity = str(source.username)
        else:
            identity = str(source.query)
        return f"github:{source.type}:{identity}"

    async def _fetch_user_events(
        self,
        source: GitHubSourceConfig,
        since: datetime,
    ) -> List[ContentItem]:
        """Fetch public events for a user.

        Args:
            source: GitHub source configuration
            since: Only fetch events after this time

        Returns:
            List[ContentItem]: Event content items
        """
        url = f"{self.base_url}/users/{source.username}/events/public"
        items = []

        response = await self.client.get(url, headers=self._get_headers(), follow_redirects=True)
        response.raise_for_status()
        events = response.json()
        if not isinstance(events, list):
            raise ValueError("GitHub events response must be a list")

        for event in events:
                created_at = datetime.fromisoformat(
                    event["created_at"].replace("Z", "+00:00")
                )

                if created_at < since:
                    continue

                # Filter interesting event types
                event_type = event["type"]
                if event_type not in [
                    "PushEvent", "CreateEvent", "ReleaseEvent",
                    "PublicEvent", "WatchEvent"
                ]:
                    continue

                item = self._parse_event(event, source)
                if item:
                    items.append(item)

        return items

    def _parse_event(self, event: dict, source: GitHubSourceConfig) -> Optional[ContentItem]:
        """Parse GitHub event into ContentItem.

        Args:
            event: GitHub event data
            username: GitHub username

        Returns:
            Optional[ContentItem]: Parsed content item or None
        """
        event_type = event["type"]
        event_id = event["id"]
        created_at = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
        username = source.username

        repo_name = event["repo"]["name"]
        repo_url = f"https://github.com/{repo_name}"

        # Generate title and content based on event type
        if event_type == "PushEvent":
            commits = event["payload"].get("commits", [])
            title = f"{username} pushed {len(commits)} commit(s) to {repo_name}"
            content = "\n".join([c.get("message", "") for c in commits[:3]])
        elif event_type == "CreateEvent":
            ref_type = event["payload"].get("ref_type", "repository")
            title = f"{username} created {ref_type} in {repo_name}"
            content = event["payload"].get("description", "")
        elif event_type == "ReleaseEvent":
            release = event["payload"].get("release", {})
            title = f"{username} released {release.get('tag_name', '')} in {repo_name}"
            content = release.get("body", "")
            repo_url = release.get("html_url", repo_url)
        elif event_type == "PublicEvent":
            title = f"{username} made {repo_name} public"
            content = ""
        elif event_type == "WatchEvent":
            title = f"{username} starred {repo_name}"
            content = ""
        else:
            return None

        return ContentItem(
            id=self._generate_id("github", "event", event_id),
            source_type=SourceType.GITHUB,
            title=title,
            url=repo_url,
            content=content,
            author=username,
            published_at=created_at,
            profile=source.profile,
            metadata={
                "event_type": event_type,
                "repo": repo_name,
                "category": source.category,
                "source_shadow": source.shadow,
            }
        )

    async def _fetch_repo_releases(
        self,
        source: GitHubSourceConfig,
        since: datetime,
    ) -> List[ContentItem]:
        """Fetch releases for a repository.

        Args:
            source: GitHub source configuration
            since: Only fetch releases after this time

        Returns:
            List[ContentItem]: Release content items
        """
        owner, repo = source.owner, source.repo
        url = f"{self.base_url}/repos/{owner}/{repo}/releases"
        items = []

        response = await self.client.get(url, headers=self._get_headers(), follow_redirects=True)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {403, 429}:
                raise
            self._fallback_sources.add(self._source_id(source))
            return await self._fetch_repo_releases_atom(source, since)
        releases = response.json()
        if not isinstance(releases, list):
            raise ValueError("GitHub releases response must be a list")

        for release in releases:
                published_at = datetime.fromisoformat(
                    release["published_at"].replace("Z", "+00:00")
                )

                if published_at < since:
                    continue

                item = ContentItem(
                    id=self._generate_id("github", "release", str(release["id"])),
                    source_type=SourceType.GITHUB,
                    title=f"{owner}/{repo} released {release['tag_name']}",
                    url=release["html_url"],
                    content=release.get("body", ""),
                    author=release["author"]["login"],
                    published_at=published_at,
                    profile=source.profile,
                    metadata={
                        "repo": f"{owner}/{repo}",
                        "tag": release["tag_name"],
                        "prerelease": release.get("prerelease", False),
                        "category": source.category,
                        "source_shadow": source.shadow,
                    }
                )
                items.append(item)

        return items

    async def _fetch_repo_releases_atom(
        self,
        source: GitHubSourceConfig,
        since: datetime,
    ) -> List[ContentItem]:
        owner, repo = source.owner, source.repo
        response = await self.client.get(
            f"https://github.com/{owner}/{repo}/releases.atom",
            headers={"User-Agent": "Horizon-Aggregator"},
            follow_redirects=True,
        )
        response.raise_for_status()
        feed = feedparser.parse(response.text)
        if not feed.entries and not feed.get("version"):
            raise ValueError("GitHub releases Atom response was not a feed")

        since_utc = (
            since.replace(tzinfo=timezone.utc)
            if since.tzinfo is None
            else since.astimezone(timezone.utc)
        )
        items: List[ContentItem] = []
        for entry in feed.entries:
            updated = entry.get("updated_parsed") or entry.get("published_parsed")
            if not updated:
                continue
            published_at = datetime.fromtimestamp(
                calendar.timegm(updated), tz=timezone.utc
            )
            if published_at < since_utc:
                continue
            entry_id = str(entry.get("id") or entry.get("link") or entry.get("title"))
            digest = hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:16]
            content_rows = entry.get("content") or []
            content = (
                str(content_rows[0].get("value") or "")
                if content_rows and isinstance(content_rows[0], dict)
                else str(entry.get("summary") or "")
            )
            author = str(entry.get("author") or owner)
            items.append(
                ContentItem(
                    id=self._generate_id("github", "release-atom", digest),
                    source_type=SourceType.GITHUB,
                    title=f"{owner}/{repo} released {entry.get('title') or 'a new version'}",
                    url=str(entry.get("link") or f"https://github.com/{owner}/{repo}/releases"),
                    content=content,
                    author=author,
                    published_at=published_at,
                    profile=source.profile,
                    metadata={
                        "repo": f"{owner}/{repo}",
                        "tag": entry.get("title"),
                        "category": source.category,
                        "source_fallback": "release_atom",
                        "source_shadow": source.shadow,
                    },
                )
            )
        return items

    async def _search_repositories(
        self,
        source: GitHubSourceConfig,
        since: datetime,
    ) -> List[ContentItem]:
        since_utc = (
            since.replace(tzinfo=timezone.utc)
            if since.tzinfo is None
            else since.astimezone(timezone.utc)
        )
        discovery_start = since_utc - timedelta(days=source.discovery_window_days)
        query = (
            f"{source.query.strip()} created:>={discovery_start.date().isoformat()} "
            f"pushed:>={since_utc.date().isoformat()} "
            f"stars:>={source.min_stars}"
        )
        response = await self.client.get(
            f"{self.base_url}/search/repositories",
            headers=self._get_headers(),
            params={
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": source.fetch_limit,
            },
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()

        rows = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("GitHub repository search response must contain items")

        items: List[ContentItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                created_at = datetime.fromisoformat(
                    str(row["created_at"]).replace("Z", "+00:00")
                )
                activity_at = datetime.fromisoformat(
                    str(row.get("pushed_at") or row["updated_at"]).replace(
                        "Z", "+00:00"
                    )
                )
                repo_id = str(row["id"])
                full_name = str(row["full_name"])
                html_url = str(row["html_url"])
            except (KeyError, TypeError, ValueError):
                continue
            if created_at < discovery_start or activity_at < since_utc:
                continue
            stars = int(row.get("stargazers_count") or 0)
            if stars < source.min_stars:
                continue
            description = str(row.get("description") or "").strip()
            topics = [str(topic) for topic in (row.get("topics") or [])]
            content = description
            if content:
                content += "\n\n"
            content += (
                f"Stars: {stars}; forks: {int(row.get('forks_count') or 0)}; "
                f"language: {row.get('language') or 'unknown'}."
            )
            items.append(
                ContentItem(
                    id=self._generate_id("github", "repository", repo_id),
                    source_type=SourceType.GITHUB,
                    title=f"{full_name} ({stars} stars)",
                    url=html_url,
                    content=content,
                    author=str((row.get("owner") or {}).get("login") or "GitHub"),
                    published_at=activity_at,
                    profile=source.profile,
                    metadata={
                        "repo": full_name,
                        "category": source.category,
                        "stars": stars,
                        "forks": int(row.get("forks_count") or 0),
                        "open_issues": int(row.get("open_issues_count") or 0),
                        "language": row.get("language"),
                        "topics": topics,
                        "updated_at": row.get("updated_at"),
                        "pushed_at": row.get("pushed_at"),
                        "repository_created_at": created_at.isoformat(),
                        "source_kind": "official_repository_search",
                        "source_shadow": source.shadow,
                    },
                )
            )
        return items

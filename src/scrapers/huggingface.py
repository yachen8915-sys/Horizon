"""Official Hugging Face trending models and Daily Papers source."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from .base import BaseScraper
from ..models import ContentItem, HuggingFaceConfig, SourceType

logger = logging.getLogger(__name__)


class HuggingFaceScraper(BaseScraper):
    MODELS_URL = "https://huggingface.co/api/models"
    PAPERS_URL = "https://huggingface.co/api/daily_papers"

    def __init__(self, config: HuggingFaceConfig, http_client: httpx.AsyncClient):
        super().__init__({"huggingface": config}, http_client)
        self.hf_config = config

    async def fetch(self, since: datetime) -> list[ContentItem]:
        if not self.hf_config.enabled:
            return []
        items: list[ContentItem] = []
        if self.hf_config.fetch_models:
            try:
                items.extend(await self._fetch_models(since))
            except Exception as exc:
                logger.warning("Hugging Face models unavailable, skipping: %s", exc)
        if self.hf_config.fetch_papers:
            try:
                items.extend(await self._fetch_papers(since))
            except Exception as exc:
                logger.warning("Hugging Face Daily Papers unavailable, skipping: %s", exc)
        return items

    async def _fetch_models(self, since: datetime) -> list[ContentItem]:
        response = await self.client.get(
            self.MODELS_URL,
            params={
                "sort": "trendingScore",
                "direction": "-1",
                "limit": self.hf_config.model_limit,
                "full": "true",
            },
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            return []
        items = []
        for row in payload[: self.hf_config.model_limit]:
            if not isinstance(row, dict):
                continue
            modified = self._parse_datetime(row.get("lastModified"))
            trending_score = self._integer(row.get("trendingScore"))
            model_id = str(row.get("id") or row.get("modelId") or "").strip()
            if not model_id or modified is None or modified < self._as_utc(since):
                continue
            if trending_score < self.hf_config.model_min_trending_score:
                continue
            downloads = self._integer(row.get("downloads"))
            likes = self._integer(row.get("likes"))
            pipeline = str(row.get("pipeline_tag") or "unknown")
            tags = [str(tag) for tag in row.get("tags", []) if tag][:20]
            items.append(
                ContentItem(
                    id=self._generate_id("huggingface", "model", model_id),
                    source_type=SourceType.HUGGINGFACE,
                    title=f"Hugging Face Trending Model: {model_id}",
                    url=f"https://huggingface.co/{model_id}",
                    content=(
                        f"Model: {model_id}\nTask: {pipeline}\n"
                        f"Trending score: {trending_score}\nDownloads: {downloads}\n"
                        f"Likes: {likes}\nTags: {', '.join(tags)}"
                    ),
                    author=model_id.split("/", 1)[0],
                    published_at=modified,
                    profile=self.hf_config.profile,
                    metadata={
                        "category": "ai-tech-model",
                        "model_id": model_id,
                        "pipeline_tag": pipeline,
                        "trending_score": trending_score,
                        "downloads": downloads,
                        "likes": likes,
                        "tags": tags,
                        "engagement": {"downloads": downloads, "likes": likes},
                        "provider": "huggingface",
                        "source_name": "Hugging Face Trending Models",
                        "source_kind": "official",
                        "reliability": "official_stable",
                        "original_url": f"https://huggingface.co/{model_id}",
                    },
                )
            )
        return items

    async def _fetch_papers(self, since: datetime) -> list[ContentItem]:
        response = await self.client.get(
            self.PAPERS_URL,
            params={"limit": self.hf_config.paper_limit},
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            return []
        items = []
        for row in payload[: self.hf_config.paper_limit]:
            if not isinstance(row, dict):
                continue
            paper = row.get("paper") if isinstance(row.get("paper"), dict) else row
            paper_id = str(paper.get("id") or "").strip()
            title = str(paper.get("title") or row.get("title") or "").strip()
            submitted = self._parse_datetime(
                paper.get("submittedOnDailyAt")
                or row.get("submittedOnDailyAt")
                or paper.get("publishedAt")
                or row.get("publishedAt")
            )
            upvotes = self._integer(paper.get("upvotes") or row.get("upvotes"))
            if not paper_id or not title or submitted is None:
                continue
            if submitted < self._as_utc(since) or upvotes < self.hf_config.paper_min_upvotes:
                continue
            summary = str(paper.get("summary") or row.get("summary") or "").strip()
            project_url = paper.get("projectPage") or row.get("projectPage")
            github_url = paper.get("githubRepo") or row.get("githubRepo")
            comments = self._integer(row.get("numComments"))
            content_lines = [summary, f"Hugging Face upvotes: {upvotes}"]
            if project_url:
                content_lines.append(f"Project: {project_url}")
            if github_url:
                content_lines.append(f"GitHub: {github_url}")
            items.append(
                ContentItem(
                    id=self._generate_id("huggingface", "paper", paper_id),
                    source_type=SourceType.HUGGINGFACE,
                    title=title,
                    url=f"https://huggingface.co/papers/{paper_id}",
                    content="\n".join(line for line in content_lines if line),
                    author="Hugging Face Papers",
                    published_at=submitted,
                    profile=self.hf_config.profile,
                    metadata={
                        "category": "ai-tech-paper",
                        "paper_id": paper_id,
                        "upvotes": upvotes,
                        "comments": comments,
                        "project_url": project_url,
                        "github_url": github_url,
                        "engagement": {"upvotes": upvotes, "comments": comments},
                        "provider": "huggingface",
                        "source_name": "Hugging Face Daily Papers",
                        "source_kind": "official",
                        "reliability": "official_experimental",
                        "original_url": f"https://huggingface.co/papers/{paper_id}",
                    },
                )
            )
        return items

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return HuggingFaceScraper._as_utc(
                datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            )
        except ValueError:
            return None

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _integer(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

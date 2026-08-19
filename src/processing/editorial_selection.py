"""Editorial diversity and cross-day freshness for the two AI digest profiles."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .._file_utils import _atomic_write_text
from ..models import ContentItem, EditorialSelectionConfig


TOPIC_PROFILE = "pangmen-topic-radar"
TECH_PROFILE = "pangmen-ai-tech-radar"
TARGET_PROFILES = {TOPIC_PROFILE, TECH_PROFILE}
EDITORIAL_COOLDOWN_BYPASS = {"major_release", "material_update"}


@dataclass(frozen=True)
class EditorialExclusion:
    reason: str
    replaced_by_id: str | None = None
    limit_key: str | None = None
    limit_value: int | None = None


@dataclass
class EditorialSelectionResult:
    items: list[ContentItem]
    exclusions: dict[str, EditorialExclusion] = field(default_factory=dict)


def normalize_editorial_token(value: Any) -> str:
    """Normalize AI-generated identifiers without discarding Chinese names."""
    normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "_", str(value or "").casefold())
    return normalized.strip("_")


def canonical_editorial_key(item: ContentItem) -> str:
    analysis = item.processing.analysis if item.processing else None
    if analysis is None:
        return ""
    parts = (
        normalize_editorial_token(analysis.primary_entity),
        normalize_editorial_token(analysis.use_case),
        normalize_editorial_token(analysis.content_format),
    )
    return "|".join(parts) if all(parts) else ""


def canonical_url(value: str) -> str:
    parsed = urlsplit(value)
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold()
        not in {"fbclid", "gclid", "mc_cid", "mc_eid", "ttclid"}
    ]
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            parsed.path.rstrip("/") or "/",
            urlencode(query),
            "",
        )
    )


def profile_id(item: ContentItem) -> str:
    if item.processing:
        return item.processing.classification.profile
    return item.profile if isinstance(item.profile, str) else ""


def sub_source_key(item: ContentItem) -> str:
    metadata = item.metadata
    for key in ("subreddit", "feed_name", "channel", "repo", "watchlist", "source_name"):
        value = metadata.get(key)
        if value:
            return normalize_editorial_token(value)
    if metadata.get("gn_query"):
        return normalize_editorial_token(f"google_news:{metadata['gn_query']}")
    if metadata.get("domain"):
        return normalize_editorial_token(metadata["domain"])
    return normalize_editorial_token(item.author or "unknown")


class EditorialSelector:
    """Select evidence-tagged AI items with diversity and freshness constraints."""

    def __init__(
        self,
        config: EditorialSelectionConfig,
    ) -> None:
        self.config = config
        self.state_path = Path(config.state_file)

    def select(
        self,
        items: list[ContentItem],
        *,
        now: datetime | None = None,
    ) -> EditorialSelectionResult:
        if not self.config.enabled:
            return EditorialSelectionResult(items=items)
        if not any(profile_id(item) in TARGET_PROFILES for item in items):
            return EditorialSelectionResult(items=items)

        observed_at = self._as_utc(now or datetime.now(timezone.utc))
        state_items = self._pruned_items(self._load_state()["items"], observed_at)
        exclusions: dict[str, EditorialExclusion] = {}
        candidates: list[ContentItem] = []
        passthrough: list[ContentItem] = []

        for item in items:
            if profile_id(item) not in TARGET_PROFILES:
                passthrough.append(item)
                continue
            exclusion = self._cross_day_exclusion(item, state_items, observed_at)
            if exclusion is not None:
                exclusions[item.id] = exclusion
                continue
            candidates.append(item)

        selected: list[ContentItem] = []
        entity_counts: dict[str, int] = defaultdict(int)
        topic_counts: dict[str, int] = defaultdict(int)
        use_case_counts: dict[str, int] = defaultdict(int)
        format_counts: dict[str, int] = defaultdict(int)
        source_counts: dict[str, int] = defaultdict(int)
        selected_by_dimension: dict[tuple[str, str], str] = {}

        for item in sorted(candidates, key=self._sort_key):
            item_profile = profile_id(item)
            analysis = item.processing.analysis if item.processing else None
            if analysis is None:
                continue
            entity = normalize_editorial_token(analysis.primary_entity)
            topic = normalize_editorial_token(analysis.topic_cluster)
            use_case = normalize_editorial_token(analysis.use_case)
            content_format = normalize_editorial_token(analysis.content_format)
            source = sub_source_key(item)

            if item_profile == TOPIC_PROFILE:
                limits = (
                    ("primary_entity", entity, self.config.primary_entity_limit, "diversity_entity_limit"),
                    ("topic_cluster", topic, self.config.topic_cluster_limit, "diversity_topic_limit"),
                    ("use_case", use_case, self.config.use_case_limit, "diversity_use_case_limit"),
                    (
                        "content_format",
                        content_format,
                        self.config.tutorial_workflow_limit if content_format == "tutorial_workflow" else None,
                        "diversity_format_limit",
                    ),
                    ("sub_source", source, self.config.sub_source_limit, "diversity_source_limit"),
                )
                rejected = False
                counters = {
                    "primary_entity": entity_counts,
                    "topic_cluster": topic_counts,
                    "use_case": use_case_counts,
                    "content_format": format_counts,
                    "sub_source": source_counts,
                }
                for dimension, value, limit, reason in limits:
                    if not value or limit is None:
                        continue
                    if counters[dimension][value] >= limit:
                        exclusions[item.id] = EditorialExclusion(
                            reason=reason,
                            replaced_by_id=selected_by_dimension.get((dimension, value)),
                            limit_key=f"{dimension}:{value}",
                            limit_value=limit,
                        )
                        rejected = True
                        break
                if rejected:
                    continue

            selected.append(item)
            if item_profile == TOPIC_PROFILE:
                for dimension, value, counter in (
                    ("primary_entity", entity, entity_counts),
                    ("topic_cluster", topic, topic_counts),
                    ("use_case", use_case, use_case_counts),
                    ("content_format", content_format, format_counts),
                    ("sub_source", source, source_counts),
                ):
                    if value:
                        counter[value] += 1
                        selected_by_dimension[(dimension, value)] = item.id

        if not passthrough:
            result_items = selected
        else:
            selected_ids = {item.id for item in selected}
            result_items = [
                item
                for item in items
                if profile_id(item) not in TARGET_PROFILES or item.id in selected_ids
            ]
        return EditorialSelectionResult(items=result_items, exclusions=exclusions)

    def record_selected(
        self,
        items: list[ContentItem],
        *,
        now: datetime | None = None,
    ) -> None:
        if not self.config.enabled:
            return
        selected_at = self._as_utc(now or datetime.now(timezone.utc))
        state = self._load_state()
        records = self._pruned_items(state["items"], selected_at)
        for item in items:
            if profile_id(item) not in TARGET_PROFILES:
                continue
            analysis = item.processing.analysis if item.processing else None
            if analysis is None:
                continue
            records.append(
                {
                    "item_id": item.id,
                    "title": item.title,
                    "url": canonical_url(str(item.url)),
                    "event_key": normalize_editorial_token(analysis.event_key),
                    "editorial_key": canonical_editorial_key(item),
                    "primary_entity": normalize_editorial_token(analysis.primary_entity),
                    "topic_cluster": normalize_editorial_token(analysis.topic_cluster),
                    "use_case": normalize_editorial_token(analysis.use_case),
                    "content_format": normalize_editorial_token(analysis.content_format),
                    "selected_at": selected_at.isoformat(),
                }
            )
        state = {
            "version": 1,
            "items": records[-self.config.max_history_entries :],
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(
            self.state_path,
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        )

    def _cross_day_exclusion(
        self,
        item: ContentItem,
        state_items: list[dict[str, Any]],
        now: datetime,
    ) -> EditorialExclusion | None:
        analysis = item.processing.analysis if item.processing else None
        if analysis is None:
            return None
        url = canonical_url(str(item.url))
        event_key = normalize_editorial_token(analysis.event_key)
        editorial_key = canonical_editorial_key(item)
        novelty_level = normalize_editorial_token(analysis.novelty_level)
        history_cutoff = now - timedelta(days=self.config.history_days)
        editorial_cutoff = now - timedelta(days=self.config.editorial_cooldown_days)

        for record in reversed(state_items):
            selected_at = self._parse_datetime(record.get("selected_at"))
            if selected_at is None:
                continue
            replaced_by_id = str(record.get("item_id") or "") or None
            if selected_at >= history_cutoff and url and record.get("url") == url:
                return EditorialExclusion(
                    reason="exact_url_repeat",
                    replaced_by_id=replaced_by_id,
                    limit_key="url:7d",
                    limit_value=self.config.history_days,
                )
            if (
                selected_at >= history_cutoff
                and event_key
                and record.get("event_key") == event_key
            ):
                return EditorialExclusion(
                    reason="cross_day_event_repeat",
                    replaced_by_id=replaced_by_id,
                    limit_key="event_key:7d",
                    limit_value=self.config.history_days,
                )
            if (
                selected_at >= editorial_cutoff
                and editorial_key
                and record.get("editorial_key") == editorial_key
                and novelty_level not in EDITORIAL_COOLDOWN_BYPASS
            ):
                return EditorialExclusion(
                    reason="cross_day_editorial_cooldown",
                    replaced_by_id=replaced_by_id,
                    limit_key="editorial_key:3d",
                    limit_value=self.config.editorial_cooldown_days,
                )
        return None

    @staticmethod
    def _sort_key(item: ContentItem) -> tuple[float, float, float, float, float, str]:
        analysis = item.processing.analysis if item.processing else None
        published_at = EditorialSelector._as_utc(item.published_at)
        return (
            -float(analysis.score if analysis.score is not None else -1.0)
            if analysis
            else 1.0,
            -float(
                analysis.relevance_score
                if analysis.relevance_score is not None
                else -1.0
            )
            if analysis
            else 1.0,
            -float(
                analysis.novelty_score
                if analysis.novelty_score is not None
                else -1.0
            )
            if analysis
            else 1.0,
            -float(
                analysis.demonstrability_score
                if analysis.demonstrability_score is not None
                else -1.0
            )
            if analysis
            else 1.0,
            -published_at.timestamp(),
            item.id,
        )

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"version": 1, "items": []}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "items": []}
        if data.get("version") != 1 or not isinstance(data.get("items"), list):
            return {"version": 1, "items": []}
        return {"version": 1, "items": data["items"]}

    def _pruned_items(
        self,
        items: list[dict[str, Any]],
        now: datetime,
    ) -> list[dict[str, Any]]:
        cutoff = now - timedelta(days=self.config.history_days)
        return [
            item
            for item in items
            if isinstance(item, dict)
            and (selected_at := self._parse_datetime(item.get("selected_at"))) is not None
            and selected_at >= cutoff
        ][-self.config.max_history_entries :]

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return EditorialSelector._as_utc(datetime.fromisoformat(value))
        except ValueError:
            return None

    @staticmethod
    def _as_utc(moment: datetime) -> datetime:
        if moment.tzinfo is None:
            return moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

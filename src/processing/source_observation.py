"""Normalize cross-source identity and observation metadata."""

from __future__ import annotations

from ..models import ContentItem, SourceType


AGGREGATORS = {
    SourceType.AIHOT,
    SourceType.GOOGLE_NEWS,
    SourceType.PLATFORM_TRENDS,
}
PRIMARY_SOURCES = {
    SourceType.BILIBILI,
    SourceType.BLUESKY,
    SourceType.HACKERNEWS,
    SourceType.HUGGINGFACE,
    SourceType.REDDIT,
    SourceType.TWITTER,
    SourceType.YOUTUBE,
}


def normalize_source_observation(item: ContentItem) -> ContentItem:
    metadata = dict(item.metadata)
    engagement = metadata.get("engagement")
    engagement_fields = (
        sorted(str(key) for key in engagement if engagement.get(key) is not None)
        if isinstance(engagement, dict)
        else []
    )
    native_id = next(
        (
            str(metadata[key])
            for key in (
                "platform_native_id",
                "post_uri",
                "video_id",
                "tweet_id",
                "reddit_id",
                "native_id",
            )
            if metadata.get(key) not in (None, "")
        ),
        item.id,
    )
    metadata.setdefault("source_item_id", native_id)
    metadata.setdefault("source_id", item.source_type.value)
    metadata.setdefault("content_platform", item.source_type.value)
    metadata.setdefault("source_level", _infer_source_level(item))
    metadata.setdefault("raw_url", str(item.url))
    metadata.setdefault("first_discovered_at", item.fetched_at.isoformat())
    metadata["engagement_fields_present"] = engagement_fields
    return item.model_copy(update={"metadata": metadata}, deep=True)


def _infer_source_level(item: ContentItem) -> str:
    explicit = str(item.metadata.get("source_level") or "").strip()
    if explicit:
        return explicit
    category = str(item.metadata.get("category") or "")
    if item.source_type is SourceType.RSS and category.startswith("official-"):
        return "official"
    if item.source_type is SourceType.GITHUB:
        return "official" if "release" in item.id else "primary"
    if item.source_type in AGGREGATORS:
        return "aggregator"
    if item.source_type in PRIMARY_SOURCES:
        return "primary"
    return "secondary"

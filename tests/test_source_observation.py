from datetime import datetime, timezone

from src.models import ContentItem, SourceType
from src.processing.source_observation import normalize_source_observation


NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


def test_normalizes_source_identity_originality_and_engagement_fields() -> None:
    item = ContentItem(
        id="youtube:video:abc",
        source_type=SourceType.YOUTUBE,
        title="Demo",
        url="https://youtube.com/watch?v=abc",
        author="Creator",
        published_at=NOW,
        fetched_at=NOW,
        metadata={
            "video_id": "abc",
            "engagement": {"views": 1000, "likes": 20},
        },
    )

    normalized = normalize_source_observation(item)

    assert normalized.metadata["source_item_id"] == "abc"
    assert normalized.metadata["source_id"] == "youtube"
    assert normalized.metadata["content_platform"] == "youtube"
    assert normalized.metadata["source_level"] == "primary"
    assert normalized.metadata["raw_url"] == str(item.url)
    assert normalized.metadata["first_discovered_at"] == NOW.isoformat()
    assert normalized.metadata["engagement_fields_present"] == ["likes", "views"]


def test_official_metadata_is_never_downgraded() -> None:
    item = ContentItem(
        id="rss:official:update",
        source_type=SourceType.RSS,
        title="Official update",
        url="https://example.com/update",
        published_at=NOW,
        metadata={"source_level": "official", "source_id": "vendor-news"},
    )

    normalized = normalize_source_observation(item)

    assert normalized.metadata["source_level"] == "official"
    assert normalized.metadata["source_id"] == "vendor-news"

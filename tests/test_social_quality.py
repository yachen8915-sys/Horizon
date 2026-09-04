from datetime import datetime, timedelta, timezone

from src.models import (
    ContentItem,
    SocialEngagementQualityConfig,
    SourceType,
)
from src.processing.social_quality import SocialEngagementQualityGate


NOW = datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc)


def _item(
    item_id: str,
    *,
    source_type: SourceType = SourceType.BILIBILI,
    age_hours: float,
    engagement: dict,
) -> ContentItem:
    return ContentItem(
        id=item_id,
        source_type=source_type,
        title=item_id,
        url=f"https://example.com/{item_id}",
        published_at=NOW - timedelta(hours=age_hours),
        metadata={"engagement": engagement, "ai_score": 10},
    )


def _gate() -> SocialEngagementQualityGate:
    return SocialEngagementQualityGate(
        SocialEngagementQualityConfig.model_validate(
            {
                "enabled": True,
                "platforms": {
                    "bilibili": {
                        "maturity_hours": 6,
                        "minimum_sample": 200,
                        "minimum_complete_fields": 5,
                        "required_metrics": [
                            "views",
                            "likes",
                            "comments",
                            "favorites",
                            "shares",
                            "coins",
                            "danmaku",
                        ],
                    },
                    "youtube": {
                        "maturity_hours": 12,
                        "minimum_sample": 1000,
                        "minimum_complete_fields": 3,
                        "required_metrics": ["views", "likes", "comments"],
                    },
                    "twitter": {
                        "maturity_hours": 3,
                        "primary_metric": "impressions",
                        "minimum_sample": 5000,
                        "minimum_complete_fields": 4,
                        "required_metrics": [
                            "impressions",
                            "likes",
                            "reposts",
                            "replies",
                            "quotes",
                        ],
                    },
                    "bluesky": {
                        "maturity_hours": 3,
                        "primary_metric": "likes",
                        "minimum_sample": 50,
                        "minimum_complete_fields": 4,
                        "required_metrics": [
                            "likes",
                            "reposts",
                            "replies",
                            "quotes",
                        ],
                    },
                    "twitter_opencli": {
                        "maturity_hours": 3,
                        "primary_metric": "impressions",
                        "minimum_sample": 5000,
                        "minimum_complete_fields": 2,
                        "required_metrics": ["impressions", "likes"],
                    },
                },
            }
        )
    )


def test_immature_social_item_enters_observation_pool() -> None:
    item = _item("fresh", age_hours=1, engagement={"views": 0})

    result = _gate().evaluate([item], now=NOW)

    assert result.eligible == []
    assert result.observing == [item]
    assert item.metadata["engagement_gate_reason"] == "insufficient_maturity"


def test_mature_low_propagation_is_rejected_even_with_high_ai_score() -> None:
    weak = _item(
        "weak",
        age_hours=18,
        engagement={
            "views": 248,
            "likes": 2,
            "comments": 0,
            "favorites": 1,
            "shares": 0,
            "coins": 0,
            "danmaku": 0,
        },
    )
    strong = _item(
        "strong",
        age_hours=18,
        engagement={
            "views": 4000,
            "likes": 300,
            "comments": 40,
            "favorites": 180,
            "shares": 40,
            "coins": 80,
            "danmaku": 20,
        },
    )

    result = _gate().evaluate([weak, strong], now=NOW)

    assert strong in result.eligible
    assert weak in result.rejected
    assert weak.metadata["engagement_gate_reason"] == "low_propagation"


def test_mature_social_item_with_incomplete_metrics_is_rejected() -> None:
    video = _item(
        "incomplete",
        source_type=SourceType.YOUTUBE,
        age_hours=20,
        engagement={"views": 50000, "likes": 2000},
    )

    result = _gate().evaluate([video], now=NOW)

    assert result.rejected == [video]
    assert video.metadata["engagement_gate_reason"] == "incomplete_engagement"


def test_mature_youtube_rss_waits_for_metrics_instead_of_entering_digest() -> None:
    video = _item(
        "youtube-rss-pending",
        source_type=SourceType.RSS,
        age_hours=20,
        engagement={},
    )
    video.metadata.update(
        {
            "category": "overseas-ai-video",
            "quality_platform": "youtube",
            "engagement_pending": True,
            "source_level": "primary",
        }
    )

    result = _gate().evaluate([video], now=NOW)

    assert result.eligible == []
    assert result.observing == [video]
    assert video.metadata["engagement_gate_reason"] == "engagement_pending"


def test_official_rss_is_not_subject_to_social_thresholds() -> None:
    official = _item(
        "official",
        source_type=SourceType.RSS,
        age_hours=1,
        engagement={},
    )

    result = _gate().evaluate([official], now=NOW)

    assert result.eligible == [official]
    assert official.metadata["engagement_gate_status"] == "not_applicable"


def test_official_social_announcement_bypasses_propagation_threshold() -> None:
    official = _item(
        "official-social",
        source_type=SourceType.TWITTER,
        age_hours=1,
        engagement={},
    )
    official.metadata["source_level"] = "official"

    result = _gate().evaluate([official], now=NOW)

    assert result.eligible == [official]
    assert official.metadata["engagement_gate_reason"] == "official_source"


def test_x_uses_impressions_as_primary_propagation_metric() -> None:
    post = _item(
        "x-post",
        source_type=SourceType.TWITTER,
        age_hours=5,
        engagement={
            "impressions": 90000,
            "likes": 3000,
            "reposts": 600,
            "replies": 120,
            "quotes": 80,
        },
    )

    result = _gate().evaluate([post], now=NOW)

    assert result.eligible == [post]
    assert post.metadata["engagement_primary_metric"] == "impressions"


def test_bluesky_uses_likes_and_complete_public_metrics() -> None:
    post = _item(
        "bluesky-post",
        source_type=SourceType.BLUESKY,
        age_hours=5,
        engagement={
            "likes": 120,
            "reposts": 25,
            "replies": 15,
            "quotes": 5,
        },
    )

    result = _gate().evaluate([post], now=NOW)

    assert result.eligible == [post]
    assert post.metadata["engagement_primary_metric"] == "likes"


def test_x_opencli_uses_explicit_two_metric_fallback_policy() -> None:
    post = _item(
        "x-opencli-post",
        source_type=SourceType.TWITTER,
        age_hours=5,
        engagement={"impressions": 90000, "likes": 3000},
    )
    post.metadata["quality_platform"] = "twitter_opencli"

    result = _gate().evaluate([post], now=NOW)

    assert result.eligible == [post]
    assert post.metadata["content_platform"] == "twitter_opencli"
    assert post.metadata["engagement_complete_fields"] == 2

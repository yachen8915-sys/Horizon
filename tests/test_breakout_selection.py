from datetime import datetime, timedelta, timezone

from src.models import (
    ClassificationResult,
    ContentAnalysis,
    ContentItem,
    DigestConfig,
    ProcessingResult,
    SourceType,
)
from src.processing.breakout_selection import BreakoutSelector


def _item(
    item_id: str,
    *,
    profile: str = "pangmen-topic-radar",
    source_type: SourceType = SourceType.AIHOT,
    score: float = 8.0,
    evidence: float = 8.0,
    surprise: float = 8.0,
    audience: float = 8.0,
    actionability: float = 8.0,
    novelty: float = 8.0,
    novelty_level: str = "material_update",
    event_key: str | None = None,
    canonical_theme: str = "ai_product_update",
    controversial: bool = False,
    metadata: dict | None = None,
) -> ContentItem:
    return ContentItem(
        id=item_id,
        source_type=source_type,
        title=item_id,
        url=f"https://example.com/{item_id}",
        published_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
        profile=profile,
        metadata=metadata or {},
        processing=ProcessingResult(
            classification=ClassificationResult(
                profile=profile,
                method="source_override",
            ),
            analysis=ContentAnalysis(
                score=score,
                reason="test",
                summary=item_id,
                primary_entity="example",
                topic_cluster="ai_product_updates",
                canonical_theme=canonical_theme,
                use_case="daily_ai_work",
                content_format="feature_update",
                novelty_level=novelty_level,
                event_key=event_key or f"event_{item_id}",
                editorial_key="example|daily_ai_work|feature_update",
                relevance_score=score,
                novelty_score=novelty,
                demonstrability_score=score,
                evidence_quality_score=evidence,
                audience_breadth_score=audience,
                surprise_score=surprise,
                actionability_score=actionability,
                breakout_reason="test breakout",
                controversial_topic=controversial,
            ),
        ),
    )


def test_official_material_update_can_be_standard_breakout_without_social_heat() -> None:
    item = _item(
        "official-release",
        metadata={"source_kind": "official"},
        novelty=9.0,
        evidence=8.0,
        surprise=9.0,
        audience=9.0,
        actionability=8.0,
    )

    result = BreakoutSelector(DigestConfig()).select([item])

    assert result.items == [item]
    assert item.metadata["source_signal_score"] == 9.0
    assert item.metadata["breakout_score"] >= 8.0
    assert item.metadata["breakout_status"] == "breakout"
    assert item.metadata["breakout_eligibility_mode"] == "standard_pass"
    assert item.metadata["verification_status"] == "verified"


def test_aihot_high_heat_low_evidence_uses_unverified_relaxed_lane() -> None:
    low = _item(
        "lower-aihot",
        evidence=8.0,
        metadata={"aihot_score": 40},
    )
    hot = _item(
        "viral-aihot",
        evidence=3.0,
        surprise=10.0,
        audience=10.0,
        actionability=10.0,
        metadata={"aihot_score": 98, "hot_topic": True, "ai_media_candidate": True},
    )

    result = BreakoutSelector(DigestConfig()).select([low, hot])

    assert hot in result.items
    assert hot.metadata["source_signal_score"] == 9.88
    assert hot.metadata["breakout_status"] == "hot_unverified"
    assert hot.metadata["breakout_eligibility_mode"] == "high_heat_relaxed_pass"
    assert hot.metadata["verification_status"] == "unverified"
    assert hot.processing.analysis.summary == (
        "“viral-aihot”正在引发热议；现有来源不足以确认标题所述具体事实。"
    )


def test_platform_relaxed_controversial_topics_are_capped_at_three() -> None:
    items = [
        _item(
            f"celebrity-rumor-{index}",
            profile="pangmen-platform-trend-radar",
            source_type=SourceType.PLATFORM_TRENDS,
            evidence=3.0,
            surprise=10.0,
            audience=10.0,
            actionability=9.0,
            controversial=True,
            metadata={"heat_score": 9.8 - index * 0.1},
        )
        for index in range(4)
    ]

    result = BreakoutSelector(
        DigestConfig(controversial_topic_limit=3)
    ).select(items)

    assert [item.id for item in result.items] == [
        "celebrity-rumor-0",
        "celebrity-rumor-1",
        "celebrity-rumor-2",
    ]
    assert result.exclusions["celebrity-rumor-3"].reason == "controversial_topic_limit"


def test_same_event_prefers_ai_section_over_platform_trend() -> None:
    ai_item = _item("ai-original", event_key="shared_release")
    trend_item = _item(
        "trend-copy",
        profile="pangmen-platform-trend-radar",
        source_type=SourceType.PLATFORM_TRENDS,
        event_key="shared_release",
        metadata={"heat_score": 10.0},
    )

    result = BreakoutSelector(DigestConfig()).select([trend_item, ai_item])

    assert result.items == [ai_item]
    assert result.exclusions["trend-copy"].reason == "cross_section_duplicate"
    assert result.exclusions["trend-copy"].replaced_by_id == "ai-original"


def test_canonical_url_repeat_is_removed_even_without_matching_event_key() -> None:
    original = _item("original", event_key="event_original")
    copy = _item("copy", event_key="event_copy")
    copy.url = "https://example.com/original?utm_source=mirror"

    result = BreakoutSelector(DigestConfig()).select([copy, original])

    assert result.items == [original]
    assert result.exclusions["copy"].reason == "cross_section_duplicate"
    assert result.exclusions["copy"].limit_key.startswith("url:")


def test_media_section_requires_verified_source_signal() -> None:
    weak = _item(
        "weak-media",
        evidence=7.0,
        metadata={"ai_media_candidate": True, "aihot_score": 1},
    )
    strong = _item(
        "strong-media",
        evidence=7.0,
        metadata={"ai_media_candidate": True, "aihot_score": 90},
    )

    BreakoutSelector(DigestConfig()).select([weak, strong])

    assert weak.metadata["display_section"] == "ai_application"
    assert strong.metadata["display_section"] == "ai_media"


def test_no_qualified_item_does_not_create_breakout() -> None:
    item = _item(
        "ordinary-item",
        evidence=8.0,
        surprise=3.0,
        audience=4.0,
        actionability=4.0,
        metadata={"aihot_score": 10},
    )

    BreakoutSelector(DigestConfig()).select([item])

    assert item.metadata["breakout_status"] == "none"
    assert item.metadata["breakout_eligibility_mode"] == "rejected"
    assert item.metadata["breakout_reason"] == "below_breakout_threshold"


def test_high_heat_sports_topic_can_be_verified_breakout() -> None:
    item = _item(
        "sports-brand-moment",
        profile="pangmen-platform-trend-radar",
        source_type=SourceType.PLATFORM_TRENDS,
        evidence=8.0,
        surprise=9.0,
        audience=10.0,
        actionability=8.0,
        metadata={"heat_score": 9.6, "cross_platform_count": 3},
    )

    BreakoutSelector(DigestConfig()).select([item])

    assert item.metadata["breakout_status"] == "breakout"
    assert item.metadata["breakout_eligibility_mode"] == "standard_pass"
    assert item.metadata["verification_status"] == "verified"


def test_bilibili_source_signal_compares_views_relative_to_content_age() -> None:
    fetched_at = datetime(2026, 8, 23, 12, tzinfo=timezone.utc)
    old = _item(
        "old-video",
        source_type=SourceType.BILIBILI,
        metadata={"engagement": {"views": 10_000}},
    )
    fresh = _item(
        "fresh-video",
        source_type=SourceType.BILIBILI,
        metadata={"engagement": {"views": 10_000}},
    )
    old.fetched_at = fresh.fetched_at = fetched_at
    old.published_at = fetched_at - timedelta(hours=48)
    fresh.published_at = fetched_at - timedelta(hours=4)

    BreakoutSelector(DigestConfig()).annotate([old, fresh])

    assert fresh.metadata["source_signal_score"] > old.metadata["source_signal_score"]

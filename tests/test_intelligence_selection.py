from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.models import (
    CandidateRecord,
    CandidateScore,
    CandidateStatus,
    ClassificationResult,
    ContentAnalysis,
    ContentItem,
    DecisionLane,
    DeliveryRecord,
    EvidenceStatus,
    IntelligenceAnalysis,
    IntelligenceSelectionConfig,
    ProcessingResult,
    RadarRunMode,
    SourceType,
)
from src.processing.intelligence_selection import IntelligenceSelector


NOW = datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc)


def _candidate(
    candidate_id: str,
    *,
    lane: DecisionLane,
    total: float,
    author: str = "author",
    source_id: str = "source",
    platform: str = "web",
    topic: str | None = None,
    event_version: int = 1,
    evidence: EvidenceStatus = EvidenceStatus.CONFIRMED,
    impact: float = 8,
) -> CandidateRecord:
    analysis = IntelligenceAnalysis(
        primary_lane=lane,
        decision_summary="worth seeing",
        content_summary="what happened",
        evidence_status=evidence,
        score=CandidateScore(
            decision_impact=impact,
            audience_fit=8,
            novelty=8,
            evidence_quality=8,
            demonstrability=8,
            propagation_quality=8,
            freshness=8,
            differentiation=8,
            total=total,
        ),
    )
    return CandidateRecord(
        candidate_id=candidate_id,
        item=ContentItem(
            id=candidate_id,
            source_type=SourceType.RSS,
            title=candidate_id,
            url=f"https://example.com/{candidate_id}",
            author=author,
            published_at=NOW,
            metadata={"source_id": source_id, "content_platform": platform},
        ),
        status=CandidateStatus.ELIGIBLE,
        discovered_at=NOW,
        updated_at=NOW,
        event_key=f"event:{candidate_id}",
        event_version=event_version,
        editorial_topic_key=topic or f"topic:{candidate_id}",
        evidence_status=evidence,
        intelligence=analysis,
        rule_version="v1",
    )


def _selector(**overrides) -> IntelligenceSelector:
    config = IntelligenceSelectionConfig(**overrides)
    return IntelligenceSelector(config)


def _trend(candidate_id: str, *, operations=8, content=7, rank=10, heat=100,
           providers=None, **kwargs) -> CandidateRecord:
    candidate = _candidate(
        candidate_id, lane=DecisionLane.HOT_CONTENT, total=8,
        author="", source_id="dailyhotapi", platform="weibo", **kwargs,
    )
    candidate.item.processing = ProcessingResult(
        classification=ClassificationResult(
            profile="pangmen-platform-trend-radar", method="source_override",
        ),
        analysis=ContentAnalysis(
            reason="safe operations opportunity", summary="platform trend",
            score=8, operations_score=operations, content_opportunity_score=content,
        ),
    )
    candidate.item.metadata.update(
        rank=rank, hot_value=heat,
        providers=providers if providers is not None else ["DailyHotAPI"],
    )
    return candidate


def test_six_unverified_platform_trends_survive_evidence_and_aggregator_limits():
    rows = [_trend(f"trend-{i}", evidence=EvidenceStatus.UNVERIFIED) for i in range(6)]
    result = _selector(unverified_hot_limit=3, author_limit=1,
                       source_limit=1, platform_limit=1).select(rows, now=NOW)
    assert len(result.selected) == 6
    assert result.held == []


@pytest.mark.parametrize("duplicate_field", ["event_key", "editorial_topic_key"])
def test_trends_still_hold_exact_duplicates_even_for_major_events(duplicate_field):
    first, second = _trend("a"), _trend("b")
    setattr(second, duplicate_field, getattr(first, duplicate_field))
    for row in [first, second]:
        row.intelligence.score.total = 9.5
        row.intelligence.score.decision_impact = 10
    result = _selector().select([second, first], now=NOW)
    assert [row.candidate_id for row in result.selected] == ["a"]
    assert [row.candidate_id for row in result.held] == ["b"]


def test_trend_titles_sharing_broad_words_are_not_fuzzy_merged():
    rows = [_trend("a"), _trend("b")]
    rows[0].item.title = "AI 工作方式改变了，新人如何准备面试"
    rows[1].item.title = "AI 工作方式改变了，公司开始重新设计周报"
    result = _selector(author_limit=1, source_limit=1).select(rows, now=NOW)
    assert len(result.selected) == 2


def test_platform_trend_sort_uses_only_operations_providers_rank_heat_content_and_id():
    rows = [
        _trend("operations", operations=9, rank=99, content=1),
        _trend("providers", providers=["ALAPI", "DailyHotAPI"], rank=99),
        _trend("rank", rank=1, heat=1, content=1),
        _trend("heat", heat=500, content=1),
        _trend("content", content=9),
        _trend("a", providers=[" DAILYHOTAPI ", "dailyhotapi"]),
        _trend("z"),
    ]
    rows[-1].evidence_status = EvidenceStatus.CONFIRMED
    rows[-1].intelligence.score.total = 10
    rows[-1].intelligence.score.freshness = 10
    rows[0].evidence_status = EvidenceStatus.UNVERIFIED
    result = _selector(max_items=20).select(list(reversed(rows)), now=NOW)
    assert [row.candidate_id for row in result.selected] == [
        "operations", "providers", "rank", "heat", "content", "a", "z",
    ]


def test_platform_trend_sort_handles_legacy_scores_and_invalid_native_signals():
    fallback = _trend("fallback", operations=None, content=None, rank="bad", heat=None)
    fallback.item.processing.analysis.score = 9
    invalid = _trend("z-invalid", rank=True, heat=float("nan"))
    valid = _trend("valid", rank=2, heat=10)
    result = _selector().select([invalid, valid, fallback], now=NOW)
    assert [row.candidate_id for row in result.selected] == ["fallback", "valid", "z-invalid"]


def test_sixteenth_trend_is_retained_as_capacity_overflow():
    rows = [_trend(f"trend-{i:02}") for i in range(16)]
    result = _selector(max_items=20).select(rows, now=NOW)
    assert len(result.selected) == 15
    assert [row.candidate_id for row in result.held] == ["trend-15"]
    assert result.held[0].reason_codes[-1].value == "held_by_capacity"
    assert result.held[0].item.processing.classification.profile == "pangmen-platform-trend-radar"
    assert result.rejected == []


def test_platform_trend_limit_can_be_lowered_and_never_forces_fill():
    rows = [_trend(f"trend-{i}") for i in range(8)]
    assert len(_selector(max_items=20).select(rows, now=NOW).selected) == 8
    result = _selector(max_items=20, platform_trend_detail_limit=5).select(rows, now=NOW)
    assert len(result.selected) == 5
    assert len(result.held) == 3


@pytest.mark.parametrize("limit", [0, 16])
def test_platform_trend_detail_limit_rejects_out_of_range_values(limit):
    with pytest.raises(ValidationError):
        IntelligenceSelectionConfig(platform_trend_detail_limit=limit)


def test_platform_trend_detail_limit_defaults_to_fifteen():
    assert IntelligenceSelectionConfig().platform_trend_detail_limit == 15


def test_mixed_lanes_still_obey_the_twenty_item_total_capacity():
    trends = [_trend(f"trend-{i:02}", operations=10) for i in range(15)]
    ai_rows = [
        _candidate(f"ai-{i}", lane=DecisionLane.PRODUCT_CAPABILITY, total=8,
                   author=f"author-{i}", source_id=f"source-{i}", platform=f"platform-{i}")
        for i in range(6)
    ]
    result = _selector().select([*ai_rows, *trends], now=NOW)
    assert len(result.selected) == 20
    assert [row.candidate_id for row in result.held] == ["ai-5"]
    assert result.held[0].reason_codes[-1].value == "held_by_capacity"



def test_default_capacity_keeps_fifteen_trends_and_a_strict_ai_candidate():
    trends = [_trend(f"trend-{i:02}", operations=10) for i in range(16)]
    ai = _candidate("strict-ai", lane=DecisionLane.PRODUCT_CAPABILITY,
                    total=8, source_id="official", author="official")
    result = _selector().select([ai, *trends], now=NOW)
    assert len(result.selected) == 16
    assert [row.candidate_id for row in result.selected][-1] == "strict-ai"
    assert [row.candidate_id for row in result.held] == ["trend-15"]
    assert result.held[0].reason_codes[-1].value == "held_by_capacity"



def test_platform_trend_cooldown_remains_effective():
    trend = _trend("repeat")
    delivery = DeliveryRecord(
        delivery_id="d1", run_id="morning", run_mode=RadarRunMode.MORNING,
        delivered_at=NOW - timedelta(days=1), candidate_id=trend.candidate_id,
        event_key=trend.event_key, event_version=1,
        display_tier="selected", content_fingerprint="old",
    )
    result = _selector().select([trend], deliveries=[delivery], now=NOW)
    assert result.selected == []
    assert result.held[0].reason_codes[-1].value == "duplicate"


def test_raw_item_profile_also_identifies_platform_trends():
    rows = [_trend(f"trend-{i}", evidence=EvidenceStatus.UNVERIFIED) for i in range(6)]
    for row in rows:
        row.item.profile = "pangmen-platform-trend-radar"
        row.item.processing = None
    result = _selector().select(rows, now=NOW)
    assert len(result.selected) == 6


def test_duplicate_after_fifteen_details_is_not_exposed_as_capacity_overflow():
    rows = [_trend(f"trend-{i:02}") for i in range(17)]
    rows[-1].event_key = rows[-2].event_key
    result = _selector(max_items=20).select(rows, now=NOW)
    assert [row.reason_codes[-1].value for row in result.held] == ["held_by_capacity", "duplicate"]


def test_platform_trends_do_not_consume_ai_lane_diversity_counts():
    trend = _trend("trend", operations=10)
    ai = _candidate("ai", lane=DecisionLane.PRODUCT_CAPABILITY, total=8,
                    author="", source_id="dailyhotapi", platform="weibo")
    result = _selector(author_limit=1, source_limit=1, platform_limit=1).select([trend, ai], now=NOW)
    assert [row.candidate_id for row in result.selected] == ["trend", "ai"]


def test_ai_lane_still_obeys_source_and_platform_limits():
    rows = [_candidate(f"ai-{i}", lane=DecisionLane.PRODUCT_CAPABILITY,
                       total=8, author=f"author-{i}", platform="hackernews")
            for i in range(4)]
    result = _selector(source_limit=2, platform_limit=3).select(rows, now=NOW)
    assert len(result.selected) == 2
    assert all(row.reason_codes[-1].value == "held_by_diversity" for row in result.held)


@pytest.mark.parametrize("first_lane", ["trend", "ai"])
@pytest.mark.parametrize("duplicate_field", ["editorial_topic_key", "event_key"])
@pytest.mark.parametrize("max_items", [1, 20])
def test_cross_lane_exact_duplicates_precede_capacity_and_major_event_override(
    first_lane, duplicate_field, max_items,
):
    trend = _trend("trend", operations=10 if first_lane == "trend" else 8)
    ai = _candidate("ai", lane=DecisionLane.PRODUCT_CAPABILITY,
                    total=9.2, impact=10, evidence=EvidenceStatus.CONFIRMED)
    setattr(ai, duplicate_field, getattr(trend, duplicate_field))
    result = _selector(max_items=max_items, topic_limit=2).select([ai, trend], now=NOW)
    assert [row.candidate_id for row in result.selected] == [first_lane]
    assert [row.candidate_id for row in result.held] == ["ai" if first_lane == "trend" else "trend"]
    assert result.held[0].reason_codes[-1].value == "duplicate"
    assert result.soft_limit_overrides == []


@pytest.mark.parametrize("duplicate_field", ["editorial_topic_key", "event_key"])
@pytest.mark.parametrize("max_items", [15, 20])
def test_platform_overflow_prevents_duplicate_major_ai_in_details_or_more(
    duplicate_field, max_items,
):
    trends = [_trend(f"trend-{i:02}", operations=10) for i in range(16)]
    ai = _candidate("major-ai", lane=DecisionLane.PRODUCT_CAPABILITY,
                    total=9.2, impact=10, evidence=EvidenceStatus.CONFIRMED)
    setattr(ai, duplicate_field, getattr(trends[-1], duplicate_field))
    result = _selector(max_items=max_items).select([ai, *trends], now=NOW)
    assert len(result.selected) == 15
    assert [row.candidate_id for row in result.held] == ["trend-15", "major-ai"]
    assert [row.reason_codes[-1].value for row in result.held] == ["held_by_capacity", "duplicate"]
    assert result.soft_limit_overrides == []


@pytest.mark.parametrize("duplicate_field", ["editorial_topic_key", "event_key"])
def test_ai_capacity_overflow_also_prevents_a_later_duplicate_platform_trend(duplicate_field):
    filler = _trend("filler", operations=10)
    ai = _candidate("ai", lane=DecisionLane.PRODUCT_CAPABILITY,
                    total=9.2, impact=10, evidence=EvidenceStatus.CONFIRMED)
    trend = _trend("trend", operations=8)
    setattr(trend, duplicate_field, getattr(ai, duplicate_field))
    result = _selector(max_items=1).select([trend, ai, filler], now=NOW)
    assert [row.candidate_id for row in result.selected] == ["filler"]
    assert [row.candidate_id for row in result.held] == ["ai", "trend"]
    assert [row.reason_codes[-1].value for row in result.held] == ["held_by_capacity", "duplicate"]


def test_pure_ai_major_events_keep_the_existing_topic_diversity_override():
    rows = [_candidate(f"ai-{i}", lane=DecisionLane.PRODUCT_CAPABILITY,
                       total=9.2, impact=10, topic="same-topic") for i in range(2)]
    result = _selector(topic_limit=1).select(rows, now=NOW)
    assert len(result.selected) == 2
    assert result.held == []
    assert result.soft_limit_overrides == ["ai-1"]


def test_product_decisions_are_prioritized_when_scores_are_close() -> None:
    hot = _candidate("hot", lane=DecisionLane.HOT_CONTENT, total=8.5)
    product = _candidate("product", lane=DecisionLane.PRODUCT_CAPABILITY, total=8.1)

    result = _selector(max_items=1).select([hot, product], now=NOW)

    assert [row.candidate_id for row in result.selected] == ["product"]
    assert result.selected[0].status_history[-1].from_status is (
        CandidateStatus.ELIGIBLE
    )
    assert result.selected[0].status_history[-1].to_status is (
        CandidateStatus.SELECTED
    )


def test_admitted_industry_at_six_survives_legacy_threshold_and_sorting():
    candidate = _candidate("industry", lane=DecisionLane.AI_INDUSTRY_SOCIETY, total=6)
    result = _selector(minimum_score=6.3).select([candidate], now=NOW)
    assert [row.candidate_id for row in result.selected] == ["industry"]


def test_strict_lane_still_rejected_by_legacy_minimum_score():
    candidate = _candidate("product", lane=DecisionLane.PRODUCT_CAPABILITY, total=6)
    result = _selector(minimum_score=6.3).select([candidate], now=NOW)
    assert result.selected == []
    assert result.rejected[0].reason_codes[-1].value == "low_quality"


def test_selector_does_not_fill_target_with_low_quality_items() -> None:
    candidates = [
        _candidate(
            f"good-{index}",
            lane=DecisionLane.PRODUCT_CAPABILITY,
            total=8,
            author=f"author-{index}",
            source_id=f"source-{index}",
            platform=f"platform-{index}",
        )
        for index in range(3)
    ]

    result = _selector(target_min_items=8, max_items=12).select(candidates, now=NOW)

    assert len(result.selected) == 3


def test_below_minimum_score_is_rejected_with_an_explanation() -> None:
    weak = _candidate(
        "weak",
        lane=DecisionLane.PRODUCT_CAPABILITY,
        total=5,
    )

    result = _selector(minimum_score=7).select([weak], now=NOW)

    assert result.selected == []
    assert result.rejected[0].status is CandidateStatus.REJECTED
    assert result.rejected[0].reason_codes[-1].value == "low_quality"


def test_author_diversity_holds_the_third_ordinary_item() -> None:
    candidates = [
        _candidate(
            f"same-{index}",
            lane=DecisionLane.TECHNICAL_FRONTIER,
            total=8 - index / 10,
            author="same-author",
        )
        for index in range(3)
    ]

    result = _selector(author_limit=2).select(candidates, now=NOW)

    assert len(result.selected) == 2
    assert result.held[0].candidate_id == "same-2"


def test_confirmed_major_event_can_exceed_a_soft_diversity_limit() -> None:
    candidates = [
        _candidate(
            f"major-{index}",
            lane=DecisionLane.PRODUCT_CAPABILITY,
            total=9.2,
            impact=9.5,
            author="official-account",
        )
        for index in range(3)
    ]

    result = _selector(author_limit=2).select(candidates, now=NOW)

    assert len(result.selected) == 3
    assert result.soft_limit_overrides == ["major-2"]


def test_same_event_version_is_cooled_but_material_update_can_return() -> None:
    previous = DeliveryRecord(
        delivery_id="d1",
        run_id="morning",
        run_mode=RadarRunMode.MORNING,
        delivered_at=NOW - timedelta(days=1),
        candidate_id="old",
        event_key="event:product",
        event_version=1,
        display_tier="selected",
        content_fingerprint="old",
    )
    repeat = _candidate(
        "repeat",
        lane=DecisionLane.PRODUCT_CAPABILITY,
        total=9,
        event_version=1,
    ).model_copy(update={"event_key": "event:product"})
    update = _candidate(
        "update",
        lane=DecisionLane.PRODUCT_CAPABILITY,
        total=8.8,
        event_version=2,
    ).model_copy(update={"event_key": "event:product"})

    result = _selector().select([repeat, update], deliveries=[previous], now=NOW)

    assert [row.candidate_id for row in result.selected] == ["update"]
    assert [row.candidate_id for row in result.held] == ["repeat"]


def test_hot_content_has_no_separate_fixed_quota() -> None:
    candidates = [
        _candidate(
            f"hot-{index}",
            lane=DecisionLane.HOT_CONTENT,
            total=8,
            author=f"author-{index}",
            source_id=f"source-{index}",
            platform=f"platform-{index}",
        )
        for index in range(5)
    ]

    result = _selector(max_items=12).select(candidates, now=NOW)

    assert len(result.selected) == 5


def test_unverified_hot_content_is_capped_without_capping_verified_hot_content() -> None:
    candidates = [
        _candidate(
            f"unverified-{index}",
            lane=DecisionLane.HOT_CONTENT,
            total=8,
            author=f"author-{index}",
            source_id=f"source-{index}",
            platform=f"platform-{index}",
            evidence=EvidenceStatus.UNVERIFIED,
        )
        for index in range(4)
    ]
    candidates.append(
        _candidate(
            "confirmed",
            lane=DecisionLane.HOT_CONTENT,
            total=7.9,
            author="confirmed-author",
            source_id="confirmed-source",
            platform="confirmed-platform",
            evidence=EvidenceStatus.CONFIRMED,
        )
    )

    result = _selector(unverified_hot_limit=3).select(candidates, now=NOW)

    assert len(result.selected) == 4
    assert sum(
        row.evidence_status is EvidenceStatus.UNVERIFIED
        for row in result.selected
    ) == 3
    assert result.held[0].reason_codes[-1].value == "held_by_evidence_limit"

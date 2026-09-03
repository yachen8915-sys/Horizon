from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.models import (
    CandidateRecord,
    CandidateScore,
    CandidateStatus,
    ContentItem,
    DecisionLane,
    DeliveryRecord,
    EvidenceStatus,
    IntelligenceAnalysis,
    IntelligenceSelectionConfig,
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

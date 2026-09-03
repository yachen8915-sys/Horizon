from src.models import (
    CandidateScore,
    DecisionLane,
    EvidenceStatus,
)
from src.processing.intelligence_analysis import (
    IntelligenceDraft,
    assess_hard_gates,
    build_intelligence_analysis,
)


def _draft(**overrides):
    values = {
        "primary_lane": DecisionLane.PRODUCT_CAPABILITY,
        "content_kind": "product_update",
        "direct_impacts": ["users can complete a new workflow"],
        "decision_summary": "Worth testing for a demo.",
        "content_summary": "A product added a material capability.",
        "evidence_status": EvidenceStatus.CONFIRMED,
        "dimensions": CandidateScore(
            decision_impact=8,
            audience_fit=8,
            novelty=8,
            evidence_quality=9,
            demonstrability=8,
            propagation_quality=4,
            freshness=9,
            differentiation=7,
        ),
    }
    values.update(overrides)
    return IntelligenceDraft(**values)


def test_analysis_has_exactly_one_primary_decision_lane() -> None:
    analysis = build_intelligence_analysis(
        _draft(novelty_basis="new_angle")
    )

    assert analysis.primary_lane is DecisionLane.PRODUCT_CAPABILITY
    assert analysis.score.total > 0
    assert analysis.content_kind == "product_update"
    assert analysis.novelty_basis == "new_angle"
    assert analysis.direct_impacts == ["users can complete a new workflow"]


def test_financing_without_direct_product_or_user_impact_is_rejected() -> None:
    draft = _draft(content_kind="financing", direct_impacts=[])

    gate = assess_hard_gates(draft)

    assert gate.accepted is False
    assert gate.reason == "no_direct_decision_impact"


def test_financing_with_direct_product_impact_can_continue() -> None:
    draft = _draft(
        content_kind="financing",
        direct_impacts=["funding immediately expands the product into China"],
    )

    assert assess_hard_gates(draft).accepted is True


def test_unverified_product_claim_cannot_be_rescued_by_high_total_score() -> None:
    draft = _draft(
        evidence_status=EvidenceStatus.UNVERIFIED,
        dimensions=CandidateScore(
            decision_impact=10,
            audience_fit=10,
            novelty=10,
            evidence_quality=10,
            demonstrability=10,
            propagation_quality=10,
            freshness=10,
            differentiation=10,
        ),
    )

    gate = assess_hard_gates(draft)

    assert gate.accepted is False
    assert gate.reason == "evidence_insufficient"


def test_hot_content_requires_propagation_quality() -> None:
    draft = _draft(
        primary_lane=DecisionLane.HOT_CONTENT,
        evidence_status=EvidenceStatus.REPORTED,
        dimensions=CandidateScore(
            decision_impact=8,
            audience_fit=8,
            novelty=8,
            evidence_quality=6,
            demonstrability=6,
            propagation_quality=2,
            freshness=9,
            differentiation=8,
        ),
    )

    gate = assess_hard_gates(draft)

    assert gate.accepted is False
    assert gate.reason == "low_propagation"


def test_lane_weights_make_evidence_more_important_for_platform_changes() -> None:
    dimensions = CandidateScore(
        decision_impact=8,
        audience_fit=8,
        novelty=7,
        evidence_quality=10,
        demonstrability=4,
        propagation_quality=4,
        freshness=8,
        differentiation=6,
    )
    product = build_intelligence_analysis(
        _draft(primary_lane=DecisionLane.PRODUCT_CAPABILITY, dimensions=dimensions)
    )
    platform = build_intelligence_analysis(
        _draft(primary_lane=DecisionLane.PLATFORM_AI_CHANGE, dimensions=dimensions)
    )

    assert platform.score.total > product.score.total

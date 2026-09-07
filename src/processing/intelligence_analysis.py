"""Decision-lane scoring and non-overridable intelligence hard gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..models import (
    AtomicClaim,
    CandidateScore,
    DecisionLane,
    EvidenceReference,
    EvidenceStatus,
    IntelligenceAnalysis,
)


LANE_WEIGHTS: dict[DecisionLane, dict[str, float]] = {
    DecisionLane.PRODUCT_CAPABILITY: {
        "decision_impact": 0.25,
        "audience_fit": 0.20,
        "novelty": 0.15,
        "evidence_quality": 0.15,
        "demonstrability": 0.15,
        "propagation_quality": 0.00,
        "freshness": 0.05,
        "differentiation": 0.05,
    },
    DecisionLane.HOT_CONTENT: {
        "decision_impact": 0.10,
        "audience_fit": 0.20,
        "novelty": 0.15,
        "evidence_quality": 0.10,
        "demonstrability": 0.10,
        "propagation_quality": 0.20,
        "freshness": 0.10,
        "differentiation": 0.05,
    },
    DecisionLane.TECHNICAL_FRONTIER: {
        "decision_impact": 0.20,
        "audience_fit": 0.15,
        "novelty": 0.20,
        "evidence_quality": 0.20,
        "demonstrability": 0.10,
        "propagation_quality": 0.05,
        "freshness": 0.05,
        "differentiation": 0.05,
    },
    DecisionLane.AI_INDUSTRY_SOCIETY: {
        "decision_impact": 0.20,
        "audience_fit": 0.20,
        "novelty": 0.10,
        "evidence_quality": 0.15,
        "demonstrability": 0.05,
        "propagation_quality": 0.10,
        "freshness": 0.10,
        "differentiation": 0.10,
    },
    DecisionLane.PLATFORM_AI_CHANGE: {
        "decision_impact": 0.25,
        "audience_fit": 0.20,
        "novelty": 0.15,
        "evidence_quality": 0.25,
        "demonstrability": 0.05,
        "propagation_quality": 0.00,
        "freshness": 0.05,
        "differentiation": 0.05,
    },
}


class IntelligenceDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_lane: DecisionLane
    content_kind: Literal[
        "product_update",
        "technical_update",
        "hot_content",
        "industry_social",
        "platform_change",
        "financing",
        "personnel",
        "other",
    ]
    novelty_basis: Literal[
        "new_event",
        "new_release",
        "new_data",
        "new_angle",
        "ongoing_update",
        "none",
    ] = "none"
    direct_impacts: list[str] = Field(default_factory=list)
    decision_summary: str
    content_summary: str
    evidence_status: EvidenceStatus
    dimensions: CandidateScore
    claims: list[AtomicClaim] = Field(default_factory=list)
    evidence_refs: list[EvidenceReference] = Field(default_factory=list)


@dataclass(frozen=True)
class HardGateResult:
    accepted: bool
    reason: str


def calculate_weighted_score(
    lane: DecisionLane, dimensions: CandidateScore
) -> float:
    weights = LANE_WEIGHTS[lane]
    total = sum(
        float(getattr(dimensions, dimension)) * weight
        for dimension, weight in weights.items()
    )
    return round(total, 3)


def assess_hard_gates(
    draft: IntelligenceDraft,
    *,
    minimum_score: float = 6.5,
) -> HardGateResult:
    if draft.content_kind in {"financing", "personnel"} and not draft.direct_impacts:
        return HardGateResult(False, "no_direct_decision_impact")
    if draft.evidence_status is EvidenceStatus.DISPUTED:
        return HardGateResult(False, "evidence_disputed")
    if (
        draft.primary_lane is not DecisionLane.HOT_CONTENT
        and draft.evidence_status is EvidenceStatus.UNVERIFIED
    ):
        return HardGateResult(False, "evidence_insufficient")
    if draft.dimensions.audience_fit < 6 or draft.dimensions.decision_impact < 5:
        return HardGateResult(False, "low_relevance")
    if draft.dimensions.novelty < 5:
        return HardGateResult(False, "low_novelty")
    if (
        draft.primary_lane is DecisionLane.HOT_CONTENT
        and draft.dimensions.propagation_quality < 5
    ):
        return HardGateResult(False, "low_propagation")
    if calculate_weighted_score(draft.primary_lane, draft.dimensions) < minimum_score:
        return HardGateResult(False, "low_weighted_score")
    return HardGateResult(True, "passed_hard_gates")


def build_intelligence_analysis(draft: IntelligenceDraft) -> IntelligenceAnalysis:
    score = draft.dimensions.model_copy(
        update={
            "total": calculate_weighted_score(
                draft.primary_lane, draft.dimensions
            )
        }
    )
    return IntelligenceAnalysis(
        primary_lane=draft.primary_lane,
        content_kind=draft.content_kind,
        novelty_basis=draft.novelty_basis,
        direct_impacts=draft.direct_impacts,
        decision_summary=draft.decision_summary,
        content_summary=draft.content_summary,
        evidence_status=draft.evidence_status,
        claims=draft.claims,
        evidence_refs=draft.evidence_refs,
        score=score,
    )

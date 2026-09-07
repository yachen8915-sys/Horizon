"""Final candidate selection with freshness and explainable soft diversity."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..models import (
    CandidateRecord,
    CandidateStatus,
    CandidateStatusTransition,
    DecisionLane,
    DeliveryRecord,
    EvidenceStatus,
    IntelligenceSelectionConfig,
    ReasonCode,
)


LANE_PRIORITY_BOOST = {
    DecisionLane.PRODUCT_CAPABILITY: 0.6,
    DecisionLane.TECHNICAL_FRONTIER: 0.3,
    DecisionLane.PLATFORM_AI_CHANGE: 0.2,
    DecisionLane.HOT_CONTENT: 0.0,
    DecisionLane.AI_INDUSTRY_SOCIETY: 0.1,
}

EVIDENCE_SORT = {
    EvidenceStatus.CONFIRMED: 5,
    EvidenceStatus.CORROBORATED: 4,
    EvidenceStatus.REPORTED: 3,
    EvidenceStatus.UNVERIFIED: 2,
    EvidenceStatus.DISPUTED: 1,
}


@dataclass
class IntelligenceSelectionResult:
    selected: list[CandidateRecord] = field(default_factory=list)
    held: list[CandidateRecord] = field(default_factory=list)
    rejected: list[CandidateRecord] = field(default_factory=list)
    soft_limit_overrides: list[str] = field(default_factory=list)


class IntelligenceSelector:
    def __init__(self, config: IntelligenceSelectionConfig) -> None:
        self.config = config

    def select(
        self,
        candidates: list[CandidateRecord],
        *,
        deliveries: list[DeliveryRecord] | None = None,
        now: datetime | None = None,
    ) -> IntelligenceSelectionResult:
        observed_at = self._utc(now or datetime.now(timezone.utc))
        deliveries = deliveries or []
        recent_versions = self._recent_event_versions(deliveries, observed_at)
        eligible_with_analysis = [
            candidate
            for candidate in candidates
            if candidate.status is CandidateStatus.ELIGIBLE
            and candidate.intelligence is not None
        ]
        result = IntelligenceSelectionResult()
        eligible = []
        for candidate in eligible_with_analysis:
            assert candidate.intelligence is not None
            lane = candidate.intelligence.primary_lane
            content_gate_owns_score = (
                lane is DecisionLane.AI_INDUSTRY_SOCIETY
                or (
                    lane is DecisionLane.HOT_CONTENT
                    and candidate.item.processing
                    and candidate.item.processing.classification.profile
                    == "pangmen-platform-trend-radar"
                )
            )
            if (
                not content_gate_owns_score
                and candidate.intelligence.score.total < self.config.minimum_score
            ):
                result.rejected.append(
                    self._rejected(candidate, ReasonCode.LOW_QUALITY, observed_at)
                )
            else:
                eligible.append(candidate)
        eligible.sort(key=self._sort_key)

        author_counts: Counter[str] = Counter()
        source_counts: Counter[str] = Counter()
        platform_counts: Counter[str] = Counter()
        topic_counts: Counter[str] = Counter()
        unverified_hot_count = 0

        for candidate in eligible:
            if len(result.selected) >= self.config.max_items:
                result.held.append(
                    self._held(candidate, ReasonCode.HELD_BY_CAPACITY, observed_at)
                )
                continue
            is_unverified_hot = bool(
                candidate.intelligence
                and candidate.intelligence.primary_lane is DecisionLane.HOT_CONTENT
                and candidate.evidence_status is EvidenceStatus.UNVERIFIED
            )
            if (
                is_unverified_hot
                and unverified_hot_count >= self.config.unverified_hot_limit
            ):
                result.held.append(
                    self._held(
                        candidate,
                        ReasonCode.HELD_BY_EVIDENCE_LIMIT,
                        observed_at,
                    )
                )
                continue
            version_key = (candidate.event_key, candidate.event_version)
            if candidate.event_key and version_key in recent_versions:
                result.held.append(
                    self._held(candidate, ReasonCode.DUPLICATE, observed_at)
                )
                continue

            author = (candidate.item.author or "unknown").strip().lower()
            source = str(
                candidate.item.metadata.get("source_id")
                or candidate.item.source_type.value
            ).lower()
            platform = str(
                candidate.item.metadata.get("content_platform")
                or candidate.item.source_type.value
            ).lower()
            topic = candidate.editorial_topic_key or candidate.event_key or candidate.candidate_id
            exceeds = (
                author_counts[author] >= self.config.author_limit
                or source_counts[source] >= self.config.source_limit
                or platform_counts[platform] >= self.config.platform_limit
                or topic_counts[topic] >= self.config.topic_limit
            )
            if exceeds and not self._major_event(candidate):
                result.held.append(
                    self._held(candidate, ReasonCode.HELD_BY_DIVERSITY, observed_at)
                )
                continue
            if exceeds:
                result.soft_limit_overrides.append(candidate.candidate_id)

            result.selected.append(self._selected(candidate, observed_at))
            author_counts[author] += 1
            source_counts[source] += 1
            platform_counts[platform] += 1
            topic_counts[topic] += 1
            if is_unverified_hot:
                unverified_hot_count += 1
        return result

    def _sort_key(self, candidate: CandidateRecord) -> tuple:
        analysis = candidate.intelligence
        assert analysis is not None
        effective = analysis.score.total + LANE_PRIORITY_BOOST[analysis.primary_lane]
        return (
            -effective,
            -EVIDENCE_SORT[candidate.evidence_status],
            -analysis.score.freshness,
            -analysis.score.differentiation,
            candidate.candidate_id,
        )

    def _major_event(self, candidate: CandidateRecord) -> bool:
        analysis = candidate.intelligence
        return bool(
            analysis
            and analysis.score.total >= self.config.major_event_override_score
            and analysis.score.decision_impact >= 9
            and candidate.evidence_status is EvidenceStatus.CONFIRMED
        )

    def _recent_event_versions(
        self, deliveries: list[DeliveryRecord], now: datetime
    ) -> set[tuple[str, int]]:
        cutoff = now - timedelta(days=self.config.cooldown_days)
        return {
            (delivery.event_key, delivery.event_version)
            for delivery in deliveries
            if self._utc(delivery.delivered_at) >= cutoff
        }

    @staticmethod
    def _selected(
        candidate: CandidateRecord,
        observed_at: datetime,
    ) -> CandidateRecord:
        reasons = list(candidate.reason_codes)
        if ReasonCode.SELECTED not in reasons:
            reasons.append(ReasonCode.SELECTED)
        return candidate.model_copy(
            update={
                "status": CandidateStatus.SELECTED,
                "updated_at": observed_at,
                "reason_codes": reasons,
                "status_history": [
                    *candidate.status_history,
                    CandidateStatusTransition(
                        from_status=candidate.status,
                        to_status=CandidateStatus.SELECTED,
                        changed_at=observed_at,
                        reason_code=ReasonCode.SELECTED,
                    ),
                ],
            },
            deep=True,
        )

    @staticmethod
    def _held(
        candidate: CandidateRecord,
        reason: ReasonCode,
        observed_at: datetime,
    ) -> CandidateRecord:
        reasons = list(candidate.reason_codes)
        if reason not in reasons:
            reasons.append(reason)
        return candidate.model_copy(
            update={
                "status": CandidateStatus.HELD,
                "updated_at": observed_at,
                "reason_codes": reasons,
                "status_history": [
                    *candidate.status_history,
                    CandidateStatusTransition(
                        from_status=candidate.status,
                        to_status=CandidateStatus.HELD,
                        changed_at=observed_at,
                        reason_code=reason,
                    ),
                ],
            },
            deep=True,
        )

    @staticmethod
    def _rejected(
        candidate: CandidateRecord,
        reason: ReasonCode,
        observed_at: datetime,
    ) -> CandidateRecord:
        reasons = list(candidate.reason_codes)
        if reason not in reasons:
            reasons.append(reason)
        return candidate.model_copy(
            update={
                "status": CandidateStatus.REJECTED,
                "updated_at": observed_at,
                "reason_codes": reasons,
                "status_history": [
                    *candidate.status_history,
                    CandidateStatusTransition(
                        from_status=candidate.status,
                        to_status=CandidateStatus.REJECTED,
                        changed_at=observed_at,
                        reason_code=reason,
                    ),
                ],
            },
            deep=True,
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

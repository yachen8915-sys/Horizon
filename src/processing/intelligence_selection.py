"""Final candidate selection with freshness and explainable soft diversity."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from math import isfinite

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
        # Fill trend slots from leverage first; keep AI positions and pool-local ranking.
        trend_order = iter(sorted(
            (candidate for candidate in eligible if self._is_platform_trend(candidate)),
            key=lambda candidate: candidate.item.metadata.get("trend_pool") == "watch",
        ))
        eligible = [
            next(trend_order) if self._is_platform_trend(candidate) else candidate
            for candidate in eligible
        ]

        author_counts: Counter[str] = Counter()
        source_counts: Counter[str] = Counter()
        platform_counts: Counter[str] = Counter()
        topic_counts: Counter[str] = Counter()
        event_counts: Counter[str] = Counter()
        platform_trend_topics: set[str] = set()
        platform_trend_events: set[str] = set()
        other_topics: set[str] = set()
        other_events: set[str] = set()
        unverified_hot_count = 0
        platform_trend_selected_count = 0

        for candidate in eligible:
            is_platform_trend = self._is_platform_trend(candidate)
            topic = candidate.editorial_topic_key or candidate.event_key or candidate.candidate_id
            opposite_topics, opposite_events = (
                (other_topics, other_events)
                if is_platform_trend
                else (platform_trend_topics, platform_trend_events)
            )
            if topic in opposite_topics or candidate.event_key in opposite_events:
                result.held.append(
                    self._held(candidate, ReasonCode.DUPLICATE, observed_at)
                )
                continue
            if not is_platform_trend and len(result.selected) >= self.config.max_items:
                # The more tier must obey the same cross-lane identity boundary.
                other_topics.add(topic)
                if candidate.event_key:
                    other_events.add(candidate.event_key)
                result.held.append(
                    self._held(candidate, ReasonCode.HELD_BY_CAPACITY, observed_at)
                )
                continue
            is_unverified_hot = bool(
                not is_platform_trend
                and candidate.intelligence
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
            if is_platform_trend:
                if (
                    topic_counts[topic] >= self.config.topic_limit
                    or (candidate.event_key and event_counts[candidate.event_key])
                ):
                    result.held.append(
                        self._held(candidate, ReasonCode.DUPLICATE, observed_at)
                    )
                    continue
                # Capacity overflow is still a qualified, deduplicated candidate.
                topic_counts[topic] += 1
                platform_trend_topics.add(topic)
                if candidate.event_key:
                    event_counts[candidate.event_key] += 1
                    platform_trend_events.add(candidate.event_key)
                if (
                    len(result.selected) >= self.config.max_items
                    or platform_trend_selected_count >= self.config.platform_trend_detail_limit
                ):
                    result.held.append(
                        self._held(candidate, ReasonCode.HELD_BY_CAPACITY, observed_at)
                    )
                    continue
                result.selected.append(self._selected(candidate, observed_at))
                platform_trend_selected_count += 1
                continue
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
            other_topics.add(topic)
            if candidate.event_key:
                event_counts[candidate.event_key] += 1
                other_events.add(candidate.event_key)
            if is_unverified_hot:
                unverified_hot_count += 1
        return result

    def _sort_key(self, candidate: CandidateRecord) -> tuple:
        analysis = candidate.intelligence
        assert analysis is not None
        if self._is_platform_trend(candidate):
            content_analysis = candidate.item.processing.analysis if candidate.item.processing else None
            operations = content_analysis.operations_score if content_analysis else None
            content = content_analysis.content_opportunity_score if content_analysis else None
            fallback = content_analysis.score if content_analysis else None
            metadata = candidate.item.metadata
            providers = metadata.get("providers")
            if not isinstance(providers, (list, tuple, set)):
                providers = [metadata.get("provider")]
            provider_count = len({
                str(provider).strip().casefold()
                for provider in providers if provider and str(provider).strip()
            })
            rank = self._native_number(metadata.get("rank"))
            heat = self._native_number(metadata.get("hot_value"))
            return (
                -(operations if operations is not None else fallback or 0),
                -provider_count,
                rank if rank is not None and rank >= 1 else float("inf"),
                -(heat if heat is not None and heat >= 0 else 0),
                -(content if content is not None else fallback or 0),
                0,
                candidate.candidate_id,
            )
        effective = analysis.score.total + LANE_PRIORITY_BOOST[analysis.primary_lane]
        return (
            -effective,
            -EVIDENCE_SORT[candidate.evidence_status],
            -analysis.score.freshness,
            -analysis.score.differentiation,
            0,
            0,
            candidate.candidate_id,
        )

    @staticmethod
    def _is_platform_trend(candidate: CandidateRecord) -> bool:
        profile = (
            candidate.item.processing.classification.profile
            if candidate.item.processing else candidate.item.profile
        )
        return profile == "pangmen-platform-trend-radar"

    @staticmethod
    def _native_number(value: object) -> float | None:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value):
            return float(value)
        return None

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

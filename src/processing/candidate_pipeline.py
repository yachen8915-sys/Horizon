"""Convert fetched and analyzed content into durable candidate records."""

from __future__ import annotations

import re

from ..models import (
    CandidateRecord,
    CandidateStatus,
    CandidateStatusTransition,
    ContentItem,
    EvidenceReference,
    EvidenceStatus,
    ReasonCode,
    SourceType,
)
from .candidate_identity import canonicalize_url
from .evidence import assess_claim_evidence
from .intelligence_analysis import IntelligenceDraft, assess_hard_gates
from .source_observation import normalize_source_observation


AUTHORITY_BY_SOURCE = {
    SourceType.GITHUB: "official",
    SourceType.HUGGINGFACE: "primary",
    SourceType.REDDIT: "primary",
    SourceType.TWITTER: "primary",
    SourceType.YOUTUBE: "primary",
    SourceType.BILIBILI: "primary",
    SourceType.HACKERNEWS: "primary",
    SourceType.AIHOT: "aggregator",
    SourceType.GOOGLE_NEWS: "aggregator",
    SourceType.PLATFORM_TRENDS: "aggregator",
}


class CandidateBuilder:
    def __init__(self, rule_version: str):
        self.rule_version = rule_version

    def from_preanalysis_item(self, item: ContentItem) -> CandidateRecord:
        item = normalize_source_observation(item)
        gate_status = str(item.metadata.get("engagement_gate_status") or "")
        gate_reason = str(item.metadata.get("engagement_gate_reason") or "")
        if gate_status == "observing":
            status = CandidateStatus.OBSERVING
            reasons = [ReasonCode.IMMATURE]
        elif gate_status == "rejected":
            status = CandidateStatus.REJECTED
            reasons = [
                ReasonCode.INCOMPLETE_ENGAGEMENT
                if gate_reason == "incomplete_engagement"
                else ReasonCode.LOW_PROPAGATION
            ]
        elif gate_status in {"passed", "not_applicable"}:
            status = CandidateStatus.ENRICHED
            reasons = []
        else:
            status = CandidateStatus.DISCOVERED
            reasons = []
        return self._base_candidate(item, status=status, reason_codes=reasons)

    def from_analyzed_item(self, item: ContentItem) -> CandidateRecord:
        item = normalize_source_observation(item)
        legacy = item.processing.analysis if item.processing else None
        intelligence = legacy.intelligence if legacy else None
        if intelligence is None:
            return self._base_candidate(
                item,
                status=CandidateStatus.PROCESSING_ERROR,
                reason_codes=[ReasonCode.ANALYSIS_FAILED],
            )

        reference = self._evidence_reference(item)
        evidence_status = assess_claim_evidence([reference])
        intelligence = intelligence.model_copy(
            update={
                "evidence_status": evidence_status,
                "evidence_refs": [reference],
                "claims": [
                    claim.model_copy(
                        update={
                            "status": evidence_status,
                            "evidence_refs": [item.id],
                        }
                    )
                    for claim in intelligence.claims
                ],
            },
            deep=True,
        )

        draft = IntelligenceDraft(
            primary_lane=intelligence.primary_lane,
            content_kind=intelligence.content_kind,
            novelty_basis=intelligence.novelty_basis,
            direct_impacts=intelligence.direct_impacts,
            decision_summary=intelligence.decision_summary,
            content_summary=intelligence.content_summary,
            evidence_status=intelligence.evidence_status,
            dimensions=intelligence.score,
            claims=intelligence.claims,
            evidence_refs=intelligence.evidence_refs,
        )
        gate = assess_hard_gates(draft)
        if gate.accepted:
            status = CandidateStatus.ELIGIBLE
            reasons = [ReasonCode.PASSED_HARD_GATES]
        else:
            status = CandidateStatus.REJECTED
            reasons = [self._reason_code(gate.reason)]
        return self._base_candidate(
            item,
            status=status,
            reason_codes=reasons,
            intelligence=intelligence,
            evidence_status=evidence_status,
            evidence_refs=[reference],
        )

    def _base_candidate(
        self,
        item: ContentItem,
        *,
        status: CandidateStatus,
        reason_codes: list[ReasonCode],
        intelligence=None,
        evidence_status: EvidenceStatus = EvidenceStatus.UNVERIFIED,
        evidence_refs: list[EvidenceReference] | None = None,
    ) -> CandidateRecord:
        legacy = item.processing.analysis if item.processing else None
        event_key = (
            legacy.event_key
            if legacy and legacy.event_key
            else str(item.metadata.get("event_key") or item.id)
        )
        topic_key = None
        if legacy:
            topic_key = legacy.editorial_key or "|".join(
                value
                for value in (legacy.topic_cluster, legacy.use_case)
                if value
            )
        references = evidence_refs or [self._evidence_reference(item)]
        return CandidateRecord(
            candidate_id=item.id,
            item=item,
            status=status,
            discovered_at=item.fetched_at,
            updated_at=item.fetched_at,
            canonical_url=canonicalize_url(str(item.url)),
            source_item_id=str(item.metadata.get("source_item_id") or item.id),
            event_key=event_key,
            event_version=max(1, int(item.metadata.get("event_version") or 1)),
            event_version_at=item.fetched_at,
            editorial_topic_key=topic_key or None,
            evidence_status=evidence_status,
            intelligence=intelligence,
            evidence_refs=references,
            reason_codes=reason_codes,
            status_history=[
                CandidateStatusTransition(
                    from_status=None,
                    to_status=status,
                    changed_at=item.fetched_at,
                    reason_code=(reason_codes[-1] if reason_codes else None),
                )
            ],
            rule_version=self.rule_version,
        )

    def _evidence_reference(self, item: ContentItem) -> EvidenceReference:
        return EvidenceReference(
            source_item_id=str(item.metadata.get("source_item_id") or item.id),
            url=item.url,
            authority=self._authority(item),
            independent_group=str(
                item.metadata.get("independent_source_group")
                or item.metadata.get("feed_name")
                or item.author
                or item.source_type.value
            ),
            excerpt=self._evidence_excerpt(item),
        )

    @staticmethod
    def _evidence_excerpt(item: ContentItem) -> str | None:
        content = re.sub(r"\s+", " ", item.content or "").strip()
        return content[:500] or None

    @staticmethod
    def _authority(item: ContentItem) -> str:
        source_level = str(item.metadata.get("source_level") or "")
        if source_level == "official":
            return "official"
        if source_level == "official_republished":
            return "primary"
        if source_level in {"secondary", "unverified"}:
            return source_level
        category = str(item.metadata.get("category") or "")
        if item.source_type is SourceType.RSS and category.startswith("official-"):
            return "official"
        return AUTHORITY_BY_SOURCE.get(item.source_type, "secondary")

    @staticmethod
    def _reason_code(reason: str) -> ReasonCode:
        if reason in {"evidence_insufficient", "evidence_disputed"}:
            return ReasonCode.EVIDENCE_INSUFFICIENT
        if reason == "low_propagation":
            return ReasonCode.LOW_PROPAGATION
        if reason in {"low_relevance", "no_direct_decision_impact"}:
            return ReasonCode.LOW_RELEVANCE
        return ReasonCode.LOW_QUALITY

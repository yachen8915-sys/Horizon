"""Deterministic evidence-status rules applied after claim extraction."""

from __future__ import annotations

from ..models import AtomicClaim, EvidenceReference, EvidenceStatus


REPORTING_AUTHORITIES = {"official", "primary", "secondary"}


def assess_claim_evidence(
    references: list[EvidenceReference],
) -> EvidenceStatus:
    if any(reference.stance == "contradicts" for reference in references):
        return EvidenceStatus.DISPUTED

    supporting = [
        reference for reference in references if reference.stance == "supports"
    ]
    if any(reference.authority == "official" for reference in supporting):
        return EvidenceStatus.CONFIRMED

    reporting_groups = {
        reference.independent_group
        for reference in supporting
        if reference.authority in REPORTING_AUTHORITIES
    }
    if len(reporting_groups) >= 2:
        return EvidenceStatus.CORROBORATED
    if len(reporting_groups) == 1:
        return EvidenceStatus.REPORTED
    return EvidenceStatus.UNVERIFIED


def build_atomic_claim(
    text: str,
    references: list[EvidenceReference],
) -> AtomicClaim:
    return AtomicClaim(
        text=text,
        status=assess_claim_evidence(references),
        evidence_refs=[reference.source_item_id for reference in references],
    )

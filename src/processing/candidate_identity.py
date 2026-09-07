"""Layered URL, event-version, and editorial-topic identity rules."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..models import (
    CandidateRecord,
    CandidateStatus,
    CandidateStatusTransition,
    EvidenceReference,
    EvidenceStatus,
    ReasonCode,
)
from .evidence import assess_claim_evidence
from .source_observation import normalize_source_observation


TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "source",
}


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    port = f":{parts.port}" if parts.port else ""
    netloc = host + port
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in TRACKING_PARAMETERS
        )
    )
    return urlunsplit(((parts.scheme or "https").lower(), netloc, path, query, ""))


def candidate_identity_key(candidate: CandidateRecord) -> str:
    if candidate.event_key:
        return f"event:{candidate.event_key}:v{candidate.event_version}"
    canonical = candidate.canonical_url or canonicalize_url(str(candidate.item.url))
    if canonical:
        return f"url:{canonical}"
    return f"native:{candidate.source_item_id or candidate.item.id}"


def group_duplicate_candidates(
    candidates: list[CandidateRecord],
) -> list[list[CandidateRecord]]:
    groups: dict[str, list[CandidateRecord]] = {}
    order: list[str] = []
    for candidate in candidates:
        key = candidate_identity_key(candidate)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(candidate)
    return [groups[key] for key in order]


AUTHORITY_RANK = {
    "official": 4,
    "primary": 3,
    "secondary": 2,
    "aggregator": 1,
}

EVIDENCE_RANK = {
    EvidenceStatus.CONFIRMED: 5,
    EvidenceStatus.CORROBORATED: 4,
    EvidenceStatus.REPORTED: 3,
    EvidenceStatus.UNVERIFIED: 2,
    EvidenceStatus.DISPUTED: 1,
}


def _candidate_strength(candidate: CandidateRecord) -> tuple[int, int, float, str]:
    authority = max(
        (AUTHORITY_RANK.get(ref.authority, 0) for ref in candidate.evidence_refs),
        default=0,
    )
    score = candidate.intelligence.score.total if candidate.intelligence else 0.0
    return (
        authority,
        EVIDENCE_RANK[candidate.evidence_status],
        score,
        candidate.candidate_id,
    )


@dataclass
class CandidateMergeResult:
    primary: CandidateRecord
    duplicates: list[CandidateRecord]


def merge_duplicate_group(group: list[CandidateRecord]) -> CandidateMergeResult:
    if not group:
        raise ValueError("cannot merge an empty candidate group")
    primary_source = max(group, key=_candidate_strength)
    metadata = dict(primary_source.item.metadata)
    for plural, singular, fallback in (
        ("providers", "provider", "source_id"),
        ("platforms", "platform", "content_platform"),
    ):
        values: set[str] = set()
        for candidate in group:
            observed = normalize_source_observation(candidate.item).metadata
            raw_values = observed.get(plural)
            if isinstance(raw_values, (list, tuple, set)):
                values.update(
                    str(value).strip().casefold()
                    for value in raw_values
                    if value and str(value).strip()
                )
            value = str(
                observed.get(singular) or observed.get(fallback) or ""
            ).strip().casefold()
            if value:
                values.add(value)
        metadata[plural] = sorted(values)
    evidence_by_key: dict[tuple[str, str], EvidenceReference] = {}
    for candidate in group:
        for reference in candidate.evidence_refs:
            evidence_by_key[(reference.source_item_id, str(reference.url))] = reference
    combined_references = list(evidence_by_key.values())
    evidence_status = assess_claim_evidence(combined_references)
    intelligence = primary_source.intelligence
    if intelligence is not None:
        intelligence = intelligence.model_copy(
            update={
                "evidence_status": evidence_status,
                "evidence_refs": combined_references,
                "claims": [
                    claim.model_copy(
                        update={
                            "status": evidence_status,
                            "evidence_refs": [
                                reference.source_item_id
                                for reference in combined_references
                            ],
                        }
                    )
                    for claim in intelligence.claims
                ],
            },
            deep=True,
        )
    primary = primary_source.model_copy(
        update={
            "item": primary_source.item.model_copy(
                update={"metadata": metadata}, deep=True
            ),
            "evidence_refs": combined_references,
            "evidence_status": evidence_status,
            "intelligence": intelligence,
        },
        deep=True,
    )

    duplicates: list[CandidateRecord] = []
    for candidate in group:
        if candidate.candidate_id == primary.candidate_id:
            continue
        reasons = list(candidate.reason_codes)
        if ReasonCode.MERGED_INTO_STRONGER_EVIDENCE not in reasons:
            reasons.append(ReasonCode.MERGED_INTO_STRONGER_EVIDENCE)
        duplicates.append(
            candidate.model_copy(
                update={
                    "status": CandidateStatus.MERGED,
                    "merged_into": primary.candidate_id,
                    "reason_codes": reasons,
                    "status_history": [
                        *candidate.status_history,
                        CandidateStatusTransition(
                            from_status=candidate.status,
                            to_status=CandidateStatus.MERGED,
                            changed_at=candidate.updated_at,
                            reason_code=(
                                ReasonCode.MERGED_INTO_STRONGER_EVIDENCE
                            ),
                        ),
                    ],
                },
                deep=True,
            )
        )
    return CandidateMergeResult(primary=primary, duplicates=duplicates)

from __future__ import annotations

from src.models import EvidenceReference, EvidenceStatus
from src.processing.evidence import assess_claim_evidence


def _ref(
    source_id: str,
    *,
    authority: str,
    group: str,
    stance: str = "supports",
) -> EvidenceReference:
    return EvidenceReference(
        source_item_id=source_id,
        url=f"https://example.com/{source_id}",
        authority=authority,
        independent_group=group,
        stance=stance,
    )


def test_official_source_confirms_a_claim() -> None:
    assert assess_claim_evidence(
        [_ref("official", authority="official", group="vendor")]
    ) is EvidenceStatus.CONFIRMED


def test_two_independent_sources_corroborate_a_claim() -> None:
    refs = [
        _ref("media-a", authority="secondary", group="publisher-a"),
        _ref("media-b", authority="secondary", group="publisher-b"),
    ]

    assert assess_claim_evidence(refs) is EvidenceStatus.CORROBORATED


def test_single_media_report_does_not_become_confirmed() -> None:
    assert assess_claim_evidence(
        [_ref("media", authority="secondary", group="publisher")]
    ) is EvidenceStatus.REPORTED


def test_single_social_post_remains_unverified() -> None:
    assert assess_claim_evidence(
        [_ref("social", authority="social", group="account")]
    ) is EvidenceStatus.UNVERIFIED


def test_conflicting_independent_sources_are_disputed() -> None:
    refs = [
        _ref("support", authority="primary", group="source-a"),
        _ref(
            "contradict",
            authority="official",
            group="source-b",
            stance="contradicts",
        ),
    ]

    assert assess_claim_evidence(refs) is EvidenceStatus.DISPUTED


def test_reposts_from_same_publisher_count_as_one_source() -> None:
    refs = [
        _ref("wire-original", authority="secondary", group="wire-service"),
        _ref("wire-repost", authority="secondary", group="wire-service"),
    ]

    assert assess_claim_evidence(refs) is EvidenceStatus.REPORTED

from __future__ import annotations

from datetime import datetime, timezone

from src.models import (
    CandidateRecord,
    CandidateStatus,
    ContentItem,
    EvidenceReference,
    EvidenceStatus,
    SourceType,
)
from src.processing.candidate_identity import (
    canonicalize_url,
    group_duplicate_candidates,
    merge_duplicate_group,
)


NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


def _candidate(
    candidate_id: str,
    *,
    url: str,
    event_key: str | None = None,
    event_version: int = 1,
    topic_key: str | None = None,
    authority: str = "secondary",
    evidence_status: EvidenceStatus = EvidenceStatus.REPORTED,
) -> CandidateRecord:
    evidence = EvidenceReference(
        source_item_id=candidate_id,
        url=url,
        authority=authority,
        independent_group=candidate_id,
    )
    return CandidateRecord(
        candidate_id=candidate_id,
        item=ContentItem(
            id=candidate_id,
            source_type=SourceType.RSS,
            title=candidate_id,
            url=url,
            published_at=NOW,
        ),
        status=CandidateStatus.ENRICHED,
        discovered_at=NOW,
        updated_at=NOW,
        canonical_url=canonicalize_url(url),
        event_key=event_key,
        event_version=event_version,
        editorial_topic_key=topic_key,
        evidence_status=evidence_status,
        evidence_refs=[evidence],
        rule_version="v1",
    )


def test_canonical_url_removes_tracking_without_losing_real_parameters() -> None:
    assert canonicalize_url(
        "https://Example.com/update/?id=42&utm_source=x&ref=feed#section"
    ) == "https://example.com/update?id=42"


def test_same_event_across_sources_merges_into_stronger_official_record() -> None:
    media = _candidate(
        "media",
        url="https://news.example.com/story",
        event_key="openai:feature-x",
        authority="secondary",
    )
    official = _candidate(
        "official",
        url="https://openai.com/feature-x",
        event_key="openai:feature-x",
        authority="official",
        evidence_status=EvidenceStatus.CONFIRMED,
    )

    groups = group_duplicate_candidates([media, official])
    merged = merge_duplicate_group(groups[0])

    assert merged.primary.candidate_id == "official"
    assert merged.duplicates[0].status is CandidateStatus.MERGED
    assert merged.duplicates[0].merged_into == "official"
    assert {ref.source_item_id for ref in merged.primary.evidence_refs} == {
        "media",
        "official",
    }


def test_two_independent_reporting_sources_upgrade_event_to_corroborated() -> None:
    first = _candidate(
        "first-report",
        url="https://one.example/story",
        event_key="product:reported-feature",
        authority="secondary",
    )
    second = _candidate(
        "second-report",
        url="https://two.example/story",
        event_key="product:reported-feature",
        authority="secondary",
    )

    merged = merge_duplicate_group([first, second])

    assert merged.primary.evidence_status is EvidenceStatus.CORROBORATED
    assert merged.primary.intelligence is None


def test_two_real_updates_to_same_product_are_not_merged() -> None:
    first = _candidate(
        "first",
        url="https://example.com/changelog",
        event_key="product:feature",
        event_version=1,
    )
    second = _candidate(
        "second",
        url="https://example.com/changelog?version=2",
        event_key="product:feature",
        event_version=2,
    )

    assert len(group_duplicate_candidates([first, second])) == 2


def test_same_workflow_topic_controls_diversity_but_does_not_hard_merge() -> None:
    tool_a = _candidate(
        "tool-a",
        url="https://example.com/a",
        event_key="tool-a:tutorial",
        topic_key="workflow:slides-from-doc",
    )
    tool_b = _candidate(
        "tool-b",
        url="https://example.com/b",
        event_key="tool-b:tutorial",
        topic_key="workflow:slides-from-doc",
    )

    assert len(group_duplicate_candidates([tool_a, tool_b])) == 2

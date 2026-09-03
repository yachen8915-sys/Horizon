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
    RadarRunMode,
    SourceType,
)
from src.processing.delivery_selection import DeliverySelector


MORNING = datetime(2026, 9, 3, 1, 0, tzinfo=timezone.utc)
AFTERNOON = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _candidate(
    candidate_id: str,
    *,
    updated_at: datetime,
    event_key: str | None = None,
    event_version: int = 1,
    event_version_at: datetime | None = None,
) -> CandidateRecord:
    analysis = IntelligenceAnalysis(
        primary_lane=DecisionLane.PRODUCT_CAPABILITY,
        decision_summary="worth testing",
        content_summary="new capability",
        evidence_status=EvidenceStatus.CONFIRMED,
        score=CandidateScore(total=8),
    )
    return CandidateRecord(
        candidate_id=candidate_id,
        item=ContentItem(
            id=candidate_id,
            source_type=SourceType.RSS,
            title=candidate_id,
            url=f"https://example.com/{candidate_id}",
            published_at=updated_at,
        ),
        status=CandidateStatus.SELECTED,
        discovered_at=updated_at,
        updated_at=updated_at,
        event_key=event_key or f"event:{candidate_id}",
        event_version=event_version,
        event_version_at=event_version_at,
        intelligence=analysis,
        evidence_status=EvidenceStatus.CONFIRMED,
        rule_version="v1",
    )


def _delivered(candidate: CandidateRecord, at: datetime) -> DeliveryRecord:
    return DeliveryRecord(
        delivery_id=f"delivery:{candidate.candidate_id}",
        run_id="morning-run",
        run_mode=RadarRunMode.MORNING,
        delivered_at=at,
        candidate_id=candidate.candidate_id,
        event_key=candidate.event_key,
        event_version=candidate.event_version,
        display_tier="selected",
        content_fingerprint="fingerprint",
    )


def test_morning_delivery_contains_all_selected_candidates() -> None:
    candidates = [
        _candidate("one", updated_at=MORNING - timedelta(hours=1)),
        _candidate("two", updated_at=MORNING - timedelta(minutes=30)),
    ]

    result = DeliverySelector().select(
        candidates,
        run_id="morning-run",
        run_mode=RadarRunMode.MORNING,
        now=MORNING,
    )

    assert result.status == "send"
    assert [row.candidate_id for row in result.candidates] == ["one", "two"]
    assert len(result.records) == 2


def test_capacity_held_candidates_are_delivered_as_more() -> None:
    selected = _candidate("selected", updated_at=MORNING)
    more = _candidate("more", updated_at=MORNING).model_copy(
        update={"status": CandidateStatus.HELD}
    )

    result = DeliverySelector().select(
        [selected],
        more_candidates=[more],
        run_id="morning-run",
        run_mode=RadarRunMode.MORNING,
        now=MORNING,
    )

    assert [row.candidate_id for row in result.more_candidates] == ["more"]
    assert [row.display_tier for row in result.records] == ["selected", "more"]


def test_afternoon_only_sends_candidates_new_since_morning() -> None:
    old = _candidate("old", updated_at=MORNING - timedelta(hours=1))
    new = _candidate("new", updated_at=MORNING + timedelta(hours=2))
    morning_record = _delivered(old, MORNING)

    result = DeliverySelector().select(
        [old, new],
        deliveries=[morning_record],
        run_id="afternoon-run",
        run_mode=RadarRunMode.AFTERNOON,
        now=AFTERNOON,
    )

    assert [row.candidate_id for row in result.candidates] == ["new"]


def test_afternoon_does_not_send_old_held_item_just_because_it_was_reranked() -> None:
    old = _candidate(
        "old-held",
        updated_at=AFTERNOON - timedelta(minutes=5),
        event_version_at=MORNING - timedelta(hours=1),
    )

    result = DeliverySelector().select(
        [old],
        deliveries=[
            DeliveryRecord(
                delivery_id="morning-marker",
                run_id="morning-run",
                run_mode=RadarRunMode.MORNING,
                delivered_at=MORNING,
                candidate_id="other",
                event_key="event:other",
                event_version=1,
                display_tier="selected",
                content_fingerprint="other",
            )
        ],
        run_id="afternoon-run",
        run_mode=RadarRunMode.AFTERNOON,
        now=AFTERNOON,
    )

    assert result.status == "no_qualified_updates"


def test_afternoon_uses_scheduled_morning_cutoff_when_morning_sent_nothing() -> None:
    local_afternoon_utc = datetime(2026, 9, 3, 8, tzinfo=timezone.utc)
    old = _candidate(
        "old-before-nine",
        updated_at=local_afternoon_utc,
        event_version_at=datetime(2026, 9, 3, 0, tzinfo=timezone.utc),
    )

    result = DeliverySelector().select(
        [old],
        deliveries=[],
        run_id="afternoon-run",
        run_mode=RadarRunMode.AFTERNOON,
        now=local_afternoon_utc,
    )

    assert result.status == "no_qualified_updates"


def test_afternoon_does_not_repeat_morning_event_version() -> None:
    morning_candidate = _candidate("morning", updated_at=MORNING)
    duplicate = _candidate(
        "duplicate",
        updated_at=MORNING + timedelta(hours=2),
        event_key=morning_candidate.event_key,
        event_version=1,
    )

    result = DeliverySelector().select(
        [duplicate],
        deliveries=[_delivered(morning_candidate, MORNING)],
        run_id="afternoon-run",
        run_mode=RadarRunMode.AFTERNOON,
        now=AFTERNOON,
    )

    assert result.status == "no_qualified_updates"
    assert result.candidates == []


def test_material_event_version_can_be_sent_again_in_afternoon() -> None:
    original = _candidate(
        "original", updated_at=MORNING, event_key="event:product", event_version=1
    )
    update = _candidate(
        "update",
        updated_at=MORNING + timedelta(hours=3),
        event_key="event:product",
        event_version=2,
    )

    result = DeliverySelector().select(
        [update],
        deliveries=[_delivered(original, MORNING)],
        run_id="afternoon-run",
        run_mode=RadarRunMode.AFTERNOON,
        now=AFTERNOON,
    )

    assert [row.candidate_id for row in result.candidates] == ["update"]


def test_collection_failure_is_not_described_as_no_updates() -> None:
    result = DeliverySelector().select(
        [],
        run_id="failed-run",
        run_mode=RadarRunMode.MORNING,
        now=MORNING,
        pipeline_status="collection_degraded",
    )

    assert result.status == "collection_degraded"

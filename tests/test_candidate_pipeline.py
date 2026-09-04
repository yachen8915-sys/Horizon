from datetime import datetime, timezone
from types import SimpleNamespace

from src.models import (
    CandidateScore,
    CandidateStatus,
    ClassificationResult,
    ContentAnalysis,
    ContentItem,
    DecisionLane,
    EvidenceStatus,
    IntelligenceAnalysis,
    IntelligenceRadarConfig,
    ProcessingResult,
    ReasonCode,
    SourceType,
)
from src.processing.candidate_pipeline import CandidateBuilder


NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


def _item(with_intelligence: bool = True) -> ContentItem:
    intelligence = (
        IntelligenceAnalysis(
            primary_lane=DecisionLane.PRODUCT_CAPABILITY,
            content_kind="product_update",
            direct_impacts=["new user workflow"],
            decision_summary="worth testing",
            content_summary="new capability",
            evidence_status=EvidenceStatus.CONFIRMED,
            score=CandidateScore(
                decision_impact=8,
                audience_fit=8,
                novelty=8,
                evidence_quality=9,
                demonstrability=8,
                propagation_quality=4,
                freshness=9,
                differentiation=8,
                total=8,
            ),
        )
        if with_intelligence
        else None
    )
    return ContentItem(
        id="rss:official:update",
        source_type=SourceType.RSS,
        title="Update",
        url="https://example.com/update?utm_source=rss",
        published_at=NOW,
        metadata={"category": "official-ai-product"},
        processing=ProcessingResult(
            classification=ClassificationResult(
                profile="pangmen-topic-radar", method="source_override"
            ),
            analysis=ContentAnalysis(
                score=8,
                reason="useful",
                summary="update",
                event_key="product:feature",
                topic_cluster="product_capability",
                use_case="workflow",
                intelligence=intelligence,
            ),
        ),
    )


def test_builder_turns_valid_analysis_into_eligible_candidate() -> None:
    candidate = CandidateBuilder("v1").from_analyzed_item(_item())

    assert candidate.status is CandidateStatus.ELIGIBLE
    assert candidate.canonical_url == "https://example.com/update"
    assert candidate.event_key == "product:feature"
    assert candidate.reason_codes == [ReasonCode.PASSED_HARD_GATES]
    assert candidate.evidence_refs[0].authority == "official"
    assert [step.to_status for step in candidate.status_history] == [
        CandidateStatus.ELIGIBLE
    ]


def test_candidate_keeps_a_bounded_body_excerpt_as_evidence() -> None:
    item = _item()
    item.content = "  Official   release details.  " + ("x" * 600)

    candidate = CandidateBuilder("v1").from_analyzed_item(item)

    assert candidate.evidence_refs[0].excerpt is not None
    assert candidate.evidence_refs[0].excerpt.startswith(
        "Official release details."
    )
    assert len(candidate.evidence_refs[0].excerpt) == 500


def test_missing_intelligence_is_processing_error_not_quality_rejection() -> None:
    candidate = CandidateBuilder("v1").from_analyzed_item(
        _item(with_intelligence=False)
    )

    assert candidate.status is CandidateStatus.PROCESSING_ERROR
    assert candidate.reason_codes == [ReasonCode.ANALYSIS_FAILED]


def test_social_observation_can_be_recorded_before_ai() -> None:
    item = _item(with_intelligence=False)
    item.processing = None
    item.metadata["engagement_gate_status"] = "observing"
    item.metadata["engagement_gate_reason"] = "insufficient_maturity"

    candidate = CandidateBuilder("v1").from_preanalysis_item(item)

    assert candidate.status is CandidateStatus.OBSERVING
    assert candidate.reason_codes == [ReasonCode.IMMATURE]


def test_pending_youtube_metrics_use_incomplete_engagement_reason() -> None:
    item = _item(with_intelligence=False)
    item.processing = None
    item.metadata["engagement_gate_status"] = "observing"
    item.metadata["engagement_gate_reason"] = "engagement_pending"

    candidate = CandidateBuilder("v1").from_preanalysis_item(item)

    assert candidate.status is CandidateStatus.OBSERVING
    assert candidate.reason_codes == [ReasonCode.INCOMPLETE_ENGAGEMENT]


def test_observation_history_is_carried_across_candidate_snapshots(
    tmp_path, monkeypatch
) -> None:
    from src.orchestrator import HorizonOrchestrator

    monkeypatch.chdir(tmp_path)
    orchestrator = object.__new__(HorizonOrchestrator)
    orchestrator.config = SimpleNamespace(
        intelligence=IntelligenceRadarConfig(
            enabled=True,
            candidate_store_file="data/candidates.jsonl",
        )
    )
    first = _item(with_intelligence=False)
    first.processing = None
    first.metadata.update(
        {
            "engagement_gate_status": "observing",
            "engagement_gate_reason": "insufficient_maturity",
            "first_observed_at": "2026-09-03T08:00:00+00:00",
            "last_observed_at": "2026-09-03T08:00:00+00:00",
            "observation_deadline": "2026-09-03T14:00:00+00:00",
            "engagement": {"views": 10},
        }
    )
    orchestrator.last_social_quality_observing = [first]
    orchestrator.last_social_quality_rejected = []
    orchestrator._select_intelligence_candidates([])

    second = first.model_copy(deep=True)
    second.metadata["last_observed_at"] = "2026-09-03T09:00:00+00:00"
    second.metadata["engagement"] = {"views": 50}
    orchestrator.last_social_quality_observing = [second]
    orchestrator._select_intelligence_candidates([])

    latest = orchestrator.last_intelligence_candidates[0]
    assert latest.item.metadata["first_observed_at"] == (
        "2026-09-03T08:00:00+00:00"
    )
    assert latest.item.metadata["observation_count"] == 2
    assert [
        row["engagement"]["views"]
        for row in latest.item.metadata["engagement_snapshots"]
    ] == [10, 50]


def test_preanalysis_only_run_archives_observing_candidates(
    tmp_path, monkeypatch
) -> None:
    from src.orchestrator import HorizonOrchestrator

    monkeypatch.chdir(tmp_path)
    item = _item(with_intelligence=False)
    item.processing = None
    item.metadata.update(
        {
            "engagement_gate_status": "observing",
            "engagement_gate_reason": "insufficient_maturity",
            "engagement": {"views": 10},
        }
    )
    orchestrator = object.__new__(HorizonOrchestrator)
    orchestrator.config = SimpleNamespace(
        intelligence=IntelligenceRadarConfig(
            enabled=True,
            candidate_store_file="data/candidates/candidates.jsonl",
        )
    )
    orchestrator.last_fetch_report = None
    orchestrator.last_social_quality_observing = [item]
    orchestrator.last_social_quality_rejected = []

    paths = orchestrator._archive_preanalysis_only()

    assert paths["jsonl"].exists()
    assert paths["html"].exists()
    assert orchestrator.last_intelligence_candidates[0].status is (
        CandidateStatus.OBSERVING
    )


def test_observing_candidate_keeps_lifecycle_when_it_later_becomes_selected(
    tmp_path, monkeypatch
) -> None:
    from src.orchestrator import HorizonOrchestrator

    monkeypatch.chdir(tmp_path)
    orchestrator = object.__new__(HorizonOrchestrator)
    orchestrator.config = SimpleNamespace(
        intelligence=IntelligenceRadarConfig(
            enabled=True,
            candidate_store_file="data/candidates.jsonl",
        )
    )
    observing = _item(with_intelligence=False)
    observing.processing = None
    observing.metadata["engagement_gate_status"] = "observing"
    orchestrator.last_social_quality_observing = [observing]
    orchestrator.last_social_quality_rejected = []
    orchestrator._select_intelligence_candidates([])

    eligible = _item()
    orchestrator.last_social_quality_observing = []
    orchestrator._select_intelligence_candidates([eligible])

    selected = orchestrator.last_intelligence_candidates[0]
    assert selected.status is CandidateStatus.SELECTED
    assert [step.to_status for step in selected.status_history] == [
        CandidateStatus.OBSERVING,
        CandidateStatus.ELIGIBLE,
        CandidateStatus.SELECTED,
    ]


def test_aggregator_cannot_self_promote_unverified_claim_to_confirmed() -> None:
    payload = _item().model_dump(mode="json")
    payload.update(
        {
            "id": "aihot:post:1",
            "source_type": SourceType.AIHOT.value,
            "url": "https://aihot.example/posts/1",
            "metadata": {},
        }
    )
    item = ContentItem.model_validate(payload)

    candidate = CandidateBuilder("v1").from_analyzed_item(item)

    assert candidate.evidence_status is EvidenceStatus.UNVERIFIED
    assert candidate.intelligence is not None
    assert candidate.intelligence.evidence_status is EvidenceStatus.UNVERIFIED
    assert candidate.status is CandidateStatus.REJECTED
    assert candidate.reason_codes == [ReasonCode.EVIDENCE_INSUFFICIENT]


def test_official_source_is_programmatically_confirmed() -> None:
    item = _item()
    assert item.processing is not None
    assert item.processing.analysis is not None
    assert item.processing.analysis.intelligence is not None
    item.processing.analysis.intelligence.evidence_status = EvidenceStatus.UNVERIFIED

    candidate = CandidateBuilder("v1").from_analyzed_item(item)

    assert candidate.evidence_status is EvidenceStatus.CONFIRMED
    assert candidate.status is CandidateStatus.ELIGIBLE


def test_orchestrator_intelligence_selection_persists_every_outcome(
    tmp_path, monkeypatch
) -> None:
    from src.orchestrator import HorizonOrchestrator
    from src.storage.candidate_store import CandidateStore

    selected = _item()
    failed_payload = _item(with_intelligence=False).model_dump(mode="json")
    failed_payload.update(
        {"id": "rss:official:failed", "url": "https://example.com/failed"}
    )
    failed = ContentItem.model_validate(failed_payload)
    monkeypatch.chdir(tmp_path)
    orchestrator = object.__new__(HorizonOrchestrator)
    orchestrator.config = SimpleNamespace(
        intelligence=IntelligenceRadarConfig(
            enabled=True,
            candidate_store_file="data/candidates.jsonl",
        )
    )
    orchestrator.last_social_quality_observing = []
    orchestrator.last_social_quality_rejected = []

    result = orchestrator._select_intelligence_candidates([selected, failed])

    assert [item.id for item in result.items] == ["rss:official:update"]
    persisted = CandidateStore(tmp_path / "data/candidates.jsonl").all_latest()
    assert {candidate.status for candidate in persisted} == {
        CandidateStatus.SELECTED,
        CandidateStatus.PROCESSING_ERROR,
    }


def test_orchestrator_selection_uses_cross_run_delivery_cooldown(
    tmp_path, monkeypatch
) -> None:
    from src.models import DeliveryRecord, RadarRunMode
    from src.orchestrator import HorizonOrchestrator
    from src.storage.delivery_store import DeliveryStore

    monkeypatch.chdir(tmp_path)
    selected = _item()
    delivery = DeliveryRecord(
        delivery_id="previous",
        run_id="previous-run",
        run_mode=RadarRunMode.MORNING,
        delivered_at=NOW,
        candidate_id="previous-candidate",
        event_key="product:feature",
        event_version=1,
        display_tier="selected",
        content_fingerprint="previous",
    )
    DeliveryStore(tmp_path / "data/deliveries.jsonl").append_many([delivery])
    orchestrator = object.__new__(HorizonOrchestrator)
    orchestrator.config = SimpleNamespace(
        intelligence=IntelligenceRadarConfig(
            enabled=True,
            candidate_store_file="data/candidates.jsonl",
            delivery_store_file="data/deliveries.jsonl",
        )
    )
    orchestrator.last_social_quality_observing = []
    orchestrator.last_social_quality_rejected = []

    result = orchestrator._select_intelligence_candidates([selected])

    assert result.items == []
    assert orchestrator.last_intelligence_candidates[0].status is CandidateStatus.HELD
    assert ReasonCode.DUPLICATE in orchestrator.last_intelligence_candidates[0].reason_codes


def test_new_angle_on_old_event_becomes_new_event_version(
    tmp_path, monkeypatch
) -> None:
    from src.models import DeliveryRecord, RadarRunMode
    from src.orchestrator import HorizonOrchestrator
    from src.storage.candidate_store import CandidateStore
    from src.storage.delivery_store import DeliveryStore

    monkeypatch.chdir(tmp_path)
    original = CandidateBuilder("v1").from_analyzed_item(_item())
    original = original.model_copy(
        update={"status": CandidateStatus.SELECTED, "event_version": 1}
    )
    CandidateStore(tmp_path / "data/candidates.jsonl").record_snapshot(original)
    DeliveryStore(tmp_path / "data/deliveries.jsonl").append_many(
        [
            DeliveryRecord(
                delivery_id="previous",
                run_id="previous-run",
                run_mode=RadarRunMode.MORNING,
                delivered_at=NOW,
                candidate_id=original.candidate_id,
                event_key="product:feature",
                event_version=1,
                display_tier="selected",
                content_fingerprint="previous",
            )
        ]
    )
    payload = _item().model_dump(mode="json")
    payload.update(
        {"id": "rss:official:new-angle", "url": "https://example.com/new-angle"}
    )
    updated = ContentItem.model_validate(payload)
    assert updated.processing is not None
    assert updated.processing.analysis is not None
    assert updated.processing.analysis.intelligence is not None
    updated.processing.analysis.intelligence.novelty_basis = "new_angle"
    orchestrator = object.__new__(HorizonOrchestrator)
    orchestrator.config = SimpleNamespace(
        intelligence=IntelligenceRadarConfig(
            enabled=True,
            candidate_store_file="data/candidates.jsonl",
            delivery_store_file="data/deliveries.jsonl",
        )
    )
    orchestrator.last_social_quality_observing = []
    orchestrator.last_social_quality_rejected = []

    result = orchestrator._select_intelligence_candidates([updated])

    assert [item.id for item in result.items] == ["rss:official:new-angle"]
    assert orchestrator.last_intelligence_candidates[0].event_version == 2


def test_same_source_item_can_increment_when_its_body_materially_changes() -> None:
    from src.orchestrator import HorizonOrchestrator

    original_item = _item()
    original_item.content = "Original release information"
    original = CandidateBuilder("v1").from_analyzed_item(original_item)
    changed_item = _item()
    changed_item.content = "New test data changes the conclusion"
    assert changed_item.processing is not None
    assert changed_item.processing.analysis is not None
    assert changed_item.processing.analysis.intelligence is not None
    changed_item.processing.analysis.intelligence.novelty_basis = "new_data"
    changed = CandidateBuilder("v1").from_analyzed_item(changed_item)

    [versioned] = HorizonOrchestrator._assign_intelligence_event_versions(
        [changed],
        [original],
    )

    assert versioned.event_version == 2


def test_same_source_item_does_not_increment_when_body_is_unchanged() -> None:
    from src.orchestrator import HorizonOrchestrator

    original_item = _item()
    original_item.content = "Same body"
    original = CandidateBuilder("v1").from_analyzed_item(original_item)
    repeated_item = _item()
    repeated_item.content = "Same body"
    assert repeated_item.processing is not None
    assert repeated_item.processing.analysis is not None
    assert repeated_item.processing.analysis.intelligence is not None
    repeated_item.processing.analysis.intelligence.novelty_basis = "new_release"
    repeated = CandidateBuilder("v1").from_analyzed_item(repeated_item)

    [versioned] = HorizonOrchestrator._assign_intelligence_event_versions(
        [repeated],
        [original],
    )

    assert versioned.event_version == 1

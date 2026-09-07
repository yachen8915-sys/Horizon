from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.models import (
    CandidateRecord,
    CandidateScore,
    CandidateStatus,
    Config,
    ContentAnalysis,
    ContentItem,
    DecisionLane,
    DeliveryRecord,
    EvidenceStatus,
    IntelligenceAnalysis,
    RadarRunMode,
    ReasonCode,
    SourceType,
)


NOW = datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc)


def test_decision_lane_includes_ai_industry_society() -> None:
    assert DecisionLane.AI_INDUSTRY_SOCIETY.value == "ai_industry_society"


def test_content_analysis_accepts_operations_focus() -> None:
    analysis = ContentAnalysis(
        score=8,
        operations_score=8,
        content_opportunity_score=5,
        operations_focus="workplace_youth",
        reason="useful",
        summary="summary",
    )

    assert analysis.operations_focus == "workplace_youth"


def _minimal_config() -> dict:
    return {
        "ai": {"provider": "openai", "model": "test", "api_key_env": "KEY"},
        "sources": {},
    }


def _item() -> ContentItem:
    return ContentItem(
        id="rss:official:item-1",
        source_type=SourceType.RSS,
        title="New capability",
        url="https://example.com/update",
        published_at=NOW,
    )


def test_old_config_loads_with_safe_intelligence_defaults() -> None:
    config = Config.model_validate(_minimal_config())

    assert config.intelligence.enabled is False
    assert config.intelligence.delivery_enabled is False
    assert config.intelligence.run_mode is RadarRunMode.SHADOW
    assert config.intelligence.decision_lanes == list(DecisionLane)
    assert config.delivery_allowed is True


def test_redesigned_pipeline_blocks_delivery_until_explicitly_enabled() -> None:
    payload = _minimal_config()
    payload["intelligence"] = {
        "enabled": True,
        "delivery_enabled": False,
        "run_mode": "shadow",
    }

    config = Config.model_validate(payload)

    assert config.delivery_allowed is False


def test_delivery_cannot_be_enabled_while_run_mode_is_shadow() -> None:
    payload = _minimal_config()
    payload["intelligence"] = {
        "enabled": True,
        "delivery_enabled": True,
        "run_mode": "shadow",
    }

    with pytest.raises(ValidationError):
        Config.model_validate(payload)


@pytest.mark.parametrize(
    "unsafe_path",
    ["C:/temp/candidates.jsonl", "../candidates.jsonl", "/tmp/candidates.jsonl"],
)
def test_intelligence_ledgers_reject_absolute_or_parent_paths(unsafe_path: str) -> None:
    payload = _minimal_config()
    payload["intelligence"] = {"candidate_store_file": unsafe_path}

    with pytest.raises(ValidationError):
        Config.model_validate(payload)


def test_nested_intelligence_analysis_keeps_legacy_analysis_contract() -> None:
    intelligence = IntelligenceAnalysis(
        primary_lane=DecisionLane.PRODUCT_CAPABILITY,
        decision_summary="Decide whether this capability is worth testing.",
        content_summary="The product added a new capability.",
        evidence_status=EvidenceStatus.CONFIRMED,
        score=CandidateScore(
            decision_impact=9,
            audience_fit=8,
            novelty=8,
            evidence_quality=10,
            demonstrability=9,
            propagation_quality=5,
            freshness=9,
            differentiation=8,
            total=8.5,
        ),
    )
    analysis = ContentAnalysis(
        reason="useful",
        summary="summary",
        intelligence=intelligence,
    )

    assert analysis.intelligence is intelligence
    assert analysis.score is None


def test_candidate_record_has_versioned_identity_and_status() -> None:
    candidate = CandidateRecord(
        candidate_id="candidate-1",
        item=_item(),
        status=CandidateStatus.ELIGIBLE,
        discovered_at=NOW,
        updated_at=NOW,
        canonical_url="https://example.com/update",
        event_key="product:new-capability",
        event_version=2,
        editorial_topic_key="product:capability-test",
        evidence_status=EvidenceStatus.CONFIRMED,
        reason_codes=[ReasonCode.PASSED_HARD_GATES],
        rule_version="2026-09-03-v1",
    )

    restored = CandidateRecord.model_validate_json(candidate.model_dump_json())

    assert restored.status is CandidateStatus.ELIGIBLE
    assert restored.event_version == 2
    assert restored.item.id == "rss:official:item-1"


def test_candidate_score_rejects_out_of_range_dimension() -> None:
    with pytest.raises(ValidationError):
        CandidateScore(decision_impact=11)


def test_delivery_record_tracks_event_version_and_display_tier() -> None:
    record = DeliveryRecord(
        delivery_id="delivery-1",
        run_id="morning-1",
        run_mode=RadarRunMode.MORNING,
        delivered_at=NOW,
        candidate_id="candidate-1",
        event_key="product:new-capability",
        event_version=2,
        display_tier="selected",
        content_fingerprint="abc123",
    )

    assert record.run_mode is RadarRunMode.MORNING
    assert record.display_tier == "selected"

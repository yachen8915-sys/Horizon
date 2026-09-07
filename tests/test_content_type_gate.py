from datetime import datetime, timezone

import pytest
from pydantic import HttpUrl

from src.models import (
    CandidateScore, CandidateStatus, ClassificationResult, ContentAnalysis,
    ContentItem, DecisionLane, EvidenceStatus, ProcessingResult, SourceType,
)
from src.processing.candidate_pipeline import CandidateBuilder
from src.processing.intelligence_analysis import IntelligenceDraft, build_intelligence_analysis


def make_gate_item(
    *, lane=DecisionLane.HOT_CONTENT, operations=7, content=6,
    metadata=None, focus=None, title="普通热点", value=6,
    evidence=EvidenceStatus.UNVERIFIED, profile="pangmen-platform-trend-radar",
):
    draft = IntelligenceDraft(
        primary_lane=lane,
        content_kind="industry_social" if lane is DecisionLane.AI_INDUSTRY_SOCIETY else "hot_content",
        decision_summary="值得关注", content_summary="事件信息", evidence_status=evidence,
        dimensions=CandidateScore(**{
            key: value for key in CandidateScore.model_fields
        }),
    )
    item = ContentItem(
        id="trend:first", source_type=SourceType.PLATFORM_TRENDS,
        title=title, url="https://example.com/topic/1",
        published_at=datetime.now(timezone.utc),
        metadata={"provider": "DailyHotAPI", "platform": "bilibili", **(metadata or {})},
        processing=ProcessingResult(
            classification=ClassificationResult(profile=profile, method="source_override"),
            analysis=ContentAnalysis(
                score=operations, operations_score=operations,
                content_opportunity_score=content, operations_focus=focus,
                relevance_score=value, reason="test", summary=title,
                event_key="exact:event", intelligence=build_intelligence_analysis(draft),
            ),
        ),
    )
    return item, draft


def gate(item, draft, minimum_score=6.3):
    # Import within the test so missing implementation cannot hide pipeline REDs.
    from src.processing.content_type_gate import assess_content_type_gate
    return assess_content_type_gate(item, draft, minimum_score=minimum_score)


@pytest.mark.parametrize("operations,content,metadata,focus,pool,reason", [
    (7, 7, {}, None, "leverage", "passed_hard_gates"),
    (8, 3, {}, None, "watch", "passed_hard_gates"),
    (7, 6, {"platform": "weibo", "rank": 10}, None, "watch", "passed_hard_gates"),
    (7, 6, {"providers": ["DailyHotAPI", "ALAPI"]}, None, "watch", "passed_hard_gates"),
    (7, 6, {"platforms": ["bilibili", "weibo"]}, None, "watch", "passed_hard_gates"),
    (7, 6, {}, "ai_tech", "watch", "passed_hard_gates"),
    (7, 6, {}, "workplace_youth", "watch", "passed_hard_gates"),
    (7, 6, {}, "visual_content", "watch", "passed_hard_gates"),
    (7, 6, {}, "general", None, "insufficient_hotspot_signal"),
    (7, 6, {"platform": "weibo", "rank": 11}, None, None, "insufficient_hotspot_signal"),
    (7, 6, {"platform": "bilibili", "rank": 1}, None, None, "insufficient_hotspot_signal"),
    (7, 6, {"providers": ["ALAPI", "alapi"]}, None, None, "insufficient_hotspot_signal"),
    (7, 6, {"platforms": ["weibo", "weibo"]}, None, None, "insufficient_hotspot_signal"),
    (6.9, 9, {"rank": 1, "platform": "weibo"}, "ai_tech", None, "low_operations_value"),
])
def test_platform_gate_matrix(operations, content, metadata, focus, pool, reason):
    item, draft = make_gate_item(operations=operations, content=content, metadata=metadata, focus=focus)
    before = item.model_dump()
    result = gate(item, draft)
    assert result.accepted is (pool is not None)
    assert result.trend_pool == pool
    assert result.reason.value == reason
    assert item.model_dump() == before


def test_military_training_talent_show_is_a_watch_example():
    item, draft = make_gate_item(title="军训才艺大赏", focus="workplace_youth")
    assert gate(item, draft).trend_pool == "watch"


def test_school_uniform_response_is_a_watch_example():
    item, draft = make_gate_item(title="教育部回应中小学是否须买校服", metadata={"platform": "weibo", "rank": 5})
    assert gate(item, draft).trend_pool == "watch"


@pytest.mark.parametrize("title", ["政治敏感事件", "严重事故通报", "死亡事件", "暴力事件"])
@pytest.mark.parametrize("lane", list(DecisionLane))
def test_brand_safety_precedes_admission_for_every_lane(title, lane):
    item, draft = make_gate_item(title=title, lane=lane, operations=9, content=9, value=9, evidence=EvidenceStatus.CONFIRMED)
    assert gate(item, draft).reason.value == "brand_safety"
    assert not gate(item, draft).accepted


@pytest.mark.parametrize("value,accepted", [(6, True), (5.9, False)])
def test_industry_threshold_and_pending_verification(value, accepted):
    item, draft = make_gate_item(lane=DecisionLane.AI_INDUSTRY_SOCIETY, value=value)
    result = gate(item, draft, minimum_score=8)
    assert result.accepted is accepted
    assert result.pending_verification is accepted
    assert result.trend_pool is None


def test_industry_lane_wins_over_platform_profile_and_high_operations():
    item, draft = make_gate_item(lane=DecisionLane.AI_INDUSTRY_SOCIETY, value=5, operations=9, content=9)
    assert not gate(item, draft).accepted


def test_industry_checks_weighted_dimensions_not_claimed_total():
    item, draft = make_gate_item(lane=DecisionLane.AI_INDUSTRY_SOCIETY, value=6)
    draft.dimensions.evidence_quality = 0
    draft.dimensions.total = 10
    assert not gate(item, draft).accepted


def test_industry_relevance_below_six_rejected_despite_high_weighted_score():
    item, draft = make_gate_item(lane=DecisionLane.AI_INDUSTRY_SOCIETY, value=9)
    item.processing.analysis.relevance_score = 5.9
    draft.dimensions.audience_fit = 5.9
    assert gate(item, draft).reason.value == "low_relevance"


def test_disputed_industry_is_not_pending_verification():
    item, draft = make_gate_item(lane=DecisionLane.AI_INDUSTRY_SOCIETY, evidence=EvidenceStatus.DISPUTED)
    assert not gate(item, draft).accepted


@pytest.mark.parametrize("lane", [DecisionLane.PRODUCT_CAPABILITY, DecisionLane.TECHNICAL_FRONTIER, DecisionLane.PLATFORM_AI_CHANGE])
@pytest.mark.parametrize("evidence", [EvidenceStatus.CONFIRMED, EvidenceStatus.UNVERIFIED])
def test_strict_lanes_keep_existing_hard_gate_results(lane, evidence):
    from src.processing.intelligence_analysis import assess_hard_gates
    item, draft = make_gate_item(lane=lane, value=8, evidence=evidence)
    assert gate(item, draft).accepted is assess_hard_gates(draft).accepted


def test_provider_name_does_not_route_nontrend_profile_to_platform_gate():
    item, draft = make_gate_item(profile="pangmen-topic-radar", operations=9, content=9, value=3)
    assert not gate(item, draft).accepted


def test_builder_defers_gate_until_after_exact_merge():
    item, _ = make_gate_item()
    candidate = CandidateBuilder("v1").from_analyzed_item(item)
    assert candidate.status is CandidateStatus.ENRICHED


def test_builder_gate_is_immutable_and_keeps_pending_label():
    item, _ = make_gate_item(lane=DecisionLane.AI_INDUSTRY_SOCIETY)
    builder = CandidateBuilder("v1")
    enriched = builder.from_analyzed_item(item)
    accepted = builder.apply_content_type_gate(enriched)
    assert enriched.status is CandidateStatus.ENRICHED
    assert "pending_verification" not in enriched.item.metadata
    assert accepted.status is CandidateStatus.ELIGIBLE
    assert accepted.item.metadata["pending_verification"] is True
    assert accepted.evidence_status is EvidenceStatus.UNVERIFIED
    assert accepted.status_history[-1].from_status is CandidateStatus.ENRICHED


def test_orchestrator_merges_provider_signal_before_gate(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src.models import IntelligenceRadarConfig
    from src.orchestrator import HorizonOrchestrator
    monkeypatch.chdir(tmp_path)
    first, _ = make_gate_item()
    second = first.model_copy(deep=True)
    second.id = "trend:second"
    second.url = HttpUrl("https://example.com/topic/2")
    second.metadata["provider"] = "ALAPI"
    orchestrator = object.__new__(HorizonOrchestrator)
    orchestrator.config = SimpleNamespace(intelligence=IntelligenceRadarConfig(enabled=True))
    orchestrator.last_social_quality_observing = []
    orchestrator.last_social_quality_rejected = []
    assert orchestrator._select_intelligence_candidates([first]).items == []
    result = orchestrator._select_intelligence_candidates([first, second])
    assert len(result.items) == 1
    assert result.items[0].metadata["trend_pool"] == "watch"
    assert {value.casefold() for value in result.items[0].metadata["providers"]} == {"dailyhotapi", "alapi"}
    assert len(orchestrator.last_intelligence_candidates) == 2
    assert any(c.status is CandidateStatus.MERGED for c in orchestrator.last_intelligence_candidates)

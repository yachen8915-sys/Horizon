from datetime import datetime, timezone
import json
from types import SimpleNamespace

from src.diagnostics.intelligence_report import build_intelligence_report
from src.models import (
    CandidateRecord,
    CandidateStatus,
    ContentItem,
    DecisionLane,
    IntelligenceAnalysis,
    CandidateScore,
    EvidenceStatus,
    IntelligenceRadarConfig,
    ReasonCode,
    SourceType,
)
from src.orchestrator import HorizonOrchestrator


NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


def _candidate(candidate_id: str, status: CandidateStatus, lane: DecisionLane, author: str):
    return CandidateRecord(
        candidate_id=candidate_id,
        item=ContentItem(
            id=candidate_id,
            source_type=SourceType.RSS,
            title=candidate_id,
            url=f"https://example.com/{candidate_id}",
            author=author,
            published_at=NOW,
            metadata={"source_id": "official-rss", "content_platform": "web"},
        ),
        status=status,
        discovered_at=NOW,
        updated_at=NOW,
        reason_codes=[
            ReasonCode.SELECTED
            if status is CandidateStatus.SELECTED
            else (
                ReasonCode.HELD_BY_CAPACITY
                if status is CandidateStatus.HELD
                else ReasonCode.LOW_QUALITY
            )
        ],
        intelligence=IntelligenceAnalysis(
            primary_lane=lane,
            decision_summary="why",
            content_summary="what",
            evidence_status=EvidenceStatus.CONFIRMED,
            score=CandidateScore(total=8),
        ),
        evidence_status=EvidenceStatus.CONFIRMED,
        rule_version="v1",
    )


def test_report_connects_source_health_funnel_concentration_and_cost() -> None:
    candidates = [
        _candidate("one", CandidateStatus.SELECTED, DecisionLane.PRODUCT_CAPABILITY, "a"),
        _candidate("two", CandidateStatus.HELD, DecisionLane.HOT_CONTENT, "a"),
        _candidate(
            "three",
            CandidateStatus.REJECTED,
            DecisionLane.TECHNICAL_FRONTIER,
            "b",
        ).model_copy(update={"merged_into": "one"}),
    ]
    fetch_report = {
        "status": "partial_failure",
        "sources": [
            {"source": "RSS", "status": "success", "item_count": 10},
            {
                "source": "YouTube Data",
                "status": "failure",
                "item_count": 0,
                "source_health": [
                    {
                        "source_id": "youtube:search:ai",
                        "status": "failed",
                        "reason_code": "missing_credentials",
                    }
                ],
            },
        ],
    }

    report = build_intelligence_report(
        run_id="run-1",
        candidates=candidates,
        fetch_report=fetch_report,
        ai_usage={"calls": 2, "input_tokens": 1000, "output_tokens": 200, "estimated_cost": 0.04},
        card_capacity={"selected": 1, "more": 0, "truncated": 0},
        run_mode="morning",
    )

    assert report["pipeline_status"] == "collection_degraded"
    assert report["funnel"] == {"selected": 1, "held": 1, "rejected": 1}
    assert report["decision_lanes"]["product_capability"] == 1
    assert report["reason_codes"]["held_by_capacity"] == 1
    assert report["concentration"]["authors"]["top_share"] == 2 / 3
    assert report["ai_usage"]["calls"] == 2
    assert report["card_capacity"]["truncated"] == 0
    assert report["run_mode"] == "morning"
    assert report["merge_groups"] == {"one": ["three"]}


def test_all_source_failure_is_not_reported_as_no_updates() -> None:
    report = build_intelligence_report(
        run_id="run-2",
        candidates=[],
        fetch_report={"status": "failure", "sources": []},
    )

    assert report["pipeline_status"] == "pipeline_failed"


def test_orchestrator_saves_searchable_candidate_archive_and_report(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    orchestrator = object.__new__(HorizonOrchestrator)
    orchestrator.config = SimpleNamespace(
        intelligence=IntelligenceRadarConfig(
            enabled=True,
            candidate_store_file="data/candidate_ledger.jsonl",
        )
    )
    orchestrator.last_fetch_report = SimpleNamespace(
        to_dict=lambda: {"status": "success", "sources": []}
    )
    orchestrator.last_intelligence_candidates = [
        _candidate(
            "one",
            CandidateStatus.SELECTED,
            DecisionLane.PRODUCT_CAPABILITY,
            "a",
        )
    ]

    paths = orchestrator._save_intelligence_artifacts("run-20260903T090000Z")

    assert paths["jsonl"].exists()
    assert paths["html"].exists()
    report = json.loads(paths["diagnostics"].read_text(encoding="utf-8"))
    assert report["pipeline_status"] == "ready"
    assert report["candidate_count"] == 1


def test_archive_counts_only_capacity_held_candidates_as_more(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    orchestrator = object.__new__(HorizonOrchestrator)
    orchestrator.config = SimpleNamespace(
        intelligence=IntelligenceRadarConfig(
            enabled=True,
            candidate_store_file="data/candidate_ledger.jsonl",
        )
    )
    orchestrator.last_fetch_report = SimpleNamespace(
        to_dict=lambda: {"status": "success", "sources": []}
    )
    capacity_held = _candidate(
        "capacity",
        CandidateStatus.HELD,
        DecisionLane.PRODUCT_CAPABILITY,
        "a",
    )
    observation = _candidate(
        "observation",
        CandidateStatus.HELD,
        DecisionLane.HOT_CONTENT,
        "b",
    ).model_copy(update={"reason_codes": [ReasonCode.IMMATURE]})
    orchestrator.last_intelligence_candidates = [capacity_held, observation]

    paths = orchestrator._save_intelligence_artifacts("run-capacity")

    report = json.loads(paths["diagnostics"].read_text(encoding="utf-8"))
    assert report["card_capacity"]["more"] == 1

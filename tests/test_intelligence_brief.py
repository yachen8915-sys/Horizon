from datetime import datetime, timezone

from src.models import (
    CandidateRecord,
    CandidateScore,
    CandidateStatus,
    ContentItem,
    DecisionLane,
    EvidenceStatus,
    IntelligenceAnalysis,
    RadarRunMode,
    SourceType,
)
from src.processing.intelligence_brief import render_intelligence_brief


NOW = datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc)


def _candidate(candidate_id: str, lane: DecisionLane) -> CandidateRecord:
    return CandidateRecord(
        candidate_id=candidate_id,
        item=ContentItem(
            id=candidate_id,
            source_type=SourceType.RSS,
            title=f"Title {candidate_id}",
            url=f"https://example.com/{candidate_id}",
            published_at=NOW,
            profile="pangmen-platform-trend-radar" if lane is DecisionLane.HOT_CONTENT else None,
        ),
        status=CandidateStatus.SELECTED,
        discovered_at=NOW,
        updated_at=NOW,
        event_key=f"event:{candidate_id}",
        evidence_status=EvidenceStatus.CONFIRMED,
        intelligence=IntelligenceAnalysis(
            primary_lane=lane,
            decision_summary=f"Decision {candidate_id}",
            content_summary=f"Content {candidate_id}",
            evidence_status=EvidenceStatus.CONFIRMED,
            score=CandidateScore(total=8.2),
        ),
        rule_version="v1",
    )


def test_brief_is_grouped_by_content_sections_not_source_columns() -> None:
    brief = render_intelligence_brief(
        [
            _candidate("product", DecisionLane.PRODUCT_CAPABILITY),
            _candidate("hot", DecisionLane.HOT_CONTENT),
            _candidate("tech", DecisionLane.TECHNICAL_FRONTIER),
            _candidate("platform", DecisionLane.PLATFORM_AI_CHANGE),
        ],
        date="2026-09-03",
        run_mode=RadarRunMode.MORNING,
        total_fetched=100,
    )

    assert "AI 产品与应用" in brief
    assert "今日可借势" in brief
    assert "AI 技术与模型" in brief
    assert "平台变化雷达" in brief
    assert "AI 应用" not in brief
    assert "AI 媒体" not in brief
    assert "**为什么值得看：** Decision product" in brief
    assert "精选 4 条 / 抓取 100 条" in brief


def test_afternoon_brief_is_explicitly_incremental() -> None:
    brief = render_intelligence_brief(
        [_candidate("new", DecisionLane.PRODUCT_CAPABILITY)],
        date="2026-09-03",
        run_mode=RadarRunMode.AFTERNOON,
        total_fetched=20,
    )

    assert "下午增量" in brief


def test_brief_keeps_capacity_tail_in_more_section() -> None:
    more = _candidate("more", DecisionLane.HOT_CONTENT).model_copy(
        update={"status": CandidateStatus.HELD}
    )

    brief = render_intelligence_brief(
        [_candidate("selected", DecisionLane.PRODUCT_CAPABILITY)],
        more_candidates=[more],
        date="2026-09-03",
        run_mode=RadarRunMode.MORNING,
        total_fetched=10,
    )

    assert "## 查看更多热点" in brief
    assert "[Title more](https://example.com/more)" in brief


def test_brief_places_shared_coverage_notice_next_to_selection_summary():
    from tests.test_coverage_notice import PARTIAL_NOTICE

    candidate = _candidate("douyin", DecisionLane.HOT_CONTENT)
    brief = render_intelligence_brief([candidate], date="2026-09-07",
        run_mode=RadarRunMode.MORNING, total_fetched=10, coverage_notice=PARTIAL_NOTICE)
    assert f"精选 1 条 / 抓取 10 条\n\n{PARTIAL_NOTICE}" in brief
    assert brief.index(PARTIAL_NOTICE) < brief.index("## 今日运营热点")
    assert str(candidate.item.url) in brief

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

from src.models import (
    ClassificationResult,
    ContentAnalysis,
    ContentItem,
    PlatformTrendsConfig,
    ProcessingResult,
    SourceType,
)
from src.orchestrator import HorizonOrchestrator
from src.processing.platform_trend_selection import PlatformTrendStateStore


def _trend_item(
    title: str,
    *,
    rank: int = 1,
    hot_value: int | None = 100,
    cross_platform: int = 1,
    operations: float = 7,
    opportunity: float = 6,
    evidence: float = 4,
) -> ContentItem:
    item = ContentItem(
        id=title,
        source_type=SourceType.PLATFORM_TRENDS,
        title=title,
        url=f"https://example.com/{title}",
        published_at=datetime.now(timezone.utc),
        profile="pangmen-platform-trend-radar",
        metadata={
            "platform": "weibo",
            "rank": rank,
            "rank_limit": 30,
            "hot_value": hot_value,
            "cross_platform_count": cross_platform,
            "platform_occurrences": [
                {
                    "platform": "weibo",
                    "rank": rank,
                    "rank_limit": 30,
                    "hot_value": hot_value,
                }
            ],
        },
        processing=ProcessingResult(
            classification=ClassificationResult(
                profile="pangmen-platform-trend-radar", method="source_override"
            ),
            analysis=ContentAnalysis(
                score=operations,
                operations_score=operations,
                content_opportunity_score=opportunity,
                evidence_quality_score=evidence,
                reason="test",
                summary=title,
            ),
        ),
    )
    return item


def _orchestrator(tmp_path):
    orchestrator = HorizonOrchestrator.__new__(HorizonOrchestrator)
    orchestrator.config = SimpleNamespace(
        sources=SimpleNamespace(
            platform_trends=PlatformTrendsConfig(
                enabled=True,
                state_file=str(tmp_path / "platform-trend-state.json"),
            )
        ),
        processing=SimpleNamespace(
            profile_settings={
                "pangmen-platform-trend-radar": SimpleNamespace(threshold=7.0)
            }
        ),
    )
    return orchestrator


def test_high_heat_relaxed_gate_allows_non_ai_public_topic(tmp_path) -> None:
    orchestrator = _orchestrator(tmp_path)
    item = _trend_item("明星演唱会带动城市消费", operations=5, opportunity=4, evidence=4)
    item.metadata["platform_occurrences"].append(
        {
            "platform": "douyin",
            "rank": 1,
            "rank_limit": 30,
            "hot_value": 100,
        }
    )
    item.metadata["cross_platform_count"] = 2
    orchestrator._prepare_platform_trend_selection([item])

    assert item.metadata["heat_score"] == 9.0
    assert orchestrator.passes_profile_filter(item) is True
    assert item.metadata["trend_eligibility_reason"] == "high_heat_relaxed_pass"


def test_high_heat_item_that_meets_standard_gate_is_labeled_standard_pass(tmp_path) -> None:
    orchestrator = _orchestrator(tmp_path)
    item = _trend_item(
        "体育赛事带动城市消费",
        operations=8,
        opportunity=7,
        evidence=6,
    )
    item.metadata["platform_occurrences"].append(
        {
            "platform": "douyin",
            "rank": 1,
            "rank_limit": 30,
            "hot_value": 100,
        }
    )
    item.metadata["cross_platform_count"] = 2
    orchestrator._prepare_platform_trend_selection([item])

    assert orchestrator.passes_profile_filter(item) is True
    assert item.metadata["trend_eligibility_reason"] == "standard_pass"


def test_high_heat_still_requires_minimum_evidence(tmp_path) -> None:
    orchestrator = _orchestrator(tmp_path)
    item = _trend_item("明星八卦标题", operations=5, opportunity=4, evidence=3)
    item.metadata["platform_occurrences"].append(
        {
            "platform": "douyin",
            "rank": 1,
            "rank_limit": 30,
            "hot_value": 100,
        }
    )
    item.metadata["cross_platform_count"] = 2
    orchestrator._prepare_platform_trend_selection([item])

    assert orchestrator.passes_profile_filter(item) is False
    assert item.metadata["trend_eligibility_reason"] == "evidence_insufficient"


def test_platform_trend_state_classifies_new_and_rising_items(tmp_path) -> None:
    config = PlatformTrendsConfig(
        enabled=True,
        state_file=str(tmp_path / "state.json"),
        history_days=7,
    )
    first = _trend_item("体育赛事带动城市文旅", rank=10, hot_value=10)
    store = PlatformTrendStateStore(config)
    first_time = datetime(2026, 8, 21, tzinfo=timezone.utc)
    store.prepare([first], now=first_time)
    assert first.metadata["trend_type"] == "current_high"
    store.commit([], now=first_time)

    second = _trend_item("体育赛事带动城市文旅", rank=1, hot_value=100)
    store = PlatformTrendStateStore(config)
    second_time = first_time + timedelta(days=1)
    store.prepare([second], now=second_time)
    assert second.metadata["trend_type"] == "rising"
    store.commit([second], now=second_time)

    payload = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["items"][0]["last_selected_at"]
    assert len(payload["items"][0]["rank_history"]) == 2

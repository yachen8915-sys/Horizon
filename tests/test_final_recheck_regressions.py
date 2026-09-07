"""Offline regressions for the remaining whole-branch review findings."""

import asyncio
from io import StringIO
import json
import logging

import httpx
import pytest
from rich.console import Console

from src.ai.summarizer import DailySummarizer
from src.diagnostics.intelligence_report import build_intelligence_report
from src.logging_config import configure_logging
from src.models import (
    CandidateStatus, DecisionLane, RadarRunMode, ReasonCode, SourceType,
    WebhookConfig, YouTubeChannelConfig, YouTubeDataConfig, YouTubeQueryConfig,
)
from src.processing.intelligence_brief import render_intelligence_brief
from src.processing.intelligence_presentation import build_intelligence_presentation
from src.scrapers.youtube import YouTubeDataScraper
from src.services.webhook import WebhookNotifier
from tests.test_intelligence_brief import NOW, _candidate


@pytest.mark.parametrize("entrypoint", ["shared-info", "shared-debug", "mcp-default"])
@pytest.mark.parametrize("route", ["search", "channel"])
@pytest.mark.parametrize("status", [403, 429])
def test_shared_logging_keeps_httpx_request_credentials_out_of_all_output(
    monkeypatch, entrypoint, route, status,
):
    secret = "final-recheck-secret-key"
    monkeypatch.setenv("YOUTUBE_DATA_API_KEY", secret)
    captured = StringIO()
    console = Console(file=captured, width=300, color_system=None)
    root = logging.getLogger()
    previous_handlers, previous_level = root.handlers[:], root.level
    # An earlier test or application configuration must not hide the regression.
    monkeypatch.setattr(logging.getLogger("httpx"), "level", logging.NOTSET)
    config = YouTubeDataConfig(enabled=True,
        queries=[YouTubeQueryConfig(query="AI tools")] if route == "search" else [],
        channels=[YouTubeChannelConfig(channel_id="UC-review", name="Review")] if route == "channel" else [])

    async def fetch():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status))) as client:
            scraper = YouTubeDataScraper(config, client)
            assert await scraper.fetch(NOW) == []
            return scraper.last_source_results

    try:
        if entrypoint == "mcp-default":
            from src.mcp import server
            monkeypatch.setattr(server, "console", console)
            monkeypatch.setattr(server.mcp, "run", lambda: None)
            monkeypatch.setattr("sys.argv", ["horizon-mcp"])
            server.main()
            assert root.level == logging.INFO
        else:
            configure_logging(console, level="DEBUG" if entrypoint == "shared-debug" else "INFO")
        logging.getLogger("horizon.review").info("Application information is still visible")
        results = asyncio.run(fetch())
        output = captured.getvalue()
        assert "Application information is still visible" in output
        assert str(status) in output  # The safe scraper warning is still useful.
        assert secret not in output
        assert "key=" not in output
        assert secret not in json.dumps(results)
    finally:
        for handler in root.handlers:
            if handler not in previous_handlers:
                handler.close()
        root.handlers = previous_handlers
        root.setLevel(previous_level)


@pytest.mark.parametrize("preanalysis_status", [CandidateStatus.OBSERVING, CandidateStatus.REJECTED])
@pytest.mark.parametrize("analysis_state", ["all_failed", "none_qualified", "partial_success"])
def test_report_uses_only_attempted_analysis_when_deciding_stage_failure(preanalysis_status, analysis_state):
    preanalysis = _candidate("not-analyzed", DecisionLane.HOT_CONTENT)
    preanalysis.intelligence = None
    preanalysis.status = preanalysis_status
    preanalysis.reason_codes = [ReasonCode.IMMATURE if preanalysis_status is CandidateStatus.OBSERVING else ReasonCode.LOW_PROPAGATION]
    failed = _candidate("analysis-failed", DecisionLane.PRODUCT_CAPABILITY)
    failed.intelligence = None
    failed.status = CandidateStatus.PROCESSING_ERROR
    failed.reason_codes = [ReasonCode.ANALYSIS_FAILED]
    analyzed = _candidate("analyzed", DecisionLane.PRODUCT_CAPABILITY)
    analyzed.status = CandidateStatus.REJECTED
    analyzed.reason_codes = [ReasonCode.LOW_QUALITY]
    rows = [preanalysis]
    if analysis_state != "none_qualified":
        rows.append(failed)
    if analysis_state != "all_failed":
        rows.append(analyzed)
    report = build_intelligence_report(run_id="recheck", candidates=rows, fetch_report={"status": "success"})
    assert report["pipeline_status"] == ("pipeline_failed" if analysis_state == "all_failed" else "no_qualified_updates")


def _mixed_hot_candidates(nontrend_first):
    trends = [_candidate(f"watch-{index:02}", DecisionLane.HOT_CONTENT) for index in range(15)]
    for trend in trends:
        trend.item.metadata["trend_pool"] = "watch"
    youtube = _candidate("youtube-hot", DecisionLane.HOT_CONTENT)
    youtube.item.source_type = SourceType.YOUTUBE
    youtube.item.profile = "pangmen-topic-radar"
    ai = _candidate("industry", DecisionLane.AI_INDUSTRY_SOCIETY)
    hot_order = [youtube, *trends] if nontrend_first else [*trends, youtube]
    selected = [*hot_order[:7], ai, *hot_order[7:]]
    held = _candidate("previous-hot-overflow", DecisionLane.HOT_CONTENT)
    held.status = CandidateStatus.HELD
    held.reason_codes = [ReasonCode.HELD_BY_CAPACITY]
    return selected, [held], hot_order, ai


@pytest.mark.parametrize("nontrend_first", [False, True])
def test_shared_hot_capacity_preserves_input_order_and_existing_overflow(nontrend_first):
    selected, more, hot_order, ai = _mixed_hot_candidates(nontrend_first)
    presentation = build_intelligence_presentation(selected, more)
    detailed = presentation.hot_leverage + presentation.hot_watch
    assert len(detailed) == 15
    assert {row.candidate_id for row in detailed} == {row.candidate_id for row in hot_order[:15]}
    assert presentation.more_hot == [hot_order[15], *more]
    assert presentation.ai_industry == [ai]
    assert all(row.status is CandidateStatus.SELECTED for row in selected)
    report = build_intelligence_report(run_id="recheck", candidates=selected + more, fetch_report={"status": "success"})
    counts = report["presentation_categories"]
    assert counts["hot_leverage"] + counts["hot_watch"] == 15
    assert counts["more_hot"] == 2


@pytest.mark.parametrize("nontrend_first", [False, True])
def test_markdown_and_feishu_share_mixed_profile_hot_capacity(monkeypatch, nontrend_first):
    selected, more, hot_order, ai = _mixed_hot_candidates(nontrend_first)
    brief = render_intelligence_brief(selected, more_candidates=more, date="2026-09-07",
        run_mode=RadarRunMode.MORNING, total_fetched=25)
    details, overflow = brief.split("## 查看更多热点", 1)
    assert str(hot_order[-1].item.url) not in details
    assert str(hot_order[-1].item.url) in overflow
    assert all(brief.count(str(row.item.url)) == 1 for row in selected + more)
    monkeypatch.setenv("RECHECK_WEBHOOK", "https://example.com/webhook")
    notifier = WebhookNotifier(WebhookConfig(enabled=True, platform="feishu", layout="collapsible", url_env="RECHECK_WEBHOOK"))
    message = notifier.build_daily_summary_messages(summary=brief,
        important_items=[row.item for row in selected], intelligence_candidates=selected,
        intelligence_more_candidates=more, all_items_count=25, date="2026-09-07",
        lang="zh", summarizer=DailySummarizer())[0]
    panels = [element for element in message["_request_body_override"]["card"]["body"]["elements"]
              if element["tag"] == "collapsible_panel"]
    more_panel = [panel for panel in panels if panel["header"]["title"]["content"].startswith("查看更多热点")]
    detail_panels = [panel for panel in panels if panel not in more_panel]
    assert len(detail_panels) == 16  # 15 total hotspots and one separate AI industry item.
    assert str(hot_order[-1].item.url) not in str(detail_panels)
    assert str(hot_order[-1].item.url) in str(more_panel)
    assert str(ai.item.url) in str(detail_panels)

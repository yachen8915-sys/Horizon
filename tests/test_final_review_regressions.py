"""Offline integration regressions for the final content-type review."""

import asyncio
from datetime import timedelta
import json
import logging
from unittest.mock import AsyncMock

import httpx
import pytest

from src.ai.summarizer import DailySummarizer
from src.diagnostics.intelligence_report import build_intelligence_report
from src.models import (
    CandidateStatus, DecisionLane, DeliveryRecord, EvidenceStatus,
    PlatformTrendProviderConfig, PlatformTrendsConfig, RadarRunMode, ReasonCode,
    SourceType, WebhookConfig, YouTubeChannelConfig, YouTubeDataConfig, YouTubeQueryConfig,
)
from src.processing.candidate_identity import merge_duplicate_group
from src.processing.candidate_pipeline import CandidateBuilder
from src.processing.delivery_selection import DeliverySelector
from src.processing.intelligence_presentation import build_intelligence_presentation
from src.scrapers.platform_trends import PlatformTrendsScraper
from src.scrapers.youtube import YouTubeDataScraper
from src.services.webhook import WebhookNotifier
from tests.test_content_type_gate import make_gate_item
from tests.test_intelligence_brief import _candidate as display_candidate
from tests.test_intelligence_selection import NOW, _candidate, _selector, _trend


@pytest.mark.parametrize("provider_id,name", [("alapi_tophub", "ALAPI"), ("dailyhotapi_public_instance", "DailyHotAPI")])
def test_scraper_display_name_cannot_create_second_provider_boost(provider_id, name):
    scraper = PlatformTrendsScraper(PlatformTrendsConfig(), AsyncMock())
    item = scraper._row_to_item(
        {"title": "女儿用豆包抄答案家长只用了一招", "url": "https://example.com/topic"},
        25, NOW, PlatformTrendProviderConfig(platform="weibo", provider=provider_id, provider_name=name),
    )
    analyzed, _ = make_gate_item(focus="general")
    item.processing = analyzed.processing
    builder = CandidateBuilder("review")
    merged = merge_duplicate_group([builder.from_analyzed_item(item)]).primary
    assert merged.item.metadata["providers"] == [provider_id]
    assert merged.item.metadata["provider_name"] == name
    gated = builder.apply_content_type_gate(merged)
    assert gated.status is CandidateStatus.REJECTED
    assert ReasonCode.INSUFFICIENT_HOTSPOT_SIGNAL in gated.reason_codes


def test_scraper_premerged_real_two_providers_keep_two_machine_identities():
    scraper = PlatformTrendsScraper(PlatformTrendsConfig(), AsyncMock())
    items = [scraper._row_to_item(
        {"title": "普通热点", "url": f"https://example.com/{identity}"}, 25, NOW,
        PlatformTrendProviderConfig(platform="weibo", provider=identity, provider_name=name),
    ) for identity, name in [("alapi_tophub", "ALAPI"), ("dailyhotapi_public_instance", "DailyHotAPI")]]
    raw = scraper._merge_exact_topics(items)[0]
    raw.processing = make_gate_item(focus="general")[0].processing
    builder = CandidateBuilder("review")
    merged = merge_duplicate_group([builder.from_analyzed_item(raw)]).primary
    assert merged.item.metadata["providers"] == ["alapi_tophub", "dailyhotapi_public_instance"]
    assert builder.apply_content_type_gate(merged).item.metadata["trend_pool"] == "watch"


@pytest.mark.parametrize("source,profile", [
    (SourceType.YOUTUBE, "pangmen-topic-radar"), (SourceType.AIHOT, "pangmen-topic-radar"),
    (SourceType.RSS, "pangmen-topic-radar"),
])
def test_nontrend_hot_content_has_shared_content_based_presentation(source, profile):
    candidate = display_candidate("valid-hot", DecisionLane.HOT_CONTENT)
    candidate.item.source_type = source
    candidate.item.profile = profile
    presentation = build_intelligence_presentation([candidate], [])
    assert presentation.hot_leverage == [candidate]
    report = build_intelligence_report(run_id="review", candidates=[candidate], fetch_report={"status": "success"})
    assert report["presentation_categories"]["hot_leverage"] == 1


def test_unmapped_analysis_is_retained_for_diagnostics_without_crashing(caplog):
    candidate = display_candidate("missing", DecisionLane.HOT_CONTENT)
    candidate.intelligence = None
    with caplog.at_level(logging.WARNING):
        report = build_intelligence_report(run_id="review", candidates=[candidate], fetch_report={"status": "success"})
    assert report["presentation_categories"]["unmapped"] == 1
    assert "missing" in caplog.text


@pytest.mark.parametrize("pool", ["unknown", ["watch"]])
def test_invalid_pool_is_retained_without_assigning_another_section(pool):
    candidate = display_candidate("bad-pool", DecisionLane.HOT_CONTENT)
    candidate.item.metadata["trend_pool"] = pool
    presentation = build_intelligence_presentation([candidate], [])
    assert presentation.unmapped == [candidate]
    assert list(presentation.sections()) == []


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("evidence,pending,label", [
    (EvidenceStatus.UNVERIFIED, False, "待核实"),
    (EvidenceStatus.CONFIRMED, True, "待验证"),
])
def test_feishu_candidate_evidence_label_survives_detail_and_more(monkeypatch, compact, evidence, pending, label):
    monkeypatch.setenv("REVIEW_WEBHOOK", "https://example.com/webhook")
    candidate = display_candidate("industry", DecisionLane.AI_INDUSTRY_SOCIETY)
    candidate.evidence_status = evidence
    candidate.item.metadata["pending_verification"] = pending
    # Deliberately leave analysis status confirmed: CandidateRecord is authoritative.
    notifier = WebhookNotifier(WebhookConfig(enabled=True, platform="feishu", layout="collapsible", url_env="REVIEW_WEBHOOK"))
    message = notifier.build_daily_summary_messages(
        summary="", important_items=[] if compact else [candidate.item],
        intelligence_candidates=[] if compact else [candidate],
        intelligence_more_candidates=[candidate] if compact else [],
        all_items_count=1, date="2026-09-07", lang="zh", summarizer=DailySummarizer(),
    )[0]
    assert label in json.dumps(message["_request_body_override"], ensure_ascii=False)


def test_ai_cooldown_precedes_capacity_and_cannot_leak_into_delivery_more():
    fresh = _candidate("fresh", lane=DecisionLane.PRODUCT_CAPABILITY, total=9, author="fresh")
    old = _candidate("old", lane=DecisionLane.PRODUCT_CAPABILITY, total=8, author="old")
    delivery = DeliveryRecord(delivery_id="yesterday", run_id="old-run", run_mode=RadarRunMode.MORNING,
        delivered_at=NOW - timedelta(days=1), candidate_id=old.candidate_id, event_key=old.event_key,
        event_version=1, display_tier="selected", content_fingerprint="old")
    result = _selector(max_items=1).select([fresh, old], now=NOW, deliveries=[delivery])
    assert result.held[0].reason_codes[-1] is ReasonCode.DUPLICATE
    more = [row for row in result.held if row.reason_codes[-1] is ReasonCode.HELD_BY_CAPACITY]
    sent = DeliverySelector().select(result.selected, more_candidates=more, run_id="new", run_mode=RadarRunMode.MORNING,
        now=NOW, deliveries=[delivery])
    assert sent.more_candidates == []


@pytest.mark.parametrize("limit", ["author_limit", "source_limit", "platform_limit"])
def test_ai_industry_from_trend_source_obeys_ai_diversity(limit):
    rows = [_trend(f"industry-{index}") for index in range(2)]
    for row in rows:
        row.intelligence.primary_lane = DecisionLane.AI_INDUSTRY_SOCIETY
    result = _selector(**{limit: 1}).select(rows, now=NOW)
    assert len(result.selected) == 1
    assert result.held[0].reason_codes[-1] is ReasonCode.HELD_BY_DIVERSITY


def test_ai_industry_does_not_consume_hot_detail_limit():
    rows = [_trend(f"trend-{index:02}") for index in range(15)]
    industry = _trend("industry")
    industry.intelligence.primary_lane = DecisionLane.AI_INDUSTRY_SOCIETY
    result = _selector().select([industry, *rows], now=NOW)
    assert len(result.selected) == 16
    assert not result.held


@pytest.mark.parametrize("status,reason", [
    (CandidateStatus.PROCESSING_ERROR, ReasonCode.ANALYSIS_FAILED),
    (CandidateStatus.REJECTED, ReasonCode.ANALYSIS_FAILED),
    (CandidateStatus.REJECTED, ReasonCode.LOW_QUALITY),
])
def test_diagnostics_distinguish_analysis_failure_from_zero_qualified(status, reason):
    candidate = display_candidate("analyzed", DecisionLane.PRODUCT_CAPABILITY)
    candidate.status = status
    candidate.reason_codes = [reason]
    report = build_intelligence_report(run_id="review", candidates=[candidate], fetch_report={"status": "success"})
    assert report["pipeline_status"] == ("no_qualified_updates" if reason is ReasonCode.LOW_QUALITY else "pipeline_failed")


@pytest.mark.parametrize("route", ["search", "channel"])
@pytest.mark.parametrize("status", [403, 429])
def test_youtube_http_errors_do_not_expose_credentials(monkeypatch, caplog, route, status):
    secret = "review-secret-credential"
    monkeypatch.setenv("YOUTUBE_DATA_API_KEY", secret)
    config = YouTubeDataConfig(enabled=True,
        queries=[YouTubeQueryConfig(query="AI tools")] if route == "search" else [],
        channels=[YouTubeChannelConfig(channel_id="UC-review", name="Review")] if route == "channel" else [])
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status))) as client:
            scraper = YouTubeDataScraper(config, client)
            assert await scraper.fetch(NOW) == []
            return scraper.last_source_results
    with caplog.at_level(logging.WARNING):
        results = asyncio.run(run())
    assert secret not in caplog.text
    assert secret not in json.dumps(results)
    assert "key=" not in caplog.text + json.dumps(results)
    assert str(status) in caplog.text

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from rich.console import Console

from src.models import ContentItem, RSSSourceConfig, SourceType
from src.orchestrator import FetchReport, HorizonOrchestrator, SourceFetchOutcome


SINCE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_item(item_id: str) -> ContentItem:
    return ContentItem(
        id=item_id,
        source_type=SourceType.RSS,
        title=item_id,
        url=f"https://example.com/{item_id}",
        content="content",
        author="tester",
        published_at=SINCE,
    )


def make_orchestrator() -> HorizonOrchestrator:
    orchestrator = object.__new__(HorizonOrchestrator)
    orchestrator.console = Console(file=StringIO())
    orchestrator.last_fetch_report = None
    return orchestrator


def test_shadow_rss_feeds_are_only_enabled_in_source_shadow_mode() -> None:
    orchestrator = make_orchestrator()
    active = RSSSourceConfig(name="Active", url="https://example.com/active.xml")
    shadow = RSSSourceConfig(
        name="Shadow",
        url="https://example.com/shadow.xml",
        shadow=True,
    )
    orchestrator.config = SimpleNamespace(  # type: ignore[assignment]
        collection=SimpleNamespace(source_shadow_enabled=False),
        sources=SimpleNamespace(rss=[active, shadow]),
    )

    assert orchestrator._enabled_rss_sources() == [active]

    orchestrator.config.collection.source_shadow_enabled = True
    assert orchestrator._enabled_rss_sources() == [active, shadow]


def make_sources(**overrides):  # type: ignore[no-untyped-def]
    values = {
        "github": [],
        "hackernews": SimpleNamespace(enabled=False),
        "rss": [],
        "reddit": SimpleNamespace(enabled=False),
        "telegram": SimpleNamespace(enabled=False),
        "twitter": None,
        "openbb": None,
        "ossinsight": SimpleNamespace(enabled=False),
        "gdelt": None,
        "google_news": None,
        "bilibili": SimpleNamespace(enabled=False),
        "aihot": SimpleNamespace(enabled=False),
        "huggingface": SimpleNamespace(enabled=False),
        "platform_trends": SimpleNamespace(enabled=False),
        "platform_changes": SimpleNamespace(enabled=False),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_fetch_report_includes_source_coverage_gate() -> None:
    report = FetchReport(
        outcomes=[SourceFetchOutcome("RSS Feeds", "empty")],
        source_coverage={
            "ready": False,
            "gaps": [{"decision_lane": "technical_frontier"}],
        },
    )

    payload = report.to_dict()

    assert payload["source_coverage"]["ready"] is False
    assert payload["source_coverage"]["gaps"][0]["decision_lane"] == (
        "technical_frontier"
    )


def test_all_provider_business_failures_make_source_fetch_fail() -> None:
    orchestrator = make_orchestrator()
    scraper = StubScraper([])
    scraper.last_provider_results = [
        {
            "source_id": "platform-trends:weibo:dailyhot",
            "status": "failed",
            "reason_code": "business_error",
            "detail": "500: 获取失败",
        }
    ]

    outcome = asyncio.run(
        orchestrator._fetch_with_progress("Platform Trends", scraper, SINCE)
    )

    assert outcome.status == "failure"
    assert outcome.error == "all configured providers failed"
    assert outcome.to_dict()["providers"][0]["reason_code"] == "business_error"


def test_one_healthy_provider_keeps_source_fetch_success() -> None:
    orchestrator = make_orchestrator()
    scraper = StubScraper([make_item("trend")])
    scraper.last_provider_results = [
        {"source_id": "weibo", "status": "failed", "reason_code": "business_error"},
        {"source_id": "douyin", "status": "healthy", "reason_code": "healthy"},
    ]

    outcome = asyncio.run(
        orchestrator._fetch_with_progress("Platform Trends", scraper, SINCE)
    )

    assert outcome.status == "success"
    assert len(outcome.provider_health) == 2


def test_mixed_provider_fetch_preserves_successful_items_and_shared_notice():
    from tests.test_coverage_notice import health, notice_for, PARTIAL_NOTICE

    orchestrator = make_orchestrator()
    items = [make_item("douyin-dailyhot"), make_item("zhihu-alapi")]
    scraper = StubScraper(items)
    scraper.last_provider_results = [health("weibo", "dailyhot", "failed"),
        health("douyin", "dailyhot", "healthy"), health("zhihu", "alapi_tophub", "healthy")]
    outcome = asyncio.run(orchestrator._fetch_with_progress("Platform Trends", scraper, SINCE))
    assert outcome.items == items
    assert outcome.status == "success"
    assert notice_for(FetchReport([outcome]).to_dict()) == PARTIAL_NOTICE


def test_all_rss_feed_failures_make_aggregate_source_fail() -> None:
    orchestrator = make_orchestrator()
    scraper = StubScraper([])
    scraper.last_feed_results = [
        {
            "source_id": "rss:one",
            "status": "failed",
            "reason_code": "transport_error",
        },
        {
            "source_id": "rss:two",
            "status": "failed",
            "reason_code": "schema_error",
        },
    ]

    outcome = asyncio.run(orchestrator._fetch_with_progress("RSS Feeds", scraper, SINCE))

    assert outcome.status == "failure"
    assert outcome.error == "all configured feeds failed"
    assert len(outcome.to_dict()["feeds"]) == 2


def test_all_github_sub_source_failures_make_aggregate_source_fail() -> None:
    orchestrator = make_orchestrator()
    scraper = StubScraper([])
    scraper.last_source_results = [
        {
            "source_id": "github:repo_releases:missing/repo",
            "status": "failed",
            "reason_code": "transport_error",
        }
    ]

    outcome = asyncio.run(orchestrator._fetch_with_progress("GitHub", scraper, SINCE))

    assert outcome.status == "failure"
    assert outcome.error == "all configured sub-sources failed"
    assert outcome.to_dict()["source_health"][0]["reason_code"] == "transport_error"


def test_bilibili_source_is_wired_into_fetch_reporting(monkeypatch) -> None:
    orchestrator = make_orchestrator()
    kept = ContentItem(
        id="bilibili:video:BV1test",
        source_type=SourceType.BILIBILI,
        title="test",
        url="https://www.bilibili.com/video/BV1test",
        published_at=SINCE,
    )
    orchestrator.config = SimpleNamespace(  # type: ignore[assignment]
        sources=make_sources(bilibili=SimpleNamespace(enabled=True)),
        extractors={},
    )
    monkeypatch.setattr(
        "src.orchestrator.BilibiliScraper",
        lambda config, client: StubScraper([kept]),
    )

    items = asyncio.run(orchestrator.fetch_all_sources(SINCE))

    assert items == [kept]
    assert orchestrator.last_fetch_report is not None
    assert orchestrator.last_fetch_report.outcomes[0].source_name == "Bilibili"


def test_content_radar_sources_are_wired_into_fetch_reporting(monkeypatch) -> None:
    orchestrator = make_orchestrator()
    hf_item = ContentItem(
        id="huggingface:model:test",
        source_type=SourceType.HUGGINGFACE,
        title="model",
        url="https://huggingface.co/test",
        published_at=SINCE,
    )
    trend_item = ContentItem(
        id="platform_trends:newsnow:weibo:test",
        source_type=SourceType.PLATFORM_TRENDS,
        title="trend",
        url="https://example.com/trend",
        published_at=SINCE,
    )
    orchestrator.config = SimpleNamespace(  # type: ignore[assignment]
        sources=make_sources(
            huggingface=SimpleNamespace(enabled=True),
            platform_trends=SimpleNamespace(enabled=True),
        ),
        extractors={},
    )
    monkeypatch.setattr(
        "src.orchestrator.HuggingFaceScraper",
        lambda config, client: StubScraper([hf_item]),
    )
    monkeypatch.setattr(
        "src.orchestrator.PlatformTrendsScraper",
        lambda config, client: StubScraper([trend_item]),
    )

    items = asyncio.run(orchestrator.fetch_all_sources(SINCE))

    assert items == [hf_item, trend_item]
    assert [outcome.source_name for outcome in orchestrator.last_fetch_report.outcomes] == [
        "Hugging Face",
        "Platform Trends",
    ]


def test_platform_change_source_is_independent_from_existing_radars(monkeypatch) -> None:
    orchestrator = make_orchestrator()
    change_item = ContentItem(
        id="platform_changes:page_diff:test",
        source_type=SourceType.PLATFORM_CHANGES,
        title="规则变化",
        url="https://example.com/change",
        published_at=SINCE,
        profile="pangmen-platform-change-radar",
    )
    orchestrator.config = SimpleNamespace(  # type: ignore[assignment]
        sources=make_sources(platform_changes=SimpleNamespace(enabled=True)),
        extractors={},
    )
    monkeypatch.setattr(
        "src.orchestrator.PlatformChangesScraper",
        lambda config, client: StubScraper([change_item]),
    )

    items = asyncio.run(orchestrator.fetch_all_sources(SINCE))

    assert items == [change_item]
    assert orchestrator.last_fetch_report.outcomes[0].source_name == "Platform Changes"


def test_platform_change_fetch_report_preserves_watcher_health(monkeypatch) -> None:
    orchestrator = make_orchestrator()
    orchestrator.config = SimpleNamespace(  # type: ignore[assignment]
        sources=make_sources(platform_changes=SimpleNamespace(enabled=True)),
        extractors={},
    )
    scraper = StubScraper([])
    scraper.last_watcher_results = [
        {"name": "xhs", "health_status": "no_change", "new_count": 0}
    ]
    monkeypatch.setattr(
        "src.orchestrator.PlatformChangesScraper",
        lambda config, client: scraper,
    )

    asyncio.run(orchestrator.fetch_all_sources(SINCE))

    source = orchestrator.last_fetch_report.to_dict()["sources"][0]
    assert source["watchers"][0]["name"] == "xhs"
    assert source["watchers"][0]["health_status"] == "no_change"


class StubScraper:
    def __init__(self, result=None, error: Exception | None = None):  # type: ignore[no-untyped-def]
        self.result = [] if result is None else result
        self.error = error

    async def fetch(self, since):  # type: ignore[no-untyped-def]
        if self.error:
            raise self.error
        return self.result


def test_all_success_empty_has_normal_success_report(monkeypatch) -> None:
    orchestrator = make_orchestrator()
    orchestrator.config = SimpleNamespace(  # type: ignore[assignment]
        sources=make_sources(github=[object()]), extractors={}
    )
    monkeypatch.setattr(
        "src.orchestrator.GitHubScraper",
        lambda config, client: StubScraper(),
    )

    items = asyncio.run(orchestrator.fetch_all_sources(SINCE))

    assert items == []
    assert orchestrator.last_fetch_report is not None
    assert orchestrator.last_fetch_report.status == "success"
    assert orchestrator.last_fetch_report.all_failed is False
    assert orchestrator.last_fetch_report.to_dict()["empty"] == 1


def test_partial_failure_keeps_items_and_source_names(monkeypatch) -> None:
    orchestrator = make_orchestrator()
    kept = make_item("kept")
    orchestrator.config = SimpleNamespace(  # type: ignore[assignment]
        sources=make_sources(
            github=[object()], hackernews=SimpleNamespace(enabled=True)
        ),
        extractors={},
    )
    monkeypatch.setattr(
        "src.orchestrator.GitHubScraper",
        lambda config, client: StubScraper([kept]),
    )
    monkeypatch.setattr(
        "src.orchestrator.HackerNewsScraper",
        lambda config, client: StubScraper(error=ValueError("unavailable")),
    )

    items = asyncio.run(orchestrator.fetch_all_sources(SINCE))

    assert items == [kept]
    assert orchestrator.last_fetch_report is not None
    report = orchestrator.last_fetch_report
    assert [outcome.source_name for outcome in report.outcomes] == ["GitHub", "Hacker News"]
    assert report.status == "partial_failure"
    assert report.failed_count == 1
    source_reports = report.to_dict()["sources"]
    assert isinstance(source_reports, list)
    assert source_reports[1]["error"] == "ValueError: unavailable"


def test_native_run_keeps_source_failure_out_of_the_group_webhook(monkeypatch) -> None:
    orchestrator = make_orchestrator()
    orchestrator.config = SimpleNamespace(  # type: ignore[assignment]
        email=None,
        collection=SimpleNamespace(time_window_hours=24),
    )
    orchestrator.email_manager = None
    send_failure = AsyncMock()
    orchestrator.webhook_notifier = SimpleNamespace(send_failure=send_failure)  # type: ignore[assignment]
    report = FetchReport(
        [
            SourceFetchOutcome("GitHub", "failure", error="RuntimeError: down"),
            SourceFetchOutcome("RSS Feeds", "failure", error="TimeoutError: slow"),
        ]
    )

    async def fetch_all_sources(since):  # type: ignore[no-untyped-def]
        orchestrator.last_fetch_report = report
        return []

    monkeypatch.setattr(orchestrator, "fetch_all_sources", fetch_all_sources)

    with pytest.raises(RuntimeError, match="All 2 attempted sources failed.*GitHub.*RSS Feeds"):
        asyncio.run(orchestrator.run())

    send_failure.assert_not_awaited()


def test_native_run_treats_all_success_empty_as_no_content(monkeypatch) -> None:
    orchestrator = make_orchestrator()
    orchestrator.config = SimpleNamespace(  # type: ignore[assignment]
        email=None,
        collection=SimpleNamespace(time_window_hours=24),
    )
    orchestrator.email_manager = None
    send_failure = AsyncMock()
    orchestrator.webhook_notifier = SimpleNamespace(send_failure=send_failure)  # type: ignore[assignment]

    async def fetch_all_sources(since):  # type: ignore[no-untyped-def]
        orchestrator.last_fetch_report = FetchReport(
            [SourceFetchOutcome("RSS Feeds", "empty")]
        )
        return []

    monkeypatch.setattr(orchestrator, "fetch_all_sources", fetch_all_sources)

    asyncio.run(orchestrator.run())

    send_failure.assert_not_awaited()


def test_orchestrator_shares_notice_between_saved_markdown_and_feishu(tmp_path, monkeypatch):
    from src.models import IntelligenceRadarConfig, RadarRunMode
    from src.services.webhook import WebhookNotifier
    from src.models import WebhookConfig
    from src.storage.manager import StorageManager
    from tests.test_intelligence_presentation import presentation_fixture
    from tests.test_coverage_notice import health, PARTIAL_NOTICE

    monkeypatch.chdir(tmp_path)
    selected, more = presentation_fixture()
    items = [c.item for c in selected]
    orchestrator = make_orchestrator()
    orchestrator.email_manager = None
    orchestrator.config = SimpleNamespace(email=None, delivery_allowed=True,
        collection=SimpleNamespace(time_window_hours=24),
        ai=SimpleNamespace(languages=["zh"]), digest=SimpleNamespace(profile_order=[]),
        intelligence=IntelligenceRadarConfig(enabled=True, run_mode=RadarRunMode.MORNING,
            candidate_store_file="candidates.jsonl",
            delivery_store_file="deliveries.jsonl"))
    orchestrator.profiles = SimpleNamespace(names={})
    orchestrator.storage = StorageManager(data_dir=tmp_path / "data")
    orchestrator.last_intelligence_candidates = selected + more
    report = FetchReport([SourceFetchOutcome("Platform Trends", "success", items=items,
        provider_health=[health("weibo", "dailyhot", "failed"),
            health("douyin", "dailyhot", "healthy"), health("zhihu", "alapi_tophub", "healthy")])])

    async def fetch(since):
        orchestrator.last_fetch_report = report
        return items

    monkeypatch.setattr(orchestrator, "fetch_all_sources", fetch)
    monkeypatch.setattr(orchestrator, "_determine_time_window", lambda hours: SINCE)
    monkeypatch.setattr(orchestrator, "merge_cross_source_duplicates", lambda rows: rows)
    monkeypatch.setattr(orchestrator, "_apply_social_quality_gate", lambda rows: rows)
    monkeypatch.setattr(orchestrator, "_cache_path", lambda kind: tmp_path / f"{kind}.json")
    monkeypatch.setattr(orchestrator, "_save_items_cache", lambda path, rows: None)
    monkeypatch.setattr(orchestrator, "analyze_items", AsyncMock(return_value=items))
    monkeypatch.setattr(orchestrator, "_select_intelligence_candidates",
        lambda rows: SimpleNamespace(items=items, exclusion_stages={}))
    monkeypatch.setattr(orchestrator, "enrich_items", AsyncMock(return_value=None))

    monkeypatch.setenv("COVERAGE_TEST_WEBHOOK", "https://example.com/webhook")
    notifier = WebhookNotifier(WebhookConfig(enabled=True, platform="feishu", layout="collapsible",
        url_env="COVERAGE_TEST_WEBHOOK"))
    delivered = []

    async def capture_delivery(**kwargs):
        delivered.extend(notifier.build_daily_summary_messages(**kwargs))
        return []

    orchestrator.webhook_notifier = SimpleNamespace(send_daily_summary=capture_delivery)
    asyncio.run(orchestrator.run())
    summaries = list((tmp_path / "data" / "summaries").glob("*.md"))
    assert len(summaries) == 1
    assert PARTIAL_NOTICE in summaries[0].read_text(encoding="utf-8")
    elements = delivered[0]["_request_body_override"]["card"]["body"]["elements"]
    assert elements[1]["content"] == PARTIAL_NOTICE
    assert all(str(c.item.url) in str(elements) for c in selected + more)

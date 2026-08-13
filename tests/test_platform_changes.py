"""Contract tests for the public platform-change radar source."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from src.ai.summarizer import DailySummarizer
from src.ai.prompting.analysis import analysis_system_prompt, analysis_user_prompt
from src.ai.prompting.enrichment import item_context
from src.models import (
    ClassificationResult,
    ContentAnalysis,
    ContentArtifact,
    ContentBlock,
    ContentItem,
    PlatformChangeWatcherConfig,
    PlatformChangesConfig,
    ProcessingResult,
    SourceType,
    WebhookConfig,
)
from src.scrapers.platform_changes import (
    PlatformChangesScraper,
    normalize_page_text,
)
from src.processing import ProfileRegistry
from src.services.webhook import WebhookNotifier


NOW = datetime(2026, 8, 12, 1, 15, tzinfo=timezone.utc)
SINCE = NOW - timedelta(hours=24)


def _run(scraper: PlatformChangesScraper) -> list[ContentItem]:
    return asyncio.run(scraper.fetch(SINCE))


def _client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _config(tmp_path: Path, *watchers: PlatformChangeWatcherConfig) -> PlatformChangesConfig:
    return PlatformChangesConfig(
        enabled=True,
        lookback_days=7,
        state_file=str(tmp_path / "platform_change_state.json"),
        watchers=list(watchers),
    )


def _discovery_item(
    item_id: str,
    *,
    title: str,
    url: str,
    content: str,
    author: str = "行业媒体",
    published_at: datetime = NOW,
) -> ContentItem:
    return ContentItem(
        id=item_id,
        source_type=SourceType.GOOGLE_NEWS,
        title=title,
        url=url,
        content=content,
        author=author,
        published_at=published_at,
    )


def test_normalize_page_text_ignores_markup_and_whitespace_noise() -> None:
    first = "<main><h1>投稿规则</h1><p>原创 内容</p></main>"
    second = "<main>\n<h1> 投稿规则 </h1><p>原创   内容</p>\n</main>"

    assert normalize_page_text(first) == normalize_page_text(second)
    assert "投稿规则" in normalize_page_text(first)


def test_index_first_run_builds_baseline_without_emitting_history(tmp_path: Path) -> None:
    html = '<a href="/notice/1">规则更新一</a><a href="https://outside.example/a">外链</a>'
    watcher = PlatformChangeWatcherConfig(
        name="xiaohongshu-index",
        mode="index",
        platform="xiaohongshu",
        url="https://school.xiaohongshu.com/newhome",
        include_patterns=[r"/notice/"],
        change_types=["ecommerce", "rule"],
        source_level="official",
    )
    client = _client(lambda request: httpx.Response(200, text=html, request=request))
    config = _config(tmp_path, watcher)

    assert _run(PlatformChangesScraper(config, client, now_provider=lambda: NOW)) == []

    state = json.loads(Path(config.state_file).read_text(encoding="utf-8"))
    seen = state["watchers"]["xiaohongshu-index"]["seen_urls"]
    assert list(seen) == ["https://school.xiaohongshu.com/notice/1"]
    asyncio.run(client.aclose())


def test_index_js_shell_without_public_anchors_is_not_marked_as_verified(tmp_path: Path) -> None:
    watcher = PlatformChangeWatcherConfig(
        name="js-only-index",
        mode="index",
        platform="xiaohongshu",
        url="https://school.xiaohongshu.com/newhome",
        source_level="official",
    )
    client = _client(
        lambda request: httpx.Response(
            200,
            text='<div id="app"></div><script src="bundle.js"></script>',
            request=request,
        )
    )
    config = _config(tmp_path, watcher)
    scraper = PlatformChangesScraper(config, client, now_provider=lambda: NOW)
    assert _run(scraper) == []

    state = json.loads(Path(config.state_file).read_text(encoding="utf-8"))
    assert "js-only-index" not in state["watchers"]
    assert scraper.last_watcher_results[0]["status"] == "shell_page"
    assert scraper.last_watcher_results[0]["content_count"] == 0
    asyncio.run(client.aclose())


def test_index_detects_app_shell_before_anchor_parsing(tmp_path: Path) -> None:
    watcher = PlatformChangeWatcherConfig(
        name="app-shell",
        mode="index",
        platform="xiaohongshu",
        url="https://ec.xiaohongshu.com/ecommerce/official-info",
        source_level="official",
    )
    client = _client(
        lambda request: httpx.Response(
            200, text="<!doctype html><html><body><div id='app'></div></body></html>", request=request
        )
    )
    scraper = PlatformChangesScraper(_config(tmp_path, watcher), client, now_provider=lambda: NOW)

    assert _run(scraper) == []
    assert scraper.last_watcher_results[0]["status"] == "shell_page"
    asyncio.run(client.aclose())


def test_watcher_health_reports_baseline_and_no_change_content_counts(tmp_path: Path) -> None:
    watcher = PlatformChangeWatcherConfig(
        name="dated-page",
        mode="page_diff",
        platform="douyin",
        url="https://open.douyin.com/rules",
        source_level="official",
        min_content_chars=5,
    )
    body = {"text": "创作者规则正文，持续有效。"}
    client = _client(lambda request: httpx.Response(200, text=f"<main>{body['text']}</main>", request=request))
    scraper = PlatformChangesScraper(_config(tmp_path, watcher), client, now_provider=lambda: NOW)

    assert _run(scraper) == []
    assert scraper.last_watcher_results[0]["status"] == "ok"
    assert scraper.last_watcher_results[0]["health_status"] == "baseline"
    assert scraper.last_watcher_results[0]["content_count"] == 1

    assert _run(scraper) == []
    assert scraper.last_watcher_results[0]["status"] == "no_change"
    assert scraper.last_watcher_results[0]["health_status"] == "no_change"
    assert scraper.last_watcher_results[0]["content_count"] == 1
    asyncio.run(client.aclose())


def test_candidate_trace_contains_fetch_identity_and_stage_slots(tmp_path: Path) -> None:
    watcher = PlatformChangeWatcherConfig(
        name="trace-page",
        mode="page_diff",
        platform="douyin",
        url="https://open.douyin.com/rules",
        source_level="official",
        min_content_chars=5,
    )
    body = {"text": "创作者规则旧正文。"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=f"<main>{body['text']}</main>", request=request)

    client = _client(handler)
    scraper = PlatformChangesScraper(_config(tmp_path, watcher), client, now_provider=lambda: NOW)
    assert _run(scraper) == []
    body["text"] = "创作者规则新正文，新增 AI 标识要求。"
    items = _run(scraper)

    trace = items[0].metadata["candidate_trace"]
    assert trace["candidate_id"] == items[0].id
    assert trace["watcher"] == "trace-page"
    assert trace["discovery_mode"] == "page_diff"
    assert trace["fetch"]["status"] == "kept"
    assert set(trace) >= {
        "candidate_id", "watcher", "discovery_mode", "fetch", "merge",
        "analyze", "threshold", "dedup", "balance", "final",
        "outcome", "reason", "merged_into_id",
    }
    asyncio.run(client.aclose())


def test_xiaohongshu_rules_api_baselines_then_emits_only_new_articles(
    tmp_path: Path,
) -> None:
    rows = [
        {
            "articleId": 119895,
            "title": "虚拟卡券商品发布及宣传规范",
            "createTime": "2026年08月11日",
            "publishStartTime": "2026年08月04日",
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers["referer"] == "https://school.xiaohongshu.com/newhome"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "success": True,
                "data": {"dataList": list(rows), "totalCount": len(rows)},
            },
            request=request,
        )

    watcher = PlatformChangeWatcherConfig(
        name="xiaohongshu-public-index",
        mode="xiaohongshu_rules",
        platform="xiaohongshu",
        url=(
            "https://school.xiaohongshu.com/api/edith/governance/inform/"
            "rule/query_article_list_by_filter"
        ),
        source_level="official",
        change_types=["ecommerce", "rule"],
        fetch_limit=30,
    )
    client = _client(handler)
    config = _config(tmp_path, watcher)
    scraper = PlatformChangesScraper(config, client, now_provider=lambda: NOW)

    assert _run(scraper) == []
    rows.insert(
        0,
        {
            "articleId": 119896,
            "title": "小红书创作者发布规则调整",
            "createTime": "2026年08月12日",
            "publishStartTime": "2026年08月12日",
        },
    )

    items = _run(scraper)

    assert [item.title for item in items] == ["小红书创作者发布规则调整"]
    assert str(items[0].url) == "https://school.xiaohongshu.com/rule/detail/119896"
    assert items[0].published_at == datetime(2026, 8, 11, 16, tzinfo=timezone.utc)
    assert items[0].metadata["discovery_mode"] == "xiaohongshu_rules"
    assert items[0].metadata["source_level"] == "official"
    asyncio.run(client.aclose())


def test_xiaohongshu_pgy_help_api_baselines_and_emits_new_rule_with正文(
    tmp_path: Path,
) -> None:
    rows = [
        {
            "shortcutId": "rule-1",
            "title": "小红书社区公约2.0",
            "updateTime": 1786545109751,
            "directory": ["规则公告"],
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/menu"):
            return httpx.Response(
                200,
                json={"success": True, "code": 0, "data": {"menuList": list(rows)}},
                request=request,
            )
        assert request.url.path.endswith("/doc")
        assert request.url.params["shortcutId"] in {"rule-1", "rule-2"}
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {"shortcutId": "rule-1", "title": rows[0]["title"], "content": '{"children":[{"text":"公约正文"}]}'}
            },
            request=request,
        )

    watcher = PlatformChangeWatcherConfig(
        name="xiaohongshu-pgy-rules",
        mode="xiaohongshu_help_api",
        platform="xiaohongshu",
        url="https://pgy.xiaohongshu.com/api/pgy/help/menu",
        source_level="official",
        change_types=["rule", "ecommerce"],
        fetch_limit=30,
        api_role="4",
    )
    client = _client(handler)
    scraper = PlatformChangesScraper(_config(tmp_path, watcher), client, now_provider=lambda: NOW)

    assert _run(scraper) == []
    rows.insert(0, {"shortcutId": "rule-2", "title": "蒲公英商业合作规则更新", "updateTime": 1786545200000, "directory": ["规则公告"]})
    items = _run(scraper)

    assert [item.title for item in items] == ["蒲公英商业合作规则更新"]
    assert "公约正文" in items[0].content
    assert items[0].metadata["source_level"] == "official"
    assert items[0].metadata["discovery_mode"] == "xiaohongshu_help_api"
    asyncio.run(client.aclose())


def test_xiaohongshu_pgy_help_api_emits_same_id_when_title_changes(tmp_path: Path) -> None:
    row = {"shortcutId": "rule-1", "title": "旧规则标题", "updateTime": 1, "directory": ["规则公告"]}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/menu"):
            return httpx.Response(200, json={"code": 0, "data": {"menuList": [dict(row)]}}, request=request)
        return httpx.Response(200, json={"success": True, "data": {"content": '{"children":[{"text":"正文"}]}' }}, request=request)

    watcher = PlatformChangeWatcherConfig(
        name="pgy-change", mode="xiaohongshu_help_api", platform="xiaohongshu",
        url="https://pgy.xiaohongshu.com/api/pgy/help/menu", source_level="official", api_role="4"
    )
    client = _client(handler)
    scraper = PlatformChangesScraper(_config(tmp_path, watcher), client, now_provider=lambda: NOW)
    assert _run(scraper) == []
    row.update(title="新规则标题", updateTime=2)
    items = _run(scraper)
    assert [item.title for item in items] == ["新规则标题"]
    assert items[0].metadata["changed_fields"] == ["title", "update_time"]
    asyncio.run(client.aclose())


def test_xiaohongshu_pgy_update_time_only_without_body_change_is_no_change(tmp_path: Path) -> None:
    row = {"shortcutId": "rule-1", "title": "规则标题", "updateTime": 1, "directory": ["规则公告"]}
    body = '{"children":[{"text":"稳定正文"}]}'
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/menu"):
            return httpx.Response(200, json={"code": 0, "data": {"menuList": [dict(row)]}}, request=request)
        return httpx.Response(200, json={"success": True, "data": {"content": body}}, request=request)
    watcher = PlatformChangeWatcherConfig(name="pgy-time", mode="xiaohongshu_help_api", platform="xiaohongshu", url="https://pgy.xiaohongshu.com/api/pgy/help/menu", source_level="official", api_role="4")
    client = _client(handler); scraper = PlatformChangesScraper(_config(tmp_path, watcher), client, now_provider=lambda: NOW)
    assert _run(scraper) == []
    row["updateTime"] = 2
    assert _run(scraper) == []
    state = json.loads(Path(scraper.state_path).read_text(encoding="utf-8"))
    assert state["watchers"]["pgy-time"]["seen_items"]["rule-1"]["update_time"] == 2
    asyncio.run(client.aclose())


def test_xiaohongshu_pgy_detail_failure_preserves_previous_hash(tmp_path: Path) -> None:
    row = {"shortcutId": "rule-1", "title": "规则标题", "updateTime": 1, "directory": ["规则公告"]}
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/menu"):
            return httpx.Response(200, json={"code": 0, "data": {"menuList": [dict(row)]}}, request=request)
        return httpx.Response(200, json={"success": True, "data": {"content": '{"children":[{"text":"旧正文"}]}' }}, request=request)
    watcher = PlatformChangeWatcherConfig(name="pgy-failure", mode="xiaohongshu_help_api", platform="xiaohongshu", url="https://pgy.xiaohongshu.com/api/pgy/help/menu", source_level="official", api_role="4")
    client = _client(handler); scraper = PlatformChangesScraper(_config(tmp_path, watcher), client, now_provider=lambda: NOW)
    assert _run(scraper) == []
    state_before = json.loads(Path(scraper.state_path).read_text(encoding="utf-8"))["watchers"]["pgy-failure"]["seen_items"]["rule-1"]
    row["updateTime"] = 2
    async def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)
    awaitable = None
    # replace transport for the detail request while keeping menu readable
    def failing_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/menu"):
            return httpx.Response(200, json={"code": 0, "data": {"menuList": [dict(row)]}}, request=request)
        return httpx.Response(503, request=request)
    asyncio.run(client.aclose()); client = _client(failing_handler); scraper = PlatformChangesScraper(_config(tmp_path, watcher), client, now_provider=lambda: NOW)
    assert _run(scraper) == []
    assert scraper.last_watcher_results[0]["status"] == "warning"
    state_after = json.loads(Path(scraper.state_path).read_text(encoding="utf-8"))["watchers"]["pgy-failure"]["seen_items"]["rule-1"]
    assert state_after["fingerprint"] == state_before["fingerprint"]
    asyncio.run(client.aclose())


def test_index_emits_only_new_matching_same_domain_links(tmp_path: Path) -> None:
    body = {'html': '<a href="/notice/1">规则更新一</a>'}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/newhome":
            return httpx.Response(200, text=body["html"], request=request)
        return httpx.Response(200, text="<article>新规则将于8月20日生效</article>", request=request)

    watcher = PlatformChangeWatcherConfig(
        name="xiaohongshu-index",
        mode="index",
        platform="xiaohongshu",
        url="https://school.xiaohongshu.com/newhome",
        include_patterns=[r"/notice/"],
        exclude_patterns=[r"tutorial"],
        change_types=["rule"],
        source_level="official",
    )
    client = _client(handler)
    scraper = PlatformChangesScraper(_config(tmp_path, watcher), client, now_provider=lambda: NOW)
    assert _run(scraper) == []

    body["html"] = """
      <a href="/notice/1">规则更新一</a>
      <a href="/notice/2">创作者规则新增门槛</a>
      <a href="/tutorial/3">直播教程</a>
      <a href="https://outside.example/notice/4">站外转载</a>
    """
    items = _run(scraper)

    assert [item.title for item in items] == ["创作者规则新增门槛"]
    assert items[0].source_type == SourceType.PLATFORM_CHANGES
    assert items[0].profile == "pangmen-platform-change-radar"
    assert items[0].metadata["source_level"] == "official"
    assert items[0].metadata["change_types"] == ["rule"]
    asyncio.run(client.aclose())


def test_index_uses_public_page_timestamp_when_available(tmp_path: Path) -> None:
    body = {"html": '<article><a href="/notice/1">旧规则</a></article>'}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/newhome":
            return httpx.Response(200, text=body["html"], request=request)
        return httpx.Response(200, text="<article>规则正文</article>", request=request)

    watcher = PlatformChangeWatcherConfig(
        name="dated-index",
        mode="index",
        platform="xiaohongshu",
        url="https://school.xiaohongshu.com/newhome",
        include_patterns=[r"/notice/"],
        source_level="official",
    )
    client = _client(handler)
    scraper = PlatformChangesScraper(_config(tmp_path, watcher), client, now_provider=lambda: NOW)
    assert _run(scraper) == []
    body["html"] = """
      <article><a href="/notice/1">旧规则</a></article>
      <article><a href="/notice/2">新规则上线</a><time datetime="2026-08-11T12:30:00+08:00">8月11日</time></article>
    """

    item = _run(scraper)[0]

    assert item.published_at == datetime(2026, 8, 11, 4, 30, tzinfo=timezone.utc)
    assert item.metadata["published_at_basis"] == "page"
    asyncio.run(client.aclose())


def test_page_diff_first_run_baselines_then_ignores_unchanged_text(tmp_path: Path) -> None:
    body = {"html": "<main><h1>B站投稿规范</h1><p>原创内容需要声明。</p></main>"}
    watcher = PlatformChangeWatcherConfig(
        name="bilibili-convention",
        mode="page_diff",
        platform="bilibili",
        url="https://member.bilibili.com/studio/convention/",
        change_types=["rule"],
        source_level="official",
        min_content_chars=10,
    )
    client = _client(lambda request: httpx.Response(200, text=body["html"], request=request))
    config = _config(tmp_path, watcher)
    scraper = PlatformChangesScraper(config, client, now_provider=lambda: NOW)

    assert _run(scraper) == []
    body["html"] = "<main>\n<h1> B站投稿规范 </h1><p>原创内容需要声明。</p>\n</main>"
    assert _run(scraper) == []

    state = json.loads(Path(config.state_file).read_text(encoding="utf-8"))
    saved = state["watchers"]["bilibili-convention"]
    assert saved["normalized_text_hash"]
    assert saved["normalized_text"]
    assert saved["last_changed"] is None
    asyncio.run(client.aclose())


def test_page_diff_change_emits_old_new_and_diff_context(tmp_path: Path) -> None:
    body = {"html": "<main><h1>投稿规则</h1><p>投稿门槛为100粉丝。</p></main>"}
    watcher = PlatformChangeWatcherConfig(
        name="douyin-rule",
        mode="page_diff",
        platform="douyin",
        url="https://open.douyin.com/platform/rule",
        change_types=["operation", "rule"],
        source_level="official",
        min_content_chars=10,
    )
    client = _client(lambda request: httpx.Response(200, text=body["html"], request=request))
    scraper = PlatformChangesScraper(_config(tmp_path, watcher), client, now_provider=lambda: NOW)
    assert _run(scraper) == []

    body["html"] = "<main><h1>投稿规则</h1><p>投稿门槛调整为500粉丝，8月20日生效。</p></main>"
    items = _run(scraper)

    assert len(items) == 1
    assert "旧版关键文本" in (items[0].content or "")
    assert "新版关键文本" in (items[0].content or "")
    assert "-投稿门槛为100粉丝" in items[0].metadata["diff_excerpt"]
    assert "+投稿门槛调整为500粉丝" in items[0].metadata["diff_excerpt"]
    assert items[0].metadata["previous_hash"] != items[0].metadata["current_hash"]
    asyncio.run(client.aclose())


def test_bilibili_bundle_diff_baselines_embedded_convention_then_detects_change(
    tmp_path: Path,
) -> None:
    body = {"rule": "投稿内容需要遵守社区规范。"}

    def bundle() -> str:
        payload = json.dumps(
            [
                {
                    "title": "账号与行为",
                    "children": [
                        {
                            "title": "投稿规范",
                            "contentXML": f"<div><p>{body['rule']}</p></div>",
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        )
        return f"var conventionData=JSON.parse('{payload}');"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/studio/convention/":
            assert "Mozilla" in request.headers["user-agent"]
            return httpx.Response(
                200,
                text=(
                    '<html><script defer src="//s1.hdslb.com/bfs/static/'
                    'creator-monorepo/convention/static/js/index.test.js"></script></html>'
                ),
                request=request,
            )
        return httpx.Response(200, text=bundle(), request=request)

    watcher = PlatformChangeWatcherConfig(
        name="bilibili-community-convention",
        mode="bilibili_bundle_diff",
        platform="bilibili",
        url="https://member.bilibili.com/studio/convention/",
        source_level="official",
        change_types=["operation", "rule"],
        min_content_chars=10,
    )
    client = _client(handler)
    config = _config(tmp_path, watcher)
    scraper = PlatformChangesScraper(config, client, now_provider=lambda: NOW)

    assert _run(scraper) == []
    assert _run(scraper) == []
    body["rule"] = "投稿内容需要遵守新版社区规范，并主动声明AI生成内容。"

    items = _run(scraper)

    assert len(items) == 1
    assert items[0].metadata["discovery_mode"] == "bilibili_bundle_diff"
    assert "+投稿内容需要遵守新版社区规范" in items[0].metadata["diff_excerpt"]
    state = json.loads(Path(config.state_file).read_text(encoding="utf-8"))
    saved = state["watchers"][watcher.name]
    assert saved["snapshot_kind"] == "bilibili_bundle"
    assert "账号与行为" in saved["normalized_text"]
    asyncio.run(client.aclose())


def test_v1_state_preserves_existing_search_seen_urls_when_new_watchers_baseline(
    tmp_path: Path, monkeypatch
) -> None:
    state_path = tmp_path / "platform_change_state.json"
    existing_url = "https://news.example/already-baselined"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "watchers": {
                    "xiaohongshu-public-search": {
                        "seen_urls": {existing_url: "2026-08-12T01:35:00+00:00"},
                        "last_seen": "2026-08-12T01:35:00+00:00",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    discovered = _discovery_item(
        "google_news:seen",
        title="小红书规则更新",
        url=existing_url,
        content="小红书规则已于2026年8月12日更新。",
    )

    class StubGoogleNews:
        def __init__(self, config, client):  # type: ignore[no-untyped-def]
            pass

        async def fetch(self, since):  # type: ignore[no-untyped-def]
            return [discovered]

    monkeypatch.setattr("src.scrapers.platform_changes.GoogleNewsScraper", StubGoogleNews)
    xhs = PlatformChangeWatcherConfig(
        name="xiaohongshu-public-index",
        mode="xiaohongshu_rules",
        platform="xiaohongshu",
        url="https://school.xiaohongshu.com/api/rules",
        source_level="official",
    )
    search = PlatformChangeWatcherConfig(
        name="xiaohongshu-public-search",
        mode="search_rss",
        platform="xiaohongshu",
        query="小红书规则更新",
        source_level="official",
        official_domains=["news.example"],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "success": True,
                "data": {
                    "dataList": [
                        {
                            "articleId": 1,
                            "title": "已存在规则",
                            "createTime": "2026年08月11日",
                        }
                    ]
                },
            },
            request=request,
        )

    client = _client(handler)
    config = PlatformChangesConfig(
        enabled=True,
        lookback_days=7,
        state_file=str(state_path),
        watchers=[xhs, search],
    )

    assert _run(PlatformChangesScraper(config, client, now_provider=lambda: NOW)) == []

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["version"] == 1
    assert existing_url in saved["watchers"]["xiaohongshu-public-search"]["seen_urls"]
    assert saved["watchers"]["xiaohongshu-public-index"]["seen_urls"]
    asyncio.run(client.aclose())


def test_one_watcher_failure_does_not_block_another_watcher(tmp_path: Path) -> None:
    body = {"good": "<main><p>规则旧版本内容。</p></main>"}
    failed = PlatformChangeWatcherConfig(
        name="failed",
        mode="page_diff",
        platform="douyin",
        url="https://open.douyin.com/failed",
        source_level="official",
        min_content_chars=5,
    )
    good = PlatformChangeWatcherConfig(
        name="good",
        mode="page_diff",
        platform="bilibili",
        url="https://member.bilibili.com/good",
        source_level="official",
        min_content_chars=5,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/failed":
            return httpx.Response(403, request=request)
        return httpx.Response(200, text=body["good"], request=request)

    client = _client(handler)
    scraper = PlatformChangesScraper(_config(tmp_path, failed, good), client, now_provider=lambda: NOW)
    assert _run(scraper) == []
    body["good"] = "<main><p>规则新版本新增创作者声明。</p></main>"

    items = _run(scraper)

    assert len(items) == 1
    assert items[0].metadata["watcher"] == "good"
    asyncio.run(client.aclose())


def test_search_rss_uses_seven_day_window_and_seen_url_dedup(tmp_path: Path, monkeypatch) -> None:
    rows = [
        _discovery_item(
            "google_news:old",
            title="抖音规则更新",
            url="https://media.example/old",
            content="抖音规则更新",
            published_at=NOW - timedelta(days=8),
        ),
        _discovery_item(
            "google_news:one",
            title="抖音新增创作者功能",
            url="https://media.example/one",
            content="抖音新增创作者功能",
        ),
    ]
    observed_since: list[datetime] = []

    class StubGoogleNews:
        def __init__(self, config, client):  # type: ignore[no-untyped-def]
            pass

        async def fetch(self, since: datetime) -> list[ContentItem]:
            observed_since.append(since)
            return list(rows)

    monkeypatch.setattr("src.scrapers.platform_changes.GoogleNewsScraper", StubGoogleNews)
    watcher = PlatformChangeWatcherConfig(
        name="douyin-search",
        mode="search_rss",
        platform="douyin",
        query="site:open.douyin.com 抖音 规则 更新",
        source_level="secondary",
        change_types=["feature", "rule"],
    )
    client = _client(lambda request: httpx.Response(500, request=request))
    scraper = PlatformChangesScraper(_config(tmp_path, watcher), client, now_provider=lambda: NOW)

    assert _run(scraper) == []
    rows.append(
        _discovery_item(
            "google_news:two",
            title="抖音上线新的发布能力",
            url="https://media.example/two",
            content="抖音已于2026年8月12日上线新的发布能力",
        )
    )
    items = _run(scraper)

    assert observed_since[0] == NOW - timedelta(days=7)
    assert [str(item.url) for item in items] == ["https://media.example/two"]
    assert _run(scraper) == []
    asyncio.run(client.aclose())


def test_search_rss_seen_state_normalizes_tracking_url_variants(
    tmp_path: Path, monkeypatch
) -> None:
    rows = [
        _discovery_item(
            "google_news:baseline",
            title="抖音创作者规则更新",
            url="https://open.douyin.com/notice/42?utm_source=google",
            content="抖音创作者规则已于2026年8月11日更新。",
        )
    ]

    class StubGoogleNews:
        def __init__(self, config, client):  # type: ignore[no-untyped-def]
            pass

        async def fetch(self, since):  # type: ignore[no-untyped-def]
            return list(rows)

    monkeypatch.setattr("src.scrapers.platform_changes.GoogleNewsScraper", StubGoogleNews)
    watcher = PlatformChangeWatcherConfig(
        name="douyin-search-normalized",
        mode="search_rss",
        platform="douyin",
        query="site:open.douyin.com 抖音 规则 更新",
        source_level="official",
        official_domains=["open.douyin.com"],
    )
    client = _client(lambda request: httpx.Response(500, request=request))
    config = _config(tmp_path, watcher)
    scraper = PlatformChangesScraper(config, client, now_provider=lambda: NOW)

    assert _run(scraper) == []
    rows[:] = [
        _discovery_item(
            "google_news:same-event-new-tracking",
            title="抖音创作者规则更新",
            url="https://OPEN.DOUYIN.COM/notice/42#latest",
            content="抖音创作者规则已于2026年8月11日更新。",
        )
    ]

    assert _run(scraper) == []
    state = json.loads(Path(config.state_file).read_text(encoding="utf-8"))
    assert list(state["watchers"][watcher.name]["seen_urls"]) == [
        "https://open.douyin.com/notice/42"
    ]
    asyncio.run(client.aclose())


def test_search_rss_query_change_baselines_history_then_accepts_new_url(tmp_path: Path, monkeypatch) -> None:
    rows = [_discovery_item("old", title="抖音历史规则更新", url="https://media.example/old", content="抖音规则更新")]
    class StubGoogleNews:
        def __init__(self, config, client):
            pass
        async def fetch(self, since):
            return list(rows)
    monkeypatch.setattr("src.scrapers.platform_changes.GoogleNewsScraper", StubGoogleNews)
    watcher = PlatformChangeWatcherConfig(name="search", mode="search_rss", platform="douyin", query="扩展查询词", source_level="secondary")
    state_path = tmp_path / "platform_change_state.json"
    state_path.write_text(json.dumps({"version": 1, "watchers": {"search": {"seen_urls": {"https://media.example/already": {"status": "candidate_emitted"}}}}}), encoding="utf-8")
    scraper = PlatformChangesScraper(_config(tmp_path, watcher), _client(lambda request: httpx.Response(500, request=request)), now_provider=lambda: NOW)
    assert _run(scraper) == []
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["watchers"]["search"]["query_fingerprint"]
    rows.append(_discovery_item("new", title="抖音新规则更新", url="https://media.example/new", content="抖音规则更新"))
    assert [str(item.url) for item in _run(scraper)] == ["https://media.example/new"]
    rows.append(_discovery_item("newer", title="抖音新功能上线", url="https://media.example/newer", content="抖音新功能上线"))
    assert {str(item.url) for item in _run(scraper)} == {"https://media.example/new", "https://media.example/newer"}


def test_search_rss_reports_url_dedup_and_qualified_candidates_separately(tmp_path: Path, monkeypatch) -> None:
    rows = [
        _discovery_item("old", title="抖音历史规则更新", url="https://media.example/old", content="抖音规则更新", published_at=NOW - timedelta(days=8)),
        _discovery_item("irrelevant", title="天气预报", url="https://media.example/weather", content="天气预报"),
        _discovery_item("fresh", title="抖音新功能上线", url="https://media.example/fresh", content="抖音新功能上线"),
        _discovery_item("fresh-duplicate", title="抖音新功能上线", url="https://media.example/fresh?utm_source=x", content="抖音新功能上线"),
    ]
    class StubGoogleNews:
        def __init__(self, config, client): pass
        async def fetch(self, since): return list(rows)
    monkeypatch.setattr("src.scrapers.platform_changes.GoogleNewsScraper", StubGoogleNews)
    watcher = PlatformChangeWatcherConfig(name="stats", mode="search_rss", platform="douyin", query="固定", source_level="secondary")
    scraper = PlatformChangesScraper(_config(tmp_path, watcher), _client(lambda request: httpx.Response(500, request=request)), now_provider=lambda: NOW)
    _run(scraper)
    health = scraper.last_watcher_results[0]
    assert health["url_dedup_count"] == 3
    assert health["qualified_candidate_count"] == 0


def test_search_rss_reprocesses_same_url_when_discovery_fingerprint_changes(
    tmp_path: Path, monkeypatch
) -> None:
    row = _discovery_item(
        "google_news:baseline",
        title="抖音创作者规则更新",
        url="https://open.douyin.com/notice/42",
        content="抖音创作者规则已于2026年8月11日更新。",
    )
    rows = [row]

    class StubGoogleNews:
        def __init__(self, config, client):  # type: ignore[no-untyped-def]
            pass

        async def fetch(self, since):  # type: ignore[no-untyped-def]
            return list(rows)

    monkeypatch.setattr("src.scrapers.platform_changes.GoogleNewsScraper", StubGoogleNews)
    watcher = PlatformChangeWatcherConfig(
        name="douyin-search-fingerprint",
        mode="search_rss",
        platform="douyin",
        query="site:open.douyin.com 抖音 规则 更新",
        source_level="official",
        official_domains=["open.douyin.com"],
    )
    client = _client(lambda request: httpx.Response(500, request=request))
    config = _config(tmp_path, watcher)
    scraper = PlatformChangesScraper(config, client, now_provider=lambda: NOW)

    assert _run(scraper) == []
    rows[:] = [
        _discovery_item(
            "google_news:title-changed",
            title="抖音创作者规则再次调整",
            url="https://open.douyin.com/notice/42",
            content="抖音创作者规则已于2026年8月11日更新。",
        )
    ]

    assert [item.title for item in _run(scraper)] == ["抖音创作者规则再次调整"]
    assert _run(scraper) == []
    rows[:] = [
        _discovery_item(
            "google_news:content-changed",
            title="抖音创作者规则再次调整",
            url="https://open.douyin.com/notice/42",
            content="抖音创作者规则已于2026年8月11日更新，新增AI内容声明要求。",
        )
    ]

    assert len(_run(scraper)) == 1
    assert _run(scraper) == []
    rows[:] = [
        _discovery_item(
            "google_news:change-date-changed",
            title="抖音创作者规则再次调整",
            url="https://open.douyin.com/notice/42",
            content="抖音创作者规则已于2026年8月12日更新，新增AI内容声明要求。",
        )
    ]

    date_changed = _run(scraper)

    assert len(date_changed) == 1
    assert date_changed[0].metadata["actual_change_at"] == "2026-08-11T16:00:00+00:00"
    assert _run(scraper) == []
    rows[:] = [
        _discovery_item(
            "google_news:article-date-changed",
            title="抖音创作者规则再次调整",
            url="https://open.douyin.com/notice/42",
            content="抖音创作者规则已于2026年8月12日更新，新增AI内容声明要求。",
            published_at=NOW + timedelta(hours=1),
        )
    ]

    assert len(_run(scraper)) == 1
    asyncio.run(client.aclose())


def test_search_rss_retries_unchanged_unconfirmed_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    rows = [
        _discovery_item(
            "google_news:baseline",
            title="微信小店规则更新汇总",
            url="https://media.example/baseline",
            content="微信小店规则更新汇总。",
        )
    ]

    class StubGoogleNews:
        def __init__(self, config, client):  # type: ignore[no-untyped-def]
            pass

        async def fetch(self, since):  # type: ignore[no-untyped-def]
            return list(rows)

    monkeypatch.setattr("src.scrapers.platform_changes.GoogleNewsScraper", StubGoogleNews)
    watcher = PlatformChangeWatcherConfig(
        name="wechat-search-retry-unconfirmed",
        mode="search_rss",
        platform="wechat",
        query="微信公开课 微信小店 更新",
        source_level="official_republished",
        attribution_keywords=["微信公开课"],
    )
    client = _client(lambda request: httpx.Response(500, request=request))
    scraper = PlatformChangesScraper(_config(tmp_path, watcher), client, now_provider=lambda: NOW)
    assert _run(scraper) == []
    rows.append(
        _discovery_item(
            "google_news:unconfirmed",
            title="微信小店上线新功能",
            url="https://media.example/unconfirmed",
            content="微信公开课介绍微信小店上线新功能，但未说明实际上线日期。",
        )
    )

    assert len(_run(scraper)) == 1
    assert len(_run(scraper)) == 1
    asyncio.run(client.aclose())


def test_search_rss_retries_prior_resolution_and_fetch_failures(
    tmp_path: Path, monkeypatch
) -> None:
    state_path = tmp_path / "platform_change_state.json"
    urls = {
        "https://open.douyin.com/notice/resolution": "resolution_failed",
        "https://open.douyin.com/notice/fetch": "fetch_failed",
    }
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "watchers": {
                    "douyin-search-retry-failures": {
                        "seen_urls": {
                            url: {
                                "first_seen": NOW.isoformat(),
                                "last_seen": NOW.isoformat(),
                                "fingerprint": "unchanged-test-fingerprint",
                                "status": status,
                            }
                            for url, status in urls.items()
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    rows = [
        _discovery_item(
            f"google_news:{status}",
            title=f"抖音规则更新 {status}",
            url=url,
            content="抖音规则已于2026年8月12日更新。",
        )
        for url, status in urls.items()
    ]

    class StubGoogleNews:
        def __init__(self, config, client):  # type: ignore[no-untyped-def]
            pass

        async def fetch(self, since):  # type: ignore[no-untyped-def]
            return list(rows)

    monkeypatch.setattr("src.scrapers.platform_changes.GoogleNewsScraper", StubGoogleNews)
    watcher = PlatformChangeWatcherConfig(
        name="douyin-search-retry-failures",
        mode="search_rss",
        platform="douyin",
        query="site:open.douyin.com 抖音 规则 更新",
        source_level="official",
        official_domains=["open.douyin.com"],
    )
    client = _client(lambda request: httpx.Response(500, request=request))
    config = PlatformChangesConfig(
        enabled=True,
        lookback_days=7,
        state_file=str(state_path),
        watchers=[watcher],
    )

    assert {str(item.url) for item in _run(PlatformChangesScraper(config, client, now_provider=lambda: NOW))} == set(urls)
    asyncio.run(client.aclose())


def test_search_rss_migrates_v1_string_entry_without_reemitting_history(
    tmp_path: Path, monkeypatch
) -> None:
    state_path = tmp_path / "platform_change_state.json"
    url = "https://open.douyin.com/notice/legacy"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "watchers": {
                    "douyin-search-legacy": {
                        "seen_urls": {url: "2026-08-12T01:35:00+00:00"}
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    row = _discovery_item(
        "google_news:legacy",
        title="抖音规则更新",
        url=url,
        content="抖音规则已于2026年8月11日更新。",
    )

    class StubGoogleNews:
        def __init__(self, config, client):  # type: ignore[no-untyped-def]
            pass

        async def fetch(self, since):  # type: ignore[no-untyped-def]
            return [row]

    monkeypatch.setattr("src.scrapers.platform_changes.GoogleNewsScraper", StubGoogleNews)
    watcher = PlatformChangeWatcherConfig(
        name="douyin-search-legacy",
        mode="search_rss",
        platform="douyin",
        query="site:open.douyin.com 抖音 规则 更新",
        source_level="official",
        official_domains=["open.douyin.com"],
    )
    client = _client(lambda request: httpx.Response(500, request=request))
    config = PlatformChangesConfig(
        enabled=True,
        lookback_days=7,
        state_file=str(state_path),
        watchers=[watcher],
    )

    assert _run(PlatformChangesScraper(config, client, now_provider=lambda: NOW)) == []

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["version"] == 1
    assert state["watchers"][watcher.name]["seen_urls"][url]["status"] == "baseline"
    asyncio.run(client.aclose())


def test_search_rss_skips_old_change_found_in_a_new_article(
    tmp_path: Path, monkeypatch
) -> None:
    rows = [
        _discovery_item(
            "google_news:baseline",
            title="抖音规则更新汇总",
            url="https://open.douyin.com/notice/baseline",
            content="抖音规则更新汇总。",
        )
    ]

    class StubGoogleNews:
        def __init__(self, config, client):  # type: ignore[no-untyped-def]
            pass

        async def fetch(self, since):  # type: ignore[no-untyped-def]
            return list(rows)

    monkeypatch.setattr("src.scrapers.platform_changes.GoogleNewsScraper", StubGoogleNews)
    watcher = PlatformChangeWatcherConfig(
        name="douyin-search-old-change",
        mode="search_rss",
        platform="douyin",
        query="site:open.douyin.com 抖音 规则 更新",
        source_level="official",
        official_domains=["open.douyin.com"],
    )
    client = _client(lambda request: httpx.Response(500, request=request))
    scraper = PlatformChangesScraper(_config(tmp_path, watcher), client, now_provider=lambda: NOW)

    assert _run(scraper) == []
    rows.append(
        _discovery_item(
            "google_news:old-change-new-article",
            title="抖音旧版投稿能力回顾",
            url="https://open.douyin.com/notice/old-change",
            content="该投稿能力已于2025年12月1日上线，今天被搜索引擎重新收录。",
            published_at=NOW,
        )
    )

    assert _run(scraper) == []
    asyncio.run(client.aclose())


def test_search_rss_marks_actual_change_time_as_unconfirmed_when_missing(
    tmp_path: Path, monkeypatch
) -> None:
    rows = [
        _discovery_item(
            "google_news:baseline",
            title="微信小店规则更新汇总",
            url="https://media.example/baseline",
            content="微信小店规则更新汇总。",
        )
    ]

    class StubGoogleNews:
        def __init__(self, config, client):  # type: ignore[no-untyped-def]
            pass

        async def fetch(self, since):  # type: ignore[no-untyped-def]
            return list(rows)

    monkeypatch.setattr("src.scrapers.platform_changes.GoogleNewsScraper", StubGoogleNews)
    watcher = PlatformChangeWatcherConfig(
        name="wechat-search-unconfirmed-time",
        mode="search_rss",
        platform="wechat",
        query="微信公开课 微信小店 更新",
        source_level="official_republished",
        attribution_keywords=["微信公开课"],
    )
    client = _client(lambda request: httpx.Response(500, request=request))
    scraper = PlatformChangesScraper(_config(tmp_path, watcher), client, now_provider=lambda: NOW)

    assert _run(scraper) == []
    rows.append(
        _discovery_item(
            "google_news:unconfirmed-time",
            title="微信小店上线新功能",
            url="https://media.example/unconfirmed-time",
            content="微信公开课介绍微信小店上线新功能，但未说明实际上线日期。",
            published_at=NOW,
        )
    )

    item = _run(scraper)[0]

    assert item.metadata["article_published_at"] == NOW.isoformat()
    assert item.metadata["change_time_confidence"] == "unconfirmed"
    assert "actual_change_at" not in item.metadata
    asyncio.run(client.aclose())


def test_official_republished_requires_explicit_attribution(tmp_path: Path, monkeypatch) -> None:
    rows = [
        _discovery_item(
            "google_news:wx",
            title="微信小店调整带货规则",
            url="https://media.example/wx",
            content="来源：微信公开课。微信小店调整带货规则。",
            author="运营媒体",
        )
    ]

    class StubGoogleNews:
        def __init__(self, config, client):  # type: ignore[no-untyped-def]
            pass

        async def fetch(self, since):  # type: ignore[no-untyped-def]
            return list(rows)

    monkeypatch.setattr("src.scrapers.platform_changes.GoogleNewsScraper", StubGoogleNews)
    watcher = PlatformChangeWatcherConfig(
        name="wechat-search",
        mode="search_rss",
        platform="wechat",
        query="微信公开课 微信小店 更新",
        source_level="official_republished",
        attribution_keywords=["微信公开课", "微信派"],
        change_types=["ecommerce", "rule"],
    )
    client = _client(lambda request: httpx.Response(500, request=request))
    scraper = PlatformChangesScraper(_config(tmp_path, watcher), client, now_provider=lambda: NOW)
    assert _run(scraper) == []

    rows.append(
        _discovery_item(
            "google_news:wx2",
            title="微信小店上线新功能",
            url="https://media.example/wx2",
            content="微信公开课宣布微信小店上线新功能。",
            author="另一媒体",
        )
    )
    rows.append(
        _discovery_item(
            "google_news:wx3",
            title="用户发现视频号灰度功能",
            url="https://media.example/wx3",
            content="有用户发现视频号灰度功能。",
            author="行业观察",
        )
    )
    items = _run(scraper)

    levels = {item.id: item.metadata["source_level"] for item in items}
    assert levels["platform_changes:search_rss:google_news:wx2"] == "official_republished"
    assert levels["platform_changes:search_rss:google_news:wx3"] == "secondary"
    assert items[0].metadata["source_attribution"] == "微信公开课，经另一媒体转述"
    asyncio.run(client.aclose())


def test_unverified_search_results_do_not_enter_candidates(tmp_path: Path, monkeypatch) -> None:
    row = _discovery_item(
        "google_news:x",
        title="小红书疑似新增功能",
        url="https://unknown.example/x",
        content="小红书疑似新增功能",
    )

    class StubGoogleNews:
        def __init__(self, config, client):  # type: ignore[no-untyped-def]
            pass

        async def fetch(self, since):  # type: ignore[no-untyped-def]
            return [row]

    monkeypatch.setattr("src.scrapers.platform_changes.GoogleNewsScraper", StubGoogleNews)
    watcher = PlatformChangeWatcherConfig(
        name="unverified",
        mode="search_rss",
        platform="xiaohongshu",
        query="小红书 更新",
        source_level="unverified",
    )
    client = _client(lambda request: httpx.Response(500, request=request))
    scraper = PlatformChangesScraper(_config(tmp_path, watcher), client, now_provider=lambda: NOW)

    assert _run(scraper) == []
    asyncio.run(client.aclose())


def _processed_change_item(platform: str, source_level: str = "official") -> ContentItem:
    return ContentItem(
        id=f"platform_changes:page_diff:{platform}",
        source_type=SourceType.PLATFORM_CHANGES,
        title=f"{platform}发布规则变化",
        url=f"https://example.com/{platform}",
        published_at=NOW,
        profile="pangmen-platform-change-radar",
        metadata={
            "platform": platform,
            "change_types": ["rule"],
            "source_level": source_level,
            "source_attribution": "微信公开课，经运营媒体转述"
            if source_level == "official_republished"
            else None,
        },
        processing=ProcessingResult(
            classification=ClassificationResult(
                profile="pangmen-platform-change-radar",
                method="source_override",
            ),
            analysis=ContentAnalysis(
                score=8,
                reason="规则发生明确变化",
                summary="平台调整了创作者规则。",
                tags=["规则"],
            ),
            artifacts={
                "zh": ContentArtifact(
                    language="zh",
                    title=f"{platform}创作者规则调整",
                    blocks=[
                        ContentBlock(
                            id="what_changed",
                            title="变了什么",
                            content="创作者发布门槛由100粉丝调整为500粉丝。",
                            primary=True,
                        ),
                        ContentBlock(
                            id="affected_audience",
                            title="影响谁",
                            content="内容创作者与账号运营人员。",
                        ),
                        ContentBlock(
                            id="change_status",
                            title="状态",
                            content="8月20日生效。",
                        ),
                    ],
                )
            },
        ),
    )


def test_summary_shows_only_platforms_with_real_changes() -> None:
    item = _processed_change_item("xiaohongshu")
    summarizer = DailySummarizer(
        profile_names={"pangmen-platform-change-radar": {"zh": "平台变化雷达"}},
        profile_order=[
            "pangmen-topic-radar",
            "pangmen-ai-tech-radar",
            "pangmen-platform-trend-radar",
            "pangmen-platform-change-radar",
        ],
    )

    summary = asyncio.run(summarizer.generate_summary([item], "2026-08-12", 1, "zh"))

    assert "## 📡 平台变化雷达" in summary
    assert "### 【小红书】" in summary
    assert "【抖音】" not in summary
    assert "**变了什么：**" in summary
    assert "**影响谁：**" in summary
    assert "**状态：**" in summary
    assert "**来源：** 官方" in summary


def test_summary_does_not_render_platform_change_section_without_items() -> None:
    summarizer = DailySummarizer(
        profile_order=[
            "pangmen-topic-radar",
            "pangmen-ai-tech-radar",
            "pangmen-platform-trend-radar",
            "pangmen-platform-change-radar",
        ]
    )

    summary = asyncio.run(summarizer.generate_summary([], "2026-08-12", 0, "zh"))

    assert "平台变化雷达" not in summary


def test_secondary_card_never_labels_the_source_as_official() -> None:
    item = _processed_change_item("wechat", source_level="secondary")
    card = DailySummarizer().generate_webhook_item(
        item,
        language="zh",
        index=1,
        total=1,
    )

    assert "**来源：** 二手待确认" in card
    assert "**来源：** 官方" not in card


def test_platform_change_prompts_receive_trusted_source_and_diff_metadata() -> None:
    profile = ProfileRegistry.load(
        Path(__file__).resolve().parents[1] / "profiles", "tech-news"
    ).get("pangmen-platform-change-radar")
    item = _processed_change_item("wechat", source_level="secondary")
    item.metadata.update(
        {
            "discovery_mode": "search_rss",
            "article_published_at": "2026-08-12T01:15:00+00:00",
            "change_time_confidence": "unconfirmed",
            "diff_excerpt": "-100粉丝\n+500粉丝",
        }
    )

    system = analysis_system_prompt(profile)
    user = analysis_user_prompt(item, "Content: diff", "")
    enrichment = item_context(item, profile, include_content=True)

    assert '"is_platform_change"' in system
    assert '"source_level"' in system
    assert '"change_types"' in system
    assert '"source_level": "secondary"' in user
    assert '"article_published_at": "2026-08-12T01:15:00+00:00"' in user
    assert '"change_time_confidence": "unconfirmed"' in user
    assert '"diff_excerpt": "-100粉丝\\n+500粉丝"' in user
    assert "搜索引擎当天发现或文章当天发布，不等于平台当天发生变化" in system
    assert '"platform": "wechat"' in enrichment


def test_content_analysis_accepts_platform_change_decision_fields() -> None:
    analysis = ContentAnalysis(
        score=8,
        reason="规则门槛变化",
        summary="门槛由100调整到500。",
        is_platform_change=True,
        platform="douyin",
        change_types=["operation", "rule"],
        source_level="official",
        affected_audience=["创作者", "运营"],
        impact_level="high",
        change_status="8月20日生效",
    )

    assert analysis.is_platform_change is True
    assert analysis.change_types == ["operation", "rule"]


def test_feishu_content_radar_renders_change_platforms_and_hides_empty_section() -> None:
    item = _processed_change_item("wechat", source_level="official_republished")
    summarizer = DailySummarizer(
        profile_names={"pangmen-platform-change-radar": {"zh": "平台变化雷达"}},
        profile_order=[
            "pangmen-topic-radar",
            "pangmen-ai-tech-radar",
            "pangmen-platform-trend-radar",
            "pangmen-platform-change-radar",
        ],
    )
    notifier = WebhookNotifier(
        WebhookConfig(
            enabled=True,
            platform="feishu",
            layout="collapsible",
            url_env="TEST_WEBHOOK_URL",
        )
    )

    assert notifier._is_content_radar_digest([item]) is True
    body = notifier._build_feishu_collapsible_body(
        important_items=[item],
        all_items_count=1,
        date="2026-08-12",
        lang="zh",
        summarizer=summarizer,
        topic_radar=False,
        content_radar=True,
    )
    rendered = json.dumps(body, ensure_ascii=False)
    assert "## 📡 平台变化雷达" in rendered
    assert "### 【视频号 / 微信小店】" in rendered
    assert "微信公开课，经运营媒体转述" in rendered

    empty_body = notifier._build_feishu_collapsible_body(
        important_items=[],
        all_items_count=0,
        date="2026-08-12",
        lang="zh",
        summarizer=summarizer,
        topic_radar=False,
        content_radar=True,
    )
    assert "平台变化雷达" not in json.dumps(empty_body, ensure_ascii=False)

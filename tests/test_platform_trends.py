import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models import PlatformTrendProviderConfig, PlatformTrendsConfig, SourceType
from src.scrapers.platform_trends import PlatformTrendsScraper


SINCE = datetime(2026, 8, 9, tzinfo=timezone.utc)


def _response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_newsnow_weibo_rank_and_provenance_enter_metadata():
    client = AsyncMock()
    client.get.return_value = _response(
        {
            "updatedTime": 1786294379668,
            "items": [
                {
                    "id": "hybrid-work",
                    "title": "年轻人开始流行混合办公",
                    "url": "https://s.weibo.com/weibo?q=hybrid",
                    "hotValue": 987654,
                }
            ],
        }
    )
    config = PlatformTrendsConfig(
        enabled=True,
        providers=[
            PlatformTrendProviderConfig(
                platform="weibo",
                provider="newsnow",
                base_url="https://newsnow.busiyi.world/api/s",
                source_id="weibo",
            )
        ],
    )

    items = asyncio.run(PlatformTrendsScraper(config, client).fetch(SINCE))

    assert len(items) == 1
    item = items[0]
    assert item.source_type == SourceType.PLATFORM_TRENDS
    assert item.profile == "pangmen-platform-trend-radar"
    assert item.metadata["platform"] == "weibo"
    assert item.metadata["rank"] == 1
    assert item.metadata["hot_value"] == 987654
    assert item.metadata["engagement"] == {"hot_value": 987654}
    assert item.metadata["provider"] == "newsnow"
    assert item.metadata["source_kind"] == "aggregator"
    assert item.metadata["source_tier"] == "core"
    assert item.metadata["provider_role"] == "core_collection"
    assert item.metadata["original_url"] == "https://s.weibo.com/weibo?q=hybrid"


def test_newsnow_douyin_without_hot_value_is_only_a_ranked_candidate():
    client = AsyncMock()
    client.get.return_value = _response(
        {
            "items": [
                {
                    "id": "2603000",
                    "title": "一个新的职场表达梗",
                    "url": "https://www.douyin.com/hot/2603000",
                }
            ]
        }
    )
    config = PlatformTrendsConfig(
        enabled=True,
        providers=[
            PlatformTrendProviderConfig(
                platform="douyin",
                provider="newsnow",
                base_url="https://newsnow.busiyi.world/api/s",
                source_id="douyin",
            )
        ],
    )

    item = asyncio.run(PlatformTrendsScraper(config, client).fetch(SINCE))[0]

    assert item.metadata["rank"] == 1
    assert item.metadata["hot_value"] is None
    assert "榜单第 1 位" in item.content
    assert "全网爆火" not in item.content


def test_title_only_hotlist_row_uses_explicit_search_fallback_url():
    client = AsyncMock()
    client.get.return_value = _response(
        {
            "items": [
                {
                    "title": "没有原文链接的热榜标题",
                    "hot": 123,
                }
            ]
        }
    )
    provider = PlatformTrendProviderConfig(
        platform="douyin",
        provider="fixture",
        base_url="https://provider.example/douyin",
    )

    item = asyncio.run(
        PlatformTrendsScraper(
            PlatformTrendsConfig(enabled=True, providers=[provider]), client
        ).fetch(SINCE)
    )[0]

    assert item.metadata["url_is_search_fallback"] is True
    assert item.metadata["source_url_kind"] == "search_fallback"
    assert "douyin.com/search" in str(item.url)


def test_alapi_non_core_platform_is_marked_supplemental_discovery():
    client = AsyncMock()
    client.post.return_value = _response(
        {
            "code": 200,
            "data": {
                "list": [
                    {
                        "title": "职场沟通新趋势",
                        "url": "https://www.zhihu.com/question/example",
                        "hot": 980000,
                    }
                ]
            },
        }
    )
    config = PlatformTrendsConfig(
        enabled=True,
        providers=[
            PlatformTrendProviderConfig(
                platform="zhihu",
                provider="alapi_tophub",
                base_url="https://v3.alapi.cn",
                endpoint="/api/tophub",
                request_method="POST",
                response_adapter="alapi_tophub",
                source_id="BMzQOL",
                api_key_env="TEST_ALAPI_TOKEN",
            )
        ],
    )

    import os

    os.environ["TEST_ALAPI_TOKEN"] = "test-token"
    try:
        item = asyncio.run(PlatformTrendsScraper(config, client).fetch(SINCE))[0]
    finally:
        del os.environ["TEST_ALAPI_TOKEN"]

    assert item.metadata["source_tier"] == "supplemental"
    assert item.metadata["provider_role"] == "supplemental_discovery"


def test_one_platform_provider_failure_is_gracefully_skipped():
    client = AsyncMock()
    client.get.side_effect = [
        RuntimeError("weibo unavailable"),
        _response(
            {
                "items": [
                    {
                        "id": "ok",
                        "title": "大学生新型学习方式",
                        "url": "https://www.douyin.com/hot/ok",
                    }
                ]
            }
        ),
    ]
    providers = [
        PlatformTrendProviderConfig(
            platform="weibo",
            provider="newsnow",
            base_url="https://newsnow.busiyi.world/api/s",
            source_id="weibo",
        ),
        PlatformTrendProviderConfig(
            platform="douyin",
            provider="newsnow",
            base_url="https://newsnow.busiyi.world/api/s",
            source_id="douyin",
        ),
    ]

    items = asyncio.run(
        PlatformTrendsScraper(
            PlatformTrendsConfig(enabled=True, providers=providers), client
        ).fetch(SINCE)
    )

    assert len(items) == 1
    assert items[0].metadata["platform"] == "douyin"


def test_missing_optional_provider_key_is_skipped(monkeypatch):
    monkeypatch.delenv("XIAOHONGSHU_TREND_API_KEY", raising=False)
    client = AsyncMock()
    config = PlatformTrendsConfig(
        enabled=True,
        providers=[
            PlatformTrendProviderConfig(
                platform="xiaohongshu",
                provider="configurable",
                base_url="https://provider.example/trends",
                api_key_env="XIAOHONGSHU_TREND_API_KEY",
            )
        ],
    )

    assert asyncio.run(PlatformTrendsScraper(config, client).fetch(SINCE)) == []
    client.get.assert_not_awaited()


def test_dailyhot_data_shape_is_parsed_with_hot_value():
    client = AsyncMock()
    client.get.return_value = _response(
        {
            "code": 200,
            "updateTime": "2026-08-09T17:26:40Z",
            "data": [
                {
                    "title": "职场人开始反向使用周报",
                    "hot": 11875925,
                    "url": "https://www.douyin.com/hot/2602902",
                }
            ],
        }
    )
    provider = PlatformTrendProviderConfig(
        platform="douyin",
        provider="dailyhotapi_public_instance",
        base_url="https://dailyhotapi.vercel.app/douyin",
    )

    item = asyncio.run(
        PlatformTrendsScraper(
            PlatformTrendsConfig(enabled=True, providers=[provider]), client
        ).fetch(SINCE)
    )[0]

    assert item.metadata["hot_value"] == 11875925
    assert item.published_at.isoformat() == "2026-08-09T17:26:40+00:00"


@pytest.mark.parametrize("platform", ["xiaohongshu", "wechat"])
def test_configurable_provider_parses_xiaohongshu_and_wechat_fixtures(platform):
    client = AsyncMock()
    client.get.return_value = _response(
        {
            "items": [
                {
                    "id": "topic-1",
                    "title": "年轻人的新型工作表达",
                    "url": "https://provider.example/topic-1",
                    "hot_value": 321,
                }
            ]
        }
    )
    provider = PlatformTrendProviderConfig(
        platform=platform,
        provider="licensed_provider_fixture",
        base_url="https://provider.example/trends",
    )

    item = asyncio.run(
        PlatformTrendsScraper(
            PlatformTrendsConfig(enabled=True, providers=[provider]), client
        ).fetch(SINCE)
    )[0]

    assert item.metadata["platform"] == platform
    assert item.metadata["provider"] == "licensed_provider_fixture"
    assert item.metadata["rank"] == 1


def test_stale_platform_snapshot_is_not_presented_as_current_trend():
    client = AsyncMock()
    client.get.return_value = _response(
        {
            "updateTime": "2026-08-01T00:00:00Z",
            "data": [
                {
                    "title": "已经过期的榜单话题",
                    "url": "https://example.com/stale",
                    "hot": 999,
                }
            ],
        }
    )
    provider = PlatformTrendProviderConfig(
        platform="douyin",
        provider="fixture",
        base_url="https://provider.example/douyin",
    )

    items = asyncio.run(
        PlatformTrendsScraper(
            PlatformTrendsConfig(enabled=True, providers=[provider]), client
        ).fetch(SINCE)
    )

    assert items == []


def test_alapi_tophub_adapter_posts_token_and_parses_nested_list(monkeypatch):
    monkeypatch.setenv("ALAPI_TOKEN", "test-token")
    client = AsyncMock()
    client.post.return_value = _response(
        {
            "code": 200,
            "success": True,
            "data": {
                "name": "抖音 - 热点榜",
                "last_update": "2026-08-09 17:26:40",
                "list": [
                    {
                        "title": "年轻人开始反向使用周报",
                        "link": "https://www.douyin.com/hot/2602902",
                        "other": "776万",
                    }
                ],
            },
        }
    )
    provider = PlatformTrendProviderConfig(
        platform="douyin",
        provider="alapi_tophub",
        provider_name="ALAPI",
        base_url="https://v3.alapi.cn",
        endpoint="/api/tophub",
        request_method="POST",
        response_adapter="alapi_tophub",
        source_id="BOoYax",
        api_key_env="ALAPI_TOKEN",
        api_key_header="token",
        api_key_prefix="",
        observed_timezone="Asia/Shanghai",
    )

    item = asyncio.run(
        PlatformTrendsScraper(
            PlatformTrendsConfig(enabled=True, providers=[provider]), client
        ).fetch(SINCE)
    )[0]

    client.post.assert_awaited_once()
    request = client.post.await_args
    assert request.kwargs["headers"] == {"token": "test-token"}
    assert request.kwargs["json"] == {"id": "BOoYax"}
    assert item.metadata["provider"] == "alapi_tophub"
    assert item.metadata["providers"] == ["ALAPI"]
    assert item.metadata["hot_value"] == 7_760_000
    assert item.published_at.isoformat() == "2026-08-09T09:26:40+00:00"


def test_provider_health_records_success_counts_and_update_time(monkeypatch):
    monkeypatch.setenv("ALAPI_TOKEN", "test-token")
    client = AsyncMock()
    response = _response(
        {
            "code": 200,
            "success": True,
            "data": {
                "last_update": "2026-08-09 17:26:40",
                "list": [
                    {
                        "title": "可核验榜单内容",
                        "link": "https://www.douyin.com/hot/ok",
                        "other": "100万",
                    }
                ],
            },
        }
    )
    response.status_code = 200
    client.post.return_value = response
    provider = PlatformTrendProviderConfig(
        platform="douyin",
        provider="alapi_tophub",
        base_url="https://v3.alapi.cn",
        endpoint="/api/tophub",
        request_method="POST",
        response_adapter="alapi_tophub",
        source_id="BOoYax",
        api_key_env="ALAPI_TOKEN",
        api_key_header="token",
        api_key_prefix="",
    )

    scraper = PlatformTrendsScraper(
        PlatformTrendsConfig(enabled=True, providers=[provider]), client
    )
    items = asyncio.run(scraper.fetch(SINCE))

    assert len(items) == 1
    assert scraper.last_provider_health == [
        {
            "provider": "alapi_tophub",
            "provider_name": "alapi_tophub",
            "platform": "douyin",
            "status": "ok",
            "item_count": 1,
                "latest_visible_at": "2026-08-09T17:26:40+00:00",
            "title_count": 1,
            "url_count": 1,
            "hot_value_count": 1,
            "http_status": 200,
            "error": None,
        }
    ]


def test_provider_business_error_is_recorded_without_blocking_other_provider(caplog):
    client = AsyncMock()
    client.get.side_effect = [
        _response({"code": 401, "success": False, "data": {}}),
        _response(
            {
                "items": [
                    {
                        "title": "可用来源内容",
                        "url": "https://example.com/ok",
                    }
                ]
            }
        ),
    ]
    providers = [
        PlatformTrendProviderConfig(
            platform="weibo",
            provider="provider_a",
            base_url="https://provider.example/a",
        ),
        PlatformTrendProviderConfig(
            platform="douyin",
            provider="provider_b",
            base_url="https://provider.example/b",
        ),
    ]
    scraper = PlatformTrendsScraper(
        PlatformTrendsConfig(enabled=True, providers=providers), client
    )

    items = asyncio.run(scraper.fetch(SINCE))

    assert [item.title for item in items] == ["可用来源内容"]
    assert [health["status"] for health in scraper.last_provider_health] == [
        "business_error",
        "ok",
    ]


def test_missing_alapi_token_is_gracefully_skipped(monkeypatch, caplog):
    monkeypatch.delenv("ALAPI_TOKEN", raising=False)
    client = AsyncMock()
    provider = PlatformTrendProviderConfig(
        platform="weibo",
        provider="alapi_tophub",
        provider_name="ALAPI",
        base_url="https://v3.alapi.cn",
        endpoint="/api/tophub",
        request_method="POST",
        response_adapter="alapi_tophub",
        source_id="BaXJOg",
        api_key_env="ALAPI_TOKEN",
        api_key_header="token",
        api_key_prefix="",
    )

    items = asyncio.run(
        PlatformTrendsScraper(
            PlatformTrendsConfig(enabled=True, providers=[provider]), client
        ).fetch(SINCE)
    )

    assert items == []
    client.post.assert_not_awaited()
    assert "ALAPI_TOKEN not configured, skipping alapi_tophub provider" in caplog.text


def test_same_platform_topic_from_two_providers_merges_without_cross_platform(monkeypatch):
    monkeypatch.setenv("ALAPI_TOKEN", "test-token")
    client = AsyncMock()
    client.get.return_value = _response(
        {
            "code": 200,
            "updateTime": "2026-08-09T17:26:40Z",
            "data": [
                {
                    "title": "宇树科技启动申购",
                    "url": "https://www.douyin.com/hot/unitree",
                    "hot": 7_760_000,
                }
            ],
        }
    )
    client.post.return_value = _response(
        {
            "code": 200,
            "data": {
                "last_update": "2026-08-10 01:26:40",
                "list": [
                    {
                        "title": "宇树科技启动申购",
                        "link": "https://alapi.example/unitree",
                        "other": "776万",
                    }
                ],
            },
        }
    )
    providers = [
        PlatformTrendProviderConfig(
            platform="douyin",
            provider="dailyhotapi_public_instance",
            provider_name="DailyHotAPI",
            base_url="https://dailyhotapi.vercel.app/douyin",
            response_adapter="dailyhotapi",
        ),
        PlatformTrendProviderConfig(
            platform="douyin",
            provider="alapi_tophub",
            provider_name="ALAPI",
            base_url="https://v3.alapi.cn",
            endpoint="/api/tophub",
            request_method="POST",
            response_adapter="alapi_tophub",
            source_id="BOoYax",
            api_key_env="ALAPI_TOKEN",
            api_key_header="token",
            api_key_prefix="",
            observed_timezone="Asia/Shanghai",
        ),
    ]

    items = asyncio.run(
        PlatformTrendsScraper(
            PlatformTrendsConfig(enabled=True, providers=providers), client
        ).fetch(SINCE)
    )

    assert len(items) == 1
    assert items[0].metadata["providers"] == ["DailyHotAPI", "ALAPI"]
    assert items[0].metadata["platforms"] == ["douyin"]
    assert items[0].metadata["cross_platform_count"] == 1
    assert len(items[0].metadata["platform_occurrences"]) == 2


def test_same_topic_on_two_platforms_preserves_cross_platform_occurrences():
    client = AsyncMock()
    client.get.side_effect = [
        _response(
            {
                "items": [
                    {
                        "title": "年轻人开始反向使用周报",
                        "url": "https://weibo.example/reverse-weekly",
                    }
                ]
            }
        ),
        _response(
            {
                "items": [
                    {
                        "title": "年轻人开始反向使用周报",
                        "url": "https://douyin.example/reverse-weekly",
                    }
                ]
            }
        ),
    ]
    providers = [
        PlatformTrendProviderConfig(
            platform="weibo",
            provider="provider_a",
            provider_name="Provider A",
            base_url="https://provider.example/weibo",
        ),
        PlatformTrendProviderConfig(
            platform="douyin",
            provider="provider_b",
            provider_name="Provider B",
            base_url="https://provider.example/douyin",
        ),
    ]

    items = asyncio.run(
        PlatformTrendsScraper(
            PlatformTrendsConfig(enabled=True, providers=providers), client
        ).fetch(SINCE)
    )

    assert len(items) == 1
    assert items[0].metadata["platforms"] == ["weibo", "douyin"]
    assert items[0].metadata["cross_platform_count"] == 2
    assert items[0].metadata["providers"] == ["Provider A", "Provider B"]

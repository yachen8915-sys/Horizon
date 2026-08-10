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

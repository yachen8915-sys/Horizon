import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from rich.console import Console

from src.models import (
    AIConfig,
    CategoryGroupConfig,
    ClassificationResult,
    Config,
    ContentAnalysis,
    ContentItem,
    DigestConfig,
    EditorialSelectionConfig,
    ProcessingConfig,
    ProcessingResult,
    ProfileSettingsConfig,
    SourceType,
    SourcesConfig,
)
from src.orchestrator import HorizonOrchestrator
from src.processing import ProfileRegistry
from src.processing.editorial_selection import EditorialSelector


def make_item(
    item_id: str,
    score: float,
    category: str | None,
    profile: str = "tech-news",
) -> ContentItem:
    metadata = {"category": category} if category is not None else {}
    return ContentItem(
        id=item_id,
        source_type=SourceType.RSS,
        title=item_id,
        url=f"https://example.com/{item_id}",
        published_at=datetime.now(timezone.utc),
        metadata=metadata,
        profile=profile,
        processing=ProcessingResult(
            classification=ClassificationResult(
                profile=profile, method="source_override"
            ),
            analysis=ContentAnalysis(
                score=score, reason="test", summary=item_id
            ),
        ),
    )


def make_orchestrator(digest: DigestConfig) -> HorizonOrchestrator:
    orchestrator = HorizonOrchestrator.__new__(HorizonOrchestrator)
    orchestrator.config = SimpleNamespace(
        digest=digest,
        processing=ProcessingConfig(
            profile_settings={
                "tech-news": ProfileSettingsConfig(threshold=7.0),
                "tech-blog": ProfileSettingsConfig(
                    threshold=4.0, topic_dedup=False
                ),
                "finance-news": ProfileSettingsConfig(threshold=7.0),
                "pangmen-platform-trend-radar": ProfileSettingsConfig(
                    threshold=7.0
                ),
            }
        ),
    )
    orchestrator.console = Console(record=True)
    return orchestrator


def test_unconfigured_balanced_digest_preserves_old_behavior() -> None:
    items = [make_item("lower", 7.0, "ai"), make_item("higher", 9.0, "finance")]
    result = make_orchestrator(DigestConfig()).apply_balanced_digest(items)

    assert result.enabled is False
    assert result.items is items


def test_category_groups_apply_limits_and_default_group_limit() -> None:
    filtering = DigestConfig(
        category_groups={
            "ai": CategoryGroupConfig(limit=2, categories=["ai", "ml"]),
            "finance": CategoryGroupConfig(limit=1, categories=["finance"]),
        },
        default_group_limit=1,
    )
    items = [
        make_item("ai-low", 7.0, "ai"),
        make_item("finance-low", 6.0, "finance"),
        make_item("other-high", 9.5, "world"),
        make_item("ai-high", 9.0, "ml"),
        make_item("finance-high", 8.5, "finance"),
        make_item("ai-mid", 8.0, "ai"),
        make_item("other-low", 5.0, None),
    ]

    result = make_orchestrator(filtering).apply_balanced_digest(items)

    assert [item.id for item in result.items] == [
        "other-high",
        "ai-high",
        "finance-high",
        "ai-mid",
    ]
    assert result.group_counts == {"other": 1, "ai": 2, "finance": 1}


def test_max_items_applies_after_group_limits() -> None:
    filtering = DigestConfig(
        max_items=2,
        category_groups={
            "ai": CategoryGroupConfig(limit=2, categories=["ai"]),
            "finance": CategoryGroupConfig(limit=2, categories=["finance"]),
        },
    )
    items = [
        make_item("finance", 8.0, "finance"),
        make_item("ai-top", 10.0, "ai"),
        make_item("ai-second", 9.0, "ai"),
    ]

    result = make_orchestrator(filtering).apply_balanced_digest(items)

    assert [item.id for item in result.items] == ["ai-top", "ai-second"]
    assert result.group_counts == {"ai": 2}


def test_max_items_works_without_category_groups() -> None:
    filtering = DigestConfig(max_items=1)
    items = [make_item("lower", 7.0, None), make_item("higher", 9.0, None)]

    result = make_orchestrator(filtering).apply_balanced_digest(items)

    assert [item.id for item in result.items] == ["higher"]


def test_profile_limits_are_upper_bounds_not_fill_quotas() -> None:
    filtering = DigestConfig(
        profile_limits={
            "pangmen-topic-radar": 2,
            "pangmen-ai-tech-radar": 1,
            "pangmen-platform-trend-radar": 2,
        }
    )
    items = [
        make_item("app-1", 10, "ai", "pangmen-topic-radar"),
        make_item("app-2", 9, "ai", "pangmen-topic-radar"),
        make_item("app-3", 8, "ai", "pangmen-topic-radar"),
        make_item("tech-1", 7, "ai-tech", "pangmen-ai-tech-radar"),
    ]

    result = make_orchestrator(filtering).apply_balanced_digest(items)

    assert [item.id for item in result.items] == ["app-1", "app-2", "tech-1"]
    assert all(
        item.processing.classification.profile != "pangmen-platform-trend-radar"
        for item in result.items
    )


def test_semantic_dedup_merges_cross_platform_occurrences(monkeypatch) -> None:
    orchestrator = make_orchestrator(DigestConfig())
    orchestrator.config.ai = AIConfig(
        provider="deepseek", model="deepseek-chat", api_key_env="TEST_KEY"
    )
    weibo = make_item(
        "weibo-topic", 9, "platform-trend", "pangmen-platform-trend-radar"
    )
    weibo.metadata["platform_occurrences"] = [
        {"platform": "weibo", "rank": 2, "url": "https://weibo.example/topic"}
    ]
    douyin = make_item(
        "douyin-topic", 8, "platform-trend", "pangmen-platform-trend-radar"
    )
    douyin.metadata["platform_occurrences"] = [
        {"platform": "douyin", "rank": 5, "url": "https://douyin.example/topic"}
    ]

    class FakeAIClient:
        async def complete(self, **kwargs):  # type: ignore[no-untyped-def]
            return '{"duplicates": [[0, 1]]}'

    monkeypatch.setattr("src.orchestrator.create_ai_client", lambda config: FakeAIClient())

    result = asyncio.run(
        orchestrator.merge_topic_duplicates([weibo, douyin], log=False)
    )

    assert len(result) == 1
    assert [row["platform"] for row in result[0].metadata["platform_occurrences"]] == [
        "weibo",
        "douyin",
    ]
    assert result[0].metadata["cross_platform_count"] == 2


def test_semantic_dedup_keeps_provider_confirmation_separate_from_platforms(
    monkeypatch,
) -> None:
    orchestrator = make_orchestrator(DigestConfig())
    orchestrator.config.ai = AIConfig(
        provider="deepseek", model="deepseek-chat", api_key_env="TEST_KEY"
    )
    dailyhot = make_item(
        "dailyhot-topic", 9, "platform-trend", "pangmen-platform-trend-radar"
    )
    dailyhot.metadata["platform_occurrences"] = [
        {
            "platform": "weibo",
            "provider": "DailyHotAPI",
            "rank": 2,
            "url": "https://weibo.example/topic",
        }
    ]
    alapi = make_item(
        "alapi-topic", 8, "platform-trend", "pangmen-platform-trend-radar"
    )
    alapi.metadata["platform_occurrences"] = [
        {
            "platform": "weibo",
            "provider": "ALAPI",
            "rank": 3,
            "url": "https://alapi.example/topic",
        }
    ]

    class FakeAIClient:
        async def complete(self, **kwargs):  # type: ignore[no-untyped-def]
            return '{"duplicates": [[0, 1]]}'

    monkeypatch.setattr("src.orchestrator.create_ai_client", lambda config: FakeAIClient())

    result = asyncio.run(
        orchestrator.merge_topic_duplicates([dailyhot, alapi], log=False)
    )

    assert len(result) == 1
    assert result[0].metadata["platforms"] == ["weibo"]
    assert result[0].metadata["providers"] == ["DailyHotAPI", "ALAPI"]
    assert result[0].metadata["cross_platform_count"] == 1


def test_semantic_dedup_does_not_merge_distinct_model_publishers(monkeypatch) -> None:
    orchestrator = make_orchestrator(DigestConfig())
    orchestrator.config.ai = AIConfig(
        provider="deepseek", model="deepseek-chat", api_key_env="TEST_KEY"
    )
    deepseek = make_item(
        "deepseek-v4", 9, "platform-trend", "pangmen-platform-trend-radar"
    )
    deepseek.title = "DeepSeek V4 Pro 正式版发布"
    nvidia = make_item(
        "nvidia-model", 8, "platform-trend", "pangmen-platform-trend-radar"
    )
    nvidia.title = "英伟达最新开源大模型上线"

    class FakeAIClient:
        async def complete(self, **kwargs):  # type: ignore[no-untyped-def]
            return '{"duplicates": [[0, 1]]}'

    monkeypatch.setattr("src.orchestrator.create_ai_client", lambda config: FakeAIClient())

    result = asyncio.run(
        orchestrator.merge_topic_duplicates([deepseek, nvidia], log=False)
    )

    assert [item.id for item in result] == ["deepseek-v4", "nvidia-model"]


def test_semantic_dedup_ignores_invalid_and_overlapping_groups(monkeypatch) -> None:
    orchestrator = make_orchestrator(DigestConfig())
    orchestrator.config.ai = AIConfig(
        provider="deepseek", model="deepseek-chat", api_key_env="TEST_KEY"
    )
    first = make_item("first", 9, "ai")
    second = make_item("second", 8, "ai")
    third = make_item("third", 7, "ai")

    class FakeAIClient:
        async def complete(self, **kwargs):  # type: ignore[no-untyped-def]
            return '{"duplicates": [[0, 1], [true, 2], [1, 2], [99, 0]]}'

    monkeypatch.setattr("src.orchestrator.create_ai_client", lambda config: FakeAIClient())

    result = asyncio.run(
        orchestrator.merge_topic_duplicates([first, second, third], log=False)
    )

    assert [item.id for item in result] == ["first", "third"]


def test_semantic_dedup_records_exact_event_repeat_for_ai_profiles(monkeypatch) -> None:
    orchestrator = make_orchestrator(DigestConfig())
    orchestrator.config.ai = AIConfig(
        provider="deepseek", model="deepseek-chat", api_key_env="TEST_KEY"
    )
    official = make_item(
        "official-chatgpt-teens",
        8,
        "ai",
        "pangmen-topic-radar",
    )
    repost = make_item(
        "repost-chatgpt-teens",
        7,
        "ai",
        "pangmen-topic-radar",
    )
    for item in (official, repost):
        item.title = "ChatGPT for Teens 发布"
        item.processing.analysis = item.processing.analysis.model_copy(
            update={
                "primary_entity": "chatgpt",
                "topic_cluster": "ai_education",
                "use_case": "teen_learning",
                "content_format": "product_release",
                "novelty_level": "major_release",
                "event_key": "openai_chatgpt_for_teens_launch",
                "editorial_key": "chatgpt|teen_learning|product_release",
                "relevance_score": 8,
                "novelty_score": 8,
                "demonstrability_score": 8,
            }
        )
    requests = []

    class FakeAIClient:
        async def complete(self, **kwargs):  # type: ignore[no-untyped-def]
            requests.append(kwargs)
            return '{"duplicates": [[0, 1]]}'

    monkeypatch.setattr("src.orchestrator.create_ai_client", lambda config: FakeAIClient())

    result = asyncio.run(
        orchestrator.merge_topic_duplicates([official, repost], log=False)
    )

    assert [item.id for item in result] == ["official-chatgpt-teens"]
    assert "Event key: openai_chatgpt_for_teens_launch" in requests[0]["user"]
    assert repost.metadata["editorial_selection"] == {
        "reason": "exact_event_repeat",
        "replaced_by_id": "official-chatgpt-teens",
    }


def test_platform_trends_share_dynamic_global_cap_with_ai_topics() -> None:
    filtering = DigestConfig(max_items=25)
    items = [
        make_item(
            f"trend-{index}",
            10 - index / 100,
            "platform-trend",
            "pangmen-platform-trend-radar",
        )
        for index in range(20)
    ]
    items.extend(
        make_item(f"app-{index}", 9.9 - index / 100, "ai", "pangmen-topic-radar")
        for index in range(20)
    )

    result = make_orchestrator(filtering).apply_balanced_digest(items)

    assert len(result.items) == 25
    trend_count = sum(
        item.processing.classification.profile == "pangmen-platform-trend-radar"
        for item in result.items
    )
    app_count = sum(
        item.processing.classification.profile == "pangmen-topic-radar"
        for item in result.items
    )
    assert trend_count > 6
    assert app_count > 0
    assert trend_count + app_count == 25


def test_unbounded_ai_profiles_are_not_cut_by_global_cap() -> None:
    filtering = DigestConfig(
        max_items=3,
        profile_limits={"pangmen-platform-trend-radar": 2},
        unbounded_profiles=["pangmen-topic-radar", "pangmen-ai-tech-radar"],
    )
    items = [
        make_item(f"app-{index}", 9 - index / 10, "ai", "pangmen-topic-radar")
        for index in range(5)
    ]
    items.extend(
        make_item(f"trend-{index}", 8, "trend", "pangmen-platform-trend-radar")
        for index in range(2)
    )

    result = make_orchestrator(filtering).apply_balanced_digest(items)

    assert sum(
        item.processing.classification.profile == "pangmen-topic-radar"
        for item in result.items
    ) == 5
    assert sum(
        item.processing.classification.profile == "pangmen-platform-trend-radar"
        for item in result.items
    ) <= 2


def test_platform_trend_items_use_heat_as_a_tiebreaker() -> None:
    filtering = DigestConfig(max_items=1)
    lower_heat = make_item(
        "trend-lower-heat",
        6.0,
        "platform-trend",
        "pangmen-platform-trend-radar",
    )
    lower_heat.metadata.update({"platform": "weibo", "rank": 24, "hot_value": 390_286})
    higher_heat = make_item(
        "trend-higher-heat",
        6.0,
        "platform-trend",
        "pangmen-platform-trend-radar",
    )
    higher_heat.metadata.update({"platform": "douyin", "rank": 9, "hot_value": 9_247_000})

    result = make_orchestrator(filtering).apply_balanced_digest(
        [lower_heat, higher_heat]
    )

    assert [item.id for item in result.items] == ["trend-higher-heat"]


def _set_trend_scores(
    item: ContentItem,
    *,
    operations: float,
    content: float,
) -> ContentItem:
    item.processing.analysis.operations_score = operations
    item.processing.analysis.content_opportunity_score = content
    item.processing.analysis.score = operations
    return item


def test_high_operations_low_content_enters_watch_pool() -> None:
    filtering = DigestConfig(
        profile_limits={"pangmen-platform-trend-radar": 8},
        platform_trend_leverage_limit=6,
        platform_trend_watch_limit=4,
    )
    item = _set_trend_scores(
        make_item(
            "hundred-flowers-awards",
            8.5,
            "platform-trend",
            "pangmen-platform-trend-radar",
        ),
        operations=8.5,
        content=4.5,
    )
    item.title = "百花奖获奖名单"
    item.metadata.update(
        {"platform": "weibo", "rank": 8, "hot_value": 8_510_000}
    )

    result = make_orchestrator(filtering).apply_balanced_digest([item])

    assert [entry.id for entry in result.items] == ["hundred-flowers-awards"]
    assert result.items[0].metadata["trend_pool"] == "watch"


def test_low_operations_high_content_does_not_pass_platform_threshold() -> None:
    item = _set_trend_scores(
        make_item(
            "easy-ppt-low-heat",
            6.0,
            "platform-trend",
            "pangmen-platform-trend-radar",
        ),
        operations=6.0,
        content=9.0,
    )

    assert make_orchestrator(DigestConfig()).passes_profile_filter(item) is False


def test_high_heat_disaster_is_excluded_even_with_high_operations_score() -> None:
    item = _set_trend_scores(
        make_item(
            "typhoon-risk",
            8.0,
            "platform-trend",
            "pangmen-platform-trend-radar",
        ),
        operations=8.0,
        content=5.0,
    )
    item.title = "台风白海豚突然大拐弯"
    item.processing.analysis.tags = ["自然灾害", "公共安全"]

    assert make_orchestrator(DigestConfig()).passes_profile_filter(item) is False
    assert item.metadata["trend_excluded_reason"] == "brand_safety"


def test_platform_pool_limits_are_independent_upper_bounds() -> None:
    filtering = DigestConfig(
        profile_limits={
            "pangmen-topic-radar": 12,
            "pangmen-ai-tech-radar": 5,
            "pangmen-platform-trend-radar": 8,
        },
        max_items=25,
        platform_trend_leverage_limit=6,
        platform_trend_watch_limit=4,
    )
    items = [
        make_item(f"app-{index}", 10 - index / 100, "ai", "pangmen-topic-radar")
        for index in range(20)
    ]
    items.extend(
        make_item(
            f"tech-{index}",
            9.5 - index / 100,
            "ai-tech",
            "pangmen-ai-tech-radar",
        )
        for index in range(10)
    )
    for index in range(6):
        items.append(
            _set_trend_scores(
                make_item(
                    f"leverage-{index}",
                    8 - index / 100,
                    "platform-trend",
                    "pangmen-platform-trend-radar",
                ),
                operations=8 - index / 100,
                content=8,
            )
        )
    for index in range(6):
        items.append(
            _set_trend_scores(
                make_item(
                    f"watch-{index}",
                    7 - index / 100,
                    "platform-trend",
                    "pangmen-platform-trend-radar",
                ),
                operations=7 - index / 100,
                content=4,
            )
        )

    result = make_orchestrator(filtering).apply_balanced_digest(items)
    profiles = [entry.processing.classification.profile for entry in result.items]
    platform_items = [
        entry
        for entry in result.items
        if entry.processing.classification.profile
        == "pangmen-platform-trend-radar"
    ]

    assert profiles.count("pangmen-topic-radar") == 12
    assert profiles.count("pangmen-ai-tech-radar") == 5
    assert len(platform_items) == 8
    assert sum(entry.metadata["trend_pool"] == "leverage" for entry in platform_items) == 6
    assert sum(entry.metadata["trend_pool"] == "watch" for entry in platform_items) == 2


def test_supplemental_source_does_not_crowd_equal_value_core_source() -> None:
    filtering = DigestConfig(
        profile_limits={"pangmen-platform-trend-radar": 1},
        platform_trend_leverage_limit=1,
        platform_trend_watch_limit=1,
    )
    supplemental = _set_trend_scores(
        make_item(
            "zhihu-supplemental",
            8,
            "platform-trend",
            "pangmen-platform-trend-radar",
        ),
        operations=8,
        content=8,
    )
    supplemental.metadata.update(
        {"platform": "zhihu", "source_tier": "supplemental", "rank": 1}
    )
    core = _set_trend_scores(
        make_item(
            "weibo-core",
            8,
            "platform-trend",
            "pangmen-platform-trend-radar",
        ),
        operations=8,
        content=8,
    )
    core.metadata.update(
        {"platform": "weibo", "source_tier": "core", "rank": 8}
    )

    result = make_orchestrator(filtering).apply_balanced_digest(
        [supplemental, core]
    )

    assert [entry.id for entry in result.items] == ["weibo-core"]


def test_platform_trend_digest_keeps_one_candidate_per_platform() -> None:
    filtering = DigestConfig(
        max_items=4,
        profile_limits={"pangmen-platform-trend-radar": 4},
        platform_trend_leverage_limit=4,
        platform_trend_watch_limit=4,
        platform_trend_minimum_per_platform=1,
    )
    items = []
    for index in range(4):
        item = _set_trend_scores(
            make_item(
                f"douyin-{index}",
                9.0 - index / 100,
                "platform-trend",
                "pangmen-platform-trend-radar",
            ),
            operations=9.0 - index / 100,
            content=8.0,
        )
        item.metadata.update({"platform": "douyin", "rank": index + 1})
        items.append(item)
    weibo = _set_trend_scores(
        make_item(
            "weibo-1",
            8.5,
            "platform-trend",
            "pangmen-platform-trend-radar",
        ),
        operations=8.5,
        content=8.0,
    )
    weibo.metadata.update({"platform": "weibo", "rank": 1})
    items.append(weibo)

    result = make_orchestrator(filtering).apply_balanced_digest(items)

    assert {item.metadata["platform"] for item in result.items} == {
        "douyin",
        "weibo",
    }
    assert len(result.items) == 4


def test_unconfigured_profiles_only_fill_space_left_by_independent_radar_caps() -> None:
    filtering = DigestConfig(
        max_items=3,
        profile_limits={
            "pangmen-topic-radar": 1,
            "pangmen-platform-trend-radar": 2,
        },
        platform_trend_leverage_limit=2,
        platform_trend_watch_limit=1,
    )
    generic = make_item("generic-high", 10, "other", "tech-news")
    app = make_item("app", 8, "ai", "pangmen-topic-radar")
    trend_one = _set_trend_scores(
        make_item(
            "trend-one",
            7,
            "platform-trend",
            "pangmen-platform-trend-radar",
        ),
        operations=7,
        content=8,
    )
    trend_two = _set_trend_scores(
        make_item(
            "trend-two",
            6,
            "platform-trend",
            "pangmen-platform-trend-radar",
        ),
        operations=6,
        content=8,
    )

    result = make_orchestrator(filtering).apply_balanced_digest(
        [generic, app, trend_one, trend_two]
    )

    assert {entry.id for entry in result.items} == {"app", "trend-one", "trend-two"}


def test_filter_items_skips_ai_topic_dedup_for_disabled_profile(monkeypatch) -> None:
    orchestrator = make_orchestrator(DigestConfig())
    orchestrator.profiles = ProfileRegistry.load(
        Path(__file__).resolve().parents[1] / "profiles", "tech-news"
    )
    items = [make_item("first", 9.0, "blog"), make_item("second", 8.0, "blog")]
    for item in items:
        item.profile = "tech-blog"
        item.processing.classification.profile = "tech-blog"

    async def unexpected_dedup(input_items, *, log=True):  # type: ignore[no-untyped-def]
        raise AssertionError("AI topic dedup must be skipped for tech-blog")

    monkeypatch.setattr(orchestrator, "merge_topic_duplicates", unexpected_dedup)

    result = asyncio.run(
        orchestrator.filter_items(items, topic_dedup=True, apply_balance=False, log=False)
    )

    assert [item.id for item in result.items] == ["first", "second"]


def test_filter_items_applies_editorial_diversity_before_balanced_digest(tmp_path) -> None:
    filtering = DigestConfig(
        profile_limits={"pangmen-topic-radar": 8},
        editorial_selection=EditorialSelectionConfig(
            enabled=True,
            state_file=str(tmp_path / "digest-selection-state.json"),
        ),
    )
    orchestrator = make_orchestrator(filtering)
    items = []
    for item_id, relevance in (
        ("gemini-bts", 7.0),
        ("gemini-sat", 9.0),
        ("gemini-chrome", 8.0),
    ):
        item = make_item(
            item_id,
            8.0,
            "ai",
            "pangmen-topic-radar",
        )
        item.metadata["feed_name"] = "google-ai"
        item.processing.analysis = item.processing.analysis.model_copy(
            update={
                "primary_entity": "gemini",
                "topic_cluster": f"topic-{item_id}",
                "use_case": f"case-{item_id}",
                "content_format": "feature_update",
                "novelty_level": "material_update",
                "event_key": f"event-{item_id}",
                "editorial_key": f"gemini|case-{item_id}|feature_update",
                "relevance_score": relevance,
                "novelty_score": relevance,
                "demonstrability_score": relevance,
            }
        )
        items.append(item)

    result = asyncio.run(
        orchestrator.filter_items(
            list(reversed(items)),
            topic_dedup=False,
            apply_balance=False,
            log=False,
        )
    )

    assert [item.id for item in result.items] == ["gemini-sat", "gemini-chrome"]
    assert result.exclusion_stages["gemini-bts"] == "diversity_entity_limit"


def test_editorial_diagnostics_include_selected_rows_labels_and_limit_details(tmp_path) -> None:
    filtering = DigestConfig(
        profile_limits={"pangmen-topic-radar": 8},
        editorial_selection=EditorialSelectionConfig(
            enabled=True,
            state_file=str(tmp_path / "digest-selection-state.json"),
            primary_entity_limit=1,
        ),
    )
    orchestrator = make_orchestrator(filtering)
    selected = make_item("gemini-best", 8.0, "ai", "pangmen-topic-radar")
    rejected = make_item("gemini-extra", 8.0, "ai", "pangmen-topic-radar")
    for item, relevance in ((selected, 9.0), (rejected, 7.0)):
        item.metadata["feed_name"] = "google-ai"
        item.processing.analysis = item.processing.analysis.model_copy(
            update={
                "primary_entity": "gemini",
                "topic_cluster": "ai_education",
                "use_case": item.id,
                "content_format": "feature_update",
                "novelty_level": "material_update",
                "event_key": f"event-{item.id}",
                "editorial_key": f"gemini|{item.id}|feature_update",
                "relevance_score": relevance,
                "novelty_score": relevance,
                "demonstrability_score": relevance,
            }
        )

    filtered = asyncio.run(
        orchestrator.filter_items(
            [rejected, selected],
            topic_dedup=False,
            apply_balance=False,
            log=False,
        )
    )
    diagnostics = orchestrator.build_selection_diagnostics(
        [selected, rejected],
        filtered.items,
        exclusion_stages=filtered.exclusion_stages,
    )

    row = diagnostics["items"][0]
    assert row["stage"] == "diversity_entity_limit"
    assert row["reason"] == "diversity_entity_limit"
    assert row["primary_entity"] == "gemini"
    assert row["topic_cluster"] == "ai_education"
    assert row["use_case"] == "gemini-extra"
    assert row["content_format"] == "feature_update"
    assert row["event_key"] == "event-gemini-extra"
    assert row["editorial_key"] == "gemini|gemini-extra|feature_update"
    assert row["replaced_by_id"] == "gemini-best"
    assert row["limit_key"] == "primary_entity:gemini"
    assert row["limit_value"] == 1
    assert diagnostics["selected_items"][0]["id"] == "gemini-best"
    assert diagnostics["selected_items"][0]["score"] == 8.0


def test_editorial_selection_does_not_lower_threshold_to_fill_topic_limit(tmp_path) -> None:
    filtering = DigestConfig(
        profile_limits={"pangmen-topic-radar": 8},
        editorial_selection=EditorialSelectionConfig(
            enabled=True,
            state_file=str(tmp_path / "digest-selection-state.json"),
        ),
    )
    orchestrator = make_orchestrator(filtering)
    orchestrator.config.processing.profile_settings["pangmen-topic-radar"] = (
        ProfileSettingsConfig(threshold=7.0, topic_dedup=False)
    )
    qualified = make_item("qualified", 8.0, "ai", "pangmen-topic-radar")
    weak = make_item("weak", 6.9, "ai", "pangmen-topic-radar")
    for item in (qualified, weak):
        item.processing.analysis = item.processing.analysis.model_copy(
            update={
                "primary_entity": item.id,
                "topic_cluster": f"topic-{item.id}",
                "use_case": f"case-{item.id}",
                "content_format": "feature_update",
                "novelty_level": "new_example",
                "event_key": f"event-{item.id}",
                "editorial_key": f"{item.id}|case-{item.id}|feature_update",
                "relevance_score": 8,
                "novelty_score": 8,
                "demonstrability_score": 8,
            }
        )

    result = asyncio.run(
        orchestrator.filter_items(
            [weak, qualified],
            topic_dedup=False,
            apply_balance=True,
            log=False,
        )
    )

    assert [item.id for item in result.items] == ["qualified"]
    diagnostics = orchestrator.build_selection_diagnostics(
        [weak, qualified],
        result.items,
        exclusion_stages=result.exclusion_stages,
    )
    assert diagnostics["items"][0]["reason"] == "below_profile_threshold"


def test_mixed_editorial_selection_preserves_non_target_relative_order(tmp_path) -> None:
    selector = EditorialSelector(
        EditorialSelectionConfig(
            enabled=True,
            state_file=str(tmp_path / "digest-selection-state.json"),
            primary_entity_limit=1,
        )
    )
    first_trend = make_item(
        "first-trend", 8.0, "trend", "pangmen-platform-trend-radar"
    )
    second_trend = make_item(
        "second-trend", 8.0, "trend", "pangmen-platform-trend-radar"
    )
    low = make_item("gemini-low", 8.0, "ai", "pangmen-topic-radar")
    high = make_item("gemini-high", 8.0, "ai", "pangmen-topic-radar")
    for item, relevance in ((low, 7), (high, 9)):
        item.processing.analysis = item.processing.analysis.model_copy(
            update={
                "primary_entity": "gemini",
                "topic_cluster": f"topic-{item.id}",
                "use_case": f"case-{item.id}",
                "content_format": "feature_update",
                "novelty_level": "material_update",
                "event_key": f"event-{item.id}",
                "editorial_key": f"gemini|case-{item.id}|feature_update",
                "relevance_score": relevance,
                "novelty_score": relevance,
                "demonstrability_score": relevance,
            }
        )

    result = selector.select([first_trend, low, second_trend, high])

    assert [item.id for item in result.items] == [
        "first-trend",
        "second-trend",
        "gemini-high",
    ]


def test_august_19_replay_gives_every_rejected_item_an_explicit_reason(tmp_path) -> None:
    rows = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "editorial_replay_2026-08-19.json"
        ).read_text(encoding="utf-8")
    )
    filtering = DigestConfig(
        profile_limits={"pangmen-topic-radar": 8},
        editorial_selection=EditorialSelectionConfig(
            enabled=True,
            state_file=str(tmp_path / "digest-selection-state.json"),
        ),
    )
    orchestrator = make_orchestrator(filtering)
    items = []
    for row in rows:
        item = make_item(row["id"], row["score"], "ai", "pangmen-topic-radar")
        item.title = row["title"]
        item.url = row["url"]
        item.content = row["body_evidence"]
        item.author = row["sub_source"]
        item.metadata["feed_name"] = row["sub_source"]
        item.processing.analysis = item.processing.analysis.model_copy(
            update={
                "primary_entity": row["primary_entity"],
                "topic_cluster": row["topic_cluster"],
                "use_case": row["use_case"],
                "content_format": row["content_format"],
                "novelty_level": row["novelty_level"],
                "event_key": row["event_key"],
                "editorial_key": "ignored-by-selector",
                "relevance_score": row["relevance_score"],
                "novelty_score": row["novelty_score"],
                "demonstrability_score": row["demonstrability_score"],
            }
        )
        items.append(item)

    filtered = asyncio.run(
        orchestrator.filter_items(
            items,
            threshold=7.0,
            topic_dedup=False,
            apply_balance=True,
            log=False,
        )
    )
    diagnostics = orchestrator.build_selection_diagnostics(
        items,
        filtered.items,
        threshold=7.0,
        exclusion_stages=filtered.exclusion_stages,
    )

    selected_analysis = [item.processing.analysis for item in filtered.items]
    assert len(filtered.items) == 8
    assert sum(a.primary_entity == "gemini" for a in selected_analysis) == 2
    assert sum(a.topic_cluster == "ai_short_drama" for a in selected_analysis) == 1
    assert sum(a.content_format == "tutorial_workflow" for a in selected_analysis) <= 2
    assert len({a.topic_cluster for a in selected_analysis}) >= 4
    assert len(diagnostics["items"]) == 4
    assert sorted(row["reason"] for row in diagnostics["items"]) == [
        "diversity_entity_limit",
        "diversity_semantic_limit",
        "diversity_semantic_limit",
        "diversity_semantic_limit",
    ]
    assert all(row["replaced_by_id"] for row in diagnostics["items"][:3])


def test_runtime_threshold_override_takes_priority() -> None:
    orchestrator = make_orchestrator(DigestConfig())
    item = make_item("item", 7.5, "ai")

    assert orchestrator.passes_profile_filter(item)
    assert not orchestrator.passes_profile_filter(item, threshold=8.0)


def test_profile_without_threshold_bypasses_score_filter() -> None:
    orchestrator = make_orchestrator(DigestConfig())
    orchestrator.config.processing.profile_settings = {}

    assert orchestrator.passes_profile_filter(make_item("item", 1.0, "ai"))


def test_selection_diagnostics_records_why_analyzed_items_were_excluded() -> None:
    orchestrator = make_orchestrator(DigestConfig())
    selected = make_item("selected", 8.0, "ai")
    below_threshold = make_item("below-threshold", 6.5, "ai")
    deduplicated = make_item("deduplicated", 8.0, "ai")

    diagnostics = orchestrator.build_selection_diagnostics(
        [selected, below_threshold, deduplicated],
        [selected],
    )

    assert diagnostics["analyzed_count"] == 3
    assert diagnostics["selected_count"] == 1
    assert diagnostics["rejected_count"] == 2
    assert diagnostics["items"] == [
        {
            "id": "below-threshold",
            "title": "below-threshold",
            "profile": "tech-news",
            "source_type": "rss",
            "category": "ai",
            "score": 6.5,
            "threshold": 7.0,
            "stage": "below_profile_threshold",
            "analysis_reason": "test",
        },
        {
            "id": "deduplicated",
            "title": "deduplicated",
            "profile": "tech-news",
            "source_type": "rss",
            "category": "ai",
            "score": 8.0,
            "threshold": 7.0,
            "stage": "removed_after_threshold",
            "analysis_reason": "test",
        },
    ]


def test_selection_diagnostics_preserves_pipeline_exclusion_stages() -> None:
    orchestrator = make_orchestrator(DigestConfig())
    deduped = make_item("deduped", 8.0, "ai")
    capped = make_item("capped", 8.0, "ai")
    failed = make_item("failed", 8.0, "ai")

    diagnostics = orchestrator.build_selection_diagnostics(
        [deduped, capped, failed],
        [],
        exclusion_stages={
            "deduped": "topic_dedup",
            "capped": "digest_limit",
            "failed": "enrichment_failed",
        },
    )

    assert [row["stage"] for row in diagnostics["items"]] == [
        "topic_dedup",
        "digest_limit",
        "enrichment_failed",
    ]


def test_selection_diagnostics_includes_platform_change_candidate_trace() -> None:
    orchestrator = make_orchestrator(DigestConfig())
    item = ContentItem(
        id="platform_changes:page_diff:trace",
        source_type=SourceType.PLATFORM_CHANGES,
        title="平台规则变化",
        url="https://official.example/rules",
        published_at=datetime.now(timezone.utc),
        profile="pangmen-platform-change-radar",
        metadata={
            "candidate_trace": {
                "candidate_id": "platform_changes:page_diff:trace",
                "watcher": "official-rules",
                "discovery_mode": "page_diff",
                "outcome": "pending",
            }
        },
        processing=ProcessingResult(
            classification=ClassificationResult(
                profile="pangmen-platform-change-radar", method="source_override"
            ),
            analysis=ContentAnalysis(score=6.0, reason="below", summary="规则"),
        ),
    )

    diagnostics = orchestrator.build_selection_diagnostics([item], [])

    assert diagnostics["items"][0]["candidate_trace"]["candidate_id"] == item.id


def test_run_refills_enrichment_failures_from_eligible_candidates(tmp_path, monkeypatch) -> None:
    config = Config(
        ai=AIConfig(
            provider="openai", model="test", api_key_env="TEST_API_KEY", languages=[]
        ),
        sources=SourcesConfig(),
        digest=DigestConfig(max_items=1),
    )
    orchestrator = HorizonOrchestrator(config, SimpleNamespace())
    first = make_item("first", 9.0, "ai")
    fallback = make_item("fallback", 8.0, "ai")
    enriched: list[str] = []

    async def fetch_all_sources(since):  # type: ignore[no-untyped-def]
        return [first, fallback]

    async def analyze_content(input_items):  # type: ignore[no-untyped-def]
        return input_items

    async def merge_topic_duplicates(input_items, *, log=True):  # type: ignore[no-untyped-def]
        return input_items

    async def enrich_important_items(input_items):  # type: ignore[no-untyped-def]
        enriched.extend(item.id for item in input_items)
        if input_items[0].id == "first":
            from src.ai.enricher import EnrichmentBatchResult

            return EnrichmentBatchResult(failures={"first": "test failure"})
        from src.ai.enricher import EnrichmentBatchResult

        return EnrichmentBatchResult(succeeded_ids=[input_items[0].id])

    monkeypatch.setattr(orchestrator, "fetch_all_sources", fetch_all_sources)
    monkeypatch.setattr(orchestrator, "analyze_items", analyze_content)
    monkeypatch.setattr(orchestrator, "merge_topic_duplicates", merge_topic_duplicates)
    monkeypatch.setattr(orchestrator, "enrich_items", enrich_important_items)
    monkeypatch.chdir(tmp_path)

    asyncio.run(orchestrator.run())

    assert enriched == ["first", "fallback"]


def test_rejects_settings_for_unknown_profile() -> None:
    config = Config(
        ai=AIConfig(
            provider="openai",
            model="test",
            api_key_env="TEST_API_KEY",
        ),
        sources=SourcesConfig(),
        processing=ProcessingConfig(
            profile_settings={
                "missing": ProfileSettingsConfig(threshold=7.0)
            }
        ),
    )

    with pytest.raises(ValueError, match="Unknown processing profile: missing"):
        HorizonOrchestrator(config, SimpleNamespace())


@pytest.mark.parametrize(
    "profile_order",
    [
        ["tech-news", "tech-blog"],
        ["tech-news", "tech-blog", "finance-news", "missing"],
    ],
)
def test_rejects_incomplete_or_unknown_profile_order(profile_order) -> None:
    config = Config(
        ai=AIConfig(
            provider="openai",
            model="test",
            api_key_env="TEST_API_KEY",
        ),
        sources=SourcesConfig(),
        digest=DigestConfig(profile_order=profile_order),
    )

    with pytest.raises(ValueError, match="must list every loaded profile"):
        HorizonOrchestrator(config, SimpleNamespace())


def test_duplicate_category_warns_and_first_group_wins() -> None:
    filtering = DigestConfig(
        category_groups={
            "first": CategoryGroupConfig(limit=1, categories=["shared"]),
            "second": CategoryGroupConfig(limit=2, categories=["shared"]),
        }
    )
    orchestrator = make_orchestrator(filtering)

    result = orchestrator.apply_balanced_digest(
        [make_item("top", 9.0, "shared"), make_item("second", 8.0, "shared")]
    )

    assert [item.id for item in result.items] == ["top"]
    assert result.duplicate_categories == ["shared"]
    assert "using 'first'" in orchestrator.console.export_text()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_items": 0},
        {"default_group_limit": 0},
        {"category_groups": {"ai": {"limit": 0, "categories": ["ai"]}}},
        {"category_groups": {"ai": {"limit": 1, "categories": []}}},
        {"profile_order": ["tech-news", "tech-news"]},
        {"profile_order": ["tech-news", ""]},
    ],
)
def test_digest_config_rejects_invalid_values(kwargs) -> None:
    with pytest.raises(ValidationError):
        DigestConfig(**kwargs)


def test_run_applies_balanced_digest_before_enrichment(tmp_path, monkeypatch) -> None:
    config = Config(
        ai=AIConfig(
            provider="openai",
            model="test",
            api_key_env="TEST_API_KEY",
            languages=[],
        ),
        sources=SourcesConfig(),
        processing=ProcessingConfig(
            profile_settings={
                "tech-news": ProfileSettingsConfig(threshold=7.0)
            }
        ),
        digest=DigestConfig(
            max_items=1,
            category_groups={
                "ai": CategoryGroupConfig(limit=1, categories=["ai"]),
                "finance": CategoryGroupConfig(limit=1, categories=["finance"]),
            },
        ),
    )
    storage = SimpleNamespace()
    orchestrator = HorizonOrchestrator(config, storage)
    items = [
        make_item("ai", 9.0, "ai"),
        make_item("finance", 8.0, "finance"),
        make_item("below-threshold", 6.0, "ai"),
    ]
    enriched_ids: list[str] = []

    async def fetch_all_sources(since):  # type: ignore[no-untyped-def]
        return items

    async def analyze_content(input_items):  # type: ignore[no-untyped-def]
        return input_items

    async def merge_topic_duplicates(input_items, *, log=True):  # type: ignore[no-untyped-def]
        return input_items

    async def expand_twitter_discussion(input_items):  # type: ignore[no-untyped-def]
        return None

    async def enrich_important_items(input_items):  # type: ignore[no-untyped-def]
        enriched_ids.extend(item.id for item in input_items)

    monkeypatch.setattr(orchestrator, "fetch_all_sources", fetch_all_sources)
    monkeypatch.setattr(orchestrator, "analyze_items", analyze_content)
    monkeypatch.setattr(orchestrator, "merge_topic_duplicates", merge_topic_duplicates)
    monkeypatch.setattr(orchestrator, "_expand_twitter_discussion", expand_twitter_discussion)
    monkeypatch.setattr(orchestrator, "enrich_items", enrich_important_items)
    monkeypatch.chdir(tmp_path)

    asyncio.run(orchestrator.run())

    assert enriched_ids == ["ai"]


def test_run_balances_after_twitter_reanalysis(tmp_path, monkeypatch) -> None:
    config = Config(
        ai=AIConfig(
            provider="openai",
            model="test",
            api_key_env="TEST_API_KEY",
            languages=[],
        ),
        sources=SourcesConfig(),
        digest=DigestConfig(max_items=1),
    )
    orchestrator = HorizonOrchestrator(config, SimpleNamespace())
    items = [make_item("first", 9.0, "ai"), make_item("second", 8.0, "ai")]
    enriched_ids: list[str] = []

    async def fetch_all_sources(since):  # type: ignore[no-untyped-def]
        return items

    async def analyze_content(input_items):  # type: ignore[no-untyped-def]
        return input_items

    async def merge_topic_duplicates(input_items, *, log=True):  # type: ignore[no-untyped-def]
        return input_items

    async def expand_twitter_discussion(input_items):  # type: ignore[no-untyped-def]
        input_items[0].processing.analysis.score = 7.0
        input_items[1].processing.analysis.score = 10.0
        input_items.sort(
            key=lambda item: item.processing.analysis.score or 0,
            reverse=True,
        )

    async def enrich_important_items(input_items):  # type: ignore[no-untyped-def]
        enriched_ids.extend(item.id for item in input_items)

    monkeypatch.setattr(orchestrator, "fetch_all_sources", fetch_all_sources)
    monkeypatch.setattr(orchestrator, "analyze_items", analyze_content)
    monkeypatch.setattr(orchestrator, "merge_topic_duplicates", merge_topic_duplicates)
    monkeypatch.setattr(orchestrator, "_expand_twitter_discussion", expand_twitter_discussion)
    monkeypatch.setattr(orchestrator, "enrich_items", enrich_important_items)
    monkeypatch.chdir(tmp_path)

    asyncio.run(orchestrator.run())

    assert enriched_ids == ["second"]

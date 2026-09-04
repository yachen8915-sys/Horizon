import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_github_runtime_config_bounds_slow_ai_analysis():
    config = json.loads(
        (REPOSITORY_ROOT / "data" / "config.github.json").read_text(
            encoding="utf-8"
        )
    )

    assert config["ai"]["request_timeout_sec"] == 25
    assert config["ai"]["analysis_concurrency"] == 2


def test_github_runtime_config_declares_source_registry_and_shadow_state():
    config = json.loads(
        (REPOSITORY_ROOT / "data" / "config.github.json").read_text(
            encoding="utf-8"
        )
    )

    assert config["collection"]["source_registry_file"] == "data/source_registry.json"
    assert config["collection"]["core_entity_registry_file"] == "data/core_entities.json"
    assert config["collection"]["source_health_state_file"] == (
        "data/shadow/source_health_state.json"
    )
    assert config["collection"]["source_shadow_enabled"] is False
    assert config["collection"]["engagement_tracking"]["refresh_after_hours"] == 3


def test_github_runtime_config_defines_bluesky_as_bounded_free_shadow_source():
    config = json.loads(
        (REPOSITORY_ROOT / "data" / "config.github.json").read_text(
            encoding="utf-8"
        )
    )

    bluesky = config["sources"]["bluesky"]
    assert bluesky["enabled"] is True
    assert bluesky["shadow"] is True
    assert bluesky["public_api_base_url"] == "https://public.api.bsky.app"
    assert bluesky["max_requests_per_run"] == 5
    assert bluesky["actors"] == [
        "simonwillison.net",
        "swyx.io",
        "emollick.bsky.social",
        "karpathy.bsky.social",
        "replicate.com",
    ]
    assert len(bluesky["queries"]) == 2
    assert all(query["enabled"] is False for query in bluesky["queries"])

    policy = config["collection"]["social_engagement_quality"]["platforms"][
        "bluesky"
    ]
    assert policy["primary_metric"] == "likes"
    assert policy["minimum_sample"] == 50
    assert policy["required_metrics"] == ["likes", "reposts", "replies", "quotes"]


def test_github_runtime_config_uses_no_key_youtube_and_disables_direct_x():
    config = json.loads(
        (REPOSITORY_ROOT / "data" / "config.github.json").read_text(
            encoding="utf-8"
        )
    )

    twitter = config["sources"]["twitter"]
    youtube = config["sources"]["youtube"]
    assert twitter["enabled"] is False
    assert twitter["local_cli_fallback_enabled"] is False
    assert youtube["local_cli_fallback_enabled"] is True
    assert youtube["local_cli_command"] == "yt-dlp"
    assert youtube["local_cli_concurrency"] == 2

    policy = config["collection"]["social_engagement_quality"]["platforms"][
        "twitter_opencli"
    ]
    assert policy["required_metrics"] == ["impressions", "likes"]
    assert policy["minimum_complete_fields"] == 2


def test_github_p0_sources_are_declared_as_shadow_only():
    config = json.loads(
        (REPOSITORY_ROOT / "data" / "config.github.json").read_text(
            encoding="utf-8"
        )
    )

    sources = config["sources"]["github"]
    assert sources
    assert all(source["shadow"] is True for source in sources)
    assert {source["type"] for source in sources} == {
        "repo_releases",
        "repo_search",
    }
    assert sum(source["type"] == "repo_search" for source in sources) >= 2


def test_youtube_channel_feeds_are_enabled_as_shadow_discovery() -> None:
    config = json.loads(
        (REPOSITORY_ROOT / "data" / "config.github.json").read_text(
            encoding="utf-8"
        )
    )
    registry = json.loads(
        (REPOSITORY_ROOT / "data" / "source_registry.json").read_text(
            encoding="utf-8"
        )
    )

    youtube_feeds = [
        source
        for source in config["sources"]["rss"]
        if source["name"].startswith("YouTube -")
    ]
    youtube_registry = next(
        source
        for source in registry["sources"]
        if source["source_id"] == "youtube-rss"
    )

    assert youtube_feeds
    assert all(source["enabled"] is True for source in youtube_feeds)
    assert all(source["shadow"] is True for source in youtube_feeds)
    assert all(source["expected_cadence_hours"] == 24 for source in youtube_feeds)
    assert youtube_registry["lifecycle"] == "shadow"


def test_smol_ai_news_is_a_shadow_editorial_feed() -> None:
    config = json.loads(
        (REPOSITORY_ROOT / "data" / "config.github.json").read_text(
            encoding="utf-8"
        )
    )

    feed = next(
        source
        for source in config["sources"]["rss"]
        if source["name"] == "Smol AI News"
    )

    assert feed == {
        "name": "Smol AI News",
        "url": "https://news.smol.ai/rss.xml",
        "enabled": True,
        "shadow": True,
        "expected_cadence_hours": 24,
        "category": "overseas-ai-media",
        "profile": "pangmen-topic-radar",
    }


def test_overseas_editorial_feeds_are_shadowed_until_validated() -> None:
    config = json.loads(
        (REPOSITORY_ROOT / "data" / "config.github.json").read_text(
            encoding="utf-8"
        )
    )

    feeds = [
        source
        for source in config["sources"]["rss"]
        if source.get("category") == "overseas-ai-media"
    ]

    assert {feed["name"] for feed in feeds} == {
        "TechCrunch Artificial Intelligence",
        "The Verge AI",
        "MIT Technology Review AI",
        "Smol AI News",
    }
    assert all(feed["shadow"] is True for feed in feeds)


def test_workflow_defines_disabled_ready_morning_and_afternoon_modes():
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "daily-summary.yml").read_text(
        encoding="utf-8"
    )

    assert "webhook_test" in workflow
    assert "platform_changes_smoke" in workflow
    assert "horizon-platform-changes-smoke" in workflow
    assert "horizon-webhook --config data/config.github.json --lang zh" in workflow
    assert "workflow_dispatch:" in workflow
    assert "run-name:" in workflow
    assert "schedule:" in workflow
    assert '- cron: "50 23 * * *"' in workflow
    assert '- cron: "0 8 * * *"' in workflow
    assert "source_shadow" in workflow
    assert "intelligence_shadow" in workflow
    assert "--intelligence-run-mode" in workflow
    assert "X_BEARER_TOKEN: ${{ secrets.X_BEARER_TOKEN }}" not in workflow
    assert "YOUTUBE_DATA_API_KEY: ${{ secrets.YOUTUBE_DATA_API_KEY }}" not in workflow
    assert "github.event_name == 'workflow_dispatch' && inputs.run_mode == 'webhook_test'" in workflow
    assert "ALAPI_TOKEN: ${{ secrets.ALAPI_TOKEN }}" in workflow


def test_scheduled_workflow_gates_all_expensive_or_external_steps_after_checkout():
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "daily-summary.yml").read_text(
        encoding="utf-8"
    )

    gate = workflow.split("- name: Check for an earlier successful daily run", 1)[1]
    assert "actions: read" in workflow
    assert "id: daily_run_gate" in gate
    assert "github.event_name == 'schedule'" in gate
    assert "python scripts/check_daily_run_gate.py" in gate
    assert "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in gate

    guarded_steps = (
        "Set up Python",
        "Install uv",
        "Install dependencies",
        "Prepare GitHub Actions config",
        "Restore intelligence shadow state",
        "Run intelligence radar",
        "Save intelligence shadow state",
        "Upload intelligence shadow artifacts",
    )
    fail_closed_guard = (
        "github.event_name != 'schedule' || "
        "steps.daily_run_gate.outputs.should_run == 'true'"
    )
    for name in guarded_steps:
        body = workflow.split(f"- name: {name}", 1)[1].split("- name:", 1)[0]
        assert fail_closed_guard in body, name


def test_platform_changes_smoke_restores_baseline_without_full_run_or_state_save():
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "daily-summary.yml").read_text(
        encoding="utf-8"
    )

    smoke_restore = workflow.split(
        "- name: Restore production platform change state for smoke", 1
    )[1].split("- name:", 1)[0]
    smoke_run = workflow.split("- name: Run platform changes smoke", 1)[1].split(
        "- name:", 1
    )[0]
    save_state = workflow.split("- name: Save platform change state", 1)[1].split(
        "- name:", 1
    )[0]

    assert "inputs.run_mode == 'platform_changes_smoke'" in smoke_restore
    assert "id: restore_platform_change_state_smoke" in smoke_restore
    assert "key: platform-change-state-smoke-${{ github.run_id }}" in smoke_restore
    assert "restore-keys: |" in smoke_restore
    assert "platform-change-state-" in smoke_restore
    assert "fail-on-cache-miss: true" in smoke_restore
    assert "--require-state" in smoke_run
    assert "HORIZON_WEBHOOK_URL" not in smoke_run
    assert "DEEPSEEK_API_KEY" not in smoke_run
    assert "platform_changes_smoke" not in save_state


def test_github_runtime_config_uses_independent_radar_upper_bounds():
    config = json.loads(
        (REPOSITORY_ROOT / "data" / "config.github.json").read_text(
            encoding="utf-8"
        )
    )

    settings = config["processing"]["profile_settings"]
    assert settings["pangmen-topic-radar"]["threshold"] == 6.0
    assert settings["pangmen-ai-tech-radar"]["threshold"] == 6.0
    assert settings["pangmen-platform-trend-radar"]["threshold"] == 7.0
    assert config["digest"]["profile_limits"] == {
        "pangmen-platform-trend-radar": 8,
        "pangmen-platform-change-radar": 5,
    }
    assert config["digest"]["unbounded_profiles"] == [
        "pangmen-topic-radar",
        "pangmen-ai-tech-radar",
    ]
    assert config["digest"]["max_items"] == 25
    assert config["digest"]["platform_trend_leverage_limit"] == 6
    assert config["digest"]["platform_trend_watch_limit"] == 4
    assert config["digest"]["platform_trend_minimum_per_platform"] == 1
    editorial = config["digest"]["editorial_selection"]
    assert editorial == {
        "enabled": True,
        "state_file": "data/digest_selection_state.json",
        "history_days": 7,
        "editorial_cooldown_days": 3,
        "primary_entity_limit": 2,
        "topic_cluster_limit": 2,
        "use_case_limit": 2,
        "tutorial_workflow_limit": 2,
        "semantic_cooldown_days": 3,
        "same_day_semantic_limit": 1,
        "sub_source_limit": 2,
        "max_history_entries": 500,
    }
    assert config["sources"]["huggingface"]["enabled"] is True

    platform_providers = config["sources"]["platform_trends"]["providers"]
    dailyhot_platforms = {
        provider["platform"]
        for provider in platform_providers
        if provider["enabled"]
        and provider["provider"] == "dailyhotapi_public_instance"
    }
    assert dailyhot_platforms == {"weibo", "douyin"}
    alapi_providers = [
        provider
        for provider in platform_providers
        if provider["enabled"] and provider["provider"] == "alapi_tophub"
    ]
    assert {provider["platform"] for provider in alapi_providers} == {
        "weibo",
        "douyin",
        "toutiao",
        "zhihu",
        "baidu",
        "36kr",
    }
    assert all(provider["api_key_env"] == "ALAPI_TOKEN" for provider in alapi_providers)
    assert all(provider["response_adapter"] == "alapi_tophub" for provider in alapi_providers)
    assert all(
        not provider["enabled"]
        for provider in platform_providers
        if provider["platform"] in {"xiaohongshu", "wechat"}
    )
    assert {
        provider["platform"]: provider["provider"]
        for provider in platform_providers
        if not provider["enabled"]
        and provider["platform"] in {"xiaohongshu", "wechat"}
    } == {
        "xiaohongshu": "external_public_provider_required",
        "wechat": "external_public_provider_required",
    }
    provider_limits = {
        (provider["platform"], provider["provider"]): (
            provider["fetch_limit"],
            provider["rank_limit"],
        )
        for provider in platform_providers
        if provider["enabled"]
    }
    assert provider_limits[("weibo", "alapi_tophub")] == (30, 30)
    assert provider_limits[("toutiao", "alapi_tophub")] == (20, 20)
    assert provider_limits[("zhihu", "alapi_tophub")] == (20, 20)
    assert "platform-trend" not in config["digest"]["category_groups"]


def test_digest_selection_state_uses_an_independent_restore_and_save_cache():
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "daily-summary.yml").read_text(
        encoding="utf-8"
    )

    restore = workflow.split("- name: Restore digest selection state", 1)[1].split(
        "- name:", 1
    )[0]
    save = workflow.split("- name: Save digest selection state", 1)[1].split(
        "- name:", 1
    )[0]

    assert "actions/cache/restore@v4" in restore
    assert "id: restore_digest_selection_state" in restore
    assert "path: data/digest_selection_state.json" in restore
    assert "key: digest-selection-state-${{ github.run_id }}" in restore
    assert "restore-keys: |" in restore
    assert "digest-selection-state-" in restore
    assert "platform-change-state-" not in restore
    assert "fail-on-cache-miss" not in restore
    assert "actions/cache/save@v4" in save
    assert "path: data/digest_selection_state.json" in save
    assert "key: digest-selection-state-${{ github.run_id }}" in save
    assert "if: success()" in save
    assert workflow.index("Restore digest selection state") < workflow.index("Run Horizon")
    assert workflow.index("Run Horizon") < workflow.index("Save digest selection state")


def test_platform_change_radar_uses_public_watchers_and_persistent_action_state():
    config = json.loads(
        (REPOSITORY_ROOT / "data" / "config.github.json").read_text(encoding="utf-8")
    )
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "daily-summary.yml").read_text(
        encoding="utf-8"
    )

    source = config["sources"]["platform_changes"]
    assert source["enabled"] is True
    assert source["lookback_days"] == 7
    assert source["state_file"] == "data/platform_change_state.json"
    watchers = {watcher["name"]: watcher for watcher in source["watchers"]}
    assert watchers["xiaohongshu-public-index"]["mode"] == "xiaohongshu_rules"
    assert watchers["bilibili-community-convention"]["mode"] == "bilibili_bundle_diff"
    assert watchers["wechat-public-search"]["mode"] == "search_rss"
    assert watchers["wechat-public-search"]["source_level"] == "official_republished"
    assert "actions/cache/restore@v4" in workflow
    assert "actions/cache/save@v4" in workflow
    assert "data/platform_change_state.json" in workflow
    assert workflow.index("Restore platform change state") < workflow.index("Run Horizon")
    assert workflow.index("Run Horizon") < workflow.index("Save platform change state")
    save_state = workflow.split("- name: Save platform change state", 1)[1].split(
        "- name:", 1
    )[0]
    assert "if: success()" in save_state
    assert "github.event_name == 'workflow_dispatch'" in save_state
    assert "inputs.run_mode == 'full'" in save_state

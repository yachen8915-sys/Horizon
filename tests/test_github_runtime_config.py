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


def test_workflow_starts_daily_at_0915_and_holds_delivery_until_1000():
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
    assert '- cron: "15 1 * * *"' in workflow
    assert 'HORIZON_WEBHOOK_NOT_BEFORE_LOCAL: "10:00"' in workflow
    assert 'HORIZON_WEBHOOK_TIMEZONE: "Asia/Shanghai"' in workflow
    assert "github.event_name == 'schedule' || inputs.run_mode == 'full'" in workflow
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
        "Restore platform change state",
        "Run Horizon",
        "Save platform change state",
        "Upload selection diagnostics",
        "Set current date as environment variable",
        "Deploy to GitHub Pages",
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
    assert "key: platform-change-state-31554080940" in smoke_restore
    assert "restore-keys:" not in smoke_restore
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
    assert settings["pangmen-topic-radar"]["threshold"] == 7.0
    assert settings["pangmen-ai-tech-radar"]["threshold"] == 7.0
    assert settings["pangmen-platform-trend-radar"]["threshold"] == 7.0
    assert config["digest"]["profile_limits"] == {
        "pangmen-topic-radar": 12,
        "pangmen-ai-tech-radar": 5,
        "pangmen-platform-trend-radar": 8,
        "pangmen-platform-change-radar": 5,
    }
    assert config["digest"]["max_items"] == 25
    assert config["digest"]["platform_trend_leverage_limit"] == 6
    assert config["digest"]["platform_trend_watch_limit"] == 4
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
    assert "steps.daily_run_gate.outputs.should_run == 'true'" in save_state
    assert "github.event_name == 'schedule' || inputs.run_mode == 'full'" in save_state

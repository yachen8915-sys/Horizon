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

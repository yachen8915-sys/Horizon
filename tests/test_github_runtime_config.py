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


def test_workflow_keeps_manual_entry_without_github_native_schedule():
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "daily-summary.yml").read_text(
        encoding="utf-8"
    )

    assert "webhook_test" in workflow
    assert "horizon-webhook --config data/config.github.json --lang zh" in workflow
    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow


def test_github_runtime_config_uses_dynamic_radar_mix_under_total_cap():
    config = json.loads(
        (REPOSITORY_ROOT / "data" / "config.github.json").read_text(
            encoding="utf-8"
        )
    )

    settings = config["processing"]["profile_settings"]
    assert settings["pangmen-topic-radar"]["threshold"] == 7.0
    assert settings["pangmen-ai-tech-radar"]["threshold"] == 7.0
    assert settings["pangmen-platform-trend-radar"]["threshold"] == 5.0
    assert config["digest"].get("profile_limits", {}) == {}
    assert config["digest"]["max_items"] == 25
    assert config["sources"]["huggingface"]["enabled"] is True

    platform_providers = config["sources"]["platform_trends"]["providers"]
    enabled_platforms = {
        provider["platform"] for provider in platform_providers if provider["enabled"]
    }
    assert enabled_platforms == {"weibo", "douyin"}
    assert all(
        not provider["enabled"]
        for provider in platform_providers
        if provider["platform"] in {"xiaohongshu", "wechat"}
    )

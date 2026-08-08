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

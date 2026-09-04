import json
from pathlib import Path

from scripts.prepare_canary_config import build_canary_config
from src.ai.summarizer import DailySummarizer
from src.models import WebhookConfig
from src.services.webhook import WebhookNotifier


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _runtime_config() -> dict:
    return json.loads(
        (REPOSITORY_ROOT / "data" / "config.github.json").read_text(
            encoding="utf-8"
        )
    )


def test_canary_config_enables_delivery_only_with_isolated_state() -> None:
    config = build_canary_config(_runtime_config())

    assert config["intelligence"] == {
        **_runtime_config()["intelligence"],
        "delivery_enabled": True,
        "canary_mode": True,
        "run_mode": "morning",
        "candidate_store_file": "data/canary/candidate_ledger.jsonl",
        "delivery_store_file": "data/canary/delivery_ledger.jsonl",
    }
    assert config["webhook"]["enabled"] is True
    assert config["webhook"]["url_env"] == "HORIZON_CANARY_WEBHOOK_URL"
    assert config["webhook"]["title_prefix"] == "【测试】"
    assert config["collection"]["source_health_state_file"] == (
        "data/canary/source_health_state.json"
    )
    assert config["collection"]["engagement_tracking"]["state_filename"] == (
        "canary_engagement_snapshots.json"
    )
    assert config["digest"]["editorial_selection"]["state_file"] == (
        "data/canary/digest_selection_state.json"
    )
    assert config["sources"]["platform_changes"]["state_file"] == (
        "data/canary/platform_change_state.json"
    )


def test_canary_config_does_not_mutate_the_source_mapping() -> None:
    source = _runtime_config()
    before = json.dumps(source, ensure_ascii=False, sort_keys=True)

    build_canary_config(source)

    assert json.dumps(source, ensure_ascii=False, sort_keys=True) == before


def test_canary_title_prefix_is_applied_to_collapsible_card() -> None:
    notifier = WebhookNotifier(
        WebhookConfig(
            enabled=False,
            platform="feishu",
            layout="collapsible",
            title_prefix="【测试】",
            languages=["zh"],
        )
    )

    message = notifier.build_daily_summary_messages(
        summary="unused",
        important_items=[],
        all_items_count=12,
        date="2026-09-04",
        lang="zh",
        summarizer=DailySummarizer(),
    )[0]

    assert message["message_title"] == "【测试】Horizon 2026-09-04 折叠日报"
    assert message["_request_body_override"]["card"]["header"]["title"][
        "content"
    ] == "【测试】Horizon 2026-09-04 折叠日报"


def test_canary_workflow_is_date_gated_and_never_uses_production_webhook() -> None:
    workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "horizon-canary.yml"
    ).read_text(encoding="utf-8")

    assert 'cron: "50 23 * * *"' in workflow
    assert "2026-09-05" in workflow
    assert "HORIZON_CANARY_WEBHOOK_URL: ${{ secrets.HORIZON_CANARY_WEBHOOK_URL }}" in workflow
    assert "HORIZON_WEBHOOK_URL: ${{ secrets.HORIZON_WEBHOOK_URL }}" not in workflow
    assert "python scripts/prepare_canary_config.py" in workflow
    assert "uv run horizon --config data/config.canary.json --hours 24" in workflow
    assert '--resume-cache "$checkpoint"' in workflow
    assert "retrying once from checkpoint" in workflow
    assert "uv run horizon-webhook --config data/config.canary.json" in workflow
    assert "inputs.run_mode == 'webhook_test'" in workflow
    assert "data/cache/*" in workflow
    assert "codex/horizon-intelligence-redesign-plan-20260903" in workflow
    assert "contents: read" in workflow
    assert "contents: write" not in workflow
    assert "peaceiris/actions-gh-pages" not in workflow
    assert "X_BEARER_TOKEN" not in workflow
    assert "YOUTUBE_DATA_API_KEY" not in workflow

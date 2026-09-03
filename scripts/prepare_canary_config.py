"""Build a fail-closed Horizon config for an isolated Feishu canary run."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from src._file_utils import _atomic_write_text
from src.models import Config


def build_canary_config(source: dict) -> dict:
    """Return a validated canary config without mutating ``source``."""
    config = deepcopy(source)

    intelligence = config["intelligence"]
    intelligence.update(
        {
            "delivery_enabled": True,
            "run_mode": "morning",
            "candidate_store_file": "data/canary/candidate_ledger.jsonl",
            "delivery_store_file": "data/canary/delivery_ledger.jsonl",
        }
    )

    webhook = config["webhook"]
    webhook.update(
        {
            "enabled": True,
            "url_env": "HORIZON_CANARY_WEBHOOK_URL",
            "title_prefix": "【测试】",
        }
    )

    config["collection"]["source_health_state_file"] = (
        "data/canary/source_health_state.json"
    )
    config["collection"]["engagement_tracking"]["state_filename"] = (
        "canary_engagement_snapshots.json"
    )
    config["digest"]["editorial_selection"]["state_file"] = (
        "data/canary/digest_selection_state.json"
    )
    config["sources"]["platform_changes"]["state_file"] = (
        "data/canary/platform_change_state.json"
    )

    Config.model_validate(config)
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    source = json.loads(args.input.read_text(encoding="utf-8"))
    canary = build_canary_config(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        args.output,
        json.dumps(canary, ensure_ascii=False, indent=2) + "\n",
    )


if __name__ == "__main__":
    main()

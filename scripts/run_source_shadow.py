"""Run only P0 shadow sources; never call AI, webhook, email, or production state."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import httpx

from src.models import Config
from src.processing.source_health import SourceHealthLedger
from src.processing.source_shadow import collect_shadow_sources
from src.processing.social_quality import SocialEngagementQualityGate
from src.storage.source_shadow_export import export_source_shadow_items


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Horizon source-only shadow collection"
    )
    parser.add_argument("--config", default="data/config.github.json")
    parser.add_argument("--hours", type=int, default=None)
    return parser.parse_args()


async def run(config_path: Path, hours: int | None = None) -> dict:
    config = Config.model_validate(json.loads(config_path.read_text(encoding="utf-8")))
    state_path = Path(config.collection.source_health_state_file).resolve()
    if "shadow" not in {part.lower() for part in state_path.parts}:
        raise ValueError("source-only state path must be inside a directory named shadow")

    now = datetime.now(timezone.utc)
    lookback_hours = hours or config.collection.time_window_hours
    default_since = now - timedelta(hours=lookback_hours)
    ledger = SourceHealthLedger.load(state_path)
    since = ledger.next_since(default_since=default_since, overlap_minutes=15)

    async with httpx.AsyncClient(timeout=30.0) as client:
        outcomes = await collect_shadow_sources(config, since, client)

    health_rows = [row for outcome in outcomes for row in outcome.health]
    items = [item for outcome in outcomes for item in outcome.items]
    quality_config = config.collection.social_engagement_quality
    SocialEngagementQualityGate(quality_config).evaluate(items, now=now)
    quality_counts = Counter(
        str(item.metadata.get("engagement_gate_status") or "unclassified")
        for item in items
    )
    run_id = now.strftime("source-shadow-%Y%m%dT%H%M%SZ")
    snapshot = export_source_shadow_items(
        [
            (outcome.source_name, item)
            for outcome in outcomes
            for item in outcome.items
        ],
        state_path.parent / "source-runs" / now.date().isoformat(),
        run_id=run_id,
    )
    run_record = ledger.record_run(
        run_id=run_id,
        checked_at=now,
        since=since,
        item_ids=[item.id for item in items],
        health_rows=health_rows,
        snapshot_jsonl_path=str(snapshot.jsonl_path),
        snapshot_html_path=str(snapshot.html_path),
    )
    ledger.save(state_path)
    return {
        "mode": "source_only_shadow",
        "run": run_record.model_dump(mode="json"),
        "outcomes": [outcome.to_dict() for outcome in outcomes],
        "state_path": str(state_path),
        "snapshot": {
            "jsonl": str(snapshot.jsonl_path),
            "html": str(snapshot.html_path),
        },
        "quality_counts": dict(sorted(quality_counts.items())),
        "safety": {
            "ai_called": False,
            "delivery_called": False,
            "production_state_written": False,
        },
    }


def main() -> int:
    args = parse_args()
    result = asyncio.run(run(Path(args.config), args.hours))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    outcomes = result["outcomes"]
    return 1 if outcomes and all(row["status"] == "failure" for row in outcomes) else 0


if __name__ == "__main__":
    raise SystemExit(main())

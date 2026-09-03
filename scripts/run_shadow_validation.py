"""Summarize source-only shadow history without collecting or writing data."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import timezone
import json
from pathlib import Path

from src.processing.source_health import SourceHealthLedger, SourceShadowRun


def summarize_shadow_runs(runs: list[SourceShadowRun]) -> dict[str, object]:
    latest_by_day: dict[str, SourceShadowRun] = {}
    for run in sorted(runs, key=lambda row: row.checked_at):
        day = run.checked_at.astimezone(timezone.utc).date().isoformat()
        latest_by_day[day] = run
    daily_runs = list(latest_by_day.values())
    total_items = sum(run.item_count for run in daily_runs)
    total_unique = sum(run.unique_item_count for run in daily_runs)
    total_duplicates = sum(run.duplicate_count for run in daily_runs)
    health = Counter(
        {
            status: sum(run.health_counts.get(status, 0) for run in daily_runs)
            for status in {
                status
                for run in daily_runs
                for status in run.health_counts
            }
        }
    )
    blocking_reasons: list[str] = []
    if len(daily_runs) < 7:
        blocking_reasons.append("need_7_distinct_days")
    if health.get("failed", 0):
        blocking_reasons.append("failed_source_checks_present")
    missing_snapshot_runs = sum(
        1
        for run in daily_runs
        if not run.snapshot_jsonl_path or not run.snapshot_html_path
    )
    if missing_snapshot_runs:
        blocking_reasons.append("missing_source_candidate_snapshots")

    return {
        "ready_for_source_review": not blocking_reasons,
        "raw_run_count": len(runs),
        "run_count": len(daily_runs),
        "distinct_days": len(daily_runs),
        "first_run_at": (
            min(run.checked_at for run in runs).isoformat() if runs else None
        ),
        "last_run_at": (
            max(run.checked_at for run in runs).isoformat() if runs else None
        ),
        "item_count": total_items,
        "unique_item_rate": total_unique / total_items if total_items else 0.0,
        "duplicate_item_rate": (
            total_duplicates / total_items if total_items else 0.0
        ),
        "new_item_count": sum(run.new_item_count for run in daily_runs),
        "repeated_item_count": sum(
            run.repeated_item_count for run in daily_runs
        ),
        "health_counts": dict(sorted(health.items())),
        "failed_checks": health.get("failed", 0),
        "missing_snapshot_runs": missing_snapshot_runs,
        "blocking_reasons": blocking_reasons,
    }


def summarize_shadow_ledger(ledger: SourceHealthLedger) -> dict[str, object]:
    report = summarize_shadow_runs(ledger.runs)
    report["open_source_gaps"] = [
        {
            "source_id": source_id,
            "gap_started_at": watermark.gap_started_at.isoformat(),
        }
        for source_id, watermark in sorted(ledger.watermarks.items())
        if watermark.gap_started_at is not None
    ]
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize Horizon source-only shadow history"
    )
    parser.add_argument(
        "--state",
        default="data/shadow/source_health_state.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ledger = SourceHealthLedger.load(Path(args.state))
    print(
        json.dumps(
            summarize_shadow_ledger(ledger),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

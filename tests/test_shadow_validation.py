from datetime import datetime, timedelta, timezone

from scripts.run_shadow_validation import (
    summarize_shadow_ledger,
    summarize_shadow_runs,
)
from src.processing.source_health import (
    SourceHealthLedger,
    SourceShadowRun,
    SourceWatermark,
)


NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


def _run(
    day: int,
    *,
    failed: int = 0,
    snapshots: bool = True,
) -> SourceShadowRun:
    checked_at = NOW + timedelta(days=day)
    return SourceShadowRun(
        run_id=f"run-{day}",
        checked_at=checked_at,
        since=checked_at - timedelta(hours=24),
        item_count=10,
        unique_item_count=9,
        duplicate_count=1,
        new_item_count=5,
        repeated_item_count=4,
        health_counts={"healthy": 9, "failed": failed},
        snapshot_jsonl_path=(
            f"data/shadow/source-runs/run-{day}.jsonl" if snapshots else None
        ),
        snapshot_html_path=(
            f"data/shadow/source-runs/run-{day}.html" if snapshots else None
        ),
    )


def test_shadow_summary_requires_seven_distinct_days() -> None:
    report = summarize_shadow_runs([_run(day) for day in range(6)])

    assert report["ready_for_source_review"] is False
    assert report["distinct_days"] == 6
    assert "need_7_distinct_days" in report["blocking_reasons"]


def test_shadow_summary_reports_rates_and_persistent_failures() -> None:
    report = summarize_shadow_runs(
        [_run(day, failed=1 if day in {0, 1, 2} else 0) for day in range(7)]
    )

    assert report["distinct_days"] == 7
    assert report["unique_item_rate"] == 0.9
    assert report["duplicate_item_rate"] == 0.1
    assert report["failed_checks"] == 3
    assert "failed_source_checks_present" in report["blocking_reasons"]


def test_shadow_summary_requires_reviewable_candidate_snapshots() -> None:
    report = summarize_shadow_runs(
        [_run(day, snapshots=day != 6) for day in range(7)]
    )

    assert report["missing_snapshot_runs"] == 1
    assert "missing_source_candidate_snapshots" in report["blocking_reasons"]


def test_shadow_ledger_lists_open_source_gaps() -> None:
    ledger = SourceHealthLedger(
        watermarks={
            "youtube": SourceWatermark(
                source_id="youtube",
                gap_started_at=NOW,
            )
        }
    )

    report = summarize_shadow_ledger(ledger)

    assert report["open_source_gaps"] == [
        {
            "source_id": "youtube",
            "gap_started_at": NOW.isoformat(),
        }
    ]

from datetime import datetime, timedelta, timezone

from src.processing.source_health import (
    SourceHealthLedger,
    SourceHealthObservation,
    SourceHealthStatus,
    SourceWatermark,
    assess_source_health,
)


NOW = datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc)


def _observation(**overrides: object) -> SourceHealthObservation:
    values: dict[str, object] = {
        "source_id": "dailyhot-weibo",
        "checked_at": NOW,
        "enabled": True,
        "transport_ok": True,
        "schema_ok": True,
        "business_ok": True,
        "item_count": 10,
        "newest_item_at": NOW - timedelta(hours=1),
        "expected_cadence_hours": 6,
        "stale_after_multiplier": 3,
    }
    values.update(overrides)
    return SourceHealthObservation.model_validate(values)


def test_http_200_with_business_error_is_failed() -> None:
    observation = _observation(
        business_ok=False,
        business_code="500",
        error="获取失败",
        item_count=0,
    )

    result = assess_source_health(observation)

    assert result.status is SourceHealthStatus.FAILED
    assert result.reason_code == "business_error"
    assert "500" in result.detail


def test_unexpected_empty_result_is_degraded_not_healthy() -> None:
    result = assess_source_health(
        _observation(item_count=0, unexpected_empty=True, newest_item_at=None)
    )

    assert result.status is SourceHealthStatus.DEGRADED
    assert result.reason_code == "unexpected_empty"


def test_old_data_is_stale_even_when_transport_and_schema_succeed() -> None:
    result = assess_source_health(
        _observation(newest_item_at=NOW - timedelta(hours=19))
    )

    assert result.status is SourceHealthStatus.STALE
    assert result.reason_code == "stale_data"


def test_disabled_and_known_coverage_gap_are_not_failures() -> None:
    disabled = assess_source_health(_observation(enabled=False))
    gap = assess_source_health(_observation(coverage_gap=True, item_count=0))

    assert disabled.status is SourceHealthStatus.DISABLED
    assert gap.status is SourceHealthStatus.COVERAGE_GAP


def test_watermark_retries_from_gap_with_overlap() -> None:
    watermark = SourceWatermark(
        source_id="youtube-data",
        last_success_at=NOW - timedelta(hours=4),
        last_native_id="video-42",
        gap_started_at=NOW - timedelta(hours=2),
    )

    assert watermark.next_since(default_since=NOW - timedelta(hours=24), overlap_minutes=15) == (
        NOW - timedelta(hours=2, minutes=15)
    )


def test_watermark_uses_last_success_when_there_is_no_gap() -> None:
    watermark = SourceWatermark(
        source_id="youtube-data",
        last_success_at=NOW - timedelta(hours=4),
        last_native_id="video-42",
    )

    assert watermark.next_since(default_since=NOW - timedelta(hours=24), overlap_minutes=15) == (
        NOW - timedelta(hours=4, minutes=15)
    )


def test_health_ledger_opens_and_closes_collection_gap(tmp_path) -> None:
    path = tmp_path / "source_health_state.json"
    ledger = SourceHealthLedger.load(path)

    ledger.record_run(
        run_id="run-1",
        checked_at=NOW,
        since=NOW - timedelta(hours=24),
        item_ids=["a", "a", "b"],
        health_rows=[
            {"source_id": "github:release:one", "status": "failed"},
            {"source_id": "reddit:one", "status": "healthy"},
        ],
    )
    ledger.save(path)

    reloaded = SourceHealthLedger.load(path)
    assert reloaded.watermarks["github:release:one"].gap_started_at == (
        NOW - timedelta(hours=24)
    )
    assert reloaded.watermarks["reddit:one"].last_success_at == NOW
    assert reloaded.runs[-1].unique_item_count == 2
    assert reloaded.runs[-1].duplicate_count == 1
    assert reloaded.runs[-1].new_item_count == 2
    assert reloaded.runs[-1].repeated_item_count == 0
    assert reloaded.next_since(
        default_since=NOW - timedelta(hours=24), overlap_minutes=15
    ) == NOW - timedelta(hours=24)

    reloaded.record_run(
        run_id="run-2",
        checked_at=NOW + timedelta(hours=2),
        since=NOW - timedelta(minutes=15),
        item_ids=["a", "c"],
        health_rows=[
            {"source_id": "github:release:one", "status": "healthy"},
        ],
    )
    assert reloaded.watermarks["github:release:one"].gap_started_at is None
    assert reloaded.watermarks["github:release:one"].last_success_at == (
        NOW + timedelta(hours=2)
    )
    assert reloaded.runs[-1].new_item_count == 1
    assert reloaded.runs[-1].repeated_item_count == 1


def test_budget_exhaustion_does_not_close_an_existing_gap() -> None:
    ledger = SourceHealthLedger()
    ledger.record_run(
        run_id="failed",
        checked_at=NOW,
        since=NOW - timedelta(hours=24),
        item_ids=[],
        health_rows=[{"source_id": "x:query", "status": "failed"}],
    )

    ledger.record_run(
        run_id="skipped",
        checked_at=NOW + timedelta(hours=1),
        since=NOW - timedelta(hours=24),
        item_ids=[],
        health_rows=[
            {
                "source_id": "x:query",
                "status": "degraded",
                "reason_code": "budget_exhausted",
            }
        ],
    )

    assert ledger.watermarks["x:query"].gap_started_at == (
        NOW - timedelta(hours=24)
    )


def test_explicitly_disabled_source_closes_an_existing_gap() -> None:
    ledger = SourceHealthLedger()
    ledger.record_run(
        run_id="failed",
        checked_at=NOW,
        since=NOW - timedelta(hours=24),
        item_ids=[],
        health_rows=[{"source_id": "bluesky:search:q", "status": "failed"}],
    )

    ledger.record_run(
        run_id="disabled",
        checked_at=NOW + timedelta(hours=1),
        since=NOW - timedelta(hours=24),
        item_ids=[],
        health_rows=[
            {
                "source_id": "bluesky:search:q",
                "status": "disabled",
                "reason_code": "disabled",
            }
        ],
    )

    assert ledger.watermarks["bluesky:search:q"].gap_started_at is None

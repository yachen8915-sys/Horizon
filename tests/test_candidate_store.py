from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from src.models import (
    CandidateRecord,
    CandidateStatus,
    ContentItem,
    ReasonCode,
    SourceType,
)
from src.storage.candidate_store import (
    CandidateStore,
    CandidateStoreCorruptionError,
    InvalidCandidateTransition,
    load_legacy_cooldown_baseline,
)


NOW = datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc)


def _candidate(candidate_id: str = "candidate-1") -> CandidateRecord:
    return CandidateRecord(
        candidate_id=candidate_id,
        item=ContentItem(
            id=f"rss:official:{candidate_id}",
            source_type=SourceType.RSS,
            title="New feature",
            url=f"https://example.com/{candidate_id}",
            published_at=NOW,
        ),
        discovered_at=NOW,
        updated_at=NOW,
        rule_version="2026-09-03-v1",
    )


def test_candidate_lifecycle_is_persisted_as_append_snapshots(tmp_path) -> None:
    path = tmp_path / "candidate-ledger.jsonl"
    store = CandidateStore(path)
    store.create(_candidate())
    store.transition("candidate-1", CandidateStatus.ENRICHED, at=NOW + timedelta(minutes=1))
    store.transition(
        "candidate-1",
        CandidateStatus.ELIGIBLE,
        at=NOW + timedelta(minutes=2),
        reason_code=ReasonCode.PASSED_HARD_GATES,
    )
    store.transition(
        "candidate-1",
        CandidateStatus.SELECTED,
        at=NOW + timedelta(minutes=3),
        reason_code=ReasonCode.SELECTED,
    )

    reloaded = CandidateStore(path)
    candidate = reloaded.get("candidate-1")

    assert candidate is not None
    assert candidate.status is CandidateStatus.SELECTED
    assert [row.to_status for row in candidate.status_history] == [
        CandidateStatus.ENRICHED,
        CandidateStatus.ELIGIBLE,
        CandidateStatus.SELECTED,
    ]
    assert len(path.read_text(encoding="utf-8").splitlines()) == 4


def test_illegal_terminal_state_rollback_is_rejected(tmp_path) -> None:
    store = CandidateStore(tmp_path / "candidate-ledger.jsonl")
    store.create(_candidate())
    store.transition("candidate-1", CandidateStatus.REJECTED, at=NOW)

    with pytest.raises(InvalidCandidateTransition):
        store.transition("candidate-1", CandidateStatus.ELIGIBLE, at=NOW)


def test_processing_error_can_be_retried_without_becoming_quality_rejection(tmp_path) -> None:
    store = CandidateStore(tmp_path / "candidate-ledger.jsonl")
    store.create(_candidate())
    store.transition(
        "candidate-1",
        CandidateStatus.PROCESSING_ERROR,
        at=NOW,
        reason_code=ReasonCode.ANALYSIS_FAILED,
    )
    store.transition("candidate-1", CandidateStatus.ENRICHED, at=NOW + timedelta(hours=1))

    assert store.get("candidate-1").status is CandidateStatus.ENRICHED


def test_corrupt_ledger_requires_explicit_readonly_recovery(tmp_path) -> None:
    path = tmp_path / "candidate-ledger.jsonl"
    valid = _candidate().model_dump_json()
    path.write_text(valid + "\n{broken\n", encoding="utf-8")

    with pytest.raises(CandidateStoreCorruptionError):
        CandidateStore(path)

    recovery = CandidateStore.recover_readonly(path)
    assert [record.candidate_id for record in recovery.records] == ["candidate-1"]
    assert recovery.invalid_lines == [2]
    assert path.read_text(encoding="utf-8").endswith("{broken\n")


def test_legacy_selection_state_only_builds_cooldown_baseline(tmp_path) -> None:
    path = tmp_path / "digest-selection-state.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "items": [
                    {
                        "item_id": "old-item",
                        "url": "https://example.com/old",
                        "event_key": "product:update",
                        "editorial_key": "product|update",
                        "selected_at": NOW.isoformat(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    baseline = load_legacy_cooldown_baseline(path)

    assert baseline[0].event_key == "product:update"
    assert baseline[0].selected_at == NOW
    assert not hasattr(baseline[0], "status")

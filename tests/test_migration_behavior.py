from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from src.storage.candidate_store import CandidateStore, load_legacy_cooldown_baseline


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_old_selectors_are_not_wired_alongside_new_intelligence_pipeline() -> None:
    processing = REPOSITORY_ROOT / "src" / "processing"

    assert not (processing / "breakout_selection.py").exists()
    assert not (processing / "platform_trend_selection.py").exists()
    assert (processing / "intelligence_analysis.py").exists()
    assert (processing / "intelligence_selection.py").exists()


def test_legacy_digest_state_is_read_only_and_does_not_create_candidates(
    tmp_path: Path,
) -> None:
    legacy_path = tmp_path / "digest_selection_state.json"
    candidate_path = tmp_path / "candidate_ledger.jsonl"
    selected_at = datetime(2026, 8, 24, tzinfo=timezone.utc)
    legacy_path.write_text(
        json.dumps(
            {
                "version": 1,
                "items": [
                    {
                        "item_id": "old-item",
                        "event_key": "old:event",
                        "selected_at": selected_at.isoformat(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    baseline = load_legacy_cooldown_baseline(legacy_path)

    assert [row.event_key for row in baseline] == ["old:event"]
    assert CandidateStore(candidate_path).all_latest() == []
    assert not candidate_path.exists()

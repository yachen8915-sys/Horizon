from datetime import datetime, timezone
import json

from src.models import CandidateRecord, CandidateStatus, ContentItem, ReasonCode, SourceType
from src.storage.candidate_export import export_candidates


NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


def _candidate(candidate_id: str, status: CandidateStatus, reason: ReasonCode) -> CandidateRecord:
    return CandidateRecord(
        candidate_id=candidate_id,
        item=ContentItem(
            id=candidate_id,
            source_type=SourceType.RSS,
            title=f"Title {candidate_id}",
            url=f"https://example.com/{candidate_id}",
            published_at=NOW,
        ),
        status=status,
        discovered_at=NOW,
        updated_at=NOW,
        reason_codes=[reason],
        rule_version="v1",
    )


def test_candidate_export_contains_non_selected_lifecycle_states(tmp_path) -> None:
    candidates = [
        _candidate("observing", CandidateStatus.OBSERVING, ReasonCode.IMMATURE),
        _candidate("held", CandidateStatus.HELD, ReasonCode.HELD_BY_CAPACITY),
        _candidate("rejected", CandidateStatus.REJECTED, ReasonCode.LOW_QUALITY),
        _candidate(
            "failed",
            CandidateStatus.PROCESSING_ERROR,
            ReasonCode.ANALYSIS_FAILED,
        ),
    ]

    result = export_candidates(candidates, tmp_path, run_id="run-1")

    rows = [
        json.loads(line)
        for line in result.jsonl_path.read_text(encoding="utf-8").splitlines()
    ]
    assert {row["status"] for row in rows} == {
        "observing",
        "held",
        "rejected",
        "processing_error",
    }
    html = result.html_path.read_text(encoding="utf-8")
    assert 'type="search"' in html
    assert "held_by_capacity" in html
    assert "https://example.com/rejected" in html

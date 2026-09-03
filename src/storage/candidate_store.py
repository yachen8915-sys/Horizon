"""Versioned append-snapshot ledger for intelligence candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path

from pydantic import BaseModel

from .._file_utils import _atomic_write_text
from ..models import (
    CandidateRecord,
    CandidateStatus,
    CandidateStatusTransition,
    ReasonCode,
)


class CandidateStoreError(RuntimeError):
    pass


class CandidateStoreCorruptionError(CandidateStoreError):
    def __init__(self, path: Path, line_number: int):
        super().__init__(f"candidate ledger is corrupt at {path}:{line_number}")
        self.path = path
        self.line_number = line_number


class InvalidCandidateTransition(CandidateStoreError):
    pass


class DuplicateCandidateError(CandidateStoreError):
    pass


@dataclass
class CandidateRecovery:
    records: list[CandidateRecord]
    invalid_lines: list[int]


class LegacyCooldownBaseline(BaseModel):
    item_id: str | None = None
    url: str | None = None
    event_key: str | None = None
    editorial_key: str | None = None
    semantic_key: str | None = None
    selected_at: datetime


ALLOWED_TRANSITIONS: dict[CandidateStatus, set[CandidateStatus]] = {
    CandidateStatus.DISCOVERED: {
        CandidateStatus.ENRICHED,
        CandidateStatus.OBSERVING,
        CandidateStatus.REJECTED,
        CandidateStatus.EXPIRED,
        CandidateStatus.PROCESSING_ERROR,
    },
    CandidateStatus.ENRICHED: {
        CandidateStatus.OBSERVING,
        CandidateStatus.ELIGIBLE,
        CandidateStatus.REJECTED,
        CandidateStatus.EXPIRED,
        CandidateStatus.PROCESSING_ERROR,
    },
    CandidateStatus.OBSERVING: {
        CandidateStatus.ELIGIBLE,
        CandidateStatus.REJECTED,
        CandidateStatus.EXPIRED,
        CandidateStatus.PROCESSING_ERROR,
    },
    CandidateStatus.ELIGIBLE: {
        CandidateStatus.SELECTED,
        CandidateStatus.HELD,
        CandidateStatus.MERGED,
        CandidateStatus.REJECTED,
        CandidateStatus.EXPIRED,
        CandidateStatus.PROCESSING_ERROR,
    },
    CandidateStatus.HELD: {
        CandidateStatus.ELIGIBLE,
        CandidateStatus.SELECTED,
        CandidateStatus.MERGED,
        CandidateStatus.REJECTED,
        CandidateStatus.EXPIRED,
        CandidateStatus.PROCESSING_ERROR,
    },
    CandidateStatus.PROCESSING_ERROR: {
        CandidateStatus.ENRICHED,
        CandidateStatus.OBSERVING,
        CandidateStatus.REJECTED,
        CandidateStatus.EXPIRED,
    },
    CandidateStatus.SELECTED: set(),
    CandidateStatus.MERGED: set(),
    CandidateStatus.REJECTED: set(),
    CandidateStatus.EXPIRED: set(),
}


class CandidateStore:
    def __init__(self, path: Path):
        self.path = path
        self._snapshots: list[CandidateRecord] = []
        self._latest: dict[str, CandidateRecord] = {}
        if path.exists():
            self._load_strict()

    def _load_strict(self) -> None:
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                record = CandidateRecord.model_validate(json.loads(line))
            except Exception as exc:
                raise CandidateStoreCorruptionError(self.path, line_number) from exc
            self._snapshots.append(record)
            self._latest[record.candidate_id] = record

    @classmethod
    def recover_readonly(cls, path: Path) -> CandidateRecovery:
        latest: dict[str, CandidateRecord] = {}
        invalid_lines: list[int] = []
        if not path.exists():
            return CandidateRecovery(records=[], invalid_lines=[])
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                record = CandidateRecord.model_validate(json.loads(line))
            except Exception:
                invalid_lines.append(line_number)
                continue
            latest[record.candidate_id] = record
        return CandidateRecovery(records=list(latest.values()), invalid_lines=invalid_lines)

    def create(self, record: CandidateRecord) -> CandidateRecord:
        existing = self._latest.get(record.candidate_id)
        if existing is not None:
            if existing == record:
                return existing
            raise DuplicateCandidateError(record.candidate_id)
        self._append(record)
        return record

    def get(self, candidate_id: str) -> CandidateRecord | None:
        return self._latest.get(candidate_id)

    def all_latest(self) -> list[CandidateRecord]:
        return list(self._latest.values())

    def record_snapshot(self, record: CandidateRecord) -> CandidateRecord:
        """Persist a pipeline snapshot without replaying historical transitions."""
        existing = self._latest.get(record.candidate_id)
        if existing == record:
            return existing
        self._append(record)
        return record

    def transition(
        self,
        candidate_id: str,
        to_status: CandidateStatus,
        *,
        at: datetime,
        reason_code: ReasonCode | None = None,
        detail: str | None = None,
        merged_into: str | None = None,
    ) -> CandidateRecord:
        current = self._latest.get(candidate_id)
        if current is None:
            raise KeyError(candidate_id)
        allowed = ALLOWED_TRANSITIONS[current.status]
        if to_status not in allowed:
            raise InvalidCandidateTransition(
                f"{candidate_id}: {current.status.value} -> {to_status.value}"
            )
        if to_status is CandidateStatus.MERGED and not merged_into:
            raise InvalidCandidateTransition("merged transition requires merged_into")

        reason_codes = list(current.reason_codes)
        if reason_code is not None and reason_code not in reason_codes:
            reason_codes.append(reason_code)
        transition = CandidateStatusTransition(
            from_status=current.status,
            to_status=to_status,
            changed_at=at,
            reason_code=reason_code,
            detail=detail,
        )
        updated = current.model_copy(
            update={
                "status": to_status,
                "updated_at": at,
                "merged_into": merged_into or current.merged_into,
                "reason_codes": reason_codes,
                "status_history": [*current.status_history, transition],
            },
            deep=True,
        )
        self._append(updated)
        return updated

    def _append(self, record: CandidateRecord) -> None:
        self._snapshots.append(record)
        self._latest[record.candidate_id] = record
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(
            snapshot.model_dump_json() for snapshot in self._snapshots
        )
        _atomic_write_text(self.path, payload + "\n")


def load_legacy_cooldown_baseline(path: Path) -> list[LegacyCooldownBaseline]:
    """Read legacy selection history without creating or replaying candidates."""

    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if payload.get("version") != 1 or not isinstance(payload.get("items"), list):
        return []
    baseline: list[LegacyCooldownBaseline] = []
    for row in payload["items"]:
        if not isinstance(row, dict) or not row.get("selected_at"):
            continue
        try:
            baseline.append(LegacyCooldownBaseline.model_validate(row))
        except Exception:
            continue
    return baseline

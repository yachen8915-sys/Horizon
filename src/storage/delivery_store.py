"""Append-only delivery ledger used for cross-run deduplication."""

from __future__ import annotations

import json
from pathlib import Path

from .._file_utils import _atomic_write_text
from ..models import DeliveryRecord


class DeliveryStoreError(RuntimeError):
    pass


class DeliveryStore:
    def __init__(self, path: Path):
        self.path = path
        self._records: list[DeliveryRecord] = []
        self._ids: set[str] = set()
        if path.exists():
            self._load()

    def _load(self) -> None:
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                record = DeliveryRecord.model_validate(json.loads(line))
            except Exception as exc:
                raise DeliveryStoreError(
                    f"delivery ledger is corrupt at {self.path}:{line_number}"
                ) from exc
            if record.delivery_id in self._ids:
                continue
            self._records.append(record)
            self._ids.add(record.delivery_id)

    def all(self) -> list[DeliveryRecord]:
        return list(self._records)

    def append_many(self, records: list[DeliveryRecord]) -> None:
        changed = False
        for record in records:
            if record.delivery_id in self._ids:
                continue
            self._records.append(record)
            self._ids.add(record.delivery_id)
            changed = True
        if not changed:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(record.model_dump_json() for record in self._records)
        _atomic_write_text(self.path, payload + "\n")

from datetime import datetime, timezone

from src.models import DeliveryRecord, RadarRunMode
from src.storage.delivery_store import DeliveryStore


NOW = datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc)


def _record(delivery_id: str) -> DeliveryRecord:
    return DeliveryRecord(
        delivery_id=delivery_id,
        run_id="morning-run",
        run_mode=RadarRunMode.MORNING,
        delivered_at=NOW,
        candidate_id=f"candidate:{delivery_id}",
        event_key=f"event:{delivery_id}",
        event_version=1,
        display_tier="selected",
        content_fingerprint=f"fingerprint:{delivery_id}",
    )


def test_delivery_store_persists_records_across_runs(tmp_path) -> None:
    path = tmp_path / "delivery.jsonl"
    store = DeliveryStore(path)

    store.append_many([_record("one"), _record("two")])

    restored = DeliveryStore(path).all()
    assert [record.delivery_id for record in restored] == ["one", "two"]


def test_delivery_store_is_idempotent_by_delivery_id(tmp_path) -> None:
    path = tmp_path / "delivery.jsonl"
    store = DeliveryStore(path)
    record = _record("one")

    store.append_many([record])
    store.append_many([record])

    assert [row.delivery_id for row in DeliveryStore(path).all()] == ["one"]

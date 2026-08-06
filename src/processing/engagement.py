"""Lightweight first-seen and one-time 24-hour engagement snapshots."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from .._file_utils import _atomic_write_text
from ..models import ContentItem


class EngagementTracker:
    DEFAULT_THRESHOLDS = {
        "views": {"absolute": 5_000, "relative": 0.5},
        "likes": {"absolute": 200, "relative": 0.5},
        "comments": {"absolute": 50, "relative": 0.5},
        "shares": {"absolute": 50, "relative": 0.5},
        "favorites": {"absolute": 200, "relative": 0.5},
        "stars": {"absolute": 100, "relative": 0.25},
    }

    def __init__(self, state_path: Path, refresh_after_hours: int = 24, thresholds=None):
        self.state_path = Path(state_path)
        self.refresh_after_hours = refresh_after_hours
        self.thresholds = thresholds or self.DEFAULT_THRESHOLDS

    def observe(self, items: list[ContentItem], now: datetime | None = None) -> list[ContentItem]:
        observed_at = self._as_utc(now or datetime.now(timezone.utc))
        state = self.load_state()
        records = state.setdefault("items", {})
        rising: list[ContentItem] = []
        changed = False

        for item in items:
            metrics = self._metrics(item)
            if not metrics:
                continue
            record = records.get(item.id)
            if record is None:
                records[item.id] = {
                    "source_type": item.source_type.value,
                    "url": str(item.url),
                    "title": item.title,
                    "first_seen_at": observed_at.isoformat(),
                    "initial_metrics": metrics,
                    "latest_metrics": metrics,
                    "refreshed_at": None,
                }
                changed = True
                continue

            if record.get("refreshed_at"):
                continue
            first_seen_at = self._parse_datetime(record.get("first_seen_at"))
            if first_seen_at is None:
                continue
            due_at = first_seen_at + timedelta(hours=self.refresh_after_hours)
            if observed_at < due_at:
                continue

            growth, triggered = self._calculate_growth(
                record.get("initial_metrics") or {}, metrics
            )
            record["latest_metrics"] = metrics
            record["refreshed_at"] = observed_at.isoformat()
            record["growth"] = growth
            record["triggered"] = triggered
            item.metadata["engagement_growth"] = {**growth, "triggered": triggered}
            changed = True
            if triggered:
                rising.append(item)

        if changed:
            self._save_state(state)
        return rising

    def load_state(self) -> dict:
        if not self.state_path.exists():
            return {"version": 1, "items": {}}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "items": {}}
        if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
            return {"version": 1, "items": {}}
        return data

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(
            self.state_path,
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        )

    def _calculate_growth(
        self, initial: dict[str, Any], latest: dict[str, int]
    ) -> tuple[dict[str, dict[str, int | float | None]], bool]:
        growth: dict[str, dict[str, int | float | None]] = {}
        triggered = False
        for metric, latest_value in latest.items():
            if metric not in initial:
                continue
            initial_value = self._int(initial.get(metric))
            delta = latest_value - initial_value
            relative = delta / initial_value if initial_value > 0 else None
            growth[metric] = {
                "initial": initial_value,
                "latest": latest_value,
                "delta": delta,
                "relative": relative,
            }
            threshold = self.thresholds.get(metric)
            if not threshold or relative is None:
                continue
            if (
                delta >= self._int(threshold.get("absolute"))
                and relative >= float(threshold.get("relative", 0))
            ):
                triggered = True
        return growth, triggered

    @staticmethod
    def _metrics(item: ContentItem) -> dict[str, int]:
        raw = item.metadata.get("engagement")
        if not isinstance(raw, dict):
            return {}
        return {
            str(key): EngagementTracker._int(value)
            for key, value in raw.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }

    @staticmethod
    def _int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return EngagementTracker._as_utc(datetime.fromisoformat(value))
        except ValueError:
            return None

    @staticmethod
    def _as_utc(moment: datetime) -> datetime:
        if moment.tzinfo is None:
            return moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

"""Programmatic heat scoring and daily state for platform trend candidates.

This module deliberately does not fetch data or call an AI model.  It enriches
already fetched platform-trend items once per normal daily run and persists the
observation only after the caller has completed successfully.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
from pathlib import Path
from typing import Any

from .._file_utils import _atomic_write_text
from ..models import ContentItem, ContentAnalysis, PlatformTrendsConfig


STATE_VERSION = 1


def normalize_trend_key(value: object) -> str:
    """Build a conservative title identity for daily trend observations."""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").casefold())


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(max(value, low), high)


def _percentile(value: float | None, values: list[float]) -> float:
    if value is None or not values:
        return 0.0
    if len(values) == 1:
        return 1.0
    ordered = sorted(values)
    below_or_equal = sum(candidate <= value for candidate in ordered)
    return _clamp((below_or_equal - 1) / (len(ordered) - 1))


def derive_extension_score(analysis: ContentAnalysis | None) -> float | None:
    """Derive the single extension score from the independent AI dimensions."""
    if analysis is None:
        return None
    operations = analysis.operations_score
    opportunity = analysis.content_opportunity_score
    evidence = analysis.evidence_quality_score
    if operations is None and opportunity is None and evidence is None:
        return None
    operations = operations if operations is not None else analysis.score
    opportunity = opportunity if opportunity is not None else analysis.score
    evidence = evidence if evidence is not None else 0.0
    if operations is None or opportunity is None:
        return None
    return round(
        _clamp(
            (float(opportunity) * 0.5 + float(operations) * 0.3 + float(evidence) * 0.2)
            / 10.0
        )
        * 10.0,
        2,
    )


def annotate_extension_score(item: ContentItem) -> None:
    """Persist the derived extension score on platform trend analyses."""
    if not item.processing or not item.processing.analysis:
        return
    score = derive_extension_score(item.processing.analysis)
    if score is None:
        return
    item.processing.analysis = item.processing.analysis.model_copy(
        update={"extension_score": score}
    )
    item.metadata["extension_score"] = score


class PlatformTrendStateStore:
    """Read, stage, and commit a version-1 daily platform-trend state."""

    def __init__(self, config: PlatformTrendsConfig):
        self.path = Path(config.state_file)
        self.history_days = config.history_days
        self._state = self._load()
        self._pending: list[dict[str, Any]] = []

    def prepare(self, items: list[ContentItem], *, now: datetime | None = None) -> None:
        observed_at = _as_utc(now or datetime.now(timezone.utc))
        platform_values: dict[str, list[float]] = {}
        for item in items:
            occurrences = item.metadata.get("platform_occurrences")
            if not isinstance(occurrences, list) or not occurrences:
                occurrences = [
                    {
                        "platform": item.metadata.get("platform"),
                        "hot_value": item.metadata.get("hot_value"),
                    }
                ]
            for occurrence in occurrences:
                if not isinstance(occurrence, dict):
                    continue
                platform = str(occurrence.get("platform") or "unknown")
                value = occurrence.get("hot_value")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    platform_values.setdefault(platform, []).append(float(value))

        existing = self._state.get("items", [])
        by_key = {
            str(record.get("event_key")): record
            for record in existing
            if isinstance(record, dict) and record.get("event_key")
        }
        self._pending = []
        for item in items:
            metadata = item.metadata
            platform = str(metadata.get("platform") or "unknown")
            rank = metadata.get("rank")
            rank_limit = metadata.get("rank_limit") or 30
            occurrences = metadata.get("platform_occurrences")
            if not isinstance(occurrences, list) or not occurrences:
                occurrences = [
                    {
                        "platform": platform,
                        "rank": rank,
                        "hot_value": metadata.get("hot_value"),
                        "rank_limit": rank_limit,
                    }
                ]
            rank_scores: list[float] = []
            hot_percentiles: list[float] = []
            numeric_hots: list[float] = []
            for occurrence in occurrences:
                if not isinstance(occurrence, dict):
                    continue
                occurrence_rank = occurrence.get("rank")
                occurrence_limit = occurrence.get("rank_limit") or rank_limit
                if isinstance(occurrence_rank, (int, float)) and not isinstance(occurrence_rank, bool):
                    rank_scores.append(
                        1.0
                        - _clamp(
                            (float(occurrence_rank) - 1.0)
                            / max(float(occurrence_limit) - 1.0, 1.0)
                        )
                    )
                occurrence_hot = occurrence.get("hot_value")
                numeric_hot = (
                    float(occurrence_hot)
                    if isinstance(occurrence_hot, (int, float))
                    and not isinstance(occurrence_hot, bool)
                    else None
                )
                if numeric_hot is not None:
                    numeric_hots.append(numeric_hot)
                    occurrence_platform = str(occurrence.get("platform") or platform)
                    hot_percentiles.append(
                        _percentile(numeric_hot, platform_values.get(occurrence_platform, []))
                    )
            rank_score = max(rank_scores, default=0.0)
            hot_percentile = max(hot_percentiles, default=0.0)
            numeric_hot = max(numeric_hots, default=None)
            cross_platform_count = int(metadata.get("cross_platform_count") or 1)
            cross_platform_score = _clamp((cross_platform_count - 1) / 2.0)
            heat_score = round(
                10.0
                * (
                    0.45 * rank_score
                    + 0.35 * hot_percentile
                    + 0.20 * cross_platform_score
                ),
                2,
            )
            event_key = normalize_trend_key(
                metadata.get("trend_event_key") or item.title
            )
            record = by_key.get(event_key)
            recent_history = self._recent_history(record, observed_at)
            previous = recent_history[-1] if recent_history else None
            trend_type = self._trend_type(
                heat_score=heat_score,
                rank=rank,
                cross_platform_count=cross_platform_count,
                history=recent_history,
            )
            metadata.update(
                {
                    "trend_event_key": event_key,
                    "heat_score": heat_score,
                    "heat_percentile": round(hot_percentile * 100, 2),
                    "rank_score": round(rank_score * 10, 2),
                    "trend_type": trend_type,
                    "trend_previous_heat_score": (
                        previous.get("heat_score") if previous else None
                    ),
                    "trend_history_count": len(recent_history),
                }
            )
            self._pending.append(
                {
                    "event_key": event_key,
                    "title": item.title,
                    "url": str(item.url),
                    "first_seen": (
                        record.get("first_seen") if record else observed_at.isoformat()
                    ),
                    "last_seen": observed_at.isoformat(),
                    "rank_history": [
                        *[entry for entry in (record or {}).get("rank_history", []) if isinstance(entry, dict)],
                        {
                            "observed_at": observed_at.isoformat(),
                            "platform": platform,
                            "rank": rank,
                            "heat_score": heat_score,
                        },
                    ][-self.history_days :],
                    "hot_value_history": [
                        *[entry for entry in (record or {}).get("hot_value_history", []) if isinstance(entry, dict)],
                        {
                            "observed_at": observed_at.isoformat(),
                            "platform": platform,
                            "hot_value": numeric_hot,
                        },
                    ][-self.history_days :],
                    "platform_sources": sorted(
                        set((record or {}).get("platform_sources", [])) | {platform}
                    ),
                    "cross_platform_count": cross_platform_count,
                    "last_trend_type": trend_type,
                    "last_selected_at": (record or {}).get("last_selected_at"),
                }
            )

    def commit(
        self,
        selected_items: list[ContentItem],
        *,
        now: datetime | None = None,
    ) -> None:
        if not self._pending:
            return
        selected_keys = {
            str(item.metadata.get("trend_event_key"))
            for item in selected_items
            if item.metadata.get("trend_event_key")
        }
        selected_at = _as_utc(now or datetime.now(timezone.utc)).isoformat()
        merged: dict[str, dict[str, Any]] = {
            str(record.get("event_key")): dict(record)
            for record in self._state.get("items", [])
            if isinstance(record, dict) and record.get("event_key")
        }
        for record in self._pending:
            record = dict(record)
            if record.get("event_key") in selected_keys:
                record["last_selected_at"] = selected_at
            merged[str(record["event_key"])] = record
        cutoff = _as_utc(datetime.now(timezone.utc)) - timedelta(days=self.history_days)
        records = []
        for record in merged.values():
            last_seen = self._parse_datetime(record.get("last_seen"))
            if last_seen is not None and last_seen >= cutoff:
                records.append(record)
        records.sort(key=lambda row: str(row.get("last_seen") or ""))
        payload = {"version": STATE_VERSION, "items": records}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(self.path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        self._state = payload
        self._pending = []

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": STATE_VERSION, "items": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": STATE_VERSION, "items": []}
        if payload.get("version") != STATE_VERSION or not isinstance(payload.get("items"), list):
            return {"version": STATE_VERSION, "items": []}
        return {"version": STATE_VERSION, "items": payload["items"]}

    def _recent_history(self, record: dict[str, Any] | None, now: datetime) -> list[dict[str, Any]]:
        if not record:
            return []
        cutoff = now - timedelta(days=self.history_days)
        return [
            entry
            for entry in record.get("rank_history", [])
            if isinstance(entry, dict)
            and (moment := self._parse_datetime(entry.get("observed_at"))) is not None
            and moment >= cutoff
        ]

    @staticmethod
    def _trend_type(
        *,
        heat_score: float,
        rank: object,
        cross_platform_count: int,
        history: list[dict[str, Any]],
    ) -> str:
        if not history:
            return "breaking" if heat_score >= 8.0 or cross_platform_count >= 2 else "current_high"
        if len(history) + 1 >= 3:
            return "fixed"
        previous = history[-1]
        previous_heat = previous.get("heat_score")
        previous_rank = previous.get("rank")
        if isinstance(previous_heat, (int, float)) and heat_score >= float(previous_heat) + 0.5:
            return "rising"
        if isinstance(previous_rank, (int, float)) and isinstance(rank, (int, float)) and rank <= previous_rank - 2:
            return "rising"
        if isinstance(previous_heat, (int, float)) and heat_score <= float(previous_heat) - 1.0:
            return "cooling"
        return "current_high"

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return _as_utc(datetime.fromisoformat(value))
        except ValueError:
            return None

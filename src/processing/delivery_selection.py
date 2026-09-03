"""Morning full selection and afternoon material-delta delivery rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
import hashlib
import json
from typing import Literal
from zoneinfo import ZoneInfo

from ..models import CandidateRecord, DeliveryRecord, RadarRunMode


DeliveryStatus = Literal[
    "send",
    "no_qualified_updates",
    "collection_degraded",
    "pipeline_failed",
]
BEIJING = ZoneInfo("Asia/Shanghai")


@dataclass
class DeliverySelectionResult:
    status: DeliveryStatus
    candidates: list[CandidateRecord] = field(default_factory=list)
    more_candidates: list[CandidateRecord] = field(default_factory=list)
    records: list[DeliveryRecord] = field(default_factory=list)


class DeliverySelector:
    def select(
        self,
        candidates: list[CandidateRecord],
        *,
        more_candidates: list[CandidateRecord] | None = None,
        run_id: str,
        run_mode: RadarRunMode,
        now: datetime,
        deliveries: list[DeliveryRecord] | None = None,
        pipeline_status: Literal[
            "healthy", "collection_degraded", "pipeline_failed"
        ] = "healthy",
    ) -> DeliverySelectionResult:
        if pipeline_status != "healthy":
            return DeliverySelectionResult(status=pipeline_status)

        observed_at = self._utc(now)
        same_day = [
            delivery
            for delivery in (deliveries or [])
            if self._utc(delivery.delivered_at).date() == observed_at.date()
        ]
        delivered_versions = {
            (delivery.event_key, delivery.event_version) for delivery in same_day
        }
        if run_mode is RadarRunMode.AFTERNOON:
            morning_times = [
                self._utc(delivery.delivered_at)
                for delivery in same_day
                if delivery.run_mode is RadarRunMode.MORNING
            ]
            local_day = observed_at.astimezone(BEIJING).date()
            scheduled_morning = datetime.combine(
                local_day,
                time(hour=9),
                tzinfo=BEIJING,
            ).astimezone(timezone.utc)
            cutoff = (
                max([scheduled_morning, *morning_times])
                if morning_times
                else scheduled_morning
            )
        else:
            cutoff = None

        def filter_new(rows: list[CandidateRecord]) -> list[CandidateRecord]:
            result: list[CandidateRecord] = []
            for candidate in rows:
                event_key = candidate.event_key or candidate.candidate_id
                version_key = (event_key, candidate.event_version)
                if version_key in delivered_versions:
                    continue
                version_at = candidate.event_version_at or candidate.updated_at
                if cutoff is not None and self._utc(version_at) <= cutoff:
                    continue
                result.append(candidate)
            return result

        selected = filter_new(candidates)
        more = filter_new(more_candidates or [])

        if not selected and not more:
            return DeliverySelectionResult(status="no_qualified_updates")
        records = [
            self._record(candidate, run_id, run_mode, observed_at, "selected")
            for candidate in selected
        ]
        records.extend(
            self._record(candidate, run_id, run_mode, observed_at, "more")
            for candidate in more
        )
        return DeliverySelectionResult(
            status="send",
            candidates=selected,
            more_candidates=more,
            records=records,
        )

    @staticmethod
    def _record(
        candidate: CandidateRecord,
        run_id: str,
        run_mode: RadarRunMode,
        delivered_at: datetime,
        display_tier: Literal["selected", "more"],
    ) -> DeliveryRecord:
        event_key = candidate.event_key or candidate.candidate_id
        fingerprint_payload = {
            "candidate_id": candidate.candidate_id,
            "event_key": event_key,
            "event_version": candidate.event_version,
            "title": candidate.item.title,
            "decision_summary": (
                candidate.intelligence.decision_summary
                if candidate.intelligence
                else ""
            ),
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:20]
        return DeliveryRecord(
            delivery_id=f"{run_id}:{candidate.candidate_id}:v{candidate.event_version}",
            run_id=run_id,
            run_mode=run_mode,
            delivered_at=delivered_at,
            candidate_id=candidate.candidate_id,
            event_key=event_key,
            event_version=candidate.event_version,
            display_tier=display_tier,
            content_fingerprint=fingerprint,
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

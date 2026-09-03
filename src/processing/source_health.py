"""Source registry and health contracts for the intelligence radar."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
import json
from pathlib import Path
import re

from pydantic import BaseModel, Field, model_validator


class SourceRole(str, Enum):
    CONFIRM = "confirm"
    DISCOVER = "discover"
    BACKFILL = "backfill"


class SourceAuthority(str, Enum):
    OFFICIAL = "official"
    PRIMARY = "primary"
    SECONDARY = "secondary"
    AGGREGATOR = "aggregator"


class SourceLifecycle(str, Enum):
    ACTIVE = "active"
    SHADOW = "shadow"
    PLANNED = "planned"
    DISABLED = "disabled"
    COVERAGE_GAP = "coverage_gap"


class SourceHealthStatus(str, Enum):
    HEALTHY = "healthy"
    STALE = "stale"
    DEGRADED = "degraded"
    FAILED = "failed"
    DISABLED = "disabled"
    COVERAGE_GAP = "coverage_gap"


class SourceRegistryEntry(BaseModel):
    source_id: str
    config_ref: str
    decision_lanes: list[str]
    entities: list[str] = Field(default_factory=list)
    role: SourceRole
    authority: SourceAuthority
    access_method: str
    fallback_source_ids: list[str] = Field(default_factory=list)
    expected_cadence_hours: float = Field(default=24, gt=0)
    required_fields: list[str] = Field(
        default_factory=lambda: ["title", "url", "published_at"]
    )
    watermark_strategy: str = "published_at_with_overlap"
    fact_ceiling: str = "reported"
    lifecycle: SourceLifecycle = SourceLifecycle.ACTIVE


class CoverageRequirement(BaseModel):
    decision_lane: str
    required_roles: list[SourceRole]
    minimum_operational_sources: int = Field(default=1, ge=1)
    required_source_ids: list[str] = Field(default_factory=list)


class CoverageGap(BaseModel):
    decision_lane: str
    missing_roles: list[str] = Field(default_factory=list)
    missing_source_ids: list[str] = Field(default_factory=list)
    operational_sources: int = 0
    minimum_operational_sources: int = 1


class CoverageReport(BaseModel):
    ready: bool
    gaps: list[CoverageGap] = Field(default_factory=list)


class CoreEntity(BaseModel):
    entity_id: str
    display_name: str
    priority: str = "core"
    required_roles: list[SourceRole] = Field(
        default_factory=lambda: [SourceRole.CONFIRM, SourceRole.DISCOVER]
    )


class CoreEntityRegistry(BaseModel):
    version: int
    entities: list[CoreEntity] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "CoreEntityRegistry":
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))


class EntityCoverageGap(BaseModel):
    entity_id: str
    missing_roles: list[str] = Field(default_factory=list)


class EntityCoverageReport(BaseModel):
    ready: bool
    gaps: list[EntityCoverageGap] = Field(default_factory=list)


class SourceRegistry(BaseModel):
    version: int
    requirements: list[CoverageRequirement] = Field(default_factory=list)
    sources: list[SourceRegistryEntry] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "SourceRegistry":
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))

    @model_validator(mode="after")
    def validate_registry_links(self) -> "SourceRegistry":
        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            duplicates = sorted(
                source_id
                for source_id in set(source_ids)
                if source_ids.count(source_id) > 1
            )
            raise ValueError(f"duplicate source_id: {', '.join(duplicates)}")

        known_ids = set(source_ids)
        for source in self.sources:
            unknown = sorted(set(source.fallback_source_ids) - known_ids)
            if unknown:
                raise ValueError(
                    f"unknown fallback for {source.source_id}: {', '.join(unknown)}"
                )
        for requirement in self.requirements:
            unknown = sorted(set(requirement.required_source_ids) - known_ids)
            if unknown:
                raise ValueError(
                    "unknown required source for "
                    f"{requirement.decision_lane}: {', '.join(unknown)}"
                )
        return self

    def coverage_report(self, *, production: bool) -> CoverageReport:
        allowed_lifecycles = {SourceLifecycle.ACTIVE}
        if not production:
            allowed_lifecycles.add(SourceLifecycle.SHADOW)

        gaps: list[CoverageGap] = []
        for requirement in self.requirements:
            matching = [
                source
                for source in self.sources
                if requirement.decision_lane in source.decision_lanes
                and source.lifecycle in allowed_lifecycles
            ]
            roles = {source.role for source in matching}
            missing_roles = [
                role.value for role in requirement.required_roles if role not in roles
            ]
            operational_ids = {source.source_id for source in matching}
            missing_source_ids = [
                source_id
                for source_id in requirement.required_source_ids
                if source_id not in operational_ids
            ]
            if (
                missing_roles
                or missing_source_ids
                or len(matching) < requirement.minimum_operational_sources
            ):
                gaps.append(
                    CoverageGap(
                        decision_lane=requirement.decision_lane,
                        missing_roles=missing_roles,
                        missing_source_ids=missing_source_ids,
                        operational_sources=len(matching),
                        minimum_operational_sources=requirement.minimum_operational_sources,
                    )
                )
        return CoverageReport(ready=not gaps, gaps=gaps)

    def entity_coverage_report(
        self,
        registry: CoreEntityRegistry,
        *,
        production: bool,
    ) -> EntityCoverageReport:
        allowed_lifecycles = {SourceLifecycle.ACTIVE}
        if not production:
            allowed_lifecycles.add(SourceLifecycle.SHADOW)
        gaps: list[EntityCoverageGap] = []
        for entity in registry.entities:
            roles = {
                source.role
                for source in self.sources
                if entity.entity_id in source.entities
                and source.lifecycle in allowed_lifecycles
            }
            missing = [
                role.value for role in entity.required_roles if role not in roles
            ]
            if missing:
                gaps.append(
                    EntityCoverageGap(
                        entity_id=entity.entity_id,
                        missing_roles=missing,
                    )
                )
        return EntityCoverageReport(ready=not gaps, gaps=gaps)

    def missing_config_families(self, configured_families: set[str]) -> set[str]:
        covered: set[str] = set()
        for source in self.sources:
            match = re.match(r"^sources\.([a-z_]+)", source.config_ref)
            if match:
                covered.add(match.group(1))
        return configured_families - covered


class SourceHealthObservation(BaseModel):
    source_id: str
    checked_at: datetime
    enabled: bool = True
    coverage_gap: bool = False
    credentials_ok: bool = True
    transport_ok: bool = True
    schema_ok: bool = True
    business_ok: bool = True
    business_code: str | None = None
    error: str | None = None
    item_count: int = Field(default=0, ge=0)
    unexpected_empty: bool = False
    newest_item_at: datetime | None = None
    expected_cadence_hours: float = Field(default=24, gt=0)
    stale_after_multiplier: float = Field(default=3, gt=0)


class SourceHealthResult(BaseModel):
    source_id: str
    status: SourceHealthStatus
    reason_code: str
    detail: str = ""


def assess_source_health(observation: SourceHealthObservation) -> SourceHealthResult:
    if not observation.enabled:
        return SourceHealthResult(
            source_id=observation.source_id,
            status=SourceHealthStatus.DISABLED,
            reason_code="disabled",
        )
    if observation.coverage_gap:
        return SourceHealthResult(
            source_id=observation.source_id,
            status=SourceHealthStatus.COVERAGE_GAP,
            reason_code="coverage_gap",
        )
    if not observation.credentials_ok:
        return SourceHealthResult(
            source_id=observation.source_id,
            status=SourceHealthStatus.FAILED,
            reason_code="missing_credentials",
            detail=observation.error or "required credentials are unavailable",
        )
    if not observation.transport_ok:
        return SourceHealthResult(
            source_id=observation.source_id,
            status=SourceHealthStatus.FAILED,
            reason_code="transport_error",
            detail=observation.error or "transport failed",
        )
    if not observation.schema_ok:
        return SourceHealthResult(
            source_id=observation.source_id,
            status=SourceHealthStatus.FAILED,
            reason_code="schema_error",
            detail=observation.error or "response schema did not match",
        )
    if not observation.business_ok:
        detail_parts = [
            part
            for part in (observation.business_code, observation.error)
            if part
        ]
        return SourceHealthResult(
            source_id=observation.source_id,
            status=SourceHealthStatus.FAILED,
            reason_code="business_error",
            detail=": ".join(detail_parts) or "business response reported failure",
        )
    if observation.unexpected_empty:
        return SourceHealthResult(
            source_id=observation.source_id,
            status=SourceHealthStatus.DEGRADED,
            reason_code="unexpected_empty",
            detail="source returned no items outside its expected empty pattern",
        )
    if observation.newest_item_at is not None:
        stale_after = timedelta(
            hours=(
                observation.expected_cadence_hours
                * observation.stale_after_multiplier
            )
        )
        if observation.checked_at - observation.newest_item_at > stale_after:
            return SourceHealthResult(
                source_id=observation.source_id,
                status=SourceHealthStatus.STALE,
                reason_code="stale_data",
                detail=f"newest item is older than {stale_after}",
            )
    return SourceHealthResult(
        source_id=observation.source_id,
        status=SourceHealthStatus.HEALTHY,
        reason_code="healthy",
    )


class SourceWatermark(BaseModel):
    source_id: str
    last_success_at: datetime | None = None
    last_native_id: str | None = None
    gap_started_at: datetime | None = None

    def next_since(self, *, default_since: datetime, overlap_minutes: int) -> datetime:
        anchor = self.gap_started_at or self.last_success_at
        if anchor is None:
            return default_since
        return max(default_since, anchor - timedelta(minutes=overlap_minutes))


class SourceShadowRun(BaseModel):
    run_id: str
    checked_at: datetime
    since: datetime
    item_count: int = Field(ge=0)
    unique_item_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    new_item_count: int = Field(default=0, ge=0)
    repeated_item_count: int = Field(default=0, ge=0)
    health_counts: dict[str, int] = Field(default_factory=dict)
    snapshot_jsonl_path: str | None = None
    snapshot_html_path: str | None = None


class SourceHealthLedger(BaseModel):
    """Small append-style shadow ledger for source stability and gap recovery."""

    version: int = 1
    watermarks: dict[str, SourceWatermark] = Field(default_factory=dict)
    seen_item_ids: dict[str, datetime] = Field(default_factory=dict)
    runs: list[SourceShadowRun] = Field(default_factory=list)
    max_runs: int = Field(default=32, ge=7, le=366)

    @classmethod
    def load(cls, path: Path) -> "SourceHealthLedger":
        if not path.exists():
            return cls()
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            self.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def record_run(
        self,
        *,
        run_id: str,
        checked_at: datetime,
        since: datetime,
        item_ids: list[str],
        health_rows: list[dict[str, object]],
        snapshot_jsonl_path: str | None = None,
        snapshot_html_path: str | None = None,
    ) -> SourceShadowRun:
        health_counts: dict[str, int] = {}
        for row in health_rows:
            source_id = str(row.get("source_id") or "").strip()
            status = str(row.get("status") or "unknown")
            reason_code = str(row.get("reason_code") or "")
            health_counts[status] = health_counts.get(status, 0) + 1
            if not source_id:
                continue
            watermark = self.watermarks.setdefault(
                source_id, SourceWatermark(source_id=source_id)
            )
            if status in {
                SourceHealthStatus.HEALTHY.value,
                SourceHealthStatus.STALE.value,
            } or (
                status == SourceHealthStatus.DEGRADED.value
                and reason_code != "budget_exhausted"
            ):
                watermark.last_success_at = checked_at
                watermark.gap_started_at = None
            elif status == SourceHealthStatus.DISABLED.value:
                watermark.gap_started_at = None
            elif status == SourceHealthStatus.FAILED.value:
                watermark.gap_started_at = watermark.gap_started_at or since

        unique_ids = set(item_ids)
        unique_count = len(unique_ids)
        repeated_count = sum(
            1 for item_id in unique_ids if item_id in self.seen_item_ids
        )
        new_count = unique_count - repeated_count
        for item_id in unique_ids:
            self.seen_item_ids[item_id] = checked_at
        prune_before = checked_at - timedelta(days=30)
        self.seen_item_ids = {
            item_id: last_seen
            for item_id, last_seen in self.seen_item_ids.items()
            if last_seen >= prune_before
        }
        run = SourceShadowRun(
            run_id=run_id,
            checked_at=checked_at,
            since=since,
            item_count=len(item_ids),
            unique_item_count=unique_count,
            duplicate_count=len(item_ids) - unique_count,
            new_item_count=new_count,
            repeated_item_count=repeated_count,
            health_counts=health_counts,
            snapshot_jsonl_path=snapshot_jsonl_path,
            snapshot_html_path=snapshot_html_path,
        )
        self.runs.append(run)
        self.runs = self.runs[-self.max_runs :]
        return run

    def next_since(
        self, *, default_since: datetime, overlap_minutes: int = 15
    ) -> datetime:
        if not self.watermarks:
            return default_since
        return min(
            watermark.next_since(
                default_since=default_since,
                overlap_minutes=overlap_minutes,
            )
            for watermark in self.watermarks.values()
        )

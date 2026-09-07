"""One explainable report spanning collection, candidates, selection, and cost."""

from __future__ import annotations

from collections import Counter
from collections import defaultdict
from dataclasses import fields
from typing import Any

from ..models import CandidateRecord, CandidateStatus, ReasonCode
from ..processing.coverage_notice import build_platform_coverage_notice
from ..processing.intelligence_presentation import build_intelligence_presentation


def build_intelligence_report(
    *,
    run_id: str,
    candidates: list[CandidateRecord],
    fetch_report: dict[str, Any] | None,
    ai_usage: dict[str, Any] | None = None,
    card_capacity: dict[str, Any] | None = None,
    run_mode: str | None = None,
) -> dict[str, Any]:
    fetch_report = fetch_report or {"status": "not_attempted", "sources": []}
    fetch_status = str(fetch_report.get("status") or "not_attempted")
    if fetch_status == "failure":
        pipeline_status = "pipeline_failed"
    elif fetch_status == "partial_failure":
        pipeline_status = "collection_degraded"
    elif any(candidate.status.value == "selected" for candidate in candidates):
        pipeline_status = "ready"
    else:
        pipeline_status = "no_qualified_updates"

    presentation = build_intelligence_presentation(
        [candidate for candidate in candidates if candidate.status is CandidateStatus.SELECTED],
        [candidate for candidate in candidates if candidate.status is CandidateStatus.HELD
         and ReasonCode.HELD_BY_CAPACITY in candidate.reason_codes],
    )
    funnel = Counter(candidate.status.value for candidate in candidates)
    lanes = Counter(
        candidate.intelligence.primary_lane.value
        for candidate in candidates
        if candidate.intelligence is not None
    )
    reasons = Counter(
        reason.value
        for candidate in candidates
        for reason in candidate.reason_codes
    )
    authors = Counter(
        (candidate.item.author or "unknown").strip() or "unknown"
        for candidate in candidates
    )
    sources = Counter(
        str(
            candidate.item.metadata.get("source_id")
            or candidate.item.source_type.value
        )
        for candidate in candidates
    )
    platforms = Counter(
        str(
            candidate.item.metadata.get("content_platform")
            or candidate.item.source_type.value
        )
        for candidate in candidates
    )
    topics = Counter(
        candidate.editorial_topic_key or candidate.event_key or candidate.candidate_id
        for candidate in candidates
    )
    merge_groups: dict[str, list[str]] = defaultdict(list)
    for candidate in candidates:
        if candidate.merged_into:
            merge_groups[candidate.merged_into].append(candidate.candidate_id)

    source_health: list[dict[str, Any]] = []
    for source in fetch_report.get("sources") or []:
        if not isinstance(source, dict):
            continue
        granular: list[dict[str, Any]] = []
        for key in ("source_health", "providers", "feeds", "watchers", "health"):
            values = source.get(key)
            if isinstance(values, list):
                granular.extend(row for row in values if isinstance(row, dict))
        if granular:
            source_health.extend(granular)
        else:
            source_health.append(
                {
                    "source_id": source.get("source"),
                    "status": source.get("status"),
                    "reason_code": source.get("error") or source.get("status"),
                }
            )

    return {
        "run_id": run_id,
        "run_mode": run_mode,
        "pipeline_status": pipeline_status,
        "source_health": source_health,
        "presentation_categories": {
            field.name: len(getattr(presentation, field.name))
            for field in fields(presentation)
        },
        "trend_pools": {
            "leverage": len(presentation.hot_leverage),
            "watch": len(presentation.hot_watch),
        },
        "coverage_notice": build_platform_coverage_notice(fetch_report),
        "funnel": dict(sorted(funnel.items())),
        "decision_lanes": dict(sorted(lanes.items())),
        "reason_codes": dict(sorted(reasons.items())),
        "merge_groups": {
            primary: sorted(candidate_ids)
            for primary, candidate_ids in sorted(merge_groups.items())
        },
        "concentration": {
            "authors": _concentration(authors),
            "sources": _concentration(sources),
            "platforms": _concentration(platforms),
            "topics": _concentration(topics),
        },
        "ai_usage": ai_usage
        or {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost": None,
            "cost_status": "pricing_not_configured",
            "cache_hits": 0,
            "degraded_calls": 0,
        },
        "card_capacity": card_capacity
        or {"selected": 0, "more": 0, "truncated": 0},
        "candidate_count": len(candidates),
    }


def _concentration(counter: Counter[str]) -> dict[str, Any]:
    total = sum(counter.values())
    if total == 0:
        return {"top_key": None, "top_count": 0, "top_share": 0.0}
    top_key, top_count = counter.most_common(1)[0]
    return {
        "top_key": top_key,
        "top_count": top_count,
        "top_share": top_count / total,
    }

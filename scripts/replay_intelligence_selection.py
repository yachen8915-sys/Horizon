"""Read one archived run and replay content policy without collection or delivery.

Scores, lane assignments and upstream exclusions are historical evidence. This
does not reanalyze content, refresh timestamps, or simulate a new shadow run.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import fields
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from pydantic import ValidationError

from src.diagnostics.intelligence_report import build_intelligence_report
from src.models import CandidateRecord, CandidateStatus, IntelligenceRadarConfig, RadarRunMode, ReasonCode
from src.processing.candidate_pipeline import CandidateBuilder
from src.processing.intelligence_presentation import build_intelligence_presentation
from src.processing.intelligence_selection import IntelligenceSelector
from src.storage.manager import StorageManager


def replay_archive(archive_root: Path, *, config: IntelligenceRadarConfig) -> dict[str, Any]:
    """Deterministic read-only replay; all output writes belong to main().

    Accept one recursively discovered candidates-*.json[l] snapshot, with an
    optional matching diagnostics-*.json. Refuse ambiguous multi-run roots.
    """
    root = archive_root.resolve()
    if not root.is_dir():
        raise ValueError("Archive root must be an existing directory")
    candidate_files = sorted({*root.rglob("candidates-*.jsonl"), *root.rglob("candidates-*.json")})
    if len(candidate_files) != 1:
        raise ValueError(f"Expected one candidate snapshot, found {len(candidate_files)}")
    source = candidate_files[0]
    if not source.resolve().is_relative_to(root):
        raise ValueError("Candidate snapshot resolves outside the supplied archive")
    text = source.read_text(encoding="utf-8-sig")
    raw_rows = (
        [json.loads(line) for line in text.splitlines() if line.strip()]
        if source.suffix == ".jsonl" else json.loads(text)
    )
    if not isinstance(raw_rows, list) or any(not isinstance(row, dict) for row in raw_rows):
        raise ValueError("Candidate snapshot must contain objects in a JSON list or JSONL")

    # Only use the diagnostic that belongs to this exact snapshot.
    diagnostic_path = source.with_name(source.stem.replace("candidates-", "diagnostics-", 1) + ".json")
    limitations = [
        "Archived scores and lanes are unchanged; no new AI analysis or freshness collection was run.",
        "Configured delivery ledger is not read; historical delivery cooldown cannot be reconstructed.",
        "Exact event/topic selection rules are reused; no fuzzy or title-based deduplication is performed.",
    ]
    fetch_report = None
    if diagnostic_path.exists():
        if not diagnostic_path.resolve().is_relative_to(root):
            raise ValueError("Diagnostic snapshot resolves outside the supplied archive")
        diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8-sig"))
        if not isinstance(diagnostic, dict):
            raise ValueError("Diagnostic snapshot must be a JSON object")
        fetch_report = diagnostic.get("fetch_report")
        if fetch_report is not None:
            if not isinstance(fetch_report, dict) or not isinstance(fetch_report.get("sources", []), list):
                raise ValueError("Malformed diagnostic fetch_report")
        elif "source_health" in diagnostic:
            health = diagnostic["source_health"]
            if not isinstance(health, list) or any(not isinstance(row, dict) for row in health):
                raise ValueError("Malformed diagnostic source_health")
            trend_health = [row for row in health if str(row.get("source_id", "")).startswith("platform-trends:")]
            fetch_report = {"status": "not_attempted", "sources": [{
                "source": "Platform Trends", "source_health": trend_health,
            }]}
            limitations.append("Legacy flat source_health adapted for coverage only; aggregate fetch status is unavailable.")
        else:
            limitations.append("Diagnostic lacks fetch_report/source_health; coverage is unknown.")
    else:
        limitations.append("Matching diagnostic is absent; coverage is unknown.")

    effective = config.model_copy(update={
        "delivery_enabled": False, "canary_mode": False, "run_mode": RadarRunMode.SHADOW,
    }, deep=True)
    builder = CandidateBuilder(effective.rule_version, effective.selection.minimum_score)
    compatible: list[CandidateRecord] = []
    incompatible: list[dict[str, Any]] = []
    archived_by_id: dict[str, CandidateRecord] = {}
    protected_reasons = {
        ReasonCode.IMMATURE, ReasonCode.INCOMPLETE_ENGAGEMENT, ReasonCode.DUPLICATE,
        ReasonCode.MERGED_INTO_STRONGER_EVIDENCE, ReasonCode.EXPIRED,
        ReasonCode.ANALYSIS_FAILED, ReasonCode.PROCESSING_ERROR,
    }
    protected_statuses = {
        CandidateStatus.DISCOVERED, CandidateStatus.OBSERVING, CandidateStatus.MERGED,
        CandidateStatus.EXPIRED, CandidateStatus.PROCESSING_ERROR,
    }
    for index, row in enumerate(raw_rows, 1):
        try:
            candidate = CandidateRecord.model_validate(row)
            if candidate.candidate_id in archived_by_id:
                raise ValueError("Duplicate candidate_id in snapshot")
            archived_by_id[candidate.candidate_id] = candidate
            protected = (
                candidate.status in protected_statuses or candidate.merged_into
                or protected_reasons.intersection(candidate.reason_codes)
                or candidate.item.metadata.get("engagement_gate_status") in {"observing", "rejected"}
            )
            if protected:
                # Do not accidentally let legacy eligible/selected labels override upstream gates.
                if candidate.status in {CandidateStatus.ENRICHED, CandidateStatus.ELIGIBLE, CandidateStatus.SELECTED, CandidateStatus.HELD}:
                    candidate = candidate.model_copy(update={"status": CandidateStatus.REJECTED}, deep=True)
                compatible.append(candidate)
                continue
            if candidate.intelligence is None:
                raise ValueError("No archived intelligence for content-gate replay")
            candidate = builder.apply_content_type_gate(candidate.model_copy(update={
                "status": CandidateStatus.ENRICHED, "reason_codes": [], "status_history": [],
            }, deep=True))
            if candidate.status is CandidateStatus.ELIGIBLE:
                # Expose old lanes/profiles that cannot map to the current presentation.
                build_intelligence_presentation([candidate], [])
            compatible.append(candidate)
        except (ValidationError, ValueError) as exc:
            incompatible.append({
                "file": source.relative_to(root).as_posix(), "record": index,
                "candidate_id": row.get("candidate_id"),
                "errors": (
                    [{"location": list(error["loc"]), "type": error["type"]}
                     for error in exc.errors(include_input=False, include_url=False)]
                    if isinstance(exc, ValidationError) else [{"type": str(exc)}]
                ),
            })

    now = max((candidate.updated_at for candidate in compatible), default=None)
    selection = IntelligenceSelector(effective.selection).select(compatible, deliveries=[], now=now)
    outcomes = {row.candidate_id: row for row in [*selection.selected, *selection.held, *selection.rejected]}
    final = [outcomes.get(row.candidate_id, row) for row in compatible]
    more = [row for row in selection.held if ReasonCode.HELD_BY_CAPACITY in row.reason_codes]
    presentation = build_intelligence_presentation(selection.selected, more)
    categories = {field.name: [row.item.title for row in getattr(presentation, field.name)] for field in fields(presentation)}
    diagnostics = build_intelligence_report(
        run_id=source.stem.removeprefix("candidates-"), candidates=final,
        fetch_report=fetch_report, run_mode="shadow",
        card_capacity={"selected": len(selection.selected), "more": len(more), "truncated": 0},
    )
    reasons_by_id = {
        row.candidate_id: [reason.value for reason in row.reason_codes if reason not in {ReasonCode.PASSED_HARD_GATES, ReasonCode.SELECTED}]
        for row in final if row.status is not CandidateStatus.SELECTED
    }
    return {
        "counts": {"archived": len(raw_rows), "compatible": len(compatible), "incompatible": len(incompatible),
                   "selected": len(selection.selected), "held": len(selection.held),
                   "more": len(more), "statuses": dict(sorted(Counter(row.status.value for row in final).items()))},
        "selected_titles": [row.item.title for row in selection.selected],
        "held_titles": [row.item.title for row in selection.held],
        "reject_reasons": reasons_by_id,
        "presentation_categories": categories,
        "trend_pools": {pool: [row.item.title for row in [*selection.selected, *more] if row.item.metadata.get("trend_pool") == pool]
                        for pool in ("leverage", "watch")},
        "coverage_notice": diagnostics["coverage_notice"],
        "incompatible_records": incompatible,
        "effective_config": effective.model_dump(mode="json"),
        "limitations": limitations,
        "diagnostics": diagnostics,
        "records": [{
            "candidate_id": row.candidate_id, "title": row.item.title,
            "archived_status": archived_by_id[row.candidate_id].status.value,
            "status": row.status.value, "reason_codes": [reason.value for reason in row.reason_codes],
            "lane": row.intelligence.primary_lane.value if row.intelligence else None,
            "event_key": row.event_key, "editorial_topic_key": row.editorial_topic_key,
            "source_item_id": row.source_item_id, "canonical_url": row.canonical_url,
            "trend_pool": row.item.metadata.get("trend_pool"),
            "signals": {key: row.item.metadata.get(key) for key in ("providers", "platforms", "platform", "rank", "hot_value")},
            "scores": ({key: getattr(row.item.processing.analysis, key) for key in
                        ("score", "operations_score", "content_opportunity_score", "operations_focus")}
                       if row.item.processing and row.item.processing.analysis else None),
        } for row in final],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        output = args.output.resolve()
        if output.is_relative_to(args.input.resolve()):
            raise ValueError("Output must be outside the read-only input archive")
        # Reuse the existing loader without StorageManager.__init__ mkdir side effects.
        config = StorageManager.load_config(SimpleNamespace(config_path=REPOSITORY_ROOT / "data/config.github.json"))
        for ledger in (config.intelligence.candidate_store_file, config.intelligence.delivery_store_file):
            if output in {(REPOSITORY_ROOT / ledger).resolve(), Path(ledger).resolve()}:
                raise ValueError("Output must not overwrite a configured ledger")
        report = replay_archive(args.input, config=config.intelligence)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"Replay failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

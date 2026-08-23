import json
from datetime import datetime, timezone
from pathlib import Path

from src.models import (
    ClassificationResult,
    ContentAnalysis,
    ContentItem,
    DigestConfig,
    EditorialSelectionConfig,
    ProcessingResult,
    SourceType,
)
from src.orchestrator import HorizonOrchestrator
from src.processing.breakout_selection import BreakoutSelector, canonical_identity
from src.processing.editorial_selection import EditorialSelector


FIXTURE = Path(__file__).parent / "fixtures" / "ai_breakout_replay_2026-08-23.json"


def _load_items(tmp_path: Path) -> tuple[list[ContentItem], DigestConfig]:
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
    items: list[ContentItem] = []
    for row in rows:
        metadata = {
            key: row[key]
            for key in ("aihot_score", "heat_score", "ai_media_candidate")
            if key in row
        }
        items.append(
            ContentItem(
                id=row["id"],
                source_type=SourceType(row["source_type"]),
                title=row["title"],
                url=row["url"],
                published_at=datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
                profile=row["profile"],
                metadata=metadata,
                processing=ProcessingResult(
                    classification=ClassificationResult(
                        profile=row["profile"], method="source_override"
                    ),
                    analysis=ContentAnalysis(
                        score=row["score"],
                        reason="sanitized replay",
                        summary=row["title"],
                        primary_entity=row["id"],
                        topic_cluster=row["topic_cluster"],
                        canonical_theme=row["canonical_theme"],
                        use_case=row["use_case"],
                        content_format=row["content_format"],
                        novelty_level=row["novelty_level"],
                        event_key=row["event_key"],
                        editorial_key=(
                            f'{row["id"]}|{row["use_case"]}|{row["content_format"]}'
                        ),
                        relevance_score=row["score"],
                        novelty_score=row["score"],
                        demonstrability_score=row["actionability"],
                        evidence_quality_score=row["evidence"],
                        audience_breadth_score=row["audience"],
                        surprise_score=row["surprise"],
                        actionability_score=row["actionability"],
                        breakout_reason="sanitized replay",
                    ),
                ),
            )
        )
    config = DigestConfig(
        controversial_topic_limit=3,
        editorial_selection=EditorialSelectionConfig(
            enabled=True,
            state_file=str(tmp_path / "digest-state.json"),
            same_day_semantic_limit=1,
        ),
    )
    return items, config


def test_august_23_replay_has_no_section_repeat_and_prioritizes_real_contrast(
    tmp_path: Path,
) -> None:
    items, config = _load_items(tmp_path)
    breakout = BreakoutSelector(config)
    breakout.annotate(items)
    editorial = EditorialSelector(config.editorial_selection).select(items)
    result = breakout.select(editorial.items, annotate=False)
    ordered = sorted(
        result.items,
        key=HorizonOrchestrator._selection_sort_key,
        reverse=True,
    )

    identities = [canonical_identity(item) for item in ordered]
    for index, current in enumerate(identities):
        for other in identities[index + 1 :]:
            assert not set(value for value in current if value).intersection(
                value for value in other if value
            )
    tutorials = [
        item
        for item in ordered
        if item.processing.analysis.canonical_theme
        == "ai_narrative_video_workflow"
    ]
    assert len(tutorials) == 1
    assert editorial.exclusions["short-drama-repackage"].reason == (
        "semantic_theme_repeat"
    )
    assert ordered.index(next(item for item in ordered if item.id == "unitree-contrast")) < (
        ordered.index(next(item for item in ordered if item.id == "end-of-heat"))
    )
    assert next(
        item for item in ordered if item.id == "unitree-contrast"
    ).metadata["breakout_status"] == "breakout"

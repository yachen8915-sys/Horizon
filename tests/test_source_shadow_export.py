from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from src.models import ContentItem, SourceType
from src.storage.source_shadow_export import export_source_shadow_items


def _item() -> ContentItem:
    return ContentItem(
        id="youtube:video:abc",
        source_type=SourceType.YOUTUBE,
        title="Fresh AI workflow",
        url="https://www.youtube.com/watch?v=abc",
        author="Useful Creator",
        published_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        metadata={
            "category": "overseas-ai-video",
            "source_access": "yt_dlp_web",
            "engagement": {"views": 42000, "likes": 2100},
            "engagement_gate_status": "passed",
            "engagement_gate_reason": "qualified",
        },
    )


def test_source_shadow_export_keeps_reviewable_raw_items(tmp_path) -> None:
    result = export_source_shadow_items(
        [("YouTube Data", _item())],
        tmp_path,
        run_id="source-shadow-20260903T010000Z",
    )

    row = json.loads(result.jsonl_path.read_text(encoding="utf-8"))
    html = result.html_path.read_text(encoding="utf-8")
    assert row["source_group"] == "YouTube Data"
    assert row["item"]["metadata"]["engagement"]["views"] == 42000
    assert "Fresh AI workflow" in html
    assert "qualified" in html
    assert "Items: 1 · 未调用 AI" in html


def test_source_shadow_export_rejects_unsafe_run_id(tmp_path) -> None:
    with pytest.raises(ValueError, match="unsafe filename"):
        export_source_shadow_items([], tmp_path, run_id="../escape")

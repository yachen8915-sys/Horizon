import json
from pathlib import Path

from scripts.build_editorial_shadow_review import build_review


def _write_diagnostics(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-08-22T09:00:00+08:00",
                "selected_items": rows,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_review_summarizes_concentration_evidence_and_sensitive_topics(
    tmp_path: Path,
) -> None:
    _write_diagnostics(
        tmp_path / "2026-08-22" / "selection.json",
        [
            {
                "id": "one",
                "title": "Official AI release",
                "url": "https://example.com/one",
                "source_type": "rss",
                "sub_source": "Vendor",
                "category": "official-ai-product",
                "profile": "pangmen-topic-radar",
                "event_key": "event-one",
            },
            {
                "id": "two",
                "title": "军事热点",
                "url": "https://example.com/two",
                "source_type": "platform_trends",
                "sub_source": "Hot API",
                "category": "platform-trend",
                "profile": "pangmen-platform-trend-radar",
                "event_key": "event-two",
            },
        ],
    )

    report = build_review(tmp_path)

    day = report["days"][0]
    assert day["selected_count"] == 2
    assert day["secondary_or_aggregator_count"] == 1
    assert day["sensitive_topic_count"] == 1
    assert report["human_review_required"] is True

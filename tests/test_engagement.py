from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.models import CollectionConfig, ContentItem, SourceType
from src.processing.engagement import EngagementTracker


def make_item(views: int, likes: int = 10) -> ContentItem:
    return ContentItem(
        id="bilibili:video:BV1trend",
        source_type=SourceType.BILIBILI,
        title="A useful AI demo",
        url="https://www.bilibili.com/video/BV1trend",
        published_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        metadata={"engagement": {"views": views, "likes": likes}},
    )


def test_engagement_tracker_records_first_and_24_hour_growth_without_ai(tmp_path) -> None:
    state_path = tmp_path / "engagement_snapshots.json"
    tracker = EngagementTracker(
        state_path,
        refresh_after_hours=24,
        thresholds={"views": {"absolute": 1000, "relative": 0.5}},
    )
    first_seen = datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc)

    assert tracker.observe([make_item(1000)], now=first_seen) == []
    assert tracker.observe(
        [make_item(1200)], now=first_seen + timedelta(hours=12)
    ) == []

    rising = make_item(2600, likes=40)
    assert tracker.observe(
        [rising], now=first_seen + timedelta(hours=24)
    ) == [rising]
    assert rising.metadata["engagement_growth"]["views"] == {
        "initial": 1000,
        "latest": 2600,
        "delta": 1600,
        "relative": 1.6,
    }
    assert rising.metadata["engagement_growth"]["triggered"] is True


def test_engagement_tracker_persists_one_refresh_only(tmp_path) -> None:
    state_path = tmp_path / "engagement_snapshots.json"
    first_seen = datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc)
    EngagementTracker(state_path, refresh_after_hours=24).observe(
        [make_item(1000)], now=first_seen
    )

    reloaded = EngagementTracker(state_path, refresh_after_hours=24)
    assert reloaded.observe(
        [make_item(1500)], now=first_seen + timedelta(hours=24)
    ) == []
    assert reloaded.observe(
        [make_item(9000)], now=first_seen + timedelta(hours=48)
    ) == []

    state = reloaded.load_state()
    record = state["items"]["bilibili:video:BV1trend"]
    assert record["latest_metrics"]["views"] == 1500
    assert record["refreshed_at"] == "2026-08-03T00:00:00+00:00"


def test_collection_config_accepts_lightweight_24_hour_tracking() -> None:
    config = CollectionConfig.model_validate(
        {
            "time_window_hours": 24,
            "engagement_tracking": {
                "enabled": True,
                "refresh_after_hours": 24,
                "lookback_hours": 48,
                "thresholds": {
                    "views": {"absolute": 5000, "relative": 0.5}
                },
            },
        }
    )

    assert config.engagement_tracking.enabled is True
    assert config.engagement_tracking.refresh_after_hours == 24
    assert config.engagement_tracking.lookback_hours == 48

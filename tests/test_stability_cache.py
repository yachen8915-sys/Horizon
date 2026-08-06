from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from src.models import ContentItem, SourceType
from src.orchestrator import HorizonOrchestrator


def test_item_cache_round_trip_preserves_structured_content(tmp_path: Path):
    item = ContentItem(
        id="rss:test:cache",
        source_type=SourceType.RSS,
        title="缓存条目",
        url="https://example.com/cache",
        published_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
        profile="pangmen-topic-radar",
        metadata={"category": "test"},
    )
    path = tmp_path / "cache" / "merged-20260806T000000Z.json"
    HorizonOrchestrator._save_items_cache(path, [item])
    loaded = HorizonOrchestrator._load_items_cache(path)
    assert loaded[0].id == item.id
    assert loaded[0].metadata["category"] == "test"

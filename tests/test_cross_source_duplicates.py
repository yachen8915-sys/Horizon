import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from src.models import (
    AIConfig,
    ClassificationResult,
    ContentAnalysis,
    ContentItem,
    ProcessingResult,
    SourceType,
)
from src.orchestrator import HorizonOrchestrator


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def item(
    item_id: str,
    url: str,
    *,
    source_type: SourceType = SourceType.RSS,
    content: str | None = None,
    metadata: dict | None = None,
    profile: str | list[str] | None = None,
) -> ContentItem:
    return ContentItem(
        id=item_id,
        source_type=source_type,
        title=item_id,
        url=url,
        content=content,
        published_at=NOW,
        metadata=metadata or {},
        profile=profile,
    )


def merge(items: list[ContentItem]) -> list[ContentItem]:
    orchestrator = object.__new__(HorizonOrchestrator)
    return orchestrator.merge_cross_source_duplicates(items)


def test_normalizes_host_case_default_ports_path_trailing_slash_and_fragment() -> None:
    items = [
        item("one", "https://EXAMPLE.com:443/story/#first"),
        item("two", "https://example.com/story#second", source_type=SourceType.REDDIT),
    ]

    result = merge(items)

    assert len(result) == 1
    assert result[0].metadata["merged_sources"] == ["rss", "reddit"]


def test_preserves_scheme_and_non_default_port_distinctions() -> None:
    items = [
        item("https", "https://example.com/story"),
        item("http", "http://example.com/story"),
        item("custom-port", "https://example.com:8443/story"),
    ]

    result = merge(items)

    assert [merged.id for merged in result] == ["https", "http", "custom-port"]


def test_preserves_meaningful_query_parameters_and_their_order() -> None:
    items = [
        item("page-one", "https://example.com/story?page=1&sort=new"),
        item("page-two", "https://example.com/story?page=2&sort=new"),
        item("reordered", "https://example.com/story?sort=new&page=1"),
    ]

    result = merge(items)

    assert [merged.id for merged in result] == ["page-one", "page-two", "reordered"]


def test_removes_only_common_tracking_parameters() -> None:
    items = [
        item(
            "tracked",
            "https://example.com/story?id=42&utm_source=newsletter&fbclid=abc&GCLID=xyz",
        ),
        item("clean", "https://example.com/story?id=42", source_type=SourceType.REDDIT),
        item("meaningful", "https://example.com/story?id=42&source=newsletter"),
    ]

    result = merge(items)

    assert len(result) == 2
    assert result[0].metadata["merged_sources"] == ["rss", "reddit"]
    assert result[1].id == "meaningful"


def test_merge_preserves_richest_content_and_combines_metadata() -> None:
    items = [
        item("short", "https://example.com/story", content="short", metadata={"score": 12}),
        item(
            "rich",
            "https://example.com/story/",
            source_type=SourceType.REDDIT,
            content="the richer primary content",
            metadata={"comments": 4},
        ),
    ]

    result = merge(items)

    assert len(result) == 1
    assert result[0].id == "rich"
    assert result[0].content == "the richer primary content\n\n--- From rss ---\nshort"
    assert result[0].metadata == {
        "comments": 4,
        "score": 12,
        "merged_sources": ["rss", "reddit"],
    }


def test_returns_deep_copies_without_mutation_and_is_idempotent() -> None:
    singleton = item("single", "https://single.example/item", metadata={"nested": {"value": 1}})
    duplicate = item(
        "duplicate",
        "https://example.com/item",
        content="duplicate content",
        metadata={"nested": {"value": 2}},
    )
    primary = item(
        "primary",
        "https://example.com/item/",
        source_type=SourceType.REDDIT,
        content="primary content is longer",
        metadata={"discussion": {"count": 3}},
    )
    inputs = [singleton, duplicate, primary]
    before = [original.model_dump() for original in inputs]

    first = merge(inputs)
    first_before = [merged.model_dump() for merged in first]
    second = merge(first)

    assert [original.model_dump() for original in inputs] == before
    assert [merged.model_dump() for merged in first] == first_before
    assert second == first
    assert first[0] is not singleton
    assert first[0].metadata is not singleton.metadata
    assert first[0].metadata["nested"] is not singleton.metadata["nested"]
    assert first[1] is not primary
    assert first[1].metadata["nested"] is not duplicate.metadata["nested"]


def test_candidate_profile_routes_are_hashable_and_preserve_route_distinctions() -> None:
    items = [
        item(
            "candidate-one",
            "https://example.com/story",
            profile=["tech-news", "finance-news"],
        ),
        item(
            "candidate-two",
            "https://example.com/story/",
            source_type=SourceType.TELEGRAM,
            profile=["tech-news", "finance-news"],
        ),
        item(
            "different-order",
            "https://example.com/story",
            profile=["finance-news", "tech-news"],
        ),
    ]

    result = merge(items)

    assert len(result) == 2
    assert result[0].metadata["merged_sources"] == ["rss", "telegram"]
    assert result[1].id == "different-order"


def test_platform_change_same_event_from_official_and_search_urls_merges_semantically(
    monkeypatch,
) -> None:
    official = item(
        "official-page-diff",
        "https://open.douyin.com/rules/creator",
        source_type=SourceType.PLATFORM_CHANGES,
        profile="pangmen-platform-change-radar",
        metadata={"platform": "douyin", "discovery_mode": "page_diff"},
    )
    search = item(
        "search-republication",
        "https://news.example/douyin-creator-rule",
        source_type=SourceType.PLATFORM_CHANGES,
        profile="pangmen-platform-change-radar",
        metadata={"platform": "douyin", "discovery_mode": "search_rss"},
    )
    for candidate, score in ((official, 9), (search, 8)):
        candidate.processing = ProcessingResult(
            classification=ClassificationResult(
                profile="pangmen-platform-change-radar", method="source_override"
            ),
            analysis=ContentAnalysis(
                score=score,
                reason="抖音创作者门槛由100粉丝调整为500粉丝",
                summary="抖音调整同一项创作者发布门槛。",
                tags=["抖音", "创作者规则"],
            ),
        )

    class FakeAIClient:
        async def complete(self, **kwargs):  # type: ignore[no-untyped-def]
            assert "official-page-diff" in kwargs["user"]
            assert "search-republication" in kwargs["user"]
            return '{"duplicates": [[0, 1]]}'

    orchestrator = object.__new__(HorizonOrchestrator)
    orchestrator.config = SimpleNamespace(
        ai=AIConfig(provider="deepseek", model="deepseek-chat", api_key_env="TEST_KEY")
    )
    monkeypatch.setattr("src.orchestrator.create_ai_client", lambda config: FakeAIClient())

    result = asyncio.run(
        orchestrator.merge_topic_duplicates([official, search], log=False)
    )

    assert [candidate.id for candidate in result] == ["official-page-diff"]

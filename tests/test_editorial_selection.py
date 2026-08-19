import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.models import (
    ClassificationResult,
    ContentAnalysis,
    ContentItem,
    EditorialSelectionConfig,
    ProcessingResult,
    SourceType,
)
from src.processing.editorial_selection import EditorialSelector


TOPIC_PROFILE = "pangmen-topic-radar"
TECH_PROFILE = "pangmen-ai-tech-radar"


def make_editorial_item(
    item_id: str,
    *,
    score: float = 8.0,
    relevance: float = 8.0,
    novelty: float = 8.0,
    demonstrability: float = 8.0,
    entity: str,
    topic: str,
    use_case: str,
    content_format: str,
    novelty_level: str = "new_example",
    event_key: str | None = None,
    source: str = "source-a",
    profile: str = TOPIC_PROFILE,
    published_at: datetime | None = None,
    url: str | None = None,
) -> ContentItem:
    return ContentItem(
        id=item_id,
        source_type=SourceType.RSS,
        title=item_id,
        url=url or f"https://example.com/{item_id}",
        content=f"Body evidence for {item_id}",
        author=source,
        published_at=published_at or datetime(2026, 8, 19, tzinfo=timezone.utc),
        metadata={"feed_name": source, "category": "ai"},
        profile=profile,
        processing=ProcessingResult(
            classification=ClassificationResult(
                profile=profile,
                method="source_override",
            ),
            analysis=ContentAnalysis(
                score=score,
                reason="body-backed test",
                summary=f"Summary for {item_id}",
                primary_entity=entity,
                topic_cluster=topic,
                use_case=use_case,
                content_format=content_format,
                novelty_level=novelty_level,
                event_key=event_key or f"event:{item_id}",
                editorial_key=f"{entity}|{use_case}|{content_format}",
                relevance_score=relevance,
                novelty_score=novelty,
                demonstrability_score=demonstrability,
            ),
        ),
    )


def make_selector(tmp_path, **overrides) -> EditorialSelector:
    values = {
        "enabled": True,
        "state_file": str(tmp_path / "digest-selection-state.json"),
        "history_days": 7,
        "editorial_cooldown_days": 3,
        "primary_entity_limit": 2,
        "topic_cluster_limit": 2,
        "use_case_limit": 2,
        "tutorial_workflow_limit": 3,
        "sub_source_limit": 2,
        "max_history_entries": 100,
    }
    values.update(overrides)
    return EditorialSelector(EditorialSelectionConfig(**values))


def test_three_distinct_gemini_updates_keep_best_two_without_event_merging(tmp_path):
    selector = make_selector(tmp_path)
    items = [
        make_editorial_item(
            "gemini-bts",
            entity="gemini",
            topic="consumer_ai_feature",
            use_case="fan_interaction",
            content_format="feature_update",
            event_key="gemini:bts_interactions:launch",
            relevance=7,
            novelty=7,
            demonstrability=7,
        ),
        make_editorial_item(
            "gemini-sat",
            entity="gemini",
            topic="ai_education",
            use_case="sat_practice",
            content_format="feature_update",
            event_key="gemini:sat_practice_tests:launch",
            relevance=9,
            novelty=8,
            demonstrability=9,
        ),
        make_editorial_item(
            "gemini-chrome",
            entity="gemini",
            topic="browser_productivity",
            use_case="web_assistance",
            content_format="feature_update",
            event_key="gemini:chrome_android:public_access",
            relevance=9,
            novelty=9,
            demonstrability=8,
        ),
    ]

    result = selector.select(items, now=datetime(2026, 8, 19, tzinfo=timezone.utc))

    assert [item.id for item in result.items] == ["gemini-chrome", "gemini-sat"]
    assert result.exclusions["gemini-bts"].reason == "diversity_entity_limit"
    assert result.exclusions["gemini-bts"].limit_value == 2
    assert result.exclusions["gemini-bts"].replaced_by_id in {
        "gemini-chrome",
        "gemini-sat",
    }


def test_short_drama_tutorials_are_distinct_events_but_topic_and_use_case_are_limited(tmp_path):
    selector = make_selector(tmp_path)
    items = [
        make_editorial_item(
            f"short-drama-{index}",
            entity=f"workflow-{index}",
            topic="ai_short_drama",
            use_case="short_drama_creation",
            content_format="tutorial_workflow",
            event_key=f"tutorial:short_drama:{index}",
            relevance=9 - index / 10,
            novelty=9 - index / 10,
            demonstrability=9 - index / 10,
            source=f"creator-{index}",
        )
        for index in range(4)
    ]

    result = selector.select(items, now=datetime(2026, 8, 19, tzinfo=timezone.utc))

    assert [item.id for item in result.items] == ["short-drama-0", "short-drama-1"]
    assert {entry.reason for entry in result.exclusions.values()} == {
        "diversity_topic_limit"
    }
    assert len({item.processing.analysis.event_key for item in items}) == 4


def test_tutorial_workflow_has_an_independent_total_limit(tmp_path):
    selector = make_selector(
        tmp_path,
        primary_entity_limit=8,
        topic_cluster_limit=8,
        use_case_limit=8,
    )
    items = [
        make_editorial_item(
            f"tutorial-{index}",
            entity=f"tool-{index}",
            topic=f"topic-{index}",
            use_case=f"case-{index}",
            content_format="tutorial_workflow",
            relevance=9 - index / 10,
            novelty=9 - index / 10,
            demonstrability=9 - index / 10,
            source=f"creator-{index}",
        )
        for index in range(4)
    ]

    result = selector.select(items, now=datetime(2026, 8, 19, tzinfo=timezone.utc))

    assert len(result.items) == 3
    assert result.exclusions["tutorial-3"].reason == "diversity_format_limit"


def test_use_case_limit_is_reported_when_topics_and_entities_are_distinct(tmp_path):
    selector = make_selector(
        tmp_path,
        primary_entity_limit=8,
        topic_cluster_limit=8,
    )
    items = [
        make_editorial_item(
            f"use-case-{index}",
            entity=f"tool-{index}",
            topic=f"topic-{index}",
            use_case="presentation_creation",
            content_format="case_study",
            relevance=9 - index / 10,
            source=f"creator-{index}",
        )
        for index in range(3)
    ]

    result = selector.select(items)

    assert len(result.items) == 2
    assert result.exclusions["use-case-2"].reason == "diversity_use_case_limit"


def test_same_sub_source_is_limited_without_treating_source_type_as_the_source(tmp_path):
    selector = make_selector(
        tmp_path,
        primary_entity_limit=8,
        topic_cluster_limit=8,
        use_case_limit=8,
        tutorial_workflow_limit=8,
    )
    items = [
        make_editorial_item(
            f"google-{index}",
            entity=f"google-product-{index}",
            topic=f"topic-{index}",
            use_case=f"case-{index}",
            content_format="feature_update",
            source="Google AI and Product News",
        )
        for index in range(3)
    ]

    result = selector.select(items, now=datetime(2026, 8, 19, tzinfo=timezone.utc))

    assert len(result.items) == 2
    assert result.exclusions["google-2"].reason == "diversity_source_limit"


def test_cross_day_same_event_is_rejected_even_when_url_and_title_change(tmp_path):
    selector = make_selector(tmp_path)
    selected_at = datetime(2026, 8, 17, tzinfo=timezone.utc)
    previous = make_editorial_item(
        "old-story",
        entity="openai",
        topic="ai_education",
        use_case="teen_learning",
        content_format="product_release",
        event_key="openai:chatgpt_for_teens:launch",
        url="https://source-a.example/launch",
    )
    selector.record_selected([previous], now=selected_at)
    renamed = make_editorial_item(
        "renamed-story",
        entity="openai",
        topic="ai_education",
        use_case="teen_learning",
        content_format="opinion_news",
        event_key="openai:chatgpt_for_teens:launch",
        url="https://source-b.example/report",
    )

    result = selector.select(
        [renamed],
        now=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )

    exclusion = result.exclusions["renamed-story"]
    assert exclusion.reason == "cross_day_event_repeat"
    assert exclusion.replaced_by_id == "old-story"


def test_cross_day_same_url_is_rejected_for_seven_days(tmp_path):
    selector = make_selector(tmp_path)
    previous = make_editorial_item(
        "old-url",
        entity="tool-a",
        topic="topic-a",
        use_case="case-a",
        content_format="feature_update",
        url="https://example.com/story?utm_source=old",
    )
    selector.record_selected(
        [previous],
        now=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    repeated = make_editorial_item(
        "new-url-id",
        entity="tool-b",
        topic="topic-b",
        use_case="case-b",
        content_format="opinion_news",
        url="https://example.com/story?utm_source=new",
    )

    result = selector.select(
        [repeated],
        now=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )

    assert result.exclusions["new-url-id"].reason == "exact_url_repeat"


def test_material_update_bypasses_editorial_cooldown_but_not_daily_entity_limit(tmp_path):
    selector = make_selector(tmp_path)
    previous = make_editorial_item(
        "old-gemini-example",
        entity="gemini",
        topic="browser_productivity",
        use_case="web_assistance",
        content_format="feature_update",
        event_key="gemini:old_example",
    )
    selector.record_selected(
        [previous],
        now=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    daily_items = [
        make_editorial_item(
            f"gemini-{index}",
            entity="gemini",
            topic=f"topic-{index}",
            use_case="web_assistance" if index == 0 else f"case-{index}",
            content_format="feature_update",
            novelty_level="material_update" if index == 0 else "new_example",
            event_key=f"gemini:new_event:{index}",
            relevance=9 - index / 10,
        )
        for index in range(3)
    ]

    result = selector.select(
        daily_items,
        now=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )

    assert "gemini-0" in {item.id for item in result.items}
    assert len(result.items) == 2
    assert result.exclusions["gemini-2"].reason == "diversity_entity_limit"


def test_editorial_cooldown_rejects_non_material_repackage(tmp_path):
    selector = make_selector(tmp_path)
    previous = make_editorial_item(
        "old-tutorial",
        entity="doubao",
        topic="ai_short_drama",
        use_case="short_drama_creation",
        content_format="tutorial_workflow",
        event_key="tutorial:old",
    )
    selector.record_selected(
        [previous],
        now=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    repackaged = make_editorial_item(
        "new-tutorial",
        entity="doubao",
        topic="ai_short_drama",
        use_case="short_drama_creation",
        content_format="tutorial_workflow",
        novelty_level="evergreen_repackage",
        event_key="tutorial:new",
    )

    result = selector.select(
        [repackaged],
        now=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )

    assert result.exclusions["new-tutorial"].reason == (
        "cross_day_editorial_cooldown"
    )


def test_equal_scores_use_editorial_scores_time_and_id_not_input_order(tmp_path):
    selector = make_selector(tmp_path)
    base_time = datetime(2026, 8, 19, tzinfo=timezone.utc)
    values = [
        ("third", 8, 8, 8, base_time),
        ("first", 9, 9, 9, base_time - timedelta(hours=2)),
        ("second", 9, 9, 8, base_time + timedelta(hours=1)),
    ]
    items = [
        make_editorial_item(
            item_id,
            entity=f"entity-{item_id}",
            topic=f"topic-{item_id}",
            use_case=f"case-{item_id}",
            content_format="feature_update",
            relevance=relevance,
            novelty=novelty,
            demonstrability=demo,
            source=f"source-{item_id}",
            published_at=published_at,
        )
        for item_id, relevance, novelty, demo, published_at in values
    ]

    result = selector.select(
        list(reversed(items)),
        now=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )

    assert [item.id for item in result.items] == ["first", "second", "third"]


def test_non_target_profiles_pass_through_unchanged(tmp_path):
    selector = make_selector(tmp_path)
    platform_change = make_editorial_item(
        "platform-change",
        entity="douyin",
        topic="platform_rule",
        use_case="creator_compliance",
        content_format="feature_update",
        profile="pangmen-platform-change-radar",
    )
    platform_trend = make_editorial_item(
        "platform-trend",
        entity="weibo",
        topic="platform_trend",
        use_case="trend_watching",
        content_format="opinion_news",
        profile="pangmen-platform-trend-radar",
    )

    result = selector.select(
        [platform_change, platform_trend],
        now=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )

    assert result.items == [platform_change, platform_trend]
    assert result.exclusions == {}


def test_state_is_version_one_pruned_and_only_records_final_selected(tmp_path):
    selector = make_selector(tmp_path, max_history_entries=2)
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    stale_path = tmp_path / "digest-selection-state.json"
    stale_path.write_text(
        json.dumps(
            {
                "version": 1,
                "items": [
                    {
                        "item_id": "stale",
                        "url": "https://example.com/stale",
                        "event_key": "stale:event",
                        "editorial_key": "stale|case|feature_update",
                        "primary_entity": "stale",
                        "topic_cluster": "stale",
                        "use_case": "case",
                        "content_format": "feature_update",
                        "selected_at": (now - timedelta(days=8)).isoformat(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    items = [
        make_editorial_item(
            f"selected-{index}",
            entity=f"entity-{index}",
            topic=f"topic-{index}",
            use_case=f"case-{index}",
            content_format="feature_update",
        )
        for index in range(3)
    ]

    selector.record_selected(items[:2], now=now)

    state = json.loads(stale_path.read_text(encoding="utf-8"))
    assert state["version"] == 1
    assert [entry["item_id"] for entry in state["items"]] == [
        "selected-0",
        "selected-1",
    ]
    assert "selected-2" not in stale_path.read_text(encoding="utf-8")


def test_august_19_real_candidate_replay_meets_editorial_acceptance(tmp_path):
    fixture_path = (
        Path(__file__).parent / "fixtures" / "editorial_replay_2026-08-19.json"
    )
    replay_rows = json.loads(fixture_path.read_text(encoding="utf-8"))
    items = [
        make_editorial_item(
            row["id"],
            score=row["score"],
            relevance=row["relevance_score"],
            novelty=row["novelty_score"],
            demonstrability=row["demonstrability_score"],
            entity=row["primary_entity"],
            topic=row["topic_cluster"],
            use_case=row["use_case"],
            content_format=row["content_format"],
            novelty_level=row["novelty_level"],
            event_key=row["event_key"],
            source=row["sub_source"],
            url=row["url"],
        ).model_copy(
            update={"title": row["title"], "content": row["body_evidence"]}
        )
        for row in replay_rows
    ]

    result = make_selector(tmp_path).select(
        items,
        now=datetime(2026, 8, 19, 23, 59, tzinfo=timezone.utc),
    )
    # The editorial stage ranks and diversifies candidates; the existing
    # balanced digest applies the configured profile cap immediately after it.
    selected = result.items[:8]
    selected_analysis = [item.processing.analysis for item in selected]

    assert len(selected) == 8
    assert sum(a.primary_entity == "gemini" for a in selected_analysis) == 2
    assert sum(a.topic_cluster == "ai_short_drama" for a in selected_analysis) == 2
    assert sum(a.content_format == "tutorial_workflow" for a in selected_analysis) == 3
    assert len({a.topic_cluster for a in selected_analysis}) >= 4
    assert len(result.exclusions) == 3
    assert all(exclusion.reason for exclusion in result.exclusions.values())

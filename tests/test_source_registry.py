import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.processing.source_health import (
    CoreEntityRegistry,
    CoverageRequirement,
    SourceAuthority,
    SourceLifecycle,
    SourceRegistry,
    SourceRegistryEntry,
    SourceRole,
)
from src.models import (
    AIConfig,
    CollectionConfig,
    Config,
    IntelligenceRadarConfig,
    RadarRunMode,
    SourcesConfig,
)
from src.orchestrator import HorizonOrchestrator


LANES = (
    "product_capability",
    "hot_content",
    "technical_frontier",
    "platform_ai_change",
)


def _entry(
    source_id: str,
    lane: str,
    role: SourceRole,
    *,
    lifecycle: SourceLifecycle = SourceLifecycle.ACTIVE,
    fallbacks: tuple[str, ...] = (),
) -> SourceRegistryEntry:
    return SourceRegistryEntry(
        source_id=source_id,
        config_ref=f"sources.{source_id}",
        decision_lanes=[lane],
        role=role,
        authority=SourceAuthority.OFFICIAL
        if role is SourceRole.CONFIRM
        else SourceAuthority.PRIMARY,
        access_method="rss",
        fallback_source_ids=list(fallbacks),
        expected_cadence_hours=24,
        required_fields=["title", "url", "published_at"],
        watermark_strategy="published_at_with_overlap",
        fact_ceiling="confirmed" if role is SourceRole.CONFIRM else "reported",
        lifecycle=lifecycle,
    )


def test_registry_rejects_duplicate_source_ids() -> None:
    entry = _entry("official-one", LANES[0], SourceRole.CONFIRM)

    with pytest.raises(ValidationError, match="duplicate source_id"):
        SourceRegistry(
            version=1,
            requirements=[],
            sources=[entry, entry.model_copy()],
        )


def test_registry_rejects_unknown_fallback_source() -> None:
    entry = _entry(
        "official-one",
        LANES[0],
        SourceRole.CONFIRM,
        fallbacks=("missing-source",),
    )

    with pytest.raises(ValidationError, match="unknown fallback"):
        SourceRegistry(version=1, requirements=[], sources=[entry])


def test_coverage_report_distinguishes_development_and_production_gates() -> None:
    sources: list[SourceRegistryEntry] = []
    requirements: list[CoverageRequirement] = []
    for lane in LANES:
        sources.extend(
            [
                _entry(f"{lane}-official", lane, SourceRole.CONFIRM),
                _entry(
                    f"{lane}-discovery",
                    lane,
                    SourceRole.DISCOVER,
                    lifecycle=SourceLifecycle.SHADOW,
                ),
            ]
        )
        requirements.append(
            CoverageRequirement(
                decision_lane=lane,
                required_roles=[SourceRole.CONFIRM, SourceRole.DISCOVER],
                minimum_operational_sources=2,
            )
        )
    registry = SourceRegistry(version=1, requirements=requirements, sources=sources)

    development = registry.coverage_report(production=False)
    production = registry.coverage_report(production=True)

    assert development.ready is True
    assert development.gaps == []
    assert production.ready is False
    assert {gap.decision_lane for gap in production.gaps} == set(LANES)
    assert all("discover" in gap.missing_roles for gap in production.gaps)


def test_coverage_report_requires_named_critical_sources() -> None:
    registry = SourceRegistry(
        version=1,
        requirements=[
            CoverageRequirement(
                decision_lane="hot_content",
                required_roles=[SourceRole.DISCOVER],
                minimum_operational_sources=1,
                required_source_ids=["x-official", "youtube-data"],
            )
        ],
        sources=[
            _entry("generic-discovery", "hot_content", SourceRole.DISCOVER),
            _entry(
                "x-official",
                "hot_content",
                SourceRole.DISCOVER,
                lifecycle=SourceLifecycle.SHADOW,
            ),
            _entry(
                "youtube-data",
                "hot_content",
                SourceRole.DISCOVER,
                lifecycle=SourceLifecycle.COVERAGE_GAP,
            ),
        ],
    )

    development = registry.coverage_report(production=False)
    production = registry.coverage_report(production=True)

    assert development.ready is False
    assert development.gaps[0].missing_source_ids == ["youtube-data"]
    assert production.ready is False
    assert production.gaps[0].missing_source_ids == ["x-official", "youtube-data"]


def test_repository_registry_records_current_gaps_and_p0_shadow_sources() -> None:
    path = Path("data/source_registry.json")
    registry = SourceRegistry.model_validate(json.loads(path.read_text(encoding="utf-8")))
    by_id = {source.source_id: source for source in registry.sources}

    assert by_id["github-direct"].lifecycle is SourceLifecycle.SHADOW
    assert by_id["x-official"].lifecycle is SourceLifecycle.DISABLED
    assert by_id["x-official"].access_method == "official_api_or_local_opencli"
    assert by_id["youtube-rss"].lifecycle is SourceLifecycle.SHADOW
    assert by_id["youtube-data"].access_method == "official_api_or_local_ytdlp"
    assert by_id["reddit-community"].lifecycle is SourceLifecycle.SHADOW
    assert by_id["bluesky-public"].lifecycle is SourceLifecycle.SHADOW
    assert by_id["bluesky-public"].authority is SourceAuthority.PRIMARY
    assert by_id["bluesky-public"].role is SourceRole.DISCOVER
    assert by_id["xiaohongshu-trends"].lifecycle is SourceLifecycle.COVERAGE_GAP
    assert by_id["wechat-trends"].lifecycle is SourceLifecycle.COVERAGE_GAP
    assert by_id["aihot"].role is SourceRole.BACKFILL
    assert by_id["aihot"].authority is SourceAuthority.AGGREGATOR
    assert by_id["anthropic-official-news"].role is SourceRole.CONFIRM
    assert by_id["meta-ai-official-blog"].lifecycle is SourceLifecycle.SHADOW
    assert by_id["xai-official-news"].fact_ceiling == "confirmed"
    assert by_id["canva-product-news"].authority is SourceAuthority.OFFICIAL
    assert by_id["runway-product-news"].role is SourceRole.CONFIRM
    assert by_id["perplexity-changelog"].access_method == "page_diff"
    assert by_id["cursor-changelog"].expected_cadence_hours == 168

    report = registry.coverage_report(production=False)

    assert report.ready is True
    assert report.gaps == []

    requirements = {
        requirement.decision_lane: requirement
        for requirement in registry.requirements
    }
    assert requirements["hot_content"].required_source_ids == [
        "aihot",
        "youtube-rss",
        "reddit-community",
    ]
    assert "bluesky-public" not in requirements["hot_content"].required_source_ids
    assert requirements["technical_frontier"].required_source_ids == [
        "github-direct"
    ]

    production = registry.coverage_report(production=True)
    gaps = {gap.decision_lane: gap for gap in production.gaps}
    assert gaps["hot_content"].missing_source_ids == [
        "youtube-rss",
        "reddit-community",
    ]
    assert gaps["technical_frontier"].missing_source_ids == ["github-direct"]


def test_repository_registry_covers_every_configured_source_family() -> None:
    config = json.loads(Path("data/config.github.json").read_text(encoding="utf-8"))
    registry = SourceRegistry.model_validate(
        json.loads(Path("data/source_registry.json").read_text(encoding="utf-8"))
    )

    assert registry.missing_config_families(set(config["sources"])) == set()


def test_registry_loads_from_a_configured_file() -> None:
    registry = SourceRegistry.load(Path("data/source_registry.json"))

    assert registry.version == 1
    assert registry.coverage_report(production=False).ready is True


def test_core_entity_registry_has_confirm_and_discover_coverage_in_shadow() -> None:
    registry = SourceRegistry.load(Path("data/source_registry.json"))
    entities = CoreEntityRegistry.load(Path("data/core_entities.json"))

    report = registry.entity_coverage_report(entities, production=False)

    assert report.ready is True
    assert report.gaps == []
    assert {entity.entity_id for entity in entities.entities} >= {
        "openai",
        "anthropic",
        "meta_ai",
        "xai",
        "canva",
        "runway",
        "perplexity",
        "cursor",
    }


def test_orchestrator_loads_development_coverage_from_registry() -> None:
    config = Config(
        ai=AIConfig(
            provider="openai",
            model="test",
            api_key_env="TEST_API_KEY",
            languages=[],
        ),
        sources=SourcesConfig(),
        collection=CollectionConfig(
            source_registry_file="data/source_registry.json",
            core_entity_registry_file="data/core_entities.json",
        ),
    )

    orchestrator = HorizonOrchestrator(config, object())  # type: ignore[arg-type]

    assert orchestrator.source_registry is not None
    assert orchestrator.source_coverage is not None
    assert orchestrator.source_coverage.ready is True
    assert orchestrator.entity_coverage is not None
    assert orchestrator.entity_coverage.ready is True


def test_orchestrator_blocks_delivery_while_sources_are_still_shadow() -> None:
    config = Config(
        ai=AIConfig(
            provider="openai",
            model="test",
            api_key_env="TEST_API_KEY",
            languages=[],
        ),
        sources=SourcesConfig(),
        collection=CollectionConfig(
            source_registry_file="data/source_registry.json",
            core_entity_registry_file="data/core_entities.json",
        ),
        intelligence=IntelligenceRadarConfig(
            enabled=True,
            delivery_enabled=True,
            run_mode=RadarRunMode.MORNING,
        ),
    )

    with pytest.raises(ValueError, match="production source coverage gate failed"):
        HorizonOrchestrator(config, object())  # type: ignore[arg-type]

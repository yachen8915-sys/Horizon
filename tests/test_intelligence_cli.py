from src.main import apply_intelligence_run_mode
from src.models import Config, RadarRunMode


def _config() -> Config:
    return Config.model_validate(
        {
            "ai": {
                "provider": "openai",
                "model": "test",
                "api_key_env": "TEST_KEY",
            },
            "sources": {},
            "intelligence": {
                "enabled": True,
                "delivery_enabled": False,
                "run_mode": "shadow",
            },
        }
    )


def test_cli_can_choose_morning_or_afternoon_without_enabling_delivery() -> None:
    morning = apply_intelligence_run_mode(_config(), "morning")
    afternoon = apply_intelligence_run_mode(_config(), "afternoon")

    assert morning.intelligence.run_mode is RadarRunMode.MORNING
    assert afternoon.intelligence.run_mode is RadarRunMode.AFTERNOON
    assert morning.intelligence.delivery_enabled is False
    assert morning.delivery_allowed is False


def test_intelligence_shadow_includes_sources_under_source_validation() -> None:
    shadow = apply_intelligence_run_mode(_config(), "shadow")

    assert shadow.collection.source_shadow_enabled is True
    assert shadow.intelligence.delivery_enabled is False

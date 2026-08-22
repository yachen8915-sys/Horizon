from pathlib import Path

from scripts.preview_platform_trend_selection_offline import (
    build_fixture_items,
    build_offline_preview,
    evaluate_preview_items,
)


def test_offline_preview_uses_production_gate_and_hides_rejected_items(tmp_path: Path) -> None:
    items = build_fixture_items()

    result = evaluate_preview_items(items, main_limit=3, temp_dir=tmp_path)

    assert len(result.main) == 3
    assert len(result.overflow) == 1
    assert all(item.metadata["trend_eligibility_reason"].endswith("pass") for item in [*result.main, *result.overflow])
    assert {row["reason"] for row in result.rejected} == {
        "evidence_insufficient",
        "below_heat_threshold",
        "pure_ai_topic_routed_to_ai_section",
    }
    assert not (tmp_path / "platform-trend-state.json").exists()


def test_offline_preview_renders_only_eligible_items_in_frontend_sections(tmp_path: Path) -> None:
    output = tmp_path / "preview.html"

    result = build_offline_preview(output, main_limit=3, temp_dir=tmp_path)
    html = output.read_text(encoding="utf-8")

    assert "查看更多资讯（1 条）" in html
    assert '<details class="more">' in html
    assert '<details class="more" open>' not in html
    assert "某明星恋情爆料" not in html
    assert "DeepSeek 新模型发布" not in html
    assert "火箭翻新商业航天" not in html
    assert "本地诊断" not in html
    assert len(result.rejected) == 3

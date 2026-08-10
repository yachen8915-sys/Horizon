import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

import src.processing.profiles as profile_module
from src.models import ProcessingConfig, ProfileSettingsConfig
from src.processing import ProfileRegistry


def test_loads_builtin_profiles():
    registry = ProfileRegistry.load(
        Path(__file__).resolve().parents[1] / "profiles", "tech-news"
    )

    for profile_id in (
        "tech-news",
        "tech-blog",
        "finance-news",
        "pangmen-topic-radar",
        "pangmen-ai-tech-radar",
        "pangmen-platform-trend-radar",
    ):
        profile = registry.get(profile_id)
        assert profile.match_prompt
        assert profile.analysis_prompt
        assert profile.enrichment_prompt

    tech_impact = next(
        block
        for block in registry.get("tech-news").definition.enrichment.blocks
        if block.id == "impact"
    )
    finance_impact = next(
        block
        for block in registry.get("finance-news").definition.enrichment.blocks
        if block.id == "impact"
    )
    assert tech_impact.optional is True
    assert finance_impact.optional is True


@pytest.mark.parametrize(
    ("route", "message"),
    [
        ([], "cannot be empty"),
        (["tech-news", ""], "non-empty strings"),
        (["tech-news", "tech-news"], "must be unique"),
        (["tech-news", "auto"], "cannot contain 'auto'"),
        (["tech-news", "missing"], "Unknown processing profile"),
    ],
)
def test_rejects_invalid_profile_candidate_lists(route, message):
    registry = ProfileRegistry.load(
        Path(__file__).resolve().parents[1] / "profiles", "tech-news"
    )

    with pytest.raises(ValueError, match=message):
        registry.validate_source_references({"profile": route})


def test_default_profiles_fall_back_to_packaged_resources(tmp_path, monkeypatch):
    packaged_profiles = tmp_path / "packaged-profiles"
    source_profiles = Path(__file__).resolve().parents[1] / "profiles"
    shutil.copytree(source_profiles, packaged_profiles)
    working_dir = tmp_path / "working"
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)
    monkeypatch.setattr(profile_module, "BUILTIN_PROFILES_DIR", packaged_profiles)

    registry = ProfileRegistry.load(Path("profiles"), "tech-news")

    assert registry.get("tech-news").analysis_prompt


def test_rejects_runtime_filter_settings_in_profile(tmp_path):
    profile_dir = tmp_path / "invalid"
    profile_dir.mkdir()
    for name in ("match.md", "analysis.md", "enrichment.md"):
        (profile_dir / name).write_text("prompt", encoding="utf-8")
    (profile_dir / "profile.json").write_text(
        json.dumps(
            {
                "id": "invalid",
                "name": "Invalid",
                "match": "match.md",
                "analysis": "analysis.md",
                "filter": {"enabled": True, "threshold": 7.0},
                "enrichment": {
                    "prompt": "enrichment.md",
                    "blocks": [{"id": "body", "type": "section", "tools": []}],
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        ProfileRegistry.load(tmp_path, "invalid")


@pytest.mark.parametrize("threshold", [-0.1, 10.1])
def test_rejects_profile_threshold_outside_score_range(threshold):
    with pytest.raises(ValidationError):
        ProfileSettingsConfig(threshold=threshold)


def test_profile_settings_default_to_no_filter_and_topic_dedup():
    settings = ProcessingConfig().profile_settings.get("tech-news")

    assert settings is None
    defaults = ProfileSettingsConfig()
    assert defaults.threshold is None
    assert defaults.topic_dedup is True


def test_pangmen_profile_accepts_user_facing_access_changes_but_not_parameter_only_news():
    profile = ProfileRegistry.load(
        Path(__file__).resolve().parents[1] / "profiles", "tech-news"
    ).get("pangmen-topic-radar")

    assert "免费开放" in profile.analysis_prompt
    assert "默认模型升级" in profile.analysis_prompt
    assert "纯参数" in profile.analysis_prompt


def test_pangmen_profile_does_not_request_demo_or_case_block():
    profile = ProfileRegistry.load(
        Path(__file__).resolve().parents[1] / "profiles", "tech-news"
    ).get("pangmen-topic-radar")

    assert "demo_or_case" not in {
        block.id for block in profile.definition.enrichment.blocks
    }
    assert "演示或案例建议" not in profile.enrichment_prompt


def test_ai_tech_profile_rewards_capability_shifts_not_parameter_noise():
    profile = ProfileRegistry.load(
        Path(__file__).resolve().parents[1] / "profiles", "tech-news"
    ).get("pangmen-ai-tech-radar")

    assert "能力变化" in profile.analysis_prompt
    assert "产品传导" in profile.analysis_prompt
    assert "纯参数" in profile.analysis_prompt
    assert "立即可用" not in profile.analysis_prompt
    assert {block.id for block in profile.definition.enrichment.blocks} == {
        "technical_change",
        "why_important",
        "capability_shift",
        "product_impact",
        "content_opportunity",
        "priority",
        "verification",
    }


def test_platform_trend_profile_uses_heat_evidence_and_risk_not_keyword_filters():
    profile = ProfileRegistry.load(
        Path(__file__).resolve().parents[1] / "profiles", "tech-news"
    ).get("pangmen-platform-trend-radar")

    assert "关键词硬过滤" in profile.analysis_prompt
    assert "热度证据" in profile.analysis_prompt
    assert "全网爆火" in profile.analysis_prompt
    assert "时效" in profile.analysis_prompt
    assert "政治敏感" in profile.match_prompt
    assert "娱乐" in profile.match_prompt
    assert "无自然连接" in profile.analysis_prompt
    assert "借势自然度" in profile.analysis_prompt
    assert "最高 4 分" in profile.analysis_prompt
    assert "投资建议" in profile.analysis_prompt
    assert "热点独有资产" in profile.analysis_prompt
    assert "热点爆点" in profile.analysis_prompt
    assert "如果这个热点今天没有上热榜" in profile.analysis_prompt
    assert "同质化惩罚" in profile.analysis_prompt
    assert "热点替换测试" in profile.analysis_prompt
    assert "热点资产测试" in profile.analysis_prompt
    assert "内容结果测试" in profile.analysis_prompt
    assert "热点增益测试" in profile.analysis_prompt
    assert "热点独有资产" in profile.enrichment_prompt
    assert "热点爆点" in profile.enrichment_prompt
    assert "4-6 个候选" in profile.enrichment_prompt
    assert "同质化惩罚" in profile.enrichment_prompt
    assert "观点 / 反常识" in profile.enrichment_prompt
    assert "主推角度排序" in profile.enrichment_prompt
    assert "AI + PPT" in profile.enrichment_prompt
    assert "不要先选类别" in profile.enrichment_prompt
    assert "固定类型槽位" in profile.enrichment_prompt
    assert "不显示热点爆点、热点资产、内部评分、行动建议、风险" in profile.enrichment_prompt
    assert "优先追 / 追 / 观察 / 忽略" not in profile.enrichment_prompt
    blocks = {
        block.id: block for block in profile.definition.enrichment.blocks
    }
    assert set(blocks) == {
        "what_happened",
        "primary_angle",
        "backup_angle",
        "source_evidence",
    }
    assert blocks["primary_angle"].optional is False
    assert blocks["backup_angle"].optional is True


def test_platform_trend_profile_keeps_three_regression_anchors():
    profile = ProfileRegistry.load(
        Path(__file__).resolve().parents[1] / "profiles", "tech-news"
    ).get("pangmen-platform-trend-radar")

    combined = profile.analysis_prompt + profile.enrichment_prompt
    assert "宇树科技 IPO" in combined
    assert "物流总额年均增长 5.7%" in combined
    assert "5-6 分" in combined
    assert "车企回归实体按键" in combined
    assert "AI 工具会不会也走同一条路" in combined
    assert "把资料交给两个 AI" in combined


def test_platform_trend_profile_rejects_beauty_topics_forced_into_ai_visuals():
    profile = ProfileRegistry.load(
        Path(__file__).resolve().parents[1] / "profiles", "tech-news"
    ).get("pangmen-platform-trend-radar")

    combined = profile.analysis_prompt + profile.enrichment_prompt
    assert "小眼睛避雷这个睫毛特效" in combined
    assert "添加 AI / PPT 标签不能制造账号相关性" in combined
    assert "美妆、妆容、穿搭或滤镜玩法" in combined
    assert "最高 4 分" in combined


def test_rejects_prompt_path_outside_profile_directory(tmp_path):
    profile_dir = tmp_path / "invalid"
    profile_dir.mkdir()
    (tmp_path / "outside.md").write_text("prompt", encoding="utf-8")
    (profile_dir / "analysis.md").write_text("prompt", encoding="utf-8")
    (profile_dir / "enrichment.md").write_text("prompt", encoding="utf-8")
    (profile_dir / "profile.json").write_text(
        json.dumps(
            {
                "id": "invalid",
                "name": "Invalid",
                "match": "../outside.md",
                "analysis": "analysis.md",
                "enrichment": {
                    "prompt": "enrichment.md",
                    "blocks": [{"id": "body", "type": "section", "tools": []}],
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="escapes"):
        ProfileRegistry.load(tmp_path, "invalid")

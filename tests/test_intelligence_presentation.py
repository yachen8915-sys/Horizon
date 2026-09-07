from dataclasses import fields

import pytest

from src.models import CandidateStatus, DecisionLane, ReasonCode
from tests.test_intelligence_brief import _candidate


def presentation_fixture():
    product = _candidate("GPT-6", DecisionLane.PRODUCT_CAPABILITY)
    product.item.metadata.update(ai_media_candidate=True, provider="AI HOT")
    industry = _candidate("industry", DecisionLane.AI_INDUSTRY_SOCIETY)
    industry.item.profile = "pangmen-platform-trend-radar"
    industry.item.metadata["trend_pool"] = "watch"
    leverage = _candidate("leverage", DecisionLane.HOT_CONTENT)
    leverage.item.profile = "pangmen-platform-trend-radar"
    leverage.item.metadata["trend_pool"] = "leverage"
    watch = _candidate("watch", DecisionLane.HOT_CONTENT)
    watch.item.profile = "pangmen-platform-trend-radar"
    watch.item.metadata["trend_pool"] = "watch"
    selected = [product, _candidate("technical", DecisionLane.TECHNICAL_FRONTIER),
                industry, leverage, watch, _candidate("platform", DecisionLane.PLATFORM_AI_CHANGE)]
    more_ai = _candidate("more-ai", DecisionLane.AI_INDUSTRY_SOCIETY)
    more_ai.item.profile = "pangmen-platform-trend-radar"
    more_hot = _candidate("more-hot", DecisionLane.HOT_CONTENT)
    more_hot.item.profile = "pangmen-platform-trend-radar"
    more_hot.item.metadata["trend_pool"] = "watch"
    more = [more_ai, more_hot]
    for candidate in more:
        candidate.status = CandidateStatus.HELD
        candidate.reason_codes = [ReasonCode.HELD_BY_CAPACITY]
    return selected, more


def test_shared_presentation_maps_all_sections_once_with_lane_priority():
    from src.processing.intelligence_presentation import build_intelligence_presentation

    selected, more = presentation_fixture()
    presentation = build_intelligence_presentation(selected, more)
    actual = {field.name: [c.candidate_id for c in getattr(presentation, field.name)]
              for field in fields(presentation)}
    assert actual == {
        "ai_product": ["GPT-6"], "ai_technical": ["technical"],
        "ai_industry": ["industry"], "hot_leverage": ["leverage"],
        "hot_watch": ["watch"], "platform_changes": ["platform"],
        "more_ai": ["more-ai"], "more_hot": ["more-hot"],
    }
    assert len({value for values in actual.values() for value in values}) == 8


def test_presentation_rejects_duplicate_membership_across_tiers():
    from src.processing.intelligence_presentation import build_intelligence_presentation

    candidate = _candidate("duplicate", DecisionLane.PRODUCT_CAPABILITY)
    with pytest.raises(ValueError, match="Duplicate"):
        build_intelligence_presentation([candidate], [candidate])


def test_presentation_rejects_unmapped_candidates_without_source_guessing():
    from src.processing.intelligence_presentation import build_intelligence_presentation

    candidate = _candidate("unknown", DecisionLane.HOT_CONTENT)
    candidate.item.profile = None
    candidate.item.metadata.update(provider="DailyHotAPI", ai_media_candidate=True)
    with pytest.raises(ValueError, match="Unmapped"):
        build_intelligence_presentation([candidate], [])


def test_presentation_uses_platform_change_profile_as_fallback():
    from src.processing.intelligence_presentation import build_intelligence_presentation

    candidate = _candidate("change", DecisionLane.HOT_CONTENT)
    candidate.item.profile = "pangmen-platform-change-radar"
    assert build_intelligence_presentation([candidate], []).platform_changes == [candidate]


def test_presentation_rejects_missing_analysis_instead_of_silently_losing_content():
    from src.processing.intelligence_presentation import build_intelligence_presentation

    candidate = _candidate("missing-analysis", DecisionLane.HOT_CONTENT)
    candidate.intelligence = None
    with pytest.raises(ValueError, match="Unmapped"):
        build_intelligence_presentation([candidate], [])


def test_markdown_uses_exact_presentation_headings():
    from src.models import RadarRunMode
    from src.processing.intelligence_brief import render_intelligence_brief

    selected, more = presentation_fixture()
    brief = render_intelligence_brief(selected, more_candidates=more,
        date="2026-09-07", run_mode=RadarRunMode.MORNING, total_fetched=50)
    assert [line for line in brief.splitlines() if line.startswith("## ") or
            (line.startswith("### ") and not line.startswith("### ["))] == [
        "## 今日 AI 情报", "### AI 产品与应用", "### AI 技术与模型",
        "### AI 行业与社会", "## 今日运营热点", "### 今日可借势",
        "### 今日大盘观察", "## 平台变化雷达", "## 查看更多资讯", "## 查看更多热点",
    ]
    assert brief.count("[Title GPT-6]") == 1
    assert "AI 媒体" not in brief


def test_feishu_and_markdown_share_exact_category_membership(monkeypatch):
    from src.ai.summarizer import DailySummarizer
    from src.models import RadarRunMode, WebhookConfig
    from src.processing.intelligence_brief import render_intelligence_brief
    from src.services.webhook import WebhookNotifier

    monkeypatch.setenv("PRESENTATION_WEBHOOK_URL", "https://open.feishu.cn/open-apis/bot/v2/hook/test")
    selected, more = presentation_fixture()
    notifier = WebhookNotifier(WebhookConfig(enabled=True, platform="feishu",
        layout="collapsible", url_env="PRESENTATION_WEBHOOK_URL"))
    messages = notifier.build_daily_summary_messages(
        summary="unused", important_items=[c.item for c in selected],
        intelligence_candidates=selected, intelligence_more_candidates=more,
        all_items_count=50, date="2026-09-07", lang="zh", summarizer=DailySummarizer())
    card = messages[0]["_request_body_override"]["card"]
    assert card["schema"] == "2.0"
    elements = card["body"]["elements"]
    feishu_sections = {}
    heading = None
    for element in elements:
        if element["tag"] == "markdown" and element["content"].startswith("#"):
            heading = element["content"]
            feishu_sections[heading] = []
        elif element["tag"] == "collapsible_panel":
            content = str(element)
            feishu_sections[heading].extend(c.candidate_id for c in selected + more
                if str(c.item.url) in content)
    brief = render_intelligence_brief(selected, more_candidates=more,
        date="2026-09-07", run_mode=RadarRunMode.MORNING, total_fetched=50)
    markdown_sections = {}
    for line in brief.splitlines():
        if line.startswith("## ") or (line.startswith("### ") and not line.startswith("### [")):
            heading = line
            markdown_sections[heading] = []
        elif line.startswith("### [") or line.startswith("- ["):
            markdown_sections[heading].extend(c.candidate_id for c in selected + more
                if str(c.item.url) in line)
    assert feishu_sections == markdown_sections
    assert "AI 媒体" not in str(elements)
    panels = [e for e in elements if e["tag"] == "collapsible_panel"]
    assert all(panel["expanded"] is False for panel in panels)
    assert sum("Title GPT-6" in panel["header"]["title"]["content"] for panel in panels) == 1
    assert "AI HOT" in str(panels)


def test_feishu_more_only_candidates_keep_the_correct_section(monkeypatch):
    from src.ai.summarizer import DailySummarizer
    from src.models import WebhookConfig
    from src.services.webhook import WebhookNotifier

    monkeypatch.setenv("PRESENTATION_WEBHOOK_URL", "https://open.feishu.cn/open-apis/bot/v2/hook/test")
    _, more = presentation_fixture()
    notifier = WebhookNotifier(WebhookConfig(enabled=True, platform="feishu",
        layout="collapsible", url_env="PRESENTATION_WEBHOOK_URL"))
    message = notifier.build_daily_summary_messages(summary="", important_items=[],
        intelligence_candidates=[], intelligence_more_candidates=more, all_items_count=10,
        date="2026-09-07", lang="zh", summarizer=DailySummarizer())[0]
    elements = message["_request_body_override"]["card"]["body"]["elements"]
    assert [e["content"] for e in elements if e["tag"] == "markdown"][1:] == [
        "## 查看更多资讯", "## 查看更多热点"]

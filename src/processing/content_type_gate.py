"""Pure content admission policy; freshness and availability remain upstream."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from ..models import ContentItem, DecisionLane, EvidenceStatus, ReasonCode
from .intelligence_analysis import (
    IntelligenceDraft,
    assess_hard_gates,
    calculate_weighted_score,
)


PLATFORM_TREND_PROFILE_ID = "pangmen-platform-trend-radar"
CORE_TREND_PLATFORMS = {"weibo", "douyin", "xiaohongshu", "wechat"}
BRAND_SAFETY_TERMS = {
    "政治敏感", "自然灾害", "灾难", "台风", "地震", "洪水", "山火",
    "重大事故", "严重事故", "伤亡", "遇难", "逝世", "去世", "身亡", "离世",
    "死亡", "暴力",
}

# Require a combat action near a military actor/target, not a country name or
# an ordinary use of "打击" / "冲突" in operations content.
_MILITARY_TARGET = r"(?:航母|驱逐舰|军舰|军事基地|军队|部队|战机|导弹)"
_COMBAT_ACTION = r"(?:空袭|轰炸|炮击|袭击|攻击|打击|击沉|交火|开火)"
_REAL_VIOLENCE_SIGNAL = re.compile(
    rf"{_COMBAT_ACTION}.{{0,12}}{_MILITARY_TARGET}"
    rf"|{_MILITARY_TARGET}.{{0,12}}{_COMBAT_ACTION}"
    r"|武装冲突"
    r"|殴打|围殴|群殴|拳打脚踢|持刀伤人|持刀行凶|砍伤|捅伤|枪击"
)
# Only explicit fictional/simulation framing qualifies; mentioning a game or
# model alone must not exempt an assault on players or exhibition visitors.
_SIMULATED_CONTEXT = re.compile(r"(?:游戏|电影|小说|动画)(?:中|内|里)|动画演示|模型演示")
_REAL_WORLD_CONTEXT = re.compile(r"线下|现场|现实中|街头")


@dataclass(frozen=True)
class ContentTypeGateResult:
    accepted: bool
    reason: ReasonCode
    trend_pool: Literal["leverage", "watch"] | None = None
    pending_verification: bool = False


def is_brand_safety_excluded(item: ContentItem) -> bool:
    analysis = item.processing.analysis if item.processing else None
    signals = [item.title, *(analysis.tags if analysis else [])]
    normalized = " ".join(str(signal) for signal in signals).casefold()
    if any(term in normalized for term in BRAND_SAFETY_TERMS):
        return True
    for signal in signals:
        for clause in re.split(r"[,，。;；!?！？\n]", str(signal)):
            for match in _REAL_VIOLENCE_SIGNAL.finditer(clause):
                context = clause[:match.end()]
                simulated = _SIMULATED_CONTEXT.search(context)
                if simulated and not _REAL_WORLD_CONTEXT.search(
                    context[simulated.end():]
                ):
                    continue
                return True
    return False


def _distinct_values(item: ContentItem, plural: str, singular: str) -> set[str]:
    values = item.metadata.get(plural)
    if not isinstance(values, (list, tuple, set)):
        values = [item.metadata.get(singular)]
    return {
        str(value).strip().casefold()
        for value in values
        if value and str(value).strip()
    }


def assign_platform_trend_pool(
    item: ContentItem,
) -> Literal["leverage", "watch"] | None:
    if (
        not item.processing
        or item.processing.classification.profile != PLATFORM_TREND_PROFILE_ID
    ):
        return None
    analysis = item.processing.analysis
    if analysis is None or is_brand_safety_excluded(item):
        return None
    if (
        analysis.intelligence
        and analysis.intelligence.primary_lane is not DecisionLane.HOT_CONTENT
    ):
        return None
    operations = (
        analysis.operations_score
        if analysis.operations_score is not None
        else analysis.score
    )
    content = (
        analysis.content_opportunity_score
        if analysis.content_opportunity_score is not None
        else analysis.score
    )
    if operations is None or operations < 7:
        return None
    if content is not None and content >= 7:
        return "leverage"
    if operations >= 8:
        return "watch"
    rank = item.metadata.get("rank")
    platform = str(
        item.metadata.get("platform") or item.metadata.get("content_platform") or ""
    ).strip().casefold()
    core_top_ten = (
        platform in CORE_TREND_PLATFORMS
        and isinstance(rank, (int, float))
        and not isinstance(rank, bool)
        and 1 <= rank <= 10
    )
    if (
        core_top_ten
        or len(_distinct_values(item, "providers", "provider")) >= 2
        or len(_distinct_values(item, "platforms", "platform")) >= 2
        or analysis.operations_focus in {"ai_tech", "workplace_youth", "visual_content"}
    ):
        return "watch"
    return None


def assess_content_type_gate(
    item: ContentItem,
    draft: IntelligenceDraft,
    *,
    minimum_score: float,
) -> ContentTypeGateResult:
    if is_brand_safety_excluded(item):
        return ContentTypeGateResult(False, ReasonCode.BRAND_SAFETY)
    if draft.primary_lane is DecisionLane.AI_INDUSTRY_SOCIETY:
        if draft.evidence_status is EvidenceStatus.DISPUTED:
            return ContentTypeGateResult(False, ReasonCode.EVIDENCE_INSUFFICIENT)
        analysis = item.processing.analysis if item.processing else None
        relevance = (
            analysis.relevance_score
            if analysis and analysis.relevance_score is not None
            else draft.dimensions.audience_fit
        )
        if relevance < 6:
            return ContentTypeGateResult(False, ReasonCode.LOW_RELEVANCE)
        if calculate_weighted_score(draft.primary_lane, draft.dimensions) < 6:
            return ContentTypeGateResult(False, ReasonCode.LOW_QUALITY)
        return ContentTypeGateResult(
            True,
            ReasonCode.PASSED_HARD_GATES,
            pending_verification=draft.evidence_status is EvidenceStatus.UNVERIFIED,
        )
    if (
        draft.primary_lane is DecisionLane.HOT_CONTENT
        and item.processing
        and item.processing.classification.profile == PLATFORM_TREND_PROFILE_ID
    ):
        pool = assign_platform_trend_pool(item)
        if pool:
            return ContentTypeGateResult(
                True, ReasonCode.PASSED_HARD_GATES, trend_pool=pool
            )
        analysis = item.processing.analysis
        operations = None
        if analysis is not None:
            operations = (
                analysis.operations_score
                if analysis.operations_score is not None
                else analysis.score
            )
        return ContentTypeGateResult(
            False,
            ReasonCode.LOW_OPERATIONS_VALUE
            if operations is None or operations < 7
            else ReasonCode.INSUFFICIENT_HOTSPOT_SIGNAL,
        )
    gate = assess_hard_gates(draft, minimum_score=minimum_score)
    reasons = {
        "passed_hard_gates": ReasonCode.PASSED_HARD_GATES,
        "evidence_insufficient": ReasonCode.EVIDENCE_INSUFFICIENT,
        "evidence_disputed": ReasonCode.EVIDENCE_INSUFFICIENT,
        "low_propagation": ReasonCode.LOW_PROPAGATION,
        "low_relevance": ReasonCode.LOW_RELEVANCE,
        "no_direct_decision_impact": ReasonCode.LOW_RELEVANCE,
    }
    return ContentTypeGateResult(
        gate.accepted, reasons.get(gate.reason, ReasonCode.LOW_QUALITY)
    )

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
_MILITARY_TARGET = re.compile(r"航母|驱逐舰|军舰|军事基地|军队|部队|战机|导弹")
_COMBAT_ACTION = r"(?:空袭|轰炸|炮击|袭击|攻击|打击|击沉|交火|开火)"
_REAL_VIOLENCE_SIGNAL = re.compile(
    rf"(?P<combat>{_COMBAT_ACTION})"
    r"|武装冲突"
    r"|(?P<assault>殴打|围殴|群殴|拳打脚踢|持刀伤人|持刀行凶|砍伤|捅伤|枪击)"
)
# Only explicit fictional/simulation framing qualifies; mentioning a game or
# model alone must not exempt an assault on players or exhibition visitors.
_SIMULATED_CONTEXT = re.compile(r"(?:游戏|电影|小说|动画)(?:中|内|里)|动画演示|模型演示")
_VIRTUAL_CONTINUATION = re.compile(r"教程|操作|特效|制作|演示|角色|npc|画面|模型", re.I)
_NEW_REAL_EVENT = re.compile(r"约架|线下|现场|现实中|街头|宣布|通报|证实|称")
_QUANTITY = r"(?:[一二两三四五六七八九十几多数\d]{1,3}|若干)(?:个|名|群)"
_VIRTUAL_VICTIM_BEFORE = re.compile(
    r"(?:角色|npc|虚拟人物)(?:正在|突然|互相){0,2}"
    rf"(?:(?:被|遭到|受到|遭)(?:(?:{_QUANTITY})?"
    r"(?:敌人|对手|敌方角色|其他角色|npc)(?:所)?)?)?$", re.I
)
_VIRTUAL_VICTIM_AFTER = re.compile(
    rf"^(?:了|着)?(?:{_QUANTITY})?(?:游戏角色|角色|npc|虚拟人物)"
    r"(?!扮演者|演员|配音)", re.I
)


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
        # A new sentence or independent tag cannot inherit fictional framing.
        for sentence in re.split(r"[。.!?！？\n]+", str(signal)):
            previous_virtual = False
            for clause in re.split(r"[,，;；:：…]+", sentence):
                if not clause.strip():
                    continue
                frames = list(_SIMULATED_CONTEXT.finditer(clause))
                inherited = bool(
                    previous_virtual
                    and _VIRTUAL_CONTINUATION.search(clause)
                    and not _NEW_REAL_EVENT.search(clause)
                )
                attacks = list(_REAL_VIOLENCE_SIGNAL.finditer(clause))
                for index, match in enumerate(attacks):
                    # Actor/object evidence is local to this action and cannot
                    # cross another attack, even within the same clause.
                    previous_end = attacks[index - 1].end() if index else 0
                    next_start = (
                        attacks[index + 1].start()
                        if index + 1 < len(attacks) else len(clause)
                    )
                    before = clause[max(previous_end, match.start() - 12):match.start()]
                    after = clause[match.end():min(next_start, match.end() + 12)]
                    if match.group("combat") and not (
                        _MILITARY_TARGET.search(before) or _MILITARY_TARGET.search(after)
                    ):
                        continue
                    framing = next(
                        (frame for frame in reversed(frames) if frame.end() <= match.start()),
                        None,
                    )
                    virtual = inherited or framing is not None
                    if match.group("assault"):
                        # A game-related dispute is a cause, not a virtual victim.
                        # Require this attack to act on a character/NPC directly.
                        virtual = virtual and bool(
                            _VIRTUAL_VICTIM_BEFORE.search(before)
                            or _VIRTUAL_VICTIM_AFTER.search(after)
                        )
                    reality_start = framing.end() if framing else 0
                    if not virtual or _NEW_REAL_EVENT.search(
                        clause[reality_start:match.start()]
                    ):
                        return True
                # Only an adjacent clause explicitly continuing virtual content
                # may inherit; unrelated clauses end this scope.
                scope_start = frames[-1].end() if frames else 0
                previous_virtual = (bool(frames) or inherited) and not (
                    _NEW_REAL_EVENT.search(clause[scope_start:])
                )
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

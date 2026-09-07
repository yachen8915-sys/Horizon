"""Shared content-based section membership for Markdown and Feishu."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
import logging

from ..models import CandidateRecord, DecisionLane

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IntelligencePresentation:
    ai_product: list[CandidateRecord] = field(default_factory=list)
    ai_technical: list[CandidateRecord] = field(default_factory=list)
    ai_industry: list[CandidateRecord] = field(default_factory=list)
    hot_leverage: list[CandidateRecord] = field(default_factory=list)
    hot_watch: list[CandidateRecord] = field(default_factory=list)
    platform_changes: list[CandidateRecord] = field(default_factory=list)
    more_ai: list[CandidateRecord] = field(default_factory=list)
    more_hot: list[CandidateRecord] = field(default_factory=list)
    # Retain malformed candidates for diagnostics; never assign them a guessed section.
    unmapped: list[CandidateRecord] = field(default_factory=list)

    def sections(self) -> Iterator[tuple[str, list[CandidateRecord]]]:
        """Yield the same nonempty sections, in display order, to every renderer."""
        if self.ai_product or self.ai_technical or self.ai_industry:
            yield "## 今日 AI 情报", []
        for heading, candidates in (
            ("### AI 产品与应用", self.ai_product),
            ("### AI 技术与模型", self.ai_technical),
            ("### AI 行业与社会", self.ai_industry),
        ):
            if candidates:
                yield heading, candidates
        if self.hot_leverage or self.hot_watch:
            yield "## 今日运营热点", []
        for heading, candidates in (
            ("### 今日可借势", self.hot_leverage),
            ("### 今日大盘观察", self.hot_watch),
            ("## 平台变化雷达", self.platform_changes),
            ("## 查看更多资讯", self.more_ai),
            ("## 查看更多热点", self.more_hot),
        ):
            if candidates:
                yield heading, candidates


def build_intelligence_presentation(
    selected: list[CandidateRecord], more: list[CandidateRecord]
) -> IntelligencePresentation:
    presentation = IntelligencePresentation()
    seen: set[str] = set()
    ai_detail_count = 0
    hot_detail_count = 0
    ai_sections = {"ai_product", "ai_technical", "ai_industry"}
    hot_sections = {"hot_leverage", "hot_watch"}
    lane_sections = {
        DecisionLane.PRODUCT_CAPABILITY: "ai_product",
        DecisionLane.TECHNICAL_FRONTIER: "ai_technical",
        DecisionLane.AI_INDUSTRY_SOCIETY: "ai_industry",
        DecisionLane.PLATFORM_AI_CHANGE: "platform_changes",
    }
    for candidates, compact in ((selected, False), (more, True)):
        for candidate in candidates:
            if candidate.candidate_id in seen:
                raise ValueError(f"Duplicate presentation candidate: {candidate.candidate_id}")
            seen.add(candidate.candidate_id)
            if candidate.intelligence is None:
                logger.warning("Unmapped presentation candidate without analysis: %s", candidate.candidate_id)
                presentation.unmapped.append(candidate)
                continue
            lane = candidate.intelligence.primary_lane
            section = lane_sections.get(lane)
            if section is None:
                profile = (
                    candidate.item.processing.classification.profile
                    if candidate.item.processing
                    else candidate.item.profile
                )
                if profile == "pangmen-platform-change-radar":
                    section = "platform_changes"
                elif lane is DecisionLane.HOT_CONTENT:
                    pool = candidate.item.metadata.get("trend_pool", "leverage")
                    section = {
                        "leverage": "hot_leverage", "watch": "hot_watch"
                    }.get(pool) if isinstance(pool, str) else None
            if section is None:
                logger.warning("Unmapped presentation candidate: %s", candidate.candidate_id)
                presentation.unmapped.append(candidate)
                continue
            ai_overflow = not compact and section in ai_sections and ai_detail_count >= 16
            hot_overflow = not compact and section in hot_sections and hot_detail_count >= 15
            if compact or ai_overflow or hot_overflow:
                section = (
                    "more_hot"
                    if section in hot_sections
                    else "more_ai"
                )
            elif section in ai_sections:
                ai_detail_count += 1
            elif section in hot_sections:
                hot_detail_count += 1
            getattr(presentation, section).append(candidate)
    return presentation

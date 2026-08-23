from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timezone
import math
from typing import Any, Iterable

from ..models import ContentItem, DigestConfig, SourceType
from .editorial_selection import canonical_url, normalize_editorial_token, profile_id


AI_APPLICATION_PROFILE = "pangmen-topic-radar"
AI_TECHNOLOGY_PROFILE = "pangmen-ai-tech-radar"
PLATFORM_TREND_PROFILE = "pangmen-platform-trend-radar"
AI_PROFILES = {AI_APPLICATION_PROFILE, AI_TECHNOLOGY_PROFILE}


@dataclass(frozen=True)
class BreakoutExclusion:
    reason: str
    replaced_by_id: str | None = None
    limit_key: str | None = None
    limit_value: int | None = None


@dataclass
class BreakoutSelectionResult:
    items: list[ContentItem]
    exclusions: dict[str, BreakoutExclusion] = field(default_factory=dict)


class BreakoutSelector:
    """Annotate breakout evidence and enforce cross-section uniqueness."""

    def __init__(self, config: DigestConfig) -> None:
        self.config = config

    def annotate(self, items: list[ContentItem]) -> None:
        aihot_scores = self._percentile_map(
            item for item in items if item.source_type == SourceType.AIHOT
        )
        bilibili_scores = self._bilibili_percentile_map(
            item for item in items if item.source_type == SourceType.BILIBILI
        )
        for item in items:
            source_signal = self._source_signal_score(
                item,
                aihot_scores=aihot_scores,
                bilibili_scores=bilibili_scores,
            )
            item.metadata["source_signal_score"] = source_signal
            analysis = item.processing.analysis if item.processing else None
            if analysis is None:
                item.metadata.update(
                    {
                        "breakout_score": 0.0,
                        "breakout_status": "none",
                        "breakout_eligibility_mode": "rejected",
                        "verification_status": "insufficient",
                        "breakout_reason": "missing_analysis",
                    }
                )
                self._assign_display_section(item)
                continue

            surprise = self._number(analysis.surprise_score)
            audience = self._number(analysis.audience_breadth_score)
            actionability = self._number(analysis.actionability_score)
            evidence = self._number(analysis.evidence_quality_score)
            breakout_score = round(
                source_signal * 0.30
                + surprise * 0.25
                + audience * 0.20
                + actionability * 0.15
                + evidence * 0.10,
                2,
            )
            if breakout_score >= 8.0 and evidence >= 6.0:
                status = "breakout"
                mode = "standard_pass"
                verification = "verified"
                reason = "standard_pass"
            elif breakout_score >= 8.0 and source_signal >= 9.0:
                status = "hot_unverified"
                mode = "high_heat_relaxed_pass"
                verification = "unverified"
                reason = "high_heat_relaxed_pass"
                analysis.summary = (
                    f"“{item.title}”正在引发热议；"
                    "现有来源不足以确认标题所述具体事实。"
                )
            else:
                status = "none"
                mode = "rejected"
                verification = "verified" if evidence >= 6.0 else "insufficient"
                reason = "below_breakout_threshold"
            item.metadata.update(
                {
                    "breakout_score": breakout_score,
                    "breakout_status": status,
                    "breakout_eligibility_mode": mode,
                    "verification_status": verification,
                    "breakout_reason": reason,
                    "controversial_topic": bool(analysis.controversial_topic),
                }
            )
            self._assign_display_section(item)

    def select(
        self,
        items: list[ContentItem],
        *,
        annotate: bool = True,
    ) -> BreakoutSelectionResult:
        if annotate:
            self.annotate(items)
        exclusions: dict[str, BreakoutExclusion] = {}
        unique_items: list[ContentItem] = []
        for item in items:
            item_tokens = self._identity_tokens(item)
            matches = [
                candidate
                for candidate in unique_items
                if item_tokens.intersection(self._identity_tokens(candidate))
            ]
            if not matches:
                unique_items.append(item)
                continue
            group = [item, *matches]
            kept = max(group, key=self._event_winner_key)
            match_tokens = item_tokens.intersection(
                token
                for candidate in matches
                for token in self._identity_tokens(candidate)
            )
            limit_key = sorted(match_tokens)[0] if match_tokens else "identity"
            for dropped in group:
                if dropped is kept:
                    continue
                exclusions[dropped.id] = BreakoutExclusion(
                    reason="cross_section_duplicate",
                    replaced_by_id=kept.id,
                    limit_key=limit_key,
                    limit_value=1,
                )
                dropped.metadata["editorial_selection"] = {
                    "reason": "cross_section_duplicate",
                    "replaced_by_id": kept.id,
                    "limit_key": limit_key,
                    "limit_value": 1,
                }
            unique_items = [candidate for candidate in unique_items if candidate not in matches]
            unique_items.append(kept)

        unique_items.sort(key=self._sort_key, reverse=True)
        selected: list[ContentItem] = []
        controversial_count = 0
        limit = self.config.controversial_topic_limit
        for item in unique_items:
            is_relaxed_platform_topic = (
                profile_id(item) == PLATFORM_TREND_PROFILE
                and item.metadata.get("breakout_status") == "hot_unverified"
            )
            if is_relaxed_platform_topic:
                if controversial_count >= limit:
                    exclusions[item.id] = BreakoutExclusion(
                        reason="controversial_topic_limit",
                        limit_key="controversial_topic",
                        limit_value=limit,
                    )
                    item.metadata["editorial_selection"] = {
                        "reason": "controversial_topic_limit",
                        "limit_key": "controversial_topic",
                        "limit_value": limit,
                    }
                    continue
                controversial_count += 1
            selected.append(item)
        return BreakoutSelectionResult(items=selected, exclusions=exclusions)

    @staticmethod
    def _number(value: Any) -> float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return min(max(float(value), 0.0), 10.0)
        return 0.0

    @classmethod
    def _percentile_map(cls, items: Iterable[ContentItem]) -> dict[int, float]:
        rows: list[tuple[ContentItem, float]] = []
        for item in items:
            raw_score = item.metadata.get("aihot_score")
            score = (
                max(float(raw_score), 0.0)
                if isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool)
                else 0.0
            )
            rows.append((item, score))
        positive = sorted(score for _, score in rows if score > 0.0)
        result: dict[int, float] = {}
        for item, score in rows:
            if score <= 0.0 or not positive:
                result[id(item)] = 0.0
                continue
            if len(positive) == 1:
                percentile = 1.0
            else:
                percentile = sum(value < score for value in positive) / (
                    len(positive) - 1
                )
            percentile_signal = 5.0 + percentile * 5.0
            absolute_signal = min(score / 10.0, 10.0)
            result[id(item)] = round(
                absolute_signal * 0.6 + percentile_signal * 0.4,
                2,
            )
        return result

    @classmethod
    def _bilibili_percentile_map(
        cls, items: Iterable[ContentItem]
    ) -> dict[int, float]:
        rows: list[tuple[ContentItem, float, float]] = []
        for item in items:
            engagement = item.metadata.get("engagement")
            views = engagement.get("views") if isinstance(engagement, dict) else None
            views_value = float(views) if isinstance(views, (int, float)) else 0.0
            explicit_age = item.metadata.get("content_age_hours")
            if isinstance(explicit_age, (int, float)) and not isinstance(
                explicit_age, bool
            ):
                age_hours = max(float(explicit_age), 1.0)
            else:
                fetched_at = item.fetched_at.astimezone(timezone.utc)
                published_at = item.published_at.astimezone(timezone.utc)
                age_hours = max(
                    (fetched_at - published_at).total_seconds() / 3600.0,
                    1.0,
                )
            velocity = views_value / math.sqrt(age_hours)
            rows.append((item, views_value, velocity))
        positive = sorted(velocity for _, _, velocity in rows if velocity > 0.0)
        result: dict[int, float] = {}
        for item, views, velocity in rows:
            if views <= 0.0:
                result[id(item)] = 0.0
                continue
            absolute = min(math.log10(views + 1.0) * 2.5, 10.0)
            percentile = (
                1.0
                if len(positive) == 1
                else sum(value < velocity for value in positive)
                / (len(positive) - 1)
            )
            result[id(item)] = round(absolute * 0.7 + (5.0 + percentile * 5.0) * 0.3, 2)
        return result

    def _source_signal_score(
        self,
        item: ContentItem,
        *,
        aihot_scores: dict[int, float],
        bilibili_scores: dict[int, float],
    ) -> float:
        analysis = item.processing.analysis if item.processing else None
        if item.source_type == SourceType.PLATFORM_TRENDS:
            base = self._number(item.metadata.get("heat_score"))
        elif self._is_official(item) and analysis is not None and analysis.novelty_level in {
            "major_release",
            "material_update",
        }:
            base = self._number(analysis.novelty_score)
        elif item.source_type == SourceType.AIHOT:
            base = aihot_scores.get(id(item), 0.0)
        elif item.source_type == SourceType.BILIBILI:
            base = bilibili_scores.get(id(item), 0.0)
        else:
            base = self._number(item.metadata.get("heat_score"))
        if item.metadata.get("hot_topic") is True:
            base = max(base, 9.0)
        cross_platform = item.metadata.get("cross_platform_count")
        if isinstance(cross_platform, (int, float)) and cross_platform > 1:
            base += min((float(cross_platform) - 1.0) * 0.5, 1.0)
        return round(min(base, 10.0), 2)

    @staticmethod
    def _is_official(item: ContentItem) -> bool:
        source_kind = str(item.metadata.get("source_kind") or "").casefold()
        source_level = str(item.metadata.get("source_level") or "").casefold()
        return source_level == "official" or source_kind in {
            "official",
            "官方来源",
        }

    @staticmethod
    def _event_key(item: ContentItem) -> str:
        analysis = item.processing.analysis if item.processing else None
        return normalize_editorial_token(analysis.event_key) if analysis else ""

    @classmethod
    def _identity_tokens(cls, item: ContentItem) -> set[str]:
        tokens = {f"id:{item.id}", f"url:{canonical_url(str(item.url))}"}
        event_key = cls._event_key(item)
        if event_key:
            tokens.add(f"event_key:{event_key}")
        return tokens

    @staticmethod
    def _profile_priority(item: ContentItem) -> int:
        profile = profile_id(item)
        if profile in AI_PROFILES:
            return 2
        if profile == PLATFORM_TREND_PROFILE:
            return 1
        return 0

    @classmethod
    def _event_winner_key(cls, item: ContentItem) -> tuple:
        analysis = item.processing.analysis if item.processing else None
        return (
            cls._profile_priority(item),
            1 if cls._is_official(item) else 0,
            cls._number(item.metadata.get("breakout_score")),
            cls._number(analysis.score if analysis else None),
            cls._number(analysis.evidence_quality_score if analysis else None),
            item.published_at.astimezone(timezone.utc).timestamp(),
            item.id,
        )

    @classmethod
    def _sort_key(cls, item: ContentItem) -> tuple:
        analysis = item.processing.analysis if item.processing else None
        status = str(item.metadata.get("breakout_status") or "none")
        return (
            2 if status == "breakout" else 1 if status == "hot_unverified" else 0,
            cls._number(item.metadata.get("breakout_score")),
            cls._number(analysis.score if analysis else None),
            cls._number(analysis.evidence_quality_score if analysis else None),
            item.published_at.astimezone(timezone.utc).timestamp(),
            item.id,
        )

    @staticmethod
    def _assign_display_section(item: ContentItem) -> None:
        profile = profile_id(item)
        if profile == PLATFORM_TREND_PROFILE:
            item.metadata["display_section"] = "platform_trend"
            return
        if profile not in AI_PROFILES:
            return
        signal = BreakoutSelector._number(item.metadata.get("source_signal_score"))
        evidence = 0.0
        if item.processing and item.processing.analysis:
            evidence = BreakoutSelector._number(
                item.processing.analysis.evidence_quality_score
            )
        media_candidate = item.metadata.get("ai_media_candidate") is True or (
            item.source_type == SourceType.BILIBILI
        )
        if media_candidate and signal >= 6.0 and (
            evidence >= 6.0
            or item.metadata.get("breakout_status") == "hot_unverified"
        ):
            item.metadata["display_section"] = "ai_media"
        elif profile == AI_TECHNOLOGY_PROFILE:
            item.metadata["display_section"] = "ai_technology"
        else:
            item.metadata["display_section"] = "ai_application"


def canonical_identity(item: ContentItem) -> tuple[str, str, str]:
    """Return defensive card identity: id, canonical URL, normalized event key."""
    return (
        str(item.id),
        canonical_url(str(item.url)),
        BreakoutSelector._event_key(item),
    )

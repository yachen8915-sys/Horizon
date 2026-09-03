"""Maturity-aware social propagation hard gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..models import ContentItem, SocialEngagementQualityConfig, SourceType


SOCIAL_SOURCE_PLATFORM = {
    SourceType.BILIBILI: "bilibili",
    SourceType.YOUTUBE: "youtube",
    SourceType.TWITTER: "twitter",
    SourceType.BLUESKY: "bluesky",
}


@dataclass
class SocialQualityResult:
    eligible: list[ContentItem] = field(default_factory=list)
    observing: list[ContentItem] = field(default_factory=list)
    rejected: list[ContentItem] = field(default_factory=list)


class SocialEngagementQualityGate:
    def __init__(self, config: SocialEngagementQualityConfig) -> None:
        self.config = config

    def evaluate(
        self,
        items: list[ContentItem],
        *,
        now: datetime | None = None,
    ) -> SocialQualityResult:
        observed_at = self._utc(now or datetime.now(timezone.utc))
        result = SocialQualityResult()
        grouped: dict[str, list[ContentItem]] = {}
        for item in items:
            platform = str(item.metadata.get("quality_platform") or "").strip()
            if not platform:
                platform = SOCIAL_SOURCE_PLATFORM.get(item.source_type) or ""
            policy = self.config.platforms.get(platform or "")
            if self._is_official(item):
                item.metadata.setdefault("content_platform", platform)
                self._mark(item, "not_applicable", "official_source", None)
                result.eligible.append(item)
                continue
            if not self.config.enabled or not platform or not policy or not policy.enabled:
                item.metadata.setdefault("content_platform", platform)
                self._mark(item, "not_applicable", "official_or_unconfigured_source", None)
                result.eligible.append(item)
                continue
            grouped.setdefault(platform, []).append(item)

        for platform, candidates in grouped.items():
            policy = self.config.platforms[platform]
            mature_samples = [
                self._metric(item, policy.primary_metric)
                for item in candidates
                if self._age_hours(item, observed_at) >= policy.maturity_hours
                and self._metric(item, policy.primary_metric) is not None
            ]
            reference = max(
                policy.minimum_sample,
                self._quantile(mature_samples, policy.relative_quantile),
            )
            for item in candidates:
                metrics = {
                    name: self._metric(item, name)
                    for name in policy.required_metrics
                }
                complete = sum(value is not None for value in metrics.values())
                age = self._age_hours(item, observed_at)
                item.metadata.update(
                    {
                        "content_platform": platform,
                        "content_age_hours": round(age, 2),
                        "engagement_primary_metric": policy.primary_metric,
                        "engagement_complete_fields": complete,
                        "first_observed_at": item.metadata.get("first_observed_at")
                        or observed_at.isoformat(),
                        "last_observed_at": observed_at.isoformat(),
                        "observation_deadline": (
                            self._utc(item.published_at)
                            + timedelta(hours=policy.maturity_hours)
                        ).isoformat(),
                    }
                )
                if age < policy.maturity_hours:
                    self._mark(item, "observing", "insufficient_maturity", 0.0)
                    result.observing.append(item)
                    continue
                if complete < policy.minimum_complete_fields:
                    self._mark(item, "rejected", "incomplete_engagement", 0.0)
                    result.rejected.append(item)
                    continue
                primary = metrics.get(policy.primary_metric) or 0
                quality = self._quality_score(
                    metrics,
                    primary_metric=policy.primary_metric,
                    reference=reference,
                    minimum=policy.minimum_sample,
                )
                if (
                    primary < policy.minimum_sample
                    or primary < reference * policy.relative_ratio
                    or quality < policy.minimum_quality_score
                ):
                    self._mark(item, "rejected", "low_propagation", quality)
                    result.rejected.append(item)
                    continue
                self._mark(item, "passed", "qualified", quality)
                result.eligible.append(item)
        return result

    @staticmethod
    def _is_official(item: ContentItem) -> bool:
        level = str(item.metadata.get("source_level") or "").casefold()
        kind = str(item.metadata.get("source_kind") or "").casefold()
        category = str(item.metadata.get("category") or "").casefold()
        return (
            level == "official"
            or kind in {"official", "官方来源"}
            or category.startswith("official-")
        )

    @staticmethod
    def _quality_score(
        metrics: dict[str, int | None],
        *,
        primary_metric: str,
        reference: float,
        minimum: int,
    ) -> float:
        primary = max(0, metrics.get(primary_metric) or 0)
        if primary < minimum:
            return 0.0
        primary_score = min(4.0, 4.0 * primary / max(reference, 1.0))
        confidence = min(1.0, primary / max(minimum * 3, 1))
        interaction_total = sum(
            value or 0
            for name, value in metrics.items()
            if name != primary_metric
        )
        interaction_score = min(6.0, interaction_total / max(primary, 1) * 40)
        return round(min(10.0, primary_score + confidence * interaction_score), 3)

    @staticmethod
    def _mark(
        item: ContentItem, status: str, reason: str, score: float | None
    ) -> None:
        item.metadata["engagement_gate_status"] = status
        item.metadata["engagement_gate_reason"] = reason
        if score is not None:
            item.metadata["engagement_quality_score"] = score

    @staticmethod
    def _metric(item: ContentItem, name: str) -> int | None:
        engagement = item.metadata.get("engagement")
        if not isinstance(engagement, dict) or engagement.get(name) is None:
            return None
        try:
            return max(0, int(engagement[name]))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _age_hours(item: ContentItem, now: datetime) -> float:
        return max(
            0.0,
            (now - SocialEngagementQualityGate._utc(item.published_at)).total_seconds()
            / 3600,
        )

    @staticmethod
    def _quantile(values: list[int | None], quantile: float) -> float:
        clean = sorted(float(value) for value in values if value is not None)
        if not clean:
            return 0.0
        position = (len(clean) - 1) * quantile
        lower = int(position)
        upper = min(lower + 1, len(clean) - 1)
        fraction = position - lower
        return clean[lower] * (1 - fraction) + clean[upper] * fraction

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

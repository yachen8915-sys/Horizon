"""Profile-driven content enrichment."""

import asyncio
from difflib import SequenceMatcher
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TypeVar

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
)
from tenacity import retry, stop_after_attempt, wait_exponential

from .client import AIClient
from .localization import normalize_language
from .prompting.enrichment import (
    MAX_TOOL_REQUESTS,
    artifact_prompt,
    block_prompt,
    item_context,
    recommended_angle_audit_context,
    recommended_angle_audit_prompt,
    recommended_angle_review_context,
    recommended_angle_review_prompt,
    tool_planning_prompt,
    tool_results_text,
)
from .utils import parse_json_response
from ..models import ArtifactSource, ContentArtifact, ContentBlock, ContentItem
from ..processing.profiles import LoadedProfile, ProfileBlock, ProfileRegistry
from ..processing.tools import ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)

class ToolRequest(BaseModel):
    block_id: str
    tool: str
    arguments: dict[str, Any]
    purpose: str


class ToolPlan(BaseModel):
    tool_requests: list[ToolRequest] = Field(default_factory=list)


class GeneratedArtifact(BaseModel):
    title: str
    blocks: list[ContentBlock]

    @model_validator(mode="after")
    def validate_non_empty_content(self) -> "GeneratedArtifact":
        if not self.title.strip():
            raise ValueError("title must not be empty")
        for block in self.blocks:
            if not block.title.strip() or not block.content.strip():
                raise ValueError(f"block {block.id} must not be empty")
        return self


class GeneratedBlock(BaseModel):
    title: str = ""
    block: Optional[ContentBlock] = None

    @model_validator(mode="after")
    def validate_non_empty_block(self) -> "GeneratedBlock":
        if self.block and (
            not self.block.title.strip() or not self.block.content.strip()
        ):
            raise ValueError(f"block {self.block.id} must not be empty")
        return self


class GeneratedBlockWithHeader(GeneratedBlock):
    @field_validator("title")
    @classmethod
    def validate_non_empty_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value


class CandidateAngleItem(BaseModel):
    item_id: str
    angles: list[str] = Field(min_length=4, max_length=6)


class RecommendedAngleCandidates(BaseModel):
    items: list[CandidateAngleItem]


class AuditRemoval(BaseModel):
    item_id: str
    angle: str
    issue_type: str
    reason: str


class RecommendedAngleAudit(BaseModel):
    removals: list[AuditRemoval] = Field(default_factory=list)


@dataclass(frozen=True)
class AngleRejection:
    item_id: str
    angle: str
    reason: str
    stage: str


@dataclass
class RecommendedAngleReviewResult:
    generated_count: int = 0
    rejections: list[AngleRejection] = field(default_factory=list)
    audit_removals: list[AuditRemoval] = field(default_factory=list)
    regenerated_item_ids: list[str] = field(default_factory=list)
    failed_item_ids: list[str] = field(default_factory=list)
    final_counts: dict[str, int] = field(default_factory=dict)

    @property
    def final_count(self) -> int:
        return sum(self.final_counts.values())

    @property
    def deleted_count(self) -> int:
        return max(0, self.generated_count - self.final_count)


@dataclass
class EnrichmentBatchResult:
    succeeded_ids: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def succeeded_count(self) -> int:
        return len(self.succeeded_ids)

    @property
    def failed_count(self) -> int:
        return len(self.failures)

    @property
    def failed_ids(self) -> list[str]:
        return list(self.failures)

    @property
    def status(self) -> str:
        if not self.failures:
            return "success"
        if self.succeeded_ids:
            return "partial_failure"
        return "failure"


class ContentEnricher:
    """Generate localized block artifacts with profile-scoped tools."""

    ANGLE_REVIEW_BATCH_SIZE = 8

    def __init__(
        self,
        ai_client: AIClient,
        profiles: ProfileRegistry,
        languages: list[str],
        console: Optional[Console] = None,
        tools: Optional[ToolRegistry] = None,
    ):
        self.client = ai_client
        self.profiles = profiles
        self.languages = languages
        self.console = console or Console(stderr=True)
        self.tools = tools or ToolRegistry()
        self._validate_profile_tools()

    def _validate_profile_tools(self) -> None:
        for profile_id in self.profiles.ids:
            profile = self.profiles.get(profile_id)
            for block in profile.definition.enrichment.blocks:
                unknown = set(block.tools) - self.tools.names
                if unknown:
                    raise ValueError(
                        f"Profile {profile_id} block {block.id} uses unknown tools: "
                        f"{', '.join(sorted(unknown))}"
                    )

    def _get_concurrency(self) -> int:
        config = getattr(self.client, "config", None)
        return max(getattr(config, "enrichment_concurrency", 1), 1)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
    async def _complete(self, **kwargs: Any) -> str:
        return await self.client.complete(**kwargs)

    async def _complete_model(
        self,
        model: type[ModelT],
        *,
        system: str,
        user: str,
        error_message: str,
        validator: Optional[Callable[[ModelT], None]] = None,
        max_attempts: int = 2,
        correction_instruction: str = (
            "Return only a corrected JSON object."
        ),
    ) -> ModelT:
        validation_error: Optional[Exception] = None
        for attempt in range(max_attempts):
            request: dict[str, Any] = {
                "system": system,
                "user": user,
                "temperature": 0,
            }
            response = await self._complete(**request)
            parsed = parse_json_response(response)
            try:
                result = model.model_validate(parsed)
                if validator:
                    validator(result)
                return result
            except (ValidationError, ValueError) as exc:
                validation_error = exc
                logger.warning(
                    "%s response failed validation on attempt %s/%s "
                    "(response_length=%s, parsed_type=%s): %s",
                    model.__name__,
                    attempt + 1,
                    max_attempts,
                    len(response or ""),
                    type(parsed).__name__,
                    exc,
                )
                user += (
                    "\n\nYour previous response did not satisfy the output contract. "
                    f"Validation error: {exc}. {correction_instruction}"
                )
        raise ValueError(error_message) from validation_error

    async def enrich_batch(self, items: list[ContentItem]) -> EnrichmentBatchResult:
        semaphore = asyncio.Semaphore(self._get_concurrency())

        async def process(
            item: ContentItem, task_id: TaskID
        ) -> tuple[str, Optional[Exception]]:
            async with semaphore:
                try:
                    await self._enrich_item(item)
                except Exception as exc:
                    logger.error("Error enriching item %s: %s", item.id, exc)
                    return item.id, exc
                finally:
                    progress.advance(task_id)
            return item.id, None

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            transient=True,
            console=self.console,
        ) as progress:
            task_id = progress.add_task("Enriching", total=len(items))
            outcomes = await asyncio.gather(*(process(item, task_id) for item in items))

        result = EnrichmentBatchResult(
            succeeded_ids=[item_id for item_id, exc in outcomes if exc is None],
            failures={
                item_id: f"{type(exc).__name__}: {exc}"
                for item_id, exc in outcomes
                if exc is not None
            },
        )
        succeeded = {
            item_id for item_id in result.succeeded_ids
        }
        angle_review = await self.review_recommended_angles(
            [item for item in items if item.id in succeeded],
            language="zh",
        )
        for item_id in angle_review.failed_item_ids:
            if item_id in result.succeeded_ids:
                result.succeeded_ids.remove(item_id)
            result.failures[item_id] = (
                "ValueError: no valid recommended angles after audit"
            )
        return result

    async def review_recommended_angles(
        self,
        items: list[ContentItem],
        *,
        language: str = "zh",
    ) -> RecommendedAngleReviewResult:
        """Generate candidates, filter them, then audit all Pangmen angles."""
        result = RecommendedAngleReviewResult()
        if language != "zh" or language not in self.languages:
            return result
        candidates = [
            item
            for item in items
            if item.processing
            and item.processing.classification.profile == "pangmen-topic-radar"
            and item.processing.artifacts.get(language)
            and self._artifact_block(item, language, "recommended_angle")
        ]
        if not candidates:
            return result

        for start in range(0, len(candidates), self.ANGLE_REVIEW_BATCH_SIZE):
            chunk = candidates[start : start + self.ANGLE_REVIEW_BATCH_SIZE]
            await self._generate_and_filter_angle_chunk(
                chunk, language, result
            )
        regenerated = False
        for _ in range(3):
            active_candidates = [
                item for item in candidates
                if item.id not in result.failed_item_ids
            ]
            if not active_candidates:
                break
            try:
                regenerated = await self._audit_recommended_angles(
                    active_candidates, language, result
                )
            except Exception as exc:
                # A malformed/failed global audit must not abort the whole
                # daily digest. Keep the latest locally validated angles and
                # let the affected item continue with an audit warning.
                logger.warning(
                    "Recommended angle audit skipped after repeated failure: %s",
                    exc,
                )
                break
            if not regenerated:
                break
        if regenerated:
            logger.warning(
                "Recommended angle audit reached retry limit; keeping the latest "
                "locally validated angles."
            )
        result.final_counts = {
            item.id: len(
                self._split_angles(
                    self._artifact_block_content(
                        item, language, "recommended_angle"
                    )
                )
            )
            for item in candidates
        }
        return result

    async def _generate_and_filter_angle_chunk(
        self,
        items: list[ContentItem],
        language: str,
        result: RecommendedAngleReviewResult,
    ) -> None:
        generated = await self._request_angle_candidates(items, language)
        filtered: dict[str, list[str]] = {}
        for item in items:
            item_candidates = generated[item.id]
            result.generated_count += len(item_candidates)
            kept, rejected = self._filter_recommended_angle_candidates(
                item, item_candidates
            )
            result.rejections.extend(rejected)
            if not kept:
                try:
                    kept = await self._regenerate_one_item_angles(
                        item,
                        language,
                        result,
                        "; ".join(rejection.reason for rejection in rejected),
                    )
                except Exception as exc:
                    logger.warning(
                        "Recommended angles unavailable for %s; skipping item: %s",
                        item.id,
                        exc,
                    )
                    if item.id not in result.failed_item_ids:
                        result.failed_item_ids.append(item.id)
                    kept = []
            filtered[item.id] = kept
        self._apply_reviewed_angles(items, language, filtered)

    async def _request_angle_candidates(
        self,
        items: list[ContentItem],
        language: str,
        failure_note: str = "",
    ) -> dict[str, list[str]]:
        expected = {item.id for item in items}

        def validate_candidates(review: RecommendedAngleCandidates) -> None:
            returned_ids = [entry.item_id for entry in review.items]
            if len(returned_ids) != len(set(returned_ids)):
                raise ValueError("duplicate item_id in angle candidates")
            if set(returned_ids) != expected:
                raise ValueError(
                    "angle candidates must return every input item exactly once"
                )

        user = recommended_angle_review_context(
            self._recommended_angle_payload(items, language)
        )
        if failure_note:
            user += (
                "\n\n# 上一轮候选全部无效，必须重新生成\n\n"
                + failure_note
            )
        review = await self._complete_model(
            RecommendedAngleCandidates,
            system=recommended_angle_review_prompt(),
            user=user,
            error_message="Invalid recommended angle candidates",
            validator=validate_candidates,
        )
        return {entry.item_id: entry.angles for entry in review.items}

    async def _regenerate_one_item_angles(
        self,
        item: ContentItem,
        language: str,
        result: RecommendedAngleReviewResult,
        failure_note: str,
    ) -> list[str]:
        latest_rejected: list[AngleRejection] = []
        for attempt in range(2):
            note = failure_note
            if latest_rejected:
                note += (
                    "\n上一轮单条重生仍全部无效，必须避开这些问题："
                    + "; ".join(rejection.reason for rejection in latest_rejected)
                )
            generated = await self._request_angle_candidates(
                [item], language, note
            )
            candidates = generated[item.id]
            result.generated_count += len(candidates)
            result.regenerated_item_ids.append(item.id)
            kept, latest_rejected = self._filter_recommended_angle_candidates(
                item, candidates, stage=f"regeneration-{attempt + 1}"
            )
            result.rejections.extend(latest_rejected)
            if kept:
                self._apply_reviewed_angles(
                    [item], language, {item.id: kept}
                )
                return kept
        reasons = "; ".join(
            rejection.reason for rejection in latest_rejected
        )
        raise ValueError(
            f"all regenerated recommended angles were invalid for {item.id}: {reasons}"
        )

    async def _audit_recommended_angles(
        self,
        items: list[ContentItem],
        language: str,
        result: RecommendedAngleReviewResult,
    ) -> bool:
        expected = {item.id: item for item in items}
        payload = [
            {
                "item_id": item.id,
                "topic_title": item.processing.artifacts[language].title,
                "angles": self._split_angles(
                    self._artifact_block_content(
                        item, language, "recommended_angle"
                    )
                ),
            }
            for item in items
        ]

        def validate_audit(audit: RecommendedAngleAudit) -> None:
            removal_keys = [
                (entry.item_id, self._normalize_angle(entry.angle))
                for entry in audit.removals
            ]
            if len(removal_keys) != len(set(removal_keys)):
                raise ValueError("duplicate removal in angle audit")
            if not {entry.item_id for entry in audit.removals}.issubset(expected):
                raise ValueError("angle audit returned an unknown item_id")
            proposed = {
                item.id: self._split_angles(
                    self._artifact_block_content(
                        item, language, "recommended_angle"
                    )
                )
                for item in items
            }
            for removal in audit.removals:
                if removal.angle not in proposed[removal.item_id]:
                    raise ValueError(
                        "angle audit removal must exactly match an input angle"
                    )
                proposed[removal.item_id].remove(removal.angle)
            self._validate_angle_collection(proposed, expected)

        audit = await self._complete_model(
            RecommendedAngleAudit,
            system=recommended_angle_audit_prompt(),
            user=recommended_angle_audit_context(payload),
            error_message="Invalid recommended angle audit",
            validator=validate_audit,
            max_attempts=3,
            correction_instruction=(
                '只返回一个合法 JSON 对象，顶层必须是 {"removals": [...]}；'
                '没有删除项时必须返回 {"removals": []}，不要输出解释或 Markdown。'
            ),
        )
        remaining = {
            item.id: self._split_angles(
                self._artifact_block_content(
                    item, language, "recommended_angle"
                )
            )
            for item in items
        }
        for removal in audit.removals:
            remaining[removal.item_id].remove(removal.angle)
        result.audit_removals.extend(audit.removals)
        self._apply_reviewed_angles(items, language, remaining)

        regenerated = False
        for item in items:
            if remaining[item.id]:
                continue
            try:
                await self._regenerate_one_item_angles(
                    item,
                    language,
                    result,
                    "全量审计删除了该选题的全部候选，请生成全新且具体的角度。",
                )
                regenerated = True
            except Exception as exc:
                logger.warning(
                    "Global angle audit could not repair %s; excluding item: %s",
                    item.id,
                    exc,
                )
                if item.id not in result.failed_item_ids:
                    result.failed_item_ids.append(item.id)
                self._apply_reviewed_angles(
                    [item], language, {item.id: []}
                )
        return regenerated

    def _filter_recommended_angle_candidates(
        self,
        item: ContentItem,
        candidates: list[str],
        stage: str = "local",
    ) -> tuple[list[str], list[AngleRejection]]:
        kept: list[str] = []
        rejected: list[AngleRejection] = []
        for raw_angle in candidates:
            angle = re.sub(r"\s+", " ", str(raw_angle)).strip()
            try:
                self._validate_recommended_angle(angle, item)
            except ValueError as exc:
                rejected.append(
                    AngleRejection(item.id, angle, str(exc), stage)
                )
                continue
            if any(
                SequenceMatcher(
                    None,
                    self._normalize_angle(angle),
                    self._normalize_angle(existing),
                ).ratio()
                >= 0.82
                for existing in kept
            ):
                rejected.append(
                    AngleRejection(
                        item.id,
                        angle,
                        "semantic duplicate within the same topic",
                        stage,
                    )
                )
                continue
            if len(kept) >= 4:
                rejected.append(
                    AngleRejection(
                        item.id,
                        angle,
                        "lower-ranked candidate beyond the final maximum of four",
                        stage,
                    )
                )
                continue
            kept.append(angle)
        return kept, rejected

    def _recommended_angle_payload(
        self, items: list[ContentItem], language: str
    ) -> list[dict[str, Any]]:
        payload = []
        for item in items:
            artifact = item.processing.artifacts[language]
            analysis = item.processing.analysis
            payload.append(
                {
                    "item_id": item.id,
                    "topic_title": artifact.title,
                    "source_title": item.title,
                    "analysis_summary": analysis.summary if analysis else "",
                    "what_happened": self._artifact_block_content(
                        item, language, "what_happened"
                    ),
                    "audience_problem": self._artifact_block_content(
                        item, language, "audience_problem"
                    ),
                    "current_angles": self._split_angles(
                        self._artifact_block_content(
                            item, language, "recommended_angle"
                        )
                    ),
                }
            )
        return payload

    def _validate_angle_collection(
        self,
        angles_by_id: dict[str, list[str]],
        expected: dict[str, ContentItem],
    ) -> None:
        seen_angles: dict[str, str] = {}
        for item_id, angles in angles_by_id.items():
            item = expected[item_id]
            for angle in angles:
                self._validate_recommended_angle(angle, item)
                normalized = self._normalize_angle(angle)
                previous = seen_angles.get(normalized)
                if previous is not None:
                    raise ValueError(
                        f"duplicate recommended angle for {previous} and {item_id}"
                    )
                seen_angles[normalized] = item_id

    def _apply_reviewed_angles(
        self,
        items: list[ContentItem],
        language: str,
        angles_by_id: dict[str, list[str]],
    ) -> None:
        for item in items:
            if item.id not in angles_by_id:
                continue
            block = self._artifact_block(item, language, "recommended_angle")
            if block is not None:
                block.content = "\n".join(angles_by_id[item.id])

    @staticmethod
    def _artifact_block(
        item: ContentItem, language: str, block_id: str
    ) -> Optional[ContentBlock]:
        artifact = item.processing.artifacts.get(language) if item.processing else None
        if not artifact:
            return None
        return next((block for block in artifact.blocks if block.id == block_id), None)

    @classmethod
    def _artifact_block_content(
        cls, item: ContentItem, language: str, block_id: str
    ) -> str:
        block = cls._artifact_block(item, language, block_id)
        return block.content if block else ""

    @staticmethod
    def _split_angles(content: str) -> list[str]:
        angles = []
        for raw in re.split(r"\r?\n+|[；;]+", content or ""):
            angle = re.sub(r"^\s*(?:[-*•]|\d+[.)、])\s*", "", raw).strip()
            if angle:
                angles.append(angle)
        return angles[:4]

    @staticmethod
    def _normalize_angle(angle: str) -> str:
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", angle.lower())

    @classmethod
    def _validate_recommended_angle(
        cls, angle: str, item: ContentItem
    ) -> None:
        if "\n" in angle or "\r" in angle:
            raise ValueError(
                f"recommended angle must be one sentence: {angle}"
            )
        value = re.sub(r"\s+", " ", angle).strip()
        if len(value) > 45:
            raise ValueError(
                f"recommended angle should be compressed to 45 characters or less: {angle}"
            )
        banned = (
            "用前后对比验证实际收益",
            "拆解普通用户可复现的操作路径",
            "拆解普通用户能复现的操作路径",
            "核对限制后判断是否值得跟进",
            "核对限制后再判断是否值得跟进",
            "录屏复现核心功能",
            "录屏测试真实效果",
            "用真实任务测试效果",
            "看看普通人能不能用",
            "提升效率",
            "值不值得使用",
        )
        if any(phrase in value for phrase in banned):
            raise ValueError(f"generic recommended angle is not allowed: {angle}")
        if re.search(r"(?:好用吗|值得用吗|能用吗)[？?。.]?$", value):
            raise ValueError(
                f"short generic product question is not a concrete angle: {angle}"
            )

        context = " ".join(
            filter(
                None,
                [
                    item.title,
                    item.processing.analysis.summary
                    if item.processing and item.processing.analysis
                    else "",
                    cls._artifact_block_content(item, "zh", "what_happened"),
                    cls._artifact_block_content(item, "zh", "audience_problem"),
                ],
            )
        )
        ascii_anchors = {
            token.lower()
            for token in re.findall(r"[A-Za-z][A-Za-z0-9.+-]{1,}", context)
            if token.lower() not in {"ai", "the", "and", "with"}
        }
        chinese_spans = re.findall(r"[\u4e00-\u9fff]{2,}", context)
        generic_ngrams = {
            "普通", "用户", "使用", "功能", "工具", "内容", "视频", "实际",
            "效果", "工作", "流程", "效率", "问题", "场景", "产品", "可以",
            "如何", "什么", "需要", "适合", "进行", "一个",
        }
        chinese_anchors = {
            span[index : index + size]
            for span in chinese_spans
            for size in (2, 3, 4)
            for index in range(max(0, len(span) - size + 1))
            if span[index : index + size] not in generic_ngrams
        }
        lowered = value.lower()
        has_ascii_anchor = any(anchor in lowered for anchor in ascii_anchors)
        has_chinese_anchor = any(
            anchor in value for anchor in chinese_anchors
        )
        if not has_ascii_anchor and not has_chinese_anchor:
            raise ValueError(
                f"recommended angle lacks item-specific product, feature, or scenario: {angle}"
            )
        if has_ascii_anchor and not has_chinese_anchor:
            raise ValueError(
                "recommended angle names the product but lacks a concrete feature, "
                f"problem, audience, or scenario: {angle}"
            )
        value_markers = (
            "？", "?", "为什么", "到底", "能否", "是否", "怎么", "如何",
            "省", "少做", "压缩", "只需", "不用", "不再", "避免", "告别",
            "翻车", "限制", "误区", "错", "反而", "替代", "直接", "从零",
            "差多少", "会不会", "适合谁", "不适合", "真能", "结果", "风险",
        )
        if not any(marker in value for marker in value_markers):
            raise ValueError(
                "recommended angle lacks a clear pain point, conflict, result, "
                f"scenario, or viewpoint: {angle}"
            )

    async def _enrich_item(self, item: ContentItem) -> None:
        if not item.processing or not item.processing.analysis:
            raise ValueError("Item must be analyzed before enrichment")
        profile = self.profiles.get(item.processing.classification.profile)
        for language in self.languages:
            item.processing.artifacts.pop(language, None)
        tool_results = await self._plan_and_execute_tools(item, profile)
        sources = self._sources_from_tool_results(tool_results)

        artifacts = {}
        for language in self.languages:
            generated = await self._generate_artifact(
                item, profile, language, tool_results
            )
            self._expand_request_source_refs(generated.blocks, tool_results)
            self._validate_blocks(generated.blocks, profile, tool_results)
            generated.title = normalize_language(generated.title, language)
            for block in generated.blocks:
                block.title = normalize_language(block.title, language)
                block.content = normalize_language(block.content, language)
            referenced = {
                source_id
                for block in generated.blocks
                for source_id in block.source_refs
            }
            artifacts[language] = ContentArtifact(
                language=language,
                title=generated.title,
                blocks=generated.blocks,
                sources=[source for source in sources.values() if source.id in referenced],
            )
        item.processing.artifacts.update(artifacts)

    @staticmethod
    def _expand_request_source_refs(
        blocks: list[ContentBlock],
        tool_results: list[ToolResult],
    ) -> None:
        """Expand a request-level citation to its concrete result citations."""
        request_sources = {
            (result.block_id, result.request_id): [
                f"{result.request_id}-{index}"
                for index, _ in enumerate(result.results, start=1)
            ]
            for result in tool_results
        }
        for block in blocks:
            expanded = []
            for source_ref in block.source_refs:
                expanded.extend(
                    request_sources.get((block.id, source_ref), [source_ref])
                )
            block.source_refs = list(dict.fromkeys(expanded))

    async def _plan_and_execute_tools(
        self, item: ContentItem, profile: LoadedProfile
    ) -> list[ToolResult]:
        allowed = {
            block.id: set(block.tools)
            for block in profile.definition.enrichment.blocks
        }
        if not any(allowed.values()):
            return []

        plan = await self._complete_model(
            ToolPlan,
            system=tool_planning_prompt(profile.definition.enrichment.blocks),
            user=item_context(item, profile, include_content=True),
            error_message="Invalid enrichment tool plan",
        )

        results = []
        seen = set()
        for request in plan.tool_requests[:MAX_TOOL_REQUESTS]:
            if request.block_id not in allowed:
                raise ValueError(f"Tool request targets unknown block: {request.block_id}")
            if request.tool not in allowed[request.block_id]:
                raise ValueError(
                    f"Tool {request.tool} is not allowed for block {request.block_id}"
                )
            key = (request.block_id, request.tool, json.dumps(request.arguments, sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            results.append(
                await self.tools.execute(
                    request_id=f"tool-{len(results) + 1}",
                    block_id=request.block_id,
                    tool=request.tool,
                    arguments=request.arguments,
                )
            )
        return results

    async def _generate_artifact(
        self,
        item: ContentItem,
        profile: LoadedProfile,
        language: str,
        tool_results: list[ToolResult],
    ) -> GeneratedArtifact:
        configured_blocks = profile.definition.enrichment.blocks
        result_block_ids = {result.block_id for result in tool_results}
        base_blocks = [
            block for block in configured_blocks if block.id not in result_block_ids
        ]
        title = ""
        generated_by_id: dict[str, ContentBlock] = {}

        if base_blocks:
            required_base_ids = {
                block.id for block in base_blocks if not block.optional
            }
            allowed_base_ids = {block.id for block in base_blocks}

            def validate_required_blocks(generated: GeneratedArtifact) -> None:
                generated_ids = [block.id for block in generated.blocks]
                unknown = set(generated_ids) - allowed_base_ids
                if unknown:
                    raise ValueError(
                        "unknown blocks: " + ", ".join(sorted(unknown))
                    )
                if len(generated_ids) != len(set(generated_ids)):
                    raise ValueError("duplicate block IDs")
                missing = required_base_ids - set(generated_ids)
                if missing:
                    raise ValueError(
                        "missing required blocks: " + ", ".join(sorted(missing))
                    )

            generated = await self._complete_model(
                GeneratedArtifact,
                system=artifact_prompt(profile, language, base_blocks),
                user=(
                    item_context(item, profile, include_content=True)
                    + "\n\n# Tool results\n\nNo tool results are available to these blocks."
                ),
                error_message="Invalid enrichment artifact",
                validator=validate_required_blocks,
            )
            title = generated.title.strip()
            allowed_ids = {block.id for block in base_blocks}
            configured_ids = {block.id for block in configured_blocks}
            for generated_block in generated.blocks:
                if generated_block.id not in allowed_ids:
                    if generated_block.id in configured_ids:
                        continue
                    raise ValueError(
                        f"Artifact contains unknown block: {generated_block.id}"
                    )
                if generated_block.id in generated_by_id:
                    raise ValueError(
                        f"Artifact contains duplicate block: {generated_block.id}"
                    )
                generated_by_id[generated_block.id] = generated_block
            missing = {
                block.id
                for block in base_blocks
                if not block.optional and block.id not in generated_by_id
            }
            if missing:
                raise ValueError(
                    f"Artifact is missing required blocks: {', '.join(sorted(missing))}"
                )

        for block in configured_blocks:
            if block.id not in result_block_ids:
                continue
            block_results = [
                result for result in tool_results if result.block_id == block.id
            ]
            response_model = GeneratedBlockWithHeader if not title else GeneratedBlock

            def validate_requested_block(generated: GeneratedBlock) -> None:
                if generated.block is None:
                    if not block.optional:
                        raise ValueError(f"missing required block: {block.id}")
                    return
                if generated.block.id != block.id:
                    raise ValueError(
                        f"block ID {generated.block.id} does not match {block.id}"
                    )

            generated = await self._complete_model(
                response_model,
                system=block_prompt(
                    profile,
                    language,
                    block,
                    include_header=not title,
                ),
                user=(
                    item_context(item, profile, include_content=True)
                    + f"\n\n# Tool results for block `{block.id}`\n\n"
                    + tool_results_text(block_results)
                ),
                error_message=f"Invalid enrichment block: {block.id}",
                validator=validate_requested_block,
            )

            if not title:
                title = generated.title.strip()
            if generated.block is None:
                if not block.optional:
                    raise ValueError(f"Artifact is missing required block: {block.id}")
                continue
            if generated.block.id != block.id:
                raise ValueError(
                    f"Artifact block {generated.block.id} does not match requested block {block.id}"
                )
            generated_by_id[block.id] = generated.block

        if not title:
            raise ValueError("Enrichment artifact title cannot be empty")
        blocks = [
            generated_by_id[block.id]
            for block in configured_blocks
            if block.id in generated_by_id
        ]
        configured_by_id = {block.id: block for block in configured_blocks}
        for generated_block in blocks:
            generated_block.primary = configured_by_id[generated_block.id].primary
        return GeneratedArtifact(title=title, blocks=blocks)

    @staticmethod
    def _sources_from_tool_results(
        results: list[ToolResult],
    ) -> dict[str, ArtifactSource]:
        sources = {}
        for result in results:
            for index, entry in enumerate(result.results, start=1):
                source_id = f"{result.request_id}-{index}"
                sources[source_id] = ArtifactSource(
                    id=source_id,
                    title=entry["title"],
                    url=entry["url"],
                )
        return sources

    @staticmethod
    def _validate_blocks(
        blocks: list[ContentBlock],
        profile: LoadedProfile,
        tool_results: list[ToolResult],
    ) -> None:
        configured: dict[str, ProfileBlock] = {
            block.id: block for block in profile.definition.enrichment.blocks
        }
        seen = set()
        for block in blocks:
            if block.id not in configured:
                raise ValueError(f"Artifact contains unknown block: {block.id}")
            if block.id in seen:
                raise ValueError(f"Artifact contains duplicate block: {block.id}")
            seen.add(block.id)
            if not block.title.strip() or not block.content.strip():
                raise ValueError(f"Artifact block {block.id} cannot be empty")
            block_source_ids = {
                f"{result.request_id}-{index}"
                for result in tool_results
                if result.block_id == block.id
                for index, _ in enumerate(result.results, start=1)
            }
            unknown_refs = set(block.source_refs) - block_source_ids
            if unknown_refs:
                raise ValueError(
                    f"Block {block.id} contains unknown source refs: "
                    f"{', '.join(sorted(unknown_refs))}"
                )
        required = {block.id for block in configured.values() if not block.optional}
        missing = required - seen
        if missing:
            raise ValueError(
                f"Artifact is missing required blocks: {', '.join(sorted(missing))}"
            )

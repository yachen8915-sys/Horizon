import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ai.enricher import (
    ContentEnricher,
    RecommendedAngleAudit,
    RecommendedAngleReviewResult,
)
from src.models import (
    ClassificationResult,
    ContentAnalysis,
    ContentArtifact,
    ContentBlock,
    ContentItem,
    ProcessingResult,
    SourceType,
)
from src.processing import ProfileRegistry
from src.processing.tools import ToolResult


PROFILES = ProfileRegistry.load(
    Path(__file__).resolve().parents[1] / "profiles", "tech-news"
)


def make_item() -> ContentItem:
    return ContentItem(
        id="rss:test:item",
        source_type=SourceType.RSS,
        title="A technical release",
        url="https://example.com/item",
        content="A project released a new architecture.",
        published_at=datetime.now(timezone.utc),
        profile="tech-news",
        processing=ProcessingResult(
            classification=ClassificationResult(
                profile="tech-news", method="source_override"
            ),
            analysis=ContentAnalysis(
                score=8.5,
                reason="Important release",
                summary="A new architecture was released.",
                tags=["systems"],
            ),
        ),
    )


class FakeTools:
    names = {"web_search"}

    async def execute(self, request_id, block_id, tool, arguments):
        assert block_id == "background"
        assert tool == "web_search"
        assert arguments == {"query": "project architecture"}
        return ToolResult(
            request_id=request_id,
            block_id=block_id,
            tool=tool,
            results=[
                {
                    "title": "Project documentation",
                    "url": "https://docs.example.com/project",
                    "text": "Architecture background.",
                }
            ],
        )


def test_enrichment_generates_blocks_and_validated_sources():
    responses = iter(
        [
            json.dumps(
                {
                    "tool_requests": [
                        {
                            "block_id": "background",
                            "tool": "web_search",
                            "arguments": {"query": "project architecture"},
                            "purpose": "Explain the existing architecture",
                        }
                    ]
                }
            ),
            json.dumps(
                {
                    "title": "新架構發佈",
                    "blocks": [
                        {
                            "id": "summary",
                            "title": "摘要",
                            "content": "項目發佈了新的架構，它改變了系統設計，並採用了新的邊界。",
                            "source_refs": [],
                        }
                    ],
                }
            ),
            json.dumps(
                {
                    "title": "新架構發佈",
                    "blocks": [
                        {
                            "id": "summary",
                            "type": "section",
                            "title": "摘要",
                            "content": "项目发布了新的架构，它改变了系统设计，并采用了新的边界。",
                            "source_refs": [],
                        },
                        {
                            "id": "background",
                            "type": "section",
                            "title": "未隔离的背景",
                            "content": "这个版本应被丢弃。",
                            "source_refs": [],
                        },
                    ],
                }
            ),
            json.dumps(
                {
                    "title": "",
                    "block": {
                        "id": "background",
                        "type": "section",
                        "title": "背景",
                        "content": "旧架构的背景信息。",
                        "source_refs": ["tool-1"],
                    },
                }
            ),
        ]
    )
    requests = []

    async def complete(**kwargs):
        requests.append(kwargs)
        return next(responses)

    item = make_item()
    enricher = ContentEnricher(
        SimpleNamespace(complete=complete),
        PROFILES,
        ["zh"],
        tools=FakeTools(),
    )
    asyncio.run(enricher._enrich_item(item))

    artifact = item.processing.artifacts["zh"]
    assert artifact.title == "新架构发布"
    assert artifact.blocks[0].content == "项目发布了新的架构，它改变了系统设计，并采用了新的边界。"
    assert [block.id for block in artifact.blocks] == [
        "summary",
        "background",
    ]
    assert artifact.blocks[-1].title == "背景"
    assert artifact.blocks[0].primary is True
    assert artifact.blocks[1].primary is False
    assert artifact.blocks[-1].source_refs == ["tool-1-1"]
    assert artifact.sources[0].url == "https://docs.example.com/project"
    assert len(requests) == 4
    assert "explicitly mentioned in the item" in requests[0]["system"]
    assert "Treat the source item as the primary account" in requests[1]["system"]
    assert "Simplified Chinese (language tag `zh`)" in requests[1]["system"]
    assert "Treat the source item as the primary account" in requests[3]["system"]
    assert "https://docs.example.com/project" not in requests[1]["user"]
    assert "https://docs.example.com/project" in requests[3]["user"]


def test_enrichment_rejects_tool_on_unapproved_block():
    async def complete(**kwargs):
        return json.dumps(
            {
                "tool_requests": [
                    {
                        "block_id": "summary",
                        "tool": "web_search",
                        "arguments": {"query": "unapproved"},
                        "purpose": "Rewrite the news",
                    }
                ]
            }
        )

    enricher = ContentEnricher(
        SimpleNamespace(complete=complete),
        PROFILES,
        ["zh"],
        tools=FakeTools(),
    )

    with pytest.raises(ValueError, match="not allowed"):
        asyncio.run(enricher._enrich_item(make_item()))


def test_enrichment_rejects_malformed_tool_plan():
    async def complete(**kwargs):
        return "[]"

    enricher = ContentEnricher(
        SimpleNamespace(complete=complete),
        PROFILES,
        ["zh"],
        tools=FakeTools(),
    )

    with pytest.raises(ValueError, match="tool plan"):
        asyncio.run(enricher._enrich_item(make_item()))


def test_enrichment_repairs_malformed_tool_plan_once():
    responses = iter(
        [
            "[]",
            json.dumps({"tool_requests": []}),
            json.dumps(
                {
                    "title": "Technical release",
                    "blocks": [
                        {
                            "id": "summary",
                            "type": "section",
                            "title": "Summary",
                            "content": "A complete summary.",
                            "source_refs": [],
                        },
                        {
                            "id": "background",
                            "type": "section",
                            "title": "Background",
                            "content": "Context for the release.",
                            "source_refs": [],
                        }
                    ],
                }
            ),
        ]
    )
    requests = []

    async def complete(**kwargs):
        requests.append(kwargs)
        return next(responses)

    item = make_item()
    enricher = ContentEnricher(
        SimpleNamespace(complete=complete),
        PROFILES,
        ["en"],
        tools=FakeTools(),
    )

    asyncio.run(enricher._enrich_item(item))

    assert len(requests) == 3
    assert all(request["temperature"] == 0 for request in requests)
    assert requests[1]["temperature"] == 0
    assert item.processing.artifacts["en"].blocks[0].id == "summary"


def test_enrichment_repairs_empty_blog_block_once():
    responses = iter(
        [
            json.dumps(
                {
                    "title": "A technical story",
                    "blocks": [
                        {
                            "id": "background",
                            "title": " ",
                            "content": "",
                            "source_refs": [],
                        },
                        {
                            "id": "solution",
                            "title": "Solution and results",
                            "content": "The author explains the implementation.",
                            "source_refs": [],
                        },
                        {
                            "id": "takeaway",
                            "title": "Takeaway",
                            "content": "The approach is useful in bounded cases.",
                            "source_refs": [],
                        }
                    ],
                }
            ),
            json.dumps(
                {
                    "title": "A technical story",
                    "blocks": [
                        {
                            "id": "background",
                            "title": "Background",
                            "content": "The author frames the original problem and constraints.",
                            "source_refs": [],
                        },
                        {
                            "id": "solution",
                            "title": "Solution and results",
                            "content": "The author explains the implementation and its effects.",
                            "source_refs": [],
                        },
                        {
                            "id": "takeaway",
                            "title": "Takeaway",
                            "content": "The approach is useful in bounded cases.",
                            "source_refs": [],
                        }
                    ],
                }
            ),
        ]
    )
    requests = []

    async def complete(**kwargs):
        requests.append(kwargs)
        return next(responses)

    item = make_item()
    item.profile = "tech-blog"
    item.processing.classification.profile = "tech-blog"
    enricher = ContentEnricher(
        SimpleNamespace(complete=complete),
        PROFILES,
        ["en"],
        tools=FakeTools(),
    )

    asyncio.run(enricher._enrich_item(item))

    assert len(requests) == 2
    assert requests[1]["temperature"] == 0
    assert "corrected JSON object" in requests[1]["user"]
    assert [
        block.id for block in item.processing.artifacts["en"].blocks
    ] == ["background", "solution", "takeaway"]
    assert all(
        not block.primary for block in item.processing.artifacts["en"].blocks
    )


def test_enrichment_repairs_schema_type_used_as_blog_block_id():
    responses = iter(
        [
            json.dumps(
                {
                    "title": "A technical story",
                    "blocks": [
                        {
                            "id": "section",
                            "type": "section",
                            "title": "Background",
                            "content": "A complete but misidentified background block.",
                            "source_refs": [],
                        },
                        {
                            "id": "solution",
                            "title": "Solution and results",
                            "content": "The implementation produced a measured result.",
                            "source_refs": [],
                        },
                        {
                            "id": "takeaway",
                            "title": "Takeaway",
                            "content": "The method has a clear bounded use.",
                            "source_refs": [],
                        }
                    ],
                }
            ),
            json.dumps(
                {
                    "title": "A technical story",
                    "blocks": [
                        {
                            "id": "background",
                            "title": "Background",
                            "content": "The corrected background and constraints.",
                            "source_refs": [],
                        },
                        {
                            "id": "solution",
                            "title": "Solution and results",
                            "content": "The implementation produced a measured result.",
                            "source_refs": [],
                        },
                        {
                            "id": "takeaway",
                            "title": "Takeaway",
                            "content": "The method has a clear bounded use.",
                            "source_refs": [],
                        }
                    ],
                }
            ),
        ]
    )
    requests = []

    async def complete(**kwargs):
        requests.append(kwargs)
        return next(responses)

    item = make_item()
    item.profile = "tech-blog"
    item.processing.classification.profile = "tech-blog"
    enricher = ContentEnricher(
        SimpleNamespace(complete=complete),
        PROFILES,
        ["en"],
        tools=FakeTools(),
    )

    asyncio.run(enricher._enrich_item(item))

    assert len(requests) == 2
    assert "unknown blocks: section" in requests[1]["user"]
    assert item.processing.artifacts["en"].blocks[0].id == "background"


def test_failed_reenrichment_removes_stale_target_artifact():
    async def complete(**kwargs):
        return json.dumps(
            {
                "title": "A technical story",
                "blocks": [
                    {
                        "id": "story",
                        "type": "section",
                        "title": "",
                        "content": "",
                        "source_refs": [],
                    }
                ],
            }
        )

    item = make_item()
    item.profile = "tech-blog"
    item.processing.classification.profile = "tech-blog"
    item.processing.artifacts["en"] = ContentArtifact(
        language="en",
        title="Stale story",
    )
    enricher = ContentEnricher(
        SimpleNamespace(complete=complete),
        PROFILES,
        ["en"],
        tools=FakeTools(),
    )

    with pytest.raises(ValueError, match="Invalid enrichment artifact"):
        asyncio.run(enricher._enrich_item(item))

    assert "en" not in item.processing.artifacts


def test_enrichment_rejects_cross_block_source_reference():
    block = ContentBlock(
        id="summary",
        type="section",
        title="News",
        content="Content",
        source_refs=["tool-1-1"],
    )
    tool_result = ToolResult(
        request_id="tool-1",
        block_id="background",
        tool="web_search",
        results=[
            {
                "title": "Source",
                "url": "https://example.com/source",
                "text": "Context",
            }
        ],
    )

    with pytest.raises(ValueError, match="unknown source refs"):
        ContentEnricher._validate_blocks(
            [block], PROFILES.get("tech-news"), [tool_result]
        )


def test_enrichment_rejects_empty_required_block():
    block = ContentBlock(
        id="summary",
        type="section",
        title=" ",
        content="",
        source_refs=[],
    )

    with pytest.raises(ValueError, match="cannot be empty"):
        ContentEnricher._validate_blocks(
            [block], PROFILES.get("tech-news"), []
        )


def test_enrichment_batch_reports_failure_without_discarding_successes():
    async def complete(**kwargs):
        raise RuntimeError("AI unavailable")

    successful_item = make_item()
    failed_item = make_item().model_copy(update={"id": "rss:test:failed"})
    enricher = ContentEnricher(
        SimpleNamespace(complete=complete),
        PROFILES,
        ["zh"],
        tools=FakeTools(),
    )

    async def enrich_item(item):  # type: ignore[no-untyped-def]
        if item.id == failed_item.id:
            raise RuntimeError("AI unavailable")

    enricher._enrich_item = enrich_item  # type: ignore[method-assign]

    result = asyncio.run(enricher.enrich_batch([successful_item, failed_item]))

    assert result.status == "partial_failure"
    assert result.succeeded_ids == [successful_item.id]
    assert result.failed_ids == [failed_item.id]
    assert result.failures[failed_item.id] == "RuntimeError: AI unavailable"


def test_topic_radar_batch_reviews_and_repairs_generic_duplicate_angles():
    first = make_item().model_copy(
        update={
            "id": "rss:test:workbuddy",
            "title": "WorkBuddy 文件为什么写不进飞书",
            "profile": "pangmen-topic-radar",
        }
    )
    second = make_item().model_copy(
        update={
            "id": "rss:test:gemini-forms",
            "title": "Gemini 在 Google Forms 里生成测验",
            "profile": "pangmen-topic-radar",
        }
    )
    for item in (first, second):
        item.processing.classification.profile = "pangmen-topic-radar"
        item.processing.artifacts["zh"] = ContentArtifact(
            language="zh",
            title=item.title,
            blocks=[
                ContentBlock(
                    id="what_happened",
                    type="section",
                    title="发生了什么",
                    content=item.title,
                    primary=True,
                ),
                ContentBlock(
                    id="audience_problem",
                    type="section",
                    title="适合谁、解决什么问题",
                    content="适合需要处理重复任务的普通用户。",
                ),
                ContentBlock(
                    id="recommended_angle",
                    type="section",
                    title="推荐切入点",
                    content="用前后对比验证实际收益。",
                ),
            ],
        )

    responses = iter(
        [
            json.dumps(
                {
                    "items": [
                        {
                            "item_id": first.id,
                            "angles": [
                                "同一份客户资料，手动录入与 WorkBuddy 写入飞书究竟差几步？",
                                "WorkBuddy 已授权却仍写不进飞书，问题可能卡在哪一层？",
                                "用前后对比验证实际收益。",
                                "WorkBuddy 好用吗？",
                            ],
                        },
                        {
                            "item_id": second.id,
                            "angles": [
                                "教师拿同一份 PDF 出题，Gemini 能把半小时压缩到几分钟？",
                                "Gemini 生成测验看似省事，答案准确率和题型限制会不会翻车？",
                                "拆解普通用户能复现的操作路径。",
                                "Gemini 好用吗？",
                            ],
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            json.dumps({"removals": []}, ensure_ascii=False),
        ]
    )
    requests = []

    async def complete(**kwargs):
        requests.append(kwargs)
        return next(responses)

    enricher = ContentEnricher(
        SimpleNamespace(complete=complete),
        PROFILES,
        ["zh"],
        tools=FakeTools(),
    )

    async def keep_existing_artifact(item):  # type: ignore[no-untyped-def]
        return None

    enricher._enrich_item = keep_existing_artifact  # type: ignore[method-assign]
    result = asyncio.run(enricher.enrich_batch([first, second]))

    assert result.status == "success"
    assert len(requests) == 2
    assert "跨选题" in requests[0]["system"]
    assert "4-6 条候选" in requests[0]["system"]
    assert "全量" in requests[1]["system"]
    assert first.processing.artifacts["zh"].blocks[-1].content == (
        "同一份客户资料，手动录入与 WorkBuddy 写入飞书究竟差几步？\n"
        "WorkBuddy 已授权却仍写不进飞书，问题可能卡在哪一层？"
    )
    assert second.processing.artifacts["zh"].blocks[-1].content == (
        "Gemini 生成测验看似省事，答案准确率和题型限制会不会翻车？"
    )


def test_watch_pool_skips_angle_enrichment_without_ai_call():
    class NoCallClient:
        config = SimpleNamespace(enrichment_concurrency=1)

        async def complete(self, **kwargs):
            raise AssertionError("watch pool must not call enrichment AI")

    item = make_item()
    item.profile = "pangmen-platform-trend-radar"
    item.processing.classification.profile = "pangmen-platform-trend-radar"
    item.metadata["trend_pool"] = "watch"
    enricher = ContentEnricher(NoCallClient(), PROFILES, ["zh"])

    result = asyncio.run(enricher.enrich_batch([item]))

    assert result.succeeded_ids == [item.id]
    assert item.processing.artifacts == {}


def test_topic_radar_angle_generation_is_chunked_before_global_audit():
    items = []
    for index in range(9):
        item = make_item().model_copy(
            update={
                "id": f"rss:test:topic-{index}",
                "title": f"工具{index}自动整理运营周报",
                "profile": "pangmen-topic-radar",
            }
        )
        item.processing.classification.profile = "pangmen-topic-radar"
        item.processing.artifacts["zh"] = ContentArtifact(
            language="zh",
            title=item.title,
            blocks=[
                ContentBlock(
                    id="what_happened",
                    type="section",
                    title="发生了什么",
                    content=f"工具{index}新增了自动整理运营周报的功能。",
                    primary=True,
                ),
                ContentBlock(
                    id="audience_problem",
                    type="section",
                    title="适合谁、解决什么问题",
                    content="适合每周需要汇总多张表格的运营人员。",
                ),
                ContentBlock(
                    id="recommended_angle",
                    type="section",
                    title="推荐切入点",
                    content="用前后对比验证实际收益。",
                ),
            ],
        )
        items.append(item)

    requests = []

    async def complete(**kwargs):
        requests.append(kwargs)
        if kwargs["system"].startswith("# 旁门左道PPT推荐切入点全量"):
            return json.dumps({"removals": []}, ensure_ascii=False)
        payload = json.loads(kwargs["user"].split("\n\n", 1)[1])
        return json.dumps(
            {
                "items": [
                    {
                        "item_id": entry["item_id"],
                        "angles": [
                            f"{entry['topic_title']}后，运营每周少做哪些重复汇总步骤？",
                            f"运营用{entry['topic_title']}汇总周报，最容易在哪个环节翻车？",
                            f"{entry['topic_title']}真能替代复制表格吗？先看数据口径限制",
                            f"{entry['topic_title']}适合多项目运营，还是会增加维护成本？",
                        ],
                    }
                    for entry in payload
                ]
            },
            ensure_ascii=False,
        )

    enricher = ContentEnricher(
        SimpleNamespace(complete=complete),
        PROFILES,
        ["zh"],
        tools=FakeTools(),
    )

    async def keep_existing_artifact(item):  # type: ignore[no-untyped-def]
        return None

    enricher._enrich_item = keep_existing_artifact  # type: ignore[method-assign]
    result = asyncio.run(enricher.enrich_batch(items))

    generation_requests = [
        request
        for request in requests
        if not request["system"].startswith("# 旁门左道PPT推荐切入点全量")
    ]
    batch_sizes = [
        len(json.loads(request["user"].split("\n\n", 1)[1]))
        for request in generation_requests
    ]
    assert result.status == "success"
    assert batch_sizes == [8, 1]
    assert len(requests) == 3


def test_invalid_topic_angle_is_repaired_as_one_item_instead_of_whole_chunk():
    first = make_item().model_copy(
        update={
            "id": "rss:test:workbuddy-granular",
            "title": "WorkBuddy 自动写入飞书客户资料",
            "profile": "pangmen-topic-radar",
        }
    )
    second = make_item().model_copy(
        update={
            "id": "rss:test:gemini-granular",
            "title": "Gemini 根据 PDF 生成课堂测验",
            "profile": "pangmen-topic-radar",
        }
    )
    for item in (first, second):
        item.processing.classification.profile = "pangmen-topic-radar"
        item.processing.artifacts["zh"] = ContentArtifact(
            language="zh",
            title=item.title,
            blocks=[
                ContentBlock(
                    id="what_happened",
                    type="section",
                    title="发生了什么",
                    content=item.title,
                    primary=True,
                ),
                ContentBlock(
                    id="audience_problem",
                    type="section",
                    title="适合谁、解决什么问题",
                    content="适合需要处理重复资料的职场人和教师。",
                ),
                ContentBlock(
                    id="recommended_angle",
                    type="section",
                    title="推荐切入点",
                    content="用前后对比验证实际收益。",
                ),
            ],
        )

    requests = []

    def valid_angles(item_id):  # type: ignore[no-untyped-def]
        if item_id == first.id:
            return [
                "同一份客户资料，WorkBuddy 自动写入飞书究竟能少做多少步骤？",
                "WorkBuddy 已授权却写不进飞书，最容易卡在哪个权限环节？",
                "销售每天录客户资料时，WorkBuddy 能否直接替代手动复制？",
                "WorkBuddy 写入飞书失败时，哪些文件格式最容易翻车？",
            ]
        return [
            "教师上传同一份 PDF，Gemini 生成的课堂测验能否直接使用？",
            "Gemini 自动出题看似省事，答案准确率会不会让老师返工？",
            "培训师用 Gemini 生成测验后，哪些题型仍然需要手动调整？",
            "同一份课程资料，Gemini 出题能否替代教师逐题录入？",
        ]

    async def complete(**kwargs):
        requests.append(kwargs)
        if kwargs["system"].startswith("# 旁门左道PPT推荐切入点全量"):
            return json.dumps({"removals": []}, ensure_ascii=False)
        item_ids = list(dict.fromkeys(re.findall(r'"item_id": "([^"]+)"', kwargs["user"])))
        if len(requests) == 1:
            return json.dumps(
                {
                    "items": [
                        {
                            "item_id": first.id,
                            "angles": [
                                "WorkBuddy 好用吗？",
                                "用前后对比验证实际收益。",
                                "录屏测试真实效果。",
                                "看看普通人能不能用。",
                            ],
                        },
                        {"item_id": second.id, "angles": valid_angles(second.id)},
                    ]
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "items": [
                    {"item_id": item_id, "angles": valid_angles(item_id)}
                    for item_id in item_ids
                ]
            },
            ensure_ascii=False,
        )

    enricher = ContentEnricher(
        SimpleNamespace(complete=complete),
        PROFILES,
        ["zh"],
        tools=FakeTools(),
    )

    async def keep_existing_artifact(item):  # type: ignore[no-untyped-def]
        return None

    enricher._enrich_item = keep_existing_artifact  # type: ignore[method-assign]
    result = asyncio.run(enricher.enrich_batch([first, second]))

    regeneration_requests = [
        request
        for request in requests[1:]
        if not request["system"].startswith("# 旁门左道PPT推荐切入点全量")
    ]
    regenerated_ids = list(
        dict.fromkeys(re.findall(r'"item_id": "([^"]+)"', regeneration_requests[0]["user"]))
    )
    assert result.status == "success"
    assert regenerated_ids == [first.id]
    assert first.processing.artifacts["zh"].blocks[-1].content.startswith(
        "同一份客户资料"
    )


def test_concrete_nineteen_character_topic_angle_is_valid():
    item = make_item().model_copy(
        update={
            "id": "aihot:test:baidu-avatar",
            "title": "百度搭子一句话生成数字人口播视频",
            "profile": "pangmen-topic-radar",
        }
    )
    item.processing.classification.profile = "pangmen-topic-radar"
    item.processing.artifacts["zh"] = ContentArtifact(
        language="zh",
        title=item.title,
        blocks=[
            ContentBlock(
                id="what_happened",
                type="section",
                title="发生了什么",
                content="百度搭子支持用一句话生成数字人口播视频。",
                primary=True,
            ),
            ContentBlock(
                id="audience_problem",
                type="section",
                title="适合谁、解决什么问题",
                content="适合不想露脸但需要制作口播视频的内容创作者。",
            ),
            ContentBlock(
                id="recommended_angle",
                type="section",
                title="推荐切入点",
                content="不想露脸也能做口播？百度搭子一句话搞定",
            ),
        ],
    )

    ContentEnricher._validate_recommended_angle(
        "不想露脸也能做口播？百度搭子一句话搞定",
        item,
    )


def test_topic_angle_candidates_are_filtered_without_padding_to_four():
    item = make_item().model_copy(
        update={
            "id": "aihot:test:baidu-avatar-filter",
            "title": "百度搭子一句话生成数字人口播视频",
            "profile": "pangmen-topic-radar",
        }
    )
    item.processing.classification.profile = "pangmen-topic-radar"
    item.processing.artifacts["zh"] = ContentArtifact(
        language="zh",
        title=item.title,
        blocks=[
            ContentBlock(
                id="what_happened",
                type="section",
                title="发生了什么",
                content="百度搭子支持用一句话生成数字人口播视频。",
                primary=True,
            ),
            ContentBlock(
                id="audience_problem",
                type="section",
                title="适合谁、解决什么问题",
                content="适合不想露脸但需要制作口播视频的内容创作者。",
            ),
            ContentBlock(
                id="recommended_angle",
                type="section",
                title="推荐切入点",
                content="旧切入点",
            ),
        ],
    )
    candidates = [
        "不想露脸也能做口播？百度搭子一句话搞定",
        "用前后对比验证实际收益。",
        "百度搭子好用吗？",
        "百度搭子生成数字人口播时，长文脚本会不会让口型和停顿翻车？",
        "不想露脸也能做口播，百度搭子一句话就能搞定",
        "百度搭子现在可以把一整篇特别特别长的脚本直接变成数字人口播视频并且还能自动处理所有复杂的镜头语言和后期包装问题",
    ]
    enricher = ContentEnricher(
        SimpleNamespace(complete=None),
        PROFILES,
        ["zh"],
        tools=FakeTools(),
    )
    filter_candidates = getattr(
        enricher, "_filter_recommended_angle_candidates", None
    )

    assert callable(filter_candidates)
    kept, rejected = filter_candidates(item, candidates)

    assert kept == [
        "不想露脸也能做口播？百度搭子一句话搞定",
        "百度搭子生成数字人口播时，长文脚本会不会让口型和停顿翻车？",
    ]
    assert len(rejected) == 4


def test_angle_audit_can_use_a_final_strict_json_retry():
    responses = iter(
        [
            "",
            "没有需要删除的切入点。",
            json.dumps({"removals": []}, ensure_ascii=False),
        ]
    )
    requests = []

    async def complete(**kwargs):
        requests.append(kwargs)
        return next(responses)

    enricher = ContentEnricher(
        SimpleNamespace(complete=complete),
        PROFILES,
        ["zh"],
        tools=FakeTools(),
    )

    audit = asyncio.run(
        enricher._complete_model(
            RecommendedAngleAudit,
            system="audit",
            user="payload",
            error_message="Invalid recommended angle audit",
            max_attempts=3,
            correction_instruction="只返回合法 JSON 对象。",
        )
    )

    assert audit.removals == []
    assert len(requests) == 3
    assert "只返回合法 JSON 对象" in requests[-1]["user"]


def test_specific_complete_angle_under_fourteen_chars_is_allowed():
    item = make_item().model_copy(
        update={
            "id": "aihot:test:baidu-short",
            "title": "百度搭子生成数字人口播",
            "profile": "pangmen-topic-radar",
        }
    )
    item.processing.classification.profile = "pangmen-topic-radar"
    item.processing.artifacts["zh"] = ContentArtifact(
        language="zh",
        title=item.title,
        blocks=[
            ContentBlock(
                id="what_happened",
                type="section",
                title="发生了什么",
                content="百度搭子支持生成数字人口播。",
                primary=True,
            ),
            ContentBlock(
                id="audience_problem",
                type="section",
                title="适合谁、解决什么问题",
                content="适合不想露脸的内容创作者。",
            ),
        ],
    )

    angle = "百度搭子口播会翻车吗？"
    assert len(angle) < 14
    ContentEnricher._validate_recommended_angle(angle, item)


def test_single_topic_regeneration_gets_one_bounded_quality_retry():
    item = make_item().model_copy(
        update={
            "id": "bilibili:video:cat-reminder",
            "title": "用 AI 做一只能打人的猫，专治久坐",
            "profile": "pangmen-topic-radar",
        }
    )
    item.processing.classification.profile = "pangmen-topic-radar"
    item.processing.artifacts["zh"] = ContentArtifact(
        language="zh",
        title=item.title,
        blocks=[
            ContentBlock(
                id="what_happened",
                type="section",
                title="发生了什么",
                content="创作者用 AI 和硬件做出会拍打久坐用户的猫爪装置。",
                primary=True,
            ),
            ContentBlock(
                id="audience_problem",
                type="section",
                title="适合谁、解决什么问题",
                content="适合久坐办公且经常忘记起身的人。",
            ),
            ContentBlock(
                id="recommended_angle",
                type="section",
                title="推荐切入点",
                content="旧切入点",
            ),
        ],
    )
    attempts = 0

    async def request_candidates(items, language, failure_note=""):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return {
                item.id: [
                    "用前后对比验证实际收益。",
                    "录屏测试真实效果。",
                    "看看普通人能不能用。",
                    "提升效率。",
                ]
            }
        assert "上一轮" in failure_note
        return {
            item.id: [
                "久坐提醒总被忽略，会打人的 AI 猫爪为何反而更有效？",
                "办公室久坐人群被猫爪拍一下，能否真正打断忘我工作？",
                "从屏幕弹窗到实体猫爪，AI 久坐提醒改变了什么流程？",
                "AI 猫爪提醒久坐时，误触和打扰同事会不会翻车？",
            ]
        }

    enricher = ContentEnricher(
        SimpleNamespace(complete=None),
        PROFILES,
        ["zh"],
        tools=FakeTools(),
    )
    enricher._request_angle_candidates = request_candidates  # type: ignore[method-assign]
    result = RecommendedAngleReviewResult()

    kept = asyncio.run(
        enricher._regenerate_one_item_angles(
            item,
            "zh",
            result,
            "全量审计删除了全部候选。",
        )
    )

    assert attempts == 2
    assert len(kept) == 4
    assert result.generated_count == 8


def test_global_audit_rechecks_after_a_second_single_topic_regeneration():
    item = make_item().model_copy(
        update={
            "id": "bilibili:video:repeat-audit",
            "title": "AI 猫爪提醒久坐",
            "profile": "pangmen-topic-radar",
        }
    )
    item.processing.classification.profile = "pangmen-topic-radar"
    item.processing.artifacts["zh"] = ContentArtifact(
        language="zh",
        title=item.title,
        blocks=[
            ContentBlock(
                id="recommended_angle",
                type="section",
                title="推荐切入点",
                content="AI 猫爪拍打久坐用户，能否真正打断忘我工作？",
            )
        ],
    )
    enricher = ContentEnricher(
        SimpleNamespace(complete=None),
        PROFILES,
        ["zh"],
        tools=FakeTools(),
    )
    # Repeatedly reporting that all angles were removed must not abort the
    # whole batch; the latest locally validated angles remain usable.
    audit_results = iter([True, True, True])
    audit_calls = 0

    async def keep_generated(items, language, result):
        result.generated_count = 4

    async def audit(items, language, result):
        nonlocal audit_calls
        audit_calls += 1
        return next(audit_results)

    enricher._generate_and_filter_angle_chunk = keep_generated  # type: ignore[method-assign]
    enricher._audit_recommended_angles = audit  # type: ignore[method-assign]

    result = asyncio.run(enricher.review_recommended_angles([item]))

    assert audit_calls == 3
    assert result.final_count == 1


def test_global_angle_audit_isolates_item_that_cannot_be_regenerated():
    item = make_item().model_copy(
        update={
            "id": "bilibili:video:unrepairable-angle",
            "title": "AI 猫爪提醒久坐",
            "profile": "pangmen-topic-radar",
        }
    )
    item.processing.classification.profile = "pangmen-topic-radar"
    item.processing.artifacts["zh"] = ContentArtifact(
        language="zh",
        title=item.title,
        blocks=[
            ContentBlock(
                id="recommended_angle",
                type="section",
                title="推荐切入点",
                content="AI 猫爪拍打久坐用户，能否真正打断忘我工作？",
            )
        ],
    )
    enricher = ContentEnricher(
        SimpleNamespace(complete=None),
        PROFILES,
        ["zh"],
        tools=FakeTools(),
    )

    async def complete_model(*args, **kwargs):
        return RecommendedAngleAudit(
            removals=[
                {
                    "item_id": item.id,
                    "angle": "AI 猫爪拍打久坐用户，能否真正打断忘我工作？",
                    "issue_type": "generic",
                    "reason": "test removal",
                }
            ]
        )

    async def regenerate(*args, **kwargs):
        raise ValueError("no valid replacement")

    enricher._complete_model = complete_model  # type: ignore[method-assign]
    enricher._regenerate_one_item_angles = regenerate  # type: ignore[method-assign]
    result = RecommendedAngleReviewResult()

    asyncio.run(enricher._audit_recommended_angles([item], "zh", result))

    assert result.failed_item_ids == [item.id]
    assert item.processing.artifacts["zh"].blocks[0].content == ""

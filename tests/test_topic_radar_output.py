import asyncio
from datetime import datetime, timezone

from src.ai.summarizer import DailySummarizer
from src.models import (
    ClassificationResult,
    ContentAnalysis,
    ContentArtifact,
    ContentBlock,
    ContentItem,
    ProcessingResult,
    SourceType,
)


def test_topic_radar_webhook_hides_legacy_demo_or_case_block():
    item = ContentItem(
        id="rss:topic-radar-demo",
        source_type=SourceType.RSS,
        title="AI 动画工具更新",
        url="https://example.com/topic-radar-demo",
        published_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
        profile="pangmen-topic-radar",
        processing=ProcessingResult(
            classification=ClassificationResult(
                profile="pangmen-topic-radar", method="source_override"
            ),
            analysis=ContentAnalysis(score=8, reason="test", summary="test"),
            artifacts={
                "zh": ContentArtifact(
                    language="zh",
                    title="AI 动画工具更新",
                    blocks=[
                        ContentBlock(
                            id="recommended_angle",
                            title="推荐切入点",
                            content="AI 动画工具能否替代手工补帧？",
                        ),
                        ContentBlock(
                            id="demo_or_case",
                            title="演示或案例建议",
                            content="录屏展示生成过程。",
                        ),
                    ],
                )
            },
        ),
    )

    result = DailySummarizer().generate_webhook_item(
        item, language="zh", index=1, total=1
    )

    assert "推荐切入点" in result
    assert "演示或案例建议" not in result
    assert "录屏展示生成过程" not in result

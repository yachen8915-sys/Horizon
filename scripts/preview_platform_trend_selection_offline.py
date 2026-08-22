"""Render a deterministic local preview through the production trend gate.

The fixture is intentionally local: no source fetch, real AI call, webhook,
deployment, or state commit happens in this script.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import html
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from src.models import (
    ClassificationResult,
    ContentAnalysis,
    ContentItem,
    PlatformTrendsConfig,
    ProcessingResult,
    SourceType,
)
from src.orchestrator import HorizonOrchestrator


TREND_PROFILE = "pangmen-platform-trend-radar"


@dataclass
class OfflinePreviewResult:
    main: list[ContentItem]
    overflow: list[ContentItem]
    rejected: list[dict[str, object]]


def _fixture_item(
    item_id: str,
    title: str,
    *,
    rank: int,
    hot_value: int | None,
    platforms: tuple[str, ...],
    operations: float,
    opportunity: float,
    evidence: float,
    angles: tuple[str, ...],
    profile: str = TREND_PROFILE,
) -> ContentItem:
    occurrences = [
        {
            "platform": platform,
            "rank": rank,
            "rank_limit": 30,
            "hot_value": hot_value,
        }
        for platform in platforms
    ]
    return ContentItem(
        id=f"offline:{item_id}",
        source_type=SourceType.PLATFORM_TRENDS,
        title=title,
        url=f"https://example.com/offline/{item_id}",
        content="离线固定样本，仅用于验证筛选与展示结构。",
        published_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
        profile=profile,
        metadata={
            "platform": platforms[0],
            "platforms": list(platforms),
            "rank": rank,
            "rank_limit": 30,
            "hot_value": hot_value,
            "cross_platform_count": len(platforms),
            "platform_occurrences": occurrences,
        },
        processing=ProcessingResult(
            classification=ClassificationResult(
                profile=profile,
                method="source_override",
            ),
            analysis=ContentAnalysis(
                score=operations,
                operations_score=operations,
                content_opportunity_score=opportunity,
                evidence_quality_score=evidence,
                extension_angles=list(angles),
                extension_reason="离线固定分析结果，用于验证正式准入规则。",
                reason="离线固定分析结果",
                summary=title,
            ),
        ),
    )


def build_fixture_items() -> list[ContentItem]:
    """Return a fixed mix of passes, rejections, and AI-section routing."""
    return [
        _fixture_item(
            "concert-consumption",
            "明星演唱会带动城市夜间消费",
            rank=1,
            hot_value=100,
            platforms=("weibo", "douyin"),
            operations=5,
            opportunity=4,
            evidence=5,
            angles=("城市消费", "演出经济", "品牌联动"),
        ),
        _fixture_item(
            "marathon-tourism",
            "全国马拉松带动城市文旅消费",
            rank=2,
            hot_value=100,
            platforms=("weibo", "douyin", "zhihu"),
            operations=8,
            opportunity=8,
            evidence=7,
            angles=("赛事传播", "城市品牌", "大众参与"),
        ),
        _fixture_item(
            "variety-brand",
            "热门综艺引发品牌联名消费潮",
            rank=3,
            hot_value=100,
            platforms=("weibo", "douyin", "zhihu"),
            operations=8,
            opportunity=7,
            evidence=6,
            angles=("联名营销", "粉丝消费", "内容传播"),
        ),
        _fixture_item(
            "sports-brand",
            "体育冠军商业代言推动国货品牌讨论",
            rank=2,
            hot_value=100,
            platforms=("weibo", "douyin", "zhihu"),
            operations=8,
            opportunity=7,
            evidence=6,
            angles=("体育营销", "品牌心智", "大众情绪"),
        ),
        _fixture_item(
            "celebrity-rumor",
            "某明星恋情爆料",
            rank=1,
            hot_value=100,
            platforms=("weibo", "douyin"),
            operations=3,
            opportunity=2,
            evidence=2,
            angles=(),
        ),
        _fixture_item(
            "commercial-space",
            "火箭翻新推动商业航天成本下降",
            rank=14,
            hot_value=None,
            platforms=("toutiao",),
            operations=8,
            opportunity=8,
            evidence=7,
            angles=("产业成本", "商业模式"),
        ),
        _fixture_item(
            "deepseek-release",
            "DeepSeek 新模型发布",
            rank=1,
            hot_value=100,
            platforms=("weibo", "douyin"),
            operations=9,
            opportunity=9,
            evidence=8,
            angles=("产品更新",),
            profile="pangmen-ai-tech-radar",
        ),
    ]


def _orchestrator(temp_dir: Path) -> HorizonOrchestrator:
    orchestrator = HorizonOrchestrator.__new__(HorizonOrchestrator)
    orchestrator.config = SimpleNamespace(
        sources=SimpleNamespace(
            platform_trends=PlatformTrendsConfig(
                enabled=True,
                state_file=str(temp_dir / "platform-trend-state.json"),
            )
        ),
        processing=SimpleNamespace(
            profile_settings={
                TREND_PROFILE: SimpleNamespace(threshold=7.0),
            }
        ),
    )
    return orchestrator


def evaluate_preview_items(
    items: list[ContentItem],
    *,
    main_limit: int,
    temp_dir: Path,
) -> OfflinePreviewResult:
    """Use the production heat scorer, gate, and ordering without committing state."""
    orchestrator = _orchestrator(temp_dir)
    trend_items = [
        item
        for item in items
        if item.processing
        and item.processing.classification.profile == TREND_PROFILE
    ]
    orchestrator._prepare_platform_trend_selection(trend_items)

    eligible: list[ContentItem] = []
    rejected: list[dict[str, object]] = []
    for item in items:
        profile = (
            item.processing.classification.profile
            if item.processing
            else None
        )
        if profile != TREND_PROFILE:
            rejected.append(
                {
                    "id": item.id,
                    "title": item.title,
                    "reason": "pure_ai_topic_routed_to_ai_section",
                }
            )
            continue
        if orchestrator.passes_profile_filter(item):
            eligible.append(item)
            continue
        rejected.append(
            {
                "id": item.id,
                "title": item.title,
                "reason": item.metadata.get("trend_eligibility_reason"),
            }
        )

    eligible.sort(key=orchestrator._selection_sort_key, reverse=True)
    return OfflinePreviewResult(
        main=eligible[:main_limit],
        overflow=eligible[main_limit:],
        rejected=rejected,
    )


def _escape(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _render_item(item: ContentItem, index: int) -> str:
    analysis = item.processing.analysis
    metadata = item.metadata
    angles = "、".join(analysis.extension_angles) if analysis else ""
    return (
        '<details class="item"><summary>'
        f'<span class="index">{index}.</span> {_escape(item.title)}'
        f'<span class="score">综合 {_escape(metadata.get("trend_final_score"))}</span>'
        '</summary><div class="detail">'
        f'<p><b>热度：</b>{_escape(metadata.get("heat_score"))}　'
        f'<b>延展：</b>{_escape(analysis.extension_score if analysis else None)}　'
        f'<b>证据：</b>{_escape(analysis.evidence_quality_score if analysis else None)}　'
        f'<b>趋势：</b>{_escape(metadata.get("trend_type"))}</p>'
        f'<p><b>准入：</b>{_escape(metadata.get("trend_eligibility_reason"))}</p>'
        f'<p><b>可延展方向：</b>{_escape(angles)}</p>'
        '</div></details>'
    )


def _render_html(result: OfflinePreviewResult) -> str:
    main_body = "".join(
        _render_item(item, index)
        for index, item in enumerate(result.main, 1)
    )
    overflow_body = ""
    if result.overflow:
        overflow_body = (
            f'<details class="more"><summary>查看更多资讯（{len(result.overflow)} 条）</summary>'
            + "".join(
                _render_item(item, index)
                for index, item in enumerate(
                    result.overflow,
                    len(result.main) + 1,
                )
            )
            + "</details>"
        )
    css = """
    :root{--bg:#f5f7fb;--card:#fff;--blue:#2563eb;--text:#1f2937;--muted:#6b7280;--line:#dbe2ea;--orange:#d97706}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);font-family:"Segoe UI","Microsoft YaHei",sans-serif;color:var(--text)}
    .wrap{max-width:1000px;margin:32px auto;padding:0 20px}.notice{background:#fff7ed;border:1px solid #fed7aa;color:#9a3412;padding:12px 16px;border-radius:10px;margin-bottom:18px;line-height:1.6}
    .card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:24px;box-shadow:0 8px 30px #1e3a8a0d}h1{margin:0 0 8px;color:#1d4ed8}.muted{color:var(--muted);font-size:14px}
    .item,.more{border:1px solid var(--line);border-radius:9px;margin:8px 0;background:#fff}summary{cursor:pointer;padding:13px 14px;font-weight:600}.detail{padding:0 14px 14px;line-height:1.65}.score{float:right;color:var(--orange);font-size:13px}.index{color:var(--muted);font-weight:400}
    """
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>平台热点真实规则离线回放</title>'
        f'<style>{css}</style></head><body><main class="wrap">'
        '<div class="notice">固定样本离线回放：直接调用当前生产热度计算、双档准入和综合排序代码。未联网、未调用真实 AI、未发送飞书、未部署、未写生产 state。</div>'
        '<article class="card"><h1>今日运营热点 · 真实规则预览</h1>'
        f'<p class="muted">合格 {len(result.main) + len(result.overflow)} 条　|　主区 {len(result.main)} 条　|　查看更多 {len(result.overflow)} 条。未通过内容不会出现在前端。</p>'
        f'{main_body}{overflow_body}'
        '</article></main></body></html>'
    )


def build_offline_preview(
    output: Path,
    *,
    main_limit: int = 3,
    temp_dir: Path | None = None,
) -> OfflinePreviewResult:
    output.parent.mkdir(parents=True, exist_ok=True)
    if temp_dir is not None:
        result = evaluate_preview_items(
            build_fixture_items(),
            main_limit=main_limit,
            temp_dir=temp_dir,
        )
    else:
        with TemporaryDirectory(prefix="horizon-trend-preview-") as directory:
            result = evaluate_preview_items(
                build_fixture_items(),
                main_limit=main_limit,
                temp_dir=Path(directory),
            )
    output.write_text(_render_html(result), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an offline platform-trend preview through production rules"
    )
    parser.add_argument(
        "--output",
        default="data/previews/platform-trend-selection-offline-2026-08-22.html",
    )
    parser.add_argument("--main-limit", type=int, default=3)
    args = parser.parse_args()
    result = build_offline_preview(Path(args.output), main_limit=args.main_limit)
    print(
        f"offline preview: main={len(result.main)} "
        f"overflow={len(result.overflow)} rejected={len(result.rejected)}"
    )


if __name__ == "__main__":
    main()

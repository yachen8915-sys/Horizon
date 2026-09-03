"""Deterministic decision-oriented digest rendering for the redesigned radar."""

from __future__ import annotations

from collections import defaultdict

from ..models import CandidateRecord, DecisionLane, RadarRunMode


LANE_LABELS = {
    DecisionLane.PRODUCT_CAPABILITY: "产品与能力判断",
    DecisionLane.HOT_CONTENT: "热门内容与选题机会",
    DecisionLane.TECHNICAL_FRONTIER: "技术前沿判断",
    DecisionLane.PLATFORM_AI_CHANGE: "平台与 AI 生态变化",
}

EVIDENCE_LABELS = {
    "confirmed": "已确认",
    "corroborated": "多源佐证",
    "reported": "单源报道",
    "unverified": "待核实",
    "disputed": "存在冲突",
}


def render_intelligence_brief(
    candidates: list[CandidateRecord],
    *,
    more_candidates: list[CandidateRecord] | None = None,
    date: str,
    run_mode: RadarRunMode,
    total_fetched: int,
) -> str:
    mode_label = (
        "上午全量"
        if run_mode is RadarRunMode.MORNING
        else "下午增量"
        if run_mode is RadarRunMode.AFTERNOON
        else "影子预览"
    )
    lines = [
        f"# Horizon 情报雷达｜{date}｜{mode_label}",
        "",
        f"精选 {len(candidates)} 条 / 抓取 {total_fetched} 条",
    ]
    grouped: dict[DecisionLane, list[CandidateRecord]] = defaultdict(list)
    for candidate in candidates:
        if candidate.intelligence is not None:
            grouped[candidate.intelligence.primary_lane].append(candidate)

    for lane in DecisionLane:
        lane_candidates = grouped.get(lane, [])
        if not lane_candidates:
            continue
        lines.extend(["", f"## {LANE_LABELS[lane]}", ""])
        for candidate in lane_candidates:
            analysis = candidate.intelligence
            if analysis is None:
                continue
            title = _escape_link_text(candidate.item.title)
            evidence = EVIDENCE_LABELS[candidate.evidence_status.value]
            lines.extend(
                [
                    f"### [{title}]({candidate.item.url})",
                    "",
                    f"**为什么值得看：** {analysis.decision_summary}",
                    "",
                    analysis.content_summary,
                    "",
                    f"证据：{evidence}｜评分：{analysis.score.total:.1f}",
                    "",
                ]
            )
    if more_candidates:
        lines.extend(["", f"## 查看更多（{len(more_candidates)} 条）", ""])
        for candidate in more_candidates:
            title = _escape_link_text(candidate.item.title)
            evidence = EVIDENCE_LABELS[candidate.evidence_status.value]
            lines.append(
                f"- [{title}]({candidate.item.url})｜{evidence}"
            )
    return "\n".join(lines).rstrip() + "\n"


def _escape_link_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")

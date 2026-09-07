"""Deterministic decision-oriented digest rendering for the redesigned radar."""

from __future__ import annotations

from ..models import CandidateRecord, RadarRunMode
from .intelligence_presentation import build_intelligence_presentation

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
    presentation = build_intelligence_presentation(candidates, more_candidates or [])
    for heading, section_candidates in presentation.sections():
        lines.extend(["", heading, ""])
        compact = (
            section_candidates is presentation.more_ai
            or section_candidates is presentation.more_hot
        )
        for candidate in section_candidates:
            title = _escape_link_text(candidate.item.title)
            evidence = EVIDENCE_LABELS[candidate.evidence_status.value]
            if compact:
                lines.append(f"- [{title}]({candidate.item.url})｜{evidence}")
                continue
            analysis = candidate.intelligence
            if analysis is None:
                continue
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
    return "\n".join(lines).rstrip() + "\n"


def _escape_link_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")

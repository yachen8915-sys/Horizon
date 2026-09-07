"""Safe, user-facing hotspot coverage derived from normalized fetch health."""

from __future__ import annotations

from typing import Any


_PLATFORM_LABELS = {
    "weibo": "微博", "douyin": "抖音", "xiaohongshu": "小红书",
    "wechat": "微信", "toutiao": "今日头条", "zhihu": "知乎",
    "baidu": "百度", "36kr": "36氪",
}
_HEALTHY = {"healthy", "success", "empty"}
_FAILED = {"failed", "failure"}


def build_platform_coverage_notice(fetch_report: dict[str, Any] | None) -> str | None:
    """Read FetchReport.to_dict(); never copy errors, details or arbitrary IDs."""
    platforms: dict[str, set[str]] = {}
    for source in (fetch_report or {}).get("sources") or []:
        if not isinstance(source, dict) or source.get("source") != "Platform Trends":
            continue
        rows = [
            row
            for key in ("providers", "source_health", "health")
            for row in (source.get(key) or [])
            if isinstance(row, dict)
        ]
        for row in rows or [source]:
            status = str(row.get("status") or "unknown")
            if status in {"disabled", "not_attempted"}:
                continue
            source_id = str(row.get("source_id") or "")
            parts = source_id.split(":")
            platform = parts[1] if len(parts) >= 3 and parts[0] == "platform-trends" else source_id
            label = _PLATFORM_LABELS.get(platform, "其他热点")
            platforms.setdefault(label, set()).add(status)

    if not platforms or all(statuses <= _HEALTHY for statuses in platforms.values()):
        return None
    if all(statuses <= _FAILED for statuses in platforms.values()):
        return "热点覆盖暂时不可用：当前热点来源均未能正常获取数据。"

    unavailable, partial, incomplete, healthy = [], [], [], []
    for label, statuses in platforms.items():
        if statuses & _FAILED:
            (partial if statuses & _HEALTHY else unavailable).append(label)
        elif statuses - _HEALTHY:
            incomplete.append(label)
        else:
            healthy.append(label)

    clauses = []
    if unavailable:
        clauses.append(f"{'、'.join(unavailable)}来源暂时不可用")
    if partial:
        clauses.append(f"{'、'.join(partial)}部分来源暂时不可用")
    if incomplete:
        clauses.append(f"{'、'.join(incomplete)}来源数据暂不完整")
    if healthy:
        remaining = f"{healthy[0]}及其他热点来源" if len(healthy) > 1 else f"{healthy[0]}来源"
        clauses.append(f"{remaining}仍正常")
    elif any(statuses & _HEALTHY for statuses in platforms.values()):
        clauses.append("仍有其他来源正常")
    return f"热点覆盖不完整：{'，'.join(clauses)}。"

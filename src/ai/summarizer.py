"""Daily summary generation — pure programmatic rendering."""

import html
import re
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import quote, urlsplit

from .localization import normalize_language
from ..models import ContentItem


_CJK = r"[\u4e00-\u9fff\u3400-\u4dbf]"
_ASCII = r"[A-Za-z0-9]"
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()<>#!|])")
_MARKDOWN_BLOCK_START = re.compile(r"(?m)^( {0,3})(>|[-+] |\d+[.)] )")
_URL_SAFE_CHARS = ":/?#[]@!$&'*,;=~%+"
TOPIC_RADAR_PROFILE_ID = "pangmen-topic-radar"
TOPIC_RADAR_HIDDEN_BLOCK_IDS = frozenset(
    {"content_format", "priority_reason", "verification"}
)


def _escape_markdown(value: object) -> str:
    """Render untrusted text literally while retaining its readable content."""
    escaped = html.escape(str(value), quote=True)
    escaped = _MARKDOWN_SPECIAL.sub(r"\\\1", escaped)
    return _MARKDOWN_BLOCK_START.sub(r"\1\\\2", escaped)


def _safe_url(value: object) -> Optional[str]:
    """Return an HTML/Markdown-safe HTTP(S) URL, or None for unsafe URLs."""
    raw = str(value).strip()
    if not raw or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        return None
    try:
        parsed = urlsplit(raw)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return None
        parsed.port
    except (TypeError, ValueError):
        return None
    encoded = quote(raw, safe=_URL_SAFE_CHARS)
    return html.escape(encoded, quote=True)


def _pangu(text: str) -> str:
    """Insert a space between CJK and ASCII letters/digits (Pangu spacing)."""
    text = re.sub(rf"({_CJK})({_ASCII})", r"\1 \2", text)
    text = re.sub(rf"({_ASCII})({_CJK})", r"\1 \2", text)
    return text


LABELS = {
    "en": {
        "header": "Horizon Daily",
        "source": "Source",
        "background": "Background",
        "discussion": "Discussion",
        "references": "References",
        "tags": "Tags",
        "selected_items": "From {total} items, {selected} important content pieces were selected",
        "empty_analyzed": "Analyzed {total} items, but none met the importance threshold.",
        "empty_body": (
            "No significant developments today. This might indicate:\n"
            "- A quiet day in your tracked sources\n"
            "- The AI score threshold is too high\n"
            "- Your information sources need expansion\n\n"
            "Consider:\n"
            "1. Lowering the configured profile threshold\n"
            "2. Adding more diverse information sources\n"
            "3. Checking if the AI model is working correctly\n"
        ),
    },
    "zh": {
        "header": "Horizon 每日速递",
        "source": "来源",
        "background": "背景",
        "discussion": "社区讨论",
        "references": "参考链接",
        "tags": "标签",
        "selected_items": "从 {total} 条内容中筛选出 {selected} 条重要资讯。",
        "empty_analyzed": "已分析 {total} 条内容，但没有达到重要性阈值的条目。",
        "empty_body": (
            "今日暂无重要动态，可能原因：\n"
            "- 今天关注的信息源较平静\n"
            "- AI 评分阈值设置过高\n"
            "- 信息源种类有待扩充\n\n"
            "建议：\n"
            "1. 降低当前 Profile 的过滤阈值\n"
            "2. 添加更多多样化的信息源\n"
            "3. 检查 AI 模型是否正常工作\n"
        ),
    },
}


@dataclass(frozen=True)
class SummaryItemView:
    item: ContentItem
    index: int
    global_index: int
    group_count: int
    title: str
    score: float | str
    anchor_id: str


@dataclass(frozen=True)
class SummaryGroupView:
    profile_id: str
    name: str
    items: List[SummaryItemView]


@dataclass(frozen=True)
class DailySummaryView:
    groups: List[SummaryGroupView]
    item_count: int


class DailySummarizer:
    """Generates daily Markdown summaries from pre-analyzed content items."""

    def __init__(
        self,
        profile_names: Optional[Dict[str, Dict[str, str]]] = None,
        profile_order: Optional[List[str]] = None,
    ):
        self.profile_names = profile_names or {}
        self.profile_order = profile_order or []

    @staticmethod
    def _profile_id(item: ContentItem) -> str:
        if item.processing:
            return item.processing.classification.profile
        return item.profile if isinstance(item.profile, str) else "unclassified"

    def profile_name(self, profile_id: str, language: str) -> str:
        names = self.profile_names.get(profile_id, {})
        return names.get(
            language,
            names.get(
                "default",
                profile_id.replace("-", " ").replace("_", " ").title(),
            ),
        )

    def build_view(
        self,
        items: List[ContentItem],
        language: str,
    ) -> DailySummaryView:
        grouped_items: Dict[str, List[ContentItem]] = {}
        for item in items:
            grouped_items.setdefault(self._profile_id(item), []).append(item)

        ordered_groups = list(grouped_items.items())
        if self.profile_order:
            order = {
                profile_id: index
                for index, profile_id in enumerate(self.profile_order)
            }
            ordered_groups = sorted(
                ordered_groups,
                key=lambda group: order.get(group[0], len(order)),
            )

        groups = []
        global_index = 1
        for profile_id, profile_items in ordered_groups:
            view_items = []
            for index, item in enumerate(profile_items, start=1):
                artifact = (
                    item.processing.artifacts.get(language)
                    if item.processing
                    else None
                )
                analysis = item.processing.analysis if item.processing else None
                view_items.append(
                    SummaryItemView(
                        item=item,
                        index=index,
                        global_index=global_index,
                        group_count=len(profile_items),
                        title=normalize_language(
                            artifact.title if artifact else item.title, language
                        ),
                        score=(
                            analysis.score
                            if analysis and analysis.score is not None
                            else "?"
                        ),
                        anchor_id=self._item_anchor(profile_id, index),
                    )
                )
                global_index += 1
            groups.append(
                SummaryGroupView(
                    profile_id=profile_id,
                    name=normalize_language(
                        self.profile_name(profile_id, language), language
                    ),
                    items=view_items,
                )
            )
        return DailySummaryView(groups=groups, item_count=len(items))

    @staticmethod
    def _item_anchor(profile_id: str, index: int) -> str:
        safe_profile_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", profile_id).strip("-")
        return f"item-{safe_profile_id or 'unclassified'}-{index}"

    async def generate_summary(
        self,
        items: List[ContentItem],
        date: str,
        total_fetched: int,
        language: str = "en",
    ) -> str:
        """Generate daily summary in Markdown format.

        Items are rendered in score-descending order (already sorted by orchestrator).

        Args:
            items: High-scoring content items (already enriched)
            date: Date string (YYYY-MM-DD)
            total_fetched: Total number of items fetched before filtering
            language: Output language, either "en" or "zh"

        Returns:
            str: Markdown formatted summary
        """
        labels = LABELS.get(language, LABELS["en"])

        if not items:
            return self._generate_empty_summary(date, total_fetched, labels)

        view = self.build_view(items, language)
        topic_card_mode = (
            language == "zh"
            and len(view.groups) == 1
            and view.groups[0].profile_id == TOPIC_RADAR_PROFILE_ID
        )
        if topic_card_mode:
            header = (
                f"# 旁门左道PPT · 新媒体选题雷达 - {date}\n\n"
                f"> 从 {total_fetched} 条资讯中筛选出 {len(items)} 个可做视频的选题。\n\n"
                "---\n\n"
            )
            toc_entries = ["## 今日选题速览"]
            body_sections = ["## 选题卡\n\n"]
            for view_item in view.groups[0].items:
                title = _pangu(_escape_markdown(view_item.title))
                toc_entries.append(
                    f"{view_item.index}. [{title}](#{view_item.anchor_id})"
                )
                if view_item.index == 1:
                    body_sections.append("## 今日优先验证\n\n")
                elif view_item.index == 6:
                    body_sections.append("## 候选选题池\n\n")
                elif view_item.index == 13:
                    body_sections.append("## 趋势观察\n\n")
                elif view_item.index == 18:
                    body_sections.append("## 暂不采用\n\n")
                body_sections.append(
                    self._format_item(
                        view_item.item,
                        labels,
                        language,
                        view_item.index,
                        heading_level=3,
                        anchor_id=view_item.anchor_id,
                        title_override=view_item.title,
                        score_override=view_item.score,
                        topic_card=True,
                    )
                )
            return normalize_language(
                header + "\n".join(toc_entries) + "\n\n---\n\n" + "".join(body_sections),
                language,
            )

        header = (
            f"# {labels['header']} - {date}\n\n"
            f"> {labels['selected_items'].format(total=total_fetched, selected=len(items))}\n\n"
            "---\n\n"
        )

        toc_sections = []
        body_sections = []
        for group in view.groups:
            profile_name = _escape_markdown(group.name)
            if language == "zh":
                profile_name = _pangu(profile_name)
            toc_entries = [f"**{profile_name}**"]
            for view_item in group.items:
                title = _escape_markdown(view_item.title)
                if language == "zh":
                    title = _pangu(title)
                toc_entries.append(
                    f"{view_item.index}. [{title}](#{view_item.anchor_id}) "
                    f"\u2b50\ufe0f {view_item.score}/10"
                )
            toc_sections.append("\n".join(toc_entries))
            body_sections.append(f"## {profile_name}\n\n")
            body_sections.extend(
                self._format_item(
                    view_item.item,
                    labels,
                    language,
                    view_item.index,
                    heading_level=3,
                    anchor_id=view_item.anchor_id,
                    title_override=view_item.title,
                    score_override=view_item.score,
                )
                for view_item in group.items
            )

        toc = "\n\n".join(toc_sections) + "\n\n---\n\n"
        return normalize_language(header + toc + "".join(body_sections), language)

    def generate_webhook_overview(
        self,
        items: List[ContentItem],
        date: str,
        total_fetched: int,
        language: str = "en",
    ) -> str:
        """Generate a compact overview for multi-message webhook delivery."""
        labels = LABELS.get(language, LABELS["en"])
        if not items:
            return self._generate_empty_summary(date, total_fetched, labels)

        if language == "zh":
            header = (
                f"# {labels['header']} - {date}\n\n"
                f"> 从 {total_fetched} 条内容中筛选出 {len(items)} 条重要资讯。\n\n"
                "下面会按内容逐条发送详情，你可以只看感兴趣的标题。\n\n"
            )
        else:
            header = (
                f"# {labels['header']} - {date}\n\n"
                f"> Selected {len(items)} important items from {total_fetched} fetched items.\n\n"
                "Details will be sent item by item so you can read only the topics you care about.\n\n"
            )

        sections = []
        view = self.build_view(items, language)
        for group in view.groups:
            profile_name = _escape_markdown(group.name)
            if language == "zh":
                profile_name = _pangu(profile_name)
            entries = [f"**{profile_name}**"]
            for view_item in group.items:
                title = _escape_markdown(view_item.title)
                if language == "zh":
                    title = _pangu(title)
                url = _safe_url(view_item.item.url)
                title_link = f"[{title}]({url})" if url else title
                entries.append(
                    f"{view_item.index}. {title_link} "
                    f"\u2b50\ufe0f {view_item.score}/10"
                )
            sections.append("\n".join(entries))

        return normalize_language(header + "\n\n".join(sections), language)

    def generate_webhook_item(
        self,
        item: ContentItem,
        language: str,
        index: int,
        total: int,
        *,
        title: Optional[str] = None,
        score: float | str | None = None,
    ) -> str:
        """Generate one item message for multi-message webhook delivery."""
        labels = LABELS.get(language, LABELS["en"])
        prefix = f"第 {index}/{total} 条\n\n" if language == "zh" else f"Item {index}/{total}\n\n"
        return normalize_language(
            prefix
            + self._format_item(
                item,
                labels,
                language,
                index,
                title_override=title,
                score_override=score,
            ).rstrip("-\n "),
            language,
        )

    def _format_item(
        self,
        item: ContentItem,
        labels: dict,
        language: str,
        index: int,
        *,
        heading_level: int = 2,
        anchor_id: Optional[str] = None,
        title_override: Optional[str] = None,
        score_override: float | str | None = None,
        topic_card: bool = False,
    ) -> str:
        """Format a single ContentItem into Markdown."""
        if topic_card and language == "zh":
            return self._format_compact_topic_card(item, index, anchor_id, title_override)
        artifact = item.processing.artifacts.get(language) if item.processing else None
        analysis = item.processing.analysis if item.processing else None
        is_topic_radar = (
            item.profile == TOPIC_RADAR_PROFILE_ID
            or (
                item.processing is not None
                and item.processing.classification.profile == TOPIC_RADAR_PROFILE_ID
            )
        )
        _title = title_override or (artifact.title if artifact else item.title)
        title = _escape_markdown(_title)
        raw_url = str(item.url)
        url = _safe_url(raw_url)
        score = (
            score_override
            if score_override is not None
            else analysis.score
            if analysis and analysis.score is not None
            else "?"
        )
        meta = item.metadata

        summary = analysis.summary if not artifact and analysis else ""
        primary_block = (
            next((block for block in artifact.blocks if block.primary), None)
            if artifact
            else None
        )

        summary = _escape_markdown(summary)
        primary_content = (
            _escape_markdown(primary_block.content) if primary_block else ""
        )

        if language == "zh":
            title = _pangu(title)
            summary = _pangu(summary)
            primary_content = _pangu(primary_content)

        # Source line with parts joined by " · ", link appended at end
        source_type = item.source_type.value
        source_parts = [_escape_markdown(source_type)]
        if meta.get("subreddit"):
            source_parts.append(_escape_markdown(f"r/{meta['subreddit']}"))
        if meta.get("feed_name"):
            source_parts.append(_escape_markdown(meta["feed_name"]))
        else:
            source_parts.append(_escape_markdown(item.author or "unknown"))
        if item.published_at:
            if language == "zh":
                source_parts.append(
                    f"{item.published_at.month}月{item.published_at.day}日 "
                    f"{item.published_at:%H:%M}"
                )
            else:
                day = item.published_at.strftime("%d").lstrip("0")
                source_parts.append(item.published_at.strftime(f"%b {day}, %H:%M"))
        source_line = " \u00b7 ".join(source_parts)  # ·

        discussion_url = meta.get("discussion_url")
        if discussion_url:
            safe_discussion_url = _safe_url(discussion_url)
            if safe_discussion_url and str(discussion_url) != raw_url:
                source_line += f' · [{labels["discussion"]}]({safe_discussion_url})'

        title_link = f"[{title}]({url})" if url else title

        lines = [
            f'<a id="{anchor_id or f"item-{index}"}"></a>',
            f"{'#' * heading_level} {title if topic_card else title_link} \u2b50\ufe0f {score}/10",  # ⭐️
        ]
        if topic_card:
            raw_title = _escape_markdown(item.title)
            if language == "zh":
                raw_title = _pangu(raw_title)
            original = f"[{raw_title}]({url})" if url else raw_title
            lines.extend(["", f"**原始资讯**：{original}"])
        if summary.strip():
            lines.extend(["", summary])
        if primary_content.strip():
            if topic_card and primary_block:
                primary_title = _escape_markdown(primary_block.title)
                if language == "zh":
                    primary_title = _pangu(primary_title)
                lines.extend(["", f"**「{primary_title}」** {primary_content}"])
            else:
                lines.extend(["", primary_content])
        if topic_card:
            lines.extend(["", f"**来源与发布时间**：{source_line}"])
        else:
            lines.extend(["", source_line])

        if artifact:
            for block in artifact.blocks:
                if block.primary:
                    continue
                if is_topic_radar and block.id in TOPIC_RADAR_HIDDEN_BLOCK_IDS:
                    continue
                block_title = _escape_markdown(block.title)
                block_content = _escape_markdown(block.content)
                if language == "zh":
                    block_title = _pangu(block_title)
                    block_content = _pangu(block_content)
                lines.extend(["", f"**「{block_title}」** {block_content}"])

        sources = artifact.sources if artifact else []
        if sources:
            reference_items = []
            for source in sources:
                reference_title = html.escape(source.title, quote=True)
                reference_url = _safe_url(source.url)
                if reference_url:
                    reference_items.append(f'<li><a href="{reference_url}">{reference_title}</a></li>\n')
                else:
                    reference_items.append(f"<li>{reference_title}</li>\n")
            items_html = "".join(reference_items)
            lines += [
                "",
                f'<details><summary>{labels["references"]}</summary>\n<ul>\n{items_html}\n</ul>\n</details>',
            ]

        if analysis and analysis.tags:
            tags_str = ", ".join([f"`#{_escape_markdown(t)}`" for t in analysis.tags])
            lines.append("")
            lines.append(f"**{labels['tags']}**: {tags_str}")

        lines.append("")
        lines.append("---")

        return "\n".join(lines) + "\n\n"

    def _format_compact_topic_card(
        self, item: ContentItem, index: int, anchor_id: Optional[str], title_override: Optional[str]
    ) -> str:
        """Render the short Pangmen card; ranking details stay outside the card."""
        artifact = item.processing.artifacts.get("zh") if item.processing else None
        blocks = {block.id: block for block in (artifact.blocks if artifact else [])}
        def clean(value: object, limit: int = 170) -> str:
            text = re.sub(r"\s+", " ", str(value or "")).strip()
            return text if len(text) <= limit else text[:limit].rstrip("，。；、 ") + "……"
        title = _pangu(_escape_markdown(title_override or (artifact.title if artifact else item.title)))
        original_title = _escape_markdown(item.title)
        original_url = _safe_url(item.metadata.get("original_url") or item.url)
        aihot_url = _safe_url(item.metadata.get("aihot_url"))
        source_name = item.metadata.get("feed_name") or item.author or "未知来源"
        source_type = item.metadata.get("source_kind") or item.source_type.value
        when = f"{item.published_at:%Y-%m-%d %H:%M UTC}" if item.published_at else "时间未知"
        happened = clean(blocks.get("what_happened").content if blocks.get("what_happened") else item.content, 190)
        audience = clean(blocks.get("audience_problem").content if blocks.get("audience_problem") else "适用人群和具体痛点待补充", 90)
        angle_text = blocks.get("recommended_angle").content if blocks.get("recommended_angle") else ""
        angles = []
        for raw_angle in re.split(r"\r?\n+|[；;]+", str(angle_text)):
            angle = re.sub(r"^\s*(?:[-*•]|\d+[.)、])\s*", "", raw_angle).strip()
            if angle:
                angles.append(clean(angle, 70))
        angles = angles[:4]
        tags = (item.processing.analysis.tags if item.processing and item.processing.analysis else [])[:5]
        source_link = f"[{original_title}]({original_url})" if original_url else original_title
        lines = [f'<a id="{anchor_id or f"item-{index}"}"></a>', f"### {title}", "", f"**原始资讯**：{source_name} · {source_type} · {when} · {source_link}"]
        if aihot_url:
            lines[-1] += f" · [AI HOT]({aihot_url})"
        if item.metadata.get("engagement"):
            metrics = item.metadata["engagement"]
            shown = "、".join(f"{k}{v}" for k, v in metrics.items() if v is not None)
            if shown:
                lines.append(f"互动数据（实测）：{shown}")
        lines += ["", f"**发生了什么**：{happened}", "", f"**适合谁、解决什么问题**：{audience}", "", "**推荐切入点**："]
        lines.extend(f"- {angle}" for angle in angles)
        if tags:
            lines += ["", "**标签**：" + " ".join(f"#{_escape_markdown(tag)}" for tag in tags)]
        lines += ["", "---", ""]
        return "\n".join(lines)

    def _generate_empty_summary(self, date: str, total_fetched: int, labels: dict) -> str:
        """Generate summary when no high-scoring items were found."""
        return (
            f"# {labels['header']} - {date}\n\n"
            f"> {labels['empty_analyzed'].format(total=total_fetched)}\n\n"
            + labels["empty_body"]
        )

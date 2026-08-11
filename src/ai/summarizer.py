"""Daily summary generation — pure programmatic rendering."""

import html
import re
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from .localization import normalize_language
from ..models import ContentItem


_CJK = r"[\u4e00-\u9fff\u3400-\u4dbf]"
_ASCII = r"[A-Za-z0-9]"
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()<>#!|])")
_MARKDOWN_BLOCK_START = re.compile(r"(?m)^( {0,3})(>|[-+] |\d+[.)] )")
_URL_SAFE_CHARS = ":/?#[]@!$&'*,;=~%+"
TOPIC_RADAR_PROFILE_ID = "pangmen-topic-radar"
AI_TECH_RADAR_PROFILE_ID = "pangmen-ai-tech-radar"
PLATFORM_TREND_RADAR_PROFILE_ID = "pangmen-platform-trend-radar"
CONTENT_RADAR_PROFILE_IDS = frozenset(
    {
        TOPIC_RADAR_PROFILE_ID,
        AI_TECH_RADAR_PROFILE_ID,
        PLATFORM_TREND_RADAR_PROFILE_ID,
    }
)
MINING_MARKET_RADAR_PROFILE_ID = "mining-market-radar"
TOPIC_RADAR_HIDDEN_BLOCK_IDS = frozenset(
    {"content_format", "demo_or_case", "priority_reason", "verification"}
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
        profile_hint: Optional[str] = None,
    ):
        self.profile_names = profile_names or {}
        self.profile_order = profile_order or []
        self.profile_hint = profile_hint

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

        if (
            not items
            and language == "zh"
            and self.profile_hint == MINING_MARKET_RADAR_PROFILE_ID
        ):
            return self._generate_mining_market_brief(
                [], date=date, total_fetched=total_fetched, language=language
            )

        if (
            not items
            and language == "zh"
            and any(
                profile_id
                in {AI_TECH_RADAR_PROFILE_ID, PLATFORM_TREND_RADAR_PROFILE_ID}
                for profile_id in self.profile_order
            )
        ):
            return self._generate_pangmen_content_radar(
                DailySummaryView(groups=[], item_count=0),
                date=date,
                total_fetched=total_fetched,
                language=language,
            )

        if not items:
            return self._generate_empty_summary(date, total_fetched, labels)

        view = self.build_view(items, language)
        topic_card_mode = (
            language == "zh"
            and len(view.groups) == 1
            and view.groups[0].profile_id == TOPIC_RADAR_PROFILE_ID
        )
        content_radar_mode = (
            language == "zh"
            and any(
                group.profile_id
                in {AI_TECH_RADAR_PROFILE_ID, PLATFORM_TREND_RADAR_PROFILE_ID}
                for group in view.groups
            )
            and all(
                group.profile_id in CONTENT_RADAR_PROFILE_IDS for group in view.groups
            )
        )
        mining_market_mode = (
            language == "zh"
            and len(view.groups) == 1
            and view.groups[0].profile_id == MINING_MARKET_RADAR_PROFILE_ID
        )
        if mining_market_mode:
            return self._generate_mining_market_brief(
                view.groups[0].items,
                date=date,
                total_fetched=total_fetched,
                language=language,
            )
        if content_radar_mode:
            return self._generate_pangmen_content_radar(
                view,
                date=date,
                total_fetched=total_fetched,
                language=language,
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

    def _generate_pangmen_content_radar(
        self,
        view: DailySummaryView,
        *,
        date: str,
        total_fetched: int,
        language: str,
    ) -> str:
        """Render the two user-facing Pangmen radars from three internal profiles."""
        labels = LABELS["zh"]
        groups = {group.profile_id: group for group in view.groups}
        parts = [
            "# 🔥 旁门每日内容雷达\n\n",
            f"> 日期：{date}\n\n",
            "## 🤖 今日 AI 资讯\n\n",
        ]

        for profile_id, heading in (
            (TOPIC_RADAR_PROFILE_ID, "AI 应用"),
            (AI_TECH_RADAR_PROFILE_ID, "AI 技术"),
        ):
            parts.append(f"### {heading}\n\n")
            group = groups.get(profile_id)
            if not group:
                parts.append("今日暂无达到筛选标准的重要更新。\n\n")
                continue
            for view_item in group.items:
                parts.append(
                    self._format_item(
                        view_item.item,
                        labels,
                        language,
                        view_item.index,
                        heading_level=4,
                        anchor_id=view_item.anchor_id,
                        title_override=view_item.title,
                        score_override=view_item.score,
                        topic_card=profile_id == TOPIC_RADAR_PROFILE_ID,
                    )
                )

        trend_group = groups.get(PLATFORM_TREND_RADAR_PROFILE_ID)
        trend_items = trend_group.items if trend_group else []
        leverage_items = [
            view_item
            for view_item in trend_items
            if view_item.item.metadata.get("trend_pool", "leverage") == "leverage"
        ]
        watch_items = [
            view_item
            for view_item in trend_items
            if view_item.item.metadata.get("trend_pool") == "watch"
        ]
        parts.append("## 🔥 今日可借势热点\n\n")
        if not leverage_items:
            parts.append("今日暂无达到筛选标准的可借势热点。\n\n")
        else:
            for view_item in leverage_items:
                parts.append(
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
                )
        parts.append("## 👀 今日大盘热点观察\n\n")
        if not watch_items:
            parts.append("今日暂无达到筛选标准的大盘热点观察。\n")
        else:
            for view_item in watch_items:
                parts.append(
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
                )
        return normalize_language("".join(parts).rstrip() + "\n", language)

    @staticmethod
    def _artifact_block_content(item: ContentItem, language: str, block_id: str) -> str:
        if not item.processing:
            return ""
        artifact = item.processing.artifacts.get(language)
        if not artifact:
            return ""
        block = next((entry for entry in artifact.blocks if entry.id == block_id), None)
        return block.content.strip() if block else ""

    def _generate_mining_market_brief(
        self,
        items: List[SummaryItemView],
        *,
        date: str,
        total_fetched: int,
        language: str,
    ) -> str:
        """Render the compact, evidence-first mining morning brief."""
        section_specs = (
            ("today", "今日新增"),
            ("market", "市场与价格"),
            ("watch", "近7天持续关注"),
            ("company", "竞品与项目"),
        )
        grouped: Dict[str, List[SummaryItemView]] = {
            section: [] for section, _ in section_specs
        }
        for view_item in items:
            item = view_item.item
            section = item.metadata.get("brief_section")
            if section not in grouped:
                time_label = item.metadata.get("brief_time_label")
                category = item.metadata.get("category")
                if time_label == "今日新增":
                    section = "today"
                elif category == "mining-market":
                    section = "market"
                elif category == "mining-company":
                    section = "company"
                else:
                    section = "watch"
            grouped[section].append(view_item)

        today_count = len(grouped["today"])
        watch_count = len(items) - today_count

        header = (
            "# 矿业市场情报晨报｜今日新增 + 近7天重点\n\n"
            f"> 日期：{date}｜从 {total_fetched} 条公开信息中筛选出 {len(items)} 条重点情报；"
            f"今日新增 {today_count} 条，近7天持续关注 {watch_count} 条。"
            "演示岗位画像为假设场景，不代表华夏建龙真实内部岗位设置。\n\n"
        )
        core = self._mining_core_judgment(grouped, language)
        sections = [header, "## 今日核心判断\n\n", core, "\n\n"]
        for section, section_title in section_specs:
            sections.append(f"## {section_title}\n\n")
            section_items = grouped[section]
            if not section_items:
                empty_message = (
                    "过去24小时暂无符合高可信、高相关标准的新增情报。"
                    if section == "today"
                    else "近7天暂无符合高可信、高相关标准的重点更新。"
                )
                sections.extend([empty_message, "\n\n"])
                continue
            for index, view_item in enumerate(section_items, start=1):
                sections.append(
                    self._format_mining_brief_item(
                        view_item.item,
                        index=index,
                        language=language,
                        title_override=view_item.title,
                    )
                )
        return normalize_language("".join(sections).rstrip() + "\n", language)

    def _mining_core_judgment(
        self,
        grouped: Dict[str, List[SummaryItemView]],
        language: str,
    ) -> str:
        sentences = []
        today_items = grouped["today"]
        if today_items:
            lead = today_items[0]
            impact = self._artifact_block_content(lead.item, language, "why_it_matters")
            sentence = f"今日最重要的新增变量是「{lead.title}」"
            if impact:
                sentence += f"：{impact}"
            sentences.append(sentence.rstrip("。") + "。")
        else:
            sentences.append("过去24小时暂无符合高可信、高相关标准的重要新增，今天的判断主要延续近7天已确认变量。")

        market_items = grouped["market"] or grouped["watch"]
        if market_items:
            lead = market_items[0]
            impact = self._artifact_block_content(lead.item, language, "why_it_matters")
            sentence = f"近7天最值得关注的市场变量是「{lead.title}」"
            if impact:
                sentence += f"：{impact}"
            sentences.append(sentence.rstrip("。") + "。")

        follow_items = grouped["watch"] or grouped["company"]
        if follow_items:
            lead = follow_items[0]
            sentences.append(f"需要继续跟踪「{lead.title}」的后续进展和实际经营影响。")
        else:
            sentences.append("需要继续跟踪官方市场数据和重点矿企公告，不把付费或延迟数据写成实时公开行情。")
        return "".join(sentences[:3])

    def _format_mining_brief_item(
        self,
        item: ContentItem,
        *,
        index: int,
        language: str,
        title_override: str,
    ) -> str:
        title = _pangu(_escape_markdown(title_override))
        safe_url = _safe_url(item.url)
        title_text = f"[{title}]({safe_url})" if safe_url else title
        blocks = {
            block_id: _pangu(_escape_markdown(
                self._artifact_block_content(item, language, block_id)
            ))
            for block_id in (
                "what_happened",
                "key_numbers",
                "why_it_matters",
            )
        }
        lines = [f"### {index}. {title_text}", ""]
        time_label = item.metadata.get("brief_time_label", "近7天持续关注")
        lines.extend([f"**时间标签**：{_escape_markdown(time_label)}", ""])
        happened = blocks["what_happened"]
        if blocks["key_numbers"]:
            happened = f"{happened} {blocks['key_numbers']}".strip()
        field_values = (
            ("发生了什么", happened),
            ("为什么值得资源板块市场经理关注", blocks["why_it_matters"]),
        )
        for label, value in field_values:
            if value:
                lines.extend([f"**{label}**：{value}", ""])

        raw_source_name = (
            item.metadata.get("feed_name") or item.author or item.source_type.value
        )
        if str(raw_source_name).startswith("Google News"):
            _, separator, publisher = item.title.rpartition(" - ")
            if separator and publisher.strip():
                raw_source_name = publisher.strip()
        source_name = _escape_markdown(raw_source_name)
        published = "发布时间未知"
        if item.published_at:
            beijing_time = item.published_at.astimezone(ZoneInfo("Asia/Shanghai"))
            published = f"{beijing_time:%Y-%m-%d %H:%M}（北京时间）"
        source_text = f"{source_name} · {published}"
        lines.extend([f"**来源和发布时间**：{source_text}", "", "---", ""])
        return "\n".join(lines) + "\n"

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
        profile_id = (
            item.processing.classification.profile
            if item.processing
            else item.profile
        )
        if language == "zh" and profile_id == PLATFORM_TREND_RADAR_PROFILE_ID:
            return self._format_compact_platform_trend_card(
                item,
                index=index,
                anchor_id=anchor_id,
                title_override=title_override,
                score_override=score_override,
            )
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

    @staticmethod
    def _compact_trend_text(value: object, limit: int) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        return text if len(text) <= limit else text[:limit].rstrip("，。；、 ") + "……"

    @classmethod
    def _compact_trend_brief(cls, value: object) -> str:
        text = cls._compact_trend_text(value, 180)
        sentences = [
            part.strip()
            for part in re.findall(r"[^。！？!?]+[。！？!?]?", text)
            if part.strip()
        ]
        return cls._compact_trend_text("".join(sentences[:2]), 160)

    @staticmethod
    def _is_generic_trend_angle(value: str) -> bool:
        compact = re.sub(r"[\s，。；、｜|:：]+", "", value).casefold()
        banned = (
            "科技趋势解读",
            "公司背景介绍",
            "投资分析",
            "行业分析",
            "热点点评",
            "投资建议",
            "ai能否提升效率",
            "用ai做一下",
            "用ppt展示一下",
            "结合账号定位做内容",
            "看看这个工具好不好用",
            "把一周聊天记录交给ai",
            "一页成果看板替代流水账周报",
            "关闭全部通知和改用功能机",
            "机器人公司怎么赚钱",
            "申购策略",
            "股票分析",
            "估值建议",
            "是否值得申购",
        )
        return any(phrase in compact for phrase in banned)

    @staticmethod
    def _format_trend_hot_value(value: object) -> str:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return ""
        number = float(value)
        if abs(number) >= 100_000_000:
            shown = f"{number / 100_000_000:.1f}".rstrip("0").rstrip(".")
            return f"{shown} 亿"
        if abs(number) >= 10_000:
            shown = f"{number / 10_000:.1f}".rstrip("0").rstrip(".")
            return f"{shown} 万"
        return f"{number:g}"

    @staticmethod
    def _trend_platform_label(platform: object) -> str:
        labels = {
            "weibo": "微博",
            "douyin": "抖音",
            "xiaohongshu": "小红书",
            "wechat": "微信",
            "toutiao": "今日头条",
            "zhihu": "知乎",
            "baidu": "百度",
            "36kr": "36Kr",
        }
        raw = str(platform or "").strip()
        return labels.get(raw, raw)

    @classmethod
    def _compact_trend_source(cls, item: ContentItem) -> str:
        rows = item.metadata.get("platform_occurrences")
        occurrences = [row for row in (rows or []) if isinstance(row, dict)]
        if not occurrences:
            occurrences = [
                {
                    "platform": item.metadata.get("platform"),
                    "rank": item.metadata.get("rank"),
                    "hot_value": item.metadata.get("hot_value"),
                    "url": item.metadata.get("original_url") or item.url,
                    "provider": item.metadata.get("provider_name")
                    or item.metadata.get("provider")
                    or item.author,
                }
            ]

        by_platform: dict[str, list[dict]] = {}
        for row in occurrences:
            platform = str(row.get("platform") or "").strip()
            if platform:
                by_platform.setdefault(platform, []).append(row)

        platform_parts = []
        for platform, platform_rows in by_platform.items():
            ranks = [
                int(row["rank"])
                for row in platform_rows
                if isinstance(row.get("rank"), (int, float))
            ]
            part = cls._trend_platform_label(platform)
            if ranks:
                part += f" #{min(ranks)}"
            platform_parts.append(part)

        cross_platform = len(by_platform) > 1
        source_parts = [" / ".join(platform_parts) or "平台榜单"]
        if cross_platform:
            source_parts.append("多平台出现")
        else:
            hot_values = [
                row.get("hot_value")
                for row in occurrences
                if isinstance(row.get("hot_value"), (int, float))
            ]
            if hot_values:
                hot_text = cls._format_trend_hot_value(max(hot_values))
                if hot_text:
                    source_parts.append(f"热度 {hot_text}")

        providers = item.metadata.get("providers")
        if not isinstance(providers, list):
            providers = []
        provider_names = list(
            dict.fromkeys(
                [str(value) for value in providers if value]
                + [
                    str(row.get("provider"))
                    for row in occurrences
                    if row.get("provider")
                ]
            )
        )
        if provider_names:
            source_parts.append(" + ".join(provider_names))

        raw_url = next(
            (str(row.get("url")) for row in occurrences if row.get("url")),
            str(item.metadata.get("original_url") or item.url),
        )
        safe_url = _safe_url(raw_url)
        if safe_url:
            source_parts.append(f"[原始链接]({safe_url})")
        return "｜".join(source_parts)

    def _format_compact_platform_trend_card(
        self,
        item: ContentItem,
        *,
        index: int,
        anchor_id: Optional[str],
        title_override: Optional[str],
        score_override: float | str | None,
    ) -> str:
        """Render a short operations-trend card with verified source metadata."""
        artifact = item.processing.artifacts.get("zh") if item.processing else None
        blocks = {block.id: block for block in (artifact.blocks if artifact else [])}
        title = _pangu(
            _escape_markdown(title_override or (artifact.title if artifact else item.title))
        )
        score = (
            score_override
            if score_override is not None
            else item.processing.analysis.score
            if item.processing and item.processing.analysis
            else "?"
        )
        trend_pool = str(item.metadata.get("trend_pool") or "leverage")
        analysis = item.processing.analysis if item.processing else None
        happened = self._compact_trend_brief(
            blocks.get("what_happened").content
            if blocks.get("what_happened")
            else analysis.summary
            if analysis and analysis.summary
            else item.content
        )
        def valid_angles(value: object) -> list[str]:
            values: list[str] = []
            for raw_angle in re.split(r"\r?\n+|[；;]+", str(value or "")):
                angle = re.sub(
                    r"^\s*(?:[-*•]|\d+[.)、])\s*", "", raw_angle
                ).strip()
                if not angle or self._is_generic_trend_angle(angle):
                    continue
                compact = self._compact_trend_text(angle, 60)
                if compact not in values:
                    values.append(compact)
            return values

        primary_values = valid_angles(
            blocks.get("primary_angle").content
            if blocks.get("primary_angle")
            else ""
        )
        backup_values = valid_angles(
            blocks.get("backup_angle").content
            if blocks.get("backup_angle")
            else ""
        )
        legacy_values = valid_angles(
            blocks.get("borrowing_angles").content
            if blocks.get("borrowing_angles")
            else ""
        )
        primary_angle = next(iter(primary_values or backup_values or legacy_values), "")
        backup_pool = backup_values + legacy_values
        backup_angle = next(
            (value for value in backup_pool if value != primary_angle), ""
        )

        lines = [
            f'<a id="{anchor_id or f"item-{index}"}"></a>',
            f"### {title}"
            if trend_pool == "watch"
            else f"### {title} ⭐️ {score}/10",
            "",
            f"**【热点简报】** {happened}",
        ]
        if trend_pool == "watch":
            operations_reason = (
                analysis.operations_reason.strip()
                if analysis and analysis.operations_reason
                else self._default_platform_operations_reason(item)
            )
            lines.extend(
                [
                    "",
                    "**【为什么值得运营注意】** "
                    + _pangu(_escape_markdown(operations_reason)),
                ]
            )
        elif primary_angle:
            lines.extend(
                ["", f"**【主推角度】** {_pangu(_escape_markdown(primary_angle))}"]
            )
        if trend_pool != "watch" and backup_angle:
            lines.extend(
                ["", f"**【备选角度】** {_pangu(_escape_markdown(backup_angle))}"]
            )
        lines.extend(["", f"来源：{self._compact_trend_source(item)}"])
        lines.extend(["", "---", ""])
        return "\n".join(lines)

    @staticmethod
    def _default_platform_operations_reason(item: ContentItem) -> str:
        platforms = item.metadata.get("platforms") or [item.metadata.get("platform")]
        platforms = [platform for platform in platforms if platform]
        if len(set(platforms)) >= 2:
            return "多平台同步出现，值得持续观察传播扩散。"
        rank = item.metadata.get("rank")
        if isinstance(rank, (int, float)) and rank <= 10:
            return "平台榜单排名靠前，属于正在升温的大众话题。"
        return "大众讨论度正在提高，值得运营团队持续观察。"

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

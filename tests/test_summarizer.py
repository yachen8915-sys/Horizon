"""Unit tests for daily summary rendering."""

import asyncio
from datetime import datetime, timezone

from src.ai.summarizer import DailySummarizer
from src.models import (
    ArtifactSource,
    ClassificationResult,
    ContentAnalysis,
    ContentArtifact,
    ContentBlock,
    ContentItem,
    ProcessingResult,
    SourceType,
)


def _run_async(coro):
    return asyncio.run(coro)


def _make_item(idx: int) -> ContentItem:
    item = ContentItem(
        id=f"rss:item-{idx}",
        source_type=SourceType.RSS,
        title=f"Important Item {idx}",
        url=f"https://example.com/items/{idx}",
        content="content",
        author="tester",
        published_at=datetime(2026, 4, 25, 8, 0, tzinfo=timezone.utc),
        profile="tech-news",
        processing=ProcessingResult(
            classification=ClassificationResult(
                profile="tech-news", method="source_override"
            ),
            analysis=ContentAnalysis(
                score=8.0,
                reason="test",
                summary=f"Summary for item {idx}.",
                tags=["AI", "News"],
            ),
            artifacts={
                language: ContentArtifact(
                    language=language,
                    title=f"Important Item {idx}",
                    blocks=[
                        ContentBlock(
                            id="summary",
                            title="Summary",
                            content=f"Summary for item {idx}.",
                            primary=True,
                        )
                    ],
                )
                for language in ("en", "zh")
            },
        ),
    )
    return item


def test_generate_webhook_overview_lists_items_without_full_details():
    summarizer = DailySummarizer()
    items = [_make_item(1), _make_item(2)]

    result = summarizer.generate_webhook_overview(
        items,
        date="2026-04-25",
        total_fetched=10,
        language="en",
    )

    assert "Selected 2 important items from 10 fetched items" in result
    assert "1. [Important Item 1](https://example.com/items/1)" in result
    assert "2. [Important Item 2](https://example.com/items/2)" in result
    assert "Summary for item 1." not in result


def test_generate_webhook_item_renders_single_item_detail():
    summarizer = DailySummarizer()

    result = summarizer.generate_webhook_item(
        _make_item(1),
        language="en",
        index=1,
        total=2,
    )

    assert result.startswith("Item 1/2")
    assert "## [Important Item 1](https://example.com/items/1)" in result
    assert "Summary for item 1." in result
    assert "**Tags**: `#AI`, `#News`" in result


def test_generate_webhook_item_includes_discussion_link_when_distinct():
    summarizer = DailySummarizer()
    item = _make_item(1)
    item.metadata["discussion_url"] = "https://news.ycombinator.com/item?id=1"

    result = summarizer.generate_webhook_item(
        item,
        language="en",
        index=1,
        total=1,
    )

    assert "tester · Apr 25, 08:00 · [Discussion](https://news.ycombinator.com/item?id=1)" in result


def test_generate_webhook_item_omits_discussion_link_when_same_as_item_url():
    summarizer = DailySummarizer()
    item = _make_item(1)
    item.metadata["discussion_url"] = item.url

    result = summarizer.generate_webhook_item(
        item,
        language="en",
        index=1,
        total=1,
    )

    assert "[Discussion](https://example.com/items/1)" not in result


def test_generate_webhook_item_uses_localized_discussion_label():
    summarizer = DailySummarizer()
    item = _make_item(1)
    item.metadata["discussion_url"] = "https://www.reddit.com/r/python/comments/abc123/test/"

    result = summarizer.generate_webhook_item(
        item,
        language="zh",
        index=1,
        total=1,
    )

    assert "[社区讨论](https://www.reddit.com/r/python/comments/abc123/test/)" in result


def test_generate_summary_zh_uses_localized_selection_header_and_numeric_date():
    summarizer = DailySummarizer()
    item = _make_item(1)

    result = _run_async(
        summarizer.generate_summary(
            [item],
            date="2026-04-25",
            total_fetched=10,
            language="zh",
        )
    )

    assert "> 从 10 条内容中筛选出 1 条重要资讯。" in result
    assert "rss · tester · 4月25日 08:00" in result
    assert "From 10 items" not in result
    assert "Apr 25, 08:00" not in result


def test_generate_summary_renders_topic_radar_as_chinese_topic_cards():
    item = _make_item(1)
    item.profile = "pangmen-topic-radar"
    item.processing.classification.profile = "pangmen-topic-radar"
    item.processing.artifacts["zh"] = ContentArtifact(
        language="zh",
        title="用AI改造汇报流程",
        blocks=[
            ContentBlock(id="what_happened", type="section", title="发生了什么", content="工具新增了可复用的汇报自动化流程。", primary=True),
            ContentBlock(id="audience_problem", type="section", title="适合谁、解决什么问题", content="适合需要反复做周报的职场人。"),
            ContentBlock(id="recommended_angle", type="section", title="推荐切入点", content="把一小时周报压缩成十分钟。"),
            ContentBlock(id="demo_or_case", type="section", title="演示或案例建议", content="录屏展示导入素材到生成大纲。"),
            ContentBlock(id="content_format", type="section", title="推荐内容形式", content="录屏教程。"),
            ContentBlock(id="priority_reason", type="section", title="优先级及理由", content="P0：痛点明确且可演示。"),
            ContentBlock(id="verification", type="section", title="待核实信息", content="确认免费额度和地区限制。"),
        ],
    )

    result = _run_async(
        DailySummarizer().generate_summary([item], "2026-04-25", 10, "zh")
    )

    assert "# 旁门左道PPT · 新媒体选题雷达 - 2026-04-25" in result
    assert "> 从 10 条资讯中筛选出 1 个可做视频的选题。" in result
    assert "## 选题卡" in result
    assert "**原始资讯**：tester · rss ·" in result
    assert "https://example.com/items/1" in result
    for field in ("发生了什么", "适合谁、解决什么问题", "推荐切入点", "标签"):
        assert f"**{field}**" in result
    assert "演示或案例建议" not in result
    assert "推荐内容形式" not in result
    assert "优先级及理由" not in result
    assert "待核实信息" not in result


def test_generate_summary_renders_mining_market_brief_by_intelligence_type():
    items = []
    fixtures = [
        (
            "mining-market",
            "today",
            "今日新增",
            "铁矿石主力合约收盘上涨",
            [
                ContentBlock(id="what_happened", title="发生了什么", content="铁矿石主力合约收盘走强。", primary=True),
                ContentBlock(id="key_numbers", title="关键数字或变化", content="收盘价和涨幅以交易所公布值为准。"),
                ContentBlock(id="why_it_matters", title="为什么值得关注", content="价格变化会影响采购节奏和库存判断。"),
                ContentBlock(id="market_signal", title="市场方向", content="偏强；期货价格走高，但仍需核对现货与库存。"),
            ],
        ),
        (
            "mining-policy",
            "watch",
            "近7天持续关注",
            "某地更新矿山安全监管要求",
            [
                ContentBlock(id="what_happened", title="政策或监管事项", content="监管部门更新矿山安全检查要求。", primary=True),
                ContentBlock(id="scope", title="涉及地区和环节", content="中国；矿山生产和安全检查。"),
                ContentBlock(id="why_it_matters", title="潜在影响", content="检查趋严可能影响部分矿山排产。"),
            ],
        ),
        (
            "mining-company",
            "company",
            "近7天持续关注",
            "Vale 更新铁矿项目进度",
            [
                ContentBlock(id="what_happened", title="具体动作", content="Vale 更新一项铁矿扩产项目进度。", primary=True),
                ContentBlock(id="scope", title="公司、项目、矿种和地区", content="Vale；铁矿石；巴西。"),
                ContentBlock(id="key_numbers", title="关键数字", content="项目时间节点以公司公告为准。"),
                ContentBlock(id="why_it_matters", title="为什么值得关注", content="项目进度关系到后续海运供应增量。"),
            ],
        ),
        (
            "mining-market",
            "market",
            "近7天持续关注",
            "Fortescue 更新年度发运指引",
            [
                ContentBlock(id="what_happened", title="发生了什么", content="Fortescue 公布年度发运和下一年度指引。", primary=True),
                ContentBlock(id="key_numbers", title="关键数字", content="年度发运量和指引来自公司经营报告。"),
                ContentBlock(id="why_it_matters", title="为什么值得关注", content="供应指引影响海运铁矿增量判断。"),
            ],
        ),
    ]
    for idx, (category, section, time_label, title, blocks) in enumerate(fixtures, start=1):
        item = _make_item(idx)
        item.profile = "mining-market-radar"
        item.processing.classification.profile = "mining-market-radar"
        item.metadata["category"] = category
        item.metadata["brief_section"] = section
        item.metadata["brief_time_label"] = time_label
        if idx == 1:
            item.title = f"{title} - 东方财富"
            item.metadata["feed_name"] = "Google News｜铁矿价格与供需"
        item.processing.artifacts["zh"] = ContentArtifact(
            language="zh", title=title, blocks=blocks
        )
        items.append(item)

    result = _run_async(
        DailySummarizer().generate_summary(items, "2026-08-07", 42, "zh")
    )

    assert result.startswith("# 矿业市场情报晨报｜今日新增 + 近7天重点")
    assert "日期：2026-08-07" in result
    assert "## 今日核心判断" in result
    assert "今日最重要的新增变量" in result
    assert "## 今日新增" in result
    assert "## 市场与价格" in result
    assert "## 近7天持续关注" in result
    assert "## 竞品与项目" in result
    assert "**时间标签**：今日新增" in result
    assert "**时间标签**：近7天持续关注" in result
    assert "**来源和发布时间**" in result
    assert "东方财富 · 2026-04-25 16:00（北京时间）" in result
    assert "**为什么值得资源板块市场经理关注**" in result
    assert "**关键数字或变化**" not in result
    assert "**涉及地区和环节**" not in result
    assert "⭐" not in result
    assert "**标签**" not in result


def test_generate_summary_mining_market_brief_marks_empty_sections_without_filler():
    item = _make_item(1)
    item.profile = "mining-market-radar"
    item.processing.classification.profile = "mining-market-radar"
    item.metadata["category"] = "mining-market"
    item.metadata["brief_section"] = "today"
    item.metadata["brief_time_label"] = "今日新增"
    item.processing.artifacts["zh"] = ContentArtifact(
        language="zh",
        title="市场更新",
        blocks=[
            ContentBlock(id="what_happened", title="发生了什么", content="市场出现一项可核实变化。", primary=True),
            ContentBlock(id="why_it_matters", title="为什么值得关注", content="该变化影响短期供需判断。"),
            ContentBlock(id="market_signal", title="市场方向", content="变化有限；尚无充分证据支持单边判断。"),
        ],
    )

    result = _run_async(
        DailySummarizer().generate_summary([item], "2026-08-07", 3, "zh")
    )

    assert result.count("近7天暂无符合高可信、高相关标准的重点更新。") == 3


def test_generate_summary_mining_market_brief_supports_an_empty_run():
    result = _run_async(
        DailySummarizer(profile_hint="mining-market-radar").generate_summary(
            [], "2026-08-07", 0, "zh"
        )
    )

    assert result.startswith("# 矿业市场情报晨报｜今日新增 + 近7天重点")
    assert result.count("过去24小时暂无符合高可信、高相关标准的新增情报。") == 1
    assert result.count("近7天暂无符合高可信、高相关标准的重点更新。") == 3


def test_generate_webhook_item_hides_internal_topic_radar_fields():
    item = _make_item(1)
    item.profile = "pangmen-topic-radar"
    item.processing.classification.profile = "pangmen-topic-radar"
    item.processing.artifacts["zh"] = ContentArtifact(
        language="zh",
        title="用 AI 改造汇报流程",
        blocks=[
            ContentBlock(
                id="what_happened",
                type="section",
                title="发生了什么",
                content="工具新增了可复用的汇报自动化流程。",
                primary=True,
            ),
            ContentBlock(
                id="recommended_angle",
                type="section",
                title="推荐切入点",
                content="把一小时周报压缩成十分钟。",
            ),
            ContentBlock(
                id="demo_or_case",
                type="section",
                title="演示或案例建议",
                content="录屏展示导入素材到生成大纲。",
            ),
            ContentBlock(
                id="content_format",
                type="section",
                title="推荐内容形式",
                content="录屏教程。",
            ),
            ContentBlock(
                id="priority_reason",
                type="section",
                title="优先级及理由",
                content="P0：痛点明确且可演示。",
            ),
            ContentBlock(
                id="verification",
                type="section",
                title="待核实信息",
                content="确认免费额度和地区限制。",
            ),
        ],
    )

    result = DailySummarizer().generate_webhook_item(
        item, language="zh", index=1, total=1
    )

    assert "演示或案例建议" not in result
    assert "推荐内容形式" not in result
    assert "优先级及理由" not in result
    assert "待核实信息" not in result


def test_topic_radar_summary_does_not_pad_recommended_angles_with_generic_fallbacks():
    item = _make_item(1)
    item.profile = "pangmen-topic-radar"
    item.processing.classification.profile = "pangmen-topic-radar"
    item.processing.artifacts["zh"] = ContentArtifact(
        language="zh",
        title="WorkBuddy 写入飞书实测",
        blocks=[
            ContentBlock(
                id="what_happened",
                type="section",
                title="发生了什么",
                content="WorkBuddy 新增了通过连接器写入飞书多维表格的能力。",
                primary=True,
            ),
            ContentBlock(
                id="audience_problem",
                type="section",
                title="适合谁、解决什么问题",
                content="适合需要批量录入客户资料的销售和运营。",
            ),
            ContentBlock(
                id="recommended_angle",
                type="section",
                title="推荐切入点",
                content="同一份客户资料，手动录入与 WorkBuddy 写入飞书究竟差几步？",
            ),
        ],
    )

    result = _run_async(
        DailySummarizer().generate_summary([item], "2026-08-06", 10, "zh")
    )

    assert result.count("- 同一份客户资料，手动录入与 WorkBuddy 写入飞书究竟差几步？") == 1
    assert "用前后对比验证实际收益" not in result
    assert "拆解普通用户能复现的操作路径" not in result
    assert "核对限制后再判断是否值得跟进" not in result


def test_generate_summary_groups_items_by_profile_with_heading_hierarchy():
    news = _make_item(1)
    blog = _make_item(2)
    blog.profile = "tech-blog"
    blog.processing.classification.profile = "tech-blog"
    summarizer = DailySummarizer(
        profile_names={
            "tech-news": {"default": "Technology News", "zh": "科技新闻"},
            "tech-blog": {"default": "Technology Blog", "zh": "科技博客"},
        }
    )

    result = _run_async(
        summarizer.generate_summary(
            [news, blog],
            date="2026-04-25",
            total_fetched=2,
            language="en",
        )
    )

    assert result.count("# Horizon Daily") == 1
    assert "## Technology News" in result
    assert "## Technology Blog" in result
    assert "### [Important Item 1]" in result
    assert "### [Important Item 2]" in result


def test_generate_summary_uses_configured_profile_order():
    finance = _make_item(1)
    finance.profile = "finance-news"
    finance.processing.classification.profile = "finance-news"
    blog = _make_item(2)
    blog.profile = "tech-blog"
    blog.processing.classification.profile = "tech-blog"
    news = _make_item(3)
    summarizer = DailySummarizer(
        profile_names={
            "tech-news": {"default": "Technology News"},
            "tech-blog": {"default": "Technology Blog"},
            "finance-news": {"default": "Financial News"},
        },
        profile_order=["tech-news", "tech-blog", "finance-news"],
    )

    result = _run_async(
        summarizer.generate_summary(
            [finance, blog, news],
            date="2026-04-25",
            total_fetched=3,
            language="en",
        )
    )

    assert result.index("## Technology News") < result.index("## Technology Blog")
    assert result.index("## Technology Blog") < result.index("## Financial News")


def test_generate_content_radar_summary_splits_platform_trends_into_two_pools():
    app = _make_item(1)
    app.profile = "pangmen-topic-radar"
    app.processing.classification.profile = "pangmen-topic-radar"
    tech = _make_item(2)
    tech.profile = "pangmen-ai-tech-radar"
    tech.processing.classification.profile = "pangmen-ai-tech-radar"
    trend = _make_item(3)
    trend.profile = "pangmen-platform-trend-radar"
    trend.processing.classification.profile = "pangmen-platform-trend-radar"
    trend.metadata["trend_pool"] = "leverage"
    watch = _make_item(4)
    watch.title = "百花奖获奖名单"
    watch.profile = "pangmen-platform-trend-radar"
    watch.processing.classification.profile = "pangmen-platform-trend-radar"
    watch.processing.analysis.operations_score = 8.5
    watch.processing.analysis.content_opportunity_score = 4.5
    watch.processing.analysis.operations_reason = "娱乐文化热点快速上升。"
    watch.metadata["trend_pool"] = "watch"
    summarizer = DailySummarizer(
        profile_names={
            "pangmen-topic-radar": {"zh": "AI 应用"},
            "pangmen-ai-tech-radar": {"zh": "AI 技术"},
            "pangmen-platform-trend-radar": {"zh": "平台运营热点"},
        },
        profile_order=[
            "pangmen-topic-radar",
            "pangmen-ai-tech-radar",
            "pangmen-platform-trend-radar",
        ],
    )

    result = _run_async(
        summarizer.generate_summary(
            [app, tech, trend, watch],
            date="2026-08-10",
            total_fetched=100,
            language="zh",
        )
    )

    assert result.startswith("# 🔥 旁门每日内容雷达\n")
    assert "## 🤖 今日 AI 资讯" in result
    assert "### AI 应用" in result
    assert "### AI 技术" in result
    assert "## 🔥 今日运营热点" in result
    assert "### 今日可借势" in result
    assert "### 今日大盘观察" in result
    assert result.index("### AI 应用") < result.index("### AI 技术")
    assert result.index("### AI 技术") < result.index("## 🔥 今日运营热点")
    assert result.index("### 今日可借势") < result.index("### 今日大盘观察")
    assert "Horizon 每日速递" not in result
    assert "从 100 条内容中筛选" not in result


def test_watch_pool_card_has_operations_reason_without_forced_angles():
    trend = _make_item(5)
    trend.title = "百花奖获奖名单"
    trend.content = "微博榜单第 8 位，热度 851 万。"
    trend.profile = "pangmen-platform-trend-radar"
    trend.processing.classification.profile = "pangmen-platform-trend-radar"
    trend.processing.analysis.operations_score = 8.5
    trend.processing.analysis.content_opportunity_score = 4.5
    trend.processing.analysis.operations_reason = "娱乐文化热点快速上升。"
    trend.metadata.update(
        {
            "trend_pool": "watch",
            "platform": "weibo",
            "rank": 8,
            "hot_value": 8_510_000,
            "provider_name": "DailyHotAPI",
        }
    )
    summarizer = DailySummarizer()

    result = summarizer.generate_webhook_item(
        trend,
        language="zh",
        index=1,
        total=1,
        title=trend.title,
        score=8.5,
    )

    assert "百花奖获奖名单" in result
    assert "【为什么值得运营注意】" in result
    assert "娱乐文化热点快速上升" in result
    assert "⭐️" not in result
    assert "主推角度" not in result
    assert "备选角度" not in result
    assert "借势角度" not in result


def test_platform_trend_card_keeps_only_brief_primary_backup_and_compact_source():
    trend = _make_item(1)
    trend.profile = "pangmen-platform-trend-radar"
    trend.processing.classification.profile = "pangmen-platform-trend-radar"
    trend.processing.artifacts["zh"] = ContentArtifact(
        language="zh",
        title="宇树科技申购登上抖音热榜",
        blocks=[
            ContentBlock(
                id="what_happened",
                title="热点简报",
                content="宇树科技 IPO 申购进入抖音热点榜第 8 位，机器人与具身智能再次进入大众讨论。",
                primary=True,
            ),
            ContentBlock(
                id="primary_angle",
                title="主推角度",
                content="AI + PPT｜模拟老板明早汇报宇树，只给 AI 招股书，验收能否做出五页业务 PPT 并标注证据页码。",
            ),
            ContentBlock(
                id="backup_angle",
                title="备选角度",
                content="PPT 表达｜拿宇树招股书中的机器人产品分类做一页矩阵改版，看复杂产品线能否一眼讲清。",
            ),
            ContentBlock(
                id="source_evidence",
                title="来源",
                content="抖音 #8｜热度 776 万｜DailyHotAPI + ALAPI｜原始链接",
            ),
        ],
    )
    trend.metadata.update(
        {
            "platform": "douyin",
            "providers": ["DailyHotAPI", "ALAPI"],
            "platform_occurrences": [
                {
                    "platform": "douyin",
                    "rank": 8,
                    "hot_value": 7_760_000,
                    "url": "https://www.douyin.com/hot/unitree",
                    "provider": "DailyHotAPI",
                },
                {
                    "platform": "douyin",
                    "rank": 8,
                    "hot_value": 7_760_000,
                    "url": "https://alapi.example/unitree",
                    "provider": "ALAPI",
                },
            ],
            "cross_platform_count": 1,
        }
    )

    result = DailySummarizer().generate_webhook_item(
        trend, language="zh", index=1, total=1, score=7.5
    )

    assert "【热点简报】" in result
    assert "【主推角度】" in result
    assert "【备选角度】" in result
    assert "【可借势方向】" not in result
    assert "来源：抖音 #8｜热度 776 万｜DailyHotAPI + ALAPI｜[原始链接]" in result
    assert result.count("AI + PPT｜") == 1
    assert "为什么和旁门有关" not in result
    assert "行动建议" not in result
    assert "风险" not in result
    assert "多平台出现" not in result


def test_platform_trend_card_drops_evergreen_angles_but_keeps_hotspot_asset_angle():
    trend = _make_item(1)
    trend.profile = "pangmen-platform-trend-radar"
    trend.processing.classification.profile = "pangmen-platform-trend-radar"
    trend.processing.artifacts["zh"] = ContentArtifact(
        language="zh",
        title="宇树科技申购登上抖音热榜",
        blocks=[
            ContentBlock(
                id="what_happened",
                title="热点简报",
                content="宇树科技申购进入抖音热榜第 8 位。",
                primary=True,
            ),
            ContentBlock(
                id="borrowing_angles",
                title="可借势方向",
                content=(
                    "把一周聊天记录交给 AI，生成反向周报。\n"
                    "用一页成果看板替代流水账周报。\n"
                    "关闭全部通知和改用功能机。\n"
                    "一张图讲清机器人公司怎么赚钱。\n"
                    "拿宇树招股书里的产品参数做一页机器人矩阵改版，对比原文和重排后的理解速度。"
                ),
            ),
            ContentBlock(id="source_evidence", title="来源", content="来源证据"),
        ],
    )
    trend.metadata.update(
        {
            "platform": "douyin",
            "rank": 8,
            "provider_name": "DailyHotAPI",
            "original_url": "https://www.douyin.com/hot/unitree",
        }
    )

    result = DailySummarizer().generate_webhook_item(
        trend, language="zh", index=1, total=1, score=7.5
    )

    assert "把一周聊天记录交给 AI" not in result
    assert "一页成果看板替代流水账周报" not in result
    assert "关闭全部通知和改用功能机" not in result
    assert "一张图讲清机器人公司怎么赚钱" not in result
    assert "拿宇树招股书里的产品参数" in result
    assert "【主推角度】" in result
    assert "【备选角度】" not in result
    assert "【可借势方向】" not in result


def test_platform_trend_card_allows_no_borrowing_angles_without_filler():
    trend = _make_item(1)
    trend.profile = "pangmen-platform-trend-radar"
    trend.processing.classification.profile = "pangmen-platform-trend-radar"
    trend.processing.artifacts["zh"] = ContentArtifact(
        language="zh",
        title="缺少独有资产的榜单话题",
        blocks=[
            ContentBlock(
                id="what_happened",
                title="热点简报",
                content="该话题进入微博热榜，但没有可核实的独有内容资产。",
                primary=True,
            ),
            ContentBlock(id="source_evidence", title="来源", content="来源证据"),
        ],
    )
    trend.metadata.update(
        {
            "platform": "weibo",
            "rank": 9,
            "provider_name": "DailyHotAPI",
            "original_url": "https://weibo.example/no-asset",
        }
    )

    result = DailySummarizer().generate_webhook_item(
        trend, language="zh", index=1, total=1, score=4
    )

    assert "【热点简报】" in result
    assert "【主推角度】" not in result
    assert "【备选角度】" not in result
    assert "暂无" not in result
    assert "来源：微博 #9" in result
    assert "行动建议" not in result
    assert "风险" not in result


def test_platform_trend_card_drops_ipo_investment_advice_angles():
    trend = _make_item(1)
    trend.profile = "pangmen-platform-trend-radar"
    trend.processing.classification.profile = "pangmen-platform-trend-radar"
    trend.processing.artifacts["zh"] = ContentArtifact(
        language="zh",
        title="宇树科技申购登上抖音热榜",
        blocks=[
            ContentBlock(
                id="what_happened",
                title="热点简报",
                content="宇树科技申购进入抖音热榜第 8 位。",
                primary=True,
            ),
            ContentBlock(
                id="borrowing_angles",
                title="可借势方向",
                content=(
                    "宇树 IPO 是否值得申购，给普通人的申购策略。\n"
                    "用宇树财务数据做一份股票估值建议。\n"
                    "把宇树招股书的产品参数交给两个 AI，比较谁做出的机器人产品矩阵更清楚。"
                ),
            ),
            ContentBlock(id="source_evidence", title="来源", content="来源证据"),
        ],
    )
    trend.metadata.update(
        {
            "platform": "douyin",
            "rank": 8,
            "provider_name": "DailyHotAPI",
            "original_url": "https://www.douyin.com/hot/unitree",
        }
    )

    result = DailySummarizer().generate_webhook_item(
        trend, language="zh", index=1, total=1, score=7.5
    )

    assert "申购策略" not in result
    assert "估值建议" not in result
    assert "宇树招股书的产品参数" in result
    assert "【主推角度】" in result
    assert "【备选角度】" not in result


def test_platform_trend_card_marks_only_real_cross_platform_occurrence():
    trend = _make_item(1)
    trend.profile = "pangmen-platform-trend-radar"
    trend.processing.classification.profile = "pangmen-platform-trend-radar"
    trend.processing.artifacts["zh"] = ContentArtifact(
        language="zh",
        title="年轻人开始反向使用周报",
        blocks=[
            ContentBlock(
                id="what_happened",
                title="热点简报",
                content="该话题同时进入微博和抖音榜单。",
                primary=True,
            ),
            ContentBlock(
                id="borrowing_angles",
                title="可借势方向",
                content="AI 职场｜把无效周报交给 AI 重写，实测能否保留结果又减少废话。",
            ),
            ContentBlock(id="source_evidence", title="来源", content="来源证据"),
        ],
    )
    trend.metadata.update(
        {
            "providers": ["DailyHotAPI", "ALAPI"],
            "platform_occurrences": [
                {
                    "platform": "weibo",
                    "rank": 3,
                    "url": "https://weibo.example/topic",
                    "provider": "DailyHotAPI",
                },
                {
                    "platform": "douyin",
                    "rank": 12,
                    "url": "https://douyin.example/topic",
                    "provider": "ALAPI",
                },
            ],
            "cross_platform_count": 2,
        }
    )

    result = DailySummarizer().generate_webhook_item(
        trend, language="zh", index=1, total=1
    )

    assert "微博 #3 / 抖音 #12｜多平台出现" in result


def test_empty_content_radar_does_not_fall_back_to_generic_horizon_summary():
    summarizer = DailySummarizer(
        profile_order=[
            "pangmen-topic-radar",
            "pangmen-ai-tech-radar",
            "pangmen-platform-trend-radar",
        ]
    )

    result = _run_async(
        summarizer.generate_summary([], "2026-08-10", 80, language="zh")
    )

    assert result.startswith("# 🔥 旁门每日内容雷达\n")
    assert "### AI 应用" in result
    assert "### AI 技术" in result
    assert "## 🔥 今日运营热点" in result
    assert "### 今日可借势" in result
    assert "### 今日大盘观察" in result
    assert "Horizon 每日速递" not in result


def test_generate_summary_renders_primary_block_before_source_without_heading():
    item = _make_item(1)
    item.processing.artifacts["en"] = ContentArtifact(
        language="en",
        title="Important Item 1",
        blocks=[
            ContentBlock(
                id="summary",
                title="Summary",
                content="Primary explanation.",
                primary=True,
            ),
            ContentBlock(
                id="background",
                title="Background",
                content="Supporting context.",
            ),
        ],
    )

    result = _run_async(
        DailySummarizer().generate_summary(
            [item],
            date="2026-04-25",
            total_fetched=1,
            language="en",
        )
    )

    assert "#### Summary" not in result
    assert result.index("Primary explanation.") < result.index(
        "rss · tester · Apr 25, 08:00"
    )
    assert result.index("rss · tester · Apr 25, 08:00") < result.index(
        "**「Background」** Supporting context."
    )


def test_generate_summary_renders_non_primary_blog_sections_after_source():
    item = _make_item(1)
    item.profile = "tech-blog"
    item.processing.classification.profile = "tech-blog"
    item.processing.artifacts["en"] = ContentArtifact(
        language="en",
        title="A technical article",
        blocks=[
            ContentBlock(
                id="background",
                title="Background",
                content="The original constraints.",
            ),
            ContentBlock(
                id="solution",
                title="Solution",
                content="The implementation and evidence.",
            ),
            ContentBlock(
                id="takeaway",
                title="Takeaway",
                content="The durable lesson.",
            ),
        ],
    )

    result = _run_async(
        DailySummarizer().generate_summary(
            [item],
            date="2026-04-25",
            total_fetched=1,
            language="en",
        )
    )

    source_index = result.index("rss · tester · Apr 25, 08:00")
    context_index = result.index("**「Background」** The original constraints.")
    solution_index = result.index("**「Solution」** The implementation and evidence.")
    takeaway_index = result.index("**「Takeaway」** The durable lesson.")
    assert source_index < context_index < solution_index < takeaway_index
    assert "#### Background" not in result


def test_generate_webhook_item_normalizes_existing_zh_artifact_to_simplified():
    item = _make_item(1)
    item.processing.artifacts["zh"] = ContentArtifact(
        language="zh",
        title="代理工作流更新",
        blocks=[
            ContentBlock(
                id="background",
                title="背景",
                content="社群關注這項更新，並分享實際用量數據。",
            )
        ],
    )

    result = DailySummarizer().generate_webhook_item(
        item,
        language="zh",
        index=1,
        total=1,
    )

    assert "代理工作流更新" in result
    assert "**「背景」** 社群关注这项更新，并分享实际用量数据。" in result
    assert "關注" not in result


def test_generate_summary_renumbers_interleaved_profiles_and_localizes_headings():
    first_news = _make_item(1)
    blog = _make_item(2)
    second_news = _make_item(3)
    blog.profile = "tech-blog"
    blog.processing.classification.profile = "tech-blog"
    summarizer = DailySummarizer(
        profile_names={
            "tech-news": {"default": "Technology News", "zh": "科技新闻"},
            "tech-blog": {"default": "Technology Blog", "zh": "科技博客"},
        }
    )

    result = _run_async(
        summarizer.generate_summary(
            [first_news, blog, second_news],
            date="2026-04-25",
            total_fetched=3,
            language="zh",
        )
    )

    assert "## 科技新闻" in result
    assert "## 科技博客" in result
    assert "1. [Important Item 1](#item-tech-news-1)" in result
    assert "2. [Important Item 3](#item-tech-news-2)" in result
    assert "1. [Important Item 2](#item-tech-blog-1)" in result
    assert result.index("2. [Important Item 3]") < result.index("1. [Important Item 2]")
    assert '<a id="item-tech-news-1"></a>' in result
    assert '<a id="item-tech-blog-1"></a>' in result


def test_generate_empty_summary_zh_uses_localized_analyzed_line():
    summarizer = DailySummarizer()

    result = _run_async(
        summarizer.generate_summary(
            [],
            date="2026-04-25",
            total_fetched=10,
            language="zh",
        )
    )

    assert "> 已分析 10 条内容，但没有达到重要性阈值的条目。" in result
    assert "Analyzed 10 items" not in result


def test_generate_summary_escapes_untrusted_text_in_all_output_contexts():
    summarizer = DailySummarizer()
    item = _make_item(1)
    item.title = '<script>alert("title")</script> [click](javascript:alert(1))'
    item.processing.analysis.summary = '<img src=x onerror="alert(1)"> **summary**'
    item.author = '<svg onload="alert(1)">'
    item.processing.analysis.tags = ['tag`](javascript:alert(1))']
    item.processing.artifacts["en"] = ContentArtifact(
        language="en",
        title=item.title,
        blocks=[
            ContentBlock(
                id="summary",
                title="Summary",
                content='<img src=x onerror="alert(1)"> **summary**',
                primary=True,
            ),
            ContentBlock(
                id="background",
                title="Background",
                content='<iframe src="data:text/html,bad"></iframe>',
            ),
            ContentBlock(
                id="community_discussion",
                title="Discussion",
                content="[bad](data:text/html,bad)",
            ),
        ],
        sources=[
            ArtifactSource(
                id="ref-1",
                title='<img src=x onerror="alert(1)">',
                url="https://example.com/ref",
            )
        ],
    )
    item.metadata.update(
        {
            "feed_name": '<b onclick="alert(1)">feed</b>',
        }
    )

    result = _run_async(summarizer.generate_summary([item], "2026-04-25", 1))

    assert "<script>" not in result
    assert "<img src=x" not in result
    assert "<iframe" not in result
    assert "<b onclick" not in result
    assert "](javascript:" not in result
    assert "](data:text/html" not in result
    assert "&lt;script&gt;" in result
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in result


def test_generate_summary_rejects_unsafe_urls_and_quote_injection():
    summarizer = DailySummarizer()
    item = _make_item(1)
    item.metadata["discussion_url"] = 'javascript:alert("discussion")'
    item.processing.artifacts["en"].sources = [
        ArtifactSource(
            id="quoted",
            title='Quoted "><script>alert(1)</script>',
            url='https://example.com/\" onmouseover=\"alert(1)',
        ),
        ArtifactSource(id="js", title="JavaScript", url="javascript:alert(1)"),
        ArtifactSource(
            id="data",
            title="Data",
            url="data:text/html,<script>alert(1)</script>",
        ),
    ]

    result = _run_async(summarizer.generate_summary([item], "2026-04-25", 1))

    assert 'href="https://example.com/%22%20onmouseover=%22alert%281%29"' in result
    assert '<li>JavaScript</li>' in result
    assert '<li>Data</li>' in result
    assert 'href="javascript:' not in result
    assert 'href="data:' not in result
    assert '<script>' not in result


def test_generate_summary_preserves_normal_http_links():
    summarizer = DailySummarizer()
    item = _make_item(1)
    item.metadata["discussion_url"] = "https://example.com/discuss?id=1#comments"
    item.processing.artifacts["en"].sources = [
        ArtifactSource(
            id="useful",
            title="Useful reference",
            url="https://docs.example.com/path?q=one&lang=en",
        )
    ]

    result = _run_async(summarizer.generate_summary([item], "2026-04-25", 1))

    assert "[Important Item 1](https://example.com/items/1)" in result
    assert "[Discussion](https://example.com/discuss?id=1#comments)" in result
    assert 'href="https://docs.example.com/path?q=one&amp;lang=en"' in result

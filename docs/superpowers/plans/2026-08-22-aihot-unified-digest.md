# AI HOT Unified Digest Implementation Plan

> **For Codex:** Execute incrementally with test-driven development. Preserve all pre-existing uncommitted work and do not commit or push without explicit authorization.

**Goal:** 将更多合格 AI HOT 内容融合进现有 AI应用/AI技术栏目，并在单张飞书卡片中用“主体详版 + 查看更多资讯”呈现。

**Architecture:** 扩展 `AIHotScraper` 的发现模式并保留来源元数据；在分析前做低成本证据预筛和结构化路由；沿用现有分析、事件去重、多样性和跨日状态；在最终选择后增加显示层级分配，由 Feishu renderer 在单卡容量内生成详细面板和紧凑折叠栏。

**Tech Stack:** Python, Pydantic, pytest, Feishu Card 2.0 JSON.

---

### Task 1: 锁定配置和模型契约

**Files:**
- Modify: `src/models.py`
- Modify: `data/config.github.json`
- Test: `tests/test_github_runtime_config.py`

1. 先写失败测试，覆盖 AI HOT 扩展模式、6 分门槛、单卡主体预算和查看更多开关。
2. 增加最少配置字段；保持旧配置可加载。
3. 移除 topic/tech 的独立数量硬上限，但保留其他 profile 现有上限。
4. 运行配置相关测试。

### Task 2: 扩大 AI HOT 发现并严格预筛

**Files:**
- Modify: `src/scrapers/aihot.py`
- Test: `tests/test_aihot.py`

1. 为精选流与扩展流写夹具测试，验证跨模式 ID/URL 去重。
2. 实现可配置 `mode=selected` 与 `mode=all`，保留 category、source_kind、AI HOT score、原始链接和抓取模式。
3. 修复扩展窗口被全局 `since` 意外截断的问题，同时用配置化发布时间窗口防止历史倒灌。
4. 对无正文、无原始链接或明显非 AI 内容记录预筛原因；不进入昂贵分析。
5. 不为未证实的 GitHub Trending 接口添加伪实现。

### Task 3: 路由和 AI 核心准入

**Files:**
- Create: `src/processing/aihot_routing.py`
- Modify: `src/orchestrator.py`
- Modify: `src/ai/prompting/analysis.py`
- Modify: `profiles/pangmen-topic-radar/analysis.md`
- Test: `tests/test_aihot_routing.py`
- Test: `tests/test_fetch_reporting.py`

1. 写测试证明应用实践进入 topic、模型/论文/开发项目进入 tech，未知类型安全降级。
2. 基于结构化 category/source_kind 和正文证据路由，不仅依赖标题关键词。
3. 分析输出增加 AI 核心相关性和证据质量判定；程序硬门槛不可被总分覆盖。
4. 对媒体转述保持 secondary 属性，官方原文优先。

### Task 4: 取消 AI 栏目硬截断并统一排序

**Files:**
- Modify: `src/processing/editorial_selection.py`
- Modify: `src/orchestrator.py`
- Test: `tests/test_editorial_selection.py`
- Test: `tests/test_balanced_digest.py`

1. 写测试覆盖超过旧 8/5 上限时，所有达到 6 分且通过硬门槛的 AI 内容仍被保留。
2. 保留 exact-event、跨日冷却和同日多样性限制；“合格多少展示多少”不等于放宽质量。
3. 排序依次使用 Horizon 总分、相关性、新鲜度、可演示性、发布时间和稳定 ID；AI HOT score 仅作末级辅助。
4. 确认平台变化和运营热点数量规则不回归。

### Task 5: 单卡主体与“查看更多资讯”

**Files:**
- Modify: `src/summarizer.py`
- Modify: `src/notifiers/feishu.py`
- Modify: `src/models.py`
- Test: `tests/test_summarizer.py`
- Test: `tests/test_feishu_notifier.py`

1. 写失败测试：一次 webhook 请求、一张卡、主体动态约 12–16 条、溢出内容进入一个 `查看更多资讯（N条）` 折叠面板。
2. 为最终条目标记 `primary`/`more` 显示层级，不新建 AI HOT 栏目。
3. `more` 项使用标题、分数、来源类型、一句话价值和原始链接的紧凑格式。
4. 加入组件/文本预算；超预算时只下沉显示层级，不改变入选状态。
5. 极端超限必须产生明确诊断，不得静默截断或发送第二张卡。

### Task 6: 诊断与回归验证

**Files:**
- Modify: `src/orchestrator.py`
- Modify: `tests/test_fetch_reporting.py`
- Modify: `tests/test_balanced_digest.py`

1. 记录 AI HOT 各模式抓取、预筛、路由、合并、评分、主体/更多数量及卡片预算。
2. 增加当日真实数据的离线夹具回放，不调用真实 AI、webhook 或生产 state。
3. 运行针对性测试、full pytest、compileall、配置模型校验和 `git diff --check`。
4. 逐文件审查 diff，明确区分本轮改动、既有 AI 质量改动、RSS 身份实验及无关雷达文件。
5. 停止在 Commit 前，报告未证实的 AI HOT GitHub 覆盖和单卡容量边界。

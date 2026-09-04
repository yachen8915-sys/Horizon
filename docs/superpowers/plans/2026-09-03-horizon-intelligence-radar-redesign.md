# Horizon Intelligence Radar Redesign Implementation Plan

> **For Codex:** Execute with test-driven development in the isolated worktree. Preserve the two existing dirty worktrees. Do not commit, push, trigger Actions, call real AI, send Feishu, write production state, or enable schedulers without explicit authorization.

**Goal:** 将 Horizon 从单次日报生成器升级为具备完整候选生命周期、证据判断、决策分类、上午精选和下午增量的 AI 信息雷达。

**Architecture:** 在现有 scraper 和 AI pipeline 之间增加持久候选账本与证据层，以 `CandidateRecord` 贯穿发现、观察、准入、选择、暂缓、合并和淘汰。选择器只处理已通过硬门槛的候选，并按四类用户决策完成排序、多样性和新鲜度控制。投递层从统一选择结果生成 09:00 完整精选与 16:00 增量，所有阶段同时写可解释 diagnostics。

**Tech Stack:** Python 3.12, Pydantic, pytest, JSON state/diagnostics, existing Horizon scrapers, Feishu Card 2.0, GitHub Actions, Windows Task Scheduler.

**Design:** `docs/superpowers/specs/2026-09-03-horizon-intelligence-radar-redesign-design.md`

**Source audit:** `docs/superpowers/specs/2026-09-03-horizon-source-coverage-audit.md`

**Historical review:** `docs/superpowers/specs/2026-09-03-historical-editorial-shadow-review.md`

**Migration contract:** `docs/superpowers/specs/2026-09-03-horizon-branch-migration-contract.md`

## 2026-09-03 实施检查点

已完成：Task 0 的来源代码与单日真实 source-only shadow；Task 1–12 的新领域模型、候选账本、身份/证据/重复观测/评分/选择、上午/下午投递语义、本地候选档案、diagnostics，以及旧分支关键行为的选择性迁移；Task 13 的 workflow 模式、双时段定义、同模式防重、本地安全回退注册脚本和验收汇总工具也已实现。来源层当前登记 31 个来源家族和 13 个海外核心实体。2026-09-03 重新实测原有 4 个 YouTube 频道 RSS 均恢复 HTTP 200，现已作为 shadow 发现链路重新启用，并新增 Smol AI News RSS；YouTube 使用频道 RSS与匿名 `yt-dlp` 的重点频道/有限主题搜索补充传播数据。按用户最新决定，X 直采已从运行配置与生产门禁移除，X 等跨平台线索由 AI HOT 承担并按原始链接/作者去重。Bluesky 匿名搜索因实跑 403 继续保持显式禁用。source-only 运行仍保存可搜索 HTML、原始 JSONL 和传播门槛预判；生产门禁改为要求 AI HOT、YouTube RSS、Reddit 和 GitHub Direct，不要求 X 或 YouTube 官方凭据。

仍属上线门禁而非代码缺口：连续 7 个自然日来源 shadow、连续 3 天真实 AI 编辑质量 shadow、测试 webhook/本地卡片验收、云端 smoke、Commit/Push 与调度启用。GitHub Actions 路线不再要求 X/YouTube 运行时凭据；YouTube RSS 与匿名 `yt-dlp` 必须通过稳定性验收，失败时明确进入观察或来源降级。08:30/15:30 的独立纯来源 Windows 任务模板已准备好，默认不注册，必须显式 `-Enable`。生产 workflow、Windows 任务和生产飞书仍保持禁用。具体执行与回滚见 `docs/runbooks/horizon-intelligence-radar-rollout.md`。

---

### Task 0: 先通过来源覆盖与稳定性门禁

**Files:**
- Create: `data/source_registry.json`
- Create: `src/processing/source_health.py`
- Modify: `data/config.github.json`
- Modify: `src/orchestrator.py`
- Test: `tests/test_source_registry.py`
- Test: `tests/test_source_health.py`

- [x] 1. 将全部现有来源登记为 `confirm/discover/backfill`，记录决策分类、权威等级、主备关系、更新频率、字段契约、增量水位、成本和事实状态上限；用测试锁定空 GitHub、关闭 X/Reddit、失效 YouTube feed 和逻辑错误热榜等已知缺口。
- [x] 2. 补齐 P0 来源：核心 AI 产品官方源、AI HOT 跨平台线索、YouTube 频道 RSS 加匿名 `yt-dlp` 传播数据、GitHub Releases/Search 双通道和少量 Reddit 社区；X/YouTube 官方适配器代码保留但不作为当前运行依赖。
- [x] 3. 实现来源健康状态 `healthy/stale/degraded/failed/disabled/coverage_gap`，同时验证 HTTP、schema、业务码、数据新鲜度和异常空结果；主来源失败时只切换已登记的备用链路。
- [ ] 4. 实现每源增量 watermark、重试和缺口补采；启动连续 7 天 source-only shadow，记录独特候选率、重复率、正文成功率、最终入选率和单位入选成本。
- [x] 5. 运行 `uv run pytest tests/test_source_registry.py tests/test_source_health.py tests/test_fetch_reporting.py -q`；四类决策的来源设计、字段契约和已知缺口明确后可开始 Task 1，生产启用仍须等待 7 天来源结果。

### Task 1: 固化新领域模型和兼容配置

**Files:**
- Modify: `src/models.py`
- Modify: `data/config.example.json`
- Modify: `data/config.github.json`
- Test: `tests/test_intelligence_models.py`
- Test: `tests/test_github_runtime_config.py`

- [x] 1. 写失败测试：旧配置仍可加载；新配置支持四类决策、上午/下午运行模式、候选档案路径和规则版本。
- [x] 2. 增加 `DecisionLane`、`CandidateStatus`、`EvidenceStatus`、`ReasonCode`、`CandidateScore`、`CandidateRecord` 和 `DeliveryRecord`，保持 `ContentItem` 的现有契约可用。
- [x] 3. 用嵌套 `IntelligenceAnalysis` 承载主张、事件身份、决策分类和评分维度，避免继续把字段平铺进 `ContentAnalysis`。
- [x] 4. 为所有新字段提供向后兼容默认值，并在配置中默认关闭正式投递和生产状态写入。
- [x] 5. 运行 `uv run pytest tests/test_intelligence_models.py tests/test_github_runtime_config.py -q`，确认新旧配置路径均通过。

### Task 2: 建立候选账本和状态迁移

**Files:**
- Create: `src/storage/candidate_store.py`
- Modify: `src/storage/manager.py`
- Modify: `src/models.py`
- Test: `tests/test_candidate_store.py`

- [x] 1. 写失败测试覆盖 `discovered → enriched → observing/eligible → selected/held/merged/rejected/expired`，并拒绝非法回退。
- [x] 2. 实现版本化 JSONL 或等价追加式候选账本，按 `candidate_id` 保留最新快照，同时保留状态转换时间、原因码和规则版本。
- [x] 3. 增加原子写入、损坏文件隔离和只读恢复；处理失败保存为 `processing_error`，不得伪装成质量淘汰。
- [x] 4. 实现旧 `digest_selection_state.json` 的只读导入，只建立冷却基线，不生成历史候选或补发内容。
- [x] 5. 运行 `uv run pytest tests/test_candidate_store.py tests/test_storage.py -q`，并用临时目录验证重复运行幂等。

### Task 3: 统一来源观测和字段契约

**Files:**
- Create: `src/processing/source_observation.py`
- Modify: `src/orchestrator.py`
- Modify: `src/scrapers/base.py`
- Modify: `src/scrapers/aihot.py`
- Modify: `src/scrapers/twitter.py`
- Modify: `src/scrapers/bilibili.py`
- Test: `tests/test_source_observation.py`
- Test: `tests/test_fetch_reporting.py`

- [x] 1. 为官方、技术一手、社交传播、媒体聚合、国内热点和平台规则分别编写字段契约测试。
- [x] 2. 将抓取结果标准化为来源原始性、作者、发布时间、首次发现时间、互动字段完整度、平台原生 ID 和原始链接。
- [x] 3. 区分 `source_empty`、`source_failed`、`parse_failed` 和 `rate_limited`，并保留其他来源继续运行。
- [x] 4. 让 AI HOT 和媒体聚合默认产生二级线索；存在原始链接时建立证据关系，不把聚合标题当成官方事实。
- [x] 5. 运行 `uv run pytest tests/test_source_observation.py tests/test_fetch_reporting.py tests/test_aihot.py tests/test_twitter.py tests/test_bilibili.py -q`。

### Task 4: 身份、事件版本和语义主题去重

**Files:**
- Create: `src/processing/candidate_identity.py`
- Modify: `src/orchestrator.py`
- Modify: `src/processing/editorial_selection.py`
- Test: `tests/test_candidate_identity.py`
- Test: `tests/test_cross_source_duplicates.py`

- [x] 1. 写失败测试覆盖平台原生 ID、规范化 URL、同一事件多来源、同工具同任务教程和同一产品的两个独立更新。
- [x] 2. 实现 `source_item_id`、`canonical_url`、`event_key`、`event_version` 和 `editorial_topic_key` 的分层身份。
- [x] 3. 合并同一事件时选择证据最强的主记录，并把其他来源保存在 `evidence_refs` 和 `merged_into`。
- [x] 4. 仅当新功能、新数据、新证据、新观点或传播状态发生实质变化时增加事件版本，标题换写不算更新。
- [x] 5. 运行 `uv run pytest tests/test_candidate_identity.py tests/test_cross_source_duplicates.py tests/test_editorial_selection.py -q`。

### Task 5: 证据抽取和事实状态

**Files:**
- Create: `src/processing/evidence.py`
- Modify: `src/ai/prompting/analysis.py`
- Modify: `src/ai/analyzer.py`
- Modify: `src/models.py`
- Test: `tests/test_evidence.py`
- Test: `tests/test_analyzer.py`

- [x] 1. 写失败测试覆盖官方确认、两个独立来源印证、单一媒体报道、单条社交传闻和相互冲突的来源。
- [x] 2. 从正文提取原子主张与支撑片段，生成 `confirmed/corroborated/reported/unverified/disputed`，禁止只凭摘要升级状态。
- [x] 3. 按来源独立性建立证据组，同一媒体转载链不能被误算为多个独立来源。
- [x] 4. AI 输出必须通过 Pydantic 校验；失败时候选保留为 `analysis_failed`，可重试且不进入精选。
- [x] 5. 运行 `uv run pytest tests/test_evidence.py tests/test_analyzer.py tests/test_prompting.py -q`。

### Task 6: 实现传播成熟度和观察池

**Files:**
- Create: `src/processing/social_quality.py`
- Modify: `src/processing/engagement.py`
- Modify: `src/orchestrator.py`
- Test: `tests/test_social_quality.py`
- Test: `tests/test_engagement.py`

- [x] 1. 从质量工作区迁移行为测试：未成熟进入观察、成熟低传播淘汰、互动字段不足淘汰、AI 高分不能绕过硬门槛、官方内容豁免播放量。
- [ ] 2. 以平台、账号体量、内容年龄和历史基线计算相对传播表现，绝对阈值只作为安全底线。
- [x] 3. 为观察项保存首次/最近观测、观测次数、播放互动快照和截止时间，成熟后重新进入准入判断。
- [x] 4. 对 X、YouTube、B站及后续社交源使用可配置策略；未知平台没有可信传播数据时不伪造热度。
- [x] 5. 运行 `uv run pytest tests/test_social_quality.py tests/test_engagement.py tests/test_bilibili.py -q`。

当前第 2 项已实现平台内相对分位与同条内容跨次增长快照；按账号体量归一化需要积累足够历史样本后再校准，暂不伪造基线，也不阻断 source-only 稳定性验证。

### Task 7: 按用户决策分类和评分

**Files:**
- Create: `src/processing/intelligence_analysis.py`
- Modify: `src/ai/prompting/analysis.py`
- Modify: `src/orchestrator.py`
- Modify: `profiles/pangmen-ai-tech-radar/analysis.md`
- Modify: `profiles/pangmen-topic-radar/analysis.md`
- Test: `tests/test_intelligence_analysis.py`

- [x] 1. 写失败测试：四类决策只有一个主分类；融资/人事新闻只有产生直接产品、技术、用户或平台影响时才准入。
- [x] 2. 生成决策影响、旁门相关性、实质新颖性、证据质量、可实测性、传播质量、新鲜度和差异性八个原始分。
- [x] 3. 为四类使用不同权重，先执行硬门槛再评分，任何综合分都不能覆盖证据、成熟度或相关性硬拒绝。
- [x] 4. 每条生成“为什么值得看”的判断摘要和“发生了什么”的内容摘要，并保留原始证据链接。
- [x] 5. 运行 `uv run pytest tests/test_intelligence_analysis.py tests/test_analyzer.py tests/test_category_wiring.py -q`。

### Task 8: 重构最终选择、多样性和跨日新鲜度

**Files:**
- Create: `src/processing/intelligence_selection.py`
- Modify: `src/processing/editorial_selection.py`
- Modify: `src/orchestrator.py`
- Test: `tests/test_intelligence_selection.py`
- Test: `tests/test_balanced_digest.py`

- [x] 1. 写失败测试覆盖产品/能力优先顺序、目标 8–12 但不凑数、热门内容无固定上限、单作者/来源/平台/主题集中控制。
- [x] 2. 只让 `eligible` 候选进入排序；选择顺序为主价值、证据、新鲜度、差异性和稳定 ID。
- [x] 3. 应用作者、二级来源、平台和主题软配额；确有多个独立重大事件时允许越过软配额，并写解释。
- [x] 4. 将合格但未选内容转为 `held`，重复项转为 `merged`；跨日冷却以事件版本为单位。
- [x] 5. 运行 `uv run pytest tests/test_intelligence_selection.py tests/test_balanced_digest.py tests/test_editorial_selection.py -q`。

### Task 9: 生成上午完整精选和下午增量

**Files:**
- Create: `src/processing/delivery_selection.py`
- Modify: `src/orchestrator.py`
- Modify: `src/models.py`
- Test: `tests/test_delivery_selection.py`

- [x] 1. 写失败测试覆盖 09:00 完整精选、16:00 仅增量、上午事件不重复、实质更新可再次发送和零合格内容不发送。
- [x] 2. 上午从全部当日 `eligible` 候选选择；下午只比较上午投递水位之后的新候选和新事件版本。
- [x] 3. 将每次投递记录到 `DeliveryRecord`，包含时间窗、事件版本、展示层级和内容指纹。
- [x] 4. 明确返回 `send/no_qualified_updates/collection_degraded/pipeline_failed`，失败不能被错误描述为无更新。
- [x] 5. 运行 `uv run pytest tests/test_delivery_selection.py tests/test_daily_run_gate.py -q`。

### Task 10: 更新摘要、飞书卡片和本地候选导出

**Files:**
- Modify: `src/ai/summarizer.py`
- Modify: `src/services/webhook.py`
- Modify: `src/services/webhook_cli.py`
- Create: `src/storage/candidate_export.py`
- Test: `tests/test_summarizer.py`
- Test: `tests/test_webhook.py`
- Test: `tests/test_candidate_export.py`

- [x] 1. 写快照测试覆盖四类顺序、双摘要、证据状态、来源链接、待核实标记和下午增量标题。
- [x] 2. 让一张卡优先展示完整精选；达到容量预算时将仍达精选标准的尾部项目下沉到“查看更多”，不得静默截断。
- [x] 3. 生成可搜索的本地 JSONL/HTML 候选导出，覆盖观察、暂缓、合并、淘汰和处理失败；不接入飞书 Base。
- [x] 4. 所有测试使用本地 payload 和假 webhook；不得读取真实 secret 或发送网络请求。
- [x] 5. 运行 `uv run pytest tests/test_summarizer.py tests/test_webhook.py tests/test_webhook_cli.py tests/test_candidate_export.py -q`。

### Task 11: 建立完整 diagnostics 和成本账本

**Files:**
- Create: `src/diagnostics/intelligence_report.py`
- Modify: `src/orchestrator.py`
- Modify: `src/ai/tokens.py`
- Test: `tests/test_intelligence_diagnostics.py`
- Test: `tests/test_fetch_reporting.py`

- [x] 1. 写失败测试覆盖来源健康、阶段漏斗、原因码、合并组、四类数量、集中度、上午/下午差异、AI 成本和卡片容量。
- [x] 2. 用同一 `run_id` 串联抓取、证据、分析、选择和投递记录，确保每个候选最终都有状态或失败原因。
- [x] 3. 记录模型调用次数、token、估算成本、缓存命中和降级；规则预筛淘汰项不得产生 AI 调用。
- [x] 4. 为 `no_qualified_updates`、`collection_degraded`、`pipeline_failed` 生成不同的机器状态和人类摘要。
- [x] 5. 运行 `uv run pytest tests/test_intelligence_diagnostics.py tests/test_fetch_reporting.py tests/test_analyzer.py -q`。

### Task 12: 迁移平台热点与 AI 质量升级能力

**Files:**
- Modify: `src/scrapers/platform_trends.py`
- Modify: `src/processing/intelligence_selection.py`
- Modify: `src/processing/social_quality.py`
- Modify: `src/orchestrator.py`
- Test: `tests/test_platform_trends.py`
- Test: `tests/test_migration_behavior.py`

- [x] 1. 从 `e4cd9f2` 和 `6c7e8f3` 生成行为差异清单，写测试锁定要保留的趋势状态、跨栏唯一展示、爆点评分、传播门槛、观察池和来源限制。
- [x] 2. 按文件和行为选择性迁移，不 merge 整分支，不触碰两个脏工作区中的 RSS identity、招聘、矿业或预览文件。
- [x] 3. 将同义阈值收口到新策略对象；旧 selector 只作兼容入口，禁止新旧规则重复执行。
- [x] 4. 对决定废弃的旧行为写负向测试和迁移说明，确保旧状态只建立冷却基线、不触发历史补发。
- [x] 5. 运行 `uv run pytest tests/test_platform_trends.py tests/test_migration_behavior.py tests/test_cross_source_duplicates.py -q`。

### Task 13: 调度、影子运行、上线门禁和回滚

**Files:**
- Modify: `.github/workflows/daily-summary.yml`
- Modify: `scripts/register_windows_daily_trigger.ps1`
- Modify: `scripts/trigger_daily_horizon.ps1`
- Create: `scripts/run_shadow_validation.py`
- Create: `docs/runbooks/horizon-intelligence-radar-rollout.md`
- Test: `tests/test_shadow_validation.py`
- Test: `tests/test_windows_daily_trigger.py`
- Test: `tests/test_daily_run_gate.py`

- [x] 1. 写测试证明影子模式绝不发送 webhook、不写生产 state，上午和下午任务可独立运行且同日增量水位正确。
- [x] 2. 为 workflow 增加显式 `shadow/morning/afternoon` 模式，但保持 schedule、Windows 任务和 webhook 默认禁用；任何 enable 都留到单独授权步骤。
- [ ] 3. 连续 3 天用离线或影子输入产出评审表：重大漏报、值得看比例、明显垃圾、集中度、来源故障和估算成本。
- [ ] 4. 通过后先生成本地单卡预览，再做一次无生产写入的云端 smoke；用户明确确认后才分阶段启用 09:00 与 16:00。
- [ ] 5. 运行 full pytest、compileall、配置模型校验和 `git diff --check`，执行回滚演练并停止在 Commit/Push/启用之前报告结果。

---

## 执行顺序与停点

- Phase 0：Task 0，先补来源并通过开发门；7 天来源稳定性观察与后续开发并行，未通过不得进入生产。
- Phase 1：Task 1–5，建立数据契约、候选账本、身份和证据。
- Phase 2：Task 6–8，建立观察、分类、评分和最终选择。
- Phase 3：Task 9–11，建立双时段投递、展示、候选导出和 diagnostics。
- Phase 4：Task 12，选择性迁移旧分支能力。
- Phase 5：Task 13，只做影子验证和上线准备。

每个 Phase 完成后先回报测试与 diff，再进入下一阶段。来源稳定性使用连续 7 天 source-only shadow；精选质量仍使用已确认的连续 3 天影子评审。任何 Commit、Push、Actions、真实 AI、飞书、生产状态写入或调度启用均需要用户另行明确授权。

# Horizon 信息源覆盖审计与补充方案

**审计日期：** 2026-09-03

**状态：** P0 架构已实施，连续 7 天来源影子验收尚未完成

**结论：** 当前来源可以维持一份基础 AI 日报，但不足以稳定实现已确认的“海外优先、产品情报为主、热门内容与技术前沿并重”的信息雷达。实施必须先通过来源覆盖门禁，再建设候选池、评分和展示。

**2026-09-03 实施进展：** 已新增统一来源注册表、13 个海外核心实体注册表、GitHub Releases/Search、Reddit、X、YouTube、Bluesky 匿名公开作者流、8 个海外官方产品页、3 个海外行业媒体 RSS、主备降级与来源健康账本。复查发现原有 4 个 YouTube channel feed 当前均返回 404，已全部停用并将 `youtube-rss` 标为显式覆盖缺口；YouTube 默认优先 Data API，在本机无 Key 时自动降级到匿名 `yt-dlp`，仍保留重点频道、少量主题搜索和传播数据。X 默认优先官方 API，在本机无 Token 时使用 OpenCLI 复用已登录浏览器，继续执行重点账号合并查询和两条主题查询。Bluesky 先用 5 个高信号作者/产品账号作为免费补充层；匿名搜索实跑返回 403，因此已明确禁用搜索，不把不可用能力伪装成备用。开发/影子覆盖检查已通过；生产覆盖仍故意不通过，须完成连续 7 天影子运行，并根据最终运行位置选择本地 CLI 链路或云端 API 凭据，再将通过验收的 shadow 来源显式晋级后放行。

**最新 source-only shadow（2026-09-03 13:04，北京时间）：** 在未设置 X Token 和 YouTube Key 的本机环境中采到 122 条、122 个唯一 ID、0 个本轮重复；X OpenCLI 贡献 30 条，YouTube `yt-dlp` 贡献 21 条近期视频，Bluesky、RSS、Reddit 和 GitHub 继续正常贡献。YouTube 本地主题搜索已加入 `after:日期` 约束，修复默认相关性结果偏旧、再被 48 小时时效门槛大量过滤的问题；X 查询同时排除转推和回复，避免低价值对话占据候选。本轮健康统计为 32 `healthy`、12 `degraded`、2 `disabled`、0 `failed`，来源账本没有未闭合缺口。12 个 `degraded` 中，9 个来自 X/YouTube 成功启用本地备用，3 个来自受限官网由登记的搜索备用接管；2 个 `disabled` 是实跑证实匿名不可用后关闭的 Bluesky 搜索。该轮还生成了可搜索 HTML 和原始 JSONL 候选快照，使 7 天验收可以检查标题、链接、作者和传播数据，而不是只检查来源是否存活。规则预判得到 61 条非社交来源继续处理、11 条社交内容通过、25 条观察、25 条低传播淘汰；传播门槛在 AI 调用前执行，低传播噪声不消耗模型成本。该结果证明“本地低成本路线”已具备完整连通性，但只有 1 个自然日证据，尚不能证明连续稳定性；OpenCLI 登录态和 `yt-dlp` 网页结构风险必须在 7 天 shadow 中验证。

## 1. 本次核验范围

- 读取 `origin/main@907395b` 的 `data/config.github.json`、来源模型和 scraper 实现。
- 对比 `e4cd9f2`、`6c7e8f3` 的来源相关文件范围；两个分支主要补充平台趋势和筛选行为，没有补齐海外核心来源结构。
- 对 26 个已启用 RSS 做匿名 GET 可达性和 feed 结构抽查。
- 对 Hacker News、Hugging Face、OSSInsight、AI HOT、Bilibili 和 DailyHotAPI 做匿名只读抽查。
- 核验 YouTube Data API、X API、Bluesky public AppView 和 GitHub Releases 的官方公开能力说明。
- 未调用真实 AI、付费 API、飞书、Actions 或生产状态。

## 2. 当前来源现状

### 2.1 配置层

生产 `main@907395b` 的原始配置曾启用：

- 26 个 RSS；
- Hacker News；
- OSSInsight；
- 1 个 Telegram 频道；
- Google News；
- Bilibili 搜索；
- AI HOT；
- Hugging Face 模型与论文；
- 平台趋势聚合；
- 平台变化 watcher。

生产 `main@907395b` 当时未启用或为空：

- GitHub 直接监控配置为空；
- X/Twitter 关闭；
- Reddit 关闭；
- GDELT 关闭；
- 小红书和微信的平台热榜 provider 关闭，配置中标记为需要外部公开 provider。

### 2.2 实际抽查

| 检查项 | 结果 | 判断 |
|---|---|---|
| 26 个 RSS | 24 个返回 HTTP 200 且识别为 feed | RSS 主体可用，但不代表内容覆盖完整 |
| Google Workspace Updates | 本机请求超时或客户端状态异常 | 本次结果不确定，需要独立重试和备用入口 |
| 4 个 YouTube channel feed | 本次复查均返回 404 | 已全部停用；Data API 的 uploads playlist 成为待凭据验证的重点频道链路 |
| TechCrunch AI / The Verge AI / MIT Technology Review AI RSS | 均返回 HTTP 200 且解析出近期条目 | 已作为二级发现源进入 shadow，事实状态最高只能为 `reported` |
| HN / HF Models / HF Papers / OSSInsight | 本次均返回 200 JSON | 可作为技术和社区发现层，不应替代官方原文 |
| AI HOT items / hot topics | 本次均返回 200 JSON | 可作为聚合发现层，不能作为唯一事实来源 |
| Bilibili 搜索 | 本次返回 200 JSON | 可继续使用，但需要传播成熟度与字段完整性门槛 |
| DailyHotAPI 抖音 | HTTP 200，业务码 200 | 本次可用，仍属实验性聚合源 |
| DailyHotAPI 微博 | HTTP 200，但业务码 500、消息“获取失败” | 证明健康检查不能只看 HTTP 状态 |
| ALAPI 热榜 | 未调用 | 需要运行时 token，当前只确认配置了商业聚合备用路径 |

## 3. 是否足以实现目标

| 用户决策 | 当前能力 | 结论 | 主要缺口 |
|---|---|---|---|
| 产品与能力变化 | 覆盖 OpenAI、Google、Microsoft 365、Figma、Notion、GitHub/Claude Code 及少数开源工具 | 部分满足 | 核心厂商和产品名单过窄；不少来源依赖 Google News 间接发现 |
| 热门内容与传播机会 | AI HOT、HN、B站、4 个 YouTube RSS、国内聚合热榜 | 不满足 | X 关闭；YouTube 无统计数据且有失效频道；Reddit 关闭；早期上升判断缺少重复观测 |
| 技术前沿与开源 | Hugging Face、OSSInsight、HN、少量 GitHub Releases RSS | 部分满足 | GitHub 直接配置为空；缺少重点组织/仓库和新项目搜索双通道 |
| 平台 AI 变化与机会 | 抖音、小红书、B站、微信规则 watcher；部分国内热榜 | 部分满足 | 平台功能更新覆盖不完整；热榜依赖聚合；小红书/微信热榜为空缺 |

因此，生产系统当前仍会发生三类问题：

1. AI HOT 和 Google News 贡献过高，发现结果集中于二级来源。
2. 能看到 YouTube 新视频，却不能稳定判断哪些真的在快速传播。
3. 官方更新和技术项目只覆盖少数已知对象，重要变化可能从名单之外漏掉。

隔离升级工作树已经补上结构和第一批来源，但“代码已接好”不等于“来源已验收”。当前生产门禁会明确要求以下关键来源都从 `shadow` 晋级为 `active`，不能再由普通聚合源凑数量：

- 热门内容：`x-official`、`youtube-data`、`reddit-community`；
- 技术前沿：`github-direct`；
- 产品变化：`official-product-rss`，并逐实体检查确认层 + 发现层；
- 平台变化：`platform-changes-official`。

只要其中一个关键来源仍缺凭据、连续失败或未通过 7 天 shadow，生产投递就保持关闭。

## 4. 目标来源架构

每个重要信息目标都采用三层来源，而不是依赖单个平台：

1. **确认层：** 官方新闻、更新日志、文档、发布页、代码仓库和平台规则原文。
2. **发现层：** X、YouTube、Bluesky、GitHub Search、Hacker News、Reddit、B站和可信创作者。
3. **补漏层：** AI HOT、Google News、Newsletter、商业热榜和其他聚合器。

发现层找到线索后必须尽量回溯确认层。补漏层不能把二手标题直接升级为已确认事实。

## 5. P0 必补来源

### 5.1 官方产品与能力变化

先建立“核心实体注册表”，不把厂商名称散落在多个 Google News 查询中。

第一批至少覆盖：

- 海外基础模型与实验室：OpenAI、Anthropic、Google DeepMind / Gemini、Meta AI、xAI、Microsoft AI；
- 高频生产力与创作产品：Adobe Firefly、Canva、Cursor、Perplexity、Runway、Notion、Figma、GitHub Copilot；
- 国内重点模型与产品：DeepSeek、Qwen/通义、豆包/火山引擎、Kimi、智谱 GLM、腾讯混元/元宝、百度文心、MiniMax/海螺、可灵、即梦、飞书 AI、WPS AI、Coze、TRAE；
- 高频开源工作流：Dify、n8n、Coze Studio、Cherry Studio 及后续通过采用信号进入的项目。

每个实体至少登记：

- 一个官方主来源：RSS、更新日志、文档变更、GitHub Release 或官方发布页；
- 一个发现备用：官方账号、Google News 定向搜索或可信行业媒体；
- 支持的决策分类；
- 预期更新频率和“多久无更新才算异常”；
- 无稳定官方入口时的事实状态上限。

没有公开、稳定的官方源时，只能将二手发现标记为 `reported` 或 `unverified`，不能假装完成官方覆盖。

### 5.2 X 热门 AI 内容

当前 `twitter.enabled=false`，现有实现使用 Apify，无法承担核心唯一链路。

目标方案：

- 主链路：X 官方 API 的近期搜索和重点账号时间线；
- 本地低成本链路：OpenCLI 复用已登录浏览器执行同一组窄查询，只读取公开内容；登录态失效时明确失败，不静默改用聚合源；
- 查询策略：重点账号列表、产品名/模型名、AI 视频/办公/创作/编程等窄主题查询；
- 统计字段：发布时间、作者、关注者基线、喜欢、回复、转发、引用和曝光字段中公开可得的部分；
- 成本控制：每日去重缓存、只读取新 ID、限制查询数和返回量、设置月度预算上限；
- 备用链路：AI HOT 中的 X 条目和创作者自有博客/Newsletter；
- Apify：保留为可替换 provider 或短期 shadow 对照，不作为不可替代依赖。

第一阶段不追求抓完整个 X。当前以 19 个高信号账号合并成一次查询，叠加两条窄主题查询；每次最多读取 30 条帖子。按 X 当前公开的每条 Post read 价格，理论上限约为 0.15 美元/次，真实费用仍以 Developer Console 为准。连续 7 天后按独特候选率和入选率增删账号。

本机已经实测 OpenCLI 三条查询成功，并在 48 小时窗口贡献 30 条候选。它把首阶段 X 读取成本降为 0，但依赖本机 Chrome 登录态，适合本地 shadow 和本机调度，不适合作为 GitHub Actions 云端默认链路。X 官方 API 保留为云端或更高稳定性需求的可选升级。

### 5.3 YouTube 热门 AI 视频

当前 4 个频道 RSS 只能发现上传，且一个 feed 已返回 404，无法完成传播判断。

目标方案：

- uploads playlist 负责低成本、可靠地发现重点频道新视频；失效 RSS 不再作为备用；
- YouTube Data API `videos.list(part=statistics)` 批量补播放、点赞和评论统计；
- 本机无 Key 时由 `yt-dlp` 匿名读取频道页和有限主题搜索，保留发布时间、播放、点赞、评论与视频时长；页面结构失效时明确失败；
- 对候选在 2–3 个时间点重复观测，计算增长速度和相对频道基线；
- `search.list` 只用于少量主题发现，并设置每日调用上限。YouTube 2026-06 更新后的官方配额模型将其单列为每日调用桶、每次 1 单位；实现已按当前规则修正，但仍保留 Horizon 自身的每次运行预算上限；
- 频道名单从 4 个扩充为分层 watchlist：产品实测、AI 新闻解释、技术前沿、创作工具和大众 AI 内容；
- 每月根据独特候选率和最终入选率更新名单，不以粉丝量直接决定去留。

### 5.4 GitHub 与开源项目

当前 `github: []` 是技术雷达的硬缺口。

目标方案：

- 已知项目：GitHub Releases API/Atom，监控重点仓库和组织；
- 新项目：GitHub Search API 按创建时间、更新时间、主题和星标增速发现；
- 趋势补充：保留 OSSInsight，但只作为第三方趋势信号；
- 采用证据：Hugging Face、HN、X/YouTube 讨论和仓库增长作为交叉信号；
- 不使用不存在的“GitHub Trending 官方 API”作为设计前提。

### 5.5 社区和大众热点

- 启用少量高信号 Reddit 社区作为海外讨论发现层，优先公开 RSS/合规 API；不可用时标记缺口，不绕过登录或反爬限制。
- 保留 Hacker News，但区分技术社区热度和大众传播热度。
- B站继续承担国内视频验证，不以刚发布内容直接参与精选。
- 微博、抖音等热榜至少配置两个 provider 或一个 provider 加可观测备用；业务码失败必须触发降级。
- 小红书和微信在没有稳定合规 provider 前明确标记 `coverage_gap`，不能用搜索摘要伪装成完整热榜。

## 6. P1 补充来源

- 高质量独立研究者和 Newsletter：保留现有五个，并按独特贡献率扩充或淘汰。
- 平台功能与创作者政策：在现有规则 watcher 之外，增加 YouTube、X、Meta/TikTok 及国内重点平台的官方产品/创作者更新页。
- 竞品内容动向：从 X、YouTube、B站和公开 Newsletter 中建立独立账号观察列表，不与官方事实源混用。
- 新产品发现：Product Hunt 只作发现层，并用官网、文档、真实演示或用户采用证据复核。

## 7. 来源注册表

每个来源必须拥有统一配置，而不是只写一个 URL：

| 字段 | 作用 |
|---|---|
| `source_id` | 稳定身份 |
| `decision_lanes` | 服务哪些用户判断 |
| `role` | `confirm/discover/backfill` |
| `authority` | `official/primary/secondary/aggregator` |
| `access_method` | RSS、API、page diff、search 等 |
| `fallback_source_ids` | 主来源失败后的备用链路 |
| `expected_cadence` | 预期更新频率 |
| `required_fields` | 结构有效的最低字段 |
| `watermark_strategy` | 增量断点和回看窗口 |
| `health_policy` | 超时、重试、连续失败和陈旧阈值 |
| `cost_policy` | 每日/月度调用和费用上限 |
| `fact_ceiling` | 此来源最高可支持的事实状态 |

## 8. 健康监控

来源健康不能只看 HTTP 200。每次抓取至少检查：

- 传输是否成功；
- 响应是否符合 schema；
- 业务码是否成功；
- 数据时间是否新鲜；
- 是否出现异常空结果；
- 原始链接和发布时间是否完整；
- 与过去 7/30 天相比产出是否异常；
- 主来源失败时备用是否接管；
- 本次漏掉的时间窗口能否补采。

统一状态：`healthy / stale / degraded / failed / disabled / coverage_gap`。

DailyHotAPI 微博的“HTTP 200 + 业务码 500”必须成为回归夹具，防止以后继续把逻辑失败记作成功。

## 9. 来源准入门禁

任何新来源进入生产前必须通过：

1. 连续 7 天 source-only shadow，至少覆盖一个周末；
2. 没有静默断档，失败和空结果可区分；
3. 增量水位和补采可复现，不重复倒灌旧内容；
4. 原始字段、链接、时间和事实状态符合契约；
5. 计算独特候选率、重复率、正文成功率、最终入选率和单位入选成本；
6. 主要来源失败时，备用链路能接管或明确报出 coverage gap。

原先确认的连续 3 天影子运行继续用于精选质量验收；来源稳定性单独使用 7 天窗口，因为 3 天不足以观察周末、低更新频率来源和短暂故障。

## 10. 实施优先级

1. 先完成来源注册表、现状验证和覆盖缺口清单。
2. 再补官方产品源、X、YouTube statistics、GitHub 直接监控和 Reddit 小规模发现。
3. 再建立来源健康、断点补采和主备切换。
4. 来源达到最低覆盖后，才实施候选池、证据、评分和投递。
5. 最后通过 7 天来源 shadow 和 3 天精选 shadow，决定是否恢复生产。

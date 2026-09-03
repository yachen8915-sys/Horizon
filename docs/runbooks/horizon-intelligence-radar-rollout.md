# Horizon 情报雷达影子验收与上线手册

## 当前安全状态

- 工作分支基于 `907395b`，所有改动位于独立 worktree。
- `intelligence.enabled=true`，但 `delivery_enabled=false` 且 `run_mode=shadow`。
- GitHub Daily Summary workflow 与 Windows `Pangmen Daily Radar` 继续禁用。
- 工作流文件已定义 09:00/16:00 两个模式，但远端 workflow 仍为 `disabled_manually`；本地注册脚本默认只输出准备结果，缺少 `-Enable` 时不会创建任务。
- 本手册中的上线步骤只用于后续执行；没有用户明确确认时不得 Commit、Push、启用任务、调用真实 AI 或发送飞书。

## 1. 来源稳定性先行

每天至少执行一次纯来源影子采集：

```powershell
uv run python scripts/run_source_shadow.py --config data/config.github.json --hours 24
uv run python scripts/run_shadow_validation.py --state data/shadow/source_health_state.json
```

它只访问已标记为 shadow 的来源，不调用 AI、不投递、不写生产 state。状态只写入 `data/shadow/`。

本机双时段观察任务已经准备好，但默认不会注册：

```powershell
# 安全预览；不会修改计划任务
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/register_windows_source_shadow.ps1 -WhatIf

# 只有得到明确授权后才执行；注册 08:30/15:30 两次纯来源 shadow
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/register_windows_source_shadow.ps1 -Enable
```

该任务使用交互式用户会话，以便 OpenCLI 复用浏览器登录态；若电脑未登录、浏览器登录失效或机器未能唤醒，验收记录会显示缺跑或 `fallback_unavailable`，不能补写成成功。

云端 GitHub Actions 路线需要补齐的运行时凭据：

- `YOUTUBE_DATA_API_KEY`：4 个重点频道 uploads playlist、2 条主题搜索与互动统计；缺失时明确记为 `missing_credentials`。
- `X_BEARER_TOKEN`：19 个重点账号合并查询 + 2 条主题查询；缺失时明确记为 `missing_credentials`。

本地低成本路线不强制要求上述凭据：YouTube 缺 Key 时用匿名 `yt-dlp`；X 缺 Token 时用 OpenCLI 复用本机浏览器登录态。两条链路都记为 `degraded/fallback_used`，不能冒充官方 API 健康；OpenCLI 登录失效、命令缺失或页面结构变化会转成 `failed/fallback_unavailable`。本地影子通过 7 天门后，才允许把它作为本机生产路线申请启用。

Bluesky 不需要凭据，当前限定为 5 个已实测可用的公开作者流。匿名 `searchPosts` 在真实环境返回 403，相关查询保持显式 `disabled`；它是免费的补充信号，不替代 X/YouTube 的生产门禁。

来源验收至少覆盖连续 7 个自然日，并使用每天最后一次运行计算。必须人工检查：

- 四个决策分类均有发现源，核心产品实体同时具备确认源和发现源；
- 关键官方源不能连续失败两天；主页面受限时必须显示 `degraded/fallback_used`，不能伪装健康；
- X、YouTube 等付费或配额型来源的失败、限流和成本可见；
- 重复率、独特候选率、新增率没有异常跳变；
- 任何 `failed` 都能追溯到来源、原因码和缺口起点。

当前一次真实影子结果仅代表连通性，不构成 7 天稳定性验收：2026-09-03 13:04 的最新一次为 122 条、122 个唯一项，健康 32、降级 12、禁用 2、失败 0，开放来源缺口为 0。X OpenCLI 贡献 30 条，YouTube `yt-dlp` 贡献 21 条近期视频；9 个 X/YouTube 子来源均成功使用本地备用。YouTube 本地主题搜索已加入 `after:日期` 约束，避免默认相关性搜索返回旧视频后被时效门槛全部丢弃；X 查询同时排除转推和回复，减少对话噪声。该轮已经同时生成可搜索 HTML 和原始 JSONL 候选快照，7 天后可检查内容质量而不只看健康计数。规则预判结果为 61 条非社交来源继续处理、11 条社交内容通过、25 条观察、25 条低传播淘汰，这些门槛在 AI 调用前执行。B站社区公约的 A/B 壳页面结构已适配并在真实请求中恢复 `healthy`。`degraded` 表示备用链路接管成功，不等于主链路健康；7 天期间必须重点观察浏览器登录态、CLI 版本和页面结构变化。

## 2. 三天编辑质量影子

来源门通过后，得到单独授权才可调用真实 AI，连续 3 天运行情报 shadow：

```powershell
uv run horizon --intelligence-run-mode shadow --hours 24
```

每天回读 `data/candidates/runs/` 中的候选 JSONL、可搜索 HTML 和 diagnostics。不得发送飞书。评审至少记录：

- 重大漏报；
- 值得看比例和明显垃圾数；
- 重复、未成熟、证据不足是否被正确分层；
- 单作者、来源、平台、主题集中度；
- 各来源入选贡献；
- AI 调用次数、输入/输出 token；估算成本若未配置价格须明确为未知，不能写成 0。

通过标准：每天 8–12 条是目标而非硬凑数；任一领域只要出现真正重要的新信息即可发送；产品与行业变化为主体，内容选题与大众热点为补充；“待核实高热”最多 3 条。

## 3. 本地投递预演

三天质量门通过后，先使用测试 webhook 或本地 payload 验证：

- 上午模式显示当日完整精选；
- 下午模式只显示上午之后的新事件或实质新版本；
- 上午已发事件版本不会重复；
- 容量之外但仍合格的候选进入“查看更多”，观察池和低质项不进入；
- 零合格内容不发送；来源降级和流水线失败不能写成“今日无更新”。

正式运行模式只能通过 CLI 显式选择：

```powershell
uv run horizon --intelligence-run-mode morning --hours 24
uv run horizon --intelligence-run-mode afternoon --hours 7
```

该参数本身不会打开投递；`delivery_enabled=false` 时必须安全停止在影子产物。

## 4. 分阶段上线门禁

只有用户明确确认后才依次执行：

1. Commit 并 Push 验证分支，不直接覆盖现有脏工作区。
2. 在验证环境做一次无生产 state 的云端 smoke。
3. 单独打开上午 09:00，观察至少一个完整运行日。
4. 再单独打开下午 16:00 增量。
5. 最后才决定是否替换旧 Daily Summary；不得同时打开新旧投递。

每一步都要回读真实 Run、候选账本、投递账本和飞书送达结果。配置加载成功不等于正式送达已验收。

Windows 回退任务只允许在该阶段显式注册：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/register_windows_daily_trigger.ps1 -Enable -WhatIf
```

确认预览无误后才可移除 `-WhatIf`。脚本将准备 09:20 上午回退和 16:20 下午回退；不带 `-Enable` 时不会注册任何任务。

## 5. 回滚

发现重大漏报、重复投递、来源集中、成本异常或生产状态污染时：

1. 先禁用对应 GitHub workflow 和 Windows 任务，保留日志与账本。
2. 将 `delivery_enabled` 恢复为 `false`，`run_mode` 恢复为 `shadow`。
3. 不删除候选、投递或来源健康账本；复制问题 Run 的 diagnostics 作为证据。
4. 代码回滚到已记录的上一生产 SHA；禁止在两个既有脏 worktree 中执行 reset/clean。
5. 重新通过来源 7 天门和编辑 3 天门后再申请恢复。

## 已知非阻断项

- 官方页面的 HTML 结构会变化，因此保留官方域名搜索备用链路；备用链路只能降级发现，不能把二手摘要升级为官方确认。
- 当前成本诊断可记录调用数和 token；跨供应商精确人民币成本需后续给出实际模型单价，否则保持 `null`。
- 飞书 Base 已明确不做 V1；所有候选先保存在本地 JSONL/HTML，可搜索、可回看、可解释。

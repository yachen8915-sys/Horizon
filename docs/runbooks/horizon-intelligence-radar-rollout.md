# Horizon 情报雷达影子验收与上线手册

## 当前安全状态

- 工作分支基于 `907395b`，所有改动位于独立 worktree。
- `intelligence.enabled=true`，但 `delivery_enabled=false` 且 `run_mode=shadow`。
- GitHub Daily Summary workflow 与 Windows `Pangmen Daily Radar` 继续禁用。
- 工作流文件已定义上午/下午两个模式；上午任务 07:50 启动并以 09:00 前送达为目标，下午仍为 16:00 启动。远端 workflow 仍为 `disabled_manually`；本地注册脚本默认只输出准备结果，缺少 `-Enable` 时不会创建任务。
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

当前 GitHub Actions 路线不要求 `YOUTUBE_DATA_API_KEY` 或 `X_BEARER_TOKEN`：

- YouTube：4 个重点频道 RSS 负责无密钥发现，匿名 `yt-dlp` 负责重点频道、有限主题搜索和传播数据；页面结构变化或限流必须记为 `degraded/fallback_unavailable`。
- X：不再直采；AI HOT 中的 X 条目按原始链接和原始作者建立身份，AI HOT 只承担发现，不能作为独立事实证据。
- Smol AI News：作为 shadow 编辑精选 RSS 补充海外 AI 社交与工程圈信号，事实状态最高为 `reported`。

YouTube RSS 与 `yt-dlp` 通过来源影子门后，才允许晋级为生产链路；官方 API 适配器保留为未来可选增强，不是当前上线条件。

Bluesky 不需要凭据，当前限定为 5 个已实测可用的公开作者流。匿名 `searchPosts` 在真实环境返回 403，相关查询保持显式 `disabled`；它是免费的补充信号，不替代 AI HOT、YouTube RSS 和 Reddit 的生产门禁。

来源验收至少覆盖连续 7 个自然日，并使用每天最后一次运行计算。必须人工检查：

- 四个决策分类均有发现源，核心产品实体同时具备确认源和发现源；
- 关键官方源不能连续失败两天；主页面受限时必须显示 `degraded/fallback_used`，不能伪装健康；
- AI HOT、YouTube RSS、`yt-dlp` 和 Reddit 的失败、限流、字段缺失和成本可见；
- 重复率、独特候选率、新增率没有异常跳变；
- 任何 `failed` 都能追溯到来源、原因码和缺口起点。

2026-09-03 13:04 的一次真实影子结果仅代表旧配置的连通性，不构成新方案的稳定性验收：当时为 122 条、122 个唯一项，YouTube `yt-dlp` 贡献 21 条近期视频。随后用户决定取消 X 直采，并启用恢复可达的 4 个 YouTube RSS 与 Smol AI News；因此后续 7 天统计必须建立新基线，不能把旧轮次的 X 数量和健康状态计入当前方案结论。YouTube 本地主题搜索继续使用日期约束，候选快照继续保存 HTML、JSONL 和传播门槛预判。

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

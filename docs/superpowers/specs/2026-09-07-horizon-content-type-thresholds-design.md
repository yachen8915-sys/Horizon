# Horizon 按内容类型设置筛选门槛设计

状态：已确认方向，待用户审阅书面规格

日期：2026-09-07

## 1. 目标

修复旁门每日内容雷达当前“来源抓到了，但因统一门槛被大量误杀”的问题，同时保持信息密度、事实边界和品牌安全。

本次设计遵循三个已确认前提：

1. DailyHotAPI、ALAPI 是当前阶段需要重点参考的补充信息源，不是新的展示分类。
2. 最终栏目按内容本身分类，不能按来源分类。
3. AI 事实、平台热点和平台规则变化使用不同门槛，不能继续共用同一套硬筛选。

## 2. 当前问题与证据

2026-09-07 07:40 对应的 Canary 运行共抓到 100 条平台热点，其中 DailyHotAPI 抖音 30 条、ALAPI 70 条；DailyHotAPI 微博源当次业务失败。

当前智能候选链路将这些热点按 AI 情报硬门槛处理：94 条在展示前被拒绝，仅 6 条通过；其中 3 条被选中、3 条因 `unverified_hot_limit=3` 暂存。主要拒绝原因为低相关性和低质量，而不是没有抓到内容。

同时，当前渲染把“AI 媒体”作为来源型分组，导致同一条 GPT-6 内容同时出现在“AI 应用”和“AI 媒体”；新的智能候选链路也没有在渲染前可靠写入 `trend_pool`，因此本次运行中的平台热点全部按默认值进入“今日可借势”，“今日大盘观察”为空。

## 3. 分类原则

### 3.1 来源与分类分离

DailyHotAPI、ALAPI、AI HOT、RSS、Bilibili、Telegram 等只记录为来源。来源影响可信度、覆盖状态和排序，但不直接决定展示栏目。

每条内容只有一个主分类，可以拥有多个标签和多个合并来源。渲染层不得因来源不同重复展示同一事件。

### 3.2 以核心事实决定主分类

分类判断回答“这条信息的核心事实是什么”，而不是检查标题里是否出现“AI”等关键词：

- 核心事实是产品发布、功能变化、工具能力或实际工作流：AI 产品与应用。
- 核心事实是模型、论文、开源工程或技术突破：AI 技术与模型。
- 核心事实是 AI 的商业、教育、职场、治理、社会使用或争议：AI 行业与社会。
- 核心事实是一个正在升温的内容形式或大众话题：今日运营热点。
- 核心事实是平台规则、功能、流量、电商或运营机制变化：平台变化雷达。

当一条内容同时包含 AI 和热点属性时，以其主要新闻价值判断。例如，“用 AI 拼豆的方式打开旅行”的主要价值是流行内容形式，应进入“今日可借势”；“女儿用豆包抄答案”的主要价值是 AI 在教育中的使用边界，应进入“AI 行业与社会”。

## 4. 用户可见栏目

### 4.1 今日 AI 情报

保留一级栏目，二级栏目调整为：

1. AI 产品与应用
2. AI 技术与模型
3. AI 行业与社会

取消“AI 媒体”展示分类。媒体、作者和 Provider 改为折叠详情中的来源信息。

### 4.2 今日运营热点

保留现有二级栏目：

1. 今日可借势
2. 今日大盘观察

DailyHotAPI、ALAPI 和其他平台热点来源均进入同一候选池，再按内容机会分流，不新增来源栏目。

### 4.3 平台变化雷达

保持现有栏目，用于平台规则、功能、流量、电商和运营机制变化，不与大众热点混合。

## 5. 按内容类型设置门槛

### 5.1 AI 产品与应用、AI 技术与模型

继续使用现有 AI 情报硬门槛，包括相关性、事实证据、决策影响、时效、差异化和内容质量。聚合标题不能自行升级为已确认事实。

原文核验成功、官方来源或独立来源交叉印证后，才允许提高证据等级。证据不足时继续拒绝或暂存，不因 DailyHotAPI、ALAPI 的优先级而放宽事实标准。

### 5.2 AI 行业与社会

该类型允许 DailyHotAPI、ALAPI 等聚合来源提供早期线索，但必须满足：

- AI 是事件的核心变量，而不是生硬借势词；
- 相关性和总分均不低于 6.0；
- 内容仍在有效时间窗口内；
- 不属于直接排除的品牌安全范围；
- 聚合来源未完成原文核验时，统一标记为“聚合线索 / 待核验”，不得写成已确认事实。

### 5.3 今日可借势

平台热点不再经过 AI 情报硬门槛。满足以下条件时进入“今日可借势”：

- `operations_score >= 7`；
- `content_opportunity_score >= 7`；
- 有明确的热点独有资产和自然内容切口；
- 通过时效、重复和品牌安全检查。

### 5.4 今日大盘观察

内容机会不足不再直接淘汰高热度热点。满足以下任一进入条件且通过安全检查时进入“今日大盘观察”：

- `operations_score >= 8`；或
- `operations_score >= 7`，并且至少满足一个增强信号：核心平台原榜前 10、两个独立 Provider 交叉出现、两个平台共同出现，或明确属于 AI/科技/互联网、职场与年轻人、视觉与内容玩法等重点领域。

进入大盘观察的内容不强行生成主推角度或备选角度，只说明“为什么运营团队应该知道”。

### 5.5 直接排除

以下条件不因来源优先级而放宽：

- 过时或缺少可用来源；
- 重复事件；
- 政治敏感、灾难事故、逝者、严重暴力或严重社会事件；
- 明显品牌安全风险；
- 纯低价值粉圈八卦；
- 将传闻写成确定事实。

## 6. DailyHotAPI 与 ALAPI 的重点参考规则

重点参考通过候选覆盖和排序实现，不通过新增栏目或固定来源配额实现：

1. 两个 Provider 的可用结果都完整进入平台热点分析，不在入口处因“聚合源”降为低优先级。
2. 核心平台的原榜排名和真实热度优先于泛关键词相关性。
3. 同一事件同时出现在 DailyHotAPI 与 ALAPI 时合并来源并提高交叉确认信号，不重复展示。
4. 同分条件下，DailyHotAPI、ALAPI 的核心平台候选优先于普通补漏搜索结果。
5. 平台热点只按规范化真实标题或完整事件键做保守去重，不使用宽泛词语相似度合并不同热点。
6. DailyHotAPI 或 ALAPI 单源失败时，另一来源继续工作；不得将单源失败误报为当天没有热点。

## 7. 排序与展示容量

“今日可借势”和“今日大盘观察”分别排序：

1. 运营情报分或内容机会分；
2. 核心平台优先级；
3. 原榜排名和真实热度；
4. 跨 Provider、跨平台确认；
5. 时效。

“今日运营热点”日常详细展示以 10–15 条为体验目标，不设置每个 Provider 的固定名额，也不为凑数量降低门槛。主卡最多详细展示 15 条；超出的安全合格候选进入同一栏目内的“查看更多热点”折叠列表，而不是被筛选系统丢弃或另建 DailyHot 分类。

全部候选及拒绝原因继续保留在运行归档中。

## 8. 数据流与组件边界

处理顺序固定为：

1. 全来源采集并记录 Provider 健康状态；
2. 规范化平台、原榜排名、热度、时间和来源身份；
3. 跨来源保守去重并合并来源证据；
4. 根据核心事实生成唯一主分类；
5. 根据主分类执行对应门槛；
6. 对平台热点写入 `trend_pool=leverage|watch`；
7. 按栏目排序并生成详细项与折叠的更多候选；
8. 渲染飞书卡片并保存诊断归档。

采集器只负责获取和标准化来源数据；分类器只负责内容类型；门槛模块只负责准入；排序器只负责展示顺序；渲染器不再自行推断分类。

## 9. 失败与降级处理

- 单个 Provider 失败时继续使用其他来源，诊断中记录真实业务错误。
- 核心平台覆盖明显不完整时，卡片仅显示简短覆盖提示，不展示内部错误堆栈。
- 缺少排名或热度时，只能作为普通候选，不能声称“爆火”或获得热度加权。
- 缺少原文核验的 AI 行业与社会内容保留“待核验”标记。
- 分类或分析失败的条目进入归档，不进入正式卡片。
- 飞书容量不足时使用同栏目折叠列表；不得静默丢弃合格热点。

## 10. 验收标准

使用 2026-09-06 的真实分析产物进行离线回放，并至少满足：

1. “用 AI 拼豆的方式打开旅行”进入“今日可借势”。
2. “女儿用豆包抄答案家长只用了一招”进入“AI 行业与社会”，并保留聚合来源边界。
3. “军训才艺大赏”“教育部回应中小学是否须买校服”等符合条件的内容进入“今日大盘观察”，不再被 AI 证据门槛误杀。
4. 同一条 GPT-6 内容只出现一次，“AI 媒体”栏目不再生成。
5. 政治敏感、严重事故和高品牌风险内容仍被排除。
6. DailyHotAPI、ALAPI 同一热点合并来源，不误合并两个不同热点。
7. 热点详细项日常保持约 10–15 条，超出项进入同栏目“查看更多热点”。
8. DailyHotAPI 微博失败时，抖音和 ALAPI 结果仍能进入卡片，并准确记录覆盖不完整。
9. AI 产品与技术的既有证据硬门槛回归测试保持通过。
10. 飞书卡片、Markdown 摘要和候选归档对同一条目的主分类一致。

## 11. 实施范围

实施阶段只修改与本设计直接相关的模型枚举、分析提示、候选门槛、热点分流、排序、摘要/飞书渲染、配置和测试。

本轮不新增数据源，不建设用户反馈学习系统，不重做飞书卡片视觉样式，不改变平台变化雷达的采集范围，也不自动提交、推送、触发 GitHub Actions 或发送飞书消息。

## 12. 实施验证（2026-09-07，离线回放实测）

以下仅追加事实验证记录，不修改前述设计决策。状态为 `DONE_WITH_CONCERNS`：回放工具完成，整体内容验收尚有缺口。

实际执行命令（在隔离工作树中，使用 `uv run --offline --extra dev python`；`-o addopts=''` 用于显示精确测试总数）：

```powershell
uv run --offline --extra dev python -m pytest tests/test_replay_intelligence_selection.py -o addopts='' -q
uv run --offline --extra dev python scripts/replay_intelligence_selection.py --input 'C:\Users\cheni\AppData\Local\Temp\horizon-canary-34067056865-b430a6c5ff83439e993dccb95748ade5' --output "$env:TEMP/horizon-content-type-replay.json"
uv run --offline --extra dev python -m compileall -q src scripts
uv run --offline --extra dev python -m pytest tests/test_intelligence_models.py tests/test_intelligence_analysis.py tests/test_analyzer.py tests/test_profiles.py tests/test_content_type_gate.py tests/test_candidate_pipeline.py tests/test_balanced_digest.py tests/test_intelligence_selection.py tests/test_delivery_selection.py tests/test_candidate_identity.py tests/test_intelligence_presentation.py tests/test_intelligence_brief.py tests/test_webhook.py tests/test_summarizer.py tests/test_coverage_notice.py tests/test_intelligence_diagnostics.py tests/test_fetch_reporting.py tests/test_replay_intelligence_selection.py tests/test_canary_config.py tests/test_github_runtime_config.py -o addopts='' -q
uv run --offline --extra dev python -m pytest tests/test_cli.py tests/test_main.py -o addopts='' -q
uv run --offline --extra dev python -m pytest -o addopts='' -q
uv run --offline --extra dev python -m pytest tests/test_replay_intelligence_selection.py tests/test_canary_config.py tests/test_github_runtime_config.py -o addopts='' -q
git diff --check
```

- TDD：回放测试先出现 9 项失败（入口/模块未实现），实现后 9 项通过；入口测试禁止网络、子进程及报告以外的文件写入，检查输入字节不变。配置在内存中强制 shadow、关闭 delivery/canary；不读取或写入配置中的候选/送达账本。
- 聚焦回归 502 项通过，CLI/main 15 项通过，回放与配置复核 30 项通过，compileall 通过。全套 1025 项通过、4 项失败：`tests/test_bluesky.py::test_bluesky_search_is_anonymous_and_preserves_native_metrics`、`tests/test_x_official.py::test_x_recent_search_preserves_native_metrics`、`tests/test_youtube.py::test_youtube_search_enriches_native_engagement`、`tests/test_youtube.py::test_youtube_channel_watchlist_uses_uploads_playlist`。四项均使用 2026-09-02 固定发布时间，实际来源健康为 `stale`，断言仍期望 `healthy`；与开工前已知失败一致，未修改 freshness 或夹具。
- 真实输出：`C:\Users\cheni\AppData\Local\Temp\horizon-content-type-replay.json`。读取 253 条归档，253 条兼容、0 条 incompatible；19 条 selected、6 条 held（全部容量溢出）、223 条 rejected、5 条 observing。详情为 3 条 AI 产品、1 条平台变化、15 条热点观察；更多热点 6 条。原始得分、分类和来源信号未改写。
- **未通过详细可借势验收**：“用AI拼豆的方式打开旅行”归档运营分 7、内容分 8，正确得到 `trend_pool=leverage`，但按当前运营分优先排序落在 `more_hot`；详细 `hot_leverage` 为空，不能报告已经进入详细“今日可借势”。
- “军训才艺大赏”运营分 7、内容分 5、抖音第 10 位，满足核心平台前十信号，进入 watch 池后因容量落在 `more_hot`；“教育部回应中小学是否须买校服”运营分 8、内容分 5、抖音第 1 位，进入详细 `hot_watch`。二者均由真实归档信号满足规则，未补写新 `operations_focus`。
- **待新影子运行验证**：“女儿用豆包抄答案家长只用了一招”的归档分类仍为 `hot_content`；本次运营分 7、内容分 6、微博第 25 位、单 Provider、无新 focus，被 `insufficient_hotspot_signal` 拒绝。旧数据不能验证“AI 行业与社会”新分类。
- **未通过品牌安全验收**：“伊朗称打击了美军航母和驱逐舰”“中国博主伦敦直播遭外籍青年挑衅殴打”均进入详细 watch，说明现有安全词表仍有缺口。虽然当前门槛拦截 3 条 `brand_safety`，不能据此声称全部高风险题材已排除；本任务未扩改生产规则。
- DailyHotAPI 微博失败、ALAPI 微博健康、DailyHotAPI 抖音健康得到准确提示：“热点覆盖不完整：微博部分来源暂时不可用，抖音、百度、36氪来源数据暂不完整，今日头条及其他热点来源仍正常。”详细热点保留 DailyHotAPI 9 条、ALAPI 6 条。旧诊断仅有扁平 `source_health`，回放明确标注只转换覆盖信息，无法恢复整体 fetch 状态。
- 当前展示类别无“AI 媒体”，Provider 仍为来源信号。归档中 18 条标题含 GPT-6 的候选，1 条被选中、17 条被门槛拒绝；本次没有 GPT-6 去重命中，不能将拒绝计作“合并成功”。脚本复用现有精确 event/topic 选择规则并保留已有上游合并状态，没有新增标题/模糊去重；来源合并与身份规则依赖已通过的聚焦单测，本回放不重跑上游来源身份合并。
- 详细热点确为 15 条，6 条过筛溢出保留在 `more_hot`，但上述安全缺口尚未解决。共享展示、Markdown 和飞书结构的单测通过；**真实飞书视觉与送达未验证**，本轮未联网、调用 AI、发送 webhook、触发 Actions 或发起新影子运行。

### 12.1 最终离线复验（2026-09-07，生产基线 `11754ab`）

本节是对 12 节初次回放状态的追加更新；前文初次失败记录保留为历史证据，当前结果以本节为准。已包含可借势优先排序与后续安全边界修复。

- 修复回放诊断结构校验：提供的 `fetch_report` 必须是对象，`sources` 及实际消费的 `providers/source_health/health/feeds/watchers` 必须为对象列表；损坏容器或非对象成员返回 CLI exit 2，且不创建成功报告。未提供的 optional 字段仍兼容。main 级负例先观察到 17 项失败（原实现错误地返回 0），修复后回放测试共 33 项通过；未触碰生产采集、AI、发送或 ledger。
- 在 `11754ab` 上重新执行两次真实命令：`uv run --offline --extra dev python scripts/replay_intelligence_selection.py --input 'C:/Users/cheni/AppData/Local/Temp/horizon-canary-34067056865-b430a6c5ff83439e993dccb95748ade5' --output 'C:/Users/cheni/AppData/Local/Temp/horizon-content-type-replay.json'`，第二次仅将输出改为 `C:/Users/cheni/AppData/Local/Temp/horizon-content-type-replay-final-check.json`。两次 exit 0；输入全部 6 文件 SHA256 前后不变；两份报告 SHA256 相同：`3AB7DB5E97C7459B0505A3D752A4D2C2C89177DA8D6D7369685FC30C0CAA5A34`。
- 当前真实结果：253 条 compatible、0 incompatible；19 selected、4 held（均容量溢出）、225 rejected、5 observing。15 条热点详情为 1 条 `hot_leverage` + 14 条 `hot_watch`，4 条 `more_hot`；另有 3 条 AI 产品和 1 条平台变化详情。
- **本次通过**：“用AI拼豆的方式打开旅行”已在 `hot_leverage` 详情；“伊朗称打击了美军航母和驱逐舰”“中国博主伦敦直播遭外籍青年挑衅殴打”均为 `rejected/brand_safety`，不在详情或更多。前次对应两项问题在这些真实案例上已修复，不据此宣称所有题材都已验收。
- “军训才艺大赏”仍是 watch 池、`held_by_capacity → more_hot`；“教育部回应中小学是否须买校服”仍在 `hot_watch` 详情。两者继续使用原归档分数与信号。“女儿用豆包抄答案家长只用了一招”仍为旧 `hot_content`，被 `insufficient_hotspot_signal` 拒绝，新“AI 行业与社会”分类仍 **待新影子运行验证**。
- 展示分类不含“AI 媒体”；覆盖提示仍为：“热点覆盖不完整：微博部分来源暂时不可用，抖音、百度、36氪来源数据暂不完整，今日头条及其他热点来源仍正常。”旧扁平来源健康信息仅用于覆盖提示，不恢复未归档的整体 fetch 状态或历史 delivery cooldown。
- 在 `11754ab` 重跑上节同一组 20 文件聚焦命令：`614 passed in 15.01s`；`uv run --offline --extra dev python -m pytest tests/test_replay_intelligence_selection.py tests/test_cli.py tests/test_main.py -o addopts='' -q`：`48 passed in 5.18s`（33 replay + 15 CLI/main）；compileall exit 0。`uv run --offline --extra dev python -m pytest -o addopts='' -q`：`1137 passed, 4 failed in 30.32s`，仍仅为上节列明的 Bluesky/X/YouTube 四项固定日期健康状态断言，没有新增失败，未修改 freshness 或夹具。
- 仍未运行新 AI 分析、真实飞书视觉或送达验收；未联网、触发 Actions、发送 webhook 或写 configured ledger。本次提交仅限回放诊断结构修复、新入口测试与此事实附录。

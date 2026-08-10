# 旁门每日内容雷达：V1 信息源调研与验证

日期：2026-08-10

## 结论

Horizon 现有的 Source、Profile、语义去重、engagement tracking、balanced digest、Summarizer 和 Webhook 均可复用，不需要重写系统。V1 新增 Hugging Face 官方技术信号和可配置平台榜单 Provider；平台源按来源可信度保留元数据，任何单个 Provider 失败都只跳过该源。

## AI 应用

| 来源 | 接入方式 | 状态 | 说明 |
| --- | --- | --- | --- |
| 现有 OpenAI、Google Workspace、Microsoft 365、Figma、Notion、GitHub 等 | 官方 RSS / Atom | 保留，稳定性优先 | 继续进入 `pangmen-topic-radar`，未放宽原筛选规则 |
| Google News AI 应用定向检索 | 公开 RSS | 已真实验证 | 用于补齐没有稳定 RSS 的产品更新；属于聚合索引，最终仍需核对原始来源 |
| 现有 Bilibili AI 创作者和产品关键词 | 公开搜索 | 保留 | 不新增 Bilibili 泛热榜 |

## AI 技术

| 来源 | 接入方式 | 状态 | 说明 |
| --- | --- | --- | --- |
| Hugging Face Trending Models | 官方 Hub API | 已真实验证，官方稳定 | 保留 model id、task、trending score、downloads、likes、last modified、tags 和原始 URL |
| Hugging Face Daily Papers | Hugging Face 公开接口 | 已真实验证，Experimental | 保留标题、摘要、upvotes、评论、项目/GitHub 链接和论文 URL；接口公开但稳定性不按正式版本承诺 |
| Google News AI 能力与模型定向检索 | 公开 RSS | 配置完成 | 作为官方站点索引补充，不代替原始公告 |

## 平台运营热点

| 平台 | Provider | 当前状态 | 运行边界 |
| --- | --- | --- | --- |
| 微博 | DailyHotApi 公共实例 | 接口可达，但本次上游返回 `code=500` | 已 graceful skip；不能宣称已稳定获取微博热搜 |
| 抖音 | DailyHotApi 公共实例 | 已真实读取 30 条 | 第三方聚合、Experimental；本次包含 rank 和 hot value，不代表抖音官方 API |
| 微博 / 抖音 | NewsNow 公共实例 | Mock 解析通过；本次真实请求被 Cloudflare 403 | 暂未作为主配置，公共实例存在风控；如自部署可复用同一 Provider 配置 |
| 小红书 | configurable provider | 仅 Mock 验证 | 没有假装存在官方全站热榜；长期运行需要合法、稳定的第三方数据 Provider |
| 微信生态 | configurable provider | 仅 Mock 验证 | 长期运行需要公众号行业热文/热榜 Provider；不擅自购买新榜、清博等服务 |

## 可靠性与数据口径

- Hugging Face 两类数据都标记 `source_kind=official`；Daily Papers 额外标记 `reliability=official_experimental`。
- 平台公共实例统一标记为 `source_kind=aggregator`，并保留 `platform`、`provider`、`source_name`、`original_url`、`rank`、`hot_value`、`reliability`。
- 没有 hot value 时，原始内容只写“榜单候选”，禁止写“全网爆火”“正在刷屏”。
- `hot_value` 会进入 `metadata.engagement`，可以沿用现有快照能力；V1 不为不同平台强设同一增长阈值。
- 同一热点经语义去重后合并 `platform_occurrences`，保留各平台名次和链接；跨平台出现只作为附加信号，不自动加高分。

## Secret

本次启用的新增 Source 不需要 GitHub Secret。小红书、微信若以后接入商业或授权 Provider，只需在配置中填写环境变量名并在 GitHub Actions Secrets 添加对应值；缺失 Key 时会跳过该 Provider，不影响整份日报。

## 真实 Smoke Test

- AI 应用：Google News 公开 RSS 成功生成 `ContentItem`。
- AI 技术：Hugging Face 成功生成 4 条 `ContentItem`。
- 运营热点：DailyHotApi 公共实例的抖音源成功生成 30 条 `ContentItem`；微博源本次返回上游错误并被跳过。

Smoke Test 仅验证采集和字段结构，没有调用 DeepSeek，也没有向飞书群发送消息。

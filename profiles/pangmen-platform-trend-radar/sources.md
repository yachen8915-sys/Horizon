# 平台热点来源状态

最后核验：2026-08-11（北京时间）

## P0 核心平台

| 平台 | 状态 | 当前 Provider | 定位 |
| --- | --- | --- | --- |
| 微博 | 已接通 | ALAPI；DailyHotAPI 配置保留 | ALAPI 当前可读；DailyHotAPI 本次返回业务码 500，单 Provider 失败不阻断微博来源 |
| 抖音 | 已接通 | DailyHotAPI；ALAPI | 两个 Provider 当前均可读，ALAPI 同时承担交叉验证 |
| 小红书 | P0 数据源缺口 | 无 | 未找到满足 GitHub Actions 稳定运行、无需 Cookie/登录的当前 Provider |
| 微信 | P0 数据源缺口 | 无 | ALAPI 介绍页提到微信，但当前 `/tophub/site` 实际站点列表没有微信站点，不能视为已接通 |

## Supplemental discovery

今日头条、知乎、百度、36Kr 仅作为 supplemental discovery：用于补漏、发现科技/职场/商业议题和跨平台线索。除非运营情报价值很高或被核心平台验证，否则不优先占用正式运营热点名额。

## 2026-08-11 实测说明

- ALAPI `/tophub/site` 返回成功，共 100 个站点条目；未发现小红书或微信热点/热文站点。
- ALAPI 微博返回 50 条、ALAPI 抖音返回 49 条，DailyHotAPI 抖音返回 49 条；DailyHotAPI 微博本次 HTTP 可达但业务码为 500、无榜单条目。
- 不使用个人 Cookie、模拟登录、Playwright 或脆弱页面爬虫补齐小红书/微信。

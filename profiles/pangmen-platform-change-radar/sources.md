# 平台变化雷达公开来源说明

- 抖音：开放平台长期运营规范页使用 page_diff；官方公告与抖音电商变化使用 Google News 定向补漏。
- 小红书：电商学习中心公开首页优先使用 index；公开详情页由新链接继续读取；搜索 RSS 补漏。
- B站：社区公约使用 page_diff；若普通 HTML 只有 JavaScript 壳则跳过，使用 site 定向搜索补漏。
- 视频号/微信小店：微信小店公开开发文档尝试 page_diff；主要通过“微信公开课/微信派”搜索发现并严格区分官方原文、明确转述和二手线索。

所有路径均不使用 Cookie、登录、Playwright、App 抓包或私有接口。

## 2026-08-12 Smoke Test

- 抖音：运营规范 page_diff 成功读取 2,454 字；Google News 7 天补漏建立 4 个 seen URL baseline。
- 小红书：公开首页返回纯 JavaScript 页面，普通 HTML 没有可用 anchor，因此 index 不记为已验证；Google News 路径可访问，但本次 7 天窗口返回 0 条。
- B站：社区公约普通 HTTP 仅返回“请启用 JavaScript”壳，page_diff 已 graceful skip；Google News 补漏建立 2 个 seen URL baseline。
- 视频号 / 微信小店：微信小店公开开发文档 page_diff 成功读取 5,743 字；Google News 7 天补漏建立 1 个 seen URL baseline。

首次 Smoke Test 仅建立临时 baseline，未生成正式资讯，未调用 AI，未发送飞书。

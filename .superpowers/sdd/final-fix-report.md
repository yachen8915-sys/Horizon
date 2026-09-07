# 最终审查修复报告

状态：七项 Important 已实施并通过离线回归，等待最终 reviewer 复验。基线 `70ed524`。本报告不代表新影子运行、AI 分类效果或真实飞书视觉/送达已验收。

## 修复映射

1. Provider 身份：`candidate_identity.py` 在合并前用 provider_name、platform_occurrences 的 provider_id 和当前已知机器 ID 解析展示名别名，合并后的 providers 只保留唯一身份。ALAPI、DailyHotAPI 展示名保留在来源元数据。新增 scraper 标准化输出 → CandidateBuilder → merge → gate 回归，运营 7 / 内容 6 / 微博 25 / general 单源拒绝；真实两个 Provider 的预合并结果仍为两个，进入 watch。
2. HOT_CONTENT 展示：`intelligence_presentation.py` 依据 HOT_CONTENT lane 映射热点展示，YouTube、AI HOT、topic profile 无需伪装成平台热榜。未知 trend_pool 或缺少分析项进入显式 `unmapped` 列表和 WARNING，诊断包含其数量，正文不猜测类别。已有重复 membership 防线保留。
3. 飞书证据：`summarizer.py` 从 CandidateRecord.evidence_status 和 item.metadata.pending_verification 生成共享标签，详情和 webhook more 均使用；未核实为“待核实”，pending 补“待验证”。复用 Markdown 既有证据字典，Markdown 原语义未修改。测试故意让分析对象仍为 confirmed，验证展示以候选状态为准。
4. 冷却优先：`intelligence_selection.py` 将非热点容量判断移至冷却、证据及已有多样性限制之后，合格溢出才标记 held_by_capacity。跨日已发送 AI 在容量满时仍 held/duplicate，selector → DeliverySelector 测试证明不会进入今日 more。
5. 主分类边界：`_is_platform_trend` 同时要求趋势 profile 与 HOT_CONTENT lane。趋势来源的 AI 行业候选继续受 author/source/platform 限制，各限制为 1 的测试均通过；AI 行业不占 15 热点详情名额。
6. 分析故障诊断：`intelligence_report.py` 对所有候选均为 PROCESSING_ERROR 或 ANALYSIS_FAILED 的情况报告 pipeline_failed；正常分析但无合格条目仍为 no_qualified_updates。
7. YouTube 脱敏：`youtube.py` 的 search/channel API 异常通过共享安全格式化；HTTPStatusError 只记录状态码和不含 query 的 path，其他异常只记录类型。403/429 × search/channel 的 MockTransport 测试断言日志和 last_source_results 不含假 key 或 key=。

对应新测试集中在 `tests/test_final_review_regressions.py`；`tests/test_intelligence_presentation.py` 的三个旧异常契约断言同步为显式归档/内容分类契约。无额外生产模块修改。

## 验证证据

独立 worktree：`F:/10-GitHub/Horizon/Horizon-final-fix-snapshot-20260907`，由 committed `70ed524` 创建，只有本轮修复和测试，不包含目标工作树的 19 个预存未提交文件。

实际命令（所有 Python 验证均使用 offline dev 环境）：

```powershell
uv run --offline --extra dev python -m pytest tests/test_final_review_regressions.py -o addopts='' -q
uv run --offline --extra dev python -m pytest tests/test_final_review_regressions.py tests/test_intelligence_presentation.py -o addopts='' -q
uv run --offline --extra dev python -m pytest tests/test_candidate_identity.py tests/test_candidate_pipeline.py tests/test_content_type_gate.py tests/test_intelligence_presentation.py tests/test_intelligence_brief.py tests/test_intelligence_diagnostics.py tests/test_intelligence_selection.py tests/test_delivery_selection.py tests/test_webhook.py tests/test_summarizer.py tests/test_final_review_regressions.py -o addopts='' -q
uv run --offline --extra dev python -m pytest -o addopts='' -q --tb=short
uv run --offline --extra dev python -m compileall -q src scripts
```

- 首轮在未改代码的 committed 基线加测试：21 failed / 1 passed，七类问题均复现。
- 修复后首轮新增测试 22 passed；追加无效 pool 和独立容量边界后，新回归 25 项，连同 presentation 测试 45 passed。
- 最终聚焦 424 passed in 2.81s。
- 全套 1150 passed / 4 failed in 28.39s；compileall exit 0。四项失败仍完全是已知固定日期 stale 断言，未修改日期夹具或时效策略：
  - `tests/test_bluesky.py::test_bluesky_search_is_anonymous_and_preserves_native_metrics`
  - `tests/test_x_official.py::test_x_recent_search_preserves_native_metrics`
  - `tests/test_youtube.py::test_youtube_search_enriches_native_engagement`
  - `tests/test_youtube.py::test_youtube_channel_watchlist_uses_uploads_playlist`
- 本轮补丁应用到目标工作树后，运行 `uv run --offline --extra dev python -m pytest tests/test_final_review_regressions.py tests/test_candidate_identity.py tests/test_intelligence_selection.py tests/test_intelligence_presentation.py tests/test_webhook.py -o addopts='' -q`：228 passed in 2.50s。
- git diff --cached --check 通过。应用补丁后未暂存差异仍是原 19 文件，790 insertions / 23 deletions；仅使用明确的本轮补丁暂存，未 git add .，没有包含预存 semantic merge、webhook 或配置改动。

## 待验证与 concerns

- 最终 reviewer 复验尚未完成。
- 原 Minor：单字“称”可能误命中“昵称”，本批未扩改品牌安全规则。
- 新 AI 分类、真实飞书视觉和送达仍待另行授权运行。本批没有联网、调用真实 API、写配置中的 ledger、发送飞书、触发 Actions 或推送远程。
- YouTube 本轮覆盖的是采集器生成的异常日志和健康 detail；HTTPX 在主动开启 INFO/DEBUG 日志时自行记录请求 URL 的通用日志行为不在本轮修改范围内。

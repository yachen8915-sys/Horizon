# Horizon 旧分支能力迁移契约

**核对日期：** 2026-09-03

**新实现基线：** `origin/main@907395b` 上的隔离工作区

**只读来源：** `e4cd9f2`、`6c7e8f3`，以及质量工作区的未提交差异。两个原工作区均保持原状，没有执行 merge、reset、clean、checkout、暂存或写入。

## 保留、重写与废弃

| 旧能力 | 处理决定 | 新实现与验证 |
|---|---|---|
| 平台热点状态、业务码识别、趋势候选 | 保留行为并重写到统一来源健康和候选链路 | `src/scrapers/platform_trends.py`；`tests/test_platform_trends.py`、`tests/test_source_health.py` |
| 跨栏目唯一展示 | 用“单一主决策分类 + 同事件合并”替代旧栏目的事后去重 | `src/processing/candidate_identity.py`、`src/processing/intelligence_brief.py`；`tests/test_candidate_identity.py`、`tests/test_intelligence_brief.py` |
| 独立爆点评分 | 不迁移独立 selector；并入热门内容决策分类的八维评分和统一竞争 | `src/processing/intelligence_analysis.py`、`src/processing/intelligence_selection.py`；`tests/test_intelligence_analysis.py`、`tests/test_intelligence_selection.py` |
| B站未成熟观察、成熟低传播淘汰、官方豁免 | 保留行为并扩展到 X、YouTube、Bluesky | `src/processing/social_quality.py`；`tests/test_social_quality.py`、`tests/test_engagement.py` |
| AI HOT 单一二级来源限制 | 收口到统一作者、来源、平台和主题多样性策略 | `src/processing/intelligence_selection.py`；`tests/test_intelligence_selection.py` |
| 旧跨日冷却 | 仅作为只读基线；事件出现实质更新时生成新版本重新竞争 | `src/storage/candidate_store.py`、`src/processing/intelligence_selection.py`；`tests/test_candidate_store.py`、`tests/test_delivery_selection.py` |
| 旧平台预览脚本和旧栏目渲染 | 不迁移 | 新实现由候选 JSONL/HTML、情报 brief 和本地卡片预演承担 |

## 明确未带入新实现的内容

- `src/processing/breakout_selection.py` 和 `src/processing/platform_trend_selection.py` 没有整文件迁入，避免新旧 selector 重复执行。
- `job-market-radar`、`mining-market-radar`、RSS identity、历史预览文件和交接卡不属于本次迁移范围。
- 旧 `digest_selection_state.json` 不会生成历史候选，也不会触发补发；它只提供冷却时间线索。
- 两个脏工作区中的未提交文件仍归用户所有，新实现没有回写或批量暂存这些文件。

## 当前仍待运行验证

- 连续 7 个自然日来源 shadow；
- 连续 3 天真实 AI 编辑质量 shadow；
- 本地卡片、测试 webhook 和云端 smoke；
- 用户确认后的 Commit、Push 与分阶段调度启用。

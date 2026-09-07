# Horizon Content-Type Thresholds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax and must be completed in order.

**Goal:** 将 Horizon 的情报准入从“所有内容共用 AI 硬门槛”改为“按内容类型使用不同门槛”，让 DailyHotAPI、ALAPI 作为重点补充来源进入统一候选池，同时保持唯一内容分类、品牌安全、证据边界和 10–15 条热点展示容量。

**Architecture:** 采集器继续只负责抓取和标准化；分析层输出唯一 `DecisionLane` 与平台热点所需的运营字段；新的纯函数门槛模块根据内容类型判定准入并写入 `trend_pool`；选择层只做排序、容量和保守去重；新的展示分组模块为 Markdown 与飞书卡片提供同一份栏目映射；覆盖提示从 `FetchReport` 派生，不把 Provider 故障误写成“没有热点”。

**Tech Stack:** Python 3.11+、Pydantic、pytest、JSON 配置、飞书 Card 2.0 Markdown/折叠面板。

**Design spec:** `docs/superpowers/specs/2026-09-07-horizon-content-type-thresholds-design.md`

## Execution guardrails

- 本计划基于独立工作树 `F:\10-GitHub\Horizon\Horizon-intelligence-redesign-plan-20260903`。
- 当前工作树已有未提交修改。执行时先记录 `git status --short`，不得使用 `git add .`；每一步只暂存本任务新增的 hunk，若无法与既有修改安全分离则先停止提交、保留工作区结果并说明。
- 不修改采集源数量，不做模糊标题合并，不给 DailyHotAPI/ALAPI 新建栏目或固定配额。
- 本地验证不得访问网络、写生产 ledger、触发 GitHub Actions 或发送飞书；真实影子运行需要用户另行明确授权。
- 旧运行产物只能验证既有分析字段下的门槛、分流、容量和渲染。它不能证明新模型提示会把“女儿用豆包抄答案家长只用了一招”重新归到“AI 行业与社会”；这一项必须用提示契约测试和后续新的无投递影子运行验收。

## Target data flow

```text
ContentItem + provider metadata
        |
        v
analysis_system_prompt -> ContentAnalysis + IntelligenceAnalysis
        |
        v
CandidateBuilder -> assess_content_type_gate
        |
        +-- AI product/technical/platform change -> existing strict evidence gates
        +-- AI industry/social -> >= 6.0, current, safe, pending-verification allowed
        +-- platform trend -> operations/content/boost gate, no AI evidence gate
        |
        v
IntelligenceSelector -> conservative duplicate check + lane-aware ranking + 15-detail cap
        |
        v
build_intelligence_presentation
        |
        +-- Markdown brief
        +-- Feishu card
        +-- diagnostics / coverage notice
```

## Task 1: Extend the content taxonomy and analyzer contract

**Files:**

- Modify: `src/models.py`
- Modify: `src/processing/intelligence_analysis.py`
- Modify: `src/ai/prompting/analysis.py`
- Modify: `src/ai/analyzer.py`
- Test: `tests/test_intelligence_models.py`
- Test: `tests/test_intelligence_analysis.py`
- Test: `tests/test_analyzer.py`
- Test: `tests/test_profiles.py`

- [ ] **Step 1: Add failing model and prompt tests**

Add tests that require:

```python
def test_decision_lane_includes_ai_industry_society() -> None:
    assert DecisionLane.AI_INDUSTRY_SOCIETY.value == "ai_industry_society"


def test_content_analysis_accepts_operations_focus() -> None:
    analysis = ContentAnalysis(
        score=8,
        operations_score=8,
        content_opportunity_score=5,
        operations_focus="workplace_youth",
    )
    assert analysis.operations_focus == "workplace_youth"
```

In `tests/test_analyzer.py` assert the intelligence contract contains:

```python
assert "ai_industry_society" in prompt
assert "industry_social" in prompt
assert '"operations_focus"' in prompt
assert "按核心事实选择唯一主分类" in prompt
assert "来源不能决定主分类" in prompt
```

Also add a validation case proving an unknown `operations_focus` value fails the first Pydantic validation and is repaired on the analyzer's existing single repair attempt; it must not be silently treated as a focused-domain boost.

- [ ] **Step 2: Run the focused tests and confirm the contract is missing**

Run:

```powershell
python -m pytest tests/test_intelligence_models.py tests/test_intelligence_analysis.py tests/test_analyzer.py tests/test_profiles.py -q
```

Expected: new tests fail because the enum, content kind and focus field are absent.

- [ ] **Step 3: Implement the smallest schema change**

In `src/models.py`:

```python
class DecisionLane(str, Enum):
    PRODUCT_CAPABILITY = "product_capability"
    HOT_CONTENT = "hot_content"
    TECHNICAL_FRONTIER = "technical_frontier"
    AI_INDUSTRY_SOCIETY = "ai_industry_society"
    PLATFORM_AI_CHANGE = "platform_ai_change"


operations_focus: Literal[
    "ai_tech",
    "workplace_youth",
    "visual_content",
    "general",
] | None = None
```

In `src/processing/intelligence_analysis.py`:

- Add `"industry_social"` to `IntelligenceDraft.content_kind`.
- Add `DecisionLane.AI_INDUSTRY_SOCIETY` to `LANE_WEIGHTS`. Use the agreed 6.0 threshold at the gate layer, not by lowering the global `minimum_score`.
- Keep `PRODUCT_CAPABILITY` and `TECHNICAL_FRONTIER` weights and evidence behavior unchanged.

A suitable initial weight map is:

```python
DecisionLane.AI_INDUSTRY_SOCIETY: {
    "decision_impact": 0.20,
    "audience_fit": 0.20,
    "novelty": 0.10,
    "evidence_quality": 0.15,
    "demonstrability": 0.05,
    "propagation_quality": 0.10,
    "freshness": 0.10,
    "differentiation": 0.10,
},
```

In `src/ai/prompting/analysis.py`:

- Extend the platform-trend JSON contract with `operations_focus`.
- Extend the intelligence contract with `ai_industry_society` and `industry_social`.
- State content-first routing with concrete positive/negative examples:
  - “用 AI 拼豆的方式打开旅行” → `hot_content` because the core value is a popular format.
  - “女儿用豆包抄答案” → `ai_industry_society` because AI use in education is the core event.
  - An item mentioning AI only as a hook must not enter the AI lane.
- State that DailyHotAPI, ALAPI, AI HOT, RSS and other providers are source metadata and never a lane.
- For unverified aggregator items classified as `ai_industry_society`, require wording equivalent to “聚合线索 / 待核验” and prohibit confirmed language.

Update `src/ai/analyzer.py` normalization so the new optional field survives valid responses and invalid enum values cannot break the full batch.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
python -m pytest tests/test_intelligence_models.py tests/test_intelligence_analysis.py tests/test_analyzer.py tests/test_profiles.py -q
```

Expected: all focused tests pass and existing AI product/technical score tests remain unchanged.

- [ ] **Step 5: Commit only Task 1 hunks**

Review:

```powershell
git diff -- src/models.py src/processing/intelligence_analysis.py src/ai/prompting/analysis.py src/ai/analyzer.py tests/test_intelligence_models.py tests/test_intelligence_analysis.py tests/test_analyzer.py tests/test_profiles.py
```

Stage only newly implemented hunks with `git add -p`, verify `git diff --cached --check`, then commit:

```powershell
git commit -m "扩展按内容分类的情报分析契约"
```

## Task 2: Merge exact-source evidence before applying content-type gates

**Files:**

- Create: `src/processing/content_type_gate.py`
- Modify: `src/models.py`
- Modify: `src/processing/candidate_pipeline.py`
- Modify: `src/processing/candidate_identity.py`
- Modify: `src/orchestrator.py`
- Test: `tests/test_content_type_gate.py`
- Test: `tests/test_candidate_pipeline.py`
- Test: `tests/test_candidate_identity.py`
- Test: `tests/test_balanced_digest.py`

- [ ] **Step 1: Write a table-driven failing gate suite**

Create helpers that build verified AI product/technical candidates, unverified AI industry/social candidates, platform-trend items with scores/rank/providers/platforms/focus, and stale/duplicate/brand-unsafe items. Add one cross-provider case where neither raw record has enough signal alone, but the exact duplicate group becomes eligible after DailyHotAPI and ALAPI provider metadata is merged.

Cover this exact matrix:

| Content type | Accept condition | Expected pool / result |
|---|---|---|
| AI product / technical | Existing strict hard gates | Existing result unchanged |
| AI industry/social | current, safe, relevance ≥ 6, weighted total ≥ 6.0 | eligible; unverified retains pending-verification label |
| Platform trend | operations ≥ 7 and content ≥ 7 | `leverage` |
| Platform trend | operations ≥ 8 | `watch` |
| Platform trend | operations ≥ 7 plus core-platform top 10 | `watch` |
| Platform trend | operations ≥ 7 plus 2 providers | `watch` |
| Platform trend | operations ≥ 7 plus 2 platforms | `watch` |
| Platform trend | operations ≥ 7 plus focused domain | `watch` |
| Platform trend | operations 7, content 6, no boost | rejected |
| Any | brand-safety exclusion | rejected |
| Any | stale, duplicate or unusable source | rejected by the existing owning stage |

Use separate tests for “军训才艺大赏” and “教育部回应中小学是否须买校服” as watch-pool examples, and for political sensitivity, severe accident, death and violence exclusions.

- [ ] **Step 2: Run the new tests and verify failure**

```powershell
python -m pytest tests/test_content_type_gate.py tests/test_candidate_pipeline.py tests/test_balanced_digest.py -q
```

Expected: failures show platform trends still pass through generic `assess_hard_gates` and `trend_pool` is not assigned on the intelligence path.

- [ ] **Step 3: Add explicit reason codes**

In `src/models.py` add:

```python
BRAND_SAFETY = "brand_safety"
LOW_OPERATIONS_VALUE = "low_operations_value"
INSUFFICIENT_HOTSPOT_SIGNAL = "insufficient_hotspot_signal"
```

Do not collapse these into `LOW_QUALITY`; diagnostics must distinguish safety, low operations value and missing boost.

- [ ] **Step 4: Implement a pure gate module**

Create `src/processing/content_type_gate.py` with no network, storage or renderer imports:

```python
@dataclass(frozen=True)
class ContentTypeGateResult:
    accepted: bool
    reason: ReasonCode
    trend_pool: Literal["leverage", "watch"] | None = None
    pending_verification: bool = False


```

Expose three typed functions: `assess_content_type_gate(item, draft, *, minimum_score) -> ContentTypeGateResult`, `assign_platform_trend_pool(item) -> Literal["leverage", "watch"] | None`, and `is_brand_safety_excluded(item) -> bool`. Their complete branch behavior is the seven-rule list below and must be implemented without stub branches.

Implementation rules:

1. Route by `draft.primary_lane` first. `AI_INDUSTRY_SOCIETY` must keep the AI-industry gate even when discovered by a platform-trend source. Apply the platform-trend gate only when the processing profile is `pangmen-platform-trend-radar` and the primary lane is `HOT_CONTENT`; never infer this route from provider name.
2. Run brand safety before score admission.
3. For platform trends:
   - leverage when operations ≥ 7 and content opportunity ≥ 7;
   - otherwise watch when operations ≥ 8;
   - otherwise watch when operations ≥ 7 plus at least one boost: core platform rank ≤ 10, two distinct providers, two distinct platforms, or `operations_focus` is exactly one of `ai_tech`, `workplace_youth`, `visual_content`;
   - otherwise reject with `LOW_OPERATIONS_VALUE` or `INSUFFICIENT_HOTSPOT_SIGNAL`.
4. For `AI_INDUSTRY_SOCIETY`, permit `UNVERIFIED` only when current/safe and the relevance and weighted thresholds are both at least 6.0; return `pending_verification=True`.
5. For AI product, technical and platform-change lanes, delegate to current `assess_hard_gates` unchanged.
6. Preserve the existing freshness, duplicate and source-availability owners; do not reproduce those policies in multiple modules.
7. Move the existing brand-safety terms from `src/orchestrator.py` into this module and keep a compatibility wrapper only if legacy tests still call the orchestrator helper.

- [ ] **Step 5: Preserve provider/platform evidence during exact duplicate merge**

In `merge_duplicate_group`, build deterministic, de-duplicated metadata arrays from every member in an already-established exact duplicate group:

- `providers`: values from each item's `metadata["provider"]` or normalized `source_id`;
- `platforms`: values from each item's `metadata["platform"]` or normalized `content_platform`.

Keep the winning item's title and primary metadata, continue merging `EvidenceReference` objects, and do not alter `group_duplicate_candidates` or add fuzzy matching. Add assertions that DailyHotAPI + ALAPI becomes two providers, while two merely similar titles remain separate groups.

- [ ] **Step 6: Wire the build → exact merge → gate order**

Split `CandidateBuilder` into two explicit phases:

1. `from_analyzed_item` normalizes evidence and returns `ENRICHED` when intelligence is valid, or `PROCESSING_ERROR` when it is not.
2. `apply_content_type_gate(candidate)` builds the typed `IntelligenceDraft`, calls `assess_content_type_gate`, and returns an immutable copy transitioned to `ELIGIBLE` or `REJECTED`. Accepted copies receive `PASSED_HARD_GATES`, returned `trend_pool`, and `pending_verification=True` only when required; rejected copies keep the specific returned reason code.

In `_select_intelligence_candidates`, preserve processing errors separately, group exact duplicate `ENRICHED` candidates, merge each group, then apply the content-type gate to each merged primary before selection. This order is required because the two-provider/two-platform boost does not exist until exact duplicates are merged.

In `src/orchestrator.py` make the pool and safety helpers delegate to the shared module, then remove duplicate policy constants after regression tests prove both paths use the same rule.

- [ ] **Step 7: Run gate and pipeline tests**

```powershell
python -m pytest tests/test_content_type_gate.py tests/test_candidate_pipeline.py tests/test_candidate_identity.py tests/test_balanced_digest.py tests/test_intelligence_analysis.py -q
```

Expected: all pass; AI product/technical unverified candidates are still rejected.

- [ ] **Step 8: Commit only Task 2 hunks**

Selectively stage the Task 2 hunks, run `git diff --cached --check`, then:

```powershell
git commit -m "按内容类型执行情报准入门槛"
```

## Task 3: Make selection lane-aware and reserve 15 detailed hotspot slots

**Files:**

- Modify: `src/models.py`
- Modify: `src/processing/intelligence_selection.py`
- Modify: `src/processing/delivery_selection.py` only if the existing capacity handoff cannot preserve separate AI/hot overflow
- Modify: `data/config.github.json`
- Test: `tests/test_intelligence_selection.py`
- Test: `tests/test_delivery_selection.py`
- Test: `tests/test_canary_config.py`
- Test: `tests/test_github_runtime_config.py`

- [ ] **Step 1: Add failing selection tests**

Add cases proving:

1. Six safe unverified platform trends are not capped at three by `unverified_hot_limit`.
2. Platform trends from DailyHotAPI/ALAPI are not held merely because several share the same aggregator source or unknown author.
3. Exact topic/event duplicates are still held.
4. Two different trend titles sharing broad words are not fuzzy-merged.
5. Trend ordering is operations score, provider corroboration, native rank/heat, content opportunity score, then stable candidate id.
6. At most 15 platform trends are detailed; the 16th safe eligible trend becomes `HELD_BY_CAPACITY` and remains available for “查看更多热点”.
7. AI lane candidates continue to obey existing global evidence/diversity controls.

- [ ] **Step 2: Confirm current behavior fails**

```powershell
python -m pytest tests/test_intelligence_selection.py tests/test_delivery_selection.py tests/test_canary_config.py tests/test_github_runtime_config.py -q
```

Expected: the unverified hot limit and generic source/author diversity still suppress platform-trend candidates, and no explicit 15-item trend detail limit exists.

- [ ] **Step 3: Add configuration**

In `IntelligenceSelectionConfig` add:

```python
platform_trend_detail_limit: int = Field(default=15, ge=1, le=15)
```

In `data/config.github.json`:

- add `platform_trend_detail_limit: 15`;
- include `ai_industry_society` in `decision_lanes`;
- bump `rule_version` from `2026-09-05-v2` to `2026-09-07-v3`;
- do not enable delivery or change source lists.

Update exact-dictionary config tests.

- [ ] **Step 4: Implement lane-aware selection**

In `IntelligenceSelector`:

- identify platform trends from the item profile, not from `HOT_CONTENT` alone;
- exempt platform-trend items from `unverified_hot_limit` and generic author/source/platform limits;
- continue applying exact `event_key` / `editorial_topic_key` duplicate limits and delivery cooldown;
- maintain a separate `platform_trend_selected_count` and hold only the 16th+ detailed trend with `HELD_BY_CAPACITY`;
- add `AI_INDUSTRY_SOCIETY` to `LANE_PRIORITY_BOOST` without outranking product evidence by default;
- use a platform-trend sort key based on normalized operations score, provider count, rank/heat, content score and id;
- do not introduce fuzzy text similarity or per-provider quotas.

The 15-item limit is a hard maximum, not a target that lowers gates. If only 8 qualify, render 8.

- [ ] **Step 5: Run selection tests**

```powershell
python -m pytest tests/test_intelligence_selection.py tests/test_delivery_selection.py tests/test_candidate_identity.py tests/test_canary_config.py tests/test_github_runtime_config.py -q
```

Expected: selection tests pass, conservative identity tests pass, and the config remains shadow/no-delivery.

- [ ] **Step 6: Commit only Task 3 hunks**

Review and selectively stage, then:

```powershell
git diff --cached --check
git commit -m "调整热点排序与展示容量"
```

## Task 4: Use one presentation model for Markdown and Feishu

**Files:**

- Create: `src/processing/intelligence_presentation.py`
- Modify: `src/processing/intelligence_brief.py`
- Modify: `src/services/webhook.py`
- Modify: `src/orchestrator.py`
- Test: `tests/test_intelligence_presentation.py`
- Test: `tests/test_intelligence_brief.py`
- Test: `tests/test_webhook.py`
- Test: `tests/test_summarizer.py`

- [ ] **Step 1: Add failing presentation tests**

Create candidates for every user-visible section and assert a single builder produces:

```python
@dataclass(frozen=True)
class IntelligencePresentation:
    ai_product: list[CandidateRecord]
    ai_technical: list[CandidateRecord]
    ai_industry: list[CandidateRecord]
    hot_leverage: list[CandidateRecord]
    hot_watch: list[CandidateRecord]
    platform_changes: list[CandidateRecord]
    more_ai: list[CandidateRecord]
    more_hot: list[CandidateRecord]
```

Tests must prove:

- every candidate appears in at most one primary list;
- `AI 媒体` never appears as a section;
- a GPT-6 item tagged `ai_media_candidate=True` still appears once, according to its content lane;
- `trend_pool=watch` produces “今日大盘观察”;
- capacity-held trends render under “查看更多热点”, not generic “查看更多”;
- capacity-held AI items render under “查看更多资讯”;
- Markdown and Feishu use identical category membership.

- [ ] **Step 2: Run presentation tests and confirm failure**

```powershell
python -m pytest tests/test_intelligence_presentation.py tests/test_intelligence_brief.py tests/test_webhook.py tests/test_summarizer.py -q
```

Expected: tests fail because current Markdown groups by generic lane labels and the legacy webhook still synthesizes an `AI 媒体` source section.

- [ ] **Step 3: Implement the shared presentation builder**

In `src/processing/intelligence_presentation.py` implement the typed public function `build_intelligence_presentation(selected: list[CandidateRecord], more: list[CandidateRecord]) -> IntelligencePresentation`. It must populate every dataclass list using the mapping below and reject duplicate membership.

Mapping:

| Content lane/profile | User-visible section |
|---|---|
| `PRODUCT_CAPABILITY` | AI 产品与应用 |
| `TECHNICAL_FRONTIER` | AI 技术与模型 |
| `AI_INDUSTRY_SOCIETY` | AI 行业与社会 |
| platform-trend + `leverage` | 今日可借势 |
| platform-trend + `watch` | 今日大盘观察 |
| `PLATFORM_AI_CHANGE` / platform-change profile | 平台变化雷达 |

Apply lane mappings before source-profile mappings, so a platform-trend source classified as `AI_INDUSTRY_SOCIETY` appears in “AI 行业与社会”, not a hotspot section. Source/provider names remain detail metadata only. Raise or archive an unmapped candidate rather than guessing a renderer-only category.

- [ ] **Step 4: Refactor both renderers to consume the shared presentation**

Update `render_intelligence_brief` to call the builder and emit these exact headings:

```text
## 今日 AI 情报
### AI 产品与应用
### AI 技术与模型
### AI 行业与社会
## 今日运营热点
### 今日可借势
### 今日大盘观察
## 平台变化雷达
## 查看更多资讯
## 查看更多热点
```

In `src/services/webhook.py`:

- remove `ai_media_candidate`-based grouping and the `### AI 媒体` heading;
- keep provider/media/author in the collapsible detail;
- use the same presentation object and section labels as Markdown;
- preserve the existing Card 2.0 collapsible panel format and card-size guards;
- avoid reclassifying from title or source at render time.

Update the orchestrator call sites to pass selected and capacity-held candidates consistently.

- [ ] **Step 5: Run renderer tests**

```powershell
python -m pytest tests/test_intelligence_presentation.py tests/test_intelligence_brief.py tests/test_webhook.py tests/test_summarizer.py -q
```

Expected: all pass; `rg -n "### AI 媒体" src` returns no renderer heading.

- [ ] **Step 6: Commit only Task 4 hunks**

Review and selectively stage, then:

```powershell
git diff --cached --check
git commit -m "统一情报栏目与飞书展示分组"
```

## Task 5: Surface provider coverage without creating source categories

**Files:**

- Create: `src/processing/coverage_notice.py`
- Modify: `src/diagnostics/intelligence_report.py`
- Modify: `src/processing/intelligence_brief.py`
- Modify: `src/services/webhook.py`
- Modify: `src/orchestrator.py`
- Test: `tests/test_coverage_notice.py`
- Test: `tests/test_intelligence_diagnostics.py`
- Test: `tests/test_intelligence_brief.py`
- Test: `tests/test_webhook.py`
- Test: `tests/test_fetch_reporting.py`

- [ ] **Step 1: Add failing coverage tests**

Build `fetch_report` payloads for:

- DailyHotAPI Weibo business failure + DailyHotAPI Douyin success + ALAPI success;
- one failed non-core feed;
- total platform-source failure;
- healthy sources.

Expected user-facing behavior:

```python
assert notice == "热点覆盖不完整：微博来源暂时不可用，抖音及其他热点来源仍正常。"
```

The exact source names must come from normalized health records, not exception stack traces. Healthy runs return `None`. A total failure must say coverage is unavailable, not “今日无热点”.

- [ ] **Step 2: Run focused tests and confirm failure**

```powershell
python -m pytest tests/test_coverage_notice.py tests/test_intelligence_diagnostics.py tests/test_intelligence_brief.py tests/test_webhook.py tests/test_fetch_reporting.py -q
```

Expected: no shared coverage-notice function exists and diagnostics lack presentation/pool counts.

- [ ] **Step 3: Implement the coverage adapter**

Create `src/processing/coverage_notice.py` and expose `build_platform_coverage_notice(fetch_report: dict[str, Any] | None) -> str | None`. Implement every healthy, partial-failure and total-failure branch described below; do not leave a default stub branch.

Rules:

- consume the nested `FetchReport.to_dict()` shape already used by diagnostics;
- mention only user-understandable platform/source coverage;
- never expose traceback, tokens, raw response bodies or internal retries;
- treat one-provider failure as partial coverage when another provider/platform succeeded;
- return `None` when healthy.

- [ ] **Step 4: Add diagnostic category and pool counts**

Extend `build_intelligence_report` with three concrete keys: `presentation_categories` maps each presentation field name to `len(getattr(presentation, field_name))`; `trend_pools` contains the exact `leverage` and `watch` counts from the same presentation; `coverage_notice` is the return value of `build_platform_coverage_notice(fetch_report)`.

Derive presentation counts through `build_intelligence_presentation` so diagnostics cannot drift from renderers.

Pass the notice through orchestrator to the Markdown and webhook rendering functions. Keep it short and place it near the fetch/selection summary.

- [ ] **Step 5: Run coverage and diagnostics tests**

```powershell
python -m pytest tests/test_coverage_notice.py tests/test_intelligence_diagnostics.py tests/test_intelligence_brief.py tests/test_webhook.py tests/test_fetch_reporting.py -q
```

Expected: partial failure is accurately reported while successful DailyHotAPI/ALAPI items remain present.

- [ ] **Step 6: Commit only Task 5 hunks**

Review and selectively stage, then:

```powershell
git diff --cached --check
git commit -m "补充热点来源覆盖提示与诊断"
```

## Task 6: Add an offline replay and verify the full change set

**Files:**

- Create: `scripts/replay_intelligence_selection.py`
- Create: `tests/test_replay_intelligence_selection.py`
- Modify: `docs/superpowers/specs/2026-09-07-horizon-content-type-thresholds-design.md` only to append observed verification status
- Test: all files touched above

- [ ] **Step 1: Write a failing replay smoke test**

The script contract:

```powershell
python scripts/replay_intelligence_selection.py --input "C:\Users\cheni\AppData\Local\Temp\horizon-canary-34067056865-b430a6c5ff83439e993dccb95748ade5" --output "$env:TEMP\horizon-content-type-replay.json"
```

The test implementation should invoke the Python entry point directly. The script must:

- discover the archived candidate/diagnostic JSON without assuming a random extracted directory name beyond the supplied root;
- deserialize with current models where possible and report incompatible records explicitly;
- run only gate, selection, presentation and diagnostics;
- never instantiate scrapers, call AI/network clients, send webhook messages, or write configured candidate/delivery ledgers;
- write one standalone JSON report containing counts, selected titles, held titles, reject reasons, category membership, trend pools and coverage notice;
- exit non-zero on malformed input, but not because an old record lacks a newly optional field.

Test this with a temporary fixture directory, not the user’s absolute temp path.

- [ ] **Step 2: Implement the replay script**

Keep I/O in `main()` and expose the pure typed function `replay_archive(archive_root: Path, *, config: IntelligenceRadarConfig) -> dict[str, Any]`. The returned dictionary must contain `counts`, `selected_titles`, `held_titles`, `reject_reasons`, `presentation_categories`, `trend_pools`, `coverage_notice`, and `incompatible_records`.

Load `data/config.github.json` through the repository’s existing config loader. Force no-delivery behavior in memory even if configuration changes later. Do not mutate the input artifact.

- [ ] **Step 3: Run replay unit tests**

```powershell
python -m pytest tests/test_replay_intelligence_selection.py -q
```

Expected: pass, including no-network and no-ledger-write assertions.

- [ ] **Step 4: Replay the real 2026-09-07 canary artifact**

Use one PowerShell line to avoid shell-continuation ambiguity:

```powershell
python scripts/replay_intelligence_selection.py --input 'C:\Users\cheni\AppData\Local\Temp\horizon-canary-34067056865-b430a6c5ff83439e993dccb95748ade5' --output "$env:TEMP\horizon-content-type-replay.json"
Get-Content -LiteralPath "$env:TEMP\horizon-content-type-replay.json"
```

Check and record, without inventing results:

- “用 AI 拼豆的方式打开旅行” is in `hot_leverage`.
- “军训才艺大赏” and “教育部回应中小学是否须买校服” are admitted to `hot_watch` only if their archived scores/signals satisfy the agreed rule.
- detailed hotspots are no more than 15 and safe overflow is retained in `more_hot`.
- DailyHotAPI Weibo failure produces a partial-coverage notice while Douyin/ALAPI items survive.
- no `AI 媒体` category exists.
- duplicate GPT-6 items collapse only through existing exact event/source identity rules.

Evidence boundary: the old artifact classified “女儿用豆包抄答案家长只用了一招” as `hot_content` and does not contain the new model output. Mark the “AI 行业与社会” example as **待新影子运行验证**, even if a hand-built unit fixture proves the routing contract. Do not rewrite replay data to force the expected category.

- [ ] **Step 5: Run the full relevant regression suite**

```powershell
python -m compileall -q src scripts
python -m pytest tests/test_intelligence_models.py tests/test_intelligence_analysis.py tests/test_analyzer.py tests/test_profiles.py tests/test_content_type_gate.py tests/test_candidate_pipeline.py tests/test_balanced_digest.py tests/test_intelligence_selection.py tests/test_delivery_selection.py tests/test_candidate_identity.py tests/test_intelligence_presentation.py tests/test_intelligence_brief.py tests/test_webhook.py tests/test_summarizer.py tests/test_coverage_notice.py tests/test_intelligence_diagnostics.py tests/test_fetch_reporting.py tests/test_replay_intelligence_selection.py tests/test_canary_config.py tests/test_github_runtime_config.py -q
python -m pytest tests/test_cli.py tests/test_main.py -q
python -m pytest -q
```

Expected: all feature-focused tests pass. If the full suite still has the known date-sensitive Bluesky/X/YouTube freshness-fixture failures, report their exact names and evidence; do not change production freshness behavior as part of this feature.

- [ ] **Step 6: Append factual verification status to the design spec**

Append a short “实施验证” section containing:

- commands actually run;
- exact pass/fail counts;
- the real replay output path;
- which acceptance examples passed;
- “女儿用豆包……” marked `待新影子运行验证` if no new analysis exists;
- no claim of Feishu delivery validation, because this plan does not send.

Do not change the approved design decisions.

- [ ] **Step 7: Final diff and scope audit**

```powershell
git diff --check
git status --short
git diff --name-only
rg -n "AI 媒体|unverified_hot_limit|platform_trend_detail_limit|ai_industry_society|operations_focus" src tests data/config.github.json docs/superpowers/specs/2026-09-07-horizon-content-type-thresholds-design.md
```

Verify:

- no source lists, secrets, workflows or delivery flags changed;
- no fuzzy trend dedup was introduced;
- DailyHotAPI/ALAPI appear as source metadata, not headings;
- existing unrelated dirty changes remain preserved.

- [ ] **Step 8: Commit replay and verification artifacts**

Selectively stage only Task 6 files/hunks, verify cached diff, then:

```powershell
git diff --cached --check
git commit -m "增加情报筛选离线回放验收"
```

## Acceptance checklist

- [ ] AI product/technical lanes still require existing evidence hard gates.
- [ ] AI industry/social permits safe aggregator leads at score/relevance ≥ 6.0 and visibly marks them pending verification.
- [ ] Platform trends bypass AI hard gates and follow leverage/watch thresholds.
- [ ] DailyHotAPI/ALAPI improve candidate coverage and tie-breaking but never become categories or quotas.
- [ ] Brand safety, staleness, duplicate and unusable-source exclusions remain.
- [ ] Every item has one primary user-visible category.
- [ ] “AI 媒体” is absent from Markdown and Feishu headings.
- [ ] Up to 15 hotspot details render; safe overflow remains under “查看更多热点”.
- [ ] Partial provider failure yields an accurate coverage notice.
- [ ] Offline replay causes no network, ledger, Actions or Feishu side effects.
- [ ] A new no-delivery shadow run remains a separate, user-authorized acceptance step.

# 实现落点图（E00 盘点）

对照 `CONTRACTS.md` 与 TASK_INDEX 候选路径。状态：`present`=已有可复用实现；`partial`=有相关能力但未满足本轮契约；`missing`=需新建。路径相对仓库根。

## 宿主 / 版本 / 安装面

| 能力 | 状态 | 实际位置 |
|---|---|---|
| `engines.dsh` 区间 | present | `package.json`, `dsh.plugin.json` |
| `entry.inject=["tools"]` | present | `dsh.plugin.json` |
| cordis / bundle patch | present | `cordis.patch.yml`, `package.json#dsh.bundle.patch` |
| 薄宿主工具 + 0.2.1 schema | present | `src/index.js`, `src/tool-spec.js`, `src/test/host-compat-021.mjs` |
| Memory client search | present | `src/memory-client.js` → `POST /api/v1/memories/search` |
| `discoverDshBaseUrl` | partial | 需在 E05 等任务核对 `src/memory-process.js` / bridge；禁止硬编码端口 |
| 干净 clone 自安装 E2E | present | E08 `clean_bank_lifecycle`；E15 `scripts/verify_clean_install.py` + `reports/clean_install.json` |

## 契约主线 → 代码

| 任务 | 契约要点 | 状态 | 实际/等价落点 | 备注 |
|---|---|---|---|---|
| E01 | SourceRef / Coverage / 信任执法 | present | `evidence/source_contract.py`；`governance/workspace.py`；`evidence/object_store.py`；`ingestion/pipeline.py`；`core/service.py` capture/propose；`cognition/consolidate.py`；API observations/evidences | AC01–04 于 `tests/unit/test_source_coverage_contract.py` 通过（2026-11-01） |
| E02 | 修订 / 派生失效 / tombstone | present | `evidence/derivation.py`；migration `018_derivation_manifest`；`object_store.lineage` cycle；`forget_memory` tombstone | AC05–09 于 `tests/unit/test_derived_freshness.py` 通过 |
| E03 | AnswerSelection API/SDK | present | `cognition/answer_selection.py`；`POST /api/v1/cognition/answers/select`；Python `select_answer` / TS `selectAnswer`；OpenAPI | AC10–16 通过；与 CognitiveLoops 分离 |
| E04 | 完整上下文预算 | present | `retrieval/context_budget.py`；`build_context` 信封；`node_config.context_budget`；宿主 tool 透传 | AC17–19 通过 |
| E05 | DSH 接入当前选择 | present | `memory_select_answer`；`MemoryClient.selectAnswer`；`DshHttpAdapter.select_answer` | AC20–21 通过；故障→unavailable |
| E06 | 会话账本 / 恢复 | present | `cognition/session_checkpoint.py`；migration `019_session_checkpoint`；REST/SDK sessions.*；宿主 `recordSessionMessage` | AC22–25 通过；无独立 `events/` 包，账本在 cognition+API |
| E07 | UsageLedger / 成本 | present | `tracing/cost_ledger.py`；migration `020_usage_ledger`；`call_budget`；answers 入口执法；usage API | AC26–28 通过 |
| E08 | 健康探针 / 干净安装 | present | `tracing/health_probes.py`；health/probes/explain API；manifest 能力；clean_bank_lifecycle | AC29–31 通过 |
| E09 | 词法+多路融合 | present | `retrieval/fusion.py`；pipeline `fusion_rrf`；ablation script | AC32–34 通过；hash 向量不冒充 HQ embedding |
| E10 | 巩固 / 用途视图 | present | `consolidate.SessionConsolidationJob`；utility view；sessions/consolidate API | AC35–37 通过；后台默认关闭至 E11 |
| E11 | watermark / fencing | present | `strategy/watermarks.py`；`strategy/fencing.py`；migration `021_*` | AC38–40 通过；写层 fencing 拒绝旧租约 |
| E12 | 执行回执 | present | `execution_receipts.py`；FakeExecutor；executions API；migration `022_*` | AC41–43 通过；saved≠adopted≠execution_success |
| E13 | 归因反馈→有限学习 | present | `attributed_feedback.py`；strategy learning candidates；migration `023_*` | AC44–46 通过；污染拒绝；CAS 无半采用 |
| E14 | 渐进迁移 + 文本书领域 | present | `domains/text_book.py`；`migration/text_book_import.py` | AC47–50 通过；UTF-8 TXT 首域 |
| E15 | 全链路验收 | present | `scripts/verify_memory_continuity.py`；`tests/contract/test_memory_continuity_e2e.py`；`scripts/verify_clean_install.py`；`reports/FINAL_REPORT.md` + `CASE_RESULTS.json` | Final 413/11；连续性+干净安装 passed；AC01–50 对账 |

## 迁移与 API

- 迁移表：`service/src/cognitive_memory/storage/migrations.py`；本轮新增至 `023_*`（session/usage/watermark/fencing/execution/feedback）；历史双 `016_*` 不重写。
- OpenAPI：`service/schemas/openapi.v1.yaml`（含 `/cognition/answers/select` 等本轮入口）。
- Python SDK：`service/src/cognitive_memory/sdk/client.py`；TS SDK：`service/sdk-ts/src/`。
- 终验证据：`reports/FINAL_REPORT.md`、`reports/CASE_RESULTS.json`、`reports/memory_continuity.json`、`reports/clean_install.json`。

## 已满足且应复用（勿重做）

- Observation / Evidence / Claim / Verification 主链  
- CognitiveLoops + ValidationSuite 学习门禁  
- governance policy 执法（allow_store/forward/train/retain）  
- TMS / tombstone / ChangePlan / reevaluate  
- Domain Pack 加载注册基础设施（非 book 内容包）  
- DSH 0.2.1 工具 output.schema 形态与 engines 声明（host-compat 已测）

## 布局漂移记录

候选文件名以 TASK_INDEX 为准；若后续实现落在等价模块，在本文件追加「proposed → actual」行，并同步任务报告。E00 时点无产品功能改动。

# Domain Cognition & Fanqie Book Cognition — Phase 0 审计

**审计日期：** 2026-11-01  
**执行模式：** Repository Audit only（本阶段不改变生产行为）  
**任务书：** Mnemosyne Domain Cognition & Fanqie Book Cognition 完整工程制作总指令  

> **状态枚举约定：** Implemented / Partial / Planned / Missing / Blocked / External-Unverified  
> 禁止把 README / Roadmap 计划写成 Implemented。

---

## 0. 一句话结论

**可以开始 Domain Cognition，但本环境尚不能宣称「可运行的 Fanqie Book Cognition 全链路」。**

| 结论项 | 判定 |
|---|---|
| 权威实现仓（文档声明） | `canyueY/mnemosyne`（本机 `桌宠/mnemosyne`） |
| GitHub 现实 | `mnemosyne` **已 Archive**；`dsh-mnemosyne` **仍活跃**且含 Claim/TMS/Fault/P0.5 |
| Domain Pack 雏形 | **Partial**：`domains/` + Music pack 已有代码与测试（**未提交**到 `mnemosyne` main） |
| Book Domain | **Partial→Implemented（Pack）**：`domains/book/` 多文件本体 + 注册/门控测试（无 Fanqie） |
| Fanqie 仓库 | **Missing**（本地与 `canyueY/*` 均无 `dsh-fanqie-shelf-memory`） |
| Memory Bank 契约仓 | 远端存在；本机仅 `tmp/mnemosyne-memory-bank` 薄克隆 |
| P0.5 迁移门禁 | **仍生效**：`provenance_complete_rate=0.0` → **禁止生产 Claim 回填** |
| 安全推进建议 | Phase 1–2 先在权威仓落地 Domain Registry + Book Domain Pack；Fanqie 待仓库可用；不得在 Fanqie 私建 Claim/Evidence |

---

## 1. 仓库识别（实际路径 / Remote / HEAD）

| 角色 | 本地路径 | Remote | Branch / HEAD | 工作区 | 权威性 |
|---|---|---|---|---|---|
| **权威 Node（文档）** | `桌宠/mnemosyne` | `https://github.com/canyueY/mnemosyne.git` | `main` @ `49fdb16` | **脏**：多份 docs/代码已改；`domains/` **未跟踪** | 文档声明权威；GitHub **archived** |
| **DSH 插件 / 活跃 2.0 任务书实现** | `桌宠/dsh-mnemosyne` | `https://github.com/canyueY/dsh-mnemosyne.git` | `feat/taskbook-p6-p7-p8-p9-complete` @ `772e56d`（ahead 3） | `release-dist/` 未跟踪；含完整 Claim 栈 | **非** REPO_BASELINE 标尺；但含大量 2.0 任务书代码 |
| Memory Bank 契约 | `桌宠/tmp/mnemosyne-memory-bank` | `https://github.com/canyueY/mnemosyne-memory-bank.git` | `2c49685` | 仅 LICENSE/README | 契约仓；**不是**第二套记忆服务 |
| Music Domain 先例 | `桌宠/mnemosyne-music-acquisition` | `canyueY/mnemosyne-music-acquisition` | （独立组件） | `domain/music_domain_pack.v1.json` 存在 | Domain Pack 首个可复用范式 |
| Fanqie Book | — | — | — | — | **Missing** |
| 宿主 | `桌宠/Murasame`（及 `_dsh_plugins`） | `MurasamePet` 等 | — | — | Client only |

### 1.1 权威性冲突（必须人工确认）

本机 `mnemosyne/docs/REPO_BASELINE.md` 写明：

- 2.0 GA / Matrix **只认** `canyueY/mnemosyne`
- `dsh-mnemosyne` = Legacy / 插件发行壳，**不得**当标尺

但 GitHub 现实：

- `canyueY/mnemosyne`：`isArchived=true`（2026-10-31）
- `canyueY/dsh-mnemosyne`：活跃，描述自称「Mnemosyne 2.0…独立 Python Node」

**Phase 1+ 开写前必须由维护者确认：**

1. 是否解除 Archive 并继续以 `mnemosyne` 为唯一 Node；或  
2. 是否修订 REPO_BASELINE，把活跃 Node 迁到 `dsh-mnemosyne` / 新仓；或  
3. 是否做一次受控代码合流（禁止 silently 覆盖）。

本审计 **不擅自切换权威仓**，后续实现默认落在本地 `mnemosyne/`，并显式标注与 `dsh-mnemosyne` Claim 栈的差距。

---

## 2. 真实架构（从代码目录）

### 2.1 `mnemosyne`（权威声明仓）

已有包（`src/cognitive_memory/`）：

- Core：`core/`, `memory/`, `storage/`, `events/`
- Cognition：`cognition/`（schema_evolution / change_plan / feedback_learning / conflict / metacognition…）
- Evidence：`evidence/`
- Identity / Governance / Retrieval / Migration / MCP / API / SDK / Components / Adapters
- **Domain packs（WIP，未提交）：** `domains/{pack,registrar,retrieval}.py`

**没有：** `claims/`, `verification/`, `tms/`, `visibility/`, `fault/`, `strategy/`, `knowledge/`, `judgment/`, `migration_spike/`

### 2.2 `dsh-mnemosyne/service`（活跃任务书实现）

在权威仓之上额外具备（已落地代码，见既有 PLAN_COMPLETION_AUDIT）：

- Claim AST / Store / Lifecycle / Merge-Split / Relations
- VerificationEngine、Visibility、SecurityContext
- TMS / Knowledge / Experience / Strategy / MetaController
- Fault pipeline + rollback drill
- P0.5 migration_spike + P9 dry-run/scratch trial
- Domain packs 目录存在（与 mnemosyne 同构雏形；需核对是否已入库）

### 2.3 Memory Bank

`mnemosyne-memory-bank` README 强调：**不是第二套 Memory Bank 实现**。数据目录是 `%LOCALAPPDATA%\Mnemosyne Memory Bank`（见 REPO_BASELINE），与代码仓分离。

### 2.4 Domain Pack 数据流（已有雏形）

```
Domain Pack JSON (music_domain_pack.v1.json)
    → load_domain_pack / validate
    → register_domain_pack
    → CognitiveSchema (ontology only；不导入用户数据)
    → retrieval apply_domain_gate (意图门控)
    → Memory Core 仍管生命周期
```

这与任务书要求的 Domain→Registry→Schema→Memory Core 方向一致，**可复用**，但尚缺 Book Pack、版本升级回滚、完整 Ontology Schema 校验器。

---

## 3. 能力矩阵（代码证据）

### 3.1 Memory Core / Infra

| 能力 | mnemosyne | dsh-mnemosyne | 证据 | Book 相关缺口 |
|---|---|---|---|---|
| Memory 模型 / CRUD | Implemented | Implemented | `core/service.py`, API | — |
| Observation / Ingestion | Implemented | Implemented | `ingestion/` | 书架 Observation 契约未定 |
| Evidence 对象 | Partial→Implemented | Implemented(+independence) | `evidence/`；dsh 有 object_store | 图书证据类型未定义 |
| Entity | Partial | Partial | cognition/entity 面薄 | BookIdentity 消歧 Missing |
| Relation | Partial | Partial(+Claim relations) | schema_network；dsh judgment/relations | 人物关系时间轴 Missing |
| Event | Partial | Partial | event_types in pack | ReadingEvent 状态机 Missing |
| Bank / Scope / Identity | Implemented-lite | Implemented | identity/, banks | 多用户隔离需在 Book 写入强制 |
| REST `/api/v1` | Implemented | Implemented | `api/app.py` | Domain register API 需补 |
| MCP / SDK | Partial→Implemented | Partial | mcp/, sdk/ | Fanqie Adapter Missing |
| Worker / Outbox | Partial | Partial | jobs/, events | 探索任务预算 Missing |
| Backup / Node CLI | Implemented-lite | Partial | docs GA_STATUS | — |

### 3.2 Cognition / Epistemics

| 能力 | mnemosyne | dsh-mnemosyne | 判定 | 说明 |
|---|---|---|---|---|
| Schema Evolution | Implemented-lite | Implemented-lite | Partial | `schema_evolution`/`schema_ops`；领域升级策略未齐 |
| SCHEMA_MUTATION gate | 需对照 capability_gate | Implemented | Partial→Implemented(dsh) | dsh `governance/schema_governance.py` |
| ChangePlan | Implemented | Implemented | Implemented | 不可绕过 |
| Feedback Learning | Partial | Partial | Partial | 兴趣算法不可私建旁路 |
| Claim 纵向链路 | **Missing** | Near Complete | **缺口** | Book Claim 必须走现有模型或先扩展 Core |
| Verification | **Missing** | Implemented | **缺口** | 禁止 Fanqie 伪造 Verified |
| Visibility / 推理泄漏 | **Missing** | Near Complete | **缺口** | 跨 Bank opaque 404 已在 dsh |
| TMS / Knowledge | **Missing** | Implemented | **缺口** | — |
| Experience / Strategy / Meta | **Missing** | Near Complete | **缺口** | 自主探索可复用 Meta 边界，不可绕 Gate |
| Fault / Rollback | **Missing** | Near Complete | **缺口** | — |

### 3.3 Domain Cognition（本任务核心）

| 能力 | 状态 | 证据 | 缺口 |
|---|---|---|---|
| DomainRegistry | Implemented-lite | `domains/registry.py` + `registrar.py` | 回滚事务 / 细粒度权限解析仍 Incomplete |
| DomainPack loader | Implemented-lite | `domains/pack.py` | 多文件 Pack 已支持；完整 JSON Schema 仍弱 |
| Music Domain Pack | Implemented（组件侧） | `mnemosyne-music-acquisition/domain/music_domain_pack.v1.json` + 单测 | 可作 Book Pack 模板 |
| Book Domain Pack | Implemented（本体） | `domains/book/*` + `test_book_domain_pack.py` | Fanqie Adapter / Claim 写入仍 Missing |
| Domain Retrieval Profile | Partial→Implemented-lite | `domains/retrieval.py`（music+book） | 六路召回完整映射仍 Planned |
| Interest Cognition（Book） | **Missing** | — | 七维模型 / 反例 / 校准 Missing |
| Autonomous Exploration | **Missing**/Planned | MetaController 仅策略面 | KnowledgeGap / Stopping Policy Missing |
| Fanqie Memory Adapter | **Missing** | 无仓库 | — |

### 3.4 Retrieval

| 能力 | 状态 | 备注 |
|---|---|---|
| 语义 / 关键词 / FTS | Implemented-lite | `retrieval/` |
| 实体 / 关系 / 时间专用通道 | Partial | 未达任务书六路完备 |
| Epistemic Policy 准入 | Partial(dsh Visibility+policy) | mnemosyne 侧较薄；Adapter 不得绕过 |
| Context Assembly（Book） | Missing | — |

---

## 4. 前置风险（Phase 0.5 门禁）

### 4.1 历史 Memory 迁移 Spike（已完成只读）

证据：`dsh-mnemosyne/service/docs/2.0/P0_5_MIGRATION_SPIKE_REPORT.md` + `fixtures/.../spike_stats.json`

| 指标 | 值 |
|---:|
| sample_count | 100 |
| claim_extraction_rate | 0.18 |
| unresolved_rate | 0.82 |
| **provenance_complete_rate** | **0.0** |
| provenance_unknown_rate | 0.54 |

**硬门禁（仍生效）：**

- 禁止生产 Claim 回填 / 批量 Verified  
- 禁止为 Fanqie 伪造 Evidence 以抬升完整率  
- Book Domain 新增 **不得** 绕过该门禁  

### 4.2 四类认识论 / Schema mutation

- dsh：contracts + Verification/Visibility/Relation/Security 已加厚；SCHEMA_MUTATION 有 gate  
- mnemosyne：Schema Evolution 有，**Claim/Verification 模块缺失** → Book「Claim」若需要完整纵向链路，必须先在权威仓引入或正式合流 dsh 实现，**禁止 Fanqie 私建**

### 4.3 脏工作区

Phase 1–2 已在分支 `feat/domain-cognition-book-p1-p2` 提交（含 `domains/` + Book Pack + 单测）。  
正式方案文档见 `docs/domain-cognition/`（PART 14）。  
**禁止**未经确认 `reset` / 覆盖用户改动。后续工作继续在已确认权威仓策略下进行。

---

## 5. 可直接复用 vs 必须新增

### 复用

1. `domains/pack.py` + `registrar.py` + Music pack JSON 形态  
2. CognitiveSchema / ChangePlan / Schema Evolution  
3. Memory / Observation / Evidence 生命周期  
4. Identity / Bank / grants（权限骨架）  
5. dsh 侧 Claim/Verification/Visibility/TMS（若权威仓确认合流）  
6. Music Acquisition 的「组件仓 + domain pack + 宿主薄适配」工程范式  

### 新增（按任务书）

1. Book Domain Pack（实体/关系/事件/兴趣/检索/演化策略）— **本体 Done**；运行时消歧/Adapter 未做  
2. BookIdentity 消歧与候选实体 — **契约 Done** / 运行时 Missing  
3. Chapter 版本与目录漂移 — Planned（Pack 已声明约束）  
4. ReadingEvent 状态机 + ShelfSnapshot≠Reading — **契约+Pack Done** / Fanqie 运行时 Missing  
5. 七维兴趣 + 反例 + 校准 — **图式 Done** / 算法 Planned  
6. Fanqie 插件仓与 MemoryAdapter — **Blocked**（仓 Missing；契约草案在 `docs/domain-cognition/fanqie-contracts/`）  
7. KnowledgeGap / ActionSelector / StoppingPolicy — Planned（Phase 7）  
8. Memory Bank 契约文档同步 — **草案 Done**（`memory-bank-contracts/`；正式 PR 需完整 clone）  

### 明确禁止

- Fanqie 独立长期记忆 / 兴趣画像 / 平行向量库 / 第二套 Evidence / Schema Evolution  
- 删除测试、关 CI、改断言过关  
- 把 Archive 仓与活跃仓 silently 混写  

---

## 6. 缺失仓库与阻塞

| 项 | 状态 | 影响 |
|---|---|---|
| `dsh-fanqie-shelf-memory` | Missing | Phase 3–8 Fanqie 实现与跨仓 E2E **Blocked** |
| `mnemosyne-memory-bank` 完整工作区 | 仅 tmp 薄克隆 | 契约同步可做草案；正式 PR **需完整 clone** |
| 权威仓 Archive | Blocked（发布） | 可本地开发；公开发布路径需人工决策 |
| Claim 栈不在权威仓 | Blocked（完整认识论） | Book Claim/Verified 需合流或移植方案 |

**允许在阻塞下继续：** Phase 1–2（Domain Registry 加固 + Book Domain Pack + 单测），全部落在 Mnemosyne，不伪造 Fanqie E2E。

---

## 7. 建议实施顺序（修正后）

| 阶段 | 内容 | 前提 |
|---|---|---|
| **Phase 0** | 本审计 | ✅ 完成 |
| **Phase 0.5a** | 权威仓决策（Archive / 合流 / 改 Baseline） | 人工确认 |
| **Phase 0.5b** | Claim/Verification 是否迁入权威仓的最小方案（文档+差距，不伪造） | 0.5a |
| **Phase 1** | Domain Registry 加固（校验/版本/停用/权限）；提交现有 Music domains WIP | 不碰生产 DB |
| **Phase 2** | Book Domain Pack + Schema 校验 + 注册测试 | Phase 1 |
| **Phase 3+** | Fanqie 感知/导入 | **需要 Fanqie 仓库** |
| **Phase 4–5** | 证据 / 兴趣 | 依赖 Core 认识论能力 |
| **Phase 6–8** | 检索 / 探索 / E2E | Fanqie + Core 齐备 |

---

## 8. Phase 0 验收清单

- [x] 识别真实仓库与 Remote / HEAD / dirty  
- [x] 区分计划功能与代码实现  
- [x] 能力矩阵（有文件位置级别证据）  
- [x] P0.5 provenance 门禁重申  
- [x] Music Domain 可复用范式确认  
- [x] Fanqie Missing 明确列出  
- [x] 权威仓 Archive 冲突上报  
- [ ] 人工确认权威仓与 Claim 合流策略（**未完成，阻塞完整 Book Claim**）  

---

## 9. 下一步

**已完成：** Phase 1–2 代码 + PART 14 方案套件 + 0.5b Claim 合流方案文档 + Fanqie/MemoryBank 契约草案。

**待维护者：**

1. 确认权威仓选项 A/B/C（见 `docs/domain-cognition/AUTHORITY_AND_CLAIM_PLAN.md`）。  
2. 完整 clone `mnemosyne-memory-bank` 后按 `memory-bank-contracts/SYNC_CHECKLIST.md` 开契约 PR。  
3. 提供或授权创建真实 `dsh-fanqie-shelf-memory` 后进入 Phase 3 实现。  
4. **不创建** Fanqie 空壳仓冒充 E2E。  

---

**审计人：** Composer（Agent）  
**本文件路径：** `mnemosyne/docs/audits/domain-cognition-audit.md`  
**副本建议：** 同步到 `dsh-mnemosyne/service/docs/2.0/audits/`（只读镜像，避免双源真相）

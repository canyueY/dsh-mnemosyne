# 统一实现契约 v1

这是拟实现规格，不代表新增能力已存在。E00 核对当前实现，再逐项落实。逻辑契约优先映射既有对象、Service 和元数据，不要求一个概念一个表。遵循 service/docs/ENGINEERING_CONSTRAINTS.md 的实际策略执法。

## 1. 权威、兼容与鉴权

- 一个 Node 管理自己的 Memory Bank 数据目录；不另起认知服务、数据库或七套用途库。
- 来源拥有原文，Node 管认知修订，宿主/执行器拥有执行权限及实际状态；派生视图不另成事实权威。
- 旧 search/retrieve、CognitiveTask、Claim/Verification 保持语义兼容。普通检索继续返回候选，新选择入口显式调用。
- REST 沿用配置前缀，默认 `/api/v1`。actor 来自受验证上下文，body 的 actor/Bank 不能授予权限。授权失败按既有 403/404 风格；合法查询证据不足返回 unknown；基础设施故障仍是错误/unavailable，不能全捕获成 unknown。
- verified 仍由 VerificationEngine 授权记录产生；选择答案、摘要、自述或好评不能自动晋升。偏好自述可带 self_report 标签引用，不等同独立客观核验。
- 使用既有 schema_migrations，分配实施时下一个 ID，不预锁死为 018；不重写已应用迁移。覆盖旧库、备份、升级失败恢复及兼容读取。
- 时间用 aware UTC，并保留原始 offset/precision（可用时）；有效区间 `[from,to)`；未知发生时间不能用接收时间伪造。

## 2. 共用标识与状态

逻辑事件含 id、bank_id、producer、recorded_at、可选 occurred_at、idempotency_key、correlation_id、schema_version。来源信任/权限、认识状态、用途显著性和运行新鲜度分别管理，不混成一个 confidence。

派生缓存依赖包含来源修订、算法及配置版本。至少一次投递配合幂等消费，不承诺外部系统无法保证的全局恰好一次。

## 3. SourceRef / Coverage

SourceRef 至少含 source_id、root_source_id、revision、content_hash、locator、source_role、bank_id。角色区分 user_statement / author_claim / tool_observation / derived_summary，可与现有枚举映射。

Coverage 含 available_modalities、covered_ranges、missing_ranges、completeness=complete|partial|unknown。位置可含 chapter/paragraph/page/time_range；未知 page=null。complete 仅表示覆盖，不表示内容真实或对所有 Claim 充分。

已认证生产者与登记的外部规范 ID 决定稳定根来源，不接受任意 root_source_id 来自证独立性。采集、注入、巩固、导出及训练继续执行现有存储/转发/训练/保留策略。

原文修改产生新 revision；授权删除/撤回保留防复活标记，但不要求保留被授权删除的私密原文。哈希只证明内容一致性，不证明身份或内容真实。

## 4. DerivationManifest

含 derived_id、artifact_kind、dependencies[{id,revision,hash}]、processor_version、config_hash、cursor、freshness=current|stale|rebuilding|failed、reason_codes。

源变化先阻止旧产物作为当前证据，再异步重建；重建失败不恢复 current。读取和结果提交检查修订，防止处理中变更。依赖缺失、环路、越权有界失败关闭，不误删除仍有独立支持的其他记录。

## 5. AnswerSelection

拟新增 `POST {prefix}/cognition/answers/select`，SDK 方法 `select_answer` / `selectAnswer`；按仓库约定改名须记录并同步消费者。

请求：bank_id、query、可选 target{subject_id,predicate}、context、at、mode=current|history、budget{max_steps,max_candidates,timeout_ms}。at 缺省由可注入 clock 取得当前时刻；候选 ID 不得成为绕过鉴权的可信输入。

响应至少含 decision_id、status=chosen|partial|unknown、selected[]、alternatives[]、evidence_refs、counterevidence_refs、source_revisions、applicability、checks_complete、reason_codes、budget_usage、trace_id。条目有引用类型/ID、认识标签、时间和情境，不仅一段自由文本。

chosen 表示在当前范围内，证据和检查足以支持有限结论，不自动写数据库 verified。partial 可给带标签候选但不作确定事实；unknown 不给无依据唯一答案。选择是无认识状态写入副作用的读取。

支持的有限目标由结构化 target 或现有 parser 确定；不支持的自由文本返回 unsupported_query/needs_target，不能按关键词相似度假装一般矛盾理解。reason codes 至少覆盖 source_missing、coverage_insufficient、source_stale、counterevidence_pending、context_mismatch、budget_exhausted。

既有 CognitiveLoops 面向 allowed_actions，陈述选择不得伪造动作偷用接口。抽取共享权限/证据/预算函数，避免复制两套核验。

## 6. ContextManifest / Checkpoint

包含 session/task/Bank、稳定快照修订、用户约束、已验证进展、证据修订、pending_questions、pending_external_operations、delivered_cards。交付事件稳定去重；delivered 只证明宿主交付，不证明模型理解。

执行状态区分 prepared/requested/acknowledged/effect_confirmed/unknown/reconcile_required；模型口述不构成回执。恢复重新授权并核对来源新鲜度。未知副作用先核对，不盲重试。

完整预算含稳定前缀、工具、历史、记忆标签和输出预留，measurement 区分 measured/estimated/unknown。没有准确 tokenizer/完整宿主请求时，不宣称真实精确 token 硬上限；仍实施调用数、候选数与耗时限制。

## 7. UsageLedger / 健康

按 correlation/attempt/provider/meter_scope 去重，meter_scope 分 host/node/embedding/retry；预留和实测回执分开。unknown 不转零，零 Node 成本不转总成本零。

健康状态区分 OK/WARN/ERROR/PENDING/UNKNOWN，默认查询只读；写探针仅在专用隔离空间执行。保存状态字段不等于已经验证真实读写、索引或 worker。

## 8. 消费与巩固一致性

utility 与共检索统计各有消费者水位；业务效果和本消费者水位同事务。用途反馈不改验证状态。

实际产物提交携带有效 fencing token/代次和 expected_version；不能只在最终 task UPDATE 检查。外部文件产物用阶段日志/校验/恢复，不假称与 SQLite 单事务。E10 后台能力在 E11 通过前默认关闭。

## 9. Action / Receipt / Outcome / 学习

含 proposal_id、execution_id、correlation_id、actor、bank_id、capability、expected_state_version、idempotency_key、phase、payload_hash、receipt_source、observed_at。

计划、配置保存、运行采用、执行确认、效果观察和长期规则采用分别记账。超时未知不从“没有失败”推断成功。

以隔离 fake executor 验证协议，不授予 Node 平台执行权。反馈沿用 TaskFeedback 分类，归因默认 unresolved；采用复用 ValidationSuite、权限、版本 CAS 和回滚，不另造 RL 引擎。

公开验收病例均会被实施者看到，只证明机制；真正学习验证还需来源隔离的独立样本。没有授权真实任务数据，效果记 not_measured。

## 10. 接入面与解释

新增公开能力同步 Service、REST、Python/TS SDK、OpenAPI、薄宿主消费（适用时）、组件权限检查和真实链路测试。不能只加字段、注册工具或 mock 后声称已可用。

解释至少包含来源版本、适用时间/情境、选择及未选择理由、变更关系、新鲜度和费用测量状态。“为什么变化”分别表达观察到的顺序、提供者说明和推测，不自动作因果证明。

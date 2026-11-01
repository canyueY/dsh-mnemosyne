# 检索质量与认知正确性 契约 v1

这是拟实现规格，不代表能力已存在。M00 先核对当前实现，再逐项落实。逻辑契约优先映射既有对象、Service 和元数据，不要求一个概念一张表。

## 1. 检索与裁决分离

- retrieve/rank 只输出候选：`id`、`sort_score`、`retrieval_reason`、`sources`、`fusion_method`。
- select/decide 输出：`decision_id`、`status ∈ {chosen, partial, unknown}`、`selected[]`、`alternatives[]`、`evidence_refs`、`counterevidence_refs`、`source_revisions`、`applicability`、`checks_complete`、`budget_usage`、`reason_codes`、`trace_id`。
- `chosen` 不写数据库 `verified`；`verified` 只由 VerificationEngine 产生。

## 2. 证据与时间

- 证据独立性：`support_root` 集合按 `root_source_id + revision + content_hash` 去重；换 producer/导入格式不改变根。
- 时间适用性：`validity [from, to)`；状态 `proposed/supported/qualified/verified/disputed/rejected/retracted/expired/superseded`。
- 查询输出 `applicability ∈ {applicable_now, applicable_at, not_yet, expired, superseded, unknown}`。
- `recency` 只是排序先验，不是事实概率。

## 3. 检索融合

- `fusion_method ∈ {rrf, none}`；多路来源显式记录 `sources[]` 与各路 `rank`。
- 反证召回：支持证据与 `counterevidence_refs` 同时进入 select，不被正分淹没。
- 本地哈希只作为 `candidate_similarity`；没有真实 embedder 时，`semantic_model = unavailable`。

## 4. 巩固

- `consolidation_level ∈ {episodic, semantic_claim, derived_summary, long_term}`。
- `importance_score`、`lossy(bool)`、`source_refs[]` 必填；lossy 摘要必须保留来源引用。
- 遗忘=压缩+标记，不删除原始 append-only 记录。

## 5. 预算信封

- `budget_envelope` 至少含 `stable_prefix`、`tools_descriptions`、`history`、`memory_tags`、`output_reserve`。
- 每项 `measurement ∈ {measured, estimated, unknown}`；`output_reserve` 是硬约束。
- 没有准确 tokenizer/完整宿主请求时，不宣称精确 token 硬上限。

## 6. 账本与恢复

- `UsageLedger` 键：`correlation_id + attempt + provider + meter_scope`；`reserved` 与 `actual` 分开；`unknown` 不转零。
- `meter_scope ∈ {host, node, embedding, retry}`。
- `consumer_watermark` 按 `(consumer_id, scope)` 单调推进；业务效果与水位同事务。
- checkpoint 携带 `fencing_token`、`代次`、`expected_version`；写前校验，恢复重新核对来源新鲜度。

## 7. 通用

- 核心逻辑留在 `service/`；测试不得伪造 passed；skip/xfail 不计通过。
- 外部模型能力状态记 `disabled/not_measured`，不宣传一般语义理解或神经认知已成立。

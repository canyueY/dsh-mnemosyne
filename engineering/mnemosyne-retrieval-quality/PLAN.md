# 现状评估与改进方案

## 1. 根因

6 条距离的根因一致：当前把“排序信号 / 缓存命中 / 哈希相似度 / token 估算 / cost 字段”当成了“认识状态 / 时间适用性 / 语义质量 / 完整预算 / 权威账本”。改进不是加一个分数，而是拆成正交链路，各自用明确算法和状态机。

## 2. 逐条方案

### M01 检索候选 → 认识裁决

`retrieve（assistive） → rank（候选） → select/decide（epistemic）`。

- retrieve/rank 只输出 `candidate + sort_score + retrieval_reason`。
- select 输出 `decision_id + status(chosen|partial|unknown) + evidence_refs + counterevidence_refs + checks_complete + budget_usage + reason_codes`。
- `chosen` 表示“当前范围内证据足够支持有限结论”，不写 `verified`；`verified` 仍由 VerificationEngine 产生。

### M02 证据独立性与时间适用性

- 独立性：支持/反对的 justification graph，按 `root_source_id + revision + content_hash` 求独立支持根，不按计数。
- 时间：`validity [from, to)` + `superseded/retracted/expired` 状态机；查询时刻求区间交集，输出 `applicable_now / applicable_at(t) / not_yet / expired / superseded`。
- 标量 `recency` 只作排序先验，不作事实概率。

### M03 多路召回 + RRF + 反证

确定性：`BM25/FTS + lexical + 规则反证召回`，用 Reciprocal Rank Fusion 融合。
研究/外部：dense embedding + cross-encoder，标记 `disabled` 直到接入真实模型。

### M04 分层巩固

`episodic → salience(recency/importance/relevance) → semantic claims → evidence-linked summaries → 长期整合`。
- importance 用规则/小模型打分；遗忘=压缩成 lossy 摘要 + 来源保留，不删原始记录。
- 多层 Dream 的“神经整合”标记为研究，不用一次 LLM 重写冒充。

### M05 完整请求预算信封

`total = stable_prefix + tools_descriptions + history + memory_tags + output_reserve`。
- 每项 `measured|estimated|unknown`；输出预留是硬约束；工具描述/历史/标签各有硬上限。

### M06 统一账本 + 消费水位 + 会话恢复

- UsageLedger 键 `correlation_id + attempt + provider + meter_scope(host/node/embedding/retry)`；`reserved` 与 `actual` 分开。
- outbox + 幂等 key；业务效果与 consumer watermark 同事务。
- checkpoint 携带 `fencing_token / 代次 / expected_version`，写前校验；恢复重新核对来源新鲜度。

## 3. 轨道划分

| 能力 | 确定性工程 | 研究/外部模型 |
| --- | --- | --- |
| 语义/嵌入 | BM25/FTS + lexical + RRF 框架 | dense embedding、cross-encoder |
| 巩固 | 分层流水线 + importance(规则) + lossy 摘要 + 来源 | 神经式多层 Dream |
| 因果/概率 | 时间区间 + 状态机 + 独立性 graph | 一般因果推断、概率推理 |

研究/外部轨道：任务里写明接口位，但**不实现、不宣传**，状态记 `disabled/not_measured`。

## 4. 不变量

- 检索命中/排序不写 verified；证据计数不映射概率。
- 时间适用性用区间，不用标量衰减冒充。
- 没有真实 embedder 前，哈希只叫“候选相似度”。
- token 估算不宣称精确硬上限；cost 不宣称权威账本。
- 消费至少一次投递 + 幂等；跨设备用权威 Bank + fencing/版本，不做无脑双写。

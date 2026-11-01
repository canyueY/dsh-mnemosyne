# Mnemosyne 检索质量与认知正确性 工程执行包

用途：交给 Cursor Composer 2.5 Fast 直接执行，补齐“基础能力”与“目标体验”之间的 6 条距离。本包把工作拆成**确定性工程**与**研究/外部模型**两条轨道，避免把启发式/外部模型写成已交付。

## 6 条距离 → 任务映射

| 距离 | 任务 |
| --- | --- |
| 1. 检索候选 ≠ 任务闭环 / 事实确定 | M01 |
| 2. 证据数量 ≠ 独立支持 / 事实概率；时间降权 ≠ 时间适用性 | M02 |
| 3. 本地哈希 ≠ 语义；FTS ≠ 多路召回/融合/反证 | M03 |
| 4. rule_v1 截取式巩固 ≠ 多层 Dream / 长期整合 | M04 |
| 5. 内容 token 估算 ≠ 完整请求预算 | M05 |
| 6. Trace.cost ≠ 共用账本；checkpoint ≠ 会话恢复 | M06 |
| 全链路验收 | M07 |

## 两条轨道

- **确定性工程（默认完成）**：M01、M02、M03 的 BM25/FTS + RRF 框架 + 规则反证、M04 的分层巩固 + 来源保留 + lossy、M05 预算信封、M06 账本/水位/fencing。
- **研究/外部模型（后置，标 disabled/not_measured）**：M03 的 dense embedding / cross-encoder（需真实模型）、M04 的神经式多层 Dream、M02 的一般因果与概率推断。这些**不要求**本轮实现，但任务文件里必须明确“不做/外部/研究”，不得用规则冒充。

## 开始执行

1. 在 Cursor 打开 `dsh-mnemosyne` 仓库根目录。
2. 粘贴 `COMPOSER_PROMPT.md` 正文，或引用本目录 `START_HERE.md`。
3. 按 `TASK_INDEX.json` 的 `execution_order` 执行 M00—M07。
4. 每轮只读当前任务、必要契约和相关代码；用临时 Bank / fake executor 隔离验证，不碰生产数据。
5. 完成后更新 `PROGRESS.json` 与 `reports/M##.md`。

## 文件用途

- `COMPOSER_PROMPT.md`：可直接粘贴给 Composer 的执行指令。
- `PLAN.md`：现状评估、改进方案与轨道划分。
- `CONTRACTS.md`：字段、状态机与不变量。
- `TASK_INDEX.json`、`tasks/M00.md—M07.md`：依赖、落点、步骤与完成标准。
- `ACCEPTANCE_CASES.json`：公开验收反例（given/when/expected）。
- `VERIFICATION.md`、`tools/run_checks.ps1`：真实命令与结果记录。
- `PROGRESS.json`：初始 pending；执行者维护，不是已完成报告。

## 交付门槛

不是只加字段或 mock：功能必须接入实际 Service/API/宿主调用链并通过真实回归；外部模型能力明确标 disabled/not_measured，不宣布一般语义理解或神经认知已成立。

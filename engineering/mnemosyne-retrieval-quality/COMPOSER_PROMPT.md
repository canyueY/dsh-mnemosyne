请按 engineering/mnemosyne-retrieval-quality/START_HERE.md 实施 Mnemosyne 检索质量与认知正确性改进。工作目录是当前 dsh-mnemosyne 仓库。这是代码实施请求，不停留在重新规划或只生成文档。

1. 先读 START_HERE.md、PLAN.md、CONTRACTS.md、TASK_INDEX.json、VERIFICATION.md 和 PROGRESS.json，再仅载入当前任务及相关代码。从 M00 建基线开始；续做以进度和实际测试为准，不信任口头“已经完成”。
2. 按 execution_order 完成 M00—M07。复用现有 Observation/Evidence/Claim、Verification、AnswerSelection/CognitiveLoops、Consolidate、Context、Tracing、Outbox、staging 与权限；不造平行系统。已有实现满足时用实际测试证明并复用。
3. 每步：最小实现 → 相关回归与当前 AC 反例 → 真实入口集成 → reports/M##.md → PROGRESS.json → 下一任务。可以拆小但保留原 ID 和验收映射；不要所有代码写完才测。大型 app.py/index.js 只补薄入口，业务保持模块化。
4. 文件名是建议落点。布局变化就定位等价实现并记录；不为了文件名重造模块。契约调整在 reports/DECISIONS.md 写兼容方案并同步消费者与测试。
5. 严格区分两条轨道：确定性工程默认完成；研究/外部模型后置。禁止用规则/哈希冒充语义理解、用排序命中冒充事实、用 token 估算冒充精确硬上限、用 cost 字段冒充权威账本。
6. 用临时 Bank、隔离环境和可控 fake executor。权限与 allow_store/forward/train/retention 等策略在实际入口执行；不直连核心 SQLite；不读私人配置/token；不执行生产迁移、部署、发布或破坏性清理；保留已有未提交改动。
7. 区分新回归、原有失败和环境缺失。不能删测试、削弱断言、伪造 passed、把 skip/xfail 算通过。不用历史报告的测试数代替当前运行。
8. 每个能力都要有失败可见路径：来源缺失、覆盖不足、来源过期、反证待审、情境不匹配、预算耗尽、账本 unknown、水位不一致，都要给出结构化 code/error/hint，不静默成功。
9. 未知保持 unknown/unavailable/not_measured；配置保存不等于运行采用和效果成功。
10. 最后执行 M07：`npm test`、`npm run check`、Python 测试、`git diff --check`、50 个既有病例对账与新增验收；外部模型能力如实标 disabled/not_measured。未满足完成标准保持 partial/blocked。

现在从尚未完成且依赖满足的最早任务开始，直接实施。

请按 engineering/dsh-integration/START_HERE.md 实施 Mnemosyne 的 DSH 安装、迁移与接口适配。工作目录是当前 dsh-mnemosyne 仓库。这是代码实施请求，不停留在重新规划或只生成文档。

1. 先读 START_HERE.md、PLAN.md、CONTRACTS.md、TASK_INDEX.json、VERIFICATION.md 和 PROGRESS.json，再仅载入当前任务及相关代码。从 D00 建基线开始；续做以进度和实际测试为准，不信任口头“已经完成”。
2. 按 execution_order 完成 D00—D06。核心认知逻辑留在 `service/`，DSH 只做薄宿主：安装/迁移/设置/preset/MCP 都不得复制业务逻辑、不得直连核心 SQLite、不得读私人 token。
3. 每步：最小实现 → 相关回归与当前 AC 反例 → 真实入口集成 → reports/D##.md → PROGRESS.json → 下一任务。可以继续拆小但保留原 ID 和验收映射；不要所有代码写完才测。大型 app.py/index.js 只补薄入口，业务保持模块化。
4. 文件名是建议落点。若布局变化，定位等价实现并记录；不为了文件名重造模块。必要契约调整写兼容方案并同步消费者与测试。
5. 隔离验证：用临时 `DSH_HOME` 和临时 Memory Bank；`dsh plugin --profile <profile> add file:<绝对路径>` 必须实测可用；profile 改动前备份，幂等可重跑，可回滚。不覆盖 profile 中其他 bundle/MCP/preset。
6. 区分新回归、原有失败和环境缺失。不能删测试、削弱断言、伪造 passed、把 skip/xfail 算通过。不用历史报告的测试数代替当前运行。
7. 安装与迁移都要有失败可见路径：profile 不存在、装错 profile、缺 node_modules junction、版本不匹配、数据目录被占用、迁移中断，都要给出可诊断错误与下一步，不能静默假装成功。
8. DSH 接口逐项适配 0.2.1 约定：工具 `output.schema` 用 `{ type: 'object', additionalProperties: true }`；`apply`/`execute(args, exec)` 转发 `exec.signal`；`engines.dsh` 版本区间；`cordis.patch.yml`/bundle patch；settings namespace；agent preset；MCP stdio 与 `mcp__*` 命名。旧 search/retrieve 语义兼容。
9. 未知保持 unknown/unavailable/not_measured；配置保存不等于运行采用和效果成功。
10. 最后执行 D06：`npm test`、`npm run check`、Python 测试、`git diff --check`、隔离安装/迁移/回滚实验、设置/preset/MCP 端到端，并更新根目录 `DSH_INTEGRATION.md`。未满足完成标准保持 partial/blocked。

现在从尚未完成且依赖满足的最早任务开始，直接实施。

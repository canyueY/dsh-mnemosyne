请按 engineering/review-fixes/START_HERE.md 修复 Mnemosyne 三工程联合复核报告中的问题。工作目录是当前 dsh-mnemosyne 仓库。这是代码修复请求，不停留在重新规划或只生成文档。

1. 先读 service/reports/three-package-review-2026-11-01/REVIEW.md，再读本包 START_HERE.md、CONTRACTS.md、TASK_INDEX.json、VERIFICATION.md 和 PROGRESS.json。从 F00 复现基线开始；续做以复现证据和实际测试为准，不信任口头“已经修好”。
2. 按 execution_order 完成 F00—F12。复用现有 Evidence/Claim、Verification、AnswerSelection、Checkpoint、Migration、MCP gateway、Retrieval、Context、Jobs；不造平行系统。已有实现满足时用实际测试证明并复用。
3. 每步：最小修复 → 对应反例与相关回归 → 真实入口集成 → reports/F##.md → PROGRESS.json → 下一任务。可以拆小但保留原 ID 和验收映射；不要所有代码写完才测。大型 app.py/index.js 只补薄入口，业务保持模块化。
4. 文件名是建议落点。布局变化就定位等价实现并记录；不为了文件名重造模块。契约调整在 reports/DECISIONS.md 写兼容方案并同步消费者与测试。
5. 隔离验证：用临时 Bank、临时 DSH_HOME、干净 npm 打包产物；不读私人配置/token、不执行生产迁移、部署、发布或真实账号操作；保留已有未提交改动。
6. 区分新回归、原有失败和环境缺失。不能删测试、削弱断言、伪造 passed、把 skip/xfail 算通过。不用历史报告的测试数代替当前运行。
7. 每个缺陷都要有失败可见路径：跨 Bank 写入、目标不匹配、拒绝状态、preset 未注册、打包缺文件、stdio 无响应、迁移阶段未完成、过期候选回退、计量标签错误、其他插件配置被改，都要给出结构化 code/error/hint 或明确的非成功结果，不静默假装成功。
8. 修复顺序按报告：先 R01—R03，再 R08—R09，再 R05/R04/R06，再 R07/R10，最后补证链路与全量复验。研究/外部模型保持 disabled/not_measured，本轮不作为缺陷。
9. 未知保持 unknown/unavailable/not_measured；配置保存不等于运行采用和效果成功。
10. 最后执行 F12：复跑 reproduce 脚本、`npm test`、`npm run check`、Python 测试、SDK 构建、`git diff --check`，并按实际结果更新 E/D/M 三套 PROGRESS 与最终报告。未满足完成标准保持 partial/blocked。

现在从尚未完成且依赖满足的最早任务开始，直接实施。

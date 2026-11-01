请按 engineering/mnemosyne-next/START_HERE.md 实施 Mnemosyne 工程改进。工作目录是当前 dsh-mnemosyne 仓库。这是代码实施请求，不停留在重新规划或只生成文档。

1. 先读 START_HERE.md、CONTRACTS.md、VERIFICATION.md、TASK_INDEX.json 和 PROGRESS.json，再仅载入当前任务及相关代码。从 E00 建基线开始；续做以进度和实际测试为准，不信任口头“已经完成”。REFERENCE_PLAN.md 和原始长文只在必要时查阅，不必一次全部装入上下文。
2. 按 execution_order 完成 E00—E15 的确定性 P0/P1。复用现有 Observation/Evidence/Claim、Verification、ChangePlan、CognitiveLoops、队列、staging 和权限。不造平行系统；已有实现满足时用实际测试证明并复用。
3. 每步执行：最小实现 → 相关回归与当前 AC 反例 → 真实入口集成 → reports/E##.md → PROGRESS.json → 下一任务。大任务可继续拆小，但保留原 ID 和验收映射；不要所有代码写完才测。大型 app.py/index.js 只补薄入口，业务逻辑保持模块化。
4. 未来文件名是建议落点。若布局变化，定位等价实现并在 IMPLEMENTATION_MAP.md 记录；不要为了文件名重造模块。必要契约调整在 reports/DECISIONS.md 写兼容方案并同步消费者和测试，不用反复请求普通工程取舍确认。
5. 用临时 Bank、隔离环境和可控 fake executor。权限及 allow_store/forward/train/retention 等策略在实际入口执行；Adapter/脚本不直连核心 SQLite；不读私人配置/token、不执行生产迁移、真实平台副作用、部署、发布或破坏性清理。保留已有未提交改动。
6. 区分新回归、原有失败和环境缺失。不能删测试、削弱关键断言、伪造 passed、把 skip/xfail 算通过。可按项目声明配置隔离开发依赖，不安装对比项目、大模型或付费服务。不用历史报告的测试数代替当前运行。
7. 默认本地确定性路径。默认 hash 不宣传成成熟语义模型；读取、引用、满意或自评不变成事实增信；配置保存不等于运行采用和效果成功。未知就保持 unknown/unavailable/not_measured，并说明范围。
8. 遇到普通工程取舍自行记录推进；只有真正缺少必需信息、权限或外部状态时才记 blocker。继续不依赖该 blocker 的工作；禁止编造通过或为了闭环绕过治理。
9. 上下文不足时先记录当前文件、已改内容、最近命令及失败、下一最小步骤和 next_task。新会话从进度及报告恢复，不重新全量分析。未满足完成标准保持 partial/blocked，不标 done。
10. 最后执行 E15：完整 Python/Node 测试、SDK 构建、原 theory 与新连续性脚本、隔离新环境安装、50 个病例对账及最终报告。无授权真实任务数据则用户效果记 not_measured，后置研究记 disabled，不宣布一般自主学习或“3.0 已发布”。
11. 把「DSH 能自行完成安装」当作验收目标，而不是只写 README。保持根目录 `package.json` 的 `engines.dsh: >=0.2.1-0 <0.3.0`、`dsh.plugin.json` 的 `entry.inject=["tools"]`、`cordis.patch.yml` 与 bundle patch 机制有效；从干净 clone 按 README 顺序执行 `npm install` → `dsh plugin --profile <profile> add github:canyueY/dsh-mnemosyne#main`（或宿主等价 `install_bundle`）→ 重启/F5 后 settings 可见可用。安装与桥接自动发现 profile 和 DSH 端口（端口会漂移，禁止硬编码），验证 DSH 能启动/守护/健康回收 Node，且 profile 修改前备份、幂等可重跑。装错 profile、只装子包、缺 `node_modules` junction 或没刷新页面时，要输出可诊断错误，不能静默假装成功；安装过程不直连核心 SQLite、不读私人 token、不覆盖 profile 中其他 bundle/MCP/preset。
12. 把 DSH 当作薄宿主，核心逻辑留在 Node；新增能力经显式入口接入，不复制核心逻辑到插件。逐项适配并做真实链路测试（`src/test/host-compat-021.mjs` 与现有 thin e2e）：0.2.1 工具 `output.schema` 使用 `{ type: 'object', additionalProperties: true }`；`apply`/`execute(args, exec)` 转发 `exec.signal`；`engines.dsh` 版本区间；`cordis.patch.yml` / bundle patch / preset；MCP server 与 `mcp__*` 工具命名；settings namespace、`session/create`、`agentPreset`、`selectModel`、`projections`、`$events` 等宿主接口按当前 DSH 0.2.1 约定适配；桥接用 `discoverDshBaseUrl()` 或等价发现。旧 search/retrieve 保持语义兼容。DSH 升级后先跑 `npm run check` 与 host-compat 脚本再宣布兼容，等价实现位置记入 IMPLEMENTATION_MAP.md。

现在从尚未完成且依赖满足的最早任务开始，直接实施。

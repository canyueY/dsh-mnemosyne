# Mnemosyne DSH 安装/迁移/接口适配 工程执行包

用途：交给 Cursor Composer 2.5 Fast 直接执行。目标是让 Mnemosyne 及其附属项目具备**稳定、幂等、可回滚的 DSH 安装与迁移办法**，并补齐两个尚未完成的 DSH 接口面：

1. DSH 设置页 namespace（配置可视化，Secret 脱敏，安全默认值）。
2. 核心记忆能力的 Agent preset 与 MCP 接口（不只 `defineTool` 一种入口）。

## 开始执行

1. 在 Cursor 打开 `dsh-mnemosyne` 仓库根目录。
2. 粘贴 `COMPOSER_PROMPT.md` 正文，或引用本目录 `START_HERE.md`。
3. 按 `TASK_INDEX.json` 的 `execution_order` 执行 D00—D06。
4. 每轮只读当前任务、必要契约和相关代码；用临时 DSH_HOME / 临时 Bank 做隔离验证，不碰真实 profile 和个人数据。
5. 完成后更新 `PROGRESS.json` 和 `reports/D##.md`。

## 文件用途

- `COMPOSER_PROMPT.md`：可直接粘贴给 Composer 的执行指令。
- `PLAN.md`：安装/迁移现状评估与改进方案（设计依据）。
- `CONTRACTS.md`：不可破坏的 DSH 0.2.1 接口契约与安装/迁移不变量。
- `TASK_INDEX.json`、`tasks/D00.md—D06.md`：依赖、代码落点、步骤、回归与完成标准。
- `ACCEPTANCE_CASES.json`：公开验收反例，含 given/when/expected。
- `VERIFICATION.md`、`tools/run_checks.ps1`：真实命令与结果记录。
- `PROGRESS.json`：初始全部 pending；执行者维护，不是已完成报告。

## 默认范围

- 只做 DSH 宿主层的安装、迁移与接口适配；核心认知逻辑继续留在 `service/`，不复制、不直连 SQLite。
- 目标 DSH：`>=0.2.1-0 <0.3.0`；Windows 优先。
- 不安装对比项目、大模型或付费服务；不读私人 token；不部署、不发布、不破坏性清理。

## 交付门槛

不是只写文档或注册 mock：安装/迁移/设置/preset/MCP 都必须走真实入口并跑通对应验证。最终以 `git diff --check`、`npm test`、`npm run check`、Python 测试和隔离安装/迁移回滚实验为准。

# 现状评估与改进方案

## 1. 现状评估

### 安装

- 远程安装可用：`dsh plugin --profile <profile> add github:canyueY/dsh-mnemosyne#main`。
- 本地自安装已实测可用：`dsh plugin --profile <profile> add "file:<绝对路径>"`，会自动追加 bundle。
- 但仍缺：profile 自动识别、版本固定（tag 而非 main）、幂等重跑、改动前自动备份、失败可诊断、装后自检、一键回滚。

结论：**有基本安装路径，但还不算稳定、幂等、可回滚的安装办法。**

### 迁移

- 数据目录迁移入口存在：`mnemosyne memory-bank migrate-legacy`。
- 插件卸载不会删除数据目录（`%LOCALAPPDATA%\Mnemosyne Memory Bank`）。
- 但仍缺：profile 层旧 bundle（`dsh-cognitive-memory`）到 `dsh-mnemosyne` 的迁移、cordis.patch.yml / pnpm-lock 的备份回滚、迁移中断恢复、旧/新版本共存校验。

结论：**数据层有迁移，但 profile 层与一键迁移/回滚不完整。**

### 未完成接口

1. 没有 DSH 设置页 namespace，配置只能靠 `ctx.get('config')` 手填。
2. 核心记忆能力只有 `defineTool` 工具入口，没有 Agent preset 与 MCP 入口。

结论：**三个能力维度（安装、迁移、接口面）都有缺口。**

## 2. 改进方案

### D01 稳定自安装

新增 `scripts/install-dsh.mjs` 与 `scripts/install-dsh.ps1`：

- profile 识别：`DSH_PROFILE` → 运行中的 DSH 进程 → 最近使用 → 兜底 `web`。
- 版本源：`--tag <ver>` 走 `github:...#<tag>`；`--local` 走 `file:<仓库绝对路径>`。
- 幂等：已装同版本跳过；改动前备份 `package.json` / `cordis.patch.yml` / `pnpm-lock.yaml`。
- 装后自检：bundle 在 `dsh.profile.bundles`、`npm run check`、`node --test src/test/host-compat-021.mjs`。
- 失败输出：缺 profile、缺 dsh CLI、pnpm 失败、版本不匹配都给出 `code/hint`。
- 回滚：打印或执行 `dsh plugin --profile <profile> remove dsh-mnemosyne` 与备份恢复路径。

### D02 稳定迁移与回滚

新增 `scripts/migrate-dsh.mjs`：

- 迁移 `dsh-cognitive-memory` → `dsh-mnemosyne`（bundle 与 dependencies 同时替换）。
- 保留其他 bundle/MCP/preset；cordis.patch.yml 与 pnpm-lock 改动前备份。
- 数据目录：调用 `mnemosyne memory-bank migrate-legacy`（只迁移旧 `DSH\cognitive-memory`）。
- 迁移前后版本/数据目录快照；中断可重入；`--rollback` 恢复备份并校验。
- 验收用临时 `DSH_HOME` + 临时 Bank，不碰真实个人库。

### D03 设置页 namespace

- 用 DSH 0.2.1 的 settings namespace 机制暴露 `dsh-mnemosyne` 配置。
- 字段：`auto_start`、`service_root`、`data_dir`、`python`、`base_url`、`token`、`auto_capture`。
- `token` 与任何密钥只写不读回、不进日志；非法值拒绝；默认值安全（`auto_start=false` 时要求 base_url+token）。
- 读取路径与 `ctx.get('config')` 兼容，避免两套真相。

### D04 Agent preset

- 新增 `dsh/agent-presets/mnemosyne`：系统提示说明何时用 `memory_*`，工具白名单只含 memory 工具与无害模型工具，拒绝本地执行。
- 通过 `cordis.patch.yml` 的 bundle patch 注册，`agentPresets/list` 可见 `mnemosyne`。
- 不把 memory 工具变成必须调用；只是提供可选用法的薄层。

### D05 MCP 接口

- 复用/新增 MCP stdio gateway，把 `memory_search/get/context/observe/propose/feedback/update/forget/task_status/health` 以 `mcp__mnemosyne__*` 或等价命名暴露。
- 校验 `initialize` / `tools/list` / `tools/call`；错误结构化；不把内部 SQLite 细节泄漏给 MCP 客户端。

### D06 全链路验收

- 统一复跑：`npm test`、`npm run check`、Python 测试、`git diff --check`。
- 隔离安装/迁移/回滚、设置、preset、MCP 端到端。
- 更新根目录 `DSH_INTEGRATION.md`，把「未接入」项改为实际状态。

## 3. 不变量

- 一个 Node 一个 Memory Bank；DSH 插件只读 REST，不直连 SQLite。
- 卸载/迁移不删数据；profile 改动可回滚。
- 版本固定在 2.0.x；不擅自改版本号。
- 所有写操作先备份；失败可见、可重试、可回滚。

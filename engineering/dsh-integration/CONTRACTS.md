# DSH 安装/迁移/接口适配 契约 v1

这是拟实现规格，不代表新增能力已存在。D00 先核对当前实现，再逐项落实。

## 1. 安装契约

- 目标 DSH：`engines.dsh: >=0.2.1-0 <0.3.0`；Windows 优先，Linux/macOS 不承诺。
- 远程源：`github:canyueY/dsh-mnemosyne#<tag|branch>`；本地源：`file:<仓库绝对路径>`。
- profile 识别顺序：显式参数 → `DSH_PROFILE` → 运行中 DSH 进程命令行 `profiles/<name>` → 最近使用 → `web`。
- 安装前备份 `package.json`、`cordis.patch.yml`、`pnpm-lock.yaml`（若存在）；安装是幂等操作。
- 装后校验：`dsh.profile.bundles` 含 `dsh-mnemosyne`；`npm run check` 通过；`node --test src/test/host-compat-021.mjs` 通过。
- 失败必须返回 `{ok:false, code, error, hint}`；不静默覆盖其他 bundle/MCP/preset。

## 2. 迁移契约

- 旧 bundle `dsh-cognitive-memory` 与依赖名迁移到 `dsh-mnemosyne`，保留 tag/历史。
- `cordis.patch.yml` 中只增删本包拥有的 insert 行，其他条目逐字节保留。
- 数据目录迁移调用 Node CLI，不直接复制 `memory.db`；`MNEMOSYNE_MEMORY_BANK` / `MNEMOSYNE_DATA_DIR` 优先级不变。
- 迁移前后记录数据目录与版本快照；中断可重入；`--rollback` 恢复备份并校验前后一致。
- 未授权不得删除任何数据；删除一律先隔离到 `_backups/`。

## 3. Settings 契约

- namespace id = `dsh-mnemosyne`；字段：`auto_start`(bool)、`service_root`(string)、`data_dir`(string)、`python`(string)、`base_url`(string)、`token`(string, secret)、`auto_capture`(bool)。
- `settings.describe` 返回 schema 与当前值；`settings.update` 校验并持久化。
- secret 字段只写不读回，脱敏输出 `********`，不进日志。
- 默认值安全：`auto_start=false` 时必须有 `base_url`+`token`；`auto_start=true` 时可自动拉起本地 service。

## 4. Agent preset 契约

- preset id = `mnemosyne`；只注入 memory 使用说明与工具白名单，不注入人格。
- 允许 `memory_*` 与无害模型工具（如 `ask_user_question`、`todo_write`）；拒绝本地执行/文件/进程/插件管理。
- `agentPresets/list` 可见 `mnemosyne`；`session/create` 可用该 preset。

## 5. MCP 契约

- transport = stdio；`initialize` / `ping` / `tools/list` / `tools/call` 按 MCP 2025-06-18。
- 工具名 `mcp__mnemosyne__<name>`（或按宿主命名约定记录等价映射）。
- `tools/call` 错误结构化：非法参数 `-32602`、未知工具 `-32601`、未初始化 `-32600`。
- MCP 网关不直连 SQLite，复用 Node REST；不泄漏内部表名/路径/密钥。

## 6. 通用

- 核心认知逻辑留在 `service/`；DSH 侧只做适配，不复制业务逻辑。
- 版本固定在 2.0.x；不擅自 bump、tag、发布。
- 测试不得伪造 passed；skip/xfail 不计通过；真实入口集成必须跑通。

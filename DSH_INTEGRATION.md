# DSH 安装与接口适配总览

> 适用：Mnemosyne 2.0。代码状态以仓库当前测试为准；工程证据见 `engineering/dsh-integration/reports/`。

## 1. 项目与 DSH 的关系

| 仓库 | 在 DSH 里的形态 | DSH 接口面 |
| --- | --- | --- |
| `canyueY/dsh-mnemosyne` | 根 `src/` + `package.json` bundle（`inject: ["tools"]`）；`service/` 为独立 Python Node | `defineTool`、settings Config、agent preset、MCP REST 网关、`session/event`、`exec.signal`、`cordis.patch.yml` |
| `canyueY/mnemosyne-memory-bank` | 数据目录契约说明仓 | 无插件；REST 访问同一 Bank |
| `canyueY/mnemosyne-music-acquisition` | Host 桥 + 业务工具/MCP | 独立 music 宿主 |

## 2. 安装方法

前置：Node ≥ 20、DSH `>=0.2.1-0 <0.3.0`、Python 3.10+。

### 2.1 稳定自安装（推荐开发）

```powershell
# 可选：临时隔离
$env:DSH_HOME = "<临时目录>"
$env:DSH_CLI = "<path-to-@deepseek-ai/dsh/lib/bin.js>"   # PATH 无 dsh 时

npm run check
node scripts/install-dsh.mjs --local --profile <profile>
# 本机 CLI 低于 engines 时显式风险接受：
node scripts/install-dsh.mjs --local --profile <profile> --accept-version-risk
```

也可用包装：`scripts/install-dsh.ps1`。幂等：二次运行返回 `skipped_idempotent`。失败返回 `{ok:false, code, error, hint}`（缺 CLI / 缺 profile / 版本不匹配等）。

### 2.2 远程 / 直接 dsh CLI

```powershell
dsh plugin --profile <profile> add github:canyueY/dsh-mnemosyne#<tag>
dsh plugin --profile <profile> add "file:<仓库绝对路径>"
```

### 2.3 迁移与回滚

```powershell
node scripts/migrate-dsh.mjs --profile <profile> --skip-data   # 旧 dsh-cognitive-memory → dsh-mnemosyne
node scripts/migrate-dsh.mjs --profile <profile> --rollback
# 数据目录（可选，不删源）：
node scripts/migrate-dsh.mjs --profile <profile> --data-dir "<临时 Bank>"
```

卸载插件不删 `%LOCALAPPDATA%\Mnemosyne Memory Bank`。

## 3. 接口适配矩阵

| 面 | 状态 | 落点 |
| --- | --- | --- |
| `engines.dsh` / bundle patch / `output.schema` | 已适配 | `package.json`、`cordis.patch.yml`、`tool-spec.js` |
| `defineTool` + `exec.signal` | 已适配 | `src/index.js`、`memory-client.js` |
| Settings namespace `dsh-mnemosyne` | 已适配 | 不导出 `Config`（rc.5 对 `null` 会在 settings.describe 里执行 `'toJSON' in null`）。无自动表单；配置写在插件行，`src/settings.js` 读取行配置，token 脱敏为 `********` |
| Agent preset `mnemosyne` | 已适配 | `dsh/agent-presets/mnemosyne/`、`src/agent-preset.js`（fail-closed） |
| MCP `mcp__mnemosyne__*` | 已适配 | `src/mcp-gateway.js`（REST，不打开 SQLite）；service MCP 为进程内入口 |
| 自动捕获 | 已适配 | `session/event` 仅 user/最终 assistant |

### Settings 字段

`auto_start`、`service_root`、`data_dir`、`python`、`base_url`、`token`(secret)、`auto_capture`。
`auto_start=false` 必须有 `base_url`+`token`，否则 `apply` 拒绝（`config_incomplete`）。

配置优先级：

```text
apply(ctx, config) 插件行 > MNEMOSYNE_SETTINGS_FILE 旧文件（仅非默认值）
  > ctx.get('config')['dsh-mnemosyne' | 'dsh-cognitive-memory'] > CONFIG_DEFAULTS
```

- `apply` 不访问 `ctx.config`，也不在激活期调用 `ctx.settings.describe()`；
- 行配置先经过 `validateConfigPatch()`，未知字段/类型错误立即失败；
- 旧 `settings.yaml` 的持久化值仍兼容，但不会用默认值覆盖行/根配置；
- 没有自动设置表单；旧 Config 表单值需迁移到插件行，步骤见
  [`docs/DSH冷启动恢复说明.md`](docs/DSH冷启动恢复说明.md)。

### MCP

- `initialize` / `ping` / `tools/list` / `tools/call`
- 非法参数 `-32602`；未知工具 `-32601`；未初始化 `-32600`
- 宿主网关只走 `MemoryClient` REST

## 4. Memory Bank 边界

插件禁止直连 `memory.db`。权威数据目录：

```text
%LOCALAPPDATA%\Mnemosyne Memory Bank
```

## 5. 常见故障

| 症状 | 原因 | 处理 |
| --- | --- | --- |
| `dsh_cli_missing` | PATH/`DSH_CLI` 无 CLI | 设置 `DSH_CLI` 到 `@deepseek-ai/dsh/lib/bin.js` |
| `version_mismatch` | CLI &lt; engines.dsh | 升级 DSH，或 `--accept-version-risk` |
| `profile_missing` | 装错/不存在的 profile | `--profile` + 临时 `DSH_HOME` |
| 设置里看不到 | 装错 profile / 未刷新 | `dsh plugin --profile <p> list`；F5 |
| schema 报错 | `type:json` / 裸 `{}` | `{type:'object', additionalProperties:true}` |

## 6. 验证

```powershell
npm test
npm run check
cd service; .\.venv\Scripts\python.exe -m pytest tests -q
git diff --check
# 隔离安装/迁移：engineering/dsh-integration/tools/run_checks.ps1 -IncludeMigration
node --test src/test/install-dsh.test.mjs src/test/migrate-dsh.test.mjs src/test/settings.test.mjs src/test/preset.test.mjs src/test/mcp.test.mjs
```

工程包进度：`engineering/dsh-integration/PROGRESS.json`；病例 DA01–DA15 见 `ACCEPTANCE_CASES.json` 与各 `reports/D##.md`。

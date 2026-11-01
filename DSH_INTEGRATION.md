# DSH 安装与接口适配总览

> 适用：Mnemosyne 2.0 及附属项目。本文是「DSH 如何自行安装、以及各 DSH 接口如何适配」的权威说明；代码层面的最新状态以各仓库实际测试为准，不以本文声明的历史数字为准。

## 1. 项目与 DSH 的关系

| 仓库 | 在 DSH 里的形态 | DSH 接口面 |
| --- | --- | --- |
| `canyueY/dsh-mnemosyne` | 根目录 `src/` + `package.json` 是 DSH bundle（`inject: ["tools"]`），`service/` 是独立 Python Node | `defineTool`、`ctx.tools.register`、`session/event` 自动捕获、`exec.signal`、`cordis.patch.yml` bundle patch |
| `canyueY/mnemosyne-memory-bank` | 不是插件；只是 Memory Bank 数据目录的公开说明仓 | 无 DSH 插件；由 Node 用 REST 访问同一数据目录 |
| `canyueY/mnemosyne-music-acquisition` | `hosts/dsh-mnemosyne-music` 是 Host 桥 + 业务工具/MCP | `apply(ctx)` 命令钩子、`createToolClient`、business REST、MCP stdio gateway |

## 2. Mnemosyne 插件安装方法

前置：Node ≥ 20、DSH `>=0.2.1-0 <0.3.0`、Python 3.10+（`service/` 依赖）。

### 2.1 远程安装（推荐，GitHub 发布通道）

```powershell
dsh plugin --profile <profile> add github:canyueY/dsh-mnemosyne#main
```

`<profile>` 默认可不填；桌面版实际使用 `desktop`。装错 profile 会导致重启后看不到插件。

### 2.2 本地仓库自安装（开发/离线）

在克隆的 `dsh-mnemosyne` 目录下：

```powershell
npm install
npm run check
dsh plugin --profile <profile> add "file:<dsh-mnemosyne 绝对路径>"
```

验证（等价于 `dsh plugin add file:...`）：

```text
dependencies: + dsh-mnemosyne 2.0.0
profile dsh.profile.bundles 追加 dsh-mnemosyne
```

### 2.3 装完

1. 重启 DSH 或刷新 Web GUI（F5）——客户端模块图是加载快照，不刷新看不到。
2. 若只用工具、不需要设置页：`inject: ["tools"]` 即已足够；配置通过 `ctx.get('config')['dsh-mnemosyne']` 或旧名 `dsh-cognitive-memory` 传入。
3. 自检：

```powershell
npm test
npm run check
```

### 2.4 卸载 / 回滚

```powershell
dsh plugin --profile <profile> remove dsh-mnemosyne
```

数据目录不会随插件卸载删除：

```text
%LOCALAPPDATA%\Mnemosyne Memory Bank
```

## 3. DSH 接口适配清单

### 3.1 包与清单

| 文件 | 关键字段 | 说明 |
| --- | --- | --- |
| `package.json` | `main: ./src/index.js`、`engines.dsh: >=0.2.1-0 <0.3.0`、`dsh.manifestVersion: 1`、`dsh.bundle.patch: ./cordis.patch.yml` | bundle 入口与宿主准入 |
| `dsh.plugin.json` | `entry.name: dsh-mnemosyne`、`entry.inject: ["tools"]` | 只注入工具层，不注入 UI/连接层 |
| `cordis.patch.yml` | `insert: id/name: dsh-mnemosyne` | bundle patch 插入该 loader 行 |

### 3.2 工具注册与输出 schema

- 通过 `@deepseek-ai/dsh-tools` 的 `defineTool` 注册；找不到时回退 `DSH_TOOLS_PATH` 本地覆盖。
- `output.schema` 统一使用：

```json
{ "type": "object", "additionalProperties": true }
```

因为 DSH 0.2.1 拒绝 `type: 'json'` / 裸 `{}`。

### 3.3 生命周期与信号

- `apply(ctx)` 返回 disposer；disposer 逆序注销工具、事件监听，并 `host.stop()` 回收 Python 子进程。
- 每个工具 `execute(args, exec)` 都把 `exec.signal` 传给 `MemoryClient`；客户端用 `AbortController` 桥接外部取消。
- `session/event`：只捕获 `user/message` 与最终 `assistant/message`（`turn/end` flush），显式丢弃 reasoning/thinking 内容。
- `session/flush`：await 所有 pending 捕获，避免进程退出丢消息。

### 3.4 子进程托管

`src/memory-process.js`：

- 从多个候选解析 `service/`（不依赖 env/config）；找不到时打印完整尝试路径。
- 写 `runtime/service.json` 与 `runtime/token`；启动前删除陈旧 `service.json`。
- 等待就绪 + health 轮询；失败 kill 子进程并抛可诊断错误。
- `stdio: ['ignore','pipe','pipe']`，`windowsHide: true`。

### 3.5 尚未接入（明确不冒充已完成）

- 设置页 namespace：当前靠 `ctx.get('config')` 读取配置，没有 DSH 设置 UI。
- Agent preset / MCP：核心记忆工具走 `defineTool`；MCP 形态在 music 侧另有实现。
- 这些不作为「已适配」写进 GA。

## 4. Memory Bank 的 DSH 边界

`mnemosyne-memory-bank` 只是数据目录的公开契约说明：

```text
%LOCALAPPDATA%\Mnemosyne Memory Bank
```

DSH 插件和任何宿主都**禁止** `sqlite3.connect` 直连 `memory.db`；统一走 Node 的 REST / SDK。这样 DSH 插件即使被卸载，数据也不丢、不被插件私有化。

## 5. Music Acquisition 的 DSH 适配

`hosts/dsh-mnemosyne-music`：

- 入口 `index.js`：`runPython` / `doctor` / `bootstrap` / `createToolClient` / `apply(ctx)`。
- `createToolClient` 绑定 `owner_user_id`，拒绝额外参数（`music_sync` 强制 owner 注入）。
- business-tools 10 项，`music_sync_status` 已补齐。
- 独立运行包：`release/windows/dsh-mnemosyne-music/` + `runtime/mnemosyne-music/mnemosyne-music.exe`，脱离 Python 环境可 `--data-dir` 启动。

安装/构建：

```powershell
py -3 scripts/build_standalone.py --output release\windows
```

详细见该仓库 `docs/FINAL_ACCEPTANCE_2026_11_01.md` 与 `hosts/dsh-mnemosyne-music/README.md`。

## 6. 常见安装故障

| 症状 | 原因 | 处理 |
| --- | --- | --- |
| 重启后设置里没有 Mnemosyne | 装错 profile / 未刷新 | `dsh plugin --profile <profile> list` 确认；F5 或重启 |
| 启动报找不到 Python service | `service/` 不在解析路径 | 保持仓库 `service/` 相邻，或设置 `DSH_MNEMOSYNE_ROOT` / `config.service_root` |
| 工具 schema 报错 | `output.schema` 用 `type:json`/`{}` | 用 `{type:'object',additionalProperties:true}` |
| 工具调用被提前掐断 | 未透传 `exec.signal` | 检查 `execute(args, exec)` 是否把 signal 传到 HTTP |
| 卸载后数据丢失 | 数据目录被插件私有化 | 保持 `%LOCALAPPDATA%\Mnemosyne Memory Bank`；插件只读 REST |

## 7. 验证入口

```powershell
# 根适配层
npm test                 # 含 host-compat-021 与 auto-capture
npm run check            # node --check 全部 src

# Python Node
cd service
py -3 -m pytest tests -q
```

版本升级后先跑上述测试，再声明与目标 DSH 版本兼容。

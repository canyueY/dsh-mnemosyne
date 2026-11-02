# Mnemosyne Standalone DSH Plugin 3.1.0

Mnemosyne 本体的独立 DSH 插件封装：**不依赖别的工程树**，自带 Python Memory Node 与
DSH Host 适配层，安装后由 Agent 直接调用 `memory_*` 工具。运行时只依赖自身 `service/`
与独立 venv。

- 仓库位置：[`canyueY/dsh-mnemosyne/standalone`](https://github.com/canyueY/dsh-mnemosyne/tree/main/standalone)
- 发布页：[`v3.1.0`](https://github.com/canyueY/dsh-mnemosyne/releases/tag/v3.1.0)
- 直接下载：`dsh-mnemosyne-standalone-3.1.0.tgz`
- 宿主验证：DeepSeek Harness Desktop `0.2.1-rc.5`（profile `desktop`）
- 许可：Apache-2.0（见 `LICENSE` / `NOTICE`）

---

## 1. 这是什么

一份**单包插件**，包含三层：

| 层 | 位置 | 作用 |
| --- | --- | --- |
| DSH 适配层 | `src/` | 注册 11 个 `memory_*` 工具、自动采集会话消息、启动/守护 Node |
| Mnemosyne Node（本体） | `service/src/cognitive_memory/` | 证据、记忆、检索、上下文、Claim、治理、答案选择等 REST 服务 |
| 可选组件 | `dsh/`、`optional/`、`scripts/` | 记忆专用 Agent Preset（默认不启用）、MCP stdio 网关、运行时安装与冒烟脚本 |

运行时是**独立进程 + 独立 Memory Bank**，插件只经 REST 访问，不直连 SQLite。

```text
DSH Agent
   │  memory_* tools
   ▼
dsh-mnemosyne (Host adapter, Node.js)
   │  spawn + REST (127.0.0.1, random port, bearer token)
   ▼
bundled Python Node  ──►  %LOCALAPPDATA%\Mnemosyne DSH Memory Bank\data\memory.db
```

---

## 2. 目录

```text
Mnemosyne-DSH/
├─ package.json              # DSH bundle 清单（dsh.bundle.patch）
├─ cordis.patch.yml          # 激活行 + 默认配置
├─ locale/{en,zh}.json       # 插件管理/设置页显示名
├─ icon.svg
├─ src/
│  ├─ index.js               # apply：工具注册 + session 自动采集
│  ├─ memory-process.js      # Python 子进程启动/健康检查
│  ├─ memory-client.js       # REST 客户端
│  ├─ tool-spec.js           # 11 个工具 schema
│  ├─ settings.js            # 配置解析/脱敏
│  ├─ config-schema.js       # Config（DSH 设置表单）
│  ├─ mcp-gateway.js         # 可选 MCP stdio 网关
│  ├─ agent-preset.js        # 可选 Preset 定义与工具闸门
│  └─ preset-guard.js
├─ service/                  # Mnemosyne Node（本体内嵌，无 .venv/测试/报告）
│  ├─ src/cognitive_memory/
│  ├─ pyproject.toml
│  └─ requirements.txt
├─ dsh/agent-presets/mnemosyne/agent.cordis.yml   # 可选 Preset 内容
├─ optional/mnemosyne-preset.patch.yml            # 可选 Preset 覆盖层（不随包激活）
└─ scripts/
   ├─ setup-runtime.ps1     # 创建独立 venv 并装依赖
   └─ smoke.mjs             # 启动 Node + observe/search 冒烟
```

---

## 3. 安装与启用

### 3.0 直接下载安装（推荐）

从 [v3.1.0 Release](https://github.com/canyueY/dsh-mnemosyne/releases/tag/v3.1.0) 下载
`dsh-mnemosyne-standalone-3.1.0.tgz`，然后：

```powershell
# 1) 交给 DSH 安装（任选其一）
dsh plugin --profile <profile> add "file:<绝对路径>\dsh-mnemosyne-standalone-3.1.0.tgz"

# 或者在 DSH 会话里让 Agent 用 plugin_manager install_bundle 指向解压后的目录
```

```bash
# 2) 准备独立 Python 运行时（首次一次）
# Windows
powershell -ExecutionPolicy Bypass -File .\scripts\setup-runtime.ps1
# Linux / macOS
bash ./scripts/setup-runtime.sh
```

```powershell
# 3) 验证
node .\scripts\smoke.mjs          # 应打印 SMOKE_OK
```

安装后首次调用记忆工具会自动拉起 Node；无需手动开服务。

也可以直接从源码目录安装：

```bash
git clone https://github.com/canyueY/dsh-mnemosyne.git
cd dsh-mnemosyne/standalone
dsh plugin --profile <profile> add "file:$PWD"
bash ./scripts/setup-runtime.sh
```

### 3.1 运行时（一次性）

插件不使用外部工程的 `.venv`，而是独立运行时：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-runtime.ps1
# 输出：C:\Users\<you>\AppData\Local\Mnemosyne DSH Node\venv\Scripts\python.exe
```

```bash
bash ./scripts/setup-runtime.sh
# 输出：~/.local/share/Mnemosyne DSH Node/venv/bin/python
```

若要重建：`-Recreate`（Windows）；若 PATH 里的 `python` 不可用：`-Python "C:\...\python.exe"`。

### 3.2 安装插件（本地目录）

在 DSH 会话里让 Agent 执行 `plugin_manager`（`action: install_bundle`，`target` 为本目录绝对路径），
或在有 `dsh` CLI 的环境：

```powershell
dsh plugin --profile desktop add "file:<本目录绝对路径>"
```

安装会把本目录 link 进 profile，并应用 `cordis.patch.yml` 的 `dsh-mnemosyne` 行。
首次调用记忆工具时自动拉起 Node；无需手动开服务。

### 3.3 验证

```powershell
node .\scripts\smoke.mjs          # 不污染正式 Bank；成功打印 SMOKE_OK
```

也可在 DSH 设置页确认插件显示名，并在 Agent 工具列表看到 `memory_health` 等工具。

---

## 4. 数据与隔离

| 项 | 默认值 | 说明 |
| --- | --- | --- |
| Memory Bank | `%LOCALAPPDATA%\Mnemosyne DSH Memory Bank` | 独立于桌宠的 `Mnemosyne Memory Bank` |
| Python | `%LOCALAPPDATA%\Mnemosyne DSH Node\venv\Scripts\python.exe` | 若不存在则回退 PATH 的 `python` |
| Node 端口 | 随机（`127.0.0.1:0`） | 端口写入 `<bank>\runtime\service.json` |
| Token | 每次启动随机生成 | 仅进程内使用，不落日志；同时写入 `<bank>\runtime\token`（0600） |
| 启动日志 | `<bank>\runtime\start.log` | 记录 spawn 参数、stderr 尾部、绑定/退出，便于诊断 |

**复用与并发**：启动是幂等的，工具调用和自动采集共享同一个子进程；如果 DSH 重启后
上一个 Node 仍在运行（`service.json` + `token` 健康），插件会**直接接管**它，而不是在同一
Bank 上再起第二个写入者。接管来的 Node 不会在 DSH 退出时被本插件杀掉。

命中缓存的客户端每 15s 会重新探活一次；Node 崩溃/被杀后，下一次工具调用会自动重启，
不需要重启 DSH。

启动默认等待 60s（首次冷启动 / schema 迁移可能较慢）；失败信息会带 stderr 尾部，
完整轨迹在 `runtime/start.log`。

> 本插件以 **junction/link** 方式装进 profile，直接引用本目录。**不要移动或删除本目录**；
> 需要换位置时，先 `plugin_manager remove_bundle`，移动后再按第 3 节重装。

**为什么默认不共用桌宠的 Bank**：Mnemosyne 的硬边界是「一个 Node 一个 Memory Bank」。
桌宠随时可能拉起自己的 Node，两套调度器同时写同一份 `memory.db` 会破坏长期认知的一致性。

如果确实要让 DSH 与桌宠共享记忆，按顺序做：

1. 完整退出桌宠（确认 `%LOCALAPPDATA%\Mnemosyne Memory Bank\runtime\service.json` 指向的进程已退出）；
2. 在插件配置（设置页或 `cordis.patch.yml` 行）把 `data_dir` 指到 `%LOCALAPPDATA%\Mnemosyne Memory Bank`；
3. 只保留一边常驻。切换前先备份 `data\memory.db`。

---

## 5. 工具

| 工具 | 用途 |
| --- | --- |
| `memory_health` | 检查 Node 健康状态 |
| `memory_search` | 检索工作/情节/语义/程序性记忆 |
| `memory_get` | 单条记忆 + 证据链 |
| `memory_context` | 按 token 预算组装上下文信封 |
| `memory_select_answer` | 当前答案选择（partial/unknown 带边界，不伪装 Top1） |
| `memory_observe` | 写入原始观察（不等于直接写长期记忆） |
| `memory_propose` | 提交候选记忆（默认 candidate） |
| `memory_feedback` | 有用/错误反馈，错误触发失效审查 |
| `memory_update` | 更新记忆（可乐观锁） |
| `memory_forget` | 软删除 / `hard=true` 硬删 |
| `memory_task_status` | 查询后台慢任务 |

建议：需要回忆或长期记录时才调用；不要每轮都调用。工具失败时把失败当失败，
不要把 `search` 的 Top1 当作已证实事实。

---

## 6. 自动采集

`auto_capture: true`（默认）时，Host 监听 `session/event`：

- 每条 `user/message` 写入会话账本 + 观察；
- 每轮只落**最终** `assistant/message`（`turn/end` 时提交），不落推理/工具流量；
- 使用稳定 `message_id` 去重，重放不增加事实支持；
- 退出前等待 pending 写入（`session/flush`）。

关闭：配置 `auto_capture: false`。

---

## 7. 配置

`Config` 字段（设置页或 patch 行 `config`）：

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `auto_start` | `true` | 自动拉起内置 Node |
| `service_root` | 空 | 空 = 自动定位内置 `service/` |
| `data_dir` | 空 | 空 = `%LOCALAPPDATA%\Mnemosyne DSH Memory Bank` |
| `python` | 空 | 空 = 独立 venv，否则 PATH `python` |
| `base_url` / `token` | 空 | `auto_start:false` 时必填，连外部 Node |
| `auto_capture` | `true` | 会话自动采集 |

环境变量覆盖（调试用）：`DSH_MEMORY_DATA_DIR`、`DSH_MEMORY_PYTHON`、`DSH_MNEMOSYNE_ROOT`、
`DSH_MEMORY_TOKEN`、`DSH_MEMORY_DEBUG=1`（Node stderr 透传）。

### 7.1 起不来时

1. 看 `<bank>\runtime\start.log`：最后一行 `failed ... detail=...` 就是 spawn / stderr 原因；
2. `memory_health` 的报错文本会带 stderr 尾部（最多 800 字符）；
3. 手动验证运行时：
   ```powershell
   & "$env:LOCALAPPDATA\Mnemosyne DSH Node\venv\Scripts\python.exe" `
     .\service\src\cognitive_memory\main.py --host 127.0.0.1 --port 0 `
     --data-dir "$env:LOCALAPPDATA\Mnemosyne DSH Memory Bank" --token test
   ```
   看到 `DSH_MEMORY_BOUND ...` 即本体正常；
4. 端口被占 / Bank 被另一个 Node 锁住：先确认没有第二个 Node 在写同一个 Bank；
5. 依赖坏了：`scripts\setup-runtime.ps1 -Recreate` 重建 venv。

---

## 8. 可选：记忆专用 Preset 与 MCP

- Preset：`optional/mnemosyne-preset.patch.yml` 未包含在 bundle 激活列表。需要时把内容并入
  profile 的 `cordis.patch.yml`，或 `dsh --patch` 加载；它会拒绝 shell/文件/插件管理工具，
  **不要**在日常编码会话选它。
- MCP：`src/mcp-gateway.js` 是纯 REST 网关（`node src/mcp-gateway.js`，需
  `MNEMOSYNE_BASE_URL` + `MNEMOSYNE_TOKEN`）。内置自动启动的 token 是每次随机的，
  所以默认不接 MCP；只有连外部固定 Node 时才有意义。

---

## 9. 更新与卸载

- 更新本体：把新 `dsh-mnemosyne/service/src/cognitive_memory` 覆盖到本目录 `service/src/`，
  保持 `src/` 适配层；改完重启 DSH（Host 插件不会热替换新模块代）。
- 卸载：`plugin_manager` 移除 bundle，或从 profile 的 `package.json`/`bundles` 删除后重装依赖；
  卸载不删除 Memory Bank。
- `optional/`、`scripts/` 不参与运行时激活。

---

## 10. 未包含的组件

以下为 Mnemosyne 生态里的独立组件，**没有**打进本插件，避免引入外部授权/账号与额外服务：

- `mnemosyne-music-acquisition`（网易云采集/播放桥，需账号授权与 Consent）；
- `dsh-chat-memory-ingest`（微信/QQ 导出记录导入）；
- 桌宠侧的 `Murasame/mnemosyne_compat.py`、`Murasame/mnemosyne_adoption_bridge.py` 等宿主桥。

它们都通过 REST 访问同一个 Node；需要时单独安装即可。

---

## 11. 验收记录

- 来源工程测试：`dsh-mnemosyne` npm 38 / pytest 444（见来源仓 `engineering/dsh-integration`）。
- 本包自检：`node scripts/smoke.mjs` 启动内嵌 Node，`health` + `observe` + `search` 通过。
- DSH 侧（profile `desktop`，Host `0.2.1-rc.5`）：
  - `plugin_manager install_bundle` → `application: applied`；
  - `memory_health` 返回 `all_green: true`；
  - `memory_observe` → 生成 candidate 记忆（`retrieval_ready: true`）→ `memory_search` 命中；
  - `memory_propose` / `memory_get`（含 evidence/versions）/ `memory_context` 可用；
  - 会话账本 `POST /api/v1/cognition/sessions/messages` 返回 200（自动采集的后端路径）；
  - 移除后重新安装，Bank 内记忆仍在；
  - 手动杀掉 Node 后，超过 15s 的下一次 `memory_health` 自动重启并恢复（`start.log` 有记录）；
  - 启动接管（第二个 Host 实例复用同一 Node）通过独立脚本验证。
- 未验证：设置页表单（`Config` 是否在宿主侧解析出 schema 取决于宿主注入，本包不依赖它）；
  浏览器内的插件卡片/图标渲染未做截图验证。

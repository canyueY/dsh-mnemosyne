# Mnemosyne Standalone 2.0.0

**一个可以直接下载、独立运行的 Mnemosyne DSH 插件包。** 同一套 Mnemosyne 2.0 Node/Core，不再依赖 Monorepo 目录，也不要求你手动拼装 Python 服务。

- 仓库：<https://github.com/canyueY/dsh-mnemosyne>
- 源码目录：<https://github.com/canyueY/dsh-mnemosyne/tree/main/standalone>
- 本版本：`standalone-2.0.0`（2026-11-03）

---

## 下载

| 文件 | 大小 | SHA-256 |
| --- | --- | --- |
| [`dsh-mnemosyne-standalone-2.0.0.tgz`](https://github.com/canyueY/dsh-mnemosyne/releases/download/standalone-2.0.0/dsh-mnemosyne-standalone-2.0.0.tgz) | 334,331 B | `5d7efddf51372d6af67b6c9ab4eb56ce2ed6536d4f5abd1c34bb18f3173355ba` |

包内包含：DSH 适配层（11 个 `memory_*` 工具）、内嵌 Python Node/Core、可选 Preset/MCP、运行时安装脚本、冒烟测试。解压后是一个标准 npm 包（`package/`）。

---

## 快速安装

### 1. 安装插件

```powershell
# Windows（在 DSH 有 dsh CLI 的环境）
dsh plugin --profile <profile> add "file:<绝对路径>\dsh-mnemosyne-standalone-2.0.0.tgz"
```

```bash
# Linux / macOS
dsh plugin --profile <profile> add "file:$PWD/dsh-mnemosyne-standalone-2.0.0.tgz"
```

如果当前 DSH 版本不接受 tarball 依赖：解压 tgz，把 `package/` 目录交给
`plugin_manager` 的 `install_bundle`（`target` 指向该目录）。

### 2. 准备独立 Python 运行时（首次一次）

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File .\scripts\setup-runtime.ps1
```

```bash
# Linux / macOS
bash ./scripts/setup-runtime.sh
```

脚本会创建独立 venv 并安装 `fastapi / uvicorn / pydantic / pydantic-settings / httpx`。

### 3. 验证

```powershell
node .\scripts\smoke.mjs     # 应输出 SMOKE_OK
```

然后在 DSH 里调用 `memory_health`，应返回 `ok: true`；首次调用会自动拉起 Node。

---

## 环境要求

| 项 | 要求 |
| --- | --- |
| DSH | `0.2.1-0` ~ `<0.3.0`（在 Desktop `0.2.1-rc.5` 实测） |
| Node.js | ≥ 20 |
| Python | ≥ 3.10 |
| 磁盘 | 包约 1.4 MB；运行时 venv 约 100–200 MB；Bank 随记忆量增长 |

---

## 2.0 Standalone 更新内容

- **单包发布**：DSH 适配层 + 内嵌 Node/Core + 运行时脚本，下载即用，不再依赖 `桌宠` 或 Monorepo 目录。
- **独立 Memory Bank**：默认 `%LOCALAPPDATA%\Mnemosyne DSH Memory Bank`（Linux/macOS：`~/.local/share/Mnemosyne DSH Memory Bank`），与桌宠 Bank 隔离。
- **独立 Python 运行时**：`setup-runtime.ps1` / `setup-runtime.sh`，不占用外部 `.venv`。
- **启动可靠性**：并发启动锁、60 秒冷启动预算、崩溃后 15 秒探活自动重启、跨 DSH 重启接管既有 Node。
- **可诊断性**：`<bank>/runtime/start.log` 记录 spawn、stderr 尾部、端口绑定与退出。
- **11 个工具**：`memory_health/search/get/context/select_answer/observe/propose/feedback/update/forget/task_status`。
- **可选组件**：记忆专用 Agent Preset（默认不激活）、MCP stdio 网关。

---

## 验证基线（2026-11-03）

在桌宠现有 Bank 的**只读副本**（59,303 条 memories / 41,883 条 observations）上：

| 指标 | 结果 |
| --- | --- |
| 首次打开（18→24 migrations） | 3.68 s |
| 稳态启动 / health 可用 | 0.92 s / 0.98 s |
| search 平均 / p95 | 14.4 ms / 64.7 ms |
| recall@10 / top1 | 83.3% / 40% |
| context 平均 | 445.5 tokens |
| DB 完整性 | `quick_check=ok`，59,303 条不变 |
| 读写 | `observe` / `propose` 均 200，计数 +1 |

完整报告见本包 [`BENCHMARK.md`](BENCHMARK.md)（仓库：`standalone/BENCHMARK.md`）。

---

## 数据与升级

- 默认使用**新的独立 Bank**，不会自动读取旧的 `%LOCALAPPDATA%\Mnemosyne Memory Bank`。
- 要接旧 Bank：先完全退出桌宠，备份 `data/memory.db`（含 WAL），再把插件配置 `data_dir` 指到旧 Bank；不要两边同时运行（一个 Node 一个 Bank）。
- 打开旧 Bank 会执行 18→24 的 schema 迁移，迁移前务必备份。
- 插件运行**不调用任何付费 API**；只有把 `memory_context` 返回片段作为模型输入时才产生 token 费用。

---

## 许可与边界

- License：Apache-2.0（见 `LICENSE` / `NOTICE` / `THIRD_PARTY_NOTICES.md`）。
- 本包是 Mnemosyne 2.0 的独立分发形式，不改变核心算法与记忆格式。
- 不包含：音乐采集组件、微信/QQ 聊天导入、桌宠侧桥接；它们仍作为独立组件存在。

---

**问题反馈**：<https://github.com/canyueY/dsh-mnemosyne/issues>

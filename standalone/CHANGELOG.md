# Changelog

## 3.1.0 — 2026-11-03

**Standalone distribution.** 同一套 Mnemosyne 2.0 Node/Core，改为一个可以直接下载、独立运行的 DSH 插件包。

### 新增

- **单包发布**：`dsh-mnemosyne-standalone-3.1.0.tgz`，含 DSH 适配层 + 内嵌 Python Node + 运行时脚本；不依赖 Monorepo 目录。
- **独立 Memory Bank**：默认 `%LOCALAPPDATA%\Mnemosyne DSH Memory Bank`（Linux/macOS：`~/.local/share/Mnemosyne DSH Memory Bank`），与桌宠 Bank 隔离。
- **独立 Python 运行时**：`scripts/setup-runtime.ps1` / `setup-runtime.sh`，首次安装即可用。
- **启动可靠性**：并发启动锁、60 秒冷启动预算、Node 崩溃后 15 秒探活自动重启、跨 DSH 重启接管既有 Node。
- **可诊断性**：`<bank>/runtime/start.log` 记录 spawn、stderr 尾部、绑定与退出。
- **11 个工具**：`memory_health/search/get/context/select_answer/observe/propose/feedback/update/forget/task_status`。
- **验证基线**：在桌宠 59,303 条记忆 / 41,883 条观察的只读副本上完成回归；`quick_check=ok`，读写正常。

### 变化

- 包结构从 Monorepo 根目录改为独立 `standalone/`；`service/` 随包内嵌。
- 默认数据目录是新 Bank（不自动接管旧 `Mnemosyne Memory Bank`）；需要共用时手动设置 `data_dir`。
- 宿主适配层读取 Loader 行配置（`data_dir` / `python` / `service_root` / `auto_start` / `auto_capture`）。

### 兼容性

- DSH `0.2.1-0 < 0.3.0`，Node ≥ 20，Python ≥ 3.10。
- REST `/api/v1` 与记忆格式沿用 2.0/3.0，不改旧 Bank 数据；首次打开会执行 18→24 的 schema 迁移（请先备份）。
- 可选 Agent Preset 与 MCP 网关保持原样，默认不激活。

## 2.0.0 — 2026-10-31

- Monorepo 统一版：Node/Core 迁入 `service/`，根目录保留 DSH 适配层。
- 旧插件归档到 `legacy/dsh-plugin/`。

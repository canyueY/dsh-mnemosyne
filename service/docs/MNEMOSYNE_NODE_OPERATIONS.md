# Mnemosyne Node Operations

**状态：** Phase 1 Implemented（2026-10-30）；E08 健康探针（2026-11-01）
**已实现 Node CLI：** `mnemosyne` / `python -m cognitive_memory.node_cli`
**底层兼容：** `python -m cognitive_memory.cli backup|restore|health|migrate-amem`

### E08 健康探针

- `GET /api/v1/health`：含 `probes` / `overall` / `all_green`（默认只读）。
- `GET /api/v1/cognition/health/probes`：维度 OK/WARN/ERROR/PENDING/UNKNOWN；`include_write_probe=true` 仅允许 `probe-`/`test-`/`__probe__` 命名空间。
- `GET /api/v1/cognition/health/explain`：结构化解释 + `human_text`。
- 写探针不改用户 Bank；pending 任务无 worker 回执记 PENDING，不把无报错当完成。

---

## 1. Node CLI（Phase 1）

```
mnemosyne [--data-dir DIR] [--config PATH] doctor | status | health
mnemosyne config init | show | validate
mnemosyne start [--host] [--port] [--token] [--background]
mnemosyne stop
mnemosyne backup -o <dir>
mnemosyne restore <backup.db> [--force]
```

已实现组件 CLI（D8）：`mnemosyne component list|inspect|install|enable|disable|health|authorize|remove|profiles`。

多 Agent 授权见 REST `/api/v1/agents*`；Readiness：`scripts/mnemosyne_readiness_suite.py`。

快速验收：

```bash
set DSH_MEMORY_DATA_DIR=<独立目录>
set DSH_MEMORY_TOKEN=<token>
set PYTHONPATH=services/cognitive-memory/src
py -3 -m cognitive_memory.node_cli --data-dir %DSH_MEMORY_DATA_DIR% config init
py -3 -m cognitive_memory.node_cli --data-dir %DSH_MEMORY_DATA_DIR% doctor
py -3 -m cognitive_memory.node_cli --data-dir %DSH_MEMORY_DATA_DIR% backup -o %TEMP%\mnemosyne-bak
py -3 -m cognitive_memory.node_cli --data-dir %DSH_MEMORY_DATA_DIR% start --host 127.0.0.1 --port 8765 --token %DSH_MEMORY_TOKEN%
```
---

## 2. E1.2 备份恢复演练规程（强制）

### 2.1 前置

- 使用 **独立临时 data_dir**（禁止直接拿生产库当唯一靶，除非有额外快照）。
- 记录：OS、Python、commit SHA、data_dir、backup 路径。
- 脚本参考：`scripts/mnemosyne_backup_restore_drill.py`（若已入库）。

### 2.2 步骤

1. 初始化空 data_dir，写入 ≥1 条 Observation + Memory（或导入固定 fixture）。
2. 记录 `health`、记忆条数、抽样 ID 与内容哈希。
3. `backup` → 得到 `.db` + `.json` meta。
4. 新建 **另一** restore_dir，`restore --force`。
5. 在 restore_dir 上 `health`；比对条数与抽样内容。
6. 可选：对 backup 文件截断模拟损坏，确认失败可检测。
7. 对同一 backup **重复 restore** 两次，结果一致。
8. 写出演练报告（JSON/Markdown）：通过/失败、命令、计数差。

### 2.3 通过标准

| 项 | 标准 |
| --- | --- |
| 独立恢复 | restore_dir ≠ source_dir 且可 health |
| 计数一致 | memories/observations 与备份前一致（或文档说明可接受差） |
| 内容一致 | 抽样 ID 内容匹配 |
| 元数据 | backup json 含 source/bytes/时间 |
| 可复现 | 报告可被第三人按同样步骤重做 |

**未通过不得**将 G2 Storage Safety 标绿，也不得扩大 Import 来源。

---

## 3. 配置示意（非最终字段）

```yaml
node:
  id: local-node
  name: My Mnemosyne
  mode: standalone
runtime:
  host: 127.0.0.1
  port: 8765
storage:
  provider: sqlite
  path: ./data/mnemosyne.db
security:
  authentication: required
  remote_access: disabled
components:
  profiles:
    - core-default
```

密钥：引用密钥材料，禁止明文进普通配置仓。

---

## 4. 管理面最低能力

查看 Node 状态 · 配置 · 组件 · 数据目录 · 备份/恢复 · 迁移任务 · 接入方与权限 · 审计

初期允许无完整 GUI；Admin Panel 仅为宿主实现之一。

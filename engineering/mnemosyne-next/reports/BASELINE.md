# E00 基线记录

采集时间：2026-11-01T14:02:28+08:00（成功跑次）  
仓库根：`dsh-mnemosyne`  
分支：`feat/taskbook-p6-p7-p8-p9-complete`  
HEAD：`772e56d8d44eaaeea4d9550bfb22aae7861ea990`（`feat: complete theory loops and standardize 2.0 version`）

## 工作区（未清理、未提交）

执行前已有未提交改动，全部保留：

| 状态 | 路径 |
|---|---|
| M | `README.md` |
| M | `service/README.md` |
| M | `src/memory-process.js` |
| ?? | `COMPOSER_HANDOFF.md` |
| ?? | `DSH_INTEGRATION.md` |
| ?? | `engineering/` |
| ?? | `release-dist/` |
| ?? | `service/docs/2.0/audits/` |

未读取私人 token / 生产 Bank。未改产品版本号（Node/Python 仍为 `2.0.0`）。

## 工具链

| 项 | 值 |
|---|---|
| Python（基线采用） | `service/.venv/Scripts/python.exe` → **3.10.11** |
| pytest | **9.2.0**（经 `pip install -e ".[dev]"` 装入 service/.venv） |
| 系统 Python310 | `C:\Users\残月Y\AppData\Local\Programs\Python\Python310\python.exe`（用于创建 venv） |
| 父工作区 `.venv` | `桌宠/.venv` Python 3.10.11，**无 pytest**（首次 Baseline 失败原因） |
| Node | v24.21.0（`C:\Program Files\nodejs\node.exe`） |
| npm | 11.19.0 |
| 包版本 | `package.json` / `pyproject.toml` = `2.0.0` |
| `engines.dsh` | `>=0.2.1-0 <0.3.0` |
| `dsh.plugin.json` | `entry.inject=["tools"]` |
| `cordis.patch.yml` | 存在（bundle insert `dsh-mnemosyne`） |
| 最新 schema 迁移 ID | `017_theory_loops`（下一实施可用 `018_*`，不预锁） |

### 环境修复（仅隔离开发依赖）

首次 `run_checks.ps1 -Mode Baseline` 选用父 `.venv`，报 `No module named pytest`（exit 非 0，日志 `checks-Baseline-E00-20261101-140101-947-6cd6d7b6.log`）。  
按 VERIFICATION「可按项目声明配置隔离开发依赖」在 `service/` 创建 `.venv` 并 `pip install -e ".[dev]"`。`.venv/` 已在 `.gitignore`。**未**安装对比项目 / 大模型 / 付费服务。

## 基线命令与结果

入口：`./engineering/mnemosyne-next/tools/run_checks.ps1 -Mode Baseline`  
成功日志：`reports/checks-Baseline-E00-20261101-140228-532-8baa1b6a.log`  
JUnit：`reports/checks-Baseline-E00-20261101-140228-532-8baa1b6a.xml`

| 检查 | cwd | 退出码 | 摘要 |
|---|---|---:|---|
| pack structure (`validate_handoff.py`) | repo | 0 | ok；implementation_status=not_started |
| python version | repo | 0 | Python 3.10.11 |
| python full suite | service | 0 | **352 passed**, 1 warning（Starlette TestClient deprecation） |
| node syntax (`npm run check`) | repo | 0 | 4 文件 `--check` 通过 |
| node suite (`npm test`) | repo | 0 | **7 passed**（含 `host-compat-021.mjs`） |
| existing theory (`scripts/verify_theory_loops.py`) | service | 0 | `ok: true`，临时 Bank；capability/context/cache/rollback passed |

整体脚本：**通过**（exit 0）。

### 未运行 / 非本轮范围

| 项 | 状态 |
|---|---|
| `service/sdk-ts` `npm run build` | 未跑（Final/E03/E12 要求） |
| 干净环境离线安装闭环 | 未跑（E08/E15） |
| 50 AC 对账 | 未跑（E15）；E01 必需测试文件尚不存在 |
| 真实用户效果 | `not_measured` |
| DSH 宿主实机 `plugin add` / F5 | 未跑；静态契约：`engines.dsh` + inject + cordis + host-compat 测试已绿 |

### 原有失败

当前成功基线：**无 pytest/node/theory 失败**。  
历史失败仅环境缺失（父 venv 无 pytest），已用 service 隔离 venv 消除，**未**改断言抹平。

### 已知警告

- `StarletteDeprecationWarning`: `httpx` + `starlette.testclient` → 建议将来装 `httpx2`；非失败。

## 对后续任务的含义

- 基线全绿 → E01+ 可在同一 `service/.venv` 上增量实现与回归。  
- 工程包 `checks-Task-E01-*-67baad26.log` 仅为 pack 失败保护自检（缺必需测试即拒），**不是** E01 业务结果。  
- 既有失败影响：无。Blocker：无（E00）。

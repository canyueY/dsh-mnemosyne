# FINAL REPORT｜mnemosyne-next E00–E15

状态：**complete**  
更新：2026-11-01  
版本保持：**2.0.0**（未改号）

## 结论

P0/P1 确定性主线 E00–E15 已接入实际 Service / API / SDK / 薄宿主调用链，并通过 Final 命令集。公开 50 例（AC01–AC50）全部 mechanism-only 通过；真实用户效果记 **not_measured**；后置能力见 DEFERRED.md（neural / physiology / general_causal / real_platform_ops / federation）记 **disabled**。

## Final 命令证据

| 检查 | 结果 | 退出码 |
|---|---|---|
| pack structure (`validate_handoff.py`) | ok | 0 |
| Python full suite | **413 passed** | 0 |
| Node syntax (`npm run check`) | ok | 0 |
| Node suite | **11 passed** | 0 |
| theory loops | ok（临时 Bank） | 0 |
| TypeScript SDK build | ok | 0 |
| continuity (`verify_memory_continuity.py`) | ok | 0 |
| clean install（独立：`verify_clean_install.py`） | ok；wheel `mnemosyne-2.0.0-py3-none-any.whl` | 0 |

命令：`./engineering/mnemosyne-next/tools/run_checks.ps1 -Mode Final -TaskId E15`  
解释器：`service/.venv` Python 3.10.11  
日志：`reports/checks-Final-E15-20261101-151627-871-2a9acdb8.log`  
JUnit：`reports/checks-Final-E15-20261101-151627-871-2a9acdb8.xml`

## 连续性验收（E15）

临时 Bank 场景：记录 → 重启 → 召回/当前选择 → 纠正 → 源修订派生失效 → 下一轮回答。  
通道：Service / REST / SDK / 薄宿主适配器（`DshHttpAdapter`）。  
脚本：`service/scripts/verify_memory_continuity.py`  
测试：`service/tests/contract/test_memory_continuity_e2e.py`  
产物：`reports/memory_continuity.json`（`production_path_used=false`）

## 干净环境安装（独立证据）

`service/scripts/verify_clean_install.py`：本地 `pip wheel` → 临时 venv → 安装 wheel → import → 临时 Bank 最小闭环。  
产物：`reports/clean_install.json`（`production_interpreter_modified=false`）。

## AC01–AC50

逐项见 `reports/CASE_RESULTS.json`（status=passed；含 test_locator / command / log）。  
公开病例仅证明工程机制，不是盲测 heldout；学习真实效果无授权数据 → **not_measured**。

## 未计通过项

- skipped / xfail：**无**
- 环境故障：**无**（Final 一轮；此前连续性曾因 Starlette stderr 警告被 PowerShell 误判，已在脚本侧压制后重跑通过）
- 遗漏/后置：**disabled**（见 DEFERRED.md）

## 主要代码落点（摘要）

详见 `IMPLEMENTATION_MAP.md` 与各 `E00.md`–`E15.md`。核心新增/接入包括：SourceRef/Coverage、DerivationManifest、AnswerSelection、context budget、DSH `memory_select_answer`、SessionLedger、UsageLedger、health probes、retrieval fusion、session consolidation、watermarks/fencing、ExecutionReceipt/FakeExecutor、attributed feedback、text_book 领域与 staged import、continuity/clean-install 脚本。

## 边界

- 未自动提交、推送、发布、部署。
- 未使用真实个人 Bank。
- FakeExecutor 仅协议路径，无真实平台操作。
- 宿主兼容：`engines.dsh >=0.2.1-0 <0.3.0`。

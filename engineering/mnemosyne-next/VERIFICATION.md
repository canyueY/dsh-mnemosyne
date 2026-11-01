# 验证与证据记录

以下入口从仓库根目录执行。脚本切到 service，避免 pytest 配置路径误用。使用临时 Bank，不以真实配置启动默认个人数据目录做 smoke。

```powershell
python engineering/mnemosyne-next/tools/validate_handoff.py
./engineering/mnemosyne-next/tools/run_checks.ps1 -Mode Baseline
./engineering/mnemosyne-next/tools/run_checks.ps1 -Mode Task -TaskId E03
./engineering/mnemosyne-next/tools/run_checks.ps1 -Mode Final
```

脚本依次探测 service/.venv、仓库/.venv、父工作区/.venv 的 Python，也支持 `-Python <解释器绝对路径>`。缺 pytest/解释器时失败，不假装通过。不能运行 ps1 时手动执行等价命令并记录，无需修改全局执行策略。

## 实际检查集合

- service：`python -m pytest tests -q`，脚本额外写 junitxml 到 reports。
- service：`python scripts/verify_theory_loops.py`，原脚本已使用临时 Bank。
- 仓库根：`npm run check`、`npm test`。
- service/sdk-ts：`npm run build`，最终必跑；缺构建依赖单列失败，不静默跳过或临时联网下载未声明工具。
- E15 新增 `service/scripts/verify_memory_continuity.py`，自建临时 Bank，不默认连接生产路径；Final 运行。
- E08/E15 独立验证：本地构建包 → 新临时 venv → import/CLI → 完整最小闭环。记录安装源、版本及结果；缺离线依赖不能假称通过，不改生产解释器。

Task 模式检查索引中的已有回归和必需新增测试；新增文件缺失即失败。E05/E06 跑 Node check/test，E03/E12 跑 SDK 构建。Baseline/Final 跑全部 Python/Node 和原 theory；Final 还跑 SDK 与新连续性脚本。

若新源码改动了 JS/SDK，即使任务不在预设集合，也需补相应 check/test/build。干净环境安装是独立验收，不由现有工作区 pytest 替代。

## 结果记录

每任务写 reports/E##.md：实际改动/代码位置、接口和迁移、AC 到确切测试函数的映射、命令/工作目录/解释器、退出码/结果、限制及下一步。脚本原始输出写 reports/checks-*.log 和 junitxml；禁止日志包含 token/个人数据。

E15 写 reports/CASE_RESULTS.json，逐项包含 case_id、test_locator、status=passed|failed|blocked|not_run、command、log、notes；另列全链路、SDK 和新环境安装结果。PROGRESS.json 的 case_results 可引用这些记录。

公开病例不是盲测集，固定 synthetic heldout 只证明流程机制。学习真实效果无授权留出数据时写 not_measured。

关键权限越界、未授权改写原文、错误事实晋升、重复外部副作用在固定用例中零失败。skip/xfail、原有失败和环境缺失均单列，不算通过。有失败不得写全量通过，不以历史文档里的固定测试数判断成功。

validate_handoff.py 只检查工程包规格和结构，不验证项目实现或人工结果真实性。

## 复用与构建依赖

若既有测试已等价覆盖，更新 TASK_INDEX.json 的 required_python_tests 及对应 ACCEPTANCE_CASES.json 的定位，并在任务报告记录具体断言与复用理由；不要创建空的新文件骗过检查。脚本仍要求所有声明的必需测试真实存在并执行。

若 SDK 缺声明的 TypeScript 构建工具，可在对应任务按项目约定添加并锁定必要 devDependency、记录版本和锁文件；不依赖临时全局安装或不受控 npx 联网下载。测试只用项目明确声明的隔离依赖。

脚本为子进程重定向三种应用数据目录环境变量到 reports/isolated-runs，结束恢复原值；这是额外防护，不能替代测试显式使用临时 Bank。隔离目录不自动删除，避免误清理；其内容被 reports/.gitignore 排除。

CASE_RESULTS.json 的 log 路径相对本工程包（例如 reports/checks-Final-E15-*.log），不得使用外部/越界路径。`validate_handoff.py --completion` 检查报告覆盖、依赖完成及日志存在，不鉴定人工报告真实性。

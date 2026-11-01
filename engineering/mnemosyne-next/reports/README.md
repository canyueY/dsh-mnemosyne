# 实施证据目录

E00 已完成基线采集（`BASELINE.md`、`IMPLEMENTATION_MAP.md`、`E00.md`）。`PROGRESS.json` 中 E00=done，当前任务 E01。

`run_checks.ps1` 输出日志及 junitxml。不要把私人数据、token 或生产 Bank 放入此目录。任务完成必须有实际代码及测试证据，不能只有本目录的自述。

`PACK_VALIDATION.json` 仅记录任务包结构自检。`checks-Task-E01-*-67baad26.log` 来自刻意验证失败保护（缺必需测试即拒），不是 E01 业务结果。首次 Baseline 失败日志（父 venv 无 pytest）已保留对照。

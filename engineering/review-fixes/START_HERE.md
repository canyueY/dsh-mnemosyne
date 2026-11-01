# Mnemosyne 三工程联合复核修复 工程执行包

用途：交给 Cursor Composer 2.5 Fast 直接执行，按复核报告修复 R01—R10 与补证链路缺口，并重跑三套工程（E00—E15、D00—D06、M00—M07）验收。

## 输入

- 复核报告：[service/reports/three-package-review-2026-11-01/REVIEW.md](../../service/reports/three-package-review-2026-11-01/REVIEW.md)
- 复现脚本与记录：同目录 `reproduce_review.py`、`reproduce_dsh.mjs`、`mcp-stdio-audit.json`、`npm-pack-audit.json`、`checks.json` 等。

## 任务映射

| 问题 | 任务 | 涉及工程 |
| --- | --- | --- |
| R01 Checkpoint 跨 Bank 覆盖 | F01 | E06 / M06 |
| R02 答案选择忽略 arguments | F02 | E03 / M01 |
| R03 rejected 仍被 chosen | F03 | E03 / M02 |
| R04 preset 停留在模拟函数 | F04 | D04 |
| R05 发布包漏脚本与 preset | F05 | D01 / D04 |
| R06 MCP 网关无 stdio 入口 | F06 | D05 |
| R07 迁移重跑跳过数据阶段 | F07 | D02 |
| R08 过滤空结果又恢复过期候选 | F08 | E09 / M03 |
| R09 布尔值把估算标为精确 | F09 | E04 / M05 |
| R10 迁移全局替换改动其他插件 | F10 | D02 |
| 补证链路（settings 持久化 / worker 水位 / checkpoint 来源修订） | F11 | 交叉 |
| 全量复验与报告收口 | F12 | FINAL |

研究/外部模型轨道保持 `disabled/not_measured`，本轮不作为缺陷处理。

## 开始执行

1. 在 Cursor 打开 `dsh-mnemosyne` 仓库根目录。
2. 粘贴 `COMPOSER_PROMPT.md` 正文，或引用本目录 `START_HERE.md`。
3. 按 `TASK_INDEX.json` 的 `execution_order` 执行 F00—F12。
4. 每轮只读当前任务、相关契约与复现证据；用临时 Bank / 临时 DSH_HOME 隔离验证，不碰生产数据。
5. 完成后更新 `PROGRESS.json` 与 `reports/F##.md`。

## 文件用途

- `COMPOSER_PROMPT.md`：可直接粘贴给 Composer 的执行指令。
- `CONTRACTS.md`：修复必须遵守的不变量与回归口径。
- `TASK_INDEX.json`、`tasks/F00.md—F12.md`：依赖、落点、步骤与完成标准。
- `ACCEPTANCE_CASES.json`：公开验收反例（given/when/expected）。
- `VERIFICATION.md`、`tools/run_checks.ps1`：真实命令与结果记录。
- `PROGRESS.json`：初始 pending；执行者维护，不是已完成报告。

## 交付门槛

不是只改字段或辅助函数：每个缺陷都要走真实入口（REST/Node/preset/MCP/迁移/打包）并通过对应反例；不能用模拟函数、本地 file 源或 helper 单测替代真实链路。

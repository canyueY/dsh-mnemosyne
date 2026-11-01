# 验证入口

所有命令在 `dsh-mnemosyne` 仓库根目录执行；用临时 Bank / fake executor，不碰生产数据。

## 基线（M00）

```powershell
cd service
py -3 -m pytest tests -q
```

## 每任务回归（M01—M06）

```powershell
# 依据各任务新增的测试文件执行，例如：
cd service
py -3 -m pytest tests/unit/test_answer_selection.py tests/unit/test_evidence_independence.py tests/unit/test_retrieval_fusion.py tests/unit/test_consolidation_levels.py tests/unit/test_context_budget.py tests/unit/test_usage_ledger.py -q
```

## 根适配层

```powershell
cd ..
npm test
npm run check
```

## 全量（M07）

```powershell
cd service
py -3 -m pytest tests -q
cd ..
npm test
npm run check
git diff --check
```

## 结果记录

每个任务写 `reports/M##.md`，把实际通过/失败/跳过写入 `PROGRESS.json`。不得用历史报告数字代替当前运行；外部模型能力标 `disabled/not_measured`。

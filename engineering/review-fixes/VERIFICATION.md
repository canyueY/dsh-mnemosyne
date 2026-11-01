# 验证入口

所有命令在 `dsh-mnemosyne` 仓库根目录执行；用临时 Bank / 临时 DSH_HOME，不碰生产数据。

## 1. 复现（F00 / F12）

```powershell
# Python 反例（用 service/.venv 或等价解释器）
cd service/reports/three-package-review-2026-11-01
python reproduce_review.py

# DSH 反例
node reproduce_dsh.mjs
```

## 2. 全量

```powershell
npm test
npm run check
cd service
py -3 -m pytest tests -q
cd ..
git diff --check
```

## 3. 发布包与 MCP

```powershell
npm pack --dry-run --json --ignore-scripts
node scripts/install-dsh.mjs --help
node scripts/migrate-dsh.mjs --help
node --test src/test/preset.test.mjs src/test/mcp.test.mjs
```

## 结果记录

每个任务写 `reports/F##.md`，把实际通过/失败/跳过写入 `PROGRESS.json`。不得用历史报告数字代替当前运行；研究/外部模型标 `disabled/not_measured`。

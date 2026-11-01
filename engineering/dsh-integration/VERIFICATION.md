# 验证入口

所有命令在 `dsh-mnemosyne` 仓库根目录执行。安装/迁移实验一律用临时 `DSH_HOME` 与临时 Memory Bank，不碰真实 profile/个人数据。

## 1. 基线（D00）

```powershell
node -v
npm -v
npm test
npm run check
```

## 2. 自安装（D01）

```powershell
node scripts/install-dsh.mjs --local --profile <临时profile>
node scripts/install-dsh.mjs --local --profile <临时profile>   # 幂等重跑
```

临时 `DSH_HOME`：

```powershell
$env:DSH_HOME = "<临时目录>"
```

预期：bundle 追加成功；二次运行跳过；其他 bundle/MCP/preset 不丢。

## 3. 迁移（D02）

```powershell
node scripts/migrate-dsh.mjs --profile <临时profile>
node scripts/migrate-dsh.mjs --profile <临时profile> --rollback
```

预期：旧 `dsh-cognitive-memory` → `dsh-mnemosyne`；回滚恢复；数据目录不删。

## 4. Settings / preset / MCP（D03—D05）

```powershell
node --test src/test/settings.test.mjs
node --test src/test/preset.test.mjs
node --test src/test/mcp.test.mjs
```

## 5. 全量（D06）

```powershell
npm test
npm run check
cd service; py -3 -m pytest tests -q
git diff --check
```

## 结果记录

每个任务写 `reports/D##.md`，并把实际通过数/失败项写入 `PROGRESS.json`。不得用历史报告数字代替当前运行。

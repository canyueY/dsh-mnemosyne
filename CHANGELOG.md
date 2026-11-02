# Changelog

## 3.1.0 — 2026-11-03

- **Standalone 独立分发**：新增 `standalone/`，发布 `dsh-mnemosyne-standalone-3.1.0.tgz`（DSH 适配层 + 内嵌 Node + runtime/setup 脚本）。
- 独立 Memory Bank 与独立 Python venv；并发启动锁、60s 冷启动预算、崩溃自动重启、跨 DSH 重启接管 Node。
- `standalone/` 自带 README / CHANGELOG / RELEASE_NOTES / BENCHMARK；Monorepo 根目录结构不变。
- 在桌宠 59,303 条记忆的只读副本上完成回归：`quick_check=ok`，search 14.4 ms，recall@10 83.3%，context 445.5 tokens。

## Unreleased

- **DSH 0.2.1 宿主接口兼容**
  - `engines.dsh: >=0.2.1-0 <0.3.0`（含 rc；准入以 `engines.dsh` 为准）
  - `dsh.manifestVersion: 1`；peer `@deepseek-ai/dsh-tools` 对齐 0.2.1
  - tool `output.schema` 使用 `{ type: 'object', additionalProperties: true }`（`type:json` / 空 `{}` 会被 0.2.1 `defineTool` 拒绝）
  - `execute(args, exec)` 转发 `exec.signal` 到 MemoryClient
  - 以 `@deepseek-ai/dsh-tools@0.2.1-rc.5` 实机校验全部 memory_* 工具可注册

## 2.0.0

- 合并 `canyueY/mnemosyne` 2.0 Node 到 `canyueY/dsh-mnemosyne`，建立 2.0 单一权威仓
- 根目录保留 DSH 插件适配层；Node / Core / Memory Bank 位于 `service/`
- 旧 DSH 插件完整归档到 `legacy/dsh-plugin/`，并保留 tag `legacy/dsh-plugin-0.2.0`
- 数据目录统一为 `%LOCALAPPDATA%\Mnemosyne Memory Bank`
- Python / npm 版本同步到 `2.0.0`
- 原 2.0 历史见 `service/CHANGELOG.md` 与原 `canyueY/mnemosyne` 仓库

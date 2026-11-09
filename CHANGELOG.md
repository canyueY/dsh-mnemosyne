# Changelog

## Unreleased

- **DSH 0.2.2-rc.1 实机适配验证（2026-11-09）**
  - 以 desktop profile 从最新源码安装 bundle，`memory_*` 工具在 0.2.2 Host 中注册成功
  - `memory_health` 返回 `overall=OK`；`memory_search` 正常返回检索结果
  - 继续保留 `engines.dsh >=0.2.1-0 <0.3.0`，不无故切断 0.2.1 用户
- **DSH 0.2.2-rc.1 宿主接口兼容**（仍锁 `engines.dsh >=0.2.1-0 <0.3.0`）
  - `dsh.plugin.json` 增加 `contributes.tools` / `contributes.skills` 与 `main`，供 0.2.2 插件管理器编目
  - 继续不导出 `Config`（0.2.1-rc.5 至 0.2.2-rc.1 对 `null` Config 会在 `settings.describe` 崩溃）
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

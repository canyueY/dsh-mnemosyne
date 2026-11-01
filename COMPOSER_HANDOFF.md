# Cursor Composer 2.5 Fast 工程入口

工程包：[engineering/mnemosyne-next/START_HERE.md](engineering/mnemosyne-next/START_HERE.md)。

把 [COMPOSER_PROMPT.md](engineering/mnemosyne-next/COMPOSER_PROMPT.md) 的正文粘贴给 Composer，或者引用本文件并要求按工程包实施。

包含 16 个有依赖顺序的任务、统一契约、50 个公开验收反例、验证脚本和可恢复进度。默认完成确定性 P0/P1，研究与联邦后置。先 E00 建基线，再逐步实现并验证；规格文件和历史报告不代表本轮已通过。

原综合方案保存为包内 REFERENCE_PLAN.md，附 11 份文本的来源哈希。实施不依赖本机外部附件路径，不更改 Cursor 的模型、全局规则或权限配置。

## DSH 自安装与接口适配（两条硬验收）

除 16 个 E 任务外，全程同时守住两条宿主验收，不额外造平行系统：

- **DSH 自安装**：以「DSH 自己能装」为目标，而非只写安装文档。保持根目录 `package.json`（`engines.dsh: >=0.2.1-0 <0.3.0`）、`dsh.plugin.json`（`entry.inject=["tools"]`）与 `cordis.patch.yml` / bundle patch 机制有效；从干净 clone 走 `npm install` → `dsh plugin --profile <profile> add github:canyueY/dsh-mnemosyne#main`（或宿主等价 `install_bundle`）→ 重启/F5 可见可用。安装与桥接自动发现 profile 和会漂移的 DSH 端口，验证 DSH 能启动/守护/健康回收 Node；profile 改动前备份、幂等可重跑，失败可诊断、不覆盖其他 bundle/MCP/preset、不读私人 token、不直连核心 SQLite。
- **DSH 接口适配**：DSH 只是薄宿主，核心逻辑留在 Node。逐项适配 0.2.1 约定并用真实链路测试（`src/test/host-compat-021.mjs` 与现有 thin e2e）：工具 `output.schema` 用 `{ type: 'object', additionalProperties: true }`；`apply`/`execute(args, exec)` 转发 `exec.signal`；`engines.dsh` 版本区间；bundle patch / preset / MCP 与 `mcp__*` 命名；settings、`session/create`、`agentPreset`、`selectModel`、`projections`、`$events`；桥接用 `discoverDshBaseUrl()` 或等价发现，禁止硬编码端口。旧 search/retrieve 语义兼容，新增能力走显式入口；等价实现位置记入 IMPLEMENTATION_MAP.md，DSH 升级后先跑 `npm run check` 再宣布兼容。

# 三工程修复复查：13fead5

日期：2026-11-01。范围：综合更新 E00–E15、DSH 集成 D00–D06、检索质量 M00–M07，以及 review-fixes F00–F12。通用采集模板不在本轮范围。

## 结论

原来的最小反例已得到修正，但还不能把三个工程及 F00–F12 全部验收关闭。本轮确认 **5 项可复现缺口：2 项 P1、3 项 P2**。其中 R03、R08、R09、R10 是同一要求未覆盖完整的变体，另外一项是 F11 新接入 worker 后仍缺失的写层保护。

本轮只新增复查脚本、日志和报告，没有修改业务实现、原工程进度文件或生产 Bank/profile。检查的是当前工作区 HEAD `13fead58cc39b0dcfe6735c21c36bf48cc078bd7`，未审查远端部署。

## 仍需修复

### C01 / P1：答案选择仍将无来源、未验证的 Claim 确定采用（R03 / F03 / FA06）

位置：`service/src/cognitive_memory/cognition/answer_selection.py:282`、`:322`、`:415`。

修复已排除 rejected/retracted，也取消了仅凭 preference 语法自动标成 self_report。但 `_match_claim` 未读取对应 Evidence、Verification 或来源就绪状态；剩余候选按生命周期排序后仍直接进入 chosen。

隔离复现：通过真实 ClaimStore 创建 `C喜欢咖啡`，没有 source_memory_id，没有显式 self_report scope；记录为 proposed、verification_status=unverified。再走 REST `/cognition/answers/select`，得到 `status=chosen`、`checks_complete=true`，`self_report=false`，并把 Claim 自身 ID 放入 evidence_refs。

这并不证明内容为假，也不要求把用户自述一律核验成客观事实；问题是来源缺失时没有 unknown/partial 边界，与 F03 步骤 3 和 FA06 的“来源缺失返回 unknown”直接冲突。只声明 `wrote_verified=false` 不能替代选择资格检查。

修复要求：复用实际 Evidence/Verification/来源有效性判定，区分有溯源的自述引用与可确定采用的认识；拒绝无来源/失效/不充分候选，或给有边界的 partial。补来源缺失、撤销、验证变化、真正自述来源的 REST 测试。

证据：`remaining-python.json` / `unverified_sourceless_claim`。

### C02 / P1：旧 worker 在最终提交被拒绝前已经写入真实 Memory（F11 / FA20）

位置：`service/src/cognitive_memory/jobs/dispatcher.py:201`、`:203`、`:259`、`:265`；`service/src/cognitive_memory/cognition/consolidate.py:101`。

新增的 lease 检查发生在 handler 入口；handler 执行真实业务写入之后，才调用 `commit_artifact` 和推进水位。`consolidate_observation` 及其 `propose_memory` 写入没有收到 fencing token/generation。完成记录受保护，不等于 Memory 产物受保护。

隔离复现：创建真实 system observation，让真实 `BackgroundRuntime.run_tasks_once` 执行真实 consolidation。仅在其即将调用真实 `MemoryService.propose_memory` 时注入另一个 worker 获取新代次，之后继续原写入函数。旧 worker 的 episodic candidate 成功入库；随后任务提交报 `stale worker token rejected at write layer`，任务回到 pending，但 Memory 已保留。没有替换 consolidation 为伪业务写入。

修复要求：在实际业务写事务内检查 token/generation，实际效果、水位和任务状态的提交必须有一致的原子性/幂等恢复语义；至少覆盖“入口检查后、实际写入前被接管”与“效果写后崩溃”的真实 dispatcher 用例。现有 FA20 测试只直接调用旧 lease 的 commit_artifact，无法覆盖该窗口。

证据：`remaining-python.json` / `stale_worker_real_write`。复现使用确定性交错注入，并非声称生产已经发生竞态。

### C03 / P2：嵌套 envelope 参数仍能伪造精确 token 计量（R09 / F09）

位置：`service/src/cognitive_memory/core/service.py:1014`、`:1034`。

顶层 `has_accurate_tokenizer=true` 已不再抬升精度。但 `accurate_tokenizer` 仍从客户端 `body.envelope` 读取；实际 estimator 仍固定为 `estimate_tokens_chars`。

隔离 REST 请求：`{"query":"probe","token_budget":256,"envelope":{"accurate_tokenizer":true}}`。响应 HTTP 200，`measurement=measured`、`hard_limit_verified=true`。额外指定 envelope.measurement=measured 同样成立。

修复要求：实际服务器计量器决定精度，不能信任顶层或嵌套布尔声明；计量器接口及其覆盖范围必须与返回标记一致。对整个 ContextBuildIn 的所有输入通路补回归。

证据：`remaining-python.json` / 两项 `envelope_tokenizer_bypass`。

### C04 / P2：items 过滤正确，但 brief/fragments 重新带出过期记录（R08 / F08 / FA15）

位置：`service/src/cognitive_memory/retrieval/pipeline.py:253`、`:280`、`:281`。

删除 `or items` 已修正“全部过滤为空又恢复”的原反例。但随后 `service.build_context` 独立重新检索，没有使用过滤后的安全集合；只要还有任意有效 item，流水线就原样返回独立生成的 brief/fragments。

隔离复现：同时存入 `mixedreview active safe record` 和 `mixedreview expired unsafe record`，后者 metadata.validity_to=2000-01-01。current 检索的 items 只有有效记录，但 fragments 包含两条，brief 仍含过期文本。

修复要求：context 组装复用同一个已授权且时间有效的候选快照，或在组装所有输出字段时执行等价过滤；不要仅过滤 items。补“有效与过期混合”以及关联扩展、权限过滤后的摘要用例。

证据：`remaining-python.json` / `mixed_filtered_context`。

### C05 / P2：迁移正则仍跨 owned 行改配置，并漏掉合法引号语法（R10 / F10）

位置：`scripts/migrate-dsh.mjs:113`–`:124`。

全文 split/join 已删除，原 sourcePath 反例已保留。但新的多行正则匹配全文任意缩进的 id/name，并不识别所属 plugin row；同时只匹配不带引号的旧名字。

隔离复现：另一个 `keep-other` 插件的 `config.id` 与 `config.name` 值恰为 dsh-cognitive-memory；真正旧插件使用合法 YAML 单引号。脚本退出 0，返回 migrated，却把其他插件的嵌套值改成 dsh-mnemosyne，真正的 `id: 'dsh-cognitive-memory'` 和 name 仍保留。

修复要求：定位本插件拥有的顶层 insert row 再改 name/id；覆盖引号、注释、嵌套配置、CRLF，且其他插件块逐字节保留。不能把正则中的字段名视为所有权判断。

证据：`remaining-migration.json`；重跑脚本 `probe_migration.mjs`。

## 原 10 项修复情况

- R01：原跨 Bank 覆盖已拒绝（HTTP 403），projects 记录/manifest 保持原值；代码同事务限制 Bank/session。
- R02：原 arguments=[茶] 错配咖啡反例现在返回 unknown、空 selected。
- R03：原 rejected 反例修正；完整来源资格要求仍见 C01。
- R04：已用真实 `@deepseek-ai/dsh-agent-preset` composition 和 `preset-guard` 替代原模拟交付路径。查阅了 F04-real-dsh.md 对 DSH 0.2.1-rc.3 的 list/create/settings 重启验证记录；本轮没有独立重跑该支持版本的真实会话，执行期真实工具拒绝仍缺会话级证据，不能把本轮 helper 测试称为实际模型回合测试。
- R05：真正 npm pack 产物已包含 scripts/install-dsh.mjs、scripts/migrate-dsh.mjs、preset YAML、preset-guard。独立解包后两项 --help 和 preset loader 都通过。
- R06：stdio 入口已可用。对真正解包的网关发送 initialize/initialized/list/call，真实 MemoryClient 成功经 HTTP 调用隔离 fixture 的 `/api/v1/health`；验证了正确鉴权 header。此项不是生产 Node 或真实 DSH 会话测试。
- R07：--skip-data 后重跑已返回 data_continued，并提供数据阶段结果；未用个人旧数据库作迁移测试。
- R08：全空过滤反例修正，混合输出仍见 C04。
- R09：顶层布尔值反例修正，嵌套通路仍见 C03。
- R10：原 sourcePath 保留，其他字段所有权/引号变体仍见 C05。

## 独立验证

- Python 全量：437 passed，1 条 Starlette/httpx 弃用警告，退出码 0（`python-full.log`）。
- Node 最终串行复验：36 passed、0 failed、0 skipped，退出码 0（`node-sequential.log`）。
- npm run check：退出码 0；SDK TypeScript build：退出码 0。
- 打包产物入口及成功 MCP HTTP 调用：`artifact-and-mcp.json`，全部通过。
- 原最小反例：`counterexamples-python.json`、`counterexamples-dsh.json`。
- 新反例：`remaining-python.json`、`remaining-migration.json`。
- 工作区 `git diff --check`：退出码 0，因为实现已提交且没有 tracked diff。
- 实际修复提交 `git diff --check HEAD~1 HEAD`：退出码 2，仍有多个 Markdown 尾随空格（包含历史报告日志本身）。两种命令的范围不同，工作区无 diff 不能证明提交无空白问题。本项不计入上述 5 个功能缺陷。

测试环境干扰也已保留：初次 Node 使用系统 TEMP 被 EPERM 拒绝；改为仓库内 TEMP 并行测试时，安装器复制源码与 Python 临时文件删除冲突。最终将 TEMP/TMP、npm cache 移到仓库外独立目录，并在 Python 完成后串行重跑 Node，36 项通过。初次日志 `node-full.log` 保留，不将这些失败误报成产品功能缺陷。第一次 pack 与测试并行遇到临时文件消失，后续串行实际打包/解包成功。

## 交付边界与下一步

优先修 C01/C02，再修 C03/C04/C05。把本轮反例加入正式回归后，再跑 Python/Node/SDK 和支持版本的临时 DSH 会话工具拒绝测试。F04-real-dsh.md 自身已披露未通过实际模型回合逐个调用工具；当前本机默认 DSH CLI 独立读取为 0.2.0-rc.2，本轮未安装、升级或修改真实 DSH 环境。

本报告不要求启用明确延期的外部 embedding、cross encoder、神经 Dream 或通用因果推理。Canvas 仅做源码语法转译检查，不声称执行了 UI 视觉验收。

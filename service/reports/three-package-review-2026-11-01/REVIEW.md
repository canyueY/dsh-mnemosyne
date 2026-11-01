# Mnemosyne 三工程联合复核报告

日期：2026-11-01。结论：**已有可运行实现及绿色回归，但暂不通过“全部落地完成”的联合验收。**

## 范围与方法

检查 E00—E15 综合改进、D00—D06 DSH 安装迁移适配、M00—M07 检索质量与认知正确性，共 31 个任务。用户《工程包.txt》作为原实施要求用于比对，不作为本轮重新执行实施/发布的指令。通用采集模板不在这三个工程中。

读取进度、契约、任务报告和实际调用链；独立重跑当前全量检查，并添加临时 Bank/DSH profile 反例。未修复业务代码、未改三套原进度文件，未运行生产迁移、发布或真实账号操作。新增审查脚本和日志位于本报告同目录。

## 本轮实际重跑

- Python：430 passed，1 条 Starlette/httpx 弃用警告；退出码 0。
- Node：30 passed、0 skipped；退出码 0。
- npm run check：退出码 0。
- TypeScript SDK npm run build：退出码 0。
- git diff --check：退出码 2，仍有 DSH_INTEGRATION.md:63、MNEMOSYNE_NODE_OPERATIONS.md:3、consolidate.py:344 的空白问题。这项没有通过，不将其归因扩展成未经确认的功能回归。
- npm pack dry-run：完成，验证到关键文件缺失。
- 新增反例：5 个 Python API/流水线反例、2 个迁移反例、发布文件清单和 MCP 子进程检查，共 9 个有隔离复现的缺陷；另 1 个 preset 注册/验收缺口由静态调用链确认。

现有回归通过与下面缺陷可以同时成立。E15 的 413 是此前阶段，新增 MA 用例后的当前总数为 430；数量变化本身不是造假证据。问题在于新增测试没有覆盖真实拒绝路径和宿主运行入口。

## 发现的问题

### [P1] R01｜Checkpoint 更新可跨 Bank 覆盖已有记录

涉及：E06 / M06。位置：[service/src/cognitive_memory/cognition/session_checkpoint.py](D:/HuaweiMoveData/Users/残月Y/Desktop/桌宠/dsh-mnemosyne/service/src/cognitive_memory/cognition/session_checkpoint.py:231)。

实际发现：只获 personal 读写权限的 Agent，提交已知 projects checkpoint_id，同时在 body 声明 personal，接口返回 200。表行仍属于 projects，但 manifest.goal 被覆盖为该 Agent 的内容。入口只检查新 body.bank_id，UPSERT 未校验既有记录所属 Bank/会话。

影响：持有某 Bank 写权限的调用方可篡改其他 Bank 的任务状态；这不是单纯的报告或界面缺口。前提是知道目标 checkpoint_id，不能把 ID 不易猜测当授权。

建议修复：更新前在同一事务核对既有 checkpoint 的 Bank、会话及版本，禁止更换所属范围；SQL 更新条件绑定已授权范围，并对跨 Bank 冲突返回拒绝。补实际 REST 的两个 principal/Bank 反例。

证据：counterexamples-python.json / cross_bank_checkpoint_overwrite。

### [P1] R02｜答案选择忽略目标 arguments，茶与咖啡会错配

涉及：E03 / M01。位置：[service/src/cognitive_memory/cognition/answer_selection.py](D:/HuaweiMoveData/Users/残月Y/Desktop/桌宠/dsh-mnemosyne/service/src/cognitive_memory/cognition/answer_selection.py:173)。

实际发现：仅存 A喜欢咖啡；通过 REST 请求 subject=A、predicate=喜欢、arguments=[茶]，返回 chosen，选中的 arguments 却是咖啡。_resolve_target 保存 arguments，但调用 _match_claim 时没有传入和比较它。

影响：同主体同谓词的不同命题被当成同一个问题，破坏当前答案选择最基本的目标正确性。

建议修复：将完整目标 Atom（包含 arguments、否定及作用域）传入匹配；不支持精确比较时返回限定或未知。补同主体/谓词而对象不同、对象无命中及多对象回归。

证据：counterexamples-python.json / target_arguments_ignored。

### [P1] R03｜已 rejected 的 Claim 仍被返回 chosen

涉及：E03 / M02。位置：[service/src/cognitive_memory/cognition/answer_selection.py](D:/HuaweiMoveData/Users/残月Y/Desktop/桌宠/dsh-mnemosyne/service/src/cognitive_memory/cognition/answer_selection.py:406)。

实际发现：将 B喜欢咖啡 经现有生命周期接口转成 rejected 后调用选择，仍返回 chosen，selected.lifecycle=rejected。过滤仅明确排除 retracted/部分过期替代状态；lifecycle 排名靠后也无法在唯一候选时阻止采用。

影响：已否决知识能重新进入确定选择；没有写数据库 verified 不能证明判断已正确或已核验。当前 _match_claim 也没有据关联 Evidence/Verification 对所选认识做完整就绪检查。

建议修复：按认识状态和当前有效验证结果决定资格，拒绝 rejected/retracted；争议或不充分信息带明确边界。自述例外仍需可追溯且正确的类型与范围，不能以 preference 语法自动洗成自述。测试状态撤回、来源缺失及反证。

证据：counterexamples-python.json / rejected_claim_chosen。

### [P1] R04｜Agent preset 的验收仍停留在模拟函数

涉及：D04。位置：[src/agent-preset.js](D:/HuaweiMoveData/Users/残月Y/Desktop/桌宠/dsh-mnemosyne/src/agent-preset.js:117)。

实际发现：createSessionWithPreset 明确注释 Simulate，直接生成 sess_mnemosyne_<时间戳> 返回成功；listAgentPresets 也是本地列表。src/index.js、安装脚本和 bundle 没有接入该 preset 注册或工具授权函数。YAML 声称通过 dsh-mnemosyne-agent-preset insert 注册，但 cordis.patch.yml 实际只有主插件 insert 和说明注释。

影响：本地函数返回 session_id，不代表真实 DSH agentPresets/list 可见或 session/create 成功；工具白名单检查未证明实际会话会执行。

建议修复：使用目标 DSH 的真实 preset 注册/加载契约，挂载实际工具策略。用临时 DSH_HOME 启动受支持版本，走真实 list/create 并验证会话工具可见范围，不能再直接测试模拟函数代替。

证据：静态调用链；src/test/preset.test.mjs；cordis.patch.yml。

### [P1] R05｜发布包漏掉安装脚本和 preset 文件

涉及：D01 / D04。位置：[package.json](D:/HuaweiMoveData/Users/残月Y/Desktop/桌宠/dsh-mnemosyne/package.json:29)。

实际发现：npm pack --dry-run --json --ignore-scripts 显示 scripts/install-dsh.mjs、scripts/migrate-dsh.mjs、dsh/agent-presets/mnemosyne/agent.cordis.yml 均未打包；但 package scripts 和 src/agent-preset.js 引用它们。本地 file 源测试能访问完整仓库，从而掩盖缺文件。

影响：拿打包产物安装的用户无法使用声明的安装/迁移命令，preset loader 也会缺资源。本结论针对所检查 npm 打包产物，不把本地安装成功当发布包成功。

建议修复：补齐 files 白名单及对应文档资源；在真正打包产物的干净目录验证安装命令 --help、迁移入口和 preset 资源加载，再做临时 profile 集成。

证据：npm-pack-audit.json。

### [P1] R06｜新增 MCP REST 网关没有 stdio 运行入口

涉及：D05。位置：[src/mcp-gateway.js](D:/HuaweiMoveData/Users/残月Y/Desktop/桌宠/dsh-mnemosyne/src/mcp-gateway.js:51)。

实际发现：向 node src/mcp-gateway.js 的 stdin 发送 initialize，进程退出码 0、stdout 为空。文件仅导出类和进程内 handle，没有 stdin/stdout 循环；package/scripts 也没有连接该类的 stdio 启动入口。现有 D05 测试直接调用 handle 并注入客户端。

影响：新增网关不能作为所承诺的 stdio MCP 服务器使用。Node 服务内部既有 Python MCP 是另一条路径，其存在不能替代本工程承诺的 REST 宿主网关交付。

建议修复：提供真实启动入口、JSON-RPC 读写/生命周期/错误处理和明确配置方式；用子进程测试 initialize、initialized、tools/list、tools/call，而不是仅测试 handle。

证据：mcp-stdio-audit.json。

### [P1] R07｜Profile 迁完后重跑会跳过尚未完成的数据迁移

涉及：D02。位置：[scripts/migrate-dsh.mjs](D:/HuaweiMoveData/Users/残月Y/Desktop/桌宠/dsh-mnemosyne/scripts/migrate-dsh.mjs:299)。

实际发现：临时 profile 第一次 --skip-data 完成改名；第二次不带 --skip-data，返回 already_migrated，连 data 结果都没有。代码在旧 bundle 消失后直接返回，早于 runMigrateLegacy；首次数据迁移失败也已经改完 profile，重跑同样会走这条分支。

影响：“修复数据问题后重跑”的提示无法兑现，元数据迁移完成被误当作整个迁移完成。

建议修复：持久记录 profile_done/data_pending/data_done 等阶段，重跑核对完成状态并继续未完成的数据阶段。增加部分完成、数据阶段失败再重试和完成后幂等的独立测试。

证据：counterexamples-dsh.json。

### [P2] R08｜融合过滤清空结果后又恢复过期候选

涉及：E09 / M03。位置：[service/src/cognitive_memory/retrieval/pipeline.py](D:/HuaweiMoveData/Users/残月Y/Desktop/桌宠/dsh-mnemosyne/service/src/cognitive_memory/retrieval/pipeline.py:226)。

实际发现：记录 metadata.validity_to=2000-01-01，current 检索中融合返回 unknown/no_safe_items，但流水线通过过滤结果 or items 恢复原候选，最终 PARTIAL 仍携带该过期记录。

影响：后过滤本应强制生效，却在安全结果为空时失效；状态写 partial 并不能替代过滤。

建议修复：区分通道不可用与安全过滤为空，禁止对后者回退原始候选；携带完整时间信息并补普通 /retrieve 的过期/越权/全量剔除链路测试。

证据：counterexamples-python.json / expired_fusion_result_restored。

### [P2] R09｜客户端布尔值能把字符估算标为精确硬上限

涉及：E04 / M05。位置：[service/src/cognitive_memory/core/service.py](D:/HuaweiMoveData/Users/残月Y/Desktop/桌宠/dsh-mnemosyne/service/src/cognitive_memory/core/service.py:1023)。

实际发现：POST /contexts/build 仅设 has_accurate_tokenizer=true，返回 measurement=measured、hard_limit_verified=true；实际仍固定传 estimate_fn=estimate_tokens_chars，未接入对应 tokenizer。

影响：违反不把 token 估算当精确硬上限的契约，宿主可能据此错误规划上下文。

建议修复：measurement 与硬验证状态由实际计量器和完整请求覆盖决定；没有真实 tokenizer 时忽略该声明或返回不支持。宿主提供计量须单独标可信范围，不能用一个 bool 覆盖全部片段。

证据：counterexamples-python.json / client_flag_makes_heuristic_measured。

### [P2] R10｜迁移全局字符串替换会改动其他插件配置

涉及：D02。位置：[scripts/migrate-dsh.mjs](D:/HuaweiMoveData/Users/残月Y/Desktop/桌宠/dsh-mnemosyne/scripts/migrate-dsh.mjs:114)。

实际发现：与本插件无关的 keep-other.config.sourcePath=D:/fixtures/dsh-cognitive-memory/archive.txt，被改成 D:/fixtures/dsh-mnemosyne/archive.txt。rewriteCordis 对全文 split/join，未限定 owned insert 行。

影响：违反其他配置逐字节保留的要求，可能把其他插件指向错误资源。

建议修复：只修改已解析且属于本插件的 id/name/owned 字段，或做保留其他字节的限定补丁。测试其他插件的路径、字符串和注释包含旧名称时保持不变。

证据：counterexamples-dsh.json。

## 按工程的结论

- **E00—E15：部分通过。** 证据/版本、接口、预算与恢复等已存在实际代码；但 E03 的目标与状态选择、E06 的 checkpoint 更新授权、E09 的过滤回退、E04 的计量真实性阻断当前联合验收。不是只缺文档。
- **D00—D06：部分通过。** 临时本地安装、元数据迁移和函数测试有实际执行；但 preset 不是实际宿主会话测试，新增 MCP 网关无 stdio 入口，发布包漏文件，迁移重入及其他配置保留失败。D06 报告的本地 DSH 为 0.2.0-rc.2 且接受版本风险，不能替代所宣称 0.2.1 目标版本的完整宿主验收。
- **M00—M07：部分通过。** 17 个 MA 测试已合并到本轮 430 项全量回归；但目标匹配、生命周期、强制后过滤和真实 token 计量的反例仍失败。增加字段和直接调用辅助函数，不足以证明相应用户链路已完成。

## 仍需补证的链路

以下作为覆盖缺口，不在没有充分复现时扩大成额外正式缺陷：

- DSH settings 测试调用 settings.js 中进程内 Map；需补真实设置服务写入、重启后持久化与运行采用的证据。Config 导出可能由宿主自动接入，因此本轮不直接断言设置页一定无法工作。
- WorkerLeaseManager/ConsumerWatermarkStore 的单元反例有价值，但当前 jobs/dispatcher.py 未见这两者接入；/sessions/consolidate 接收客户端 atoms 后返回派生对象，不能仅凭 helper 测试宣布后台队列的持久化巩固全链路完成。需要补实际任务提交、崩溃恢复、水位和旧 worker 写入测试。
- checkpoint 恢复返回所存 evidence_revisions，但未见主动查询权威来源当前修订的路径；需要补来源变化后恢复的真实用例，而非只检查字段存在。
- 实际 DSH 0.2.1 设置/preset/MCP 会话端到端，以及从发布产物安装，仍应分别留证。现有本地 file 源安装不涵盖这些路径。

## 为什么原有测试没挡住

- preset.test.mjs 直接调用模拟 list/create；没有真实 DSH 会话。
- test_ma04 中 counterevidence_refs is not None 对空列表也成立；不能证明关键反证已召回或检查。
- test_ma11、test_ma14 等主要检查字段，不保证真实来源、持久化与计量来源。
- AnswerSelection 接口测试允许 chosen/partial/unknown 任一状态，未断言目标 arguments 或拒绝状态。
- 迁移重入测试与安装测试没有覆盖待完成数据阶段、其他配置内同名字串、真实打包缺文件。

## 修复与重新验收顺序

1. 先修 R01—R03，补跨 Bank 写入授权、完整目标和拒绝状态的 REST 回归。
2. 再修 R08—R09，确保当前检索不能回退已剔除内容，计量标签与实际计量器一致。
3. 修 R05、R04、R06：打包资源 → 真正 preset 注册和会话工具策略 → 真正 stdio MCP。用干净发布包和受支持宿主验证。
4. 修 R07、R10，补 profile/data 两阶段失败重入和其他配置字节保留测试。
5. 为补证链路增加真实入口用例，重跑 Python/Node/SDK/diff-check；按实际结果更新三套进度及最终报告。保持研究/外部模型 disabled/not_measured，这些后置项本轮不作为缺陷。

## 复现与原始记录

- `reproduce_review.py`：使用 service/.venv Python 运行，自动建立临时 Bank，输出 `counterexamples-python.json`。
- `reproduce_dsh.mjs`：node 运行，只改同目录生成的隔离 profile，输出 `counterexamples-dsh.json`。
- `mcp-stdio-audit.json`：向新增网关进程发 initialize，退出但没有响应。
- `npm-pack-audit.json`：真实 npm pack dry-run 文件清单检查摘要。
- `checks.json`、`python-full.log`、`node-full.log`、`node-check.log`、`sdk-build.log`、`diff-check.log`：本轮实际命令结果。

报告是当前工作区的复核快照，不修改原作者的完成标记；完整源码审计、生产效果和所有平台/宿主组合不在已验证范围内。

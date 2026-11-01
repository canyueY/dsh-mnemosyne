# 复核修复契约 v1

修复必须遵守以下不变量；每条缺陷的完成标准以 ACCEPTANCE_CASES.json 的给定/当/期望为准。

## 1. 授权与范围

- Checkpoint 更新在同一事务核对既有记录的 `bank_id`、`session_id`、`version`；禁止更换所属 Bank 或会话。跨 Bank/越权返回拒绝，不靠 ID 不可猜测当授权。
- AnswerSelection 完整目标 Atom 必须包含 `subject`、`predicate`、`arguments`、否定与作用域；同主体同谓词不同对象不得错配。无法精确比较时返回 `partial` 或 `unknown`，不返回错误对象的 `chosen`。

## 2. 认识状态与时间

- `rejected` / `retracted` / 过期的 Claim 不得进入 `chosen`；争议或不充分信息带明确边界。
- 自述例外必须可追溯、类型正确，不因 preference 语法自动洗成自述。
- 后过滤强制生效：安全过滤为空与通道不可用是两种结果；安全结果为空禁止回退原始候选。

## 3. 宿主接口真实性

- Agent preset 必须通过目标 DSH 的真实 `agentPresets/list` 与 `session/create` 验证；不得用模拟函数返回 session_id 冒充成功。
- 工具白名单要在真实会话中证明生效。
- MCP 网关必须有真实 stdio 入口：`initialize` / `initialized` / `tools/list` / `tools/call`，并处理 JSON-RPC 读写、生命周期与错误。

## 4. 发布与迁移

- npm 发布产物必须包含 `scripts/install-dsh.mjs`、`scripts/migrate-dsh.mjs`、`dsh/agent-presets/**` 及文档；用干净打包目录验证，不用本地 file 源。
- 迁移分阶段持久化 `profile_done` / `data_pending` / `data_done`；重跑继续未完成阶段，完成后幂等。
- 迁移只改本插件拥有的 id/name/owned 字段；其他插件路径、字符串、注释逐字节保留。

## 5. 计量与后台链路

- `measurement` 与 `hard_limit_verified` 由实际计量器和完整请求覆盖决定；无真实 tokenizer 时不得标 `measured`/`hard_limit_verified=true`，应返回不支持或忽略该声明。
- settings 持久化要在重启后生效并驱动运行采用。
- WorkerLeaseManager / ConsumerWatermarkStore 必须接入 jobs/dispatcher 主链路；提供崩溃恢复、水位、旧 worker 写入测试。
- Checkpoint 恢复要主动查询权威来源当前修订，不只检查字段存在。

## 6. 通用

- 核心逻辑留在 `service/`；测试不得伪造 passed；skip/xfail 不计通过。
- 研究/外部模型能力状态记 `disabled/not_measured`，本轮不作为缺陷。

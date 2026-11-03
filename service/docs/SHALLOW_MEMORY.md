# 外部感知渐变记忆（2.0.0）

## 生命周期

外部 Observation 入站的同一事务内创建 episodic/candidate 初步记忆，标记 `shallow_memory.long_term_class=provisional_deletable`、`formal_long_term=false`。它位于已有记忆库中，可检索、可通过既有遗忘入口删除，但不属于正式长期认知。初次入库即产生关键词、印象词和 `initial_cognition`；它们是未验证的规则摘要，不是人格事实。

年龄从 Node 接收时刻计算，避免历史来源时间或错误上游时钟直接跳过阶段：

- 0≤年龄<80 天：临时原文视图。
- 80≤年龄<120 天：提示词视图。
- 120≤年龄≤200 天：印象线索视图。
- >200 天：未晋升的条目标记 superseded，退出默认检索。原始 Observation、版本与审计仍保留，不执行物理清除。

配置：`src/cognitive_memory/resources/shallow-memory.json`，默认每 24 小时提取一次，关键词上限 16。当前为本地词频/短句提取器 `local_phrase_frequency_v1`，保留“不喜欢”等否定短句，不声称具备 LLM 语义理解。BackgroundRuntime 每 60 秒检查一批最久未提取的记录，每批最多 100；满足周期或跨阶段时更新视图及版本。离线期间不运行，恢复后补做当前视图，不伪造停机期间的每日提取。

## 筛选与正式长期

正式长期仅在年龄 80–200 天期间经现有 `maybe_promote` 筛选：必须具备有效的数据库 Claim 验证及可追溯 Evidence，未解决反证仍会阻止晋升。无验证时继续 candidate，不能仅凭年龄、提取频次、重复出现或点赞晋升。

筛选通过后保留原记忆 ID、Observation 证据和历史，更新 `formal_long_term=true`、`long_term_class=formal`、status=active，并清除浅层 expires_at。该记录不再参加浅层维护。

Node 管理员可发起人工复核：

```http
POST /api/v1/shallow/{memory_id}/select
Authorization: Bearer <Node token>
Content-Type: application/json

{"confirm":true,"reason":"复核此印象是否已具有独立有效证据"}
```

该入口记录复核理由并调用同一晋升判断，既不绕过证据，也不复制出另一条长期记录。策略查询：已认证的 `GET /api/v1/shallow/policy`。

## 接入与同步

video_observation、agent_inference 默认进入此流程；system 仅在 metadata.domain/scene 为 music、video、game、minecraft、screen 时作为感知处理。任意来源也可显式传 `metadata.external_perception=true`。普通 system 运维和已审批历史迁移继续原流程；已有历史长期记录不自动批量降级。

桌宠视频桥接显式附加感知标记。桌宠 cognitive_memory_settings 提供 `shallow_policy_path`，自动启动 Node 时通过 `MNEMOSYNE_SHALLOW_POLICY` 传递本工程同一资源文件。修改阈值时修改此唯一文件；不在桌宠配置中复制另一套天数。使用外部已运行 Node 时，以该 Node 返回的策略为准。新代码需在对应 Node/桌宠进程重启后加载，本次未操作生产记忆库或重启用户服务。

同步约束写入宿主 `.cursor/rules/mnemosyne-desktop-sync.mdc` 和本仓 `AGENTS.md`。版本保持 2.0.0。

## 验证

- 全量 Node 回归：455 passed（补充正式晋升正向用例前）。
- 浅层专项：6 passed，含 80/120/200 天边界、原文保留、幂等、直接长期绕过阻止、负向筛选、真实 Evidence 正向筛选与 TTL 清除、认证确认。
- 桌宠桥接回归：7 passed，含 Node/桌宠共享同一策略路径。
- 全部验证使用临时 Bank；现有生产库未批量迁移。

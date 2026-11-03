# Memory Bank：借鉴 Mem0 的检索改进（2026-11-03）

本轮修改属于 Mnemosyne Node 内的 Memory Bank 检索路径，不增加 Mem0 云服务依赖，不改变版本号，不迁移已有数据库。测试仅使用临时 Bank。

## 参考依据与取舍

- Mem0 的 API 提供 user/agent/run 等范围字段。借鉴其显式限定检索对象的方式，加入可组合的精确范围过滤。
- Mem0 目前 README 描述的托管算法采用追加写入、词法/语义/实体多信号检索与时间推理；同仓开源 SDK 的写入路径仍需区分版本。不能把托管基准成绩等同于开源 SDK 的效果，也不能直接认定迁移后会更快。
- Mnemosyne 已有候选、证据、历史版本、反证与多路融合。因此本轮改进基础 search 的范围、过期处理与词法排序，继续保留既有认知与审核流程。

参考原文（实现为自主编写，未复制 Mem0 源码）：

- https://github.com/mem0ai/mem0
- https://github.com/mem0ai/mem0/blob/main/mem0/memory/main.py
- https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/memory-operations/update.mdx

## 已实现

1. SearchQuery 新增可选 user_id、agent_id、run_id。多个字段同时传入时取交集，逐字精确匹配 Memory.metadata 中的同名字段。没有对应字段的旧记录不匹配显式范围。
2. 在 FTS、LIKE、中文降级、空查询四个入口中，Bank、范围和过期过滤均先于 LIMIT；避免其他用户的记录或过期条目挤占有效结果名额。
3. current 模式排除 expires_at 已过期或无法解析的记录；historical/any 允许查询这些记录，仍受 Bank 和显式范围限制。没有执行物理删除。SQLite julianday 处理 ISO 时区偏移。
4. FTS5 使用 BM25 排序，并以更新时间、id 收敛同分排序。现有时空、情绪与领域后处理继续运行。
5. REST POST /api/v1/memories/search 接收 SearchQuery；MCP memory_search 增加相同范围参数及 banks、mode。

## 调用示例

写入时在现有 MemoryProposeIn.metadata 中携带范围：

```json
{"content":"最近改喝低咖啡因咖啡","metadata":{"user_id":"alice","agent_id":"assistant-a","run_id":"session-1"}}
```

通过已认证的 REST 调用或 MCP memory_search 检索：

```json
{"query":"咖啡","user_id":"alice","agent_id":"assistant-a","banks":["personal"],"mode":"current","limit":10}
```

run_id 是可选的会话范围，省略可跨会话检索。agent_id 指存储元数据的归属字段，不是本次调用的身份凭证。范围过滤只是查询限制，授权继续使用现有 token、Agent grant 与 Bank 权限；不得以客户端提供的 user_id 代替授权。未携带范围的旧调用保持 Bank 级查询语义。

## 验证与边界

tests/unit/test_mem0_inspired_bank_search.py 在真实临时 SQLite Bank 中覆盖：跨用户/Agent/会话隔离、跨 Bank 默认边界、limit=1 时的有效记录召回、过期与损坏时间、历史保留、注入字符串、REST/MCP 透传和 BM25 排序。辅助回归覆盖既有融合、生命周期与 review fixes。

本轮未改变 /retrieve、/contexts/build 或各 SDK 的参数契约，没有声称完成跨入口的用户级权限系统。未实现新的 LLM 事实提取器、实体索引或真实 embedding 服务；未运行 Mem0 与 Mnemosyne 的同数据质量/性能对照，不能宣称命中率或延迟提升百分比。后续应以脱敏样本评估事实原子化、增量写入与实体召回，并保留推测/事实分离及纠错回滚。

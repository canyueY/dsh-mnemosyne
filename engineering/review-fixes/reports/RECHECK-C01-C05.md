# 复查 C01–C05 修复记录

日期：2026-11-01<br>
基线：`13fead5`<br>
复查报告：`service/reports/three-package-recheck-2026-11-01/RECHECK.md`

## C01 / P1：无来源 Claim 被确定采用

修复：`AnswerSelectionService._has_traceable_source`

- `source_memory_id` 必须指向存在且未失效的 Memory；
- 或 claim 存在真实 Evidence 对象；
- 或最新 Verification 的 evidence_refs 指向真实 Evidence；
- 无来源候选不进入 `chosen`，空匹配返回 `unknown` + `source_missing`；
- 显式 self_report 仍要求来源，不再仅凭语法例外。

测试：

- `test_review_fixes_f0x.py::test_c01_sourceless_claim_not_chosen_but_sourced_claim_is`
- 原 E03/MA 用例改为显式真实 source fixture。

## C02 / P1：旧 worker 在提交被拒前写入 Memory

修复：

- `MemoryService.propose_memory(..., write_guard=...)` 在**写事务内、插入前**执行 guard；
- `consolidate_observation(..., write_guard=...)` 透传；
- dispatcher 的 consolidation handler 同时保留入口检查和写层 guard。

测试：

- `test_worker_fencing.py::test_stale_worker_cannot_write_memory_after_takeover`
- 在真实 `BackgroundRuntime.run_tasks_once` 的 `propose_memory` 前注入新租约，断言 `memory_evidence` 为 0，任务回 pending/dead。

## C03 / P2：嵌套 envelope 伪造精确 token 计量

修复：`core/service.py` 的 `/contexts/build`

- 不再读取 `body.envelope.accurate_tokenizer` 或 `body.envelope.measurement`；
- 服务端固定使用 `estimate_tokens_chars`，`measurement=estimated`、`hard_limit_verified=false`。

测试：

- `test_c03_nested_envelope_tokenizer_flags_ignored`（两层声明各测一次）。

## C04 / P2：brief/fragments 重新带出过期记录

修复：`retrieval/pipeline.py`

- `build_context` 返回后，用过滤后的 `items` 的 id 集合裁剪 `fragments`；
- 按同一安全集合重渲染 `brief`；
- 无 items 时清空 brief/fragments。

测试：

- `test_c04_mixed_filtered_context_does_not_leak_expired_record`（有效 + 过期混合）。

## C05 / P2：迁移正则跨 owned 行、漏引号

修复：`scripts/migrate-dsh.mjs`

- 只匹配顶层 `- id:` 插件行的 id/name；
- 只改该 row 的 immediate child `name`，不碰嵌套 `config.id/name`；
- 支持单/双引号、CRLF；其他插件块逐字节保留。

测试：

- `FA18b: quoted owned row changes; other plugin nested id/name is byte-preserved`。

## 当前结果

```text
pytest         441 passed
npm test       37 passed
npm run check  exit 0
```

研究/外部模型仍为 disabled/not_measured。

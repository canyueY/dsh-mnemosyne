# 329f8ff 复验 B01/B02 修复记录

日期：2026-11-01<br>
输入：`service/reports/three-package-finalcheck-329f8ff/VERIFICATION.md`

## B01 / P1：仅反证、空来源 Evidence

修复：`AnswerSelectionService._source_readiness`

- 返回结构化 `support_refs` / `counter_refs` / `unknown_refs`；
- 只有有效正向支持（`source_ref` 非空、`provenance_status != unknown`、`source_type != unknown`、非 invalid）才进入 chosen；
- 显式 `role=counter` 只登记反证，单独存在时返回 `unknown` + `counterevidence_only`，并回传 `counterevidence_refs`；
- 空来源/未知来源 Evidence 返回 `unknown` + `provenance_unknown`；
- 支持 + 反证同时存在时仍可 chosen，但返回 `counterevidence_registered` 和真实 counter Evidence 引用。

测试：

- `test_b01_counterevidence_only_not_chosen_and_disclosed`
- `test_b01_unknown_provenance_evidence_is_not_support`
- `test_b01_support_plus_counter_is_chosen_with_counter_reason`

复跑 `probe_boundaries.py`：

```text
counter_only_source        status=unknown, counterevidence_refs=[evd_...]
unknown_provenance_object  status=unknown
```

## B02 / P2：嵌套列表被当成 owned plugin row

修复：`scripts/migrate-dsh.mjs`

- 先识别 `- insert:` 块与真正的 plugin row 缩进；任意 `- id:`（不只旧/新名）都能建立 row 层级；
- 嵌套 `sources: - id:` 和 block scalar 中的 `- id:` 不再命中；
- 仍只改本插件 row 的 id/name，支持引号与 CRLF，其他插件块逐字节保留。

测试：

- `FA18c: nested list and block scalar rows are not owned plugin rows`

复跑 `probe_nested.mjs`：

```text
exit=0
unrelated_nested_list_preserved=true
```

## 结果

```text
pytest         444 passed
npm test       38 passed
```

研究/外部模型保持 disabled/not_measured。

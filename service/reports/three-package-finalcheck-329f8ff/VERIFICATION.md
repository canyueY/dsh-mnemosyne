# 329f8ff 独立复验

日期：2026-11-01。范围：提交 `329f8ff` 对 C01–C05 的修复及对应回归。只新增验证产物，未修改业务实现、原进度、个人 Bank 或真实 DSH profile。未执行推送；不把本地提交检查表述为远端部署验收。

## 结论

原 C01–C05 的最小反例全部通过复验。C02、C03、C04 对原报告中的具体问题可以关闭；C01、C05 仍各有一个可复现的同要求边界遗漏，因此不能将完整修复包全部关闭。

## B01 / P1：只有反证也能满足“可采用来源”条件

位置：`service/src/cognitive_memory/cognition/answer_selection.py:555`、`:566`；反证输出组装位于 `:295`。

`_has_traceable_source` 收集 claim_evidence 时丢掉了 role，只要任意 Evidence 对象存在就返回 true，没有区分 support 与 counter/contradicts，也不检查对象本身的来源状态。答案输出的 counterevidence_refs 则只来自其他匹配 Claim 的否定角色，没有纳入该 Claim 的真实 Evidence 反证关系。

隔离复现：

1. 用真实 ClaimStore 创建 `D喜欢咖啡`，source_memory_id 为空。
2. 用真实 EvidenceObjectStore 创建来源明确的 doc Evidence，内容为 `D does not like coffee`。
3. 经真实 attach_evidence 以 `role=counter` 关联；接口成功，实际唯一 link 的 role 确实为 counter。
4. 通过真实 REST `/api/v1/cognition/answers/select` 查询 D/喜欢/咖啡。
5. 返回 **chosen、checks_complete=true、counterevidence_refs=[]**，selected 为肯定的 `D喜欢咖啡`。

这不是要求系统理解任意英文反证：反证角色已由调用方通过结构化 role=counter 明确提交。当前代码把已标识的反证当作“有来源即可采用”的资格，从 unknown 升成 chosen，且没有披露该反证。

同一实现还允许 `source_type=unknown、source_ref=""、provenance_status=unknown` 的空来源 Evidence 对象作为 support，使另一个无来源 Claim 变为 chosen。对象存在不能替代来源可追溯性。

修复要求：来源资格返回结构化支持/反证/限定与来源就绪状态，而非对象存在的 bool；只计有效正向支持或具有明确溯源范围的自述，显式处理已登记反证，未知来源对象仍为不足。答案应携带真实 Evidence 引用和反证原因。补“仅反证”“空来源 Evidence”“支持加反证”的 REST 回归。

证据：`boundary-python.json`。重跑：`service/.venv/Scripts/python.exe service/reports/three-package-finalcheck-329f8ff/probe_boundaries.py`。

## B02 / P2：迁移仍把嵌套列表当作 owned plugin row

位置：`scripts/migrate-dsh.mjs:119`–`:121`。

新的 rowRe 只限定行形状为 `- id:`，允许任意缩进，遍历全文而没有识别 YAML 结构或该行是否处于顶层 insert 插件列表。普通嵌套标量 id/name 的原反例已修正，但嵌套列表会再次命中。

隔离复现的其他插件块：

```yaml
- insert:
    - id: keep-other
      name: keep-other
      config:
        sources:
          - id: dsh-cognitive-memory
            name: dsh-cognitive-memory
```

同时放置真正的带引号旧插件条目；文件使用 CRLF。运行迁移退出 0、返回 migrated，真正条目正确迁移，但 keep-other.config.sources[0].id/name 也都被改成 dsh-mnemosyne，其他插件块未逐字节保留。

修复要求：仅在确认结构位置属于本插件拥有的 insert/plugin row 时修改，不能凭缩进任意的 `- id:` 判所有权。可采用结构解析定位后做保留其他字节的补丁，或明确记录 insert 列表层级的解析器；补嵌套列表、block scalar 中相似文本、单双引号、注释及 CRLF 的保留测试。

证据：`boundary-migration.json`。重跑：`node service/reports/three-package-finalcheck-329f8ff/probe_nested.mjs`。

## 原 C01–C05 反例结果

- C01：无来源 Claim 返回 unknown + source_missing；见 `remaining-python.json`。
- C02：在真实 dispatcher/consolidation 调用真实 propose_memory 前注入租约接管，写层 guard 拒绝，written_memories=[]；任务回到 pending。复查脚本仅适配新增 **kwargs 透传 write_guard，没有去掉 guard。
- C02 静态事务核对：Database.transaction 使用 BEGIN IMMEDIATE，guard 位于同一写事务内。Memory、任务状态、水位仍分属不同事务，你对这一边界的说明准确。本轮不把它表述为三者一次原子提交，也不据此否定已关闭的旧 worker Memory 写窗口。
- C03：两种 envelope 精度声明均得到 estimated、hard_limit_verified=false。
- C04：混合有效/过期检索的 items/fragments 仅保留有效 ID，brief 不含过期内容。
- C05：原其他插件嵌套标量 id/name 保留，真正单引号旧插件行已迁移；见 `remaining-migration.json`。完整所有权边界仍见 B02。

## 检查记录

最终结果：Python **441 passed、1 warning**；Node **37 passed、0 failed、0 skipped**；npm run check、TypeScript SDK build、`git diff --check HEAD~1 HEAD` 均退出 0。npm pack dry-run 也退出 0。本轮未重复执行上一轮已通过的实际 tarball 解包和 HTTP 成功夹具测试。

以本目录 `checks.json` 为最终执行记录。Python 全量、Node 全量、npm run check、SDK build 和实际提交 `git diff --check HEAD~1 HEAD` 均独立执行。TEMP/TMP/npm cache 使用仓库外隔离目录；Node 安装测试在 Python 完成后串行执行，避免前一轮的源码复制/临时文件竞争。

原更早一轮的 Python/迁移反例也已重跑，跨 Bank 拒绝、arguments 错配拒绝、rejected 拒绝、过滤全空不恢复、数据阶段续跑等保持原修复结果。

本轮未独立重新启动受支持版本 DSH 的模型会话，也未扩大到研究/外部模型。它们保持 disabled/not_measured，不列为新增缺陷。

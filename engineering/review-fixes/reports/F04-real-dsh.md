# F04 真实 DSH agentPresets / session.create 验证

日期：2026-11-01  
DSH：0.2.1-rc.3（`fileVersion 0.2.1-rc.3`）  
隔离环境：临时 `DSH_HOME` + 本地 `dsh-mnemosyne` 安装，不碰真实 profile。

## 首轮失败（已定位并修复）

1. `dsh-mnemosyne` 插件激活失败：
   `cannot get property "config" without inject`
   - 原因：`src/index.js` 直接读 `ctx.config`，但 `inject` 只有 `tools`。
   - 修复：不再直接访问 `ctx.config`，改用 `ctx.get('config')` / `ctx.get('settings')` 的 optional 路径；`inject` 保持 `['tools']`。

2. `cordis.patch.yml` 插入不存在的 `dsh-mnemosyne-agent-preset`：
   `ERR_MODULE_NOT_FOUND`
   - 修复：删除伪包行，改为真实 `@deepseek-ai/dsh-agent-preset` 行，`config.id=mnemosyne`、`config.plugins=[persona, mnemosyne-tool-guard, tool-ask-user, tool-todo]`。

3. 真实 preset composition 校验失败：
   `the composition must be a top-level list of plugin rows`
   - 原因：preset 注册的是描述对象（persona/tools/session），不是 DSH 的 `{id, plugins}` 结构。
   - 修复：改为真实 `plugins` 列表；`tool-todo` 补 `allowParallelInProgress: true`。

## 通过项

```text
npm test
  36 passed（含 preset-guard 2 项）

agentPresets/list
  standard, ptc, minimal, cordis, mnemosyne
  mnemosyne: name=Mnemosyne Memory, order=10, broken=null

session/create {agentPreset:"mnemosyne"}
  ok=true, sessionId=session-..., agentPreset=mnemosyne

settings/describe
  namespaces 包含 dsh-mnemosyne
  value={auto_start:true, ...}, token secret set=true

settings/update {ns:"dsh-mnemosyne", patch:{auto_start:false}}
  ok=true, revision 0 -> 1

重启同一 DSH_HOME 后
  auto_start=false 保留
  agentPresets 仍含 mnemosyne 且 broken=null
```

## 边界

- 本次验证了真实 list/create 与 settings 写穿/重启持久化。
- `mnemosyne-tool-guard` 已作为 preset plugin 加载（`broken=null` 说明 composition 可解析），执行期 allowlist 由 `ctx.tools.guard` 单测覆盖；未通过真实模型回合逐个工具调用验证。
- 研究/外部模型仍为 disabled/not_measured。

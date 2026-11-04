# DSH 0.2.1-rc.5 冷启动兼容修复报告

日期：2026-11-04
版本：`dsh-mnemosyne` 2.0.0（版本保持不变）
验证宿主：DeepSeek Harness Desktop `0.2.1-rc.5`，Node `v24.21.0`

---

## 1. 结论

用户反馈的冷启动崩溃**已真实复现**，根因不是 Schema 版本冲突，而是 rc.5 的
`dsh-settings` 在 `settings.describe()` 中对 **`Config === null`** 做
`'toJSON' in schema`，直接抛 `TypeError`。

修复采用**方案 B**：插件入口不再导出 `Config`，配置由插件行（`cordis.patch.yml`
的 `config:`）传入 `apply(ctx, config)`；插件不再在启动时调用宿主
`settings.describe()`。已验证：

- 修复版连续两次冷启动成功；
- `settings.describe()` 不再因本插件报错（真实宿主探针返回 21 个其它命名空间）；
- `memory_*` 11 个工具正常注册；
- 自定义 `data_dir` / `auto_capture` / `base_url` / `token` 在重启后保持；
- 打包后的 tgz 安装后同样通过。

---

## 2. 复现证据

### 2.1 故障插件（临时副本，仅用于复现）

在隔离 profile 中放入与旧版等价的 `export const Config = null`，启动 rc.5 web，
2.5 秒后调用真实 `ctx.settings.describe()`：

```json
{
  "ok": false,
  "error": "TypeError: Cannot use 'in' operator to search for 'toJSON' in null",
  "stack": "at Proxy.schema (.../dsh-settings/lib/index.js:540:40)\n    at Proxy.describe (.../dsh-settings/lib/index.js:415:70)"
}
```

对应宿主源码：

```js
// @deepseek-ai/dsh-settings/lib/index.js
schema(entry) {
  const schema = entry.fiber?.runtime?.Config;
  return schema !== undefined && "toJSON" in schema ? schema : undefined;
}
```

`null !== undefined` 为 true，随后 `'toJSON' in null` 抛错。`resolveConfig()` 只判断
`if (!runtime.Config)`，所以 `null` 能通过激活，故障被推迟到 settings 页面/欢迎页初始化。

### 2.2 修复版

同一隔离 profile、同一个真实 `settings.describe()` 探针：

```json
{
  "ok": true,
  "count": 21,
  "memoryTools": [
    "memory_health", "memory_search", "memory_get", "memory_context",
    "memory_select_answer", "memory_propose", "memory_feedback",
    "memory_update", "memory_forget", "memory_task_status", "memory_observe"
  ],
  "rowConfig": { "auto_start": true, "auto_capture": true }
}
```

连续两次冷启动均得到上述结果；`settings.describe()` 不再包含本插件条目
（没有 Config 就没有自动表单），也不会崩溃。

### 2.3 自定义配置

隔离 profile 覆盖行配置：

```yaml
- id: dsh-mnemosyne
  config:
    auto_start: false
    auto_capture: false
    data_dir: D:/.../tmp/rc5-coldstart/bank
    base_url: http://127.0.0.1:9
    token: probe-token
```

两次冷启动后探针读回：

```json
{
  "auto_start": false,
  "auto_capture": false,
  "data_dir": "D:/.../tmp/rc5-coldstart/bank",
  "base_url": "http://127.0.0.1:9",
  "token": "probe-token"
}
```

临时 Bank 保持为空，没有被默认值覆盖或切到新库；`auto_start=false` 没有启动
错误服务。

---

## 3. 根因与取舍

| 方案 | 结果 |
| --- | --- |
| A. 修复 Schema 构造，保留 Config | 需要 `@deepseek-ai/schemastery` 在插件解析链中可解析，并满足 rc.5 的 `~standard`/`toJSON` 契约；当前用户安装目录的 peer 解析不可靠，失败仍会得到 null |
| B. 不导出 Config，使用插件行配置 | **采用**。rc.5 的 Loader 会把行 `config` 作为 `apply` 第二参数传入；不需要宿主 settings 服务参与激活 |

选择 B 的原因：改动小、可验证、与用户现场“取消 Config 导出后恢复”的观察一致；
同时保留 `memory_*` 工具、Memory Node 与所有配置字段。

**这不是说 rc.5 的 null 处理没有问题。** 宿主仍应在 `settings.describe()` 中跳过
`null`/无 `toJSON` 的 Config；上游 issue 草稿见
`docs/DSH-0.2.1-rc.5-settings-toJSON-issue-draft.md`。

---

## 4. 配置契约与兼容

优先级（高 → 低）：

```text
apply(ctx, config) 插件行 config
  > MNEMOSYNE_SETTINGS_FILE 旧设置文件（仅非默认值）
  > ctx.get('config')['dsh-mnemosyne' / 'dsh-cognitive-memory']
  > CONFIG_DEFAULTS
```

- `apply(ctx, config)` 接收 Loader 传入的行配置；不再访问 `ctx.config`。
- **不再调用 `ctx.get('settings').describe()`**：rc.5 的 describe 会遍历所有插件
  Config，既可能触发 null 崩溃，也会把 Schema 默认值回灌，掩盖显式 `data_dir`。
- 行配置先经过 `validateConfigPatch()`：未知字段或类型错误立即失败，错误码可诊断。
- 旧 `settings.yaml` / `MNEMOSYNE_SETTINGS_FILE` 仍可读取，但只取非默认值。
- `token` 在 describe/日志中仍然是 `********`；行配置本身是用户本机文件，不写入日志。

---

## 5. 修改文件

| 文件 | 修改 |
| --- | --- |
| `src/index.js` | 移除 `resolveConfigExport`/`Config` 导出；`apply(ctx, config)` 接收行配置；校验行配置 |
| `src/config-schema.js` | `resolveConfigExport()` 失败明确抛错，不再返回 `null`（仅诊断用，入口不导出） |
| `src/settings.js` | 移除激活期 `settings.describe()`；加入旧文件配置层；明确优先级 |
| `cordis.patch.yml` | 行配置示例 `auto_start` / `auto_capture`；说明不要导出 Config |
| `dsh.plugin.json` | 设置能力声明改为“无自动表单，行配置” |
| `src/test/cold-start-config.test.mjs` | 新增真实边界回归：不导出 Config、null 会崩、行配置优先、非法行配置在 settings 前失败 |
| `src/test/real-coldstart.test.mjs` | 真实 rc.5 host 两轮冷启动 + `settings.describe` + 工具注册探针（需显式环境变量） |
| `plugins/Mnemosyne-DSH/src/{index,settings,config-schema}.js` | 同步 standalone 发布包 |

`桌宠/dsh-mnemosyne` 是 junction，未单独修改；实际修改只在
`Mnemosyne项目集/projects/dsh-mnemosyne` 与 `Mnemosyne项目集/plugins/Mnemosyne-DSH`。
用户 profile 实际加载的是 `Mnemosyne项目集/plugins/Mnemosyne-DSH`（standalone），
源码项目的修复已同步到该发布包并用 tgz 冷启动验证。

---

## 6. 验证命令与结果

### 6.1 单元/回归

```powershell
cd Mnemosyne项目集/projects/dsh-mnemosyne
node --test src/test/*.mjs
# 40 tests, 39 pass, 1 skipped (real host test without env)

# 全量 npm test（含 legacy）：45 tests, 44 pass, 1 skipped

node --check src/index.js
node --check src/settings.js
node --check src/config-schema.js
```

### 6.2 真实 host 冷启动

隔离 `DSH_HOME` + `mnemo-test` / `mnemo-packed` profile：

```powershell
$env:COLDSTART_DSH_CLI = '...\dsh.cmd'
$env:COLDSTART_DSH_HOME = '...\tmp\rc5-coldstart\home'
$env:COLDSTART_DSH_PROFILE = 'mnemo-packed'
$env:COLDSTART_PROBE_DIR = '...\tmp\rc5-coldstart'
node --test src/test/real-coldstart.test.mjs
# 1 pass in ~9s
```

结果：两次启动 `settings.describe` 成功，11 个 `memory_*` 工具注册，
`rowConfig.auto_start=true`。

### 6.3 桌宠桥接同步检查

本次没有修改桌宠 `modules/legacy/murasame/cognitive_memory_bridge.py`；字段契约仍包含
`auto_start`、`service_root`、`data_dir`、`python`、`base_url`、`token`，与插件行配置
一致。`auto_capture` 是插件侧会话采集开关，不属于桌宠桥接字段。

```powershell
.venv\Scripts\python.exe -c "from modules.legacy.murasame.cognitive_memory_bridge import cognitive_memory_settings as f; s=f({}); print([k for k in ('auto_start','service_root','data_dir','python','base_url','token') if k in s])"
# ['auto_start', 'service_root', 'data_dir', 'python', 'base_url', 'token']
```

环境里 `.venv` 和 DSH Node venv 都没有 `pytest`，因此没有运行 `tools/tests/` 下的
pytest 风格桥接测试；这是本次未验证项，不是因为桥接代码失败。

### 6.4 打包产物

```powershell
cd Mnemosyne项目集/plugins/Mnemosyne-DSH
npm pack --pack-destination ...\tmp\rc5-coldstart
# dsh-mnemosyne-2.0.0.tgz, 747.2 kB, 316 files
# sha1 423ef03c35f61d52de9b9a51304f719478c0303e
```

将 tgz 安装到全新隔离 profile `mnemo-final3`，加 `coldstart-probe`，再跑两次
真实冷启动测试：通过（`ok=true`、11 个 `memory_*`、`rowConfig.auto_start=true`）。

---

## 7. 尚未验证

- **欢迎页真实 UI**：本机没有浏览器自动化控制；用真实 `settings.describe()`
  覆盖了欢迎页初始化所调用的同一 Host service，但未实际点击/截图欢迎页。
- **Python Node 不可用/恢复**：本次范围内验证了 `auto_start=false` 不连接错误服务；
  Node 进程故障后的自动恢复仍由既有 `MemoryProcessHost` 测试覆盖，未在真实 host
  中停止 Node 做破坏性验证（避免动用户进程）。
- **旧 settings.yaml 的完整迁移**：实现了文件层兼容，但旧格式的实际用户文件未在本机
  样本中复现；迁移步骤见用户说明。
- 未修改任何 DSH 安装目录文件；修复代码与文档已提交至 GitHub
  `canyueY/dsh-mnemosyne` 的 `feat/shallow-memory-lifecycle` 分支，未发布 npm。

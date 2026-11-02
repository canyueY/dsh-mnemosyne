# Mnemosyne 两个插件的回归与开销测试

日期：2026-11-03（周二 01:00 左右，DeepSeek 空闲时段）  
数据源：桌宠现有 Memory Bank 的**只读备份**（源库未改动）

| 项 | 值 |
| --- | --- |
| 源 Bank | `%LOCALAPPDATA%\Mnemosyne Memory Bank` |
| 快照 | SQLite online backup（含 WAL），557.9 MB |
| 记录量 | 59,303 memories / 41,883 observations |
| 原库 schema | 18 个 migrations |
| 测试对象 A | `dsh-mnemosyne`（新插件，core 2.0.0，11 个 `memory_*` 工具） |
| 测试对象 B | `dsh-cognitive-memory`（旧插件，0.1.0，10 个工具，无 `memory_select_answer`） |
| 查询集 | 从 memories 内容确定性抽取 30 条，`queries.json` |
| 外部 API 调用 | **0**（两个 Node 都是本地 SQLite/FTS，没有 LLM/云调用） |

## 1. 回归结果

| 指标 | 新 `dsh-mnemosyne` | 旧 `dsh-cognitive-memory` |
| --- | --- | --- |
| 首次打开（含迁移） | **3.68 s**，18→24 migrations，+12 张表 | **0.66 s**，保持 18 |
| 稳态启动 | 0.92 s | 0.66 s |
| health 可用 | 首次 3.75 s / 稳态 0.98 s | 0.69 s |
| 进程 RSS | 92.8 MB | 85.1 MB |
| memories / observations | 59,303 / 41,883 | 59,303 / 41,883 |
| DB 大小变化 | 0（584,953,856 B） | 0（同） |
| `PRAGMA quick_check` | ok | ok |
| search 平均 / p50 / p95 | **14.4 / 6.8 / 64.7 ms** | 76.9 / 77.9 / 97.0 ms |
| recall@10（源记忆是否在结果中） | **83.3%**（25/30） | 80.0%（24/30） |
| top1 命中 | **40.0%** | 26.7% |
| get 单条 | 2.0 ms | 26.3 ms |
| context 平均 / 最大（估算 tokens） | **445.5 / 593** | 511.6 / 1080 |
| 写 observation | 200，`persisted_ok=true`，+1 | 200，返回 id，+1；**无 `persisted_ok` 字段** |
| 写 propose | 200，+1 | 200，+1 |
| 30 条查询的结果集一致性 | 与旧版相同集合 24/30（80%），平均 Jaccard 0.822，top1 相同仅 5/30 | — |

### 结论

- **两个插件都能直接打开桌宠的 59k 记录并读写**，数据零丢失、零改行；新插件只是多做了 6 个迁移、新增 12 张空表。
- 新插件检索**快约 5 倍**（14.4 ms vs 76.9 ms），recall@10 与 top1 略好；旧插件内存少约 8 MB、启动快约 0.25 s。
- 新旧结果集 80% 完全相同，但**排序差异明显**（top1 只有 16.7% 相同）；如果下游依赖“旧版 Top1”，升级后需要重新校准。
- 旧插件对 24-migration 的新库**仍可读写**，但它不认识新增表；长期不要两边同时跑同一 Bank。
- 旧版 observation 响应缺 `persisted_ok`，是客户端契约差异；新插件工具不受影响，但旧客户端若依赖该字段会误判。

## 2. 开销（人民币）

两个插件的 Node 本体**不调用任何付费 API**（源码里没有 OpenAI/DeepSeek/httpx 外部请求），
所以“跑插件”本身的 API 费用是 **¥0**。会产生人民币成本的只有：把 `memory_context`
返回的记忆片段作为模型输入时消耗的 input tokens。

价格口径（DeepSeek 官方，2026-11 抓取）：`deepseek-flash` 空闲时段
**输入缓存未命中 ¥1 / 百万 tokens，缓存命中 ¥0.02 / 百万，输出 ¥4 / 百万**；
高峰时段（工作日 9–12、14–18）全部翻倍。

| 每查询记忆上下文 | 新插件 445.5 tokens | 旧插件 511.6 tokens |
| --- | --- | --- |
| 单次（缓存未命中，空闲） | ¥0.000446 | ¥0.000512 |
| 1,000 次 | ¥0.4455 | ¥0.5116 |
| 10,000 次 | ¥4.455 | ¥5.116 |
| 100,000 次 | ¥44.55 | ¥51.16 |
| 1,000 次（缓存命中，空闲） | ¥0.0089 | ¥0.0102 |
| 高峰时段 | ×2 | ×2 |

- 新插件每次少注入约 66 tokens，**约省 13%** 的记忆上下文输入费（1 万次约省 ¥0.66，空闲未命中口径）。
- 本次回归测试脚本本身 **0 次模型调用 = ¥0**；我在对话里编排测试所消耗的 token 属于 DSH 会话费用，不在这张表内（可在 DSH「Token 用量」看）。
- 首次迁移多花约 2.8 s，是一次性本地开销，不产生 API 费用。

## 3. 复现

```powershell
$py = "$env:LOCALAPPDATA\Mnemosyne DSH Node\venv\Scripts\python.exe"
cd "D:\HuaweiMoveData\Users\残月Y\Desktop\桌宠\Mnemosyne项目集\bench"

& $py prepare_banks.py          # 源 Bank 只读备份 + 两份干净副本
& $py gen_queries.py            # 30 条查询

& $py run_bench.py --label new-first --core "..\projects\dsh-mnemosyne\service\src" `
  --bank .\bank-new --queries .\queries.json --out .\result-new-first.json
& $py run_bench.py --label new-steady --core "..\projects\dsh-mnemosyne\service\src" `
  --bank .\bank-new --queries .\queries.json --out .\result-new-steady.json
& $py run_bench.py --label old-steady --core "..\plugins\dsh-cognitive-memory\service\src" `
  --bank .\bank-old --queries .\queries.json --out .\result-old-steady.json
```

## 4. 限制与说明

- 30 条查询由记忆正文启发式抽取，recall@10 表示“能否召回源记忆”，不是人工标注的检索集；
  更严格的评测应使用 `engineering/mnemosyne-retrieval-quality` 的人工/合成查询集。
- token 数是按“1 token ≈ 1.6 个中英混合字符”估算，未使用 DeepSeek 精确 tokenizer；
  数量级与相对差异可用，绝对金额会有 ±10–20% 偏差。
- 旧插件的 **DSH 适配层**用本地 stub `defineTool` 验证过（10 个工具、search/context 可用），
  没有装进当前 `desktop` profile（会与新插件 `memory_*` 工具重名）；新插件是真实安装并 live 验证过的。
- 写测试后，59k 大库的 `queue_worker` 探针可能 PENDING 一段时间，`/health` 会返回 `ok:false`；
  本报告的数字来自干净副本，不受写残留影响。插件启动只看 HTTP 200，不会被该探针卡住。
- 价格可能调整，以 [DeepSeek 官方价格页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/) 为准。

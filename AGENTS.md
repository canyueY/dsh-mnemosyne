# Mnemosyne 与桌宠同步约束

修改本项目配置、API 或记忆生命周期时，同时更新宿主桌宠桥接和对应测试。
本项目的物理位置为 `Mnemosyne项目集/projects/dsh-mnemosyne`；`桌宠/dsh-mnemosyne` 是兼容入口。

浅层策略唯一源：`service/src/cognitive_memory/resources/shallow-memory.json`。
桌宠启动 Node 时通过 `MNEMOSYNE_SHALLOW_POLICY` 传同一文件，不能复制一套阈值。
默认 0–80 天临时、80–120 天提示词、120–200 天印象，每 24 小时提取关键词/印象词。
外部感知立即进入可删除的暂定长期并形成待验证印象/认知；正式长期须在 80–200 天经既有证据筛选。
改动必须验证 Node 与桌宠同步；只使用临时记忆库，版本维持 2.0.0。

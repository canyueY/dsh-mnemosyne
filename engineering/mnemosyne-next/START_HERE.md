# Mnemosyne 工程执行包

用途：把综合改进方案交给 Cursor Composer 2.5 Fast，以小任务逐步完成代码、接入和验证。本包是用户请求的工程规格，不改变 Cursor 本身的系统指令或权限。

## 开始执行

1. 在 Cursor 打开 dsh-mnemosyne 仓库根目录。
2. 粘贴 COMPOSER_PROMPT.md 正文，或引用仓库根目录 COMPOSER_HANDOFF.md。
3. 按 TASK_INDEX.json 的 execution_order 执行 E00—E15。先核对现有实现和运行基线，每轮只读当前任务、必要契约和相关代码，不要求通读所有背景。
4. 实现、测试、任务报告、进度更新全部完成后继续下一任务；中断后从 PROGRESS.json 和实际代码恢复。

## 文件用途

- COMPOSER_PROMPT.md：可直接粘贴的执行指令。
- CONTRACTS.md：不可破坏的边界、最小数据契约与拟新增 API。
- TASK_INDEX.json、tasks/E00.md—E15.md：依赖、代码落点、步骤、回归及完成标准。
- ACCEPTANCE_CASES.json：50 个公开反例，含 given、when、expected、测试定位建议。
- VERIFICATION.md、tools/run_checks.ps1：真实测试命令、结果记录和验证入口。
- PROGRESS.json：初始状态全部 pending，执行者维护；不是已完成报告。
- REFERENCE_PLAN.md：综合方案原始快照，仅在需要理论取舍时查阅。
- SOURCE_MANIFEST.json：11 份材料和方案快照的哈希；不要求读取原电脑路径才能实施。
- tools/validate_handoff.py：工程包结构、依赖及引用自检，不验证业务代码已完成。

## 默认范围

实现八条主线中确定性 P0/P1：可信来源、派生失效、当前答案、上下文、恢复、成本、健康、多路召回、巩固、消费一致性、执行反馈、有限规则学习、渐进迁移和首个内容领域。

首个领域固定为 UTF-8 长文/书籍 TXT；执行闭环先用隔离 fake executor。保持现有 Node 2.0 API 和治理兼容，不擅自改版本号。神经、生理、一般因果推断、真实平台操作与联邦不在默认实现范围，详见 DEFERRED.md。

禁止新增第二个认知服务/数据库、强制安装九个外部项目或直连核心 SQLite。保留原有未提交修改；不自动提交、推送、部署或对生产 Bank 做测试。

## 交付门槛

不是只新增 schema、工具声明和测试 mock：功能必须接入实际 Service/API/宿主调用链并通过相关验证。50 个公开病例不是盲测 heldout 集；合成测试不能证明真实用户效果。最终测试、构建、隔离安装和病例对账分别报告。

# 对 2026-10-29 Codex 第三轮评审与桌面演练的处理

> 历史归档：保留当时的版本、结论与行号；文件引用已按当前目录更新，私人标识和本机路径已脱敏。本文不代表当前实现或验收状态，当前状态见项目的 `docs/status.md`。

评审报告：[2026-10-29-codex.md](2026-10-29-codex.md)；演练报告：[2026-10-29-rehearsal.md](2026-10-29-rehearsal.md)。G01–G03 均已复现并修复，S01 已采纳；演练问题 1 与 G01 为同一缺陷，问题 2 按本人决定（方案 B）修改，问题 3–6 一并处理。测试由 51 个增至 56 个，全部通过；评审附带的复现脚本修复后全部被拦截。

| 编号 | 处理 | 位置 | 测试 |
|---|---|---|---|
| G01 / 演练 1 组合决定提前完成 | 外发动作带 `step` 分别跟踪；`decision` 新增 `partial` 状态，返回 `steps`、`localActions`；**只有最后写入的带 `decision` 的 `unpaused` 代表整个事项完成**，`released` 等只是前置步骤，任意一次 `sent` 也不再代表全部完成。技能规定本地动作在前、外发在后，逐步查 `--step` 状态，已完成的跳过 | `scripts/ledger.py` `cmd_decision`、`step_state`；`skills/followup/SKILL.md` 第 2.2 节 | `test_multi_action_decision_not_done_until_all_steps`（交还 + 回复 + 发简历，含交还后中断恢复）、`test_decision_sent_but_not_completed_resumes`、`test_decision_state`、`test_step_requires_decision` |
| G02 job_closed 经 declined → reopened 被解除 | 关闭原因叠加记录在 `closedBy`；`reopened` 只移除 `declined`，`job_closed` / `rejected` / `risk` 保留；技能只对仅因 `declined` 关闭的会话重新评估新职位 | `build_threads`；followup 第 4.3 节；`shared/ledger.md` | `test_closed_reasons_not_cleared_by_declined_then_reopened`（含直接 reopened 与经中间状态两种序列） |
| G03 孤立暂停拿不到原因 | `decision` 返回 `pausedRecords`（原因、时间、平台、公司、招聘者、职位）；对账时只用这些信息补写 inbox，不臆造选项和截止时间 | `cmd_decision`；followup 第 2.1 节 | `test_decision_returns_paused_reason` |
| S01 guard 保证表述过强 | 改为「按当前租期续期，正常连续执行时缩小窗口；非原子，任何中断后须重读页面并重新 guard；严格隔离需执行器层互斥」 | `docs/design.md` 第 9 节 | — |
| 演练 2 职位层面不符停掉整家公司 | 按本人决定（方案 B）：公司层面的用工性质（人员外包、劳务派遣）、风险、对方拒绝进停止名单；职位层面（单休、薪资、职责、地点）只记 `declined` 关闭当前会话 | followup 第 4.2、4.3 节；design 第 8 节 | —（技能流程） |
| 演练 3 崩溃后旧锁挡住 60 分钟 | 写明：默认等过期；本人确认上一轮已停止，才可 `lock release --run <上一轮 runId>`，Agent 不得自行释放 | apply 第 6 节；followup 第 7 节；`shared/ledger.md` | — |
| 演练 4 本人核对路径不清 | 本人在 inbox 或对话中确认后，由 Agent 代记 `skill: user`，`reason` 引用本人原话；`uncertain` 事项的选项固定为「已发出 / 未发出」 | apply、followup、`shared/ledger.md`、`shared/notify.md` | — |
| 演练 5 文档细节 | 未登记旧会话明确先记 `paused`；决策 ID 序号规则 | followup 第 4.1 节；`shared/notify.md` | — |
| 演练 6 重复原因 | 首次联系检查同一类原因只保留最近的一条 | `first_contact_reasons` | `test_first_contact_reasons_deduplicated` |

另：对账规则改为以 ledger 为准，inbox 的「已执行」只作展示；`decision` 不是 `done` 的已决定事项无论 inbox 如何标记都继续执行（followup 第 2.1、2.2 节）。

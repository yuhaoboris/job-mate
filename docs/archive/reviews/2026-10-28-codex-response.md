# 对 2026-10-28 Codex 评审的处理

> 历史归档：保留当时的版本、结论与行号；文件引用已按当前目录更新，私人标识和本机路径已脱敏。本文不代表当前实现或验收状态，当前状态见项目的 `docs/status.md`。

评审报告：[2026-10-28-codex.md](2026-10-28-codex.md)。14 项问题均已核实成立，全部修复。报告末尾的复现脚本在修复后全部被拦截。

| 编号 | 处理 | 位置 | 测试 |
|---|---|---|---|
| R01 已决定事项中断后重复外发 | 外发事件可带 `decision`；新增 `ledger decision --id` 查执行状态（未开始 / 进行中 / 已发出 / 失败），已发出只补记、进行中只核对；所有发送入口改走统一检查 | `scripts/ledger.py` `cmd_decision`；`skills/followup/SKILL.md` 第 2、5.0 节 | `test_decision_state` |
| R02 「L1 也执行」覆盖本轮禁发 | 本轮禁发列为最高优先级；`lock acquire --no-send` 后 `ledger` 拒绝写入任何新的外发 | `require_live_lock`；followup 第 1、2 节 | `test_no_send_lock_rejects_pending` |
| R03 并发接管过期锁双方成功 | 锁的读取、接管、续期、释放都在从不删除的协调文件锁内完成；锁文件原子替换 | `lock_mutex`、`cmd_lock` | `test_concurrent_takeover_only_one_wins`（两个进程同时接管，重复 5 轮） |
| R04 失锁的旧任务仍能外发 | 新增 `lock renew`；动作 `pending` 必须由未过期的持锁者在锁内写入，并自动续期 | `require_live_lock`、`cmd_append` | `test_pending_requires_live_lock`、`test_pending_renews_lock` |
| R05 状态转换未校验 | 校验转换表与不可变字段；`uncertain` / `failed_no_send` 只能由本人（`skill: user`）解除；额度按「写过 pending」计 | `check_transition`、`cmd_quota` | `test_invalid_transitions_rejected`、`test_manual_review_can_clear_uncertain_but_not_sent`；原先断言错误行为的测试已改正 |
| R06 公司去重只看 greet | 首次联系去重纳入所有动作；会话内发送改用 `check --kind` | `first_contact_reasons`、`conversation_reasons` | `test_first_contact_counts_other_kinds` |
| R07 暂停决定未进入发送检查 | 新增 `paused` / `unpaused` 观察；按本人决定暂停整个会话；`check` 与 `canFollowup` 都检查 | `build_threads`、`conversation_reasons`；`shared/notify.md` | `test_conversation_check_blocks_unresolved_manual_paused` |
| R08 简历直接送达未关闭 pending | 简历每个结果分支都记终态：请求待同意、直接送达均记 `sent`，再按需记送达观察 | followup 第 5.2 节 | `test_resume_direct_delivery_closes_attempt` |
| R09 非外包硬条件不符未持久化 | 所有明确不符硬条件的会话都追加停止名单 | followup 第 4.3 节 | —（技能流程） |
| R10 新入站会话被当作旧会话 | 先区分新入站 / 已登记 / 未登记旧会话；新职位先评估 | followup 第 4.1、4.2 节 | —（技能流程） |
| R11 停止名单特殊字符变形 | 读写共用 JSON 双引号转义与带引号的逗号解析；写入前读回比对 | `_yaml_scalar`、`_yaml_list`、`cmd_stoplist` | `test_stoplist_roundtrip_special_values`、`test_stoplist_hand_written_quotes` |
| R12 重新设置覆盖期间新增停止项 | `setup` 写入阶段持锁并用 `workspace changed` 检测期间变化；停止名单只用 `stoplist add` / 新增的 `remove` 逐条修改；`restore` 保留备份后新增的停止项（本人决定），运行中拒绝恢复 | `skills/setup/SKILL.md` 第 3 节；`scripts/workspace.py` `cmd_restore`、`cmd_changed` | `test_restore_keeps_stop_entries_added_after_backup`、`test_restore_refused_while_running_and_changed_detects_edits`、`test_stoplist_remove_keeps_others` |
| R13 体检放过无法使用的问答与简历索引 | 有内容却识别不出条目报错；校验版本 id 唯一、`default` 引用、`file` / `use`；缺 `uploaded` 给出提示 | `scan_yaml_list`、`check_resumes` | `test_check_flags_unusable_qa_and_resume_index` |
| R14 间隔按 pending 时间算 | 按最近一次 `pending` 或 `sent` 的时间算 | `cmd_quota` | `test_quota_interval_uses_latest_send` |

## 需要本人决定的问题（本人 2026-10-28 答复）

1. **排除条件未证实时能否沟通**：正向条件（职责、地点、薪资、硬性要求）须从 JD 证实；排除条件明确命中才排除，未写明的可以打招呼，记「待沟通核实」。已写入 `skills/apply/SKILL.md` 第 4 节与 `shared/workspace.md`。
2. **待决定事项暂停范围**：暂停整个会话。
3. **恢复备份是否回滚停止项**：保留备份之后新增的停止项。

## 顺带改动

- `apply` 只筛选时也获取运行锁（`--no-send`），避免与 `followup` 同时操作浏览器。

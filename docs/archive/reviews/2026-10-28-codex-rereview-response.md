# 对 2026-10-28 Codex 复审的处理

> 历史归档：保留当时的版本、结论与行号；文件引用已按当前目录更新，私人标识和本机路径已脱敏。本文不代表当前实现或验收状态，当前状态见项目的 `docs/status.md`。

复审报告：[2026-10-28-codex-rereview.md](2026-10-28-codex-rereview.md)。7 项均已核实成立，全部修复；复审附带的复现脚本在修复后全部被拦截。测试由 42 个增至 51 个，新增的测试覆盖复审要求的「两轮运行」和「中断点」路径。

| 编号 | 处理 | 位置 | 测试 |
|---|---|---|---|
| F01 pending 之后失锁仍可点击 | 新增 `ledger guard --run --attempt`：确认仍持有有效锁、尝试仍是本轮的 `pending`，并续期。`apply`、`followup` 每次可能发出消息的点击前调用，失败即停止、不新建尝试。设计中的结论已收窄为「剩余窗口只在 guard 与紧随的点击之间，且 guard 续期使接管不会落在窗口内」 | `cmd_guard`；apply 第 5.3、5.4 节；followup 第 5.0、5.2 节；design 第 9 节 | `test_guard_requires_lock_and_pending`（pending 后锁过期被接管，旧任务和新持锁者都不能继续操作该尝试）、`test_guard_rejects_finished_attempt` |
| F02 restore 检查锁后新任务仍能启动 | `restore` 全程持有自己的运行锁；setup 自己持锁时用 `--run` 沿用，其他任何有效锁（包括别的 setup）都拒绝 | `workspace.py` `cmd_restore`；抽出 `ledger.acquire_lock` / `release_lock` | `test_restore_holds_lock_for_whole_run`（在 restore 备份入口插入 followup 加锁，被拒绝）、`test_restore_refuses_other_setup_but_reuses_own_lock` |
| F03 筛掉的新入站会话下轮绕过筛选 | 不符时记 `observe/declined`，会话关闭；只有 HR 推来另一个评估符合的职位才记 `observe/reopened`（对 `rejected` 等无效）。排除条件不符时同时加停止名单 | `build_threads`；followup 第 4.2、4.3 节 | `test_declined_inbound_stays_closed_until_reopened` |
| F04 非外发决定被导向发送 | 决定按动作类型执行（外发 / 交还会话 / 解除暂停 / 更新资料 / 继续等待），只有外发类发消息；`decision` 对非外发类以带 `decision` 的 `released` / `unpaused` / `reopened` 判定完成 | `cmd_decision`；followup 第 2.2 节；`shared/notify.md` | `test_non_send_decision_done_and_still_pausing` |
| F05 inbox 与暂停记录不一致 | `followup` 每轮开始做幂等对账：待决定但无暂停的补 `paused`，暂停但 inbox 无事项的补写事项，已完成仍暂停的补 `unpaused`。`decision` 输出 `stillPausing` 便于核对。规定先记暂停再写 inbox | followup 第 2.1 节；`shared/notify.md` | `test_decision_done_but_still_paused_is_visible` |
| F06 相同 jobId 换公司名放行 | 同平台相同 `jobId` 优先拦截，不再依赖公司名匹配 | `first_contact_reasons` | `test_same_job_id_blocks_even_with_different_company_name` |
| F07 旧批准越过后来的接管 | 按已定设计：`manual` 一律阻止，只有本人明确交还（`released`）才恢复；`--decision` 只解除对应的暂停 | `conversation_reasons`；followup 第 2.2 节；design 第 8.2 节 | `test_manual_blocks_even_with_decision` |

上轮 R01、R04、R06、R07、R09、R10、R12 的「部分解决」部分即本表 F01–F07 对应的内容。

仍需实测验证的：技能层面的流程（对账、动作类型判断、guard 的调用时机）只能通过虚构会话演练和平台实测确认，单元测试只覆盖脚本层。

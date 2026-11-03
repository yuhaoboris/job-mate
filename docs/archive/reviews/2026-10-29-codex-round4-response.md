# 对 2026-10-29 Codex 第四轮复审的处理

> 历史归档：保留当时的版本、结论与行号；文件引用已按当前目录更新，私人标识和本机路径已脱敏。本文不代表当前实现或验收状态，当前状态见项目的 `docs/status.md`。

复审报告：[2026-10-29-codex-round4.md](2026-10-29-codex-round4.md)。H01 已复现并修复；测试由 56 个增至 60 个，全部通过。

## H01 无 step 的决定外发被当成新步骤

真实工作区中尚无执行决定的记录，旧版数据只是理论情形；但同一缺口在新版中也会出现：执行决定时漏写 `step`，记录会归入 `default`，效果相同。因此两头处理：

| 处理 | 位置 | 测试 |
|---|---|---|
| **新写入**：执行决定的外发（`pending` 及独立的 `skipped` / `already_contacted`）必须带 `step`，`append` 拒绝不带的。只约束新写入，不把旧记录判为损坏行 | `cmd_append` | `test_decision_send_requires_step` |
| **旧记录**：决定未完成且存在不带 `step` 的外发时，`decision` 返回 `needs_review`（其中有未确认的为 `in_progress`），任何 `--step` 查询也返回 `needs_review`，不再返回可执行的 `not_started` | `unstepped_decision_attempts`、`cmd_decision` | `test_legacy_unstepped_sent_needs_review`、`test_legacy_unstepped_pending_is_in_progress`、`test_legacy_partial_combo_needs_review` |
| `check --decision` 增加 `decision_needs_review` 原因；`append` 拒绝为该决定新建 `pending`。本人核对确认已全部完成后，由 Agent 记带 `decision` 的 `unpaused`（`skill: user`） | `conversation_reasons`、`cmd_append`；followup 第 2.2 节 | 同上 |

旧记录用测试直接写入文件模拟（绕过 `append`），覆盖旧版单回复已发送、旧版未确认、旧版部分组合动作三种恢复情形；带 `step` 的新格式测试保留。

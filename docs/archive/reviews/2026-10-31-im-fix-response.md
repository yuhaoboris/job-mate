# IM 修复实现说明（第二轮，2026-10-31）

> 历史归档：保留当时的版本、结论与行号；文件引用已按当前目录更新，私人标识和本机路径已脱敏。本文不代表当前实现或验收状态，当前状态见项目的 `docs/status.md`。

基线：`018d8468c068ee1b5eb991f7deb4e158769622da`
Worktree：`<用户主目录>/.paseo/worktrees/2g9vgc71/im-review-20261031`
分支：`fix/im-review-20261031`
状态：第一轮五项修复 + 复审 R1/R2/R3 返工均已完成，未提交、未合并、未推送、未部署。未访问招聘平台，未调用真实飞书，未配置 launchd，未修改 job-hunt 或本机配置。全部验证使用假 IM、假 Agent 和临时工作区。

## 1. 第二轮复审问题的修复

### R1 P1：受管进程组清理

`scripts/notify.py`

- `_stop_child` 以 `start_new_session=True` 启动后形成的**整个进程组**（`pgid == proc.pid`）为清理对象，不再只等主 PID：
  - 先向组发送 SIGTERM，循环检查主进程和整个组；
  - 主进程已退出但同组后代仍存在时，宽限期后向整个组发送 SIGKILL；
  - SIGKILL 后继续等待组消失（上限 `GROUP_KILL_WAIT_SECONDS`），避免监听器认为结束后仍有残留；
  - 只对这一个受管 `pgid` 发信号，不影响其他进程组。
- 停止、超时、子 Agent 非零退出三条路径统一调用 `_stop_child`。非零退出不再只标 `failed` 就返回。
- 新增 `STOP_GRACE_SECONDS`（默认 10）与 `GROUP_KILL_WAIT_SECONDS`（5）；测试替换宽限常量加速。
- 回归：
  - `test_stop_child_kills_surviving_descendants_only_in_managed_group`：真实启动父进程和**忽略 SIGTERM 的后代**，父进程响应 SIGTERM 退出后，后代仍被清理；同时另一个独立进程组保持存活。
  - `test_listener_failed_agent_cleans_surviving_descendants`：Agent 非零退出后清理同组残留后代。
  - `test_listener_timeout_kills_agent_and_keeps_reply`：超时路径清理忽略 SIGTERM 的后代，答复仍保留。

### R2 P1：ready 生命周期闭合

`skills/followup/SKILL.md`、`shared/notify.md`、`docs/design.md`

- 明确区分三种限制：
  - **全局暂停**：临时状态，本轮不外发，但 `ready` 回复和未执行决定保留，恢复后继续；
  - **本轮显式禁发**：只作用于本轮，`ready` 保留到下一轮，不消费；
  - **结论本身「先不回 / 保持暂停」或确认无需动作**：才收尾并标 `consumed`。
- `followup` 第 2.0 节新增 `ready` 分流表：已决定未 done → 2.2 执行；已决定且 ledger done → 2.3 只补通知和状态；「待处理」已处理 → 2.3 确认收尾；全局暂停/禁发 → 保留。
- 新增第 2.3 节「收尾 ready」：
  - `ledger decision` 已 `done`：用 inbox 中保存的该结果版本最终执行结果正文补 `done` 通知（同结果版本复用键），缺失时按 ledger 事实生成并先保存，再标 `consumed`，**不向 HR 发消息、不重做步骤**；
  - 「待处理」事项本人已处理：确认 inbox 为「已处理」，用该结果版本保存的正文补一条 `done` 说明（缺失时先保存），再标 `consumed`；
  - 已决定但被 `in_progress` / `uncertain` / `failed` / `needs_review` 阻断：按 2.2 停下，确认本轮不能继续时标 `consumed` 并写 `--note`，不伪造 `unpaused`；
  - 收尾只做一次，`done` 幂等，`consumed` 后监听器不再唤起。
- 旧 `consumed` 仍不自动重入；`notify status` 的 `needsReconcile` 只提示对账。
- 用真实 `notify` / `ledger` 脚本状态做临时工作区演练（不是真实 Agent）：
  - `test_ready_global_pause_then_ledger_done_finishes_without_resend`：暂停时 ready 保留、监听器不启动；恢复后 ledger 已 done，补 `done` 通知（重复调用 `already_sent`）、标 `consumed`，之后不再唤起；
  - `test_ready_todo_is_closed_after_user_handles_it`：「待处理」事项改为「已处理」后，无外发动作，只补通知并收尾，不再唤起。

**边界**：脚本只保证 `ready` 的队列 / 状态接口、`done` 版本键与已持久化正文的复用；是否在全局暂停或本轮禁发时保留 `ready`、是否按 2.2/2.3 收尾，是技能遵循的规则，不是脚本强制。`reply-state` 仍允许调用方主动标 `consumed`，本轮没有为此新增 API 或改变授权。未用真实 Agent 演练。

### R3 P2：未确认发送复用已存正文与路由

`scripts/notify.py`

- `out` 记录新增持久化 `replyTo`，`fold` 保留它。
- `deliver` 在同一个键已有未确认尝试时：
  - 使用第一次 `pending` 记录里的原始正文发送；
  - 使用记录里的引用路由 `replyTo`；
  - 不再用第二次调用传入的正文/路由覆盖首次可能已送达的内容；
  - 返回值标记 `reusedStored`。
- 旧 `done:<ID>` 与新 `done:<ID>:<摘要>` 都走同一路径：先按规范化正文匹配版本/旧键，再复用该记录正文与路由。
- 回归：
  - `test_done_cross_call_retry_reuses_persisted_body`：第一轮正文两个空格且发送失败，第二轮一个空格；第二次 adapter 收到第一次持久化正文，记录未被覆盖。
  - `test_legacy_done_cross_call_retry_reuses_persisted_body`：旧 `done:<ID>` 键跨调用同样复用首次正文。
  - `test_unconfirmed_key_reuses_persisted_reply_route`：同一键跨调用复用首次正文和 `reply_to` 路由。

### checkSeconds 边界

`scripts/notify.py`、`shared/notify.md`

- 默认值恢复为 **30 秒**（`CHILD_CHECK_SECONDS = 30`）。
- 新增 `child_check_seconds()`：非数字、NaN/Inf 回退默认值；有限值限制在 **0.01–30 秒**。
- 回归 `test_child_check_seconds_is_bounded`：默认 30、0.05 保留、120 截到 30、非法值回退 30。
- 文档不再宣称代码不存在的校验。

## 2. 第一轮五项修复仍保留

1. P1 指令乱序/丢失：`.poll.mutex` 串行拉取/持久化/调度；指令 pending/applied 可重放；`pause.json` 原子保存已执行 IM 指令 ID；重试按源消息去重；旧指令只提示不重放；回执锁外发送。
2. P1 Agent 阻塞监听：子 Agent 运行期间按间隔继续拉取、执行指令、更新心跳，不启动第二轮。
3. P1 consumed 过早：`received → ready → consumed`，本轮补齐全局暂停/待处理/已 done 的收尾路径。
4. P2 数字答复：序号只认独立数字 token；编号找不到时保留 `received` 交技能追问；无事项才 `ignored`。
5. P2 done 结果通知：默认键含规范化正文摘要，同正文幂等、不同正文发新版本；旧 `done:<ID>` 兼容；本轮补齐跨调用正文/路由复用。

## 3. 修改文件

| 文件 | 内容 |
|---|---|
| `scripts/notify.py` | 第一轮修复 + R1 进程组清理、R3 正文/路由复用、checkSeconds 上下限 |
| `scripts/ledger.py` | `set_pause` 原子保存并保留 `imCommands` |
| `skills/followup/SKILL.md` | `ready` 分流、2.3 收尾、全局暂停与禁发的区别、待处理事项收尾 |
| `shared/notify.md` | 状态/收尾规则、正文与路由复用、监听器进程组清理、checkSeconds |
| `docs/design.md` | 第 10、13 节同步；注明真实飞书/launchd/Agent 未实测 |
| `AGENTS.md` | 现状补记第二轮返工 |
| `tests/test_notify.py` | 新增 23 项回归测试（总数 86 → 109） |

## 4. 实际执行的命令与结果

```text
python3 -m unittest discover -s tests
Ran 109 tests in 5.888s
OK
```

（连续运行 3 次均为 109 项通过；基线 86 项，第一轮后 101 项，本轮 109 项。）

```text
python3 -m py_compile scripts/notify.py scripts/ledger.py tests/test_notify.py
# 无输出
```

```text
python3 scripts/build.py --help
python3 scripts/build.py --out <临时目录>/dist
# job-wingman-apply / job-wingman-followup / job-wingman-setup 构建成功
# 临时目录已删除，未安装到真实工作区
```

`git diff --check` 无输出。

## 5. 未验证事项

- 未在真实飞书验证：幂等窗口、引用回执、`parent_id`、分页、同秒时间戳。
- 未在 launchd 下验证：信号处理、子进程组清理、心跳、睡眠抑制。
- 未用真实 Agent 验证 `followup` 2.2/2.3 的执行；R2 的演练使用真实脚本状态模拟流程，属于脚本/文档约束验证，不代表 Agent 行为已验证。
- 未做真实平台外发；「人工接管、先不回、uncertain/failed/needs_review 不绕过」依赖现有 `ledger check/guard` 与技能文档，测试覆盖脚本层。
- 进程组清理回归使用本机真实子进程（含忽略 SIGTERM 的后代），但不是 launchd 下的真实 Agent 工具树。

## 6. git diff --stat

```text
 AGENTS.md                |   1 +
 docs/design.md           |   8 +-
 scripts/ledger.py        |  15 +-
 scripts/notify.py        | 408 +++++++++++++++++++++++++---------
 shared/notify.md         |  42 +++-
 skills/followup/SKILL.md |  37 ++-
 tests/test_notify.py     | 561 ++++++++++++++++++++++++++++++++++++++++++++++-
 7 files changed, 946 insertions(+), 126 deletions(-)
```

另：`docs/archive/reviews/2026-10-31-im-fix-response.md`（本文件）与主 Agent 的 `docs/archive/reviews/2026-10-31-im-fix-rereview.md` 为未跟踪文件；未修改复审报告原文。

## 7. 复审后的文档定点修正（2026-10-31）

本轮只改文档，不改运行时代码或测试：

- `skills/followup/SKILL.md`：`done` 正文改为“**同一结果版本**稳定”，必须包含实际执行结果（实际发送文字，或失败 / 结果不明 / 暂停原因），不能拿用户决定原文代替；首次发送前把该结果版本正文保存到 inbox；同一版本重试 / 补发读取保存的正文（已有发送尝试以 `notify` 持久化原文为准）；结果改变（如 `uncertain` → `done`）时记录新版本和新正文，由摘要键区分。2.3 的 ledger done 收尾同样使用已存最终结果正文，缺失时按 ledger 事实生成并先保存。
- `shared/notify.md`：同步“按结果版本去重”而非“同一事项固定正文”的说法，并说明 `done` 正文来源与保存位置。
- 本文件：修正 R2 边界表述，明确 `reply-state` 仍允许调用方主动 `consumed`；暂停时保留 `ready` 是技能规则，不是脚本强制接口。

验证：仅执行 `git diff --check` 与三技能临时构建。

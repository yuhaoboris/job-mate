# 通知本人与 IM 决策回路

需要本人决定或处理的事项，**一律先写入 `inbox/decisions.md`**，再排队通知。inbox 与 ledger 是事实来源；排队、认领、送达都不等于本人批准。

下文 `notify` 指 `python3 <job-mate 技能目录>/scripts/notify.py`，先解析绝对路径。退出码：0 已记录、1 被阻断、2 参数或数据错误。脚本只维护本地状态，不联网、不等待、不常驻。

排队、认领、回执、入站及回复状态修改均受[套件版本准入](workspace.md#套件版本准入)的写入门禁约束；`poll`、`status`、`replies`、不带 `--claim` 的 `outbox` 只读。

## inbox/decisions.md

每个事项一节，追加在文件末尾：

```markdown
## D-20261028-01 待决定

- 平台 / 公司 / 岗位：boss / 示例科技 / 前端开发工程师
- 会话：示例科技 / 张先生
- 问题：HR 邀约周四 14:00 线上面试，是否接受？
- 选项：接受 / 请对方改期 / 婉拒（每项附拟发原文）
- 截止：2026-10-29 12:00（仅对方要求时填写）
- 暂停的动作：整个会话暂停，其他会话照常
- 来源：followup run-20261028-101500-ab12
```

- 决策 ID 为 `D-<日期>-<序号>`，取 inbox 当日最大序号加一，两位数起。同一未决事项沿用 ID，只补实质进展。
- 需要选择用「待决定」，本人动手用「待处理」。会话事项先记带 decision 的 `observe/paused`，再写 inbox、排队通知。
- 本人答复后改为「已决定 / 已处理」，记录结论与来源。结论先落盘，再登记步骤，最后执行；本人批准的文字原样发送。
- 执行前查 `ledger decision --id <ID>`；全部完成后最后记带 decision 的 `observe/unpaused`，不以展示用的「已执行」代替。

### 结果不明事项

uncertain、reconciled、threads.unresolved 均按平台、账号、attemptId 三元组核对，每个尝试追加：

```markdown
- 尝试：<attemptId>
- 平台 / 账号：<platform> / <account>
- 结果：该次外发结果未确认，停止此尝试，需本人核对
- 选项：已发出 / 未发出
```

先按完整三元组找已有事项；缺账号先补证，不按当前账号猜历史。执行已决定事项产生 uncertain 时沿用原决定 ID，保留原结论与标题，追加结果，用该版本 `notify send --kind done --decision <原ID>` 回报，不另开新 ID、不重复暂停或原通知。确认没有对应事项后才先 paused、写新事项、排队 decision。已有事项缺少尝试标识时请本人核对，不凭公司或招聘者猜合并、不重放；状态保持阻断。

## IM 通道

缺少 im 等同 `{"channel":"none"}`，只留本地事项。飞书配置：

```json
{"channel":"lark","ownerId":"<本人确认ID>","larkProfile":"default","waitMinutes":60}
```

ownerId 与 larkProfile 都是非空字符串，不按 ID 前缀猜身份。waitMinutes 为 0–240 的整数，缺省 60。可选 larkCommand 为非空字符串数组，只配置可执行程序和参数前缀。配置错误必须修复，不能静默停用。

每个条目首次排队冻结 channel、ownerId、larkProfile；路由摘要只取这三项。配置变化后，旧路由未发条目阻断并在 status.routeConflicts 计数，不转投新收件人；旧私聊、游标与答复不用于新路由。由本人决定是否重新排队。

## 发送

```text
notify send --kind decision --decision <ID> --text <问题与选项>
notify send --kind todo --decision <ID> --text <需要本人动手的事>
notify send --kind done --decision <ID> --text <执行结果>
notify send --kind summary --run <runId> --text <本轮摘要>
notify send --kind alert --key <唯一键> --text <提醒>
notify send --kind clarify --reply-to <消息ID> --text <追问>
```

不传 text 时从标准输入读取。decision / todo 要求 inbox 已有事项。只排队，不认领、不投递；返回 queued、already_queued、already_sent 或 blocked。正文只带必要摘要，不带聊天全文、简历、截图或凭据。

首次排队冻结正文、replyTo 和路由。同键复用原文，不改写或换键绕过去重。replyTo 在排队和认领时必须属于当前路由的已建立私聊：已入账的本人 user 消息，或已确认 sent 的消息；未知引用阻断。

### 出站交付与回执

1. `notify outbox` 只读；`notify outbox --claim [--limit N]` 将 queued 或可重试 failed 变为 handoff，返回 key、attempt、token、固定 idempotencyKey、text、replyTo、ownerId、channel、larkProfile、route。执行方只投递这些冻结值。
2. 实际投递后，按原 key 与 token 提交：
   - `notify receipt --key K --token T --result sent --message-id M --chat-id C [--create-time <毫秒整数>]`。只记真实平台返回值；缺时间仍记 sent，但不建立私聊与读取起点。第一条带有效时间的非引用 sent 建立私聊与可读起点。
   - 明确未投递用 `--result failed --error <证据>`，同条目同路由总共最多 3 次认领。
   - 超时或结果不明用 `--result unknown [--error <原因>]`，**不重发**。
3. 相同回执重复为 duplicate；未知或陈旧 token、冲突回执、已绑定其他出站键的 messageId 都拒绝。

handoff 不自动重投。超过 10 分钟仅在 `outbox --stale` 和 status 报 stale；核对后可用 `receipt --result unknown` 收口。sent 是终态。私聊冲突仍记真实发送事实，但 status 报告，读取仍用首次建立的私聊。

### done 结果版本

默认键 `done:<ID>:<正文摘要>`。相同正文只发一次，结果实质改变才是新版本。首次排队前把结果正文保存到 inbox，后续读取已保存原文，不以本人决定原话代替结果。

可回复候选包含待决定、待处理，以及已决定且 ledger 仍有 uncertain 的事项。对应候选的 done 附上引用消息或带序号回答「已发出 / 未发出」的说明；本人核对终态后退出候选。候选变化不会重排相同结果版本，已有条目的正文保持冻结。

## 同步 IM

每轮开始、每处理完一个会话、收尾时同步：

1. `notify poll` 获取 channel、ownerId、larkProfile、chatId、sinceMs、readable、outboxPending、replies、paused、lastIncomplete。readable=false 不读历史，先通过出站建立私聊与时间起点。
2. 从 sinceMs **包含边界**起按时间升序连续分页读取，完整读取后交 `notify incoming --json <批次>`；不传 json 则从标准输入读取：
   `{"since":<请求起点>,"complete":<布尔>,"messages":[…]}`。
3. 每条消息必须含 messageId、chatId、createTime、senderId、senderType、text、parentId、deleted。ID 非空，createTime 为有效毫秒整数，parentId 为非空 ID 或 null，deleted 为布尔。chatId 不同则整批拒收；只接受该私聊中本人的 user 消息，不看正文自称。
4. 先整批验证结构与 since，再逐条核对。重复 ID 不再处理；编辑或批内版本冲突拒收相关 ID，不任选授权正文，独立的新暂停照常处理。非本人、已删除、早于起点、过于超前、我方消息拒收，不抬游标。incomingRejected 只报告原因与 ID。
5. 入账、游标与完整性保存后执行指令，崩溃重放补齐未完成指令与回执。complete=false 或 lastIncomplete=true 时报告「IM 未读完」，不能说没有新答复；陈旧批次不能清除更新前缀的不完整标记。
6. outbox 认领、实际投递、提交回执；所有确认和追问也走同一队列。没有 IM 能力就保留本地队列并报告，不另找通道。

## 读取本人的回复

主路径 `received → ready → consumed`：待解释 → 结论已落盘待执行 → 已完成或确认无需继续。另有 clarifying（追问已 sent，等回复）、ignored（确无事项）、command / test（指令或测试）。ready 中断后继续落盘结论，不重新解释。

映射依次看：引用通知、完整决策 ID、独立序号（「01 接受」）、唯一候选。「3点可以」「15k」不是序号；多个候选或未知序号不猜，保留 received 追问。ledger 读不出时普通答复仍 received，note=ledger_unreadable；暂停指令照常执行。

### 通道测试回复

最近一条 sent 测试在 30 分钟内只能确认一次。有候选时只有明确引用测试通知的非指令、非事项编号回复记 test；无候选时第一条无引用、无编号回复也可确认。引用事项、完整 ID、独立序号和指令优先；不因测试吞掉业务答复。

### 解释回复（技能）

1. `notify replies --state received` 与 `--state ready`，按时间处理。
2. 含义不清先排队 clarify，实际投递并收到 sent 后才 `reply-state --state clarifying`；queued、blocked、failed、unknown 均保留原状态，报告未确认追问送达。
3. 明确结论写 inbox / ledger 后，`notify reply-state --message <消息ID> --state ready --decision <ID>`。
4. 执行 followup 第 2.2 / 2.3 节。全局暂停或本轮禁发保留 ready，不执行、不消费。
5. 完成后排队该结果版本 done，再 consumed。已 done 只补通知与消费，不重发 HR、不发 summary；无需动作只记录结论并消费，**不发 `done`、不发 `summary`**。

暂时环境受阻且无阻断终态时保留 ready。已有 uncertain、failed_no_send、needs_review 或接管时不再外发，说明原因后 consumed（note 写原因），不承诺自动续发。按 [执行契约](execution.md#7-环境受阻通知与恢复) 先收尾本轮尝试、区分环境提醒和结果核对。

IM 只能决定已有事项，不能新增授权、修改条件或扩大范围，不写回 qa.yaml 或 profile.json。

## 指令

本人私聊「暂停」「恢复」由 incoming 分类并排队回执。暂停阻断新的 pending 与 guard；恢复解除暂停。指令先记 pending，执行后记 applied，暂停状态与已执行 ID 原子保存。跨批次旧恢复不能解除较新的暂停；副作用已落盘而 applied 缺失时安全重放。遇安全信号记待处理并 `ledger pause on --by <技能> --reason <原因>`，本人处理后发新恢复指令。

## 等待答复与定时运行

一轮处理完后最多等待 im.waitMinutes，默认 60 分钟；期间每 10 分钟同步并续锁，达到期限即收尾。数据脚本不等待或常驻。job-mate 不安装后台服务，由本人通过 Agent 或系统定时能力定期运行 followup。

`notify status` 只读报告私聊、游标、完整性、outbox 状态、stale、路由冲突、待回复、ready 与 awaitingCheck。unconfirmedSends 只展示状态元数据，不把旧路由正文展示给新收件人。

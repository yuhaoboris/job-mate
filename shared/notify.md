# 通知本人与 IM 决策回路

需要本人决定或处理的事项，**一律先写入 `inbox/decisions.md`**，再排队通知。inbox 与 ledger 是事实来源；排队、认领、送达都不等于本人批准。

下文 `notify` 指 `python3 <job-mate 技能目录>/scripts/notify.py`，先解析绝对路径。退出码：0 已记录、1 被阻断、2 参数或数据错误。notify 只维护本地状态；飞书收发由随包 `scripts/im_lark.py sync` 完成，不等待、不常驻。配置、身份核验与工具边界见 [飞书适配器](im/lark.md)。Windows 上 `python3` 换为 `py -3`。

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

ownerId 与 larkProfile 都是非空字符串，不按 ID 前缀猜身份。waitMinutes 为空闲窗口，整数 0–60，缺省 60；0 关闭监听。可选 larkCommand 为非空字符串数组，只配置可执行程序和参数前缀。配置错误必须修复，不能静默停用。

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

排队后运行 `python3 <技能目录>/scripts/im_lark.py --workspace <工作区> sync`；只想投递用 `sync --send-only`。适配器函数级调用 notify 的认领与回执状态机，不要求技能手工组装交付协议：

- queued 或可重试 failed 才能认领为 handoff；只使用冻结 channel、ownerId、larkProfile、text、replyTo、原 idempotencyKey，不用当前配置重建目标。
- 真实 messageId/chatId 才记 sent。发送 shortcut 的日期文字不是可信毫秒；适配器原生补查失败仍记不带时间的 sent，不改 unknown、不重发。第一条带可信时间的非引用 sent 才建立私聊与可读起点。
- 只有实测未投递拒绝才 failed，最早下一轮重试，同条目同路由最多共 3 次；实际发送超时或结果不明为 unknown，**不重发**。
- 回执绑定原 key/token；相同回执为 duplicate，陈旧 token、冲突回执或已绑定其他出站键的 messageId 拒绝。

handoff 无回执不自动重投。超过 10 分钟只在只读 `notify outbox --stale` 与 status 报 stale，请本人核对后按原 key/token 收口，不换键补投。sent 是终态。私聊冲突仍记真实发送事实，status 报告，读取仍用首次建立的私聊。

### done 结果版本

默认键 `done:<ID>:<正文摘要>`。相同正文只发一次，结果实质改变才是新版本。首次排队前把结果正文保存到 inbox，后续读取已保存原文，不以本人决定原话代替结果。

可回复候选包含待决定、待处理，以及已决定且 ledger 仍有 uncertain 的事项。对应候选的 done 附上引用消息或带序号回答「已发出 / 未发出」的说明；本人核对终态后退出候选。候选变化不会重排相同结果版本，已有条目的正文保持冻结。

## 同步 IM

每轮开始（入口对账后）、每处理完一个会话、收尾时运行 `im_lark.py sync`。只有读取需求用 `sync --fetch-only`；它不发送，确认仍 queued。`notify poll` 只是本地计划，不代表读过远端。

1. 适配器按当前 poll 路由读取；readable=false 不读历史，先通过出站建立可信私聊和时间起点。none 跳过工具；所有 sync 模式仍须通过 write 准入，恢复锁下不执行。
2. 原生毫秒、固定窗口与升序分页由 [飞书适配器](im/lark.md#读取边界) 校验；规范化 messageId、chatId、createTime、senderId、senderType、text、parentId、deleted 后整批交 incoming。首次请求前持久化扫描意图；20 页是单次预算，提交已验证前缀 complete:false 后保存 checkpoint，下次恢复同窗 token。跨 chat、坏字段、缺/重复 token、乱序或冲突整批拒收，最多新增意图，不推进业务游标、checkpoint 或完整水位。
3. 本地状态机再次验证结构与 since，只接受该私聊本人的 user 消息。重复 ID 去重；编辑或版本冲突不任选正文，独立的新暂停照常处理。非本人、已删除、起点前、过于超前、我方消息拒收，不抬游标；incomingRejected 只报告原因与 ID。
4. 先完成 incoming 的入账、可重放指令与确认，再写 checkpoint；崩溃重读旧页，业务 ID 去重。扫描记录版本、路由/私聊、边界和转移损坏时拒绝猜进度，非本人正文仅存摘要。终页成功后才保存排他上界 scanThroughMs 并关闭扫描；poll.sinceMs 取首次可信时间、businessCursorMs（仅接受本人推进）与经完整证据验证的 scanThroughMs 三者最大值，下一窗仍重叠一秒。普通 incoming 仍拒绝 since 超过 poll.sinceMs。
5. 未完成扫描使 poll.lastIncomplete 为 true；token 失败保留 checkpoint，不猜未采样错误码、不跳现在。sync.incomplete 或 lastIncomplete 为 true 时报告「IM 未读完」，不能说没有新答复；陈旧批次不能清除更新前缀的不完整标记。
6. 通知、确认和追问统一由 sync 投递同一队列。没有 IM 能力时保留 inbox、队列与回复状态并报告，不另找通道。queued、handoff、failed、unknown、blocked 都不是 sent；退出码 0 也不是送达或读完证明。

## 读取本人的回复

主路径 `received → ready → consumed`：待解释 → 结论已落盘待执行 → 已完成或确认无需继续。另有 clarifying（追问已 sent，等回复）、ignored（确无事项）、command / test（指令或测试）。ready 中断后继续落盘结论，不重新解释。

映射依次看：引用通知、完整决策 ID、独立序号（「01 接受」）、唯一候选。「3点可以」「15k」不是序号；多个候选或未知序号不猜，保留 received 追问。ledger 读不出时普通答复仍 received，note=ledger_unreadable；暂停指令照常执行。

### 通道测试回复

最近一条 sent 测试在 30 分钟内只能确认一次。有候选时只有明确引用测试通知的非指令、非事项编号回复记 test；无候选时第一条无引用、无编号回复也可确认。引用事项、完整 ID、独立序号和指令优先；不因测试吞掉业务答复。

### 解释回复（技能）

1. `notify replies --state received` 与 `--state ready`，按时间处理。
2. 含义不清先排队 clarify，运行 `im_lark.py sync`，收到 sent 后才 `reply-state --state clarifying`；queued、blocked、failed、unknown 均保留原状态，报告未确认追问送达。
3. 明确结论写 inbox / ledger 后，`notify reply-state --message <消息ID> --state ready --decision <ID>`。
4. 执行 followup 的决定或只收尾 ready 分支。全局暂停或本轮禁发保留 ready，不执行、不消费。
5. 完成后排队该结果版本 done，再 consumed。已 done 只补通知与消费，不重发 HR、不发 summary；无需动作只记录结论并消费，**不发 `done`、不发 `summary`**。

暂时环境受阻且无阻断终态时保留 ready。已有 uncertain、failed_no_send、needs_review 或接管时不再外发，说明原因后 consumed（note 写原因），不承诺自动续发。按 [执行契约](execution.md#7-环境受阻通知与恢复) 先收尾本轮尝试、区分环境提醒和结果核对。

IM 只能决定已有事项，不能新增授权、修改条件或扩大范围，不写回 qa.yaml 或 profile.json。

## 指令

本人私聊「暂停」「恢复」由 incoming 分类并排队回执。暂停阻断新的 pending 与 guard；恢复解除暂停。指令先记 pending，执行后记 applied，暂停状态与已执行 ID 原子保存。跨批次旧恢复不能解除较新的暂停；副作用已落盘而 applied 缺失时安全重放。遇安全信号记待处理并 `ledger pause on --by <技能> --reason <原因>`，本人处理后发新恢复指令。

## 等待本人回复

一轮处理完后最多等待 im.waitMinutes，默认 60 分钟；期间每 10 分钟运行 `im_lark.py sync` 并续锁，达到期限即收尾。数据脚本不等待或常驻。job-mate 不安装后台服务，由本人通过 Agent 或系统定时能力定期运行 followup。

`notify status` 只读报告私聊、游标、完整性、outbox 状态、stale、路由冲突、待回复、ready 与 awaitingCheck。unconfirmedSends 只展示状态元数据，不把旧路由正文展示给新收件人。

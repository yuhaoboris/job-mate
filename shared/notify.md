# 通知本人与 IM 决策回路

需要本人决定或处理的事项，**一律先写入 `inbox/decisions.md`**，再排队通知。inbox 与 `ledger` 是唯一事实来源，通知失败也不丢。只有本人明确答复才算决定；排队、交付 runtime、消息送达都不等于批准。

下文 `notify` 指 `python3 <plugin>/scripts/notify.py`（先解析脚本绝对路径）。输出 JSON；退出码 `0` 已记录，`1` 被阻断、需汇报，`2` 错误。核心只读写本地状态；IM 收发、等待、调度、单实例与进程收尾由当前 runtime 负责。核心不加载具体通道适配器。

## inbox/decisions.md

每个事项一节，追加在文件末尾：

| 标题 | 用途 | 本人处理后 |
|---|---|---|
| `## D-… 待决定` | 面试邀约、薪资、结果不明外发等，需要本人选择 | 改为「已决定」，写明结论 |
| `## D-… 待处理` | 安全校验、重新登录、上传附件等，需要本人动手 | 改为「已处理」 |

```markdown
## D-20261028-01 待决定

- 平台 / 公司 / 岗位：boss / 示例科技 / 前端开发工程师
- 会话：示例科技 / 张先生(人事行政专员)
- 问题：HR 邀约周四 14:00 线上面试，是否接受？
- 选项：接受 / 请对方改期 / 婉拒
- 截止：2026-10-29 12:00（对方要求）
- 暂停的动作：整个会话暂停（不回复、不跟进），其他会话照常
- 来源：followup run-20261028-101500-ab12
```

- 决策 ID 为 `D-<日期>-<序号>`，取 inbox 当日最大序号加一，两位数起。同一未决事项沿用 ID，只补实质进展。
- 需要本人决定的会话事项，**先**写 `observe/paused`（带 `decision`），**再**写 inbox；整个会话暂停回复与跟进，其他会话继续。「待处理」不涉及会话暂停。中断造成的不一致由 followup 每轮开始对账补齐。
- 本人答复后记录结论，按本地动作在前、外发在后拆步骤；只有外发动作发消息，全部完成后最后记 `observe/unpaused`。展示用的「已执行」不算完成依据；执行前查 `ledger decision --id <ID>`，避免重发或漏发。
- 旧会话批量事项按 followup 第 4.1 节：最多 10 个成员 A–J，先全部 paused（同 decision、各自 member / baseline / title、legacy 与 legacy-batch 标签），再一条 inbox、排队通知。只列编号、公司 / 岗位 / 招聘者、末条日期状态、连续条数与剩余额度，不带原文。选项「全部纳入 / 只纳入 A、C / 都不纳入（留给我自己）」，注明不放宽条件。已有未决批量事项不新开；缺 inbox 从 ledger decision.members 重建。

### 结果不明事项

运行中 uncertain、释放 / 接管的 reconciled、入口 threads.unresolved 发现的 uncertain 均按此对账。每个 attemptId 单独追加，不只写公司 / 招聘者：

```markdown
- 尝试：<attemptId>
- 结果：该次外发结果未确认，停止此尝试，需本人核对
- 选项：已发出 / 未发出
```

先按 attemptId 找已有事项。执行已决定事项产生 uncertain 时沿用原决定 ID、保留原结论与标题，追加尝试与结果；不另开新 ID、不重复暂停或原决定通知，用该版本 `notify send --kind done --decision <原ID>` 回报。其他核对事项同样复用原 ID 与键。只有确认无对应事项、无以下历史关联疑点时，才先 paused、写新事项、排队 decision 通知。

历史结果不明事项缺少 attemptId 时，不按公司 / 招聘者猜合并，不因查不到 ID 就判定无事项；不自动新开、不重放。列待对账报告，等本人核对或明确证据（如 `ledger decision` 的 attempts）唯一对应，补 ID 后才按原事项继续。期间 uncertain 与暂停等状态保持阻断，不覆盖已确认终态。

## IM 通道

配置见 [workspace.md](workspace.md)。`channel: none` 或未配置时只写 inbox；send / test 返回 skipped，outbox 为空，汇报未配置 IM。

通用配置为 `im.channel: runtime`、非空 `ownerId` 与可选 `transport` 对象；核心原样回传 transport。`transport.idempotencyWindowSeconds` 是本人经 setup 确认的 `0–86400` 整数，缺省 `0`，runtime 不能用回执临时增加保证。旧 `channel: lark` 兼容读取 `userId` 与 `larkProfile`，userId 必须以 `ou_` 开头且后缀非空，幂等窗口始终按 `0`，不改写文件。runtime 的 ownerId 仍为不绑定平台的非空标识。显式 null / 非对象配置、非法 wait / transport / 窗口须修复，不静默停用；迁移必须经本人确认。

仅接受本人在已建立私聊中的消息，身份以平台元数据为准，不采信正文自称。第一条非回复 sent 回执建立 chatId；没有带有效时间的 sent 回执前不可读取。配置换 owner / 通道 / transport 后，旧私聊、游标、待答复队列不能用于新路由；旧出站冻结路由被阻断并报告。无身份的旧历史默认不授予任何当前路由，原始记录保持不动，须按下节显式核对绑定；不能从当前配置猜原本人，不能借新 runtime 配置继承旧队列。

**其他路由待办不会消失**：poll / status / replies 统一返回 `otherRoutes: {received, ready, clarifying}`，只计数、不带正文、messageId 或其他路由标识。它包括带 route 的历史答复，以及已显式绑定的旧历史；尚未绑定的只计入 legacyUnbound，不重复计数。只要 otherRoutes 非零，trigger 返回 route_changed_pending，不能汇报成 no_replies；全局暂停、有效运行锁、未结束 launch 仍优先阻断。不能从新路由接管或绕过原有门；需要处理时只按 [其他路由待办的本地恢复](notify.md#其他路由待办的本地恢复) 执行，不通过正常 followup 同步流程恢复旧路由。

### 旧历史的显式身份绑定

poll / status 的 legacyRouteUnbound 与 `legacy_route_unbound` 提示表示旧 chat / cursor / 入站尚无可信身份。出站按键的完整归约结果识别旧协议，不能把新版 handoff / receipt 缺少重复版本字段的增量误当成旧记录；新版完整同步流程不提示迁移，也不能用新版 sent 回执冒充旧私聊绑定证据。poll / status / replies 统一返回仅含计数的 `legacyUnbound`：`replies: {received, ready, clarifying}`、`commands: {pending, applied}`、`unknownOutbox`、`bindable`（能唯一核对旧 chat 且当前是兼容配置）。不含正文或 messageId；已绑定后全部计数清零、bindable=false。绑定前 replies 不将旧 ready 当作当前本人答复，旧 received / ready 会使 trigger 返回 legacy_route_unbound，不伪装成无待办；全局暂停、未结束 launch 优先阻断。reply-state 不能绕过读取门，全历史 sent / done 去重仍生效。

仅保留旧 lark 原配置时，可由本人或可信 runtime 使用平台元数据核对**原私聊对方 ID 与应用身份**。不能靠正文自称或旧 owner 配置猜；能力不足就不迁移。确认后调用纯本地命令：

```text
notify migrate-legacy --owner-id <原本人ID> --chat-id <原私聊ID> --confirmed-owner
```

脚本只能检查 owner 精确匹配当前旧配置、旧 chat 唯一且精确匹配，不能独立验证远端身份；confirmed-owner 是本人 / 可信 runtime 的明确断言。无确认、错 owner / chat、无旧 chat 或历史 chat 冲突均拒绝，不猜。互斥内一次性保存完整 route / owner / transport / chat 与确认时间，相同绑定重复幂等；之后不允许换 owner / 通道 / 应用重绑历史。

绑定后原 chat / cursor / ready 可供核对；旧 ready 在 needsReconcile 中提示，**不计入可唤起 / 重试队列**。先按 inbox / ledger decision 的 F1 / F2 流程对账，再明确确认：

```text
notify reply-state --message <旧回复ID> --state ready --decision <原ID> --confirmed-reconciled --note <F1/F2核对结果>
```

确认保留原结论待执行时用 ready；核对已完成、仅需收尾时可用 consumed。必须保留原决定 ID 并记录 note；确认身份不是确认业务，未对账不能通过任何状态变更绕过。对账确认落盘后才允许相应后续状态 / 唤起；脚本记录本人或 Agent 的明确断言，不冒称自己验证了业务事实。

**旧格式指令即使有 pending 标记也不自动补执行**。全部在 legacyCommands 列 commandStatus（pending / applied / unknown），只作 status 汇总，请本人核对并必要时重发；已 applied 不重放、不误报无法确认，不逐条排历史提醒。旧 pending / failed 出站仍为未知、不可重试。此操作不新增业务授权。

### 其他路由待办的本地恢复

本节是临时恢复旧 route 进行本地对账的唯一流程，不是重新开启旧通道收发；不需要旧 IM 在线，既有授权不因迁移扩大，也不默认放弃已批准事项。

1. **仅由 setup 修改模式执行**，followup 不能自行修改 profile。先向本人展示新 / 旧 owner、channel、完整 transport 及待核对范围，取得明确确认。完成下一项 runtime 前置确认后，备份当前新配置，再获取 setup 运行锁；备份、加锁与写入沿用 setup 的修改规则。
2. **改配置前先暂停 runtime**：由本人 / runtime 暂停所有自动 IM 收发、outbox 投递与 trigger 调度，确认已有投递任务已停或结果已核对收口，相关实例及全部后代已停止；不能仅靠 ledger 锁证明安全。无法确认就拒绝临时恢复。此为 runtime 前置责任，核心不安装或停止任何服务；须防止后台 worker 在旧配置窗口认领原队列。
3. 恢复原 owner / channel / 完整 transport 后，**仅允许本地 `notify replies`、`notify reply-state` 与 inbox / ledger 核对**。禁止 `poll`、`incoming`、`outbox --claim`、`trigger`，禁止 runtime 收发、不运行 followup、不操作招聘平台、不重放旧指令。不因消费 ready 另排队旧 done / summary；仅本地对账，未明确决定放弃的批准事项仍保留。本人确需向原收件人发送时须另行明确决定、另走正常授权流程，不偷渡在此次恢复中。
4. 原排队通知保持原样，禁止认领 / 投递。**恢复相同 route 后脚本实际可以认领原队列，并不会自动保持 blocked**；安全来自暂停 runtime 与上述禁止调用，不能声称由脚本阻断兜底。legacy ready 不能消费时，先按 F1/F2 只核对本地已落盘结果，再以原决定 ID、`--confirmed-reconciled` 和 note 显式确认，不执行新的业务外发。
5. **完成或中断都优先恢复已备份的新配置**，不覆盖并发人工修改；发现配置变化立即停下请本人核对。恢复后读回核验，再按 [释放与对账契约](ledger.md#释放与对账契约) 处理 released / reconciled 与释放失败，仍仅本地收尾，不为旧 route 排队或投递通知。未确认恢复或收尾时 runtime 保持停用并上报；只有新配置核验通过且本人确认，才可恢复 runtime 调度。汇报实际本地操作、原批准事项与原队列等剩余事项。
6. 旧无身份历史应在迁移为 runtime **之前**，先在原 lark 配置核对可信元数据、按 [显式身份绑定](notify.md#旧历史的显式身份绑定) 本地绑定，再完成业务对账。runtime 配置下不能 migrate-legacy，但不表示迁移后永远无法绑定：已改配置者只有经本人确认、准备可验证的原身份元数据，并按本节受控恢复原 lark 配置，才可执行本地 migrate-legacy 及对账；这是身份绑定的本地特例，不开启任何 IM 收发或自动认领。无法验证或本人不愿迁移时，明确旧待办保留、会继续阻断自动唤起，可选择只手动使用，不猜归属、不自动接管。

## 发送

| 命令 | 用途 |
|---|---|
| `notify send --kind decision --decision <ID> --text <正文>` | inbox 待决定写好后，自动加标题和回复提示 |
| `notify send --kind todo --decision <ID> --text <正文>` | inbox 待处理写好后，自动加标题 |
| `notify send --kind clarify --reply-to <本人消息ID> --text <问题>` | 对应不上或含义不清时引用追问 |
| `notify send --kind done --decision <ID> --text <结果>` | 报实际发送文字或失败 / 结果不明原因 |
| `notify send --kind summary --run <runId> --text <摘要>` | 本轮实际处理会话或执行决定（含本地动作）后才发；只做收尾（消费 `ready`、补发结果通知、结论「无需动作」）的轮次不发 |
| `notify send --kind alert --key <唯一键> --text <正文>` | 异常提醒。环境提醒键 `env-blocked:<日期>:<原因代码>`，日期固定工作区时区 `YYYY-MM-DD`，例如 `env-blocked:2026-11-03:window-unavailable` |
| `notify test` | 随机 test 键，排队一条测试通知 |

以上命令**只排队，不投递、不认领、不重试**，不能汇报为「已发送」。返回 queued / already_queued / already_sent / blocked。decision / todo 要求 inbox 已有事项。正文只带最小必要信息：平台、公司 / 岗位、问题、选项、截止；不带聊天全文、简历、截图、凭据。

第一次排队冻结正文、replyTo、ownerId、channel、完整 transport、路由摘要与幂等窗口。后续同键复用原文与路由，不临时加时间或改写。replyTo 在排队和认领时都须证明属于当前 route、已建立的同一私聊：已入账本人 user 入站，或当前 route 已确认 sent 的 messageId。未知、未绑定、其他 owner / 通道 / chat 的引用一律阻断；旧 unsafe queued 也不能认领。仅有 owner 相同或正文自称不够，缺少消息级身份 / chat 元数据的旧引用也不猜。

### 出站交付与回执

1. `notify outbox` 只读；`notify outbox --claim [--limit N]` 为可交付条目写 handoff，返回 entries：`key, attempt, token, idempotencyKey, text, replyTo, ownerId, channel, transport, route`，以及下述冻结保证 / 期限字段。runtime 只投递这些冻结数据。idempotencyKey 首次 handoff 持久化，复制 / 移动工作区后仍沿用；已有未知交付但没冻结键时，不猜原路径、不重试。
2. runtime 按实际结果提交 `notify receipt --key K --token T --result …`：
   - `sent --message-id M --chat-id C [--create-time MS]`：真实送达，ID 必须非空。时间是有效非负毫秒整数；缺时间不参与首次可读边界。
   - `failed --error <明确未投递证据>`：仅通道接收前明确拒绝等有证据未投递的情况；超时不是 failed。
   - `unknown [--error <原因>]`：超时、无响应或送达不明。failed / unknown 不带消息元数据。
3. 回执严格绑定最新 handoff 的 key + token；相同回执重复是 duplicate，未知 / 陈旧 token、冲突回执拒绝，不覆盖。

handoff 只交付一次。超过 10 分钟，`outbox --stale` 与 status 只报告 stale 和 token，**不会自动解锁或重发**。runtime / 本人核对后用 receipt 收口。sent 是终态；chatId 冲突仍记实际 sent，但 status 报告，入站继续使用最早建立的私聊。

failed 仅在原路由不变且总尝试少于 3 时可再次 claim。unknown 只有冻结窗口**大于 300 秒余量**、仍在截止前、原路由与冻结幂等键不变、总尝试少于 3 时可再次 claim。旧 pending / failed 一律 legacy unknown，不因新配置增加窗口而重发。回执、提醒也走同一队列，不自动过期。

**投递截止不是仅 claim 时检查**：首次 handoff 冻结 firstHandoffMs；`idempotencyDeadlineMs = firstHandoffMs + idempotencyWindowSeconds × 1000 − 300000`，余量不足时为 null。claim 明确返回这些字段及 `requiresIdempotencyGuarantee`、`deliveryDeadlineMs`。只要此前有任何 unknown，requiresIdempotencyGuarantee=true，deliveryDeadlineMs 即固定截止；后一次 failed 也不能抹去先前未知或延长期限。首次真正未发、以及从无 unknown 的明确 failed 不依赖旧投递幂等，requiresIdempotencyGuarantee=false、deliveryDeadlineMs=null，不能被重试过期误卡。

runtime 每次实际投递前重新核对 route / 引用与冻结数据、自己仍具备原幂等保证，并检查适用的 deliveryDeadlineMs；已到截止、保证不足或等待后无法确认时不得投递，用 unknown 回执收口，不换键或谎报明确未投递。window=0 或 ≤300 秒始终不能 unknown retry。迟到的真实 sent 仍记 sent，不篡改为未发；超出适用截止的 sent / 迟到回执在 status.lateSends 标明，供本人核对。

### done 结果版本

默认键 `done:<ID>:<正文摘要>`，正文相同只发一次，结果改变（结果不明 → 已完成）才是新版本。正文写实际执行结果，不用用户决定原话代替；首次排队前保存该版本正文到 inbox，后续从保存位置读取，以已有持久化原文为准。兼容旧 `done:<ID>` 及改品牌前标题。

可回复候选包括待决定 / 待处理，以及**已决定且 ledger 派生仍有 uncertain 尝试**的事项，不解析 inbox 正文猜状态。对候选的 done 首次排队会追加「核对结果请引用本消息回复『已发出 / 未发出』，或回复『<序号> 已发出』」，参与摘要。已有相同结果版本（含旧正文、旧键）始终复用；后来候选变化不能产生第二条消息。本人在 ledger 追加核对终态后自动退出候选。

## 同步 IM

每轮开始、每处理完一个会话、收尾时由 runtime 执行：

1. `notify poll` 获取只读计划：`channel, ownerId, chatId, sinceMs, transport, readable, outboxPending, replies, paused, lastIncomplete`。readable 为 false 时先走出站建立私聊 / 时间边界，不读取历史。
2. runtime 从 sinceMs **包含边界**起按时间升序读取一段连续前缀，完整跟进分页；交给 `notify incoming --json <批次>`（不传 --json 时从 stdin 读）。批次为 `{"since":<请求起点整数>,"complete":<bool>,"messages":[…]}`。每条必须有 `messageId, chatId, createTime, senderId, senderType, text, parentId, deleted`；ID 非空，createTime 为有效毫秒整数，parentId 为非空 ID 或 null，deleted 为 bool。
3. incoming 整批先验证 schema / since，字段错误、布尔 / 负数 / 非整数 / 溢出时间、since 超游标仍整批拒收。内容冲突只拒收相关条目：已有 messageId 保留第一次内容，编辑后的 payload 计 payload_changed；新批内同一新 ID 有不同版本时，拒收该 ID 全部版本，不任选授权正文。其他合法新消息（尤其暂停）照常处理，inclusive 边界重放不会卡死。其他 / 未绑定 route 的 ID 不能靠改 payload 夺取归属，计 foreign_route。status.incomingRejected 只保留原因与相关 ID，不存编辑正文；拒收不抬游标。删除、非 user、非本人、其他私聊、早于首次可读边界、超前 5 分钟以上、我方已发送消息仍逐条拒收，只计原因。
4. 接受的消息按 ID 去重，按时间分类入账，再保存游标与完整性，再执行指令。崩溃重放会补游标、未完成指令及缺失回执；回执只排队。
5. `complete: false` 或 lastIncomplete 为 true 时，汇报「IM 未读完」，不能写成没有新答复。陈旧批次不能清除更新前缀的未读完标记；runtime 负责证明连续分页，核心无法核实远端完整性。
6. `outbox --claim` → runtime 投递 → receipt。随后按下面规则处理 received / ready。

## 读取本人的回复

| 状态 | 含义 |
|---|---|
| received | 待解释 |
| ready | 结论已写入 inbox / ledger，尚待执行 |
| consumed | 已处理完或确认无需 / 不能继续执行 |
| clarifying | 同 route / 私聊的 clarify 已有 sent 回执，等本人再回复；queued 仅保存追问，不是已追问 |
| ignored | 确无可答事项，或技能确认无需处理 |
| command / test | 指令 / 测试回复，由核心分类 |

主路径 `received → ready → consumed`。监听候选与重试队列包含 received 与 ready；ready 崩溃后继续已落盘结论，不重新解释。旧版 consumed 在 status 的 needsReconcile 中提示核对，不自动重放。

decisionHint / decisionCandidates 只是提示：引用事项通知 → 该事项；完整决策 ID → 该事项；独立序号「01 接受」匹配唯一候选 → 该事项；无序号且只有一个候选 → 该事项。多个候选不猜；「3点可以」「15k」不是序号。未知序号只要有候选就保留 received，交技能追问；确无候选才 ignored。ledger 读不出时，普通答复保持 received、note=ledger_unreadable，不猜唯一映射、不吞回复；暂停等指令仍执行，status 报待对账。

### 通道测试回复

最近一条 sent 测试在 30 分钟内只能确认一次。有候选时，只把明确引用测试通知的第一条非指令、非明确事项编号回复记 test；未引用的「收到」「接受」仍 received。无候选时，第一条无引用、无编号回复也可确认。引用事项、写完整 ID / 独立序号、指令优先。完成后同批及后续消息按正常规则分类，重放不重复确认。

### 缺少消息级元数据时

绑定身份不补造旧消息的 route / sender / chat 元数据。status / replies 对相关待办提示 `replyToBlockedReason: missing_message_metadata`、`replyRecovery: request_new_reply_with_decision_id_or_terminal`；trusted_reply_to 继续保守阻断，不换目标猜引用。

- 请本人在**同一已验证通道**重新发送带事项 ID 的新回复，或由本人在终端明确处理。新消息正常入账后，才可按普通路线引用它。
- 如需提醒，可在已绑定 route 下显式排一条唯一键的独立 alert，只请求重发，不带旧正文；它不是平台业务重试，也不能冒充 clarify 已送达。核心不自动排这类提醒。
- blocked / failed / unknown 或只有 queued / handoff 时保留原待核对状态，报告没有确认追问送达；不能因此标 clarifying、消费原待办或宣称已追问。

### 解释回复（技能）

1. `notify replies --state received` 与 `--state ready`，按时间处理。
2. 对应事项或含义不清：先排队 clarify，经 runtime 认领 / 投递 / sent 回执确认后，再标 clarifying。脚本要求引用元数据可信，且同 route / 已建立私聊中有对应 clarify 的 sent；queued、blocked、失败或结果不明均不能标成「已追问」，保留原状态并报告。会话继续暂停，不猜。
3. 明确结论写 inbox / ledger（标题已决定 / 已处理、结论与来源），再 `notify reply-state --message <消息ID> --state ready --decision <ID>`。
4. 按 followup 第 2.2 / 2.3 节执行。只有完成后才排队 done、标 consumed；全局暂停或本轮显式禁发时保留 ready。

收尾分流：

- **暂停 / 禁发**：保留 ready，不执行、不消费；恢复或下一轮继续。
- **ledger decision 已 done**：只补该最终结果版本 done 和 consumed，不重发 HR，不发 summary。
- **待处理已由本人处理**：核对 inbox 已处理，补保存的结果 done，再 consumed。
- **结论无需动作 / 先不回 / 保持暂停**：记录结论、消费 ready，**不发 `done`、不发 `summary`**。
- **运行环境受阻**：先按通用收尾规则给本轮尝试收尾再结束，不因环境问题全局暂停；已见安全信号仍优先。无阻断终态而只是暂时无法执行时保留 ready；已有 uncertain / failed_no_send / needs_review、或人工接管阻断时不再外发，说明原因后 consumed（note 写原因），不承诺恢复后续发。环境 alert 不能替代 uncertain 的 inbox 与自身通知；无 IM 时汇报原因、恢复办法及重新发起步骤。

旧会话批量事项逐成员「登记 / manual → 登记后重读分诊，必要时先新 paused → 原决定 unpaused」，不写 pending、不外发，全部解除才 done；部分完成不消费 ready，恢复看 members 的 result 与 unpaused，localActions 不是成员完成清单。

自由文本「把旧会话都跟进」不能因唯一候选而扩大授权；只能明确引用批量通知或写决策 ID，在原成员里选择，含义不清就 clarify。IM 不能新增授权、修改条件或范围；事实与承诺仅用于该事项，不写回 qa.yaml 或 profile.json。

## 指令

本人私聊「暂停」「恢复」「重试」由 incoming 分类执行并排队回执。暂停使 ledger 禁止新的 pending、guard 失败；恢复解除暂停；重试重置未完成答复的自动唤起计数，不消费答复。

整个入站与调度由 poll_mutex 串行化。指令先入账为 pending、再执行记 applied；暂停状态与已执行 ID 原子保存。跨批次乱序旧 resume 不能解除更新 pause；副作用已落盘而 applied 未写时安全重放；后来的安全暂停不会被旧恢复解除。retry 按源消息 ID 去重，重放不能重置次数。旧版指令不重放，只在 status 汇总 pending / applied / unknown，不逐条排历史提醒。

技能遇风控、安全校验、登录失效，写待处理并 `ledger pause on --by <技能> --reason <原因>`，本人处理后发一条新的恢复指令。

## 等待答复与无人值守唤起

等待由 runtime 安排：普通一轮有待答事项且不是由监听器唤起时，最多等待 im.waitMinutes（默认 60）分钟，期间每 10 分钟同步 IM，续期运行锁并处理答复。核心不等待、不睡眠、不持有防睡眠进程。

无人值守 runtime 先保证执行器单实例、超时和整个进程树收尾，再用数据接口：

- `notify trigger claim`：暂停、有效运行锁、任何未 finish 的 started（含任意久以前旧监听器记录）、每答复自动 2 次上限均返回 blocked。允许时写 started，返回 launchId / messageIds / prompt，**不启动 Agent**。
- runtime 执行完并确认该实例及全部后代停止后，`notify trigger finish --launch-id X --status ok|failed|timeout|stopped --confirmed-stopped`。缺确认拒绝；failed / timeout 提醒排队。
- 旧 started 永远不靠租约或超时自动解锁。状态未知时停止并报告，请 runtime / 本人确认停止后 finish；运行锁不能替代执行器互斥。

提示词保留「由 IM 监听器唤起」兼容标记：此轮先处理 IM 答复与已决定事项，处理相关会话后结束，不进入等待、不在终端提问。

`notify status` 只读报告通道 / 私聊 / 游标 / 完整性、各 outbox 状态、stale token、lateSends、路由与私聊冲突、awaitingCheck（仅决策与 attemptId）、未结束 launch、ready、needsReconcile、legacyUnbound、otherRoutes，以及缺消息级元数据的回复恢复提示。unconfirmedSends 仅输出状态元数据，不将旧出站正文展示给新 owner。发现旧 listener.json 只提示本人卸载，不读取主目录配置、不安装或停止任何服务。旧 wait / listen / service 命令已删除。

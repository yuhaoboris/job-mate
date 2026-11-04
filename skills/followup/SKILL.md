---
name: followup
description: 巡检招聘平台（首批 BOSS 直聘）的 HR 会话：分类新消息，按授权级别代答问答库有依据的问题、发送简历、主动跟进，需要本人决定的事项写入 inbox 并上报。当用户说“看看 HR 回复”“处理消息”“回复 HR”“跟进一下”“有没有人要简历”，本人在 IM 中答复待决定事项（如「01 接受」「D-20261030-01 婉拒」），或定时任务、runtime 的 IM 唤起要求巡检消息时使用。不负责筛选职位和首次打招呼（用 apply），不替本人答应面试时间、工作地点、薪资数字或交换联系方式。
---

# followup：巡检 HR 会话，代答与跟进

完成「定位工作区 → 读取本人在 IM 中的答复 → 处理已决定事项 → 巡检会话 → 逐个分诊 → 按授权回复或上报（写 inbox 并经 IM 通知本人）→ 等待本人答复 → 记录 → 汇报」。

需要本人决定时**只暂停这个会话**，经 IM 转告本人，其他会话照常处理；本人在 IM 中答复后，Agent 继续回复 HR。

本文件引用的路径相对于本文件所在目录。开始前阅读：

- [../../shared/workspace.md](../../shared/workspace.md)：工作区、`profile.json`、`qa.yaml`、`resumes/index.yaml`
- [../../shared/ledger.md](../../shared/ledger.md)：事件记录与 `ledger.py`
- [../../shared/safety.md](../../shared/safety.md)：安全边界，尤其「必须上报本人、不得代为承诺」
- [../../shared/notify.md](../../shared/notify.md)：inbox、同步 IM 全链路、身份与路由门禁、received / ready、指令和 runtime 唤起
- [../../platforms/boss/common.md](../../platforms/boss/common.md)、[../../platforms/boss/followup.md](../../platforms/boss/followup.md)：BOSS 直聘通用要点与消息页面细节
- [../../shared/execution.md](../../shared/execution.md)：所有 runtime 的能力自检、完整读取、草稿保护、逐动作 guard 与全部尝试收尾；准备浏览器另读 [浏览器入口](../../shared/browser/README.md)

下文的 `ledger`、`notify` 分别指 `python3 <本文件上两级目录>/scripts/ledger.py` 和 `.../scripts/notify.py`，先解析成绝对路径再调用。

## 1. 准备

1. 定位工作区，`ledger validate`。返回错误时只读巡检、不发送。
2. 读取 `profile.json`（授权级别、时段）、`candidate.md`、`qa.yaml`、`resumes/index.yaml`、`rules.md`、`stoplist.yaml`。
3. 确定本轮能否发送，优先级从高到低：
   1. **本轮显式禁发**：本人这轮说「只看看」「这次别发」等，本轮任何消息都不发，**包括此前已批准的事项**。它只作用于本轮，`ready` 回复保留到下一轮，不消费。
   2. **全局暂停**：本人回过「暂停」，或技能遇到风控、安全校验、登录失效后自动暂停。它同样是临时状态：本轮不发，`ledger` 也会拒绝新的外发，但 **`ready` 回复必须保留**，恢复后继续。
   3. **授权级别**：`authorization.followup.level`，默认 `L2`。
4. **获取运行锁之前**，所有 runtime 先按 [加锁前能力自检](../../shared/execution.md#1-加锁前能力自检) 校验工作区 / 核心脚本、所选 `profile.browser`、可见浏览器操作及结果证据能力。配置非法或偏好不支持时不操作平台、不回退；缺外发证据用 `--no-send` 锁。不能读写工作区或运行脚本时不写记录、不外发，仅基于已有可读资料筛选并汇报。平台不可用而仍需本地对账时仅用禁发锁，按 [环境受阻与恢复](../../shared/execution.md#7-环境受阻通知与恢复) 分流 ready。获取运行锁：本轮显式禁发或能力要求禁发时 `ledger lock acquire --skill followup --no-send`，否则 `ledger lock acquire --skill followup`。记下 `runId`。被占用时说明占用者并结束；持锁后先执行第 2.1 节入口对账（含 `reconciled`），再同步 IM 或准备浏览器。禁发锁拒绝新的外发；`paused` 不为空表示已全局暂停：仅同步 IM、改 inbox、不浏览平台，`ready` 不消费。运行锁不是执行器强单实例，runtime 须自己保证旧实例及后代已停止。
5. 未全局暂停且环境可读取时，按 browser/README.md 准备浏览器，核对账号。全局暂停时不浏览平台、不查找旧会话。

| 级别 | 本轮能做的（本轮未禁发时） |
|---|---|
| L1 | 只读、分诊、起草；草稿写入 inbox 由本人决定。只执行本人已决定的事项 |
| L2 | L1 + 按 `qa.yaml` 回答有依据的问题、对方索要时发简历 |
| L3 | L2 + 按第 6 节主动跟进 |

**由定时任务运行时**：先同步 IM，执行已落盘的决定，处理相关会话后收尾；定时执行与进程互斥由所用 Agent 或系统负责。

运行超过 30 分钟时，每处理完一个会话执行一次 `ledger lock renew --run <runId>`；返回失败说明已失去运行锁，**立即停止**，不再操作浏览器。

## 2. 读取答复、对账，再处理已决定的事项

ledger 是技能判断的依据，inbox 是给本人看的。两者分开写入，中断可能留下不一致；以 ledger 为准。

### 2.0 同步 IM 并读取答复（每轮都做）

所有 IM 检查点都按 [同步 IM](../../shared/notify.md#同步-im) 执行，不把 poll 当作已经读取远端：

1. `notify poll` 取得 ownerId、chatId、sinceMs、larkProfile、readable。runtime 从 sinceMs 包含边界起按时间升序连续分页，用 `notify incoming --json <批次>` 入账；readable 为 false 时不读历史，先通过出站建立私聊和时间边界。没有 IM 能力时保留本地状态与待投递队列并报告，不另找通道、不声称没有答复。
2. incoming 的 complete 为 false 或 poll.lastIncomplete 为 true 时报告「IM 未读完」。`notify status` 的 incomingRejected 提示冲突 / 编辑等拒收时请本人重新发一条新消息，不按被拒正文执行；ledger 损坏时普通答复保留 received（ledger_unreadable），不猜唯一事项，暂停等合法指令仍按核心处理。
3. 排队通知、确认与追问统一 `notify outbox --claim` → 实际投递 → `notify receipt --key <K> --token <T> --result <结果>`。核对冻结 channel、ownerId、larkProfile、正文、replyTo、token、原 idempotencyKey。真实 messageId、chatId 才回 sent，可信 createTime 建立读取起点；明确未投递 failed（最多共 3 次），超时 unknown 不重发。handoff 无 receipt（含 stale）不重投，核对后用 unknown 收口。
4. `notify replies --state received` 逐条按事项与含义处理：明确结论写入 inbox / ledger，再 `notify reply-state --message <消息ID> --state ready --decision <事项ID>`。不带引用的「已发出 / 未发出」也只能对应核心给出的既有 uncertain 候选，多个候选先澄清，不能扩大授权。
5. 对应不上或含义不清先排队 clarify，走同一认领、投递、回执链；**clarify 已 sent 才标 clarifying**。queued、blocked、failed、unknown 均保留原回复状态并报告，不冒充已追问。

本轮禁发或全局暂停仍可同步 IM 和落盘本人结论，只是不执行平台外发。send / test 只 queued，不等于 sent；channel 为 none 只保留 inbox 并汇报。

`ready` 表示结论已落盘、尚待执行：`notify replies --state ready` 里的回复只执行 inbox / ledger 中已有的结论，**不重新解释原始答复**。按事项当前状态分流：

| 事项状态 | 处理 |
|---|---|
| 已决定，`ledger decision` 不是 `done` | 第 2.2 节执行；执行完再标 `consumed` |
| 已决定，`ledger decision` 为 `done` | 第 2.3 节补结果通知和 `consumed`，**不重发 HR、不重做步骤** |
| 「待处理」且本人已处理（inbox 已改为「已处理」） | 第 2.3 节确认并收尾，无外发动作 |
| 结论是「先不回」「保持暂停」等无需动作 | 第 2.3 节记录后标 `consumed` |
| 全局暂停或本轮显式禁发 | **保留 `ready`**，本轮不执行也不消费；恢复 / 下一轮继续 |

### 2.1 对账（每轮都做，本轮禁发时也做）

持锁后先按 [ledger.md 的释放与对账契约](../../shared/ledger.md#释放与对账契约) 完成入口对账：即使 acquire 的 `reconciled` 为空，也用完整 `ledger threads` 的全部 `unresolved` 与 inbox 对账，补齐缺失的 uncertain 核对事项，再做以下 `pausedBy` 对账；不先执行任何已决定的外发。

1. `inbox/decisions.md` 中每个涉及会话的「待决定」事项：`ledger threads --company <公司> --recruiter <招聘者>` 的 `pausedBy` 里没有该决策 ID 时，补记 `observe/paused`（带 `decision`）。
2. `ledger threads` 中每个 `pausedBy` 里的决策 ID，在 inbox 里找不到对应事项时：用 `ledger decision --id <ID>` 返回的 `pausedRecords`（原因、时间、公司、招聘者）补写一条「待决定」事项，只写这些已知信息，不臆造选项和截止时间；在汇报中说明。
3. inbox 中的「已执行」标记只是展示：`ledger decision` 不是 `done` 的已决定事项，无论 inbox 怎么标，都按第 2.2 节继续执行，并在汇报中说明。

### 2.2 执行已决定的事项

处理 inbox 中标题为「已决定」、且 `ledger decision --id <ID>` 不是 `done` 的事项。结论含义不清时不猜，在汇报中请本人澄清。

**本人已接管（`manual`）的会话，旧决定一律不执行**，在汇报中列出，等本人明确交还后再处理。结论本身就是「交还会话」的除外。

1. **列出结论要求的全部动作，排好顺序**：本地动作在前（可以重复执行），外发在后。给每个外发动作起一个步骤编号，如 `reply1`、`resume1`，同一事项每次运行都用同样的编号。执行决定的外发**必须**带步骤编号，`ledger` 会拒绝不带的。

| 动作 | 例子 | 执行 |
|---|---|---|
| 交还会话 | 「这个会话交给 Agent」 | 已有 `adopted` 或 `sent` 时记 `observe/released`（带 `decision`）；本地无记录时按第 4.1 节本人点名交出的流程记 adopted |
| 更新资料 | 「把这个口径加进问答库」「附件已上传」 | 先 `workspace backup`，再改 `qa.yaml` / `resumes/index.yaml`；停止名单用 `ledger stoplist add` / `remove` |
| 外发 | 接受面试并回复时段、批准某条草稿、同意发某版简历 | 第 3 步；本轮禁发时不执行，只在汇报中列出 |
| 解除暂停 | 「解除暂停」「先照常回复其他问题」 | 没有其他动作，直接第 4 步 |
| 不处理 / 继续等 | 「先不回」「等我周五再说」 | 不动作，保持暂停，跳过第 4 步；在汇报中列出 |

   **只有外发动作才发消息**，不要为了让事项「完成」而编一条回复。

2. 先看 `ledger decision --id <ID>`：
   - `state` 为 `needs_review`：这个决定有不带步骤编号的外发记录，无法确定它对应哪个动作。**不要新建任何外发**，在汇报中列出该记录（`attempts`），请本人核对。本人确认决定已全部完成时，由 Agent 记带 `decision` 的 `observe/unpaused`（`skill: user`，`reason` 引用本人原话）；本人要求补做某个动作时，按本人的说明处理。
   - 其他状态：看 `localActions`，已做过的本地动作可以跳过，再做一次也无害。
3. 每个外发步骤按顺序处理，先查 `ledger decision --id <ID> --step <编号>` 的 `stepState`：

| `stepState` | 处理 |
|---|---|
| `not_started` | 按第 5 节发送；`check` 带 `--decision <ID>`，`pending` 带 `decision` 和 `step` |
| `in_progress` | 有未确认的外发：只核对页面，不新建尝试；看不清结果就请本人核对，**这个事项到此为止** |
| `done` | 已发出，跳过，不要再发 |
| `failed` | 上次明确未发出：本轮不重试，**这个事项到此为止**，在汇报中说明 |
| `needs_review` | 该决定有不带步骤编号的外发记录，无法确定对应关系：不新建外发，**这个事项到此为止**，按第 2 步请本人核对 |

4. 全部动作完成后，**最后**记 `observe/unpaused`（带 `decision`）。这条记录解除该会话暂停；只有已有带该 ID 的 `unpaused`，且没有会话仍被该 ID 暂停时，`ledger decision` 才返回 `done`。
5. 在 inbox 该事项末尾追加「已执行：<时间>，<做了什么>」。
6. `notify send --kind done --decision <ID>` 只排队结果通知：正文包含**这一结果版本**的实际执行结果，例如实际发送的文字，或失败 / 结果不明 / 暂停的原因和本人要做什么，不拿用户决定原文代替。首次排队前把稳定正文保存到 inbox（例如「结果：…」），同一结果版本补发复用保存的正文；已有该键则以 notify 冻结原文为准。结果改变（如 uncertain → done）时保存新版本再排队，不更改原条目。投递与回执按第 2.0 节；执行时会话有 HR 新消息按第 4 节一并处理。
7. 该回复是 `ready` 的，执行完后 `notify reply-state --message <ID> --state consumed --decision <ID>`。若本轮被**全局暂停或显式禁发**，不要标 `consumed`，保留 `ready` 到恢复 / 下一轮。若动作被人工接管、`uncertain` / `failed_no_send` / `needs_review` 阻断，不要因状态恢复而自动重新外发：按原规则停下来，在汇报中说明，并把回复标 `consumed`（`--note` 写原因），避免 runtime 反复唤起；ledger 中的决定状态仍以 `ledger decision` 为准。产生 uncertain 时按[结果不明事项](../../shared/notify.md#结果不明事项)在原决定中补记 attemptId 与结果，不另开事项或重复暂停。

中途在任何一步中断，`ledger decision` 返回 `partial` 或 `in_progress`，并列出已完成的步骤；下一轮按上面的步骤接着做，已完成的会被跳过。

### 2.3 收尾 ready（不重发 HR，不重新解释旧答复）

用于三类情况：`ledger decision` 已经是 `done`；inbox 中「待处理」事项已改为「已处理」；结论明确「先不回 / 保持暂停 / 无需动作」。

1. 只按 inbox / ledger 中已经落盘的结论收尾。不要重新解释原始回复，不要执行第 2.2 节的外发步骤，**不向 HR 发任何消息**。
2. 已决定且 `ledger decision` 为 `done`：若结果通知还没确认送达，使用 inbox 中保存的**最终结果正文**（不是用户决定原文）。缺失时先根据 ledger 事实（steps、attempts 状态、实际发送的文字或失败 / 不明原因）生成最终结果正文并保存到 inbox，再执行 `notify send --kind done --decision <ID>`；同一结果版本复用同一正文，若已有发送尝试以 `notify` 持久化原文为准。然后标 `consumed`。这一步只是补发结果通知，不算实际执行动作，因此**不发 `summary`**（第 9 节）。
3. 已决定但不是 `done`：先看 `ledger decision` 的 `steps` 和 `stepState`。有 `in_progress`、`uncertain`、`failed`、`needs_review` 时按第 2.2 节第 2、3 步停下并请本人核对（uncertain 的事项记录按[结果不明事项](../../shared/notify.md#结果不明事项)沿用原决定）；确认本轮不能继续时标 `consumed`（`--note` 写阻塞原因），避免 runtime 反复唤起，ledger 的决定状态不变。没有可执行动作但决定未收尾（例如「先不回」「无需动作」）时，在 inbox 写明保持暂停，标 `consumed`（`--note`），不要伪造 `unpaused`。**这种情况不发 `done`**：没有执行结果可报，发一条结果通知只会打扰本人；也**不发 `summary`**。
4. 「待处理」事项：确认本人已完成（inbox 标题为「已处理」或本人说明），在事项末尾补「已执行」；没有外发动作，用这一结果版本保存的稳定正文发一条 `done` 说明已处理，然后标 `consumed`。
5. 收尾只做一次：done 已有 sent 回执返回 `already_sent`，仅排队则 `already_queued`，不能当作已送达；consumed 后 runtime 不再因这条回复自动唤起，未投递通知仍由 outbox 保留。
6. 全局暂停或本轮显式禁发时，第 2–5 步都不做，保留 `ready`；恢复 / 下一轮再收尾。

## 3. 巡检会话

1. 打开消息列表，先处理有未读标记的会话，再处理 `ledger threads --platform boss` 中 `canFollowup` 为 `true` 的会话（仅 L3）。
2. 本轮处理的会话数以本人指令为准；未说明时处理全部未读和待跟进会话。列表懒加载时滚动加载，读到已处理过的旧会话为止。
3. 对每个会话，读完整上下文：从我方第一条消息或最近 20 条消息起，包括 HR 的请求卡片（交换联系方式、简历请求、面试邀约）。不只看列表摘要。
4. 用 `ledger threads --company <公司> --recruiter <姓名(职务)>` 查本地状态。招聘者写法见平台通用页面要点。
5. 只有公司名精确匹配、招聘者也一致才认作同一会话；疑似匹配不点开，汇报「身份不确定」。
6. **检查点**：每处理完一个会话，完整执行第 2.0 节同步链（poll → runtime 连续分页 → incoming → outbox 认领 / 投递 / receipt）；有新答复先按第 2.0、2.2 节处理，再继续下一个。未读完不说没有答复；超过 30 分钟按第 1 节续锁。

## 4. 分诊

### 4.1 先判断会话类型

| 类型 | 判断 | 处理 |
|---|---|---|
| 新入站会话 | 会话里**没有任何我方消息**，本地也没有记录：HR 主动来信 | 按第 4.2 节，先评估职位 |
| 已登记会话 | 本地有 `adopted` 或 `sent` 尝试 | 按第 4.3 节 |
| 本人自己的会话 | 页面已有我方活动，本地无记录 | 不回复、不跟进，只汇报；本人点名交给 Agent 才记 adopted |

已登记会话中 HR 推来**另一个职位**时，对新职位先按第 4.2 节评估，再处理消息本身。

页面有我方活动而本地无记录，视为本人自己的会话：不回复、不跟进，只汇报。只有本人明确点名交出时，在读取页面前记录 `readAt`，有效锁内记 `observe/adopted`（`skill: user`，带 `readAt`，reason 引用本人原话），再按正常会话处理；不批量登记。

### 4.2 评估职位（新入站会话、已有会话中的新职位）

打开对方关联的职位详情，按 `apply` 的筛选条件（正向条件须证实，排除项明确命中才排除，招聘者活跃度不适用于主动来信）和 `ledger check --platform boss --company <公司>` 评估。`check` 返回的原因中只看 `stoplist`、`stoplist_suspect`、`rejected`、`risk`；`same_company` 等首次联系去重的原因不适用于对方主动来信：

- **符合**：按第 4.3 节处理对方的消息；第一条回复表达对该职位的兴趣。会话原先因职位不符记过 `declined` 的，先记 `observe/reopened`（`reason` 写新职位和评估结论）。
- **明确不符**：不回复，记 `observe/declined`（`reason` 写不符的条件），会话就此关闭，之后不会再代答或跟进；HR 以后推来另一个职位时重新评估。按不符条件的性质再区分：
  - **公司层面**（人员外包、劳务派遣等用工性质）：同时 `ledger stoplist add`（原因写「不合硬条件：<哪一条>」），整家公司不再联系。
  - **职位层面**（单休、薪资、职责、地点等）：只记 `declined`，不加停止名单。
- **待确认**：记 `observe/paused`（带决策 ID），再写入 inbox，等本人决定。

### 4.3 按消息意图处理

命中多条时按表中靠前的处理：

| 场景 | 处理 |
|---|---|
| 公司在停止名单、或会话已记 `rejected` / `risk` / `job_closed` / `declined` | 不回复；HR 有新消息时在汇报中列出。只有因 `declined` 关闭、且没有其他结束原因的会话，HR 推来另一个职位时才按第 4.2 节重新评估；`ledger threads` 的 `closedBy` 列出全部结束原因 |
| 本地 `manual` 为 `true`（本人已接管） | 不发送；只在汇报中列出新消息 |
| 页面有对应不上本地记录的我方活动 | 先按下面的接管分界判断，含文字和卡片操作；确定接管记 `manual`，无法判断暂停询问 |
| 本地 `pausedBy` 非空（等待本人决定） | 不发送，**整个会话暂停**，包括其他有口径的问题和跟进；在汇报中列出新消息，必要时补充到原决策事项 |
| 有未确认的外发（`unresolved` 非空） | 不发送；先请本人核对 |
| 风险信号：收费、培训贷、刷单、索要证件或银行卡、要求下载其他 App 或加群 | 不回复；记 `observe/risk`，`ledger stoplist add`（来源写 `followup <日期>`），写入 inbox |
| 明确拒绝、职位已关闭 | 记 `observe/rejected` 或 `observe/job_closed`；不补发告别消息 |
| 沟通中确认明确不符合硬条件 | 按 `qa.yaml` 对应口径简短说明后停止推进：先发说明（第 5 节），再记 `observe/declined`（`reason` 写不符的条件）关闭会话。不符的是公司层面的用工性质（人员外包、劳务派遣）时，再 `ledger stoplist add`；职位层面的（单休、薪资、职责、地点）不加停止名单 |
| 问「是不是机器人 / AI 在回」 | 不否认、不编造；写入 inbox 上报 |
| 面试邀约（含平台邀约卡片） | 可以先问时段、形式、时长、面试官；是否接受、选哪个时间写入 inbox |
| 问能否接受某个工作地点、驻场、出差 | 可以先问清地址和比例；是否接受写入 inbox |
| 给出具体薪资数字或 Offer | 不答应；写入 inbox |
| 交换微信 / 电话（平台按钮请求） | 不点同意；写入 inbox。对方只是口头索要时，可按 `qa.yaml` 的 `contact` 口径婉拒 |
| 索要简历（文字或平台简历请求卡片） | 按第 5.2 节发送 |
| HR 提问，`qa.yaml` 有对应口径 | 按第 5.1 节回答 |
| HR 提问，`qa.yaml` 没有依据 | 不回答；写入 inbox，附上建议口径草稿和所需依据。本人答复后，提议把口径加入 `qa.yaml`，本人确认再写入 |
| HR 回复了但无需回答（如「好的」「收到」） | 记 `observe/replied`；不必回复 |
| 我方最后发言，HR 未回 | 仅 L3：第 6 节 |

**接管分界（所有会话）**：用 `threads.boundaryAt`，取本地 `sent` 时间、登记的 `readAt`、最近交还 `released` 事件时间中的最晚者。登记不用写入时间；交还不能把分界推到未来。

逐一核对我方文字与发简历、同意交换联系方式、接受面试等卡片活动。页面「10:00」为 [10:00, 10:01)，只显示日期为当天全天；看不清时间或执行者按无法判断，不能断言接管或没有接管。

1. 先比对**所有**本地 `sent`（包括构成分界的那条）：文字比 `outboundTexts`，简历卡片比 `resume` 尝试；同类且记录时间在页面区间前后 2 分钟内，算 Agent 发的。
2. 对应不上、区间整个早于分界：历史，不比对内容、不保存原文。整个晚于分界：本人接管，记 `manual`。
3. 跨分界（同一分钟或同一天日期标签）：本轮不发，先 `paused` 再写「这条是你发的吗？」；选项「是我，我来处理」（manual）/「交给 Agent 继续」（released）。选交还时 `released.acknowledged` 按页面标签分组，记录 `shown`、`precision: day|minute`、`kind: text|resume|contact|interview`、正整数 `count`，不记原文。分界仍取事件时间。
4. 后续跨界活动中，同标签同类型按页面从早到晚的前 count 条视为已确认历史，超出条数仍判断为无法判断；整个晚于分界的新活动仍是接管。上午确认当天 1 条，下午新发「14:00」是接管；若也只显示日期，则是第 2 条、再次暂停，不能算历史。

HR 有新的实质性回复时，先记 `observe/replied`（`reason` 写一句摘要，不抄全文），再处理。

**需要本人决定的会话事项：先记 `observe/paused`（带 `decision`），再写入 inbox**。此后整个会话暂停，直到该事项执行完毕或本人明确解除（`observe/unpaused`）。两步之间中断时，下轮由第 2.1 节的对账补齐。

L1 下，需要发送的一律改为起草，写入 inbox。

## 5. 发送

### 5.0 每次发送前的统一检查

所有 runtime 按 [输入与草稿保护](../../shared/execution.md#4-输入与草稿保护)、[证据与 guard](../../shared/execution.md#5-外发前-guard-与证据三分类) 执行。文字回复 / 跟进 / 已批准文字，先核对会话未被接管、最终文字非空，再完整读取 chat-input；这些检查在第 4 步 pending 之前完成。核对身份前不读写输入框；未知内容不清空，跳过、不写 pending 并汇报。no-send / L1 只起草时不碰聊天输入框（包括清草稿），搜索 / 筛选仅可改变显示。恢复后发前重新做去重和状态检查，不能用恢复规避 uncertain。

回复、发简历、主动跟进、执行已决定事项，**每一次**发送都先过这几步，任何一步不通过就不发：

1. 本轮未禁发。
2. `ledger check --platform boss --kind <reply|resume|followup> --company <公司> --recruiter <姓名(职务)> --run <runId>`（执行已决定事项时加 `--decision <ID>`）返回 `allowed: true`。它会检查停止名单、会话是否结束、本人接管、等待决定的暂停、未确认的外发，以及主动跟进的条件。
3. 重读会话末尾：如果出现了新消息，回到第 4 节重新判断。
4. `ledger append` 一条 `pending`（执行已决定事项时带 `decision`）。返回错误（如已失去运行锁、本轮禁发）时**立即停止整轮**。
5. **每一次**可能把消息发出去的点击（「发送」「同意」「发简历」的确认按钮）之前，执行 `ledger guard --run <runId> --attempt <attemptId>`，返回 `allowed: true` 后**立即**点击，中间不做别的事。返回失败说明已失锁或该尝试已被改记，**立即停止整轮**：不点击，也不为同一条消息新建尝试；结果交给本人核对。中途有过等待、卡顿或工具超时，点击前重新读页面再 `guard`。

### 5.1 回复

1. 只依据 `candidate.md`、`qa.yaml` 和会话上下文；未知的只答已知部分，说明会确认后回复，不编造，也不把未知说成「没有」。
2. 写法：第一人称、简短、一次一个重点，两三句以内。称呼对方公司用「贵公司」+ 岗位名，不点公司简称。不重复已答过的问题。同一条消息里最多带一个主动提问（如团队规模、自研还是项目交付、面试流程）。
3. 过第 5.0 节的检查，`pending` 为 `kind: reply`，`text` 为拟发文字，`reason` 写对应的 `qa.yaml` 条目 id。
4. 按平台通用页面要点和 [输入与草稿保护](../../shared/execution.md#4-输入与草稿保护) 写入，读回须与最终文字完全一致；不一致时不发送、不换输入方式，只清除能确认属于本次写入的草稿。无法安全清理保留现场并报告。发送前再次核对会话标题，再 guard 后立即点击。
5. 确认已发出（会话中出现这条新消息且显示送达或已读）后追加 `sent`；不能确认记 `uncertain`，**该会话停止**，按[结果不明事项](../../shared/notify.md#结果不明事项)写入 inbox（含 attemptId、沿用原决定及旧事项待对账）；其他会话继续。

### 5.2 发简历

1. 按 `resumes/index.yaml` 的 `use` 和职位方向选版本；拿不准时用 `default`，在汇报中注明。
2. 该版本 `uploaded.boss` 为空时不发送：写入 inbox，请本人上传附件并更新索引。Agent 不打开系统文件选择器。
3. 过第 5.0 节的检查，`pending` 为 `kind: resume`，`resume` 写版本 id 和平台附件名。
4. 所有 runtime 的按钮定位都限定在已核对的会话与具体卡片内，卡片后已有附件已发送等提示不再点。工具栏「发简历」、附件确认、卡片「同意」等每个可能外发的点击之前分别 `guard`，不套用文字消息的新气泡判据。`guard` 后按平台会话页面要点发送或同意对方的简历请求（选附件后的确认按钮前再 `guard` 一次），然后**每个结果都要给这次尝试记终态**：

| 点击后看到 | 记录 |
|---|---|
| 请求已发出，等待对方同意 | `sent`，`reason` 注明「请求待同意」；之后看到附件送达再记 `observe/resume_delivered` |
| 附件直接送达（同意对方请求时常见） | `sent`，再记 `observe/resume_delivered` |
| 无法判断是否发出 | `uncertain`，该会话停止，按[结果不明事项](../../shared/notify.md#结果不明事项)写入 inbox |
| 明确失败（页面提示发送失败） | `failed_no_send`，本轮不重试 |

5. 对方查看后记 `observe/resume_viewed`。请求发出不等于附件送达，汇报中分开写。

## 6. 主动跟进（仅 L3）

1. 只跟进 `ledger threads` 返回 `canFollowup: true` 的会话；第 5.0 节的 `check --kind followup` 会再核对一次：每天至多 1 次、连续 2 次未回停 7 天、总数至多 3 次、未接管、未暂停、未结束。
2. 还要满足页面状态：我方最后一条消息「已读」满 1 小时未回，或「送达」满 1 小时仍未读。以消息气泡上的时间起算，不从记录写入时间算。
3. 只在 `profile.json` 的 `schedule.activeHours` 内跟进。
5. 内容：一条与 JD 相关、`candidate.md` 有依据的真实经历，或一个具体问题；不催促，不重复上一条，不写「再跟进一下」。
6. 过第 5.0 节的检查，`pending` 为 `kind: followup`，发送确认同第 5.1 节。

## 7. 上报

**结果不明（`uncertain`）的外发**统一按[结果不明事项](../../shared/notify.md#结果不明事项)写入 inbox：记录 attemptId，执行原决定时沿用原 ID，不另开或重复暂停；旧事项关联先对账，核对选项为「已发出 / 未发出」。本人在 inbox 或对话中确认后，由 Agent 代为追加同一 `attemptId` 的 `sent` 或 `failed_no_send`，`skill` 记 `user`，`reason` 引用本人原话。只有本人明确说过结果才能这样记，Agent 自己看页面判断的不算。

**运行锁被上一轮占着**（上一轮崩溃没有释放）时，默认等它过期。本人确认上一轮已经停止，可以让 Agent 执行 `ledger lock release --run <上一轮 runId>`，同样按 [释放与对账契约](../../shared/ledger.md#释放与对账契约) 处理 `reconciled` 与释放失败；Agent 不得自行判断后释放别人的锁。

需要本人决定的事项按 notify.md 写入 `inbox/decisions.md`：一个会话的同一问题沿用原决策 ID；写清对方原话的要点（不抄全文）、选项、截止时间、本轮暂停了什么。涉及某个会话时同时记 `observe/paused`。写入后立即 `notify send --kind decision --decision <ID>`（需要本人动手而非选择的事用「待处理」和 `--kind todo`），然后**继续处理其他会话**，不停下整轮。

遇到验证码、安全验证、访问限制、登录失效：按 safety.md 立即停止整轮，写「待处理」，`ledger pause on --by followup --reason <原因>`，`notify send --kind todo`。

## 8. 等待本人答复

所有会话处理完，配置了 IM 且 runtime 有同步与等待能力、im.waitMinutes 大于 0、本轮不是 IM 唤起、仍有待答事项时，由 runtime 安排有界等待；核心脚本不等待、不常驻。能力不足就收尾并报告，下次运行先同步，不另找通道。

1. runtime 在不超过 im.waitMinutes 的期限内定期执行第 2.0 节完整同步链；任何等待后重新核对状态，不以只读 poll 代替远端读取，分页不完整明确汇报。
2. 每次同步前 `ledger lock renew --run <runId>`，失败立即停止并收尾；有答复按第 2.0、2.2 / 2.3 节处理。paused 非空结束等待，ready 保留；未知 / 环境受阻不再等待。
3. 每 10 分钟检查平台未读列表（仅环境可用且未暂停时），有新未读按第 4 节处理；完整读取后才下否定结论。
4. 事项已处理或达到期限即收尾，下次运行先处理已有答复。

## 9. 收尾与汇报

**结束前必查**：运行环境受阻且 `im.channel` 为 `none`（或 IM 送达未确认）时，按 [环境受阻与恢复](../../shared/execution.md#7-环境受阻通知与恢复)，最终答复必须包含 **原因、环境恢复办法、恢复后如何重新发起** 三项；由本人重新发起本技能。结果待核对另行说明，不能代替恢复指引。

所有 runtime 的任何停止分支（含读取失败）都先按 [尝试阶段与停止前收尾](../../shared/execution.md#6-尝试阶段与停止前收尾) 给本轮所有 pending 收尾；已有终态不覆盖，已见成功证据优先记 sent。unknown 或环境受阻时结束整轮，不再新建尝试，不靠同一 runId 重取锁降级（noSend 不会改变），不进入第 8 节等待。安全信号优先全局暂停；纯环境受阻不全局暂停，按共享通知规则分流 ready、分别排队环境提醒与按[结果不明事项](../../shared/notify.md#结果不明事项)记录的 uncertain 通知，不承诺自动续发。

本轮**实际处理过会话或执行过已决定事项**（含本地动作）时，`notify send --kind summary --run <runId>`：处理了多少会话、执行了哪些决定、还有哪些事项等本人答复。**只做第 2.3 节收尾的轮次不发 `summary`**——消费 `ready`、补发已 `done` 决定的结果通知、结论为「无需动作 / 先不回」时没有新的处理结果可汇报，发摘要只会制造噪声。收尾同步第 2.0 节（不重试未知投递、不因此新建平台尝试），然后执行 `ledger lock release --run <runId>`，按 [释放与对账契约](../../shared/ledger.md#释放与对账契约) 核对 `released`、`reconciled` 与释放失败，按 attemptId 对照最新 unresolved / inbox、保留原决定 ID。释放后仅本地对账和通知，不再外发；IM 不可用不妨碍 pending 收尾或释放。

汇报：

- 本轮是否禁发、授权级别。
- 巡检范围：处理了哪些会话、未检查的范围及原因。不能把「未读为 0」或只看列表摘要写成「没有新回复」。
- 每个会话一行：公司、招聘者、HR 最新消息要点、本轮动作（已回复 / 已发简历请求 / 已跟进 / 已上报 / 暂停中 / 未处理及原因）。
- 新写入 inbox 的事项和决策 ID、已执行的决定、IM 答复及处理。分别报告 queued / handoff / sent / failed / unknown / blocked；退出码 0 不等于送达。IM 未读完、incomingRejected 与路由冲突不得省略。
- 本人接管、停止名单新增、风险信号。
- 需要本人在平台上处理的事（上传简历附件等）。
- 页面有我方活动而本地无记录的会话只列出，说明未回复、未跟进；本人可明确点名交给 Agent。

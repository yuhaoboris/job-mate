# 事件记录

`ledger/events.jsonl` 是跨平台、跨技能共享的追加式记录。**只通过 `scripts/ledger.py` 读写**，不要用文件工具直接编辑、整体改写或删除行。

调用方式（`<job-mate 技能目录>` 为本文件上两级目录，先解析成绝对路径）：

```sh
python3 <job-mate 技能目录>/scripts/ledger.py [--workspace <工作区>] <命令> ...
```

所有命令输出一个 JSON 对象。退出码：`0` 成功或允许；`1` 不允许（检查不通过、额度用尽、锁被占用）；`2` 错误（参数、文件损坏、工作区缺失、未持有运行锁、本轮禁发）。退出码为 `2` 时停止一切外发并报告。

## 命令

| 命令 | 用途 |
|---|---|
| `validate` | 校验记录与停止名单；有损坏行时报告行号 |
| `lock acquire --skill <apply\|followup\|setup> [--no-send]` | 开始一轮前获取运行锁，返回 `runId`。`--no-send` 表示本轮禁发；新建 setup 未完成或草稿模式读不出时，`apply` / `followup` 被拒绝，先完成 setup |
| `lock renew --run <runId>` | 续期；失败说明已失去运行锁，立即停止 |
| `lock release --run <runId>` | 一轮结束时把本轮未终结 `pending` 追加为 `uncertain` 后释放，返回 `reconciled` |
| `lock status` | 查看当前持锁者 |
| `pause on --reason <原因> [--by <谁>]` / `pause off [--by <谁>]` / `pause status` | 全局暂停：暂停期间任何一轮都不能写入新的 `pending`，`guard` 失败。`lock acquire` 的输出带 `paused`，不为空时本轮只能做不外发的事 |
| `check --platform boss --company <公司> [--title <职位>] [--job-url <链接>] --run <runId>` | 首次招呼前：停止名单、拒绝与风险记录、首次联系去重 |
| `check --platform boss --kind <reply\|resume\|followup\|intro> --company <公司> --recruiter <姓名(职务)> --run <runId> [--decision <ID>]` | 会话内每次发送前：停止名单、会话结束、本人接管、等待决定的暂停、未确认的外发；`followup` 另查跟进条件 |
| `quota --platform boss --run <runId>` | 剩余招呼额度、距上次外发的秒数、需等待的秒数 |
| `append --json '<事件>'` | 追加一条事件；也可从 stdin 读入 |
| `decision --id <决策 ID> [--step <编号>]` | 查某个已决定事项的执行状态：`not_started` / `partial` / `in_progress` / `failed` / `needs_review` / `done`。`needs_review` 表示有不带步骤编号的外发记录，无法确定对应哪个动作，须本人核对，期间不能为该决定新建外发。另返回各外发步骤的状态（`steps`、`stepState`）、已做的本地动作（`localActions`）、暂停记录的原因与会话（`pausedRecords`）、仍被它暂停的会话（`stillPausing`）。`localActions` 为字符串列表。**已有带该 ID 的 unpaused，且没有会话仍被它暂停才算完成**；部分解除不能盖过 in_progress / needs_review / failed 等阻断 |
| `guard --run <runId> --attempt <attemptId>` | 每次可能把消息发出去的点击之前：确认本轮仍持有有效锁、该尝试仍是本轮的 `pending`，并续期。失败时立即停止，不点击 |
| `threads [--platform boss] [--company <公司>] [--recruiter <姓名(职务)>]` | 按会话汇总：最近外发与文字、HR 回复时间、跟进次数、是否本人接管、等待哪些决定、是否已结束、能否主动跟进及原因 |
| `stats [--since YYYY-MM-DD] [--until YYYY-MM-DD]` | 统计 |
| `stoplist list` / `stoplist add --name <公司> [--alias <别名>]... --reason <原因> --source <来源>` / `stoplist remove --name <公司>` | 停止名单；`remove` 按 `name` 精确匹配 |

## 运行锁

同一工作区同时只运行一个技能。锁有过期时间（默认 60 分钟），过期后可被下一轮接管。正常释放或过期接管都在协调锁内，把对应 `runId` 没有终态的 `pending` 追加为 `uncertain`，在输出的 `reconciled` 中列出（attemptId、company、title、kind）；已有终态保持不变，不核销其他轮次的尝试。调用者必须据此写入 `inbox/decisions.md` 请本人核对，脚本不会代写 inbox。

释放先在锁中保存 `releasing` 与待核销的 `releaseAttempts`，全部核销成功后才删除锁。收尾意图落盘后中途失败时立即停止外发：保留锁、拒绝新的 `pending` / `guard`、续期与同 runId 沿用；重试同一 `lock release` 完成收尾，或等过期接管。重试 / 接管的 `reconciled` 包含本次收尾已追加但尚未被本人核对的 `uncertain`，不重复追加终态。事件损坏时保留锁并报错，先人工处理损坏记录。

- `setup-progress.json` 为新建或模式读不出时，`apply` / `followup` 获取锁返回不允许（退出码 1），即使是沿用已有 `runId` 也拒绝；`setup` 不受影响。重新设置、部分重新设置草稿不阻断当前配置。
- **新的可能外发必须由有效持锁者写入**：`append` 一条动作 `pending` 时，`ledger` 核对 `runId` 是当前未过期的持锁者，且本轮未禁发，同时自动续期。不满足就返回错误（退出码 2），此时立即停止，不操作浏览器。
- **每次点击前再确认一次**：写入 `pending` 之后、真正点击之前可能停顿（进程暂停、工具超时），期间锁可能过期被接管，该尝试被改记 `uncertain`。所以每次可能发出消息的点击前都要 `guard`，通过后立即点击；失败就停止，不点击，也不新建尝试。
- 长时间只读（读详情、巡检）时，定期 `lock renew`。
- `observe/adopted` 仅本人点名交出会话时使用，skill 必须为 user，带不晚于事件时间的有效 readAt；须在有效运行锁内，禁发 / 全局暂停拒绝。持锁读取、去重、校验并追加。其他观察、终态和人工核对记录已发生事实，不要求运行锁。
- 上一轮崩溃没有释放锁时，默认等它过期；本人确认上一轮已经停止，才可以 `lock release --run <上一轮 runId>`。

## 事件字段

`append` 自动补齐 `schemaVersion`、`at`、`account`（工作区只有一个该平台账号时）；新 `pending` 和独立的 `skipped` / `already_contacted` 不带 `attemptId` 时自动生成并在输出中返回。给了 `jobUrl` 时自动去掉查询参数并提取 `jobId`。

| 字段 | 说明 |
|---|---|
| `platform`、`runId`、`skill` | 必填；`skill` 为 `apply`、`followup`、`setup`，本人核对记 `user` |
| `kind` | 动作：`greet`（首次沟通）、`intro`（平台自动打招呼后的补发）、`reply`、`followup`、`resume`；观察：`observe` |
| `status` | 动作事件必填，见下 |
| `observed` | 观察事件必填，见下 |
| `company` | 必填，按页面显示原文 |
| `title`、`location`、`recruiter` | 必须给出；不可见时为 `null`。`recruiter` 只写「姓名(职务)」，如 `张先生(人事行政专员)` |
| `reason` | 必填：为什么做，例如逐项匹配依据、跳过原因 |
| `evidence` | 必须给出：凭什么认定结果，例如「会话中出现本次新消息，显示已送达」；没有时为 `null` |
| `decision` | 可选：执行或暂停所对应的决策 ID，形如 `D-20261028-01` |
| `step` | 须与 `decision` 同用：同一事项中外发动作的步骤编号，如 `reply1`、`resume1`。执行决定的外发**必须**带 `step` |
| `jobUrl`、`jobId`、`salaryText`、`text`、`resume`、`tags` | 可选；`text` 为实际发送的文字（可见时）；`tags` 是非空字符串数组 |
| `acknowledged` | 只用于 released：本人确认过的模糊活动数组，每项仅 shown（非空页面时间标签）、precision（day / minute）、kind（text / resume / contact / interview）、count（正整数），不记原文 |

不记录密码、Cookie、令牌、手机号、完整简历或聊天全文。

## 动作状态

| 状态 | 含义 | 是否阻止再次联系 |
|---|---|---|
| `pending` | 已落盘，即将执行可能发送的动作；也可能中断在动作之后 | 是，直到有终态 |
| `sent` | 有明确证据证明本次已发出 | 是 |
| `already_contacted` | 页面证明之前已联系（如显示「继续沟通」） | 是 |
| `uncertain` | 动作可能已发送，但结果无法证实 | 是，直到本人核对 |
| `failed_no_send` | 有明确证据证明没有发出 | 仅本轮不重试 |
| `skipped` | 没有执行可能发送的动作 | 否 |

同一 `attemptId` 只允许以下转换，`ledger` 会拒绝其他写法：

| 当前 | 可以追加 | 谁可以写 |
|---|---|---|
| （新尝试） | `pending`；或不经 `pending` 的独立 `skipped` / `already_contacted` | 技能；`pending` 须持锁 |
| `pending` | `sent`、`uncertain`、`failed_no_send` | 技能 |
| `uncertain` | `sent`、`failed_no_send` | 仅本人核对（`skill: user`） |
| `failed_no_send` | `sent` | 仅本人核对（`skill: user`） |
| `sent`、`skipped`、`already_contacted` | 不能再追加 | — |

同一尝试的 `kind`、`platform`、`company` 不能改变。

- 每次可能发送前，先 `append` 一条 `pending` 并确认成功；观察结果后，用同一个 `attemptId` 追加终态。
- 超时、空白页、弹窗消失、「没看到消息」都不能证明 `failed_no_send`，记 `uncertain`。
- 打开聊天页、按钮变色、工具返回「点击成功」都不等于 `sent`。
- 一个尝试中只要出现过 `sent`，就始终按已发送处理。
- 本人核对 `uncertain` 后（在 inbox 或对话中说明「已发出」或「未发出」），由 Agent 代为追加同一 `attemptId` 的 `sent` 或 `failed_no_send`，`skill` 记 `user`，`reason` 引用本人原话。Agent 自己看页面判断的不能记为 `user`。
- 招呼额度按「写过 `pending` 的 `greet` 尝试」计，失败或结果不明也不退回；发送间隔按最近一次 `pending` 或 `sent` 的时间算。

## 观察结果

`replied`、`resume_delivered`（附件已送达）、`resume_viewed`（对方已查看）、`interview_invited`、`rejected`、`job_closed`、`declined`（我方判定职位不符，会话关闭）、`reopened`（HR 推来的新职位评估符合，只解除 `declined`；会话另有 `job_closed`、`rejected`、`risk` 时仍保持关闭）、`manual`（本人接管；须明确交还才能恢复发送，已决定事项也不能越过）、`released`（本人交还已登记会话，解除 `manual`，计数不变）、`paused`（等待本人决定，整个会话暂停，带 `decision`）、`unpaused`（解除暂停；带 `decision` 时只解除该决定，并作为该事项的完成标记，须在全部动作做完后最后写入；不带时解除全部）、`risk`（风险信号）、`limit_hit`（平台限额提示，`reason` 中注明当日已招呼数）。

`adopted` 仅登记本人明确点名交出的会话，skill 必须为 user，携带开始读取页面时的 readAt，在有效运行锁内追加。已有登记返回 duplicate，不重复追加；有未确认外发、关闭或暂停时拒绝。登记不带旧消息基线或额外跟进额度。


boundaryAt 取本地 sent 时间、adopted.readAt、released 事件时间最晚者，不能通过事件指定或推到未来；acknowledged 输出最近一次 released 的数组，未带则为空。outboundTexts 保留全部本地 sent 文字与 resume 的类型 / 时间（resume 可无 text），包括分界那条，用于页面时间区间前后 2 分钟的匹配。其他跨界活动按 followup 暂停询问，同标签同类型只认可已确认的前 count 条，不豁免后来的活动。

记 `rejected` 或 `risk` 后，`check` 会阻止再次联系该公司。

会话的结束原因叠加记录在 `threads` 的 `closedBy` 中，`closed` 显示其中最主要的一个（`declined` 以外的优先）。

会话以「平台 + 公司 + 招聘者」为键；同一 attempt 中 `pending` 时还看不到招聘者也没关系，按该 attempt 最后看到的招聘者归并。

## 去重

首次招呼的 `check` 综合以下来源，任何一项命中即不允许：

1. 停止名单：公司名或别名精确命中；名称互相包含算疑似。
2. 历史联系：所有动作（招呼、回复、跟进、发简历）建立的联系都算。`dedupScope` 为 `company`（默认）时，同一平台同一公司有阻止状态的尝试即命中；为 `job` 时比较 `jobId`，缺少 ID 时比较公司 + 职位名。同公司同职位名但 ID 不同，报告疑似重新发布。
3. 该公司的 `rejected`、`risk` 观察。

`check` 通过后，平台页面状态仍是最终依据：页面显示已联系，记 `already_contacted`，不发送。

本人在本轮指令中明确放行某家公司时，可以越过第 2 项，但不能越过停止名单、`pending` 或 `uncertain`。

## 中断恢复

### 释放与对账契约

`apply`、`followup`、`setup` 的正常结束、异常结束、经本人确认释放上一轮锁，以及 `workspace restore` 返回的收尾结果，都执行同一契约；这里只做本地对账与通知，不为恢复状态而重放平台动作。

1. **入口对账**：获取锁后、任何外发前，用不带平台 / 公司 / 招聘者筛选的 `ledger threads`，逐一对照全部 `unresolved` 与 `inbox/decisions.md`，并处理 acquire 的 `reconciled`。即使 acquire 返回空列表也不能跳过：释放成功后锁已删除，再次 release 只返回「没有锁」，不能重放上次输出；释放后、inbox 落盘前中断留下的 `uncertain` 只能靠本次对账补齐。招聘者为 `null`、尚未关联完整会话的尝试也检查，以 attemptId 为准，不按公司或招聘者猜合并。
2. **事项与通知**：按 attemptId 查找 / 沿用已有事项，统一按 [notify.md 的结果不明事项](notify.md#结果不明事项) 写入「尝试：<attemptId>」并选择通知类型；执行已决定事项产生 uncertain 时沿用原决定，不另开或重复暂停。确认没有对应事项且无历史关联疑点时，才先暂停、再写 inbox、再 `notify send --kind decision --decision <ID>`；未配置 IM 或通知失败时保留 inbox 并汇报。历史结果不明事项缺少 attemptId 时，不按公司 / 招聘者猜合并、不自动新开，列待对账报告；本人核对或明确证据唯一对应、在原事项补 ID 后才继续按原 ID 通知 / 执行，期间状态保持阻断（详见 [结果不明事项](notify.md#结果不明事项)）。只写已知的平台 / 公司 / 岗位 / 招聘者，缺失项注明未知，不编造、不打开平台补查。遗留 `pending` 仍阻断外发，报告待对账，不擅自核销其他轮次或重放尝试。
3. **释放收尾**：结束时执行 `lock release --run <runId>`，核对退出码与 `released`，按上一步处理返回的 `reconciled`，不能忽略输出。写核对事项前再对照最新 `threads.unresolved`；已经确认的终态保持不变，不因旧输出重开核对事项。释放后只做本地收尾与通知，不再持原锁重发。
4. **失败阻断**：释放失败或未确认 `released: true` 时立即停止整轮，保留锁与未完成草稿，按 `lock status` 显式报告持锁者；状态读不出就报告锁未确认释放。不能宣称完全收尾，不能继续外发、续期或沿用原锁绕过收尾。收尾中的锁按「运行锁」一节重试 release 或等过期接管；主体错误与释放错误分别报告。`threads` / `validate` 报损坏或无法读取时，报告对账未完成及损坏行，停止外发，不跳过、不删除、不用空文件覆盖。

恢复时只观察、不重放：不重复上一轮最后一次点击。执行已决定事项前先 `decision --id` 查状态与各步骤，已完成的步骤跳过，`in_progress` 只核对。

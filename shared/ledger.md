# 事件记录

`ledger/events.jsonl` 是唯一的追加式事实来源。**只通过 `scripts/ledger.py` 读写**；不整体改写、不删行。脚本路径先解析为绝对路径：

```sh
python3 <job-mate 技能目录>/scripts/ledger.py [--workspace <工作区>] <命令> ...
```

`<job-mate 技能目录>` 为本文件上两级目录。输出 JSON。退出码：0 成功，1 不允许或锁占用，2 参数、数据、版本或锁错误。遇 2 停止外发并报告。

## 命令

| 命令 | 用途 |
|---|---|
| `validate` | 校验事件与停止名单，错误包含行号 |
| `lock acquire --skill <apply\|followup\|setup> [--no-send]` | 获取本轮运行锁与 runId；输出 paused、接管的 reconciled |
| `lock renew --run <runId>` | 长时间读取时续锁；失败立即停止 |
| `lock release --run <runId>` / `lock status` | 释放并对账 / 只读持锁者 |
| `pause on --reason <原因> [--by <谁>]` / `pause off` / `pause status` | 设置 / 解除 / 查询全局暂停 |
| `check --run <runId> --json '<意图>'` | 与 prepare 同路径的只读完整预检，不消费额度 |
| `prepare --run <runId> --json '<意图>'` | 唯一 pending 入口；全部门禁通过才原子写入，协议见[发送准备](sending.md) |
| `append --json '<事件>'` | 记录终态、观察及筛选；禁止直接写动作 pending |
| `tail --run <runId> --thread '<threadKey>' --read-at <ISO> [--base <tailId>] [--json '<输入>']` | 记录尾部指纹，不保存原文；输入也可走 stdin |
| `guard --run <runId> --attempt <attemptId>` | 每个可能外发的点击前复查 pending 与有效锁，并续期；通过后立即点击 |
| `decision --id <ID> [--step <编号>]` | 查询有效版本、各版本步骤及真实 attempts |
| `threads` | 会话阶段、职位信息、待处理尾部、决定及原有状态；可按 platform / company / recruiter 筛选，不是页面证据 |
| `quota --platform <平台> [--run <runId>]` / `stats` / `export` | 配额、统计、[导出](export.md) |
| `stoplist list` / `stoplist add --name <公司> [--alias <别名>]... --reason <原因> --source <来源>` / `stoplist remove --name <公司>` | 停止名单；remove 按 name 精确匹配 |

派生见[说明](derived.md)，账号与按平台配置见[账号说明](accounts.md)。

## 运行锁

同一工作区只运行一个技能；锁默认 60 分钟。版本与恢复锁见[套件版本准入](workspace.md#套件版本准入)。`lock acquire --skill setup --recovery` 只做恢复与释放，不能用于普通写入、tail、进度、通知或续期。

- 新建 setup 未完成或草稿模式读不出时，apply / followup 取锁（包括沿用 runId）返回 1；重新设置、部分重新设置草稿不阻断当前配置。
- 新外发 pending 与 guard 必须是当前有效、未 releasing、未禁发的持锁者，全局暂停时拒绝；pending / guard 自动续期。正常等待或页面变化后重读再 guard；工具超时按[停止范围](execution.md#结果不明时的停止范围)收尾，不新建尝试绕过失锁。
- screen 和 tail 也须有效的普通运行锁；禁发或暂停时仍可记本地观察，不因此获得外发授权。adopted 另要求本人授权，禁发 / 暂停时拒绝。
- 终态与事实观察不要求运行锁；恢复锁下仍受写入门禁限制。
- 释放 / 接管只核销该轮 pending，返回 reconciled；不改其他轮次或已有终态。按[释放与对账契约](#释放与对账契约)写 inbox，脚本不代写。
- 释放先写 releasing / releaseAttempts，核销完才删锁。中断保留锁并禁止外发、续期、沿用；重试同一 release 或等过期接管。损坏记录先人工处理。
- 上一轮崩溃默认等过期；只有本人确认上一轮已停止，才可显式释放上一轮 runId。

## 事件格式 v1

`schemaVersion` 固定为 1，不识别旧事件格式。`append` 补 at、该平台唯一 account、空的页面字段，并生成每条唯一的 `eventId: evt-<12位十六进制>`；调用方自带 eventId 报错。不要把脚本输出的完整事件直接作为下一条输入：终态复用 attemptId，不复用 eventId。

| 字段 | 规则 |
|---|---|
| platform、account、runId、skill | 非空；skill 为 apply / followup / setup / user，本人核对记 user |
| company、title、location、recruiter | 页面原文，不可见为 null；招聘者格式按平台描述 |
| threadKey、jobKey | 脚本按平台描述派生，不能派生则 null；自带值须与派生结果完全一致 |
| targetRole | 匹配的目标方向；未知为 null |
| reason、evidence | 非空理由 / 结果依据，依据未知为 null；不把 HR 聊天全文写入 |
| attemptId、status | 动作按同一 attemptId 追加状态；新独立尝试自动生成 attemptId |
| decision、step | 事项 ID 如 D-20261104-01；外发步骤如 reply1、resume1，step 须与 decision 同用 |
| text、resume、tags | 我方文字 / 简历版本与附件 / 字符串标签数组；本人批准的文字保留全部空白与 Unicode，不改写 |
| profileRev、styleRev | 配置规范 JSON 的 SHA-256 前 12 位；由发送门禁填写 |

有 jobUrl 时按平台的 idPattern / stripQuery 规范化、提取 jobId。jobKey 有 ID 为 `平台|jobId`，否则为 `平台|规范化公司|规范化职位名`；threadKey 为 `平台|账号|平台 conversationKey 各规范化字段`，分量内的竖线转为全角「｜」。不猜职位 ID，不记录凭据、证件或完整简历。

kind：
- `screen`：必填 result（qualified / pending / excluded）、reasons（字符串数组）；可选 jdFacts、checks、salaryText。有效普通锁内记录，不是外发。
- `greet`、`intro`、`reply`、`nudge`、`resume`：动作，必填 status，可带 asks；jdFacts / checks 仅 greet 可带。
- `observe`：必填 observed，见下节。

信息子项：salary.range、salary.months、hours.schedule、hours.time、location.address、leave.annual、employment.type。jdFacts / facts 为子项到字符串的对象；checks / asks 为不重复的子项数组。大类如 salary 不能代替子项。

## 动作状态与防重

新尝试只可 pending，或独立 skipped / already_contacted。pending → sent / uncertain / failed_no_send；uncertain → sent / failed_no_send、failed_no_send → sent 仅限本人核对（skill: user）。其他终态不再追加；已有 sent 始终视为已发出。同一尝试的原非 null 身份冻结；不可改 kind、platform、account、company、recruiter、title、jobId、jobKey、threadKey。平台、账号、原始职位ID与派生键精确比较，人类公司/职位/招聘者按规范化比较。原 null 只能补一次，派生键也仅能随合法 null 补齐；既有非空 fallback jobKey 不自动升级到另一 ID。终态省略的已知身份由脚本继承，显式 null 不能清除；冲突退出2，转本人核对。

- 每次可能发送前先用 prepare 登记 pending，成功后才操作；每个可能外发的动作分别 guard。结果回填同一 attemptId。
- sent 须有当前业务成功证据；进入会话、按钮变色、工具成功、空白页或「没看到消息」均不能代替证据。超时或未知记 uncertain，不重放。
- 本人核对 uncertain 后，Agent 代为回填 sent 或 failed_no_send，skill 记 user、reason 引用本人结论；Agent 自己看页面不能冒充本人。
- pending / uncertain / sent / already_contacted 阻止重复首次联系。failed_no_send 仅本轮不重试，skipped 不阻断。招呼额度按写过 pending 的 greet 尝试计数，失败与不明不退款；间隔按最近 pending / sent 计。
- 决定以有效版本为准，旧未决仍须核对。步骤状态、收尾及本人核对分支见上述派生说明，不能用完成标记隐藏未决。

## 观察与决定

observed 可取：tail、handled、replied、decision、info、resume_requested、resume_delivered、resume_viewed、interview_invited、interview_decided、silent、declined、reopened、rejected、job_closed、paused、unpaused、manual、released、adopted、risk、limit_hit。

- info 必须 facts 与可派生的 jobKey；信息归属职位，不能套用另一职位结论。
- decision 必须事项 ID、连续正整数 version（现有最大值加一）、steps 数组。每步 `{step,kind,text}` 或 `{step,kind:resume,resume}`，内容非空，二者恰一；步骤名在本版本及历史版本中均不重复，批准原文原样保存。
- interview_invited 必须 decision 与完整 threadKey，后续 paused / ack 共用事项 ID；它不是本人决定。
- paused 等待该事项，unpaused 仅解除同 ID；不带 ID 则解除全部。manual 只由本人交还 released 解除，交还不重置跟进计数。declined 可由 reopened 解除；rejected / risk / job_closed 不随重新评估解除。结束原因叠加在 closedBy，closed 优先显示 declined 以外的原因。
- adopted 仅本人点名交出会话，skill: user，带不晚于事件时间的 readAt，在有效锁内写入；已有登记返回 duplicate，有未决、关闭、暂停时拒绝。
- boundaryAt 取 sent、adopted.readAt、released 时间的最大值，不允许调用方指定或推到未来。outboundTexts 保留全部本地 sent 文字和 resume 类型 / 时间，包括分界那条，供页面区间前后 2 分钟核对。
- released 可带 acknowledged：每项仅 shown、precision（day / minute）、kind（text / resume / contact / interview）、正整数 count。shown 按平台词表和 readAt（缺省 at）换算为绝对 `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM`；孤立时分不猜日期，不记原文。相同绝对时间同类型从早到晚前 count 条可豁免，超出仍核对，明确晚于分界的新活动不豁免。

## 尾部读取与处理

读取、比较、复核或 handled 前，读[尾部核对协议](tails.md)。账号歧义处理见[账号说明](accounts.md)。
## 去重

首次联系检查停止名单、同平台账号历史联系与 rejected / risk。company 范围按公司，job 范围按 ID，缺 ID 比公司 + 职位；同公司同职位而 ID 不同报疑似重新发布。通过 check 后仍以当前页面为准：已联系记 already_contacted，不发送。本人逐个放行仅经绑定单职位的 dedup_once；它不是发送授权，不能越过其他门禁。

uncertain 的停止范围与来源字段见[统一执行规则](execution.md#结果不明时的停止范围)：业务标 business，工具/环境标 tool，未说明保守按 tool；自动锁收尾的 reconcile / reconcileProof 只能由脚本生成并校验原 pending，不靠 reason 文本区分。

## 发送门禁原因码

prepare / check 拒绝时为 `allowed:false,reasons:[{code,message}]`，退出1；参数或损坏数据退出2。间隔拒绝另给 retryAfterSeconds。完整授权、绑定与计数见[发送准备](sending.md)。

- 本轮与授权：not_authorized、grant_out_of_scope、grant_exhausted、lock_invalid、no_send、run_kind_restricted、run_scope_restricted、run_limit_exhausted、recovery_lock_only。
- 全局与文字：paused、stoplist、stoplist_suspect、interval、empty_text、banned_word、too_long、run_uncertain_limit。
- 首次联系：already_contacted、company_rejected、company_risk、reposted_suspect、quota_run、quota_day、outside_hours。
- 会话：thread_closed、thread_manual、thread_paused、unresolved。
- 决定：decision_not_registered、decision_superseded、decision_unresolved、decision_text_mismatch、decision_step_done、decision_needs_review。
- 系统回执：ack_invalid_invitation、ack_duplicate、ack_contains_time。

## 中断恢复

### 释放与对账契约

`apply`、`followup`、`setup` 的正常结束、异常结束、经本人确认释放上一轮锁，以及 `workspace restore` 返回的收尾结果，都执行同一契约；这里只做本地对账与通知，不为恢复状态而重放平台动作。

1. **入口对账**：获取锁后、任何外发前，用不带平台 / 账号 / 公司 / 招聘者筛选的 `ledger threads`，逐一对照全部 `unresolved` 与 `inbox/decisions.md`，并处理 acquire 的 `reconciled`。即使 acquire 返回空列表也不能跳过：释放成功后锁已删除，再次 release 只返回「没有锁」，不能重放上次输出；释放后、inbox 落盘前中断留下的 `uncertain` 只能靠本次对账补齐。招聘者为 `null` 也检查，以平台、账号、attemptId 为准，不按姓名猜合并。
2. **事项与通知**：按平台、账号、attemptId 查找 / 沿用已有事项，统一按 [notify.md 的结果不明事项](notify.md#结果不明事项) 写入「尝试：<attemptId>」并选择通知类型；执行已决定事项产生 uncertain 时沿用原决定，不另开或重复暂停。确认没有对应事项且无历史关联疑点时，才先暂停、再写 inbox、再 `notify send --kind decision --decision <ID>`；未配置 IM 或通知失败时保留 inbox 并汇报。历史结果不明事项缺少 attemptId 时，不按公司 / 招聘者猜合并、不自动新开，列待对账报告；本人核对或明确证据唯一对应、在原事项补 ID 后才继续按原 ID 通知 / 执行，期间状态保持阻断（详见 [结果不明事项](notify.md#结果不明事项)）。只写已知的平台 / 公司 / 岗位 / 招聘者，缺失项注明未知，不编造、不打开平台补查。遗留 `pending` 仍阻断外发，报告待对账，不擅自核销其他轮次或重放尝试。
3. **释放收尾**：结束时执行 `lock release --run <runId>`，核对退出码与 `released`，按上一步处理返回的 `reconciled`，不能忽略输出。写核对事项前再对照最新 `threads.unresolved`；已经确认的终态保持不变，不因旧输出重开核对事项。释放后只做本地收尾与通知，不再持原锁重发。
4. **失败阻断**：释放失败或未确认 `released: true` 时立即停止整轮，保留锁与未完成草稿，按 `lock status` 显式报告持锁者；状态读不出就报告锁未确认释放。不能宣称完全收尾，不能继续外发、续期或沿用原锁绕过收尾。收尾中的锁按「运行锁」一节重试 release 或等过期接管；主体错误与释放错误分别报告。`threads` / `validate` 报损坏或无法读取时，报告对账未完成及损坏行，停止外发，不跳过、不删除、不用空文件覆盖。

恢复时只观察、不重放：不重复上一轮最后一次点击。执行决定前先查 `decision --id` 的有效版本与步骤；sent 跳过，superseded 不执行，pending / uncertain / needs_review 只核对。

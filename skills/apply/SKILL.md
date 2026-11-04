---
name: apply
description: 在招聘平台（首批 BOSS 直聘）通过浏览器搜索职位、读详情、按工作区条件筛选去重，授权时发起首次沟通并记录。当用户说“帮我投”“打招呼”“按条件投 5 个”“找职位”“筛选职位”“先看看有什么岗位”，或定时任务要求执行一轮投递时使用；只找职位时不发送。不负责回复 HR、发简历、跟进（用 followup），也不负责建立偏好（用 setup）。
---

# apply：筛选职位并发起首次沟通

完成「定位工作区 → 确定模式 → 搜索 → 核对详情 → 去重 → 首次沟通 → 记录 → 汇报」。授权明确、条件齐全时直接执行，不逐个职位请求批准。

**完成边界是首次沟通。** 即使 HR 当场回复，也不继续聊天、不发简历、不交换联系方式；这些交给 `followup`。汇报用「已发起沟通」，不说「简历已投递」。

本文件引用的路径相对于本文件所在目录。开始前阅读：

- [../../shared/workspace.md](../../shared/workspace.md)：工作区、`profile.json`、停止名单
- [../../shared/ledger.md](../../shared/ledger.md)：事件记录与 `ledger.py`
- [../../shared/safety.md](../../shared/safety.md)：安全边界
- [platforms/boss.md](platforms/boss.md)：BOSS 直聘页面细节
- [../../shared/execution.md](../../shared/execution.md)：所有 runtime 的能力自检、完整读取、输入保护、逐动作 guard 与停止收尾；准备浏览器时另读 [浏览器入口](../../shared/browser/README.md)

下文的 `ledger`、`notify` 分别指 `python3 <本文件上两级目录>/scripts/ledger.py` 和 `.../scripts/notify.py`，先解析成绝对路径再调用。

**事项先写 inbox，再排队通知**（见 [../../shared/notify.md](../../shared/notify.md)）：「待决定」用 `notify send --kind decision --decision <ID>`，「待处理」用 `notify send --kind todo --decision <ID>`。send 只排队（queued），不是已发送；按通知契约由 runtime `outbox --claim` → 核对冻结路由、正文、token、原幂等键及适用截止 → 投递 → `receipt`，handoff 未收口不重投，unknown 不自动重发。没有 IM 能力不另找通道，保留 inbox 与队列并报告。`apply` 不等待答复，本人的答复由 `followup` 或 runtime 调度处理。

## 1. 定位工作区，读取条件

1. 按 workspace.md 定位工作区，告知绝对路径。找不到时只能只筛选，条件取自本轮指令；需要发送时请本人先运行 `setup`。
2. 运行 `ledger validate`。返回错误时可以只筛选，不能发送。
3. 读取 `profile.json`、`rules.md`、`candidate.md`。

条件缺失时：只追问会影响本轮决定的项，例如没有目标职位或城市。完整的偏好访谈交给 `setup`，这里不展开。

本轮指令覆盖已保存条件。本人说「以后」「改成」的写回 `profile.json`；「这次」只用于本轮；没说明的按本轮处理，并在汇报中注明。

## 2. 确定模式

按本轮指令的完整语义判断，否定和限制优先于关键词：

| 本轮指令 | 模式 |
|---|---|
| 「找职位」「筛选一下」「先给我看看」「不要发消息」 | 只筛选 |
| 「帮我投」「打招呼」「按条件投 5 个」「筛完直接投」 | 发起沟通 |
| 定时任务触发，未另作说明 | `authorization.apply.send` 为 `true` 时发起沟通，否则只筛选 |
| 「找工作」等未说明是否发送 | `authorization.apply.send` 为 `true` 时发起沟通，否则只筛选并在汇报中说明 |
| 「停止」「先别投了」 | 停止后续发送，记录已发生的动作并汇报 |

`authorization.apply.send` 为 `false` 而本人本轮明确要求发送时，本轮照做，不写回。

**获取运行锁之前**，所有 runtime 先按 [加锁前能力自检](../../shared/execution.md#1-加锁前能力自检) 校验 `profile.browser` 与读写工作区、运行核心脚本、可见浏览器操作及结果证据能力。不支持选定偏好或配置非法时不操作平台、不回退；缺少外发证据用 `--no-send` 锁。不能读写工作区或运行核心脚本时不写记录、不外发，只基于已有可读资料筛选并汇报。不能操作平台而仍需本地对账时仅用禁发锁，按 [环境受阻与恢复](../../shared/execution.md#7-环境受阻通知与恢复) 收尾。

**两种模式都要获取运行锁**，避免与 `followup` 同时操作浏览器：只筛选时 `ledger lock acquire --skill apply --no-send`，发起沟通时 `ledger lock acquire --skill apply`。记下返回的 `runId`。被占用时说明占用者并结束本轮。输出的 `paused` 不为空表示已全局暂停：本轮不浏览平台，仅本地对账与汇报。运行锁不是执行器强单实例，runtime 须另行排除旧实例。持锁后、准备浏览器前，执行 [ledger.md 的释放与对账契约](../../shared/ledger.md#释放与对账契约) 的入口对账：即使 acquire 的 `reconciled` 为空，也用完整 `ledger threads` 的 `unresolved` 与 inbox 对账。没有工作区时无法加锁，只筛选并在汇报中说明。

**进入发起沟通模式前，以下条件都满足：**

- 有工作区，`ledger validate` 通过，已持有非禁发的运行锁。
- `ledger quota --platform boss --run <runId>` 返回 `canGreet: true`。当日额度用完则本轮只筛选，汇报「今日招呼已达上限」。
- 当前时间在 `schedule.activeHours` 内；不在时段内只筛选。本人本轮明确要求的单轮可例外。
- 招呼语方式已确定（`greeting.mode`，缺省为 `dynamic`）。

## 3. 准备浏览器

按 browser/README.md 选择控制方式，确认当前网站、账号与 `job-mate.json` 中的 `displayName` 一致、页面可读。需要登录时请本人自己扫码或验证。

没有可用的浏览器操作能力时，说明缺少什么，不把「可以自动化」当成已执行。

## 4. 搜索、核对、排序

1. 按 `targetRoles` × `locations` 的优先级组成有限的搜索组合，通过页面搜索框和筛选控件操作，不硬编码城市码或接口。
2. 列表靠滚动加载更多；滚到列表末尾或预算用完即止。
3. 读详情计入 `limits.detailsPerRun`（默认 50 个不同职位），用完即停止搜索。
4. 每个候选记录：职位链接（可读到时）、职位名、公司、地点、薪资原文、关键要求、招聘者（可见时）、**招聘者活跃标签**，以及逐项匹配依据。`dynamic` 方式下同时记下可用于招呼语的 1–2 个 JD 关键点。
5. 按详情中的实际职责判断，职位名称只作参考：同一名称（如 FDE、AI 应用工程师）职责差异很大，不因名称排除，也不因名称保留，对照 `targetRoles` 和排除条件看职责。薪资按 `salary` 的币种、周期、口径和 `matchRule` 比较；多薪、奖金、日薪不能直接当月薪；「面议」或缺少换算依据时标为未知。
6. 条件分两类判断：
   - **正向条件须从 JD 证实**：职责符合 `targetRoles`、实际工作地点在 `locations` 内、薪资满足 `salary`、`hardRequirements`。任何一项无法确认（如「面议」、未标薪、地址不明）列为「待确认」，不自动联系。
   - **排除条件明确命中才排除**：`exclusions` 中的职位类型、用工形式、工作制度、关键词，以及停止名单。JD 未写明的（如没写单双休、没说是否外包）可以打招呼，在 `reason` 中记「待沟通核实：<哪几项>」；不能因为没看到「外包」就断言「非外包」。
7. **招聘者活跃度是硬条件**：按 boss.md 把活跃标签换算成天数，超过 `recruiterActivity.maxAgeDays`（缺省 7）的剔除，原因写「招聘者<标签>」；看不到活跃标签的列为「待确认」。读详情前若列表已显示活跃标签，可先据此跳过，不占读详情预算。
8. 用 `ledger check` 标出已联系、停止名单命中的职位（只筛选模式也标，方便本人判断）。
9. 合格候选先按招聘者活跃度排序（越近越前），同一活跃档内再按 `targetRoles`、`locations` 的顺序和 `rules.md` 中的偏好排序，不用无法解释的匹配分数。不为凑数量放宽条件。

只筛选模式到此汇报（第 7 节）。

## 5. 逐个发起首次沟通

一个职位完成闭环（终态已落盘）后才处理下一个。

### 5.1 发送前检查

1. 运行超过 30 分钟时先 `ledger lock renew --run <runId>`；失败说明已失去运行锁，立即停止。`ledger quota --platform boss --run <runId>`：`canGreet` 为 `false` 时结束本轮；`waitSeconds` 大于 0 时先等待这么久。这是固定的最小间隔，不另加随机延时。
2. `ledger check --platform boss --company <公司> --title <职位> --job-url <链接> --run <runId>`：不允许时记 `skipped`（`reason` 写 check 返回的原因），处理下一个。
3. 重新确认详情仍符合硬条件、职位开放，目标公司和招聘者正确。
4. 按 boss.md 判断沟通按钮：显示已联系时追加 `already_contacted`，不点击。

### 5.2 写招呼语

点击前先写好本职位的招呼语，点击后只需输入和核对。

- `greeting.mode` 为 `dynamic`（默认）：针对该职位写，不超过 80 字。
  - 结构：JD 中的 1–2 个关键点（职责、技术、行业）+ `candidate.md` 中对应的真实经历或数字 + 一个提问或下一步。
  - 只写 `candidate.md` 有依据的事实；不报薪资，不用「精通」等无法验证的说法；称呼对方公司用「贵公司」。
  - 找不到明确对应点时，以 `greeting.text` 为基础改写。
  - 例（虚构事实，仅示意格式，不可直接当作本人经历）：「您好，看到岗位涉及订单流程，我有 3 年订单管理系统开发经验。想了解下团队现在的技术栈？」
- `greeting.mode` 为 `static`：使用 `greeting.text` 原文。

### 5.3 发送与确认

所有 runtime 都按 [输入与草稿保护](../../shared/execution.md#4-输入与草稿保护)、[尝试阶段与停止前收尾](../../shared/execution.md#6-尝试阶段与停止前收尾) 执行。完整读取才下否定结论，核对会话前不读写输入框，未知草稿不清；no-send / L1 只起草时不碰外发输入框。第 4 步放弃发送例外只在入口有明确正常成功结果、环境可用且连续观察到身份一致的目标空会话时适用；调用过后续可能外发动作的，还要求它们全部有结构化 `not_executed` 证据。身份丢失、失败响应、超时后即使读到空会话也不适用。可能外发前逐动作 guard，不将输入与发送合并；写后读回须与最终文字完全一致。

1. `ledger append` 一条 `kind: greet, status: pending`，`text` 为拟发文字，`tags` 记 `greeting:<mode>`；记下返回的 `attemptId`。写入失败（包括已失去运行锁、本轮禁发）立即停止整轮，不点击。
2. `ledger guard --run <runId> --attempt <attemptId>` 返回 `allowed: true` 后**立即**点击首次沟通入口，中间不做别的事；等待并观察结果，不连点。按 boss.md 判断出现的是哪种情况：

| 点击后 | 处理 |
|---|---|
| 进入会话，输入框可用，会话中没有我方消息（平台自动打招呼已关闭） | 核对会话标题中的公司、招聘者与目标一致；输入招呼语，单独核对完整文字；再 `guard` 一次，通过后立即点发送；按第 3 步确认 |
| 平台自动发出了账号默认招呼语（自动打招呼仍开启） | 按第 3 步把这条记为 `sent`（`text` 记实际自动发出的文字），在汇报中提醒「平台自动打招呼仍开启」。`static` 到此结束；`dynamic` 按第 5.4 步补发 |
| 无法判断是否已发出 | 第 5 步 |

3. 只有出现当前目标的明确发送成功提示，或会话中出现本次新发出的消息且没有失败、发送中标志，才追加 `sent`，`evidence` 写明看到了什么，`text` 记实际发出的文字。
4. 仅在上面的正常流程放弃例外成立时，追加 `failed_no_send`，`evidence` 写明目标身份、完整空会话证据、阶段及原因，继续下一个职位；对象不符或身份丢失不是此例外，保守按 `uncertain` 收尾。
5. 等待后仍无法确认：追加 `uncertain`，**停止本轮发送**，按[结果不明事项](../../shared/notify.md#结果不明事项)写入 `inbox/decisions.md`（含 attemptId、去重及旧事项待对账），请本人核对。
6. 明确证明没有发出（如页面显示发送失败）：追加 `failed_no_send`，本轮不重试。

`guard` 返回失败，说明本轮已失去运行锁，或这次尝试已被接管的一轮改记为 `uncertain`：**立即停止整轮**，不点击，也不为同一职位新建尝试。中途有过等待、卡顿或工具超时，点击前先重新读页面，再 `guard`。

每个写入 `pending` 的职位都占用一次额度，失败或结果不明也不补量。

### 5.4 补发动态招呼语（仅在平台仍自动打招呼时）

只在第 5.3 步记为 `sent` 且那条是平台自动发出的默认招呼语时补发。

1. 第 5.2 步写好的招呼语不要重复默认招呼语已说过的内容，必要时调整。
2. 先按 [输入与草稿保护](../../shared/execution.md#4-输入与草稿保护) 核对会话、接管与空输入框（未知内容不清空、不建新尝试），重做 `ledger check --kind intro`（带目标公司、招聘者与 run），再 `ledger append` 一条 `kind: intro, status: pending`（新的 `attemptId`，`tags` 记 `greeting:dynamic`），在同一会话中输入、读回核对，`guard` 通过后立即发送。
3. 按第 5.3 步第 3 步确认，追加 `sent`、`uncertain` 或 `failed_no_send`。结果不明时不重发，记 `uncertain`，**停止整轮**并按[结果不明事项](../../shared/notify.md#结果不明事项)写入 inbox，与第 5.3 步一致。

补发不另占招呼额度。

## 6. 异常与停止

| 情况 | 处理 |
|---|---|
| 验证码、安全验证、访问限制、沟通额度提示 | 立即停止整轮，额度提示另记 `observe/limit_hit`（`reason` 注明当日已招呼数）；验证码、安全验证、访问限制另执行 `ledger pause on --by apply --reason <原因>`；写入 inbox「待处理」并 `notify send --kind todo` |
| 登录失效、账号不符 | 停止；写入 inbox「待处理」，`ledger pause on --by apply --reason <原因>`，`notify send --kind todo`，请本人登录或确认账号后回「恢复」 |
| 页面空白、异常跳转 | 按 browser/README.md 处理，不刷新重试 |
| 职位已关闭、按钮不可用 | 记 `skipped` 及依据，继续下一个 |
| 无法辨认收件人或发送状态 | 停止发送并汇报，不猜按钮位置 |
| `ledger` 返回退出码 2 | 立即停止发送；如果此前可能已发送，报告该目标待人工核对 |
| 本人喊停、额度或预算用完、候选耗尽 | 保存进度并汇报，不自动开始下一批 |

任何 runtime 无论如何结束，都先按 [尝试阶段与停止前收尾](../../shared/execution.md#6-尝试阶段与停止前收尾) 给本轮所有未终结尝试收尾：已有终态不覆盖、明确成功证据优先记 sent；读取出错也不能遗漏 pending。unknown 或环境受阻后结束整轮，不再新建尝试，不靠同一 runId 重取锁降级（原锁 noSend 不会改变）。然后执行 `ledger lock release --run <runId>`，按 [释放与对账契约](../../shared/ledger.md#释放与对账契约) 核对 `released`、`reconciled` 与释放失败，按 attemptId 对照最新 unresolved 和 inbox、保留原决定 ID，释放后不再外发。

**结果不明（`uncertain`）的招呼**统一按[结果不明事项](../../shared/notify.md#结果不明事项)写入 inbox，记录 attemptId、沿用事项并处理旧事项关联，核对选项为「已发出 / 未发出」。本人确认后，由 Agent 代为追加同一 `attemptId` 的 `sent` 或 `failed_no_send`，`skill` 记 `user`，`reason` 引用本人原话。

**运行锁被上一轮占着**（上一轮崩溃没有释放）时，默认等它过期。本人确认上一轮已经停止，可以让 Agent 执行 `ledger lock release --run <上一轮 runId>`，同样按 [释放与对账契约](../../shared/ledger.md#释放与对账契约) 处理 `reconciled` 与释放失败；Agent 不得自行判断后释放别人的锁。

## 7. 汇报

**结束前必查**：运行环境受阻且 `im.channel` 为 `none`（或 IM 送达未确认）时，按 [环境受阻与恢复](../../shared/execution.md#7-环境受阻通知与恢复)，最终答复必须包含 **原因、环境恢复办法、恢复后如何重新发起** 三项；由本人重新发起本技能。恢复后发前重做去重检查；结果待核对的事项另行说明，不能代替恢复指引或据恢复重发 uncertain。

- 本轮模式和实际使用的条件；哪些是本轮临时覆盖、哪些写回了 `profile.json`。
- 读详情数、合格数、已发起沟通数、已联系跳过数、其他跳过数、待核对数；今日已用 / 剩余招呼额度。
- 候选或已沟通职位表：公司、职位、地点、薪资原文、招聘者活跃标签、匹配理由、状态、链接。
- 因招聘者不活跃而跳过的数量。
- 未达目标数量的原因、停止原因、写入 inbox 的事项。

`pending`、`uncertain` 和历史记录不计入本轮成功数。示例结尾：「已发起沟通 6 个，1 个结果待核对（已写入 inbox），本轮已暂停；未发送简历。今日剩余招呼额度 23。」

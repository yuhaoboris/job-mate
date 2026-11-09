---
name: apply
description: 在招聘平台搜索职位、读取详情、按求职工作区条件筛选，明确授权后发起首次沟通。当用户说“帮我投”“打招呼”“按条件投几个”“找职位”“只筛选”，或定时任务要求执行一轮时使用。不回复HR、不发简历、不处理面试（用 followup），不建立偏好（用 setup）；还没有求职工作区时先用 setup。
---

# apply：筛选与首次沟通

目标是争取更多面试机会，完成边界是首次沟通，不是简历投递。条件与授权明确时直接做，不逐职位请示；数量不够不放宽条件。HR 当场回复也交给 followup。

本文件路径相对本文件所在目录。**每轮必读**：
- [工作区](../../shared/workspace.md)：定位、配置与版本准入。
- [执行契约](../../shared/execution.md)：能力、完整读取、逐动作 guard、停止范围与全部尝试收尾。
- [安全边界](../../shared/safety.md)：权限、承诺、隐私与遇限即停。
- BOSS 页面事实：[通用](../../platforms/boss/common.md)、[首次沟通](../../platforms/boss/apply.md)。其他平台先读其随包描述和页面说明，不套用 BOSS 控件。

下文 ledger / notify / im_lark.py 分别指 `python3 <本文件上两级目录>/scripts/ledger.py`、`.../scripts/notify.py`、`.../scripts/im_lark.py`，解析绝对路径后使用；Windows 用 `py -3`。命令/事件首次使用读[台账](../../shared/ledger.md)，准备外发或权限例外读[发送准备](../../shared/sending.md)，需要本人处理读[通知协议](../../shared/notify.md)。

与本人对话、提问或报告问题时，先遵守 [提问与对话](../../shared/ask.md)。

## 1. 定位工作区与本轮条件

1. 按工作区规则定位，告知绝对路径；运行 `ledger validate`。读取 profile.json、rules.md、candidate.md、stoplist.yaml。
2. 没有工作区：本人在场时，说明先花约十分钟建好工作区（这次设置不会在平台发消息），本人同意就用 setup；本人只想先看看，就按本轮说的条件只筛选，不写记录、不发送。无人值守时按[无人值守运行](../../shared/ask.md#无人值守运行)只汇报。缺目标职位、地点等决定性条件：本人在场才追问，完整访谈交 setup；无人值守时不搜索缺条件的部分，写待办并通知。数据校验失败只筛选不发送；不能读写工作区/运行脚本时不写记录。
3. 本轮临时条件不写回 profile。希望长期改变规则时记录建议，请本人通过 setup 同步；不擅自直接修改真实求职工作区，也不在工作区做任何 Git 处理。

## 2. 模式、限制与能力

按完整语义决定，不按单个“投”字猜授权。明确只看看/不发时只筛选；明确帮投/打招呼时按授权发起沟通；定时或未说明时，apply.send:true 才进入发起沟通，否则只筛选。

取锁前明确本轮动作、目标与数量。本人收窄的要求登记 `--restrict`，优先于长期授权、grant 和本人决定；缺权限且本人明确追加时，另登记范围/数量明确的 `--grant`，不把 grant 当 restriction，也不修改 profile。例：本轮只发两次，restriction 为 kinds:[greet,intro]、scope:all、max:2；apply.send:false 还需 apply_send grant。自动招呼后的 intro 也是一次外发，不能靠不同来源补量；小时数例外不能放开其他门禁。结构与冻结绑定按发送准备。

**加锁前能力自检**按执行契约：读写、核心脚本、profile.browser 偏好、可见浏览器与发送结果证据。不支持偏好或配置非法时不操作平台、不回退。缺外发证据只取禁发锁；能力不足仍可完成本地对账时用禁发模式，不能读写则不写记录。

- 只筛选：`ledger lock acquire --skill apply --no-send`。
- 发起沟通：`ledger lock acquire --skill apply`，附本轮 restrict / grant，记返回 runId。
- 被占用即结束，不自行释放别人的锁；runtime 另保证旧实例及后代停止，运行锁不是执行器单实例。
- 获取锁后先做[释放与对账契约](../../shared/ledger.md#释放与对账契约)：检查 reconciled，以及完整 threads 的所有 unresolved / inbox，不能因 reconciled 为空而跳过。paused 非空只做本地对账，不浏览平台。
- 超过30分钟处理检查点续锁；失败按执行契约收尾，不用同一 runId 重取锁降级。

## 3. 准备浏览器

按[浏览器入口](../../shared/browser/README.md)选工具，整轮保持选定偏好和方式。只操作本人日常已登录的可见浏览器，核对网站、窗口、账号 displayName；登录、扫码、解锁、验证码由本人完成。不使用 CDP / Playwright、隐藏接口、无头访问，不模拟人工、不规避检测。

`ledger quota --platform boss --run <runId>` 只用于计划节奏：额度满则只筛选，waitSeconds 为固定最小间隔，不加随机延时；不是最终发送许可。首次沟通在 schedule.activeHours 内，越时段须本人明确的 hours grant。

## 4. 搜索、读详情、记录筛选

1. 按 targetRoles × locations 优先级做有限搜索组合；页面搜索和滚动，不硬编码内部接口。详情预算按 limits.detailsPerRun，默认50个不同职位。
2. 记录职位名、公司、地点、薪资原文、链接/可见ID、招聘者及活跃标签、职责要求。不可见的 jobUrl/jobId 为 null，不从其他接口补取。按实际职责而非职位名称判断。
3. **正向条件须证实**：职责、实际地点、薪资、hardRequirements 任何一项未知，列 pending，不自动联系；薪资按币种/周期/口径比较，多薪、奖金、日薪不直接等同月薪，“面议”不猜。
4. **排除项明确命中才排除**。没写外包/单双休不等于已证实非外包/双休；未命中的核实点记 checks，不因未知排除项拒绝本来合格的职位。停止名单优先。
5. 招聘者活跃度是首次联系硬条件：按平台词表换算，超过 recruiterActivity.maxAgeDays（默认7）排除，不可见则 pending。列表能预先排除时不占详情预算。
6. `ledger screen-check --platform boss --company <公司> --title <职位>` 查看公司风险和联系去重；可见时补 --job-url/--job-id，有本轮锁才补 --run。无锁、禁发也可查，退出0的 screeningOnly:true 不代表发送许可，退出2停止发送。
7. **每个读过详情的职位都写一条 screen**，包括只筛选与待确认：`ledger append --json '<screen事件>'`，含页面身份、runId、skill:apply、result:qualified/pending/excluded、reasons、salaryText、jdFacts、checks。jdFacts 为已证实子项到原文要点的对象，checks 为待核实子项数组；如双休记 hours.schedule，不自动补 hours.time。信息键见台账，不拿大类代替子项，不记录HR全文。
8. 合格职位先按活跃度、再按职位/地点偏好和 rules 排序，不编匹配分数。已联系和疑似重新发布如实标记；逐个放行必须是独立 dedup_once，不能借此取得发送权限。

只筛选到此，按第7节收尾汇报，没有任何动作 pending。

## 5. 写招呼、准备与发送

一次职位闭环后才处理下一个。发送前重读职位仍开放、身份与硬条件仍吻合，按钮已联系则记录 already_contacted，不点击。

### 招呼语

使用 style 的 tone、address、length、bannedWords、emoji；short 至多60字、medium至多100字。dynamic 由本职位JD的1–2个要点加 candidate/selfIntro 中可证实的经历表达匹配和兴趣；不用固定模板、不捏造数字、不问薪资或连续询问信息。无明确对应点时以 greeting.text 为依据改写，static 按 greeting.text 原文。不用“精通”，公司称“贵公司”。

### 一个尝试的闭环

1. 写好最终招呼。完整意图含 kind:greet、页面身份、text、reason、targetRole、tags:greeting:<mode>，并带已收集的 jdFacts/checks。`ledger check --run <runId> --json '<完整意图>'` 可预检，但不占次数也不授权点击。
2. `ledger prepare --run <runId> --json '<同一完整意图>'` 成功才有 pending；保存返回的 attemptId。禁止 append 写动作 pending，不指定 eventId、摘要或授权来源。
3. **每个可能外发动作**前重新核对当前页面并 `ledger guard --run <runId> --attempt <attemptId>`；allowed:true 后立即执行，不把点击入口、输入和发送合并。入口可能自动发招呼，所以点击入口前也 guard。
4. 进入空会话后先核对会话和完整 chat-input。未知内容不清空、不覆盖；按执行契约处理已建尝试。正常空框才输入，单独完整读回，与最终文字逐字一致，再重新 guard 后发送。
5. 平台若自动发了默认招呼，当前尝试记录实际文字及业务成功证据为 sent，提醒本人关闭自动招呼。static 到此；dynamic 需要补发时，以同轮同完整会话已 sent 的 greet 为依据，另 prepare 一个 intro（greeting:dynamic），检查空草稿、输入、完整读回、重新 guard、发送、记终态；不重复已自动发出的内容。intro 不另占招呼额度，但占本轮外发限制。
6. 所有结果都回填同一 attemptId：明确当前目标业务成功才 sent，text 为实际文字；明确未发才 failed_no_send，本轮不重试；无法确认则 uncertain。工具成功、进入聊天、按钮变化不等于送达。未知不重发，不能换 attempt 或工具补发。

## 6. 停止与通知

停止范围**只按**[结果不明时的停止范围](../../shared/execution.md#结果不明时的停止范围)：区分工具/环境未知与业务未知，结构化 uncertaintyClass 如实写；第一、第二次业务不明的处理不在这里另定。未知状态先可靠收尾，不能用后来空会话证明此前动作未执行。

验证码、安全验证、访问限制、登录失效、账号不符、额度提示按安全规则遇限即停；除额度提示外全局暂停。只写最小证据，不绕过检测、不自动恢复浏览。

需要本人处理时先写 inbox，再 `notify send --kind todo --decision <ID>`；uncertain 按[结果不明事项](../../shared/notify.md#结果不明事项)，按平台、账号、attemptId 查找原决定 ID，不为已有事项重复建ID。通知只排队；入口对账后、处理检查点和收尾运行 `im_lark.py sync`，投递规则见[飞书适配器](../../shared/im/lark.md)。没有IM能力保留本地队列，不另找通道；apply不等待答复。

## 7. 收尾与汇报

任何结束分支先处理**本轮所有**未终结尝试：已有终态不覆盖，业务成功证据优先；读取失败也不能漏 pending。按执行契约及[释放与对账契约](../../shared/ledger.md#释放与对账契约)执行 `ledger lock release --run <runId>`，核对 released、reconciled、最新 unresolved / inbox；释放失败保留锁并如实报告，不再外发。

汇报本轮模式、临时条件、详情/合格/待确认/活跃度排除/已联系/成功/不明数量、实际停止原因和未完成目标。职位表含公司、职责、地点、薪资原文、链接与理由；pending/uncertain/历史记录不计本轮成功。用“已发起沟通”，不称已投递简历。

环境受阻而无可靠IM送达时，最终报告原因、恢复办法、恢复后如何重新发起；恢复后重做去重与状态检查，不据恢复重发 uncertain。是否发送由本轮实际限制、授权和脚本门禁共同决定。

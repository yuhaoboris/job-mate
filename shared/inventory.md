# 首次 followup 会话盘点

followup 浏览平台前读取本文件。每个平台账号首次运行开始盘点、逐批接手已有会话，当前消息不必等全列表到底；不在 setup 提问、不设开关。盘点不是外发许可，reply / nudge、本轮禁发和门禁不变。本人说「这个会话我自己来」记 manual，只有明确交还 released 才解除。

## 执行顺序

1. 先处理本地未决、待决定事项和通知。全局暂停不浏览平台、不盘点，仅可本地保存已有进度；恢复锁、失锁或页面不可读也不盘点。本轮禁发、只看看仍可只读盘点并写本地观察。
2. 查 `ledger threads` 的 inventories，按原始 platform/account 区分；not_started / in_progress 都要做，completed 不再做。本人说「重新盘点」才用 start --restart；未完的盘点直接续做，不丢弃已登记数。
3. 有效 followup 锁内 `ledger inventory start --platform boss --run <runId>`。账号可显式传 --account；缺省取当前配置账号。脚本返回 inventoryId；跨运行续做沿用它。
4. 从「全部」顶部逐批向下，最终盘点到底。只读列表时间、招聘者、公司、职务、摘要及 [送达]/[已读] 前缀，**不逐个打开会话**。全量盘点不受 inboundScanDays、连续 3 个超龄项限制；不以联系人搜索代替。定位当前点名会话可搜索，不能据此宣称盘点完成。
5. 每个身份明确的列表项用 record 登记。停止名单及疑似命中、明确拒绝 / 职位关闭、风险、草稿不接手，跳过并汇报；不清草稿。不按当前求职条件淘汰本人已在聊的会话。列表截断或身份/状态不明不能编造 active，先跳过并报告。
6. 每批先登记，再优先处理本人点名、pendingTails、当前未读；未接手目标先核实身份、草稿、停止及接管状态，再沿同一 inventory record 登记并读完整尾部，未核实不外发。in_progress 不阻塞已核实会话；历史过滤、分诊、prepare/guard 和打开预算照旧。处理后用剩余预算继续盘点。
7. 处理会话后返回列表，或下轮续做时，都从顶部重新核对、跳过已登记项，不沿用可能重排的滚动位置。列表很长、预算或锁时限将尽时 progress 记停止原因；风控遇限即停并收尾，汇报当前结果和未查范围。
8. **真实滚到底后**才 finish，reason 说明到底依据，不能为处理当前消息提前完成盘点。record/finish 不提供外发许可。

## 命令与输入

均为 `ledger inventory <action> --platform <平台> [--account <原始账号>] --run <runId>`，只用普通有效 followup 锁，--no-send 可用。

| action | 参数与效果 |
|---|---|
| start | 无记录则开始；未完成则续做；完成则返回当前状态，不重扫。本人要求重扫时加 --restart |
| record | --json '<列表项>'，也可从 stdin 读取；符合条件写 adopted，已有 adopted 返回 duplicate，不增加计数 |
| progress | --reason '<停止原因>'，记录未到底的检查点；全局暂停后也可本地保存，不读取平台 |
| finish | --reason '<到底依据>'，标记本次 completed；不能用 finish 代替实际滚动 |

列表项只有以下字段，不提交摘要原文、工具节点或猜测的职位 ID：

```json
{
  "company": "合成示例公司",
  "recruiter": "示例招聘者(HR)",
  "title": "列表可见的职务",
  "readAt": "2026-11-05T12:00:00+08:00",
  "lastActivityLabel": "2026-10-20 10:00",
  "listState": "active"
}
```

- title 可省略；company、recruiter 须足以派生完整会话身份，不能拿摘要推断缺失身份。
- readAt 是读到**该列表项**的实际时刻，不是盘点开始、最后写入或批次结束时间；不得晚于记录时间、早于本次盘点开始。
- lastActivityLabel 使用可见时间及明确日期上下文；脚本按平台词表换算日期/分钟。孤立时分不猜今天，未知为 null，仅存日期、分钟、精度，不存摘要。
- listState 为 active / draft / rejected / job_closed / risk。只有确认 active 才登记；其他返回 skipped。脚本另检查停止名单与本地状态。
- 已有 adopted 返回 duplicate；有未决、关闭或暂停仍拒绝。其他已有会话记录（apply 发起、manual、released 等）返回 already_registered，不改状态。本人点名交出仍用 skill:user 的普通 adopted。

## 派生与记录

- `observe/inventory` 是平台账号级记录，无公司、招聘者、threadKey 或 jobKey。inventoryId 由脚本生成；inventoryPhase 为 started / progress / completed，registeredCount 由对应 adopted 事实计算，只有 completed 的 reachedEnd 为 true。保留原始账号、runId 与 reason；不手工 append 或改计数。
- 盘点 adopted 为 skill:followup、adoptionSource:inventory，关联 inventoryId，带 readAt、解析后的 listActivity。只能通过 inventory record 生成，不伪造我方 sent、新 HR 或 handled。
- `threads.inventories` 给出各账号 not_started / in_progress / completed、inventoryId、registeredCount、reachedEnd、stopReason、最近检查点 runId；登记后即使中断还没 progress，计数也不会丢。
- 会话的 adoptionSource 区分 inventory / user；inventoryId、listActivity、inventoryPendingOpen 供追踪。顶层 inventoryPending 是已接手未打开的队列，按 readAt 从早到晚排序，不等于没有新消息。
- 接管分界仍是 sent、adopted.readAt、released 的最大值。盘点读取前的我方活动是历史；读取后无本地 sent 对应的我方活动仍按接管规则记 manual，跨界或不明则暂停核对。重新盘点不重写既有边界、manual 或暂停。

## 接手后的处理

优先级为本人点名 → pendingTails → 未读 → HR 最后发言 → 跟进候选。同一会话一轮只处理一次；HR 最后发言组同时补查 inventoryPending，最久未处理优先，受 inboundScanPerRun 及配额约束，报告剩余数量，不因超过 14 天而永远不打开。

首次打开盘点接手的会话补读职位信息并记 screen，只补充信息流程及停止名单/风险信号；不因当前条件不符就 declined。

**盘点时已超龄的会话**：接手前已在页面上的 HR 未答消息算历史，不代答、不进入回复分诊，可在汇报中列出供本人参考；只有接手后出现的 HR 新消息才按正常流程处理。**未超龄的会话**：接手前 HR 未答尾部仍照常分诊、代答。

先按正常尾部协议记录和复核，使用 tail / threads 的 inventoryReply：historicalIndices 只作历史、newIndices 才进入回复分诊、uncertainIndices 先核对，不把缺时间或跨分界当新消息。索引对应该 tailId 的 fingerprints；混合尾部也只处理新消息，不因后来有新 HR 就重开旧问题。历史部分按无需回复处理，经复核 settled 后正常 handled，不伪造新 HR。

`nudge.existingMaxAgeDays` 缺省 30，正整数，支持普通 nudge 平台覆盖。年龄按盘点时的 readAt 与列表最后活动时间比较，不随现在时间逐轮增长。整段时间区间早于阈值的只接手，不进 canNudge 候选，prepare / guard 同样拒绝（inventory_stale）。时间未知时只提供待页面核验候选，最终门禁用实际读取的我方活动时间核验，不猜时间。

inventoryReply.ready 只在尾部完整、无歧义且新 HR 身份已有有效 replyProof 时为 true；自动 reply / resume（含系统 ack）在 prepare / guard 都检查此状态，历史或未确认消息返回 inventory_history。此限制不代替本人明确批准事项的既有决定门禁。

确认**接手之后**的 HR 新回复后恢复正常会话规则；仍须我方最后发言、等待时长、次数及页面核验。首次打开才读到的接手前 HR 历史不解除保护；只认可已校验 replyProof 中的时间或接手后基准证明，不用 handled、released 或伪造 hrAt 代替。跟进次数从接手后实际尝试计算，不把盘点当跟进或回复重置。

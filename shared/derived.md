# 会话与决定派生

运行 `ledger threads` 或 `ledger decision --id <事项ID>` 时读取本文件。两者只读，不构成外发授权；遇数据校验错误停止，不用不完整输出判断已完成。接口只描述当前事实，不代替页面核对或后续发送门禁。

## 会话

`threads` 返回 `threads` 与 `pendingTails`；platform / company / recruiter 筛选同时作用于两者。恢复时仍须查询不带筛选的完整结果。每个会话保留原有接管、暂停、关闭及未决尝试字段，并新增：

| 字段 | 含义 |
|---|---|
| threadKey、account | 平台描述派生的会话标识与账号；当前配置与历史身份见[账号说明](accounts.md) |
| currentJobKey | 该会话最近一条非空 jobKey；HR 换职位后据此重算信息 |
| stage | greeted → replied → info → resume → interview_invited，只升不降；没有达到阶段时为 null |
| info | 当前职位的 known、pending、askedUnanswered、gaveUp |
| pendingTail | 最新读取尚无对应 handled 时的快照摘要；否则 null |
| lastHandled | 最近 handled 的 tailId、at、listMark，以及已处理完整快照的 readAt、complete |
| lastOpenedAt | 最新 tail 的 readAt，仅供展示，不用于延长跳过核对的周期 |
| interviewIntentStages | 已确认 sent 且带 intent:interview 标签时的阶段集合 |
| decisions | 该会话相关事项的完整版本与步骤状态，含其他目标的同事项阻断 |

阶段依据事实：确认外发达到 greeted，HR 回复达到 replied，得到结构化信息达到 info，发送/送达/查看简历达到 resume，登记邀约达到 interview_invited。失败或未确认外发不会提升阶段；后来的低阶段事件、新职位、关闭或交还不会降低历史阶段。面试意愿按首次 sent 记录时的阶段去重；仅准备或结果不明不算表达过。

### 信息

按平台、账号、jobKey 汇总 JD 与回答；不同命名空间不共用事实或问询次数。screen 尚未看到招聘者时记录的 JD 仍归属于该职位。

- known：jdFacts 与 observe/info.facts 的并集；沟通事实覆盖 JD，同类记录取后写值。不从自由文本猜子项。
- pending：checks 子项优先，其余按 inquiry.priority 的大类与组内顺序；移除 known 和 gaveUp。缺省大类为 salary、hours、location、leave；显式空数组仅保留 checks。JD 写了 hours.schedule 不代表 hours.time 已知。
- askedUnanswered：每项已确认问过、其后有 HR 回复但仍未知的次数。一个 sent attempt 最多算一次，不按 HR 消息条数累计；已知 hrAt 早于问询的历史回复不计。replied、info、resume_requested、interview_invited 都可确认后续回应；平台简历查看状态、未处理的 tail 不替代已分诊回复。
- gaveUp：上述次数至少两次的未知子项，从 pending 移除，面试时交本人了解；之后得到明确事实则不再列为未答。

asks / tags 可只登记在 pending，确认 sent 后才使用；同职位的显式终态值覆盖准备值，空数组表示实际未问/未表达。失败、pending、uncertain 不计问询；后来本人核对为 sent 时，只消费确认记录之后的回复，不猜此前回复针对哪条问题。省略职位的问询承接首次尝试时的会话职位，晚确认不挪到新职位；不同非空 jobKey 不自动合并。

### 尾部与扫描

pendingTail 包含 tailId、baseTailId、result、newCount、readAt、reason、settled、reviewRequired、complete、anchorUnique 和 reviewReasons。顶层 pendingTails 汇总仍有 pendingTail 的会话。

- 即使 result:same 或 settled:true，没有对应最新读取 ID 的 handled 仍然 pending；不按内容或比较结果自行消除。
- reviewReasons 保留本人核对收口前的阻断原因，最新 reason:same 不会遮住先前的撤回、桥接遗漏或未决发送。具体准入仍由[尾部安全判定](ledger.md#尾部读取与处理)统一校验。
- 跳过扫描须无 pending、无未读、列表标记与 lastHandled.listMark 相同，且已处理完整快照的 lastHandled.readAt 在核对周期内。不能使用 lastOpenedAt 或 handled 写入时间 at 延期；本接口不执行扫描。

## 决定

`decision --id` 与会话中的 decisions 使用同一派生结果：

- effectiveVersion：最新登记版本；无外发版本登记为 null。
- versions：每项含 version、active、steps。每步保留批准的 kind 与 text / resume，附 status 与 attempts；原文不 trim、不改 Unicode。
- steps：所有已登记步骤名到状态的索引；步骤名在同一事项的所有版本中唯一，不跨事项合并。
- attempts：实际尝试状态及只读归属 decisionVersion、versionInvalidated、mappingIssue；保留同一步骤的每个 attempt。unmappedAttempts 单列不能安全归版的尝试，systemAttempts 单列已登记邀约的 ack。

步骤状态为 sent、pending、uncertain、failed_no_send、not_started、superseded。旧版本中未开始或明确未发的步骤为 superseded；已 sent 不降级，pending / uncertain 保持真实状态，版本 active:false、尝试 versionInvalidated:true。即使同一步已有一次 sent，另一个 attempt 的未决也必须单列并阻断整个事项。

当前尚无 prepare 冻结版本：缺 decisionVersion 时，只有在首次尝试之前已登记且唯一的 step 才能只读归版。迟绑定、未登记、事项/版本/目标/内容冲突进入 needs_review，不猜成最新版，不回写推断。已登记同目标邀约的 reply/ack 是系统动作，不当成本人批准的版本步骤；其未决仍阻断事项。

### 执行与收尾

1. 先看 state。任一版本或系统尝试仍 pending / uncertain，或存在未核对的归属问题，都是 needs_review；不新增外发，先核对原 attempt。
2. 只执行有效版本的 not_started；sent 跳过，superseded 不执行；failed_no_send 本轮不重试。`--step` 的 stepState 使用这些状态，无法安全执行时返回 needs_review。
3. 旧版本的完成标记不结束新版本。全部执行后写同 ID 的 unpaused，且所有目标均解除该事项暂停，才收尾；晚于完成标记的新尝试或核对结果需要重新收尾。
4. 本人确认缺步骤等历史记录已全部处理时，可沿现有契约记 skill:user 的 unpaused，reason 引用本人结论。completionConfirmedByUser 表示当前收尾确认；已核对的未归版终态仍展示，并以 reviewResolvedBy 引用同目标、晚于该尝试最后记录的本人确认，不因后来登记新版再次阻断。别的目标的解除不代替确认；pending / uncertain 必须先在原 attempt 回填结果。

state 为 not_started / partial / failed / needs_review / done。不要只看某个步骤 sent 或某个会话解除暂停，就声称整个事项 done。`--platform` 过滤不会隐藏该事项其他目标的未决或暂停。

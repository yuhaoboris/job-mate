# IM 条件修改与分项处理

followup 解释普通答复、收到条件修改或混合答复，以及 setup 执行该差异时读本文件。普通事项执行仍按[执行本人决定](decision-exec.md)，提交与恢复按[配置事务](config-transaction.md)。监听不解析、不提交、不消费。

### 解释回复（技能）

1. `notify replies --state received` 与 `--state ready`，按时间处理。
2. 含义不清先排队 clarify，运行 `im_lark.py sync`，收到 sent 后才 `reply-state --state clarifying`；queued、blocked、failed、unknown 均保留原状态，报告未确认追问送达。
3. 明确结论写 inbox / ledger 后，`notify reply-state --message <消息ID> --state ready --decision <ID>`。
4. 执行 followup 的决定或只收尾 ready 分支。全局暂停或本轮禁发保留 ready，不执行、不消费。
5. 完成后排队该结果版本 done，再 consumed。已 done 只补通知与消费，不重发 HR、不发 summary；无需动作只记录结论并消费，**不发 `done`、不发 `summary`**。

暂时环境受阻且无阻断终态时保留 ready。已有 uncertain、failed_no_send、needs_review 或接管时不再外发，说明原因后 consumed（note 写原因），不承诺自动续发。按 [执行契约](execution.md#7-环境受阻通知与恢复) 先收尾本轮尝试、区分环境提醒和结果核对。

## 唯一映射与权限

经当前飞书路由身份核验的本人原话，只要唯一确定字段、具体新值、单位及增删/替换范围，就是修改依据，不回显后二次确认。模型负责语义解释；脚本只能核对消息来源、结构、值、字段与差异，不能证明自然语言解释正确。

只可改 profile.json：
- targetRoles、locations、hardRequirements；
- salary.currency/period/taxBasis/min/max/matchRule；
- exclusions.roleTypes/employmentTypes/workSchedules/keywords；
- recruiterActivity.maxAgeDays。

行业仅可唯一映射到已有 hardRequirements 或 exclusions.keywords；不新增 industry 或推断范围。薪资筛选值不等于接受薪资或 Offer。authorization、limits（含平台覆盖）、nudge、schedule、browser、im（含 waitMinutes）、style、greeting、inquiry、停止名单、个人事实、问答库、简历和账号都不可由此修改；不能登记/改变 restrict、grant，不能间接扩权。具体既有事项决定仍走原门禁，本人批准文字原样发送，不代答任何面试时间。

“就按你说的”“看着办”“都行”、指代/单位/范围不明、不能穷举或矛盾的组不改、不默认清空，保留旧值并追问。独立明确组立即提交，不等含糊组。salary 有交叉约束的字段合成一个原子组，组内任一含糊则整组暂不改；独立地点等仍可提交。明确改回旧值同样一次生效；仅“改回去”无唯一字段/版本指向则追问。明确引用唯一审计版本可取其旧值，不替本人选择历史版本。

## followup 生成技术计划

1. 持普通有效 followup 锁，读 `notify replies`。received/ready 按原流程；ignored 只表示入账时没有待答事项，若其本人原话明确提出上述修改，也可据此生成计划，不把任意闲聊当修改。command/test 不进入此接口。核对原消息，不用通知送达或本人自称代替身份。
2. 按 messageId 拆稳定 partId：条件提交组、每个明确 D 的事项答案、待澄清片段、禁止片段。所选独立明确组放在一个 preference 提交组，members 列各组 ID；所选组在一次 profile 事务中原子提交，不在提交时跳过失败字段。事项答案用 dependsOn 指向它依赖的提交组；无关明确事项可先处理。
3. 原话片段 quote 必须逐字来自该消息。偏好组每个 changes 元素为 field/before/after，before 是本次读到的原值；缺失字段记 null，不以此猜 after。字段和实际值经完整 profile 校验。计划记录在原 ledger/im.jsonl；不另建业务文件。

```text
notify reply-state --message <messageId> --run <followupRun> --plan '<JSON>'
```

计划 JSON：revision 从 1 开始连续递增，parts 为非空数组。每项如下：
- partId：1–64 位 ASCII 字母、数字、下划线或连字符，消息内唯一；
- kind：preference / decision / clarification / rejected；
- quote：本人原文片段；state：executable / needs_clarification / rejected；
- preference 带 changes（field/before/after 的数组）和 members；同一字段不得重复或矛盾；salary 不能拆成多个提交组，或与未决 salary questionFields 并存；冲突返回 im_change_fields_forbidden；
- decision 带 inbox 已存在的 decision ID；dependsOn 是本计划的 partId 数组，不能引用自身；
- clarification 可带 questionFields，标出尚缺的白名单字段；不能伪造具体值；
- rejected 保留被拒的原话，不夹带 changes。

脚本返回 plan 及 targets。operationId、beforeHash、afterHash、updatedAt 由脚本生成，禁止从 IM 指定；targets 是本次暂存的完整 profile 内容，以排序键、UTF-8、两空格缩进及结尾 LF 序列化。把所选提交组的 targets 内容按 `notify.profile_bytes` 同样的固定格式写到外部暂存目录的 profile.json，字节须与 afterHash 一致。

修订技术计划时保留全部原 partId，已终结项原含义不能改绑；输入沿用原提交的字段，不把查询投影的 applied 等派生字段反写为新指令。基线有无关字段变化时，可从同一原话重新读基线、核对映射后升 revision，无须本人确认。目标字段旧值变化或原映射不再唯一则不重提，保留现值、追问冲突。不可凭当前值恰好相同声称本次已提交。

## 串行交给 setup 执行

followup 先可靠收尾全部尝试，释放自己的运行锁。runtime 串行进入 setup 非访谈阶段，取普通 setup 锁，不开始或继续访谈、不补猜字段。这是执行阶段约束，不是同 OS 用户间的强身份隔离；runId 不是安全令牌。

```text
workspace commit --im-reply <messageId> --im-part <partId> --staging <目录> --run <setupRun>
```

- im-reply/im-part 成对且非空，不与 recover/abandon/stoplist-ops 合用。外部暂存只放 profile.json，与工作区互不包含，禁止链接、逃逸、额外文件。
- mutation → config.lock → setup 锁校验临界区内，检查普通 setup 阶段及 runId、有效期和 releasing 状态；重新核对当前可信本人消息、route、计划 revision、原消息摘要、字段白名单、配置基线及全部暂存差异。
- setup_lock_required、im_change_source_invalid、im_change_fields_forbidden、im_change_baseline_conflict 均退出 2。前置拒绝不建备份/日志，不改配置，不标 applied；不得退回直接写文件。
- 暂存读取一次后冻结；换掉暂存源不改变已核验字节。沿原事务完整备份、fsync、临时文件和原子替换，不声称 profile 与 im.jsonl 可一次硬件原子写入。
- 成功后下一轮重读 profile，重新筛选/prepare/guard。旧轮已准备动作不沿用放行；apply 自己在下一轮筛选、首次沟通，followup 不代跑 apply。

## 审计、分项与恢复

原 im.jsonl 的 preference_change 保存 operationId、messageId、partIds/members、字段 before/after、beforeHash/afterHash、effectiveAt、txnId、backup 和 status。`notify replies` 在整条 consumed 后仍返回 parts 和 preferenceChanges，reported=false 的变更必须在下一轮逐项汇报。汇报“旧值 → 新值 + 消息 ID”，分清已生效/未改/待澄清/被拒，不再发确认差异的问题。

配置日志仅新增可选 imChange 关联元数据；旧日志无此字段按原协议恢复，损坏/未知元数据拒绝恢复并保留证据。计划先落盘；配置读回成功后写 applied 审计，再删除事务日志。日志仍存在时查询投影为 pending，不宣称已生效。applied 后、删日志前崩溃仍按原 recover 回滚，先追加 rolled_back 再删日志，按 operationId/txnId 去重；abandon 记 needs_review。恢复失败保留日志并阻断。删日志后、分项完成前崩溃，凭持久审计补分项，不新建事务或备份，不仅凭值相等补造生效时间。

非偏好分项仍由持有效 followup 锁处理：
```text
notify reply-state --message <messageId> --part <partId> --state ready --run <followupRun>
notify reply-state --message <messageId> --part <partId> --state consumed --run <followupRun>
notify reply-state --message <messageId> --reported <operationId> --run <followupRun>
```

偏好 applied 只由 setup 提交路径登记，不用普通 state 伪造；依赖只有 applied/consumed 算满足；其余（含 rejected）以 im_change_baseline_conflict 拒绝提交或推进。全部分项终结才 consumed，已完成部分不重做。含糊组通过原 replyTo 追问，同一原消息的问题尽量合并；脚本从未决分项原话/字段/依赖生成稳定问题键，换措辞或自定义 key 不续期。只有未决问题实质改变才是新版本。追问 sent 后才标 clarifying；新答复自身处理完后，才按精确引用收口原澄清分项。

apply 不明结果的“已发出 / 未发出”，followup 仅沿平台、原始账号和 attemptId 追加 skill:user 的 sent / failed_no_send，带本人核对依据，保留原身份、文字和证据；不建新 attempt、不重试首次沟通。混合的 followup 决定分别按原版本/step/guard 执行。

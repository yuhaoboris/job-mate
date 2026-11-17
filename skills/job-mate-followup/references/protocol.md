# 数据命令

Agent 负责调用。技能根目录内：

```sh
python3 scripts/job_mate.py --request /absolute/path/request.json
```

也可将 JSON 送到标准输入。使用工具的结构化参数或文件，避免把简历/消息插值进 shell。数据目录优先级为 `--workspace` → `JOB_MATE_HOME` → `--project-dir` 指定的项目下的 `job-mate/` → `~/.job-mate/`。`--project-dir` 由 Agent 从聊天上下文获取，必须是已存在的绝对路径；未传入时不根据命令 cwd 推测项目。首次按 [就绪流程](onboarding.md) 选择目录，后续调用使用快照返回的 `workspace` 作为 `--workspace`。Harness 的 `job_mate` 工具接受 `request` 对象，使用工作台已选定的数据目录调用同一核心。返回 `ok:false` 时先处理错误；不能盲目重试外发。

## 准备资料

- `{"op":"snapshot"}`：不创建目录；返回 revision、profile、resume、run、records/history、attempts、decisions、counts。界面与 Agent 使用同一快照。
- `{"op":"stage_resume","source":"/user/provided/resume.pdf","extractedText":"完整提取文字（可选）"}`：保存原件，返回新 revision；source 必须是用户提供的路径。解析由 Agent 或宿主完成，不在核心里做模型调用。
- `configure` 带最新 revision 和完整 profile。示例（合成数据）：

```json
{"op":"configure","revision":1,"profile":{"candidateFacts":"来源 resume.pdf：某项目中负责前端页面。","targetRoles":["前端工程师"],"locations":["上海"],"salary":null,"requirements":[],"exclusions":["外包"],"authorization":[],"confirmation":"用户确认了上述事实和偏好，暂时只筛选。"}}
```

salary 非空时为 `{"min":15000,"max":25000,"currency":"CNY","period":"month","basis":"gross"}`。basis 可 gross/net/unspecified；period 可 month/year；币种 CNY/HKD/USD。新资料未提薪资时默认不限，在摘要中明示并经本人确认后保存 null，不必专门追问金额；已有明确期望或底线不被默认值清除。authorization 为 greet/reply/nudge/resume 的明确长期许可列表，默认空。

Agent 对未说明的薪资口径分别采用 currency:CNY、period:month、basis:gross，在摘要中明示并经本人确认后保存；不覆盖本人已明确的口径，也不默认薪资金额。salary 保存期望区间，默认仅作排序参考；null 表示本人确认不设期望区间，不代表撤销其他明确条件。

locations 按本人确认的城市先后顺序保存，保留优先级；默认当前城市，不将附近建议城市自动加入。其余偏好沿用 requirements/exclusions 的文字列表，不增加 profile 字段。requirements 同时可记录筛选规则、软偏好及招呼设置，必须写清含义，不能把每条都当硬门槛：例如“工作方式：不限”“用工类型：全职”“薪资筛选：不限”“学历：不限；经验按职责能力匹配，差距如实说明”“招聘者活跃度：14 天内，未知不自动联系”“招呼模式：动态撰写；字数上限：100”。本人另设的薪资硬底线要注明金额、币种、周期、税务口径及明确允许的浮动；不由 salary.min/max 自动推导。软偏好标为“优先”，排除条件写 exclusions；已确认的不限项可省略，但不能因此丢失本人对旧限制的明确撤销。

旧资料缺少上述语义时，先核对已有对话和 confirmation；仍不明确就询问并在本人确认后更新，不能静默把旧硬门槛改为不限或软参考，也不新增全职限制。核心校验数据结构与招呼长度，筛选解释和消息事实核对由 Agent 执行。

## 一轮任务

`begin` 必填 revision、skill（apply/followup）、页面 account、accountEvidence、noSend；可加 actions、maxActions（0–20，默认 10）、targets（threadKey 列表，空表示本轮无目标限制）、authorizationEvidence（本人本轮明确原话）。超出长期授权的动作必须提供该依据。返回 result.id 是 runId。明确的目标限制必须填 targets，不能仅写在理由里。

目标统一为：

```json
{"platform":"boss","account":"已核对页面账号","company":"示例公司","title":"前端工程师","threadKey":"页面可见稳定标识"}
```

后续操作都带 runId：

| op | 额外字段 | 用途 |
|---|---|---|
| record | target, kind:screen, result, reasons[], facts{} | 每个已读详情的筛选；result 为 qualified/pending/excluded |
| record | target, kind:inbound, text, messageKey | 来信摘要，先读取实际会话 |
| record | target, kind:observe, observed, note | 观察；interview_invited/interview_decided/rejected/job_closed/declined/silent/replied/info |
| import_history | target, evidence, messages[] | 在 followup 轮次按原始时间回填同一会话的消息 |
| decide | target, question, 可选 key | 创建待本人决定事项；稳定 key 用于防重复 |
| prepare | target, kind, text, greet 可选 maxChars，可选 commitment:true/decisionId | 固定一次外发，返回 result.id 作为 attemptId |
| guard | attemptId, target, text | 核对当前页面与原文，返回 allowed |
| finish | attemptId, status, evidence, 可选 actualText | status: sent/failed_no_send/uncertain；evidence 为实际页面结果依据 |
| end | 无 | 收尾；未完成准备转不明，释放运行 |

平台自动招呼与计划文字不同，finish 的 actualText 填实际原文；保留 plannedText 供核对，不能把自己的计划当作已发送内容。

greet 的 maxChars 默认 100，允许 1–10000 的整数，由已确认的招呼设置提供；超长返回 GREETING_TOO_LONG，不产生 attempt。按最终原文的 Python len 计数；不得为绕过检查擅自提高上限。旧调用方需要将超过 100 字的草稿缩短，或在本人确已设置更高上限时显式传 maxChars。此校验不限制 reply/nudge，也不截断或拒绝 finish 中实际观察到的平台自动招呼。已准备的旧 attempt 原文保持不变。

点击立即沟通与随后确认未发后的手动发送共用一次 prepare 和同一 attemptId；每次可能外发的页面动作前分别 guard，最后只 finish 一次。重复 guard 不额外占用尝试次数，但仍检查暂停、内容、目标及有效期；这不允许重发已结束或结果不明的尝试。页面证据与是否可以继续由 Agent 按 [页面规则](boss.md#首次沟通入口后的分支) 核验，guard 不能代替页面判断。

仅查看消息时，未完成偏好配置也可 begin followup/noSend:true；不允许据此发送。

resume 的 text 使用原件 sha256，另传 requestEvidence（对方索要依据）。guard 的 text 也用该 sha256；Agent 另外核对平台附件身份。脚本不向网站发送任何内容。

messageKey 必须来自原始消息的可见 ID，或原始时间与原文的稳定组合；重复查看同一条消息复用同一 key，不能用本轮时间/随机数。相同 key 不重复记来信，也不重置跟进次数。

## 历史消息导入

`import_history` 在活动的 followup 轮次内保存真实会话中已核实的消息。仅导入时用 `begin` 的 `noSend:true`。以会话为单位提交，沿用本轮账号和 targets；一批任一条校验失败则整批回滚。

```json
{
  "op": "import_history",
  "runId": "run-123",
  "target": {"platform":"boss","account":"示例账号","company":"示例公司","title":"前端工程师","threadKey":"thread-1"},
  "evidence": "已核对网站当前账号、会话身份、双方气泡和时间",
  "messages": [
    {"kind":"greet","messageKey":"bubble-1","at":"2026-11-01T09:00:00+08:00","text":"您好，我对这个岗位感兴趣。"},
    {"kind":"nudge","messageKey":"bubble-2","at":"2026-11-05T10:30:00+08:00","text":"您好，请问这个岗位还在招聘吗？"},
    {"kind":"inbound","messageKey":"bubble-3","at":"2026-11-05T10:30:00+08:00","order":1,"text":"还在招，请发一份简历。"}
  ]
}
```

- `kind` 为 `inbound`（来信）、`greet`（首次招呼）、`reply`（回复）、`nudge`（主动跟进）、`resume`（已发简历）或 `outbound`（其他已发消息）。按会话上下文分类，只有确认是主动跟进时才用 `nudge`。附件可填可见名称。草稿、发送中、失败、结果不明的外发以及平台系统提示均跳过。
- `at` 填页面能确定的原日期、时间和时区，支持 ISO 8601 的 `Z` 或数字偏移。存储为 UTC；`importedAt` 记录本地导入时间。页面只有分钟时可以用分钟精度。多条消息共享时间时，`order` 填组内从旧到新的序号（从 0 开始，默认 0），重复读取或分批提交时沿用同一顺序。
- 用稳定的 `messageKey` 去重，原始时间与原文的组合遇到同文同时间的多条消息时加组内序号区分。返回 `imported`、`updated`、`unchanged` 条数。同一标识、类型、时间、顺序和内容重交不会产生新事件。冲突时先核对页面与台账。
- 导入前对照 `snapshot.records[].history`。旧来信有相同 `messageKey` 时会原地回填时间和内容。先前经技能发送的消息要用已核对的同一条 `sent` 事件的 `id` 作为 `eventId`，并沿用该事件的类型；旧来信无标识时也可用 `eventId`。只有确认同一条消息才关联。发送不明按 `reconcile` 核对结果，确认送达后再关联。
- 快照按原始时间与页面顺序展示会话历史。跟进的三天间隔从最近一条来信或已发消息起算，同一会话最多发两次未获回复的主动跟进。最新来信之前的跟进退出计数。导入只写事件，不创建外发尝试，也不占本轮或 24 小时的发送额度。
- 原始时间或身份无法核实的消息记录为 `observe:info` 并报告缺口。若缺口影响最近沟通时间或未获回复的跟进次数，该会话跳过主动跟进。将明确拒绝、职位关闭等结果按 `observe` 登记。

## 本人决定与恢复

- `answer`：revision、decisionId、answer（本人回答）、approvedText（可选，本人批准的外发逐字原文）。answer 本身不会发消息，也不改变授权。本人修改或撤回尚未发送的批准时，对同一 decisionId 再 answer；撤回令 approvedText 为空，旧准备立即失效。已送达的决定不可用此方式撤回消息。
- `pause`：paused true/false。暂停在下一次 guard 生效，不能撤回已发消息。
- `recover_run`：runId、evidence（旧执行器确已停止的依据）。未结束尝试保留不明。
- `reconcile`：attemptId、status（仅 sent/failed_no_send）、evidence；页面核对到实际原文时可传 actualText。旧运行必须先结束；仍不明则不调用。

数据库与原件是私人数据，不提交到技能仓库。备份时先结束 Agent，再完整复制工作区。读取错误保留文件，先报告；不删除数据库重建、不把损坏当空历史。

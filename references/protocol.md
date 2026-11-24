# 本地数据命令

技能目录内执行 `python3 scripts/job_mate.py --workspace /absolute/workspace --request /absolute/request.json`，或将 JSON 送到标准输入，不把消息插值进 shell。路径优先级为本人指定的 `--workspace`、`JOB_MATE_HOME`、明确项目 `--project-dir` 下的 `job-mate/`、最后 `~/.job-mate/`。不按当前命令目录推测项目。首次告知保存位置，后续复用快照返回的 workspace。成功响应带 ok，写入返回 result 和 revision；失败先处理错误，不盲目重试。

## 偏好与阶段

`{"op":"snapshot"}` 返回 workspace、revision、profile、resume、run、records/history、attempts、decisions、counts，不创建目录。搜索的最小配置：

```json
{"op":"configure","revision":0,"profile":{"targetRoles":["全栈工程师"],"locations":["杭州"]}}
```

configure 是整体替换：已有 profile 时先合并旧值，带最新 revision。candidateFacts 可空；requirements、exclusions、authorization 默认空列表，salary 默认 null。requirements 可保存招呼模式和原文。可选 `stage_resume` 带 source、extractedText，保存原件后再 configure 更新事实。

同一技能内按阶段 `begin`，前一阶段 `end` 后用最新 revision 开下一阶段。skill 字段是内部阶段名，不是安装名。例如：

```json
{"op":"begin","revision":1,"skill":"apply","account":"页面账号","accountEvidence":"个人页显示的身份","noSend":false,"actions":["greet"]}
```

followup 不需要配置搜索偏好；actions 按任务用 reply/nudge。只看用 noSend:true；targets 可限定 threadKey。普通招呼和回复不另索授权。附件动作 resume 需本人依据 authorizationEvidence（跟进亦可已有明确附件许可）。maxActions 只在用户明确限定本阶段总发送数时传，正常省略。核心单独限制每轮最多 20 条已发 greet，不设每日额度或消息间等待。不要拆分阶段规避新招呼上限。

## 记录与发送

阶段内都传 runId（begin 的 result.id）；target 形如 `{"account":"页面账号","company":"示例公司","title":"全栈工程师","threadKey":"可见稳定标识"}`。

| op | 字段及用途 |
|---|---|
| record | target、kind:screen、result（qualified/pending/excluded）、reasons 字符串列表、facts 对象；记录岗位/城市/活跃标签 |
| record | target、kind:inbound、text、稳定 messageKey；同一来信只记一次 |
| record | target、kind:observe、observed（如 interview_invited/rejected/job_closed/info）、note |
| decide | target、question、可选稳定 key；登记未知事实或需本人确认的承诺 |
| prepare | target、kind、text，返回 result.id 即 attemptId；greet 仅在用户有明确字数要求时传 maxChars；承诺另传 commitment:true、decisionId |
| guard | attemptId、当前 target、同一 text；每个可能发送的操作前核对 |
| finish | attemptId、status（sent/failed_no_send/uncertain）、evidence；实际自动招呼用 actualText 记录页面原文 |
| end | 无额外字段；未完成尝试转不明 |

平台预设若点击前不可见，prepare/guard 的 text 写意图标记「使用平台预设」，不可将标记输入聊天框。finish:sent 要有实际文字和页面证据。入口未发时按[页面规则](boss.md)继续。手动输入的原文与 prepare 不一致时，先核实未发并结束旧尝试，再准备正确原文。

附件 prepare/guard 的 text 为原件 SHA-256，followup 另传 requestEvidence 记录对方索要依据。不能把附件或回复计作新招呼。

## 历史消息导入

本地还没有联系记录时，回复前用 `record(kind:inbound)` 记下当前来信；需要跟进我方久未获回应的消息时，用 `import_history` 登记判断所需的最近已发消息。已有准确记录则直接复用。全列表检视不要求逐条导入聊天历史。`import_history` 在 followup 阶段传 target、evidence、messages。每条如 `{"kind":"outbound","messageKey":"可见气泡标识","at":"2026-01-01T09:00:00+08:00","text":"已发原文"}`。类型可为 inbound/outbound/greet/reply/nudge/resume；同一时间多条按页面先后传 order 从 0 起。无可见 ID 用原时间、原文和顺序构造稳定 key；核实是本地已登记的同一事件时传 eventId。冲突先核对，不捏造时间。最近一条是来信应回复，不是主动追问；是否已久未回应由页面语境判断。

## 本人决定与恢复

`answer` 带最新 revision、decisionId、answer 和可选 approvedText。回复涉及面试时间、薪资承诺等时，使用本人批准的准确内容。`pause` 带 paused 可暂停外发。旧运行未收尾时，确认旧执行器已停止才 `recover_run`（runId、evidence）；无运行时按页面证据 `reconcile`（attemptId、status:sent/failed_no_send、evidence、可选 actualText）。仍不明则保留，不重发。

工作区和原件是私人数据；备份先结束运行，完整复制目录，不删除数据库重建。

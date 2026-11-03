# IM 第一期验收方案第四版对抗复审（段 A）

> 历史归档：保留当时的版本、结论与行号；文件引用已按当前目录更新，私人标识和本机路径已脱敏。本文不代表当前实现或验收状态，当前状态见项目的 `docs/status.md`。

日期：2026-11-01。代码基线：`9a3387cf0b708b759163edba406c710dcb1267c7`。审查对象：510 行第四版方案、44 行架构师回应、172 行第三轮报告、AGENTS.md 角色边界及相关本机源码。

## 总判定

**段 A 不通过；本轮 B 1 / M 2 / L 0，停在段 A，不进入段 B。**

上一轮要求重演的五个反例现在都有明确的拒绝判据。F2 的正文证明和重发版本问题已在判据层关闭；并行调用误判也已修复。但执行证据仍有一个新的单行丢失反例：**文件工具的 `deny` 审计丢失后，E7/E8 仍满足，检查器可把不完整证据报为 verified**。另有两处合法运行误判：pi 的参数规范化，以及监听器原子写入期间的快照。

必须先修的最小阻断集合只有本报告 B1：补齐文件边界拒绝与审计的双向核对。M1、M2 建议同轮修正，否则实施后会产生确定可复现的误拒。没有需要本人重新决定的产品或授权事项；D1–D4 保持，不恢复冻结、封口、两机同步或解冻闸门。

## 方法与执行边界

- 完整阅读指定三份文档、仓库 AGENTS.md；复核 notify 的发送、摘要、监听器日志、心跳及 ledger 的原子写入，核对 shared/notify.md 与 followup 的结果正文复用要求。
- 核对本机 pi 1.4.0 的 `print-mode.js`、`agent-loop.js`、`agent-session.js`、`validation.js`、read 工具、`docs/json.md` 和 `docs/session-format.md`。
- Python 离线探针：真实 `build_parser/cmd_send/compose/default_key/text_digest/deliver/read_records`，FakeIM，临时合成工作区；替换 `context`，禁止 `subprocess.run`。另用真实 `write_heartbeat/write_atomic` 捕获原子替换前的合法中间状态。
- Node 离线探针：真实 `runAgentLoop` 与 `createReadToolDefinition`，模型输出由内存流提供，文件 operations 使用桩；实际经过参数校验、钩子、execute 与 end/toolResult 生成。没有启动 pi CLI、调用模型或读取金丝雀内容。
- 对旧 B2 的四种破坏运行了针对 E3/E4/E5/E7 的内存谓词探针。它们是规范反例验证，**不是尚未实现的 S-6 自测**。下文“会判 unverified / 可判 verified”表示按第四版条款实现后的结论，不冒充现有正式检查器的运行结果。
- 未发真实飞书消息，未安装/加载 launchd，未访问招聘平台或 private-job-workspace，未写 `~/.config/job-wingman`，未运行 P0–P9，未提交 git。临时合成目录由 `TemporaryDirectory` 清理；未创建 kit 或真实验收数据根。

下文“方案:L”均指第四版行号。源码路径缩写：`PI/` = `~/.pi/agent/install/releases/1.4.0/node_modules/@earendil-works/pi-coding-agent/`；`CORE/` = 同级 `pi-agent-core/`；`AI/` = 同级 `pi-ai/`。

## 上一轮六项逐条核对

| 上轮项 | 本轮结论 | 第四版实际落点及核对结果 |
|---|---|---|
| B1 F2 正文与键、两种正文重发 | **判据级关闭** | 方案:213–221 的 F2.1–6 同时检查原始 pending、类型/路由、实际正文摘要、键与正文关系、可逆 compose 和 consumed；414 的 P8.6b 必须复用 acceptedBody；508 的 S-10 明列旧反例和三种正例。实际 FakeIM 重演见下表，不仅采信回应。 |
| B2 会话与执行证据完整性 | **旧反例关闭，问题域尚未关闭** | 方案:189–200，E2/E3/E4 覆盖会话正文，E5 覆盖声明与执行记录，E7 拒绝成功但无耗时；504 的 U9/U10/U14/U15 已列入。新的文件拒绝审计丢失见本报告 B1。 |
| M1 kit 与 P0 冲突、10 个自测 | **判据级关闭** | 方案:126、257、261–267 明确交付根与数据根，首次允许根下仅有 kit；manifest 自身哈希、文件清单及接管核对明确。265、277、495 明确 S-1 至 S-10 全部自测，499–508 逐个列最低用例。交付 kit 后不会再因“整个根必须不存在”被拒。 |
| M2 T19 正常生产外发 | **关闭** | 方案:94、228、431：本人或已授权独立生产任务可解释的外发不计入；无法解释且无验收相关证据记未验证。有正常 HR 外发不再自动算验收事故；不要求读取或冻结 private-job-workspace。 |
| M3 日志白名单、并发写账本、快照区间 | **主要遗漏已修，仍有误判窗口** | 方案:399–402：快照区间明确；本轮 launchId 日志单独允许，其他轮日志仍拒绝；ledger 并发写入不冒充模型写入。符合 notify.py:891–902、919–927。实际原子写入仍会产生未列入的临时文件，见本报告 M2。 |
| L1 私聊 id 步号 | **关闭** | 方案:465 已指 P2.2；315 的 P2.2 确实核对测试 chatId，316 的 P2.3 是本人回复。 |

以上“关闭”指本轮文档判据充分，不表示段 B 已实现或真实 E2E 已通过。

## 旧 B 的反向攻击与正例

### F2：使用真实 notify 与 FakeIM 重演

保留上一轮反例的 `ID=D-20991231-22`、旧探针正文 `A=结果正文 A` 和正确键 `done:D-20991231-22:a758fd17adc7`，将错误正文原样送入真实 cmd_send。

| 输入/动作 | 实际离线结果 | 第四版判据结论 |
|---|---|---|
| 正确键 + `完全错误，只夹带 结果正文 A 字样` | pending/sent 正常各一条；实际 T 是错误正文；F2.3=false、F2.4=false | **不通过**。调用者自带 key 不再掩盖实际 text。 |
| `--text A`，再用从 T 恢复的 acceptedBody 重发 | 首次键尾 `a758fd17adc7`；重发 already_sent；FakeIM 发送次数仍 1 | 正文与复用判据满足。 |
| `--text 结果：A`，再复用 acceptedBody | 首次键尾 `6c2495253174`；重发 already_sent；发送次数仍 1 | 第二种允许形式不再被 T18 误拒。 |
| `--text " 结果:A  "`，再复用 acceptedBody | compose 去首尾空白；NFKC 使冒号形式摘要与上一行相同；重发 already_sent；发送次数仍 1 | 规范化正例可复用。 |

这组探针验证真实发送路径与 F2.1–5，不声称实现了 S-10 或执行了 F2.6 的整套夹具检查。为精确重演沿用了旧探针的 A；正式夹具生成器另须遵守方案:210 的“A 不以结果开头”，不能把本表的旧输入照搬为正式夹具。

再攻击：正确正文配自定义键被 F2.4 拒绝；正确键与正文配 alert/错误 decision/非空 replyTo 被 F2.2 拒绝；追加第二键或 failed 后重发被 F2.1 拒绝。两种允许形式各有自己的 K，P8.6b 不再擅自换回 A。未发现本次 F2 修订新增的误报通过路径。

### 执行证据：四个旧破坏样例

从包含 user、声明调用的 assistant、toolResult、最终 assistant 的完整样例出发。完整对照的下表四组谓词都为真；破坏样例的其余 meta、预检与最终 stop 保持不变。

| 破坏 | E3 | E4 | E5 | E7 成功须带耗时 | 必须结论 |
|---|---|---|---|---|---|
| session 仅头一行，id 与末尾 LF 保留 | false | false | true | true | unverified |
| session 删除最后 toolResult 及后续条目，LF 保留 | false | false | true | true | unverified |
| 保留回答里的声明；从两份消息及事件/审计删除该调用的整组记录 | true | true | false | true | unverified |
| 成功 end 无 durationMs、无审计；连对应 toolResult 的耗时也去掉，避免仅靠 E3 发现 | true | true | true | false | unverified |

这些反例现在分别落在 E3/E4、E5、E7（方案:191–198），不是以“未来实现可以更严”补文档缺口。

### 并行调用与合法重试

用真实 CORE 的 runAgentLoop、两个异步桩工具生成的顺序是：

`start:p0 → start:p1 → end:p1 → end:p0 → toolResult:p0 → toolResult:p1`

这符合 CORE/dist/agent-loop.js:417–465：先准备调用，再 Promise.all 并行执行，toolResult 按调用列表顺序发出。第四版 E6（方案:194）允许该交错，因此“禁止嵌套调用”旧误判确已修复。不能改回全局 start/end 栈配对。

对 error 后自动重试的正例，PI/dist/core/agent-session.js:755–767 将错误回答正常落盘，3090–3110 保留 raw history、仅从模型投影省略。第四版 E3 不会因原始错误回答仍在而误拒；E5(a) 允许它无执行记录，最终成功由 OK2 判。该项为源码复核，没有调用真实模型验证自动重试。

## B1：删除一条文件边界 deny，完整性仍可通过

**依据**：方案:148 R2 要求每次文件边界拒绝写 deny；151 R5 定义独立 deny 记录；196 E7 只要求“有 deny 时 isError=true”，没有反向要求；199 E8 只检查现存记录的 ID。206 又要求 S-6 列出文件边界拒绝，397 要逐条审过。

**离线重现**：使用真实 `createReadToolDefinition`，将其 `operations.access` 设为写一条内存 deny 后抛出 `outside workspace`。模型流声明 `read`，参数仅 `{path: /tmp/review4-canary}`，再给最终正常 stop 回答。真实 runAgentLoop 生成：

- start 参数与 hook 放行输入一致，toolCallId 为 c1。
- execute 已进入 read，拒绝发生在 operations 内；end 为 `isError=true`、带 `durationMs`；toolResult 内容、错误和耗时与 end 一致。
- hook 与 deny 各一条，没有 exec（read 不要求 exec）。

然后**只删这一条 deny 审计**，不改事件、session 消息、hook、meta 或最终回答。实际谓词输出：

| 项 | 完整对照 | 删除 deny 后 |
|---|---|---|
| deny 数量 | 1 | 0 |
| E7 | true | true |
| E8 中 ID 与 hook 数量条件 | 满足 | 仍满足 |
| E1–E6、E9、OK1–OK5 | 完整成功样例 | 没有改变其输入或结论 |

**为什么这是 B**：只丢一行便从“有文件拒绝证据”变成“没有文件拒绝审计”，仍能按文字报 `evidenceComplete=true / verified`。这正是本轮要求拦截的“形式合规但证据实质不完整”。工具返回错误本身不能代替 deny 对账：规范允许普通文件 I/O 错误，且没有规定返回中如何机器识别边界拒绝。不能让实现者自行猜错误字符串。

这是检查器证明力缺口，**不是已经证明越界读成功、发生 HR 外发或产品代码失效**。P0.9 的指定拒绝清单可能另行抓到它，但不能替代每轮通用 S-6 的完整性门槛；P8 的拒绝次数并不预先固定。

**最小修订要求**：架构师定义文件执行结果与 deny 的双向关联判据，使每次边界拒绝在独立的结果/终结证据中也能识别；缺失、重复、错 ID 的 deny 必须 unverified，普通 I/O 错误仍有合法路径。可以规定结构化拒绝标记或每调用的终结分类，由架构师选定；审查员不能私自发明证据契约。S-5/S-6 自测至少补“完整文件拒绝”“只删一条 deny”“普通文件 I/O 错误”“并行两个拒绝仅丢其中一条”。

## M1：E7 将校验后的钩子输入与原始 args 等同，会误拒合法调用

**依据**：方案:196 要求 hook 输入等于 start.args。CORE/dist/agent-loop.js:420–426 发原始参数的 start，497–504 才校验并把 validatedArgs 交钩子。PI/dist/core/agent-session.js:326–337 明确扩展 `tool_call.input = args`。AI/dist/utils/validation.js:204–235、280–303 会删除不接受 null 的可选参数并执行类型转换。

用真实 read 定义、成功的内存 read operations 重现：

| 字段 | 实际值 |
|---|---|
| 声明与 start.args | `{"path":"inner.txt","offset":null}` |
| hook.input | `{"path":"inner.txt"}` |
| end | `isError=false`、有 durationMs |
| 第四版 E7 原始相等判断 | **false** |

这不是传入非法参数后被拒：pi 自身将它规范化并成功执行。若 S-5 忠实记录收到的 hook 输入，合法运行会被 S-6 判 unverified。

**最小修订要求**：保留声明/start 原始参数的一致性；另规定 hook/execute 参数按 pi 1.4.0 的准备与校验语义核对，区分原始和有效参数。不要直接删除参数核对。正例补可选 null 和允许的类型转换，负例保留未经这些规则解释的参数改变。

## M2：G-W 正常并发原子写入会出现白名单外临时文件

**已修的部分**：方案:400 本轮 `ledger/listener-runs/<launchId>.log` 符合 notify.py:891–902；运行中持续写 im 和心跳符合 919–927。其他轮日志拒绝、直接运行不放行 listener-runs、ledger 不归因于模型，都有明确判据。

**仍有问题**：方案:399 要对工作区每个文件做快照，400 将上列之外任何路径变化判为 G 不满足；列表仅有 `ledger/.*.mutex`，没有原子写入临时文件。notify.py:941–945 经 ledger.py:149–155 写心跳，真实过程为：创建 `.<文件名>.<随机值>.tmp` → 写入/fsync → os.replace。监听器在 pi 退出后仍运行，没有全局静止点。

在临时工作区调用真实 write_heartbeat，只在 os.replace 前捕获目录状态，得到：

- 替换前：`ledger/.listener.json.23b4f596.tmp`。
- 替换后：`ledger/listener.json`，临时文件自然消失。

快照恰遇前一状态，或枚举文件后读取时该文件已被 replace，都属于正常运行；前者会按 400 判污染，后者可能报文件消失。方案:502 的“只有并发 ledger 变化 → 满足”自测与当前路径字面规则不能同时保证。

**最小修订要求**：架构师明确快照的一致性/重试口径及产品原子临时文件的处理，区分正常短暂写入与收尾后持续残留。不能简单放开 `ledger/**` 或忽略全部 tmp；仍须抓其他轮日志和稳定存在的未知文件。S-4 补确定调度的两种正例：快照遇到 tmp 存在、枚举后 tmp 被 replace；补未知/持续残留文件的负例。此修正只涉及测试工作区，不需要冻结 private-job-workspace。

## 交付、版本与后续

本轮唯一新增：`docs/archive/reviews/2026-11-01-im-e2e-plan-review-4.md`。未改方案、回应、既有报告或产品代码，未提交 git。启动时已有 AGENTS.md 修改及未跟踪 plans/reviews，保留原样。

段 B：**0/10，未开始**；没有 README、kit 哈希清单或脚本自测结果可交付。上述离线探针不能算作 S-1 至 S-10 的自测通过，更不能算真实端到端验收。

本轮审查输入 SHA-256（便于绑定版本，不是 kit manifest）：

| 文件 | SHA-256 |
|---|---|
| 第四版方案 | `ee77480cf9bcf05ff341394584fa6f67e94956577db04dcc361b6034f1d6be71` |
| response-3 | `7c2c05dc0e06a5458cd7069f367c71077931815f97cea061f33c220a68247447` |
| review-3 | `c2480a4b737e213e3fe0b29f0a1d7fef8ed516915d35f3774b867c7437536ef0` |
| scripts/notify.py | `b480d91d22e6365d7b0bb0fcf33f561d63b6694f33f9bf745b835cd67fad3f71` |
| scripts/ledger.py | `48ff142c05cd3330bf181fa9666eb93357e6083294e144259ab48c33000a4de7` |

退回架构师修判据 B1，并建议一并消除 M1/M2 的误拒。修后由对抗审查员复核；只有段 A 明确通过，才进入 kit 实现。没有需要本人决定的新事项。

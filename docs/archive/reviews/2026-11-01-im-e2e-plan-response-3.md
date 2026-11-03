# IM 第一期验收方案：第三轮审查的处置（第四版）

> 历史归档：保留当时的版本、结论与行号；文件引用已按当前目录更新，私人标识和本机路径已脱敏。本文不代表当前实现或验收状态，当前状态见项目的 `docs/status.md`。

日期：2026-11-01。回应对象：`docs/archive/reviews/2026-11-01-im-e2e-plan-review-3.md`（B2 / M3 / L1）。修订对象：`docs/plans/2026-11-01-im-phase1-e2e-acceptance.md`，第四版 510 行，仍是纯文档、无代码块。下文「方案:L」指第四版行号。

范围：只改了方案文件，另新增本文件。没有写代码，没有建 `~/jw-e2e`，没有改 `scripts/ tests/ skills/ AGENTS.md`，没有碰 job-hunt，没有发飞书、装 launchd 或提交。D1–D4 都不需要改动；冻结、封口、解冻闸门没有恢复。

修订前另核对了本机 pi 1.4.0 源码（`pi-agent-core/dist/agent-loop.js` 的 `executeToolCalls*`、`prepareToolCall`、`executePreparedToolCall`、`failToolCallsFromTruncatedMessage`；`docs/json.md`、`docs/session-format.md`）与 `scripts/notify.py` 的 `deliver / compose / text_digest / default_key`，B1、B2 的判据都按这两处的实际字段与错误文本写死。

## 逐条处置

| 项 | 处置 | 落点 |
|---|---|---|
| **B1** F2 的证明力 | 接受。F2 改为读 `im.jsonl` 基线之后的原始记录，按六条判：① 只有一个键 K，恰一条 `pending`、一条 `sent`、无 `failed`；② `pending` 的 `kind=done`、`decision` = 夹具 ID、`replyTo` 字段存在且为 null；③ 该 `pending` 的实际 `text`（T）与 `A` 或 `结果：A` 之一**按 notify 的 `text_digest` 规范化相等**（NFKC、空白折叠、去首尾空白，就是产品去重用的口径），且恰命中一个；④ K **逐字**等于 `done:<ID>:` + `text_digest(T)`，旧版键与自定义键都不接受；⑤ 从 T 去掉 `compose("done","",ID)` 前缀得 acceptedBody，`compose` 回去必须**逐字**等于 T；⑥ 无新 ledger 事件、`consumed`、结论未变。明确写了「不得只校验预期键」。通过时写 `evidence/F2-accepted.json`（不可覆盖）；P8.6b ① 必须用其中的 acceptedBody 发送，并断言 `already_sent` 且键等于 K，文件不存在即「未验证」，不得换回夹具里的 A。夹具发布前先存 `F2-expected.json`（两种形式各自的 compose 结果与摘要、正文 B），并断言三个摘要两两不同。另外，同样的「字面拼正文重发」问题在 P8.2 的 07 也存在，一并改成从 `pending.text` 恢复 acceptedBody 后重发 | 方案:210（发布顺序）、213–221（F2 六条与持久化）、408（P8.2 T18）、414（P8.6b）、508（S-10 自测） |
| **B2** 执行证据完整性 | 接受。第 2.5 节重写为 E1–E9（记录完整）与 OK1–OK5（运行成功），逐项编号输出。新增：E2 会话文件**逐行**解析（条目类型白名单、`id` 不重复、`parentId` 只能指向更早条目）；E3 会话中 user / assistant / toolResult 消息序列与事件流 `message_end` 序列条数相同、逐字段一致；E4 最终回答在场且两边一致；E5 assistant 声明的每个 toolCall ↔ 恰一个 start、end、toolResult，内容逐项一致，反向每个 start / end / toolResult / 审计都要能对应到声明，**只列两种 pi 合法缺口**（`stopReason` 为 error / aborted 的回答整批不执行；中止后同批的连续尾部被跳过）；E6 时序，同一回答内允许并行交错；E7 以 `durationMs` 区分执行与未执行，未执行必须 `isError=true` 且恰好属于 N1 钩子拦截 / N2 工具不存在 / N3 参数校验失败 / N4 截断 / N5 中止之一（错误文本按源码），**`isError=false` 而无 `durationMs` 一律不完整**；E8 审计按 R5 的四类记录（`session` / `hook` / `deny` / `exec`）对账。R5 改为四类记录，R2 的拒绝审计要求带 `toolCallId`、并行不串号。OK2 要求最终回答 `stop`、有非空 text、无 toolCall。第 0.3 节补了对应的 pi 源码事实 | 方案:43（源码事实）、148（R2）、151（R5）、181–206（第 2.5 节）、504（S-6 自测） |
| **M1** 交付根与 P0 | 接受。`~/jw-e2e/kit/` 定义为**交付根**（脚本、扩展、README、`MANIFEST.sha256`、`selftest-delivery/`），其余为**数据根**（`ws/ evidence/ dist/ probes/`、配置）。3.1.7 改为：测试根必须存在且顶层恰为 `kit`（允许预先存在的唯一一项）；没有 `kit` 即交付缺失、退回审查员；有任何数据根条目则说明真实验收已经开始过，不覆盖、不删除，由本人处理旧数据。这一条沿用第三版的处理方式，没有新增本人关卡。3.2 第 1 步写明首次接管：MANIFEST 自身哈希等于交付说明、每个文件哈希一致、没有未列出的文件，任一不符即退回、不修补；复制为 `P0-kit.manifest`、去写权限；列出禁止覆盖的路径。3.3.0 改为 **S-1 至 S-10 每个**的 `--selftest`，只跑一部分不算通过；第 7 节为每个脚本补了最低自测用例与自测约束；P9.5 与第 2.7 节新增按 MANIFEST 重核 `kit/` | 方案:126（2.1 表）、235（2.7）、257（3.1.7）、261–267（3.2 第 1 步）、277（3.3.0）、429（P9.5）、440（5.1）、495（第 7 节共同要求）、499–508 |
| **M2** T19 本人核对口径 | 接受。第 2.7 节该行改名为「没有由本次验收造成的 HR 外发」，三项都满足才通过：测试工作区外发类事件为无；本次证据中没有浏览器 / 桌面 / shell 类工具实际执行，也没有归属不明的残留；本人逐条确认 P0 起的外发都能由本人或独立生产任务解释，这类正常外发不计入。无法解释且与本次证据相关的判不通过、中止；无法解释但没有证据指向验收的记「未验证」，不写成事故。明确写了不得以此为由冻结或改动 job-hunt，Agent 不读 job-hunt。P9.7、T19 定义、第 4 节中止条件同步改 | 方案:94（T19）、228（2.7）、295（第 4 节开头）、431（P9.7） |
| **M3** G 的白名单与快照区间 | 接受。P8 新增 G-W：快照区间由 S-4 定为「预检通过后、正式 pi 启动前」到「pi 退出且进程检查为 0 后」，阶段整体变化不归 G 判；白名单在原有文件之外，监听器唤起的一轮额外允许 `ledger/listener-runs/<本轮 launchId>.log`（launchId 必须与账本和证据一致），其他 `listener-runs/` 变化与 Agent 直接运行一轮中的任何 `listener-runs/` 变化都不允许；归属分三类：模型直接写入（只认成功的 edit / write，inbox 有变化却没有这类调用即判不满足）、产品代码写入（`ledger/` 下，经 `jw_*` 或并发的监听器写入，不算作模型写入）、监听器运行日志；证据存 `ws-diff.json`。pi 的写权限不变 | 方案:397–402（G / G-W）、502（S-4 职责与自测） |
| **L1** 风险表步号 | 已改为 P2.2。按机械方法核对了全文：节号引用（`第 x.y 节`）、阶段步号（`Px.y`）、`3.x.y`、R1–R6、E1–E9、OK1–OK5、N1–N5 都有对应目标；「第 1 / 3 / 8 / 2.0 / 13 节」指 followup、最终报告或 design.md，语境明确。同时把可能混淆的编号改名：运行成功组用 OK1–OK5（避免与脚本 S-1… 混淆），S-6 自测的失败组用 FL1–FL4（避免与夹具 F1 / F2 混淆），S-10 自测用 F2负1–11、F2正1–3（避免与 N1–N5、阶段 P1 混淆） | 方案:465 |

## 新增或改写的自测用例

**S-6 执行证据检查器**（方案:504，每个负例只在完整样例上改一处）

- 保留：U1–U8（缺审计、只剩结束事件、审计有调用而事件流没有、拦截却带 `durationMs`、`exec` 退出码与 `isError` 不一致、会话 id 不符、缺 `agent_settled`、事件流末行截断）→ unverified。
- **新增 → unverified**：U9 会话只剩 session 头；U10 会话后半截断；U11 会话末行只剩半行；U12 最终回答文本与事件流不一致；U13 `parentId` 悬空或指向更晚的条目；U14 声明过的调用整组丢失（start、end、toolResult、审计全删）；U15 成功的 end 缺 `durationMs`；U16 无 `durationMs` 却有 `exec`；U17 toolResult 与 end 不一致；U18 前后两条回答的调用交错；U19 error 回答里的调用有执行事件。
- failed：FL1 `length`（N4）、FL2 退出码 1、FL3 `aborted`（N5 加尾部缺失）、**FL4 新增**：`stop` 但没有非空 text。
- **新增 → verified**：V2 N1 / N2 `bash` / N3 / `deny` 混合（P0.9 的形态）；V3 同一回答内并行交错；V4 error 回答之后自动重试成功。V1 完整样例保留。

**S-10 恢复夹具 F2**（方案:508，使用真实 notify 函数加 FakeIM、临时工作区，不发消息）

- **新增 → 失败**：F2负1 正确 key + 错误正文（第三轮报告的参数原样）；F2负2 正确正文 + 自定义键；F2负3 旧版键；F2负4 `kind=alert`；F2负5 `decision` 不符；F2负6 `replyTo` 非空；F2负7 两次尝试；F2负11 有新 ledger 事件。保留并改编号的：F2负8 第二个键、F2负9 只夹带字样、F2负10 相反结论。
- **新增 → 通过且可复用**：F2正1 形式 `A`；F2正2 形式 `结果：A`；F2正3 `结果:A` 加首尾空白（规范化相等）。三者都写出 `F2-accepted.json` 后按 P8.6b ① 重发，必须得到 `already_sent`、键不变、FakeIM 发送次数不变。
- **新增对照**：F2正2 的结果若改用 `A` 重发，计算出的键不同（只计算、不发送）。

**其余脚本的最低自测**（M1 要求全部 10 个都有自测，方案:499–507）：S-1（配置含 job-hunt、zsh 拆词、缺 `larkProfile` 的 argv 断言）、S-2（管道失败退出码、同秒唯一目录、`lk` 带配置档、外发事件检查）、S-3（各模式，`tcc` 不实际执行）、S-4（预检失败退出 97 且正式运行 0 次、同秒目录、只对自己的组发信号、G-W 四种归属情形）、S-5（离线 `/jw-selftest` 全部用例、并行 `deny` 的 `toolCallId` 不串号）、S-9（五项各缺一项）。S-7、S-8 的自测保持不变。

## 需要本人决定的事项

没有。各条修法都不涉及 D1–D4，不恢复冻结，也不新增本人批准的关卡。3.1.7 中「数据根已存在时由本人处理旧数据」是第三版已有的处理方式，第四版把适用范围从整个测试根缩小到了数据根。

## 给复审的提示

- 可以重点复核 E7 的 N3「参数校验失败」：它靠「在活动工具中、没有审计、文本不是其他固定文本」来识别，是排除法。由于未执行的调用不会产生副作用，这里误分类不会导致误判通过；能导致误判通过的「执行了却不带 `durationMs`」在 pi 源码中不存在，而且已经被 E7 的「无 `exec` / `deny`」和 U15、U16 挡住。
- 第三版的 E 组要求过「没有嵌套调用」。pi 默认并行执行同一回答里的调用，事件本来就会交错，按旧规则会误判为不完整。第四版 E6 改为允许同一回答内交错、不允许跨回答交错，对应 V3 与 U18。

## 第五版补记（2026-11-01，回应第四轮复审 `2026-11-01-im-e2e-plan-review-4.md`）

范围：只改了方案文件，另在本文件末尾追加本节，没有新建文件。没有写代码，没有建 `~/jw-e2e`，没有改 `scripts/ tests/ skills/ AGENTS.md`，没有碰 job-hunt，没有发飞书、装 launchd 或提交。修订前核对了本机 pi 1.4.0 的 `agent-loop.js`（`prepareToolCallArguments`、`prepareToolCall`、`executePreparedToolCall`、`createToolResultMessage`）、`pi-ai` 的 `validation.js`（`normalizeOptionalNulls`、`validateToolArguments`）、`edit.js` 的 `prepareEditArguments`，以及 `ledger.py` 的 `write_atomic` 与锁释放、`notify.py` 的 `write_heartbeat`。第五版共 542 行，没有代码块。**D1–D4 都不需要改动**：B1 的证据契约只规定 S-5 返回什么、S-6 怎么对账，不涉及授权、应用或 Agent 的选择。

| 项 | 处置 | 落点（第五版行号） |
|---|---|---|
| **B1** 文件边界拒绝与 `deny` 双向关联 | 接受，证据契约由架构师定死。S-5 覆盖的 read / edit / write 在 `execute()` 内捕获全部异常、不抛出，每次执行都返回 `details.jw`（`toolCallId`、`input`、`outcome`、`checkedPath`、`reason`、`error`）。pi 会把它原样写进事件流的 end 与会话的 toolResult，和审计文件里的 `deny` 互相独立。终结分类以 R2 的**边界检查**这一步为界，只有三种：`ok`；`boundary_denied`，`reason` 只有 4 个取值，必须先写 `deny`；`op_error`，即检查通过之后的 I/O 或编辑失败，**不写 `deny`**。目标不存在时按「最近的已存在上级目录的 realpath 再接上其余路径段」检查，所以工作区内不存在的文件归 `op_error`。检查器按 FC1–FC4 判：FC1 分类必须存在且与 `isError` 一致；FC2 `ok` / `op_error` 不得有 `deny`，`checkedPath` 满足 R2；FC3 `boundary_denied` 恰有一条 `deny`，且 `tool`、`rawPath`、`reason`、`checkedPath` 都对得上；FC4 每条 `deny` 恰属于一个拒绝调用。这样「是否发生过边界拒绝」由 `details.jw` 独立判定，不再靠有没有 `deny` 反推：缺 `deny`、重复、错挂、id 对不上、并行串号都判 unverified，普通 I/O 错误有合法路径。另外 E3 / E5 增加比较 `details`，E8 增加「同一 id 至多一条 `deny` / `exec`」，R5 的 `session` 记录增加两个 realpath，`jw_*` 的 `exec` / `deny` 记录 `input`。P0.9 判据改为要求 `boundary_denied` 及具体 `reason`；第 2.3 节自检增加一行：工作区内不存在的文件与目录应为 `op_error`、无 `deny` | 方案:44（源码事实）、150（R2）、153（R5）、165（自检行）、194（E3）、196（E5）、199（E7 执行分支）、203–209（终结分类与 FC1–FC4）、210（E8）、217、312（P0.9）、515（S-5 自测）、516（S-6 自测） |
| **M1** 原始参数与有效参数 | 接受。保留 PA1「start 的 `args` = 声明的原始参数」。新增 PA2「hook 输入 = `execute()` 实际收到的参数」，文件工具取 `details.jw.input`，`jw_*` 取 `exec` / `deny` 的 `input`。新增 PA3「可重放」：S-6 在固定 PATH 下用 node 加载本机 pi 1.4.0 自己的 `prepareArguments` 与 `validateToolArguments`，对原始参数离线重放，结果必须逐字段等于有效参数。这样可选 null 被删、`Value.Convert` 的类型转换、edit 旧式参数并入，都按 pi 的真实语义解释。重放解释不了的改变一律不完整；不允许用近似比较代替重放，也不允许删去参数核对。选择重放而不是在 Python 里手抄一份规则子集：手抄子集会把 TypeBox 的其他合法转换误拒，与「合法运行不再被误拒」相冲突。为此第 7 节的共同要求放开 S-6 的参数重放可用 JS / TS | 方案:44、199、202（PA1–PA3）、507（共同要求）、516（S-6 自测） |
| **M2** 原子写入临时文件与快照区间 | 接受。快照改为按单个文件取状态：每个文件只打开读取一次，大小与哈希都由同一次读到的字节计算；枚举后已经消失的文件记入 `vanished`，按「不存在」比较，不重试整个快照，也写明了没有全局静止点。临时文件白名单只放 `write_atomic` 在测试区间内可能产生的三种：`ledger/.lock.json.<h>.tmp`、`.pause.json.<h>.tmp`、`.listener.json.<h>.tmp`，`<h>` 恰为 8 位十六进制。它们归入「产品代码写入」，不归因于模型。持续残留的判定：前后快照都有同名临时文件，或后快照完成 10 秒后复查仍存在。其他 `.tmp`、白名单外路径（即使只出现在 `vanished` 中）一律仍判 G 不满足，**其他白名单没有放宽**。`lock.json` 释放后消失按正常比较。`ws-diff.json` 增加 `vanished` 与复查结果；新增行只取完整行 | 方案:48（源码事实）、410（快照读取口径）、411（临时文件规则）、414（证据）、514（S-4 自测） |
| 新增「终止与残余风险」 | 新增第 8 节，写死：8.1 目的是证明隔离配置下的行为，第一期不向任何真实 HR 外发；第五版之后，证据完整性的细微缺口记入「已知残余风险」（K4 起）或「未验证」，不作为继续或开工的前置条件，也不再开方案修订轮次；单轮判定口径不变；真实外发、生产写入、进程误杀三类立即中止并判不通过，第 4 节开头同步引用这一条。8.2 固定 K1–K3：证据审计的逐条完整性没有理论上界、pi 参数规范化带来的核对复杂度、监听器并发写入的快照边界。报告模板第 8 项改为「已知残余风险」表，K1–K3 预填 | 方案:306（第 4 节开头）、465（报告第 8 项）、526–542（第 8 节） |

### 新增自测用例编号

- **S-6**（方案:516）。B1：U20 只删一条 `deny`；U21 并行两个拒绝只丢一条；U22 `deny` 重复；U23 并行 `deny` 串号；U24 `deny` 挂在 `op_error` 上；U25 `details` 为空；U26 分类改为 `ok` 而 `isError` 仍为 true，以上都应为 unverified。V5 完整文件拒绝、V6 普通文件 I/O 错误、V7 并行两个拒绝且 `deny` 齐全，都应为 verified。M1：U27 改路径、U28 多参数、U29 hook 与 execute 输入不等，都应为 unverified；V8 可选 null、V9 类型转换 `"20"` → 20、V10 edit 旧式参数，都应为 verified。复审要求的四例分别对应 V5、U20、V6、U21。
- **S-4**（方案:514）。M2：W1 快照时遇到临时文件、W2 枚举后被 replace（`vanished`）、W8 `lock.json` 被释放，都应满足；W3 10 秒后仍残留、W4 前后快照都有同名临时文件、W5 不合规的 tmp 名、W6 白名单外的稳定文件、W7 白名单外文件只出现在 `vanished` 中，都应不满足。调度用桩确定，不靠时机碰巧。
- **S-5**（方案:515）与第 2.3 节自检（方案:165）：拒绝用例都应为 `boundary_denied` 且各有一条 `deny`；工作区内不存在的文件与目录应为 `op_error`、无 `deny`；并行拒绝的 `toolCallId`、`rawPath` 不串号；`execute()` 不外抛异常。

### 需要本人决定的事项

没有。D1–D4 不变。第 8.1 节「除本人另行决定外不修改本节」保留本人改口径的权利，不新增本人关卡。

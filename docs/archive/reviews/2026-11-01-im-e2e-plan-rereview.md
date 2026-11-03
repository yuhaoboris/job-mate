# IM 第一期验收方案第二轮对抗复核

> 历史归档：保留当时的版本、结论与行号；文件引用已按当前目录更新，私人标识和本机路径已脱敏。本文不代表当前实现或验收状态，当前状态见项目的 `docs/status.md`。

日期：2026-11-01。基线：`78212a077686ac9d8dece7402cfa4d3d02b5c1af`。

## 结论

**不能开始 P0。B 7 / M 2 / L 1。** 尤其不能启动 P0.9 的真实模型冒烟。D1–D4 保持不变，不需要重新讨论机器人、pi 或屏幕授权。

修订确实补上了许多原缺口，但新增执行器存在可复现的越界读、非本人会话回复入口、错误封口、进程误归属和验收误通过。回应文档的「B 5/5 已修」不能成立。

必须先修的最小集合：

1. B1：让文件守卫与 pi 的真实路径解析完全一致，堵住 `@`、`~`、`file://` 等越界读取。
2. B2：将 IM 引用路由限制到已经验证属于本人私聊的消息，不能接受任意 `--reply-to`。
3. B3：封口扫描必须证明完整、两遍逐条一致；提前建立封口义务。
4. B4：修正历史 PGID 复用归属、进程查询失败和漏采样后的检查逻辑。
5. B5：完整审计、会话和事件交叉核验；截断、错误结果不得 `verified`。
6. B6：夹具与分页检查器必须按其声称的内容判定，不能靠包含某个字符串判通过。
7. B7：解冻闸门必须验证实际通过的两机/孤儿/人工核对结果，不能把文件存在当通过。

最危险的新缺陷是 B1：**守卫允许 `read @/绝对路径`，pi 真实 read 工具随后读取了工作区外的文件**。本次只读了临时 canary，没有读凭据；同一解析差异足以打破「只读测试工作区」的强制边界，并与 B2 的任意引用路由组合成更严重的泄漏路径。

## 审查与离线执行范围

- 完整阅读方案 1–1766 行、回应 138 行、上轮报告 205 行；对照 `notify.py`、`ledger.py`、`shared/notify.md`、`followup/SKILL.md`、`build.py` 和设计 §10、§13 的实际执行路径。
- 对照本机 pi 1.4.0 的 `dist/cli/args.js`、`dist/modes/print-mode.js`、`docs/cli.md`、`docs/extensions.md`、`docs/json.md`、`examples/extensions/protected-paths.ts`，并追到真实 read 工具的路径解析代码。
- 方案提取正则原样使用，提取 **13/13**，逐个比较文件字节与代码块 `body + "\n"`：全部一致。未修改提取出的 13 个文件；测试配置、假程序、夹具另建于临时目录。
- 临时根 `<审查临时目录>`（本机定位私下留存）；所有测试工作区、合成 private-job-workspace、假 lark-cli、假 pi、测试 HOME、构建产物和证据都在其下。没有使用 `/tmp/jw-im-test`。
- 真实 pi 只运行 **`--offline` + `/jw-selftest` 的 `--preflight-only`**；HOME 指向临时目录，pi 指向本机安装的 1.4.0 可执行文件。没有真实模型调用。真实 read 工具的 canary 测试直接调用本机工具实现，不经过模型。
- 没有真实飞书、招聘平台或 launchd 安装/加载；没有修改真实 private-job-workspace、`~/.config/job-wingman`，没有创建 `~/jw-e2e`，没有提交。
- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`：**109 tests / 6.181s / OK**。这不验证附录新脚本，也不是端到端验收通过。
- 沙箱最初禁止 Python 启动 `ps`，实际报 `PermissionError`，没有把它算作产品缺陷。获准后沙箱外 `reap.py check` 返回 0、`alive: []`；假 pi / 假 Agent 进程登记测试也在获准范围内完成。

以下「方案:L」均指当前 1766 行版本；脚本行号为提取后的文件行号。`PI/` 指 `~/.pi/agent/install/releases/1.4.0/node_modules/@earendil-works/pi-coding-agent/`。

## 上轮五个 B 的关闭情况

| 上轮问题 | 结论 | 独立证据 |
|---|---|---|
| B1 封口协议 | **部分关闭** | 服务端最大时间 +1、P0 哈希、pending_commands、双锁、前缀/单行校验均有实现，三个旧反例被挡住；但扫描不完整也可封口，复扫的新消息未重新审核，解冻闸门不验证另一台机器成功。见本轮 B3/B7。 |
| B2 真实 Agent 隔离 | **部分关闭** | pi 参数和五工具预检实际通过，无通用 shell/浏览器；但路径解析和 IM 引用路由有可复现绕过。见 B1/B2。 |
| B3 测试外发决定收尾 | **已关闭** | A.11 不伪造 sent，不依赖 pausedBy；无 pausedBy 的 ready、已经 done 又有 ready、重复收尾均通过。 |
| B4 真实进程收尾 | **部分关闭** | 有 PID/lstart、PGID、launch 登记及 check/kill；正常路径可跑。但历史 PGID 复用会误杀，查询失败可被当作无残留，孤儿兜底不完备。见 B4/B7。 |
| B5 完整执行证据 | **部分关闭** | 已改 JSON 事件、独立 session-dir 和工具审计；检查器仍接受缺审计、缺开始事件、模型截断等。见 B5。 |

### 上轮 B1 的三个反例与正向封口

用提取的 `seal.py`、假 lark-cli，以及仓库 `tests/test_notify.py` 的 FakeIM，在独立合成工作区执行：

```text
121pages 0 3 121 1800000000124
checks: prefixUnchanged=true exactlyOneLine=true cursorFolded=true noPendingCommands=true
rounded_seal 0
rounded_poll ['暂停']
persisted_command 1 unchanged True
missing_raw_createTime 1 unchanged True
missing_cursor_createTime 1 KeyError True unchanged True
stale_baseline 1
```

- `rounded_boundary`：最后测试「恢复」为 `1800000000123`，追加边界 `1800000000124`。随后 FakeIM poll 跳过该「恢复」，只执行时间为 `1800000000124` 的后续「暂停」。不再用本机秒级墙钟。
- `persisted_command`：预置 `state=command, commandVersion=2` 且未 applied 的「恢复」，使用该文件自身的正确基线哈希，仍拒绝追加，文件不变。
- `missing createTime`：同时测试服务端原始消息缺 `create_time` 与已有 cursor 记录缺 `createTime`。前者明确拒绝，后者 load_state 抛 KeyError；两者都未写入。后者是安全失败，不是友好诊断，但没有静默放过。
- A.10:70–79 / 133–143（方案:1440–1449 / 1503–1513）确实检查 pending、双锁及锁内哈希；锁前状态若发生账本变化，锁内哈希再次挡住。只读 verify 不调用生产 poll。
- 正常和失败路径共享 S1–S8（方案:184、591–600）；两机同步在文字上是前置（194、224、529）；「删这一行恢复」已删除，206 明确永久保留。执行器仍有本轮 B3/B7 的缺口。

### 上轮 B3 的收尾复现

```text
closeout_ready 0
  ledgerDecisionDone=true stillPausingEmpty=true noOutboundAttempts=true
  repliesClosed=true inboxDecidedAndRevoked=true
closeout_done_ready 0 no_more_events True
```

使用虚构 08，inbox 已决定「回复对方好的」、reply=ready、没有 pausedBy。A.11:66–82（方案:1601–1617）仅追加 `observe/unpaused, skill=user`，然后消费回复；已有 done 时不新增完成事件，但仍收尾 ready。原结论留作历史，由明确撤销行作废；`ledger decision=done` 阻止后续技能继续执行。`noOutboundAttempts` 也检查所有关联非 observe 事件，不把伪造 sent 当收尾。

## B：开始 P0 前必须修

### B1 文件守卫与 pi 真实路径解析不同，可越界读取

**证据**：A.6 `policy.mjs`:9–28（方案:1150–1169）把非绝对路径直接接到 ws；没有处理 pi 的 `@` 前缀、波浪线、file URL。实际 pi `PI/dist/core/tools/path-utils.js`:35–47、`PI/dist/utils/paths.js`:59–87 会剥 `@`、展开 `~`、解码 `file://`，再读取。

独立调用 `checkCall('read', {path}, ws)`，然后调用本机 `createReadTool(ws).execute(...)`：

```text
read /private/tmp/.../outside-canary -> 拒绝
read ../outside-canary             -> 拒绝
read @/private/tmp/.../outside-canary -> null（允许）
真实 read 返回：OUTSIDE_CANARY
read ~/.pi/agent/auth.json          -> null（仅测策略，未读取该文件）
```

**失败场景**：模型或不可信消息诱导使用 `@/Users/...`、`~/...`，扩展审计写 allowed=true；模型获得工作区外的真实求职资料或凭据，T20 的工具名白名单仍全绿。没有 bash 并不能保护这个读取入口。

**修改建议**：按 pi 同一解析规则求最终路径，再 realpath 校验；最好由自定义受限 read 工具执行已经校验的绝对路径，消除检查与实际打开的差异。明确拒绝不支持的前缀；覆盖 `@`、`~`、file URL、Unicode 空格、符号链接及 read 的备用路径处理。测试必须实际调用 read，不能只测 policy 返回值。

### B2 `jw_notify send` 可指定任意引用路由，不能保证只发给本人

**证据**：A.6:31–37（方案:1172–1178）对 notify 只检查子命令和 `--w`；A.5:19–21（1083–1085）原样把 args 送给 Python。`notify.py:713–720` 直接传 `reply_to`；`:170–175` 的 `LarkAdapter.send` 在该值存在时调用 `+messages-reply --message-id`，不再使用 `--user-id im.userId`，也不核验该消息属于工作区私聊。

```text
checkScript('notify', ['send','--kind','alert','--key','foreign',
  '--reply-to','om_foreign_room','--text','canary']) -> null
--reply-t=om_foreign_room 的缩写形式 -> null
真实 notify.py + 假 lark-cli -> exit 0, sent:true
假 CLI 捕获：im +messages-reply --as bot --message-id om_foreign_room ...
```

**失败场景**：输入含机器人可访问的其他会话 message_id，模型把它作为 reply-to；发送路由指向该会话，违反 D2 的「仅本人私聊」。此处证明的是任意路由进入实际发送器，不声称已经真实送达外部会话；若机器人有相应访问权就可能漏发。

**修改建议**：为 send 定义完整参数 schema，禁用缩写，规范化后校验；引用只接受已核验来自本人、属于当前 chat 的实际消息 ID，合成 F1/F2 ID 不得作引用路由；不需要引用的 kind 禁止 reply-to。最终发送前再次核验路由，不能只靠 tool_call 前置提示。保留不经 shell 的 execFile 实现。

### B3 封口扫描没有完整性保证，双扫也未逐条复核

**证据**：A.10:44–56 / 121–124（方案:1414–1426 / 1491–1494）。`has_more=true` 而 token 缺失时直接 break；数字字符串就算「毫秒」；第二遍只比最大时间及最后 ID，不重新检查全部消息、非毫秒字段和未确认本人消息。

```text
missing_page_token: exit 0, appended:true, checks 全 true
  第一页 has_more=true，无 page_token，最后时间 1800000000123
  写入 boundary=1800000000124
after_bad_seal_poll ['恢复']
  未扫描到的下一页测试指令时间为 1800000000999
rescan_unacked: exit 0, rescan.userMessagesNotInWs 长度 1, appended:true
seconds_mislabeled: exit 0, nonMillisecondTimes=[]
```

复扫用例：第一遍只有时间 T+1000 的 bot 消息，第二遍多出 T+500 的未确认 user 消息，最后一条不变；脚本将该新增消息一起封掉。秒格式用例混入 `create_time='1800000000'`，另有正常毫秒消息，脚本既不报非毫秒也不拒绝。

此外，方案:386–393 在 P0.9 已运行具有 `jw_notify send` 能力的真实模型，但 `SEAL-REQUIRED` 到 P2.2（409）才建立；207、599 允许 marker 不存在时跳过封口。若冒烟模型误发一条 alert 并引来本人回复，P0 中止就走无封口出口。主观预期「此时尚未发出消息」不是强制保证。

**失败场景**：接口响应不完整时封口边界过小，解冻后测试「恢复」重放；两遍结果集合不一致时未核对的真实消息被吞掉；P0 冒烟提前发消息后无封口义务。

**修改建议**：响应 schema、成功状态、has_more/token 一致性、token 不循环、ID 不重复、时间单位与合理范围全部硬校验。两遍比较完整的规范化消息集合，并在第二遍重新校验所有阻断条件；任何不完整按失败处理。把 marker 放在第一个有 IM 发送能力的程序之前，或 P0 使用根本不能发送的专用策略。保留现有 +1、哈希、双互斥和前缀校验。

### B4 历史 PGID 复用会误杀；进程查询和孤儿兜底会漏报

**证据**：A.9:32–48（方案:1310–1326）显式 proc 的 pid+lstart 匹配正确，但随后接受所有历史 group：`pgid 相等 && lstart >= startedEpoch - 1`。group 没有过期/退役或实例连续性证明；新进程比旧组晚出生恰好总满足这个条件。

纯内存 ps 表注入，没有对任何不相关进程发信号：

```text
历史 group: owner=old, pgid=424242, startedEpoch=1
当前无关进程: pid=pgid=424242, lstart=2026-11-01, command=unrelated-job
owned() -> {424242: {..., owner:'old', why:'group 424242'}}
```

`kill` 在 A.9:68–77 随即按这些 PID 发 TERM/KILL，所以这个假阳性有真实杀进程后果。

其他同根缺口：

- A.9:18、A.4:30 忽略 `ps` returncode。注入 `CompletedProcess(returncode=1, stdout='', stderr='ps failure')` 后 `owned() -> {}`，即可能误报无残留。与本机最初抛 PermissionError 的情况不同，后者实际失败，不是假通过。
- A.4:131–151（方案:1025–1045）每两秒扫描后代，快生快退、双 fork/setsid 可能没被登记；A.9:57–61 的孤儿兜底只比基线 PID、只收 ppid=1，并按系统路径前缀忽略进程。PID 复用、路径在 skip 内的脱组进程都可能漏掉，不能证明「无残留」。
- A.4:83–87（979–983）的离线预检也启动 pi，但没有纳入该脚本的 PID/后代登记；手动 P0 预检超时的后代不能仅靠正式轮登记收尾。

**实际正向测试**：假 pi 正常运行、agent.sh 独立组运行之后，真实 `reap.py check` 返回 0。另创建一个本测试拥有的 sleep 子进程，按 PID+lstart 登记，`kill` 发 TERM 后它成为 `<defunct>`，此时退出 1；父进程 wait 回收后 `check` 返回 0。没有把僵尸暂态当作漏杀，也没有实测制造系统 PID 复用。

**修改建议**：历史组号不能作为永久授权；保留组实例/成员的连续归属证据，无法证明时标未归属并阻断，不能杀。发每个信号前复核精确进程身份。ps 查询失败必须 fail closed；孤儿基线用 PID+启动时间，不按可执行路径直接豁免。预检也进同一管理器。短命脱组仍不能保证捕获时，如实限定能力，并让未归属核验成为实际解冻门槛。

### B5 `pievidence.py` 会把缺失证据和截断运行判成 verified

**证据**：A.8:27–51（方案:1241–1265）。只有 starts−ends 检查，没有 ends−starts；缺 audit 直接用空数组；session 仅搜索头 ID 子串；只要求有退出码，不检查成功；不检查最终 assistant 的 `stopReason`。

用互相独立的最小证据目录运行提取脚本：

```text
missing-audit:  exit 0, verified （会话只有 session 头，完全没有 audit）
orphan-end:     exit 0, verified （只剩 bash 的成功 end，缺 start；toolsOutsideAllowlist=[]）
length:         exit 0, verified （message_end.stopReason=length）
exit-nonzero:   exit 0, verified （meta.exitCode=1）
```

这不是要求防恶意伪造日志：丢行、写失败、收集不全本来就是检查器要发现的故障。尤其 `PI/dist/modes/print-mode.js:110–129` 只在 text 模式把 assistant error/aborted/length 转为失败退出码；JSON 模式不能靠进程 exit 0 排除模型截断。`agent_settled` 只代表无后续自动工作，不代表任务完整成功（`docs/json.md:48`）。

**失败场景**：冒烟或手动轮结束但输出截断、审计漏存；工具开始事件丢失而成功 end 还在；T20 据空列表误判没有白名单外执行。

**修改建议**：要求正式轮 audit 的 session_start、工具 call/result 与事件按 toolCallId 对齐，校验 session 结构及工具结果一致性；开始/结束必须双向一一对应且顺序合理。检查最后 assistant stopReason、退出码、signal、最后 settlement 和预检结果。将「记录完整」与「用例成功」分开输出；完整但失败不能自动满足通过门槛。增加上述损坏负例。

### B6 F2 和分页检查器给出与实际内容矛盾的通过结果

**证据一**：A.12:69–78（方案:1694–1703），`conclusionUnchanged` 只是子串包含；`doneUsesSavedResultA` 仅要求发送文字包含「结果正文 A」。

```text
F2 实际发送正文：完全不同的错误结果，只夹带 结果正文 A 字样
seed.py check F2 -> exit 0
doneUsesSavedResultA=true，全部 checks=true
```

此测试先正常 seed F2，标 consumed，再用合成 out pending/sent 写入上述错误正文，无任何实际 IM 发送。通过项明确宣称「使用保存的结果 A」，实际没有使用，P8.7 的核心恢复判据被破坏。保留旧结论行、另加一个相反的新结论也不能靠 `CONCL in body` 排除。

**证据二**：A.13:36–44（方案:1754–1762）虽然输出 parent/sender/body 等字段，`ok` 只检查 target、页数、重叠和 isdigit。构造两页，目标只有 message_id 和 `create_time='123'`：

```text
page-find.py -> exit 0, ok:true, pagesRead:2
parent_id:null sender_type:null has_sender_id:false msg_type:null has_body_content:false
create_time_is_ms_digits:true
```

P2.9 的人工表格仍要求这些字段，所以仔细人工核对能挡住这一项；但 helper 的「字段结构符合即退出 0」契约（A.13:5）及回应中所称实际结构检查不成立，容易将未通过当通过。

**失败场景**：F2 模型复述/编写错误结果，只保留测试标签即可通过；分页字段不兼容时 Agent 仅看绿色 ok 而推进阶段。另 A.12 在发布 ready 后才取得基线行数（1669–1679），监听器可在两者之间运行，前半段行为被排除在增量检查之外。

**修改建议**：在发布夹具之前固定、持久化预期正文/结论及基线，最后原子发布 ready；与 `notify.compose/text_digest` 一致地比较完整结果，拒绝相反或重复结论。F2 对账全部实际发送尝试/终态。分页 helper 将全部要求放进 ok，检查真实毫秒范围、sender、parent 和 body；同一页重复 ID 也检查。不要让字段名充当验证。

### B7 `jwgate` 接受明确失败的另一台 Mac 记录

**证据**：A.2:68–86（方案:806–824）只对 `other-mac.txt` 执行 `-s`；既不解析 cursor/pending，也不收集 orphans 的处理结论、S1 listener PID、S6 快照或 BOSS 人工核对结果。方案:529 却以「打印全部通过后解冻」作为最终动作。

在合成 private-job-workspace、无存活登记进程、假 launchctl 返回服务不存在的环境执行原样 env.sh；另一台 Mac 文件内容为：

```text
核验失败：cursor=0，pendingCommands=1，尚未同步
```

结果：

```text
GATE_NEGATIVE_MAC 0 ✓ 解冻闸门全部通过
```

人工表格要求成功不等于脚本已经验证成功。S7（方案:194）又允许「做不到就保持那台 Mac 冻结」，与 529 的两机最终解冻口径应统一。

**失败场景**：执行者忠实记录失败内容，再看到最终绿色闸门，解冻后另一台机器重放测试指令；或未处理孤儿依旧运行，却只凭 `reap check` 全绿解冻。

**修改建议**：两机用结构化证据，校验 workspace/主机、边界、cursor、pending、核验时间与明确成功字段；缺失或失败均拒绝。孤儿核验与逐项人工确认应有明确、可验证的完成记录。脚本没有能力自动核验的项目要列为「仍需人工完成」，不能打印「全部通过」。保留最终人工解冻，但要求机器项和人工项都通过。

## M：重要，可在明确边界后处理

### M1 生产调用链与角色变化没有完整写死进最终报告模板

**证据**：回应末段明确说第一期只验证隔离 pi、带 bash/浏览器的生产命令留第二期；方案:151–165 和 A.7 实际也这样运行。但 §5.2:556–565 的模板只笼统要求能力边界/残余风险，没有强制写「生产命令行未验、只验证 jw_notify/jw_ledger 适配后的 IM/inbox 子集」；T15/16 名称仍可被单独摘成完整 pi/followup 验收。P8.3 的角色为 Agent，提示词却写「本人在终端手动启动」（503）。

**已明确的部分**：P7 未执行在 485–487 与模板 674 写死；T17 在 112、521、559、678 限定 jw_notify；假 Agent 在 101、451、667 明确仅状态机。不能说这些都没修。

**失败场景**：之后只看最终报告的人将第一期通过当作生产 bash+浏览器链路已验证，或误认该轮有本人终端交互条件。

**建议**：模板新增必填固定范围声明；明确保留的真实行为是技能文档读取、IM 对应与解释、inbox 编辑、锁、ready 生命周期、暂停保留、done 正文复用和收尾；不覆盖浏览器准备/巡检/外发、资料修改、生产命令及其权限。手动轮改称「Agent 发起的直接运行」，不再声称本人敲终端。此处不要求重议 D2。

### M2 离线复现入口与人工操作负担仍过重

**证据**：方案:253–341 只给脚本提取/环境搭建，没有给 §0.4 所称 27 个策略用例及假 CLI/假 pi 测试源；附录也没有自带离线 test 命令。本次必须自己写 harness，注入临时路径和假程序，才能验证反例。S3/S5/S6/S7、P8 的每轮 G、P9 还要求多份重复人工核对。

`jwrun`（A.2:16–28）以已有 meta 文件数量分配编号；另开专用 shell 是 P4/P5 明确要求，若同时同标签执行会覆盖相同输出。`runpi`（A.4:69–71）目录只有秒和标签，同秒同标签重试会 FileExistsError。两者通常会阻止或混淆留证，不能当作可靠的全局命令序号。

**失败场景**：本人要反复发指令/引用消息、睡眠唤醒、安装卸载、确认撤销/封口及切到另一台 Mac；任一步失败后还要人工判断到底缺哪份证据。没有离线一键回归，修改 13 个脚本后容易重复漏掉同类负例；并发采集可能覆盖证据。

**建议**：将脚本和版本化离线测试提取成单一受审包，保留一个只在临时根运行的测试入口；短 runbook 只列本人必须做的动作，重复机器判据由统一 checker 汇总。命令/运行目录用 UUID 或原子 mkdir，固定编号表映射到证据。保持 D1 的封口与人工最终解冻，避免为精简删掉必要门槛。

## L：次要

### L1 若干正文索引仍指向错误小节

**证据**：方案:38、49 把事件证据指向 §2.4，实际 §2.4（209）是可选的生产 channel=none 防护；事件要求散布于 §2.2、P8 G、§5.1 和 A.8。

**失败场景**：执行者按索引寻找事件完整性规则却进入另一项生产配置操作，增加误读和来回查阅。

**建议**：将索引统一指向 P8 G / A.8；文档重排后机械检查内部节号，勿只修回应文档。

## pi 参数与工具执行语义独立核对

| 项 | 核对结果 |
|---|---|
| `--tools read,edit,write,jw_notify,jw_ledger` | 纯名称列表替换默认选择，不叠加默认 bash；只有全为 `+name/-name` 时才增删默认项。`docs/cli.md:119–132`、`args.js:114–125`。MCP 有特殊保留规则，因此同时 `--no-mcp` 必要。 |
| `-ne` 与显式 `-e` | 禁止发现/配置/内置扩展，但显式扩展仍加载；`docs/cli.md:194–205`、`args.js:156–176`。本机离线预检实际加载了 jw-iso。 |
| `--no-skills --skill`、`-nc`、`--no-approve` | 显式 skill 仍加载；禁上下文发现及项目局部信任资源。没有看到错误开关。 |
| `--mode json --session-dir` | 参数存在，JSON 模式输出事件，session-dir 指定持久化位置；本次未调用模型，不能由离线自检声称完整业务 session 已验。 |
| tool_call 覆盖与异常 | 本机文档 `extensions.md:261、375`：嵌套调用也经过钩子，钩子异常 fail closed。本扩展没有其他任意工具执行通道；自检命令直接调用 runScript(status)，不是经 jw_notify.execute，正文「经 jw_notify 自检」说得过满。 |
| execFile / argv | 没有 shell 字符串拼接执行；`--w` 拒绝工作区及其 argparse 缩写；ledger append 必须精确三项且 kind=observe，不能通过重复 --json 偷换。未发现这些点的 shell 注入绕过；真正漏洞在路径语义和 reply-to 路由。 |
| 预检失败是否启动业务 pi | 假 pi 返回预检失败：runpi exit 97；调用日志 offline=1、actual=0。成功路径累积 offline=2、actual=1，正式运行 exit 0。失败门控确实有效，不能说预检失败仍调用模型。 |

真实本机 pi 的离线预检结果：

```text
exit 0, preflightOk:true
active = all = [read, edit, write, jw_notify, jw_ledger]
commands = [jw-selftest, skill:job-wingman-followup]
notifyStatusExit = 0
bash / launch_browser / codemode 的策略拒绝均非空
```

## 13 个提取产物的执行覆盖

哈希列为 SHA-256 前 12 位，供对应本次方案版本；完整字节一致性比较在提取时执行。

| 文件 | 行数 / 哈希前缀 | 本次离线验证与边界 |
|---|---|---|
| A.1 gen-config.py | 36 / a05c6f099995 | 临时 e2e/jh/node-bin 生成，两份配置经真实 load_listener_config 解析，exit 0。只写指定临时根；未使用生产默认目录。 |
| A.2 env.sh | 87 / 6b94f56ee259 | bash 语法检查；真实 validate/status、jwacts；jwrun 返回 0/2 与 meta 一致；负面 Mac 记录仍过 gate，见 B7。 |
| A.3 agent.sh | 57 / 2b443fcaf673 | bash 语法检查；独立进程组 ok 模式运行 exit 0，PID=PGID、listener 环境与提示有记录。tcc 未执行；hang/escape 分支静态核对，不声称已实测全部模式。 |
| A.4 runpi.py | 164 / cb14c0b39a6f | 真实 pi 离线预检通过；假 pi 成功/预检失败双路径，记录 session、PID/PGID、退出码；未运行真实模型。 |
| A.5 jw-iso.ts | 71 / 55728d7e955b | 真实 pi 离线装载、工具/命令清单和 status 自检通过；未用模型验证钩子的业务调用，路径反例使用真实 read 实现直接验证。 |
| A.6 policy.mjs | 55 / af53dfcc4ab2 | bash/browser/codemode/subagent/fetch 拒绝；普通越界与 --work 拒绝；@ 路径与任意 reply-to 放行。 |
| A.7 im-only.md | 6 / 4da38d893a9f | 非可执行脚本；字节一致，真实预检使用系统提示，假 pi 正式轮 prompt 包含全文。 |
| A.8 pievidence.py | 58 / f78d82efce9f | 假 pi 正向；缺 audit、缺 start、length、非零退出的四个错误接受负例。 |
| A.9 reap.py | 86 / 4448c8b1cdaa | 获准后真实 check；本测试子进程 TERM/僵尸回收后 check；纯合成 PGID 复用/ps 返回失败反例。未向其他进程发信号。 |
| A.10 seal.py | 152 / 972b99672c1f | 121 条/3 页/+1 正向；旧三个反例、旧哈希拒绝；缺 token/复扫新增消息/秒格式负例；FakeIM 证实错误封口后仍执行恢复。 |
| A.11 closeout.py | 91 / e529371086de | ready、done+ready、重复收尾；全部检查通过，事件无额外外发。 |
| A.12 seed.py | 87 / 7caf75ffeb2b | F1/F2 生成均 exit 0；未消费 F1 正确失败；F2 错正文却通过。夹具只写临时 inbox/ledger，不调用 IM。 |
| A.13 page-find.py | 47 / 95b5fc913084 | 假 CLI 两页读取，缺关键字段目标仍 ok；没有真实接口查询。 |

P0 的离线主体（提取→配置→专用 shell→工作区→build→validate/status→真实离线预检）可重建；构建只在临时 `dist` / 安装目录，commit 对上基线。测试使用临时路径和假 CLI，不能声称生产身份/固定 PATH 版本断言已通过；P0.9 本来就需要真实模型，本轮禁止且未执行。**只按方案文本不能复现 §0.4 的那套离线测试**，因为测试源未随方案提供；本次另写了独立 harness。语法可运行不等于判据可靠。

## P0–P9 回归判定

| 阶段 | 本轮结论 |
|---|---|
| P0 | 配置/build/离线预检可执行；不能启动真实冒烟，先修 B1/B2/B3/B5。 |
| P1 | 身份、权限由实际收发验证，失败时应用权限改动有留痕；本轮未执行。 |
| P2 | 收发、引用、幂等、同秒判据基本客观；T4a helper 存在 B6，T4b 已正确标为可选适配器跨页观察。 |
| P3 | 指令顺序/串行/applied/安全暂停可核对；共享私聊收尾依赖 B3/B7。 |
| P4 | decisionHint、耗时和 caffeinate 观察已修；操作需等待真实回复，不以静态检查代替。 |
| P5 | 明确只验监听器状态机，不计技能行为；收尾证据依赖 B4。 |
| P6 | SIGTERM 与 kickstart 已分开；TCC 结论限定具体调用链，D4 不授权保留；孤儿判据依赖 B4/B7。 |
| P7 | 第一期明确不执行，模板固定未执行，已关闭相关范围歧义。 |
| P8 | ready 生命周期与恢复夹具已补；B5/B6 的误通过必须修，B3 旧收尾问题已关闭；生产能力范围补 M1。 |
| P9 | 不论成功失败都收尾的文字成立；闸门存在 B7，封口/进程分别依赖 B3/B4；不能据绿色脚本解冻。 |

§1.2 的通过/不通过/未验证枚举和无「有条件通过」已统一；没有证据把静态检查说成真实验收已通过。但 §0.4/回应中的自测成功不等于本轮发现的安全属性成立，最终报告必须保留本轮上述边界。

## 交付边界

本报告是执行前复核，不是验收报告。临时测试产物在完成证据摘录后清理；仓库只新增本报告，既有方案、回应、上轮报告及 AGENTS.md 保持原字节。未修改或批准任何真实验收状态。

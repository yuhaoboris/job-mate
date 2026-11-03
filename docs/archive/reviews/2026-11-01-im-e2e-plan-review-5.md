# IM 第一期方案第五版最后核验与验收脚本交付

> 历史归档：保留当时的版本、结论与行号；文件引用已按当前目录更新，私人标识和本机路径已脱敏。本文不代表当前实现或验收状态，当前状态见项目的 `docs/status.md`。

日期：2026-11-01。角色：唯一主责对抗审查员。代码基线：`9a3387cf0b708b759163edba406c710dcb1267c7`。

## 结论

**段 A′：B1 通过；M1 通过（保留 K2）；M2 通过但有残余（K3、K5）。段 B：10/10 个入口已实现，统一离线自测 154/154 通过（105 个负例、49 个正例）。** 本轮不再要求修改方案。只交付 kit 与本报告，未执行 P0–P9；不把离线自测写成真实 IM 端到端验收通过。

交付根：`<用户主目录>/jw-e2e/kit/`。10 个脚本、README、共享模块与完整自测证据均在此目录。没有建立 ws/evidence/dist/probes 等数据根。未发现对未登记/不可归属进程发信号、终止其他 pi/监听器会话、真实外发或生产写入的新事实。开发中发现并修复一次**已登记自测运行器自杀式收尾**，下文单独披露，不隐去失败证据。

## 段 A′：三项限定核验

| 项 | 结论 | 实际执行证据 |
|---|---|---|
| B1 FC1–FC4 | 通过 | S-6 的 `fixture()` 实际加载 pi 1.4.0 `createReadToolDefinition`，由真实 `runAgentLoop` 执行覆盖工具的 operations；内存 stream 代替模型。V5 完整文件拒绝 → verified；U20 只删除同一份完整拒绝样例的一条 deny → unverified；V6 工作区内 missing.txt → op_error、无 deny、verified；U21 同一回答两个拒绝只删 d2 的 deny → unverified，E7 明确输出 `d2 FC3 missing/mismatched deny`。V7 两条 deny 齐全 → verified。U22–26 均被拒。 |
| M1 PA1–PA3 | 通过，保留 K2 | `pi-lib.mjs` 从固定的本机 1.4.0 安装实际 import 真实 read/edit/write 定义与 `validateToolArguments`；有 prepareArguments 时先调用，再执行校验。V8 原始 offset:null、有效无 offset → verified；V9 原始 offset:"20"、有效数字20 → verified；V10 oldText/newText 被真实 edit.prepareArguments 归入 edits → verified。U27 只改 hook.path 为重放解释不了的值 → unverified（PA2/PA3）；U28 多参数、U29 execute 与 hook 不同也拒绝。没有用宽松相等近似重放。 |
| M2 临时文件与 vanished | 通过但有残余 | 对真实 notify.write_heartbeat，在 ledger.os.replace 前捕获快照，再在枚举后的 before_open 钩子执行真实 replace；确定性得到 `ledger/.listener.json.<8位小写十六进制>.tmp` 与 vanished。S-4 的正则只允许 lock/pause/listener 三个 json 名及 `[0-9a-f]{8}`，与 ledger.write_atomic 的 uuid.hex[:8] 一致。W1/W2/W8 满足；W3/W4 持续临时文件、W5 events.tmp/非法hex、W6/W7 未知稳定/消失文件均不满足。保留非原子快照 K3 与目录符号链接 K5。 |

证据位置（均相对于 kit）：

- `selftest-delivery/S-6/{V5,U20,V6,U21,V7,V8,V9,V10,U27}/`：每项保存 events.jsonl、audit.jsonl、session/one.jsonl、meta.json、result.json。
- `selftest-delivery/S-4/heartbeat.json`：真实 write_heartbeat 替换前快照与枚举后消失快照。
- `selftest-delivery/S-5/offline-cli.json`：真实 pi `--offline /jw-selftest` 退出0；活动/全部工具都是五工具，命令恰为 jw-selftest、skill:job-wingman-followup；临时 HOME。

S-6 的 tool/message_end 来自真实 runAgentLoop；session 文件的条目包装由自测写入，字段按 pi 1.4.0 session-format，**不是声称真实模型 CLI 已跑过这些样例**。每个负例从完整对照引入单一指定故障；同一语义在关联记录中镜像改变或成组丢失（例如 U14）按方案的故障单位处理。U20/U21 则严格只删一行 audit，不改任何事件、会话或 meta。

## 段 B：实现清单和统一自测

| 入口 | 负例通过 | 正例通过 | 自测退出码 |
|---|---:|---:|---:|
| S-1 `s1-config.py` | 3/3 | 2/2 | 0 |
| S-2 `s2-shell.py` | 2/2 | 2/2 | 0 |
| S-3 `s3-agent.py` | 3/3 | 4/4 | 0 |
| S-4 `s4-runner.py` | 9/9 | 10/10 | 0 |
| S-5 `s5-extension.mjs` | 28/28 | 9/9 | 0 |
| S-6 `s6-evidence.mjs` | 33/33 | 10/10 | 0 |
| S-7 `s7-process.py` | 3/3 | 6/6 | 0 |
| S-8 `s8-audit.py` | 7/7 | 1/1 | 0 |
| S-9 `s9-close.py` | 5/5 | 1/1 | 0 |
| S-10 `s10-fixtures.py` | 12/12 | 4/4 | 0 |

- S-1：真实 notify.load_listener_config/im_config/LarkAdapter，subprocess.run 被捕获，发送和拉取均核 argv；真实 zsh 默认不拆词反例；监听器分别 2分钟/10秒、25分钟/30秒。
- S-2：固定 PATH 与 pipefail shell、notify/ledger 固定工作区、lk 固定配置档、唯一目录采集 stdout/stderr/退出码/时间、raw messageId 记账、状态/外发检查/模式/清场/系统快照；假 lark-cli 验 argv。
- S-3：7 模式实际执行；hang-once 忽略 TERM 后代、escape-once 脱组进程均登记后清理；tcc 只核命令、pi 转交桩运行器。
- S-4：预检失败97、正式运行0次；唯一目录；成功预检后的假正式运行；独立组中断实测；仅向归属 PID 发信号断言；G-W 四场景、W1–W8（另测非法hex）及真实 heartbeat。采样登记与收尾，提示词、事件、session、差异落盘。
- S-5：37项实际工具场景，包含所有路径变体、禁止路由/参数、账本限制、hook 禁用工具、status、普通I/O错误、并行 deny、异常不外抛。另由真实离线 pi 验证扩展加载与 R1；没有真实模型调用。
- S-6：**U1–U29 全部 unverified，FL1–FL4 全部 failed，V1–V10 全部 verified**。解析按 LF（不会把 U+2028/2029 当换行），检查 session 全文/声明/时序/结果/审计/有效参数，输出 E1–E9、OK1–5 和调用明细。
- S-7：live/收尾、PGID复用、组长退出残留、未登记成员、僵尸、ps失败/缺自身；复用与未知归属用记录的 signal spy 断言零信号，实际受控子进程清理到0。
- S-8：缺token、重复token、秒级时间、重复ID、缺sender均 unverified/2；第三方app/他人user均 failed/1；干净样例 verified/0。
- S-9：在临时工作区实际撤销；五项各缺一项分别操作真实记录/inbox，然后调用真实 ledger decision 与状态折叠核验，其他四项保持真。
- S-10：**F2负1–11 全部 failed，F2正1–3 与 F1 正例全部 verified**；每个正例从 acceptedBody 重发，真实 cmd_send 返回 already_sent、键不变、FakeIM次数不变。另一个负例用第三轮的原始错误正文和原始键经过真实 build_parser/cmd_send 重演。notify 的 compose/text_digest/deliver 未替换，仅 IM/context 使用 FakeIM。

所有结果在 `selftest-delivery/summary.json` 与各编号 `results.json`；stderr/stdout 也保存。最终对 S-2 模式标记重置、S-10 accepted 文件位置做了接口收尾后，只复跑这两项（`postcheck-S-2`、`postcheck-S-10`），均通过。对应编号的数量不重复计入154。

前四轮仍适用的反例已落入 S-1（zsh）、S-2（管道/目录）、S-4/7（进程归属）、S-5（路径/路由绕过）、S-6（证据丢失/截断/分类/规范化）、S-8（分页损坏）、S-9（测试决定未收尾）、S-10（错误正文/键与正文版本）。**已删除的生产封口、游标、解冻闸门不重新实现或测试，也不宣称它们已修复通过**；第五版已改独立测试应用。

## 运行器开发中发现并修复的问题

增强 S-4 正式运行自测时曾退出143。证据显示：runner PID 7602 自身记录误放入正式进程的 processes.jsonl；正式假 pi 7616 退出后，cleanup 把仍活着的7602视为应清理对象，写 cleanup-member 后发送 TERM，终止了本次自测运行器。没有按命令字符串匹配，没有触及未登记 PID，也没有终止其他 pi/监听器。

修复仅在自有验收脚本：runner 写入 runner-processes.jsonl，pi/preflight 的检查与收尾使用独立 processes.jsonl；额外增加正式运行和真实 SIGTERM 中断回归。修后完整 S-4 19项通过。原失败记录保存在 `selftest-delivery/development-self-termination/`，不纳入“通过证据”计数。该实现缺陷不能作为产品代码或 P0–P9 的故障结论。

## 残余风险 / 未验证

保留第五版 K1–K3：多份证据一致篡改无理论保证；pi 参数重放依赖固定版本；非原子快照与短命进程采样窗口。R2 的 realpath 检查和打开之间仍有方案已接受的目录替换窗口；本期不开放任何模型创建符号链接的工具。

| 编号 | 缺口与触发条件 | 本期影响 / 第二期建议 |
|---|---|---|
| K4 | 旁证基线只持久化最新一条消息 ID 与毫秒时间。若更早另一条历史消息恰好与 B0 同毫秒，分页重新取回时可能被当作 B0 后消息。 | 可能把干净私聊误报异常，不会把缺字段页当干净；对应 T21 记未验证并人工核对。第二期采用同毫秒 ID 集合/服务端稳定顺序边界。 |
| K5 | S-4 按文件快照；Path.rglob 不递归目录符号链接，is_dir 分支跳过目录。若外部操作者添加/替换目录符号链接，该目录项自身不进入文件哈希表。 | G-W 不是完整文件系统拓扑证明。本期无此类模型工具，仍保留观察限制；第二期如放开符号链接/目录操作，使用 lstat 记录每个目录/链接条目并扩展快照判据。 |

真实飞书收发、真实模型遵循、launchd、睡眠唤醒、实际 P0/P8/P9 旁证和本人HR核对均**未执行/未验证**。按第8节，上述证据细节不启动第六版修订，不阻塞本次 kit 交付。

## 文件边界和哈希

本仓库只新增本报告；方案、回应、既有报告、AGENTS.md、scripts/shared/skills/tests 不修改，不提交 git。开始时已有 AGENTS.md 改动和 plans/reviews 未跟踪文件原样保留。没有发送真实飞书消息、装/加载 launchd、读写 job-hunt、写 ~/.config/job-wingman 或调用真实模型。

主清单 SHA-256：`8fd5e1e192f5d4bd025a1c3e7a9ff5c9b850dfd506fd6ff8055a50648c35af02`。

自测产物清单 `selftest-delivery/MANIFEST.sha256` 自身 SHA-256：`0cf476049360626d29c90a21ef329300b42716cc7ea6723e8bedf6835f6e9558`。主清单按方案排除自身与 selftest-delivery；独立产物清单覆盖后者全部文件（除自身）。两个清单合起来覆盖所有交付文件；两份清单自身摘要在此固定。

主清单全文：

```text
bb716101f5de293f6c422e198752391a7f37a7a4d603ce03eca376e77953154b  README.md
6f1662c44ee006ba372fd5a80995e0d66803a3bb7fff743f63cdd00381af5a7d  common.py
f2a36b8ba970d4f59ed0a3bb9d20e3be8101f69626bdcfc9e4d5baf2a7070736  pi-lib.mjs
f185dcd12c23893a0d211b307f81ab4db08baa40eff223fa6b658cb18bfcd682  s1-config.py
997da945afca6db7ac082a58c89939b79fcb1b1a082006173850ab468377dc63  s10-fixtures.py
47e7e44aec526ce2a78338e4947347d5db2f2c304b0f2f548bd9e4580369071e  s2-env.sh
ebf5ca228b437f4f09b4952328d93d8abe2a6df65ca7aeac92bd95d6dc609977  s2-shell.py
a48a637527502bd9c0a5d79be9ab286a8d3ba4046468f52d88e8e8d56ce137ac  s3-agent.py
235e5f7f2298644d913409958d0985896f55c547ab9fd9816981219b599752d3  s4-runner.py
3a7b9c0a52b377969f92c74ff535381b74fd0ff0de792286ea01f9d4da4a6f89  s5-extension.mjs
385e0a8c2a2bb0b2c762eabb272d9d1f6b46af2ff4cf1737d3df3aa043ca3e99  s6-evidence.mjs
823e25c2f45a238d85ea06139e78520f1b30c0b7475a3695a08a22c9be972cf2  s7-process.py
4ac560b309fca5309387e6f4f72b06c2ed818d0b7837b9189a409ef03d7969c7  s8-audit.py
f65888352375e0660a2ac31223f83e876eac4937a01fb336fb74c16dbb0c6641  s9-close.py
```

审查输入绑定：

| 输入 | SHA-256 |
|---|---|
| docs/plans/2026-11-01-im-phase1-e2e-acceptance.md | `4724e261920e1530806c97638f5045c504f376af0196e00f05927cd6ba68f3a7` |
| docs/archive/reviews/2026-11-01-im-e2e-plan-response-3.md | `0a5a9eb2db4230a5663b9334a8f91ef968b4e29e0c6c04b4d63bb3852f108da5` |
| scripts/notify.py | `b480d91d22e6365d7b0bb0fcf33f561d63b6694f33f9bf745b835cd67fad3f71` |
| scripts/ledger.py | `48ff142c05cd3330bf181fa9666eb93357e6083294e144259ab48c33000a4de7` |

交付落盘复核：目标根顶层恰为 kit；两份清单逐项 SHA-256 与文件集合核对全部通过。开发中断样例的4份登记文件逐一 check=0。仅清理本次审查临时根与本次遗留的合成场景临时目录（本机定位已脱敏）；失败证据已归档到 kit，不清理其他历史临时目录。

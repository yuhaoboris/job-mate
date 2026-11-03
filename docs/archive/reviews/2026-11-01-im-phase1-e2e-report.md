# IM 决策回路（飞书）第一期端到端实测报告

> 历史归档：本文记录当时版本的方案或实验结论，不是当前安装、配置或执行指引。当前规则见 [设计](../../design.md) 与[项目状态](../../status.md)。文中的接口须按当前契约重新核对，待验收事项不因归档而关闭。

> 项目已于 2026-11-02 更名为 job-mate（职伴）。本报告保留实测时的 job-wingman 名称和证据文字；当前状态见 [项目状态](../../status.md)。

- 日期：2026-11-01（Asia/Shanghai，19:37–22:42）
- 执行人：执行验收的 Agent（对抗审查员角色），本人配合真机操作
- 方案：`docs/archive/plans/2026-11-01-im-phase1-e2e-acceptance.md`（第五版）
- 证据：`~/jw-e2e/evidence/`（含测试应用标识、消息原文与 pi 会话，不进本仓库）。**按本人要求，`~/jw-e2e`（含 kit 与全部原始证据）已于 2026-11-01 22:50 删除**；本报告保留的结论、判据与路径供追溯，原始文件不再存在。上一轮 P0 数据根曾归档到 `~/jw-e2e-archive/2026-11-01-p0-run/`；2026-11-02 整理时确认该目录也已不存在，删除时间未核实。
- 交付 kit：`~/jw-e2e/kit/`，`MANIFEST.sha256` = `7ee709ee…b207ff`，30/30 文件校验通过，接管后只读

---

## 1. 结论

**第一期结论：不通过。**

- 通过的：P0、P1、P2、P3、P4、P5、P9 的全部门槛项；P6 除 6.7 外的门槛项；P8 的 8.2/8.3/8.4、8.6b 与 8.7。
- 不通过 / 未验证的三处：
  1. **P6.7（T12）睡眠与唤醒补拉**——本人明确要求跳过，**未验证**，需复测。
  2. **P8.5（F1，T16）**——`不通过`：真实 pi 对「结论：无需动作、ledger 未 done」的预存 `ready`，仍发了 1 条 `done` 结果通知和 1 条 `summary`；方案 2.6 要求基线后没有任何新的 `out` 记录。
  3. **P8.6（F2，T16）**——`不通过`：F2 六项子判据中 5 项成立（正文、键、路由、可复用正文、consumed/无事件），仅第 1 项「一次尝试」不成立，因为同一轮多了一条 `summary`，基线后 `out` 记录为 3 条而非 2 条。
- 未执行：T14（锁屏桌面工具，按方案不做）、T16b（P9.3 可选）、P8.6b ① （缺 `F2-accepted.json`，见 6.4）。
- 没有出现中止类发现：无 HR 外发、无生产写入、无对未登记进程发信号（T19 / T20 一票否决项均为通过）。

**固定范围声明（方案 1.4 原文）**：第一期在隔离配置的 pi 中验证 followup 技能的 IM / inbox 子集——技能文档读取、答复与事项的对应和解释、inbox 编辑、运行锁、`ready` 生命周期、暂停时保留、`done` 正文复用与收尾。该 pi 的 read / edit / write 限于测试工作区与 inbox，脚本能力只有 `jw_notify` / `jw_ledger`，没有 bash、浏览器、桌面工具。**未验证**：浏览器准备、会话巡检与外发、资料修改、生产命令行及其权限（含 bash 单条命令上限）、`clarify` 发送。P8.3、P9.2、P9.3 的 pi 由执行验收的 Agent 直接启动，不是本人在终端交互。

---

## 2. 版本

| 项 | 值 |
|---|---|
| 仓库 HEAD | `6df75f7`（构建时 `dirty: false`，`BUILD.json` 一致） |
| lark-cli | 1.0.96（`doctor` 提示有 1.0.98，按方案冻结，未升级） |
| Python（固定 PATH 下 `/usr/bin/python3`） | 3.9.6（Xcode 开发者 Python；config 与 plist 均使用该路径） |
| pi | 1.4.0（`~/.pi/agent/bin/pi` 与 `current-version` 一致） |
| Node | v24.18.1（`~/.nvm/versions/node/v24.18.1/bin`） |
| macOS | 26.6.2 |
| 验收 kit | `MANIFEST.sha256` `7ee709ee…b207ff`；`P0-kit.manifest` 与 `P0-kit.sha256` 存证；P9.5 复核 30/30 一致、无未列出文件 |

测试应用：`<TEST_APP_ID>`（配置档 `jw-e2e`），测试私聊 `<TEST_CHAT_ID>`，本人 open_id `<TEST_USER_ID>`。默认配置档全程为生产应用 `<PRODUCTION_APP_ID>`（P0/P9 快照一致）。

---

## 3. 门槛项结果表

| 编号 | 待实测项 | 结果 | 证据位置 | 备注 |
|---|---|---|---|---|
| T1 | 机器人身份拉取私聊消息 | 通过 | `cmd/P1-whoami-*`、`cmd/P1-doctor-*`（`bot_identity: ready`）、`cmd/P2-3-poll-*` | — |
| T2 | 被引用消息的 `parent_id` | 通过 | `cmd/P2-3-poll-*`；`ws/ledger/im.jsonl` 中「接受」的 `parentId` 等于 02 通知的 messageId | 飞书返回 `parent_id` |
| T3 | 飞书幂等：同一键只发一次 | 通过 | `cmd/P2-7-idempotency-*`、`cmd/P2-7-verify-*` | 同一飞书幂等键两次返回同一 `message_id`；私聊中各 1 条 |
| T4 | `fetch` 实际跨页 | 通过 | `evidence/P2-8-paging.json`、`cmd/P2-8-poll-*` | 游标到「分页尾」共 59 条，`fetch` 翻 2 页；「分页尾」只记录 1 次 |
| T5 | 连发不漏不重复 | 通过 | `cmd/P2-6-poll-again-*` | b1/b2/b3 三条 messageId 互异、时间 19:42:44/47/49 不递减；再 poll `new` 为空；**同秒未覆盖，由单测兜底** |
| T6 | 指令跨进程串行、按时间执行、安全暂停 | 通过 | `cmd/P3-1-pause-*` … `cmd/P3-6-safe-pause-*` | 含并发双 poll 只 applied 一次；`pause on --by followup` 后 poll 不解除，`pause.json` 保留旧指令 ID |
| T7 | 等待：回复 / 暂停 / 超时 | 通过 | `cmd/P4-1-wait-timeout-*`、`cmd/P4-2-wait-*`、`cmd/P4-3-wait-*`、`P4-1-assertions-*.txt` | 超时 91 秒、`timedOut: true`；回复后 **2 秒**返回（快于方案写的 10–40 秒窗口，因恰逢 10 秒轮询点）；暂停后 13.7 秒返回且 `paused` 非空；等待期间 caffeinate 断言 88 次采样全在，结束后 0 |
| T8 | 监听器状态机（假 Agent） | 通过 | `cmd/P5-1-clear-*`…`cmd/P5-6-interrupt-*` | 见 6.1 的 kit 缺口与 5.1 重跑说明 |
| T9 | 脱组进程与残留 | 已记录 | `evidence/P5-5-watch.log`、`s7-process.py check` 输出 | 唤起居留 1 个脱组进程（pid 73256，ppid=1、自成组），`check` = 1；`kill` 后 = 0 |
| T10 | launchd 托管、固定 PATH、心跳、SIGTERM、KeepAlive | 通过 | `cmd/P6-1-dryrun-*`、`cmd/P6-1-{pi,node,lark}-*`、`cmd/P6-2-*`、`cmd/P6-5a-*` | 见 6.2：plist `LANG` 由默认 `zh_CN.UTF-8` 改为 `C`（kit 缺陷所致，PATH 判据不受影响） |
| T10b | `kickstart -k` 时旧唤起的终态 | 已记录 | `evidence/P6-5b-automator.log` | 旧唤起 `stopped`、pid 81843→83578、`startedAt` 更新、再唤起 `ok`、进程检查 0 |
| T11 | TCC 链权限 | 判定 | `evidence/tcc-commands.json`、`tcc-results.json`、`P6-4-*` | 结论见 4.3，仅适用于 `launchd → python3 → screencapture / osascript` |
| T12 | 睡眠与唤醒后补拉 | **未验证** | `evidence/P6-7-skipped.md` | 本人要求跳过；准备状态（caffeinate 断言生效）已存证 |
| T13 | `kill -9` 后的孤儿与重复唤起 | 已记录 | `evidence/P6-6-automator.log` | 旧 `started` 无终态；新旧 Agent 短暂并行；进程检查 0 |
| T14 | 锁屏时桌面工具 | 未执行，不构成能力结论 | — | 按方案第一期不做 |
| T15 | pi 无界面完成一轮 | 通过 | 7 轮全部 `verified`（见 5 节） | 仅限固定范围声明 |
| T16 | `ready` 生命周期与 F1/F2 | **不通过** | `cmd/P8-2-*`、`P8-3-*`（`ready → consumed`、暂停保留 `ready` 均通过）；`cmd/P8-5-F1-check-*`、`cmd/P8-6-F2-check-*`、`evidence/P8-6-F2-subitems.json` | F1：多出 `done` + `summary`；F2：仅「一次尝试」不成立 |
| T16b | 显式禁发保留 `ready` + `--no-send` | 未执行（部分观察） | `cmd/P8-3-direct-*` | P9.3 可选未做；但 P8.3 中 pi 主动使用 `lock acquire --no-send` 并保留 `ready`，与 T16b 的行为一致，仅作观察 |
| T17 | `jw_notify` 通道跑满长命令 | 已记录 | `cmd/P9-2-t17-*` | `wait --max 300 --interval 20` 单次调用 `durationMs: 301289`（301.3 秒） |
| T18 | `done` 正文版本去重 | 通过（① 未验证） | `cmd/P8-2-t18a-*`、`evidence/P8-2-t18a.json`、`cmd/P8-6b-t18b-*` | 正文 A 复原后重发 `already_sent`、键不变、飞书仍 1 条；正文 B 首发新键 `…:11c466c0a203`、再发去重；**8.6b ① 因缺 `F2-accepted.json` 记「未验证」** |
| T19 | 生产隔离 | 通过 | `cmd/P0-3_3_4-*`（路由 argv）、`cmd/P2-2-check-*`（chatId）、`cmd/P9-1-*`、`P9-4-procs-*`、`P9-system`、`cmd/P9-5-git-*`、`evidence/P9-boss.txt` | 三项全真：外发类事件 `[]`；无浏览器/桌面/shell 类工具执行、无归属不明残留；本人逐条核对 BOSS 后确认无本次验收造成的外发 |
| T20 | pi 能力隔离与执行证据 | 通过 | `pi/P0-9-*`、7 轮 `S-6 verified` | P0.9：越界读 3 次 `boundary_denied`、写 ledger 被 `mkdir_not_inbox_dir` 拒、4 个 `jw_*` 越权调用被钩子拦下、`status` 实际执行；各轮无白名单外工具执行 |
| T21 | 测试机器人无第三方应答 | 通过 | `evidence/P0-third-party.txt`、各阶段 `S-8 audit` 均退出 0 | 无第三方 app 消息、无他人 user 消息、无未归属 app 消息 |

---

## 4. 判定与观察

### 4.1 T9 脱组进程的能力边界
监听器清理范围是「受管进程组」。`escape-once` 派生的脱组进程在超时/结束后**仍在运行**，`s7-process check` 报 1（可归属，因为它被登记过）。结论：监听器不能清理脱离进程组的后代；能发现它，靠的是验收方的登记，不是产品自身。

### 4.2 T10b `kickstart -k`
`launchctl kickstart -k` 下旧唤起**记为 `stopped`**（与 6.5a 的 SIGTERM 相同），不是「停在 started」。pid 与 `startedAt` 都变化，KeepAlive 与再唤起正常。观察：`launchctl` 的 SIGTERM 路径与 `kill SIGTERM` 在本次实测中表现一致。

### 4.3 T11 权限链（只用一条链的结论）
`launchd → python3（Xcode 开发者 Python）→ /usr/sbin/screencapture` 退出 1、**没有生成截图文件** → 该链**没有**屏幕录制权限。
`/usr/bin/osascript -e 'tell application "System Events" ...'` 退出 1，弹出了自动化授权框，macOS 把请求归到 **Xcode.app**（因为 python3 属 Xcode 的开发者框架），本人按 D4 点「不允许」→ 该链**没有** Apple Events 自动化权限。本次未出现辅助功能（-25211）相关提示。
只适用于这条链；其他宿主程序、其他二进制不在结论范围内。

### 4.4 T13 `kill -9`
无终态：旧 `started` 记录不会补 `stopped`/`failed`，台账里会留一条永远「进行中」的唤起。
**新旧 Agent 短暂并行**：旧包装进程继续跑完（本次是假 Agent 的 20 秒睡眠），新监听器已起来并对同一答复再唤起一次。真实 pi 下这会造成两个 pi 同时操作同一工作区，属需要评估的风险（见 8 节 K6）。

### 4.5 T14
未执行，不构成能力结论（方案第一期不做）。

### 4.6 T16b
P9.3 未做，记「未执行，不构成能力结论」；但 P8.3 里 pi 在全局暂停下自行使用 `lock acquire --no-send` 并保留 `ready`，行为与 T16b 的期望一致，只作观察。

### 4.7 T17
`jw_notify` 通道上单条 `wait --max 300` 跑满 301.3 秒，S-6 `verified`。结论只适用于 `jw_notify` 通道（本期没有开放 bash）。

---

## 5. 真实 pi 每轮

全部 7 轮 `S-6 verdict = verified`，`exitCode 0`、无信号、`processCheck 0`。明细见 `evidence/P9-pi-rounds.json`。

| 轮次 | 目录 | launchId | 会话 | 工具调用 | 拦截/边界拒绝 | verdict |
|---|---|---|---|---|---|---|
| P0.9 隔离冒烟 | `pi/P0-9-…hsh4_krt` | —（直接运行） | `01a196e8…e5ed` | 10 | 8（3 次越界读 + 1 次越界写 + 4 次 `jw_*` 拦截） | verified |
| P8.2 | `listener-…o7srvrr5` | `launch-…-354b` | `01a1976d…4d1c` | 29 | 0 | verified |
| P8.3 | `P8-3-direct-…8e5toi_f` | —（直接运行） | `01a19775…cbee` | 26 | 0 | verified |
| P8.4 | `listener-…21f94lp9` | `launch-…-4f2f` | `01a1977e…5a0b` | 28 | 0 | verified |
| P8.5 F1 | `listener-…4vu6c53g` | `launch-…-2d5f` | `01a19784…04f0` | 21 | 0 | verified（轮次本身成功；判据不通过见 6.3） |
| P8.6 F2 | `listener-…sv9grxuw` | `launch-…-e406` | `01a19789…e82b` | 35 | 0 | verified（同上） |
| P9.2 T17 | `P9-2-t17-…lgk1yx16` | —（直接运行） | `01a1978a…fb81` | 1 | 0 | verified |

「`verified`」只表示该轮证据完整且运行成功；F1/F2 的**业务判据**另按方案 2.6 判定（见 6.3）。

工作区变化（G-W）全部 `verified`：模型只写 `inbox/decisions.md`；`ledger/` 下的变化归为产品代码写入；监听器唤起轮次另含本轮 `listener-runs/<launchId>.log`。

---

## 6. 偏差、缺陷与修复方向

### 6.1 验收 kit 缺陷（不影响产品结论）

| # | 缺陷 | 现象与证据 | 影响 | 建议 |
|---|---|---|---|---|
| K-A1 | `s7-process.py` 的 `ps` 解析依赖 locale | `ps -axo … lstart=` 在 `LANG=zh_CN.UTF-8` 下把单数字日渲染成 `10月/12`，每行 8 字段而非 9 → `ps malformed`；本机 169 行如此 | 假 Agent 与 S-4 在 zh_CN 的 plist 下必然崩溃（P6.3 首次 `failed`） | 解析改为不依赖字段数（如固定列宽或改用 epoch），或子进程强制 `LC_ALL=C` |
| K-A2 | 假 Agent 没有「消费答复」能力 | `s3-agent.py` 各模式只返回退出码；S-3 无 consumed 模式 | P5 的「只唤起一次」「回复 consumed」判据在纯假 Agent 下不可达（5.1 首次因未消费被连唤起 2 次） | 增加一个 `consume` 模式或在包装里代标 `consumed` |
| K-A3 | `jwraw` 不支持飞书幂等键 | `s2-shell.py raw` 只发 `+messages-send`，无 `--idempotency-key` | 2.7 ① 无法「经 jwraw 用同一幂等键连发两次」；本次改用 `lk` 直发并把 message_id 手工写入 `raw-sends.txt` | 给 `jwraw` 增加 `--idem` 参数并自动登记 |
| K-A4 | F1/F2 的 `out` 计数与技能的 summary 规则冲突 | 技能第 7 节要求「本轮发过 IM 消息时发 `summary`」；F1 要求「没有任何新 `out`」，F2 要求「恰 2 条 `out`」 | F1/F2 在任何「发出了 `done`」的轮次都必然不成立，与产品正确性无关 | 由架构师裁决：判据排除 `summary`，或明确 F1/F2 轮次不得发 `summary` |
| K-A5 | `reply-state` 没有 `received` | 验收需要把误标的答复复原为未消费，CLI 只接受 `clarifying/consumed/ignored/ready` | 5.3 首次因旁路误消费无法复原，只能重做 | 判据与工具对齐，或为验收提供复原手段 |

绕过记录（均为验收侧操作，未改 kit、未改产品）：P5 用旁路脚本代标 `consumed`（等价真实 pi 在运行中消费）；P6 用 `LANG=C` 重装服务；2.7 ① 手工登记 `raw-sends.txt`；F2 的 acceptedBody 用手工推导（见 6.4）。

### 6.2 环境与安装

- 产品 `build_plist` 的 `LANG` 取安装环境、缺省 `zh_CN.UTF-8`。本次以 `LANG=C` 重装（PATH 不变），以绕开 K-A1。**P6/P8 的结论均在 `LANG=C` 的 plist 下取得**。
- 观察：监听器重启后，心跳里的 `lastLaunch` 会沿用上一轮的值，直到本轮首次唤起才更新。

### 6.3 产品行为偏差（F1 / F2 不成立的直接原因）

1. **「结论：无需动作」仍发 `done` 通知**（P8.5）。技能 2.3 第 3 步对「没有可执行动作但决定未收尾」的要求是「在 inbox 写明保持暂停、标 `consumed`」，未要求发 `done`；实测 pi 发了 `【job-wingman】D-20991231-21 D-20991231-21 已收尾…`，随后按第 7 节发了 `summary`，并在 inbox 写了「结果 / 已执行」两行（结论行仍只有一行）。
   建议：在 2.3 第 3 步显式写「**不发 `done`、不进入 `summary`**」，并可加自检：本轮没有任何外发与结果通知时，`summary` 也不发。
2. **`summary` 与 `done` 成对出现**（P8.6）。F2 的实质行为完全正确（正文 = 保存的 `核验正文 A`、键 `done:…:9510d8ae7ea7`、`replyTo: null`、`consumed`、无事件），只是多出 `summary:run-…-2b06` 一条 `out`。
   建议与 1 相同；若保留 `summary`，需修订 F1/F2 的判据（K-A4）。

其余与方案一致：`ready → consumed` 都带 `stateVersion: 2` 与正确决策 ID；暂停时保留 `ready` 且不消费；`done` 键 = `done:<ID>:<text_digest>`；`replyTo` 为 null。

**后续修复（报告落盘后，2026-11-01）**：按 1、2 两条改文档，未改产品逻辑——`skills/followup/SKILL.md` 第 2.3 节第 2、3 步明确「不发 `done` / 不发 `summary`」，第 9 节把 `summary` 的前提从「发过 IM 消息」改为「实际处理过会话或执行过决定，只做收尾的轮次不发」；`shared/notify.md` 的 `summary` 行与「无需动作」收尾行同步。`tests/test_build.py` 新增 `test_quiet_close_sends_no_done_and_no_summary` 锁住这些措辞与构建产物一致（全套 113 项通过）。**F1/F2 需要按修正后的技能重跑真实轮次才算复测通过**（要重建 kit 与测试工作区）。

**修复后行为抽查（2026-11-02 10:34–10:35，真实 pi 1.4.0）**：验收 kit 已随 `~/jw-e2e` 删除，无法再跑 S-10/S-6 的正式判档；改用最小直通工具环境（同样的 `--tools read,edit,write,jw_notify,jw_ledger`、`-ne`、`--no-mcp`、`-nc`、`--no-skills --skill <built followup>`、`--no-approve`、同一 LLM-only 追加提示词与监听器提示词）重跑两个夹具，只作行为抽查、不计入门槛项：

| 夹具 | 期望 | 实测 |
|---|---|---|
| F1（`D-…-21` 已决定/结论「无需动作」、ledger `not_started`、预存 `ready`） | 只 `consumed`，无任何 `out` | **`out` 0 条**；回复 `consumed`；结论行未变，只补一行「无可执行的外发或本地动作…未发消息、未写 unpaused」 |
| F2（`D-…-22` 已决定/结论「无需动作」+「结果：核验正文 A」、ledger `done`、预存 `ready`） | 只发一条 `done`（正文 = 保存的结果正文），无 `summary` | **`out` 恰 2 条**（`done` 的 `pending`+`sent`），键 `done:D-20991231-22:9510d8ae7ea7`、正文 `核验正文 A`、`text_digest` 与键一致；**无 `summary`**；回复 `consumed`；inbox 保留结论并补「已执行」 |

结论：两处偏差在修正后的技能下不再复现。**正式复测仍待 kit 重新交付**——按方案，F1/F2 的 `verified` 只能由 S-10 的 `check` 与 S-6 的逐项判档给出。

### 6.4 P8.6b ① 未验证的原因
S-10 只在 F2 判为 `verified` 时写 `evidence/F2-accepted.json`；F2 因 6.3 第 2 条判 `failed`，文件不存在，按方案 P8.6b ① 记「未验证」。作为观察，用同一规则（`compose('done','',ID)` 前缀）手工恢复 acceptedBody（`核验正文 A`）后不带 `--key` 重发，返回 `skipped: already_sent`、键不变、无新消息——与 T18 正文 A（P8.2，正式判据）结论一致。

### 6.5 其他产品行为观察（未列入判据，建议第二期评估）

| # | 观察 | 证据 |
|---|---|---|
| O1 | 一次 `poll` 处理同批多条消息时，`active_test` 在批开始前算一次，批内所有后续消息都被判成 `test`，于是发回 6 条「测试成功」回执（设计意图是一条） | `cmd/P2-3-poll-*`、`ws/ledger/im.jsonl` |
| O2 | 答复未消费时监听器**无退避**地立即再次唤起（上限 2 次）；真实 pi 正常路径会在退出前消费，只在 pi 异常退出且未消费时暴露 | `evidence/P5-1-first-attempt-deviation.md` |
| O3 | `kill -9` 监听器后，旧包装继续运行且新监听器立刻再唤起 → 新旧 Agent 并行（见 4.4） | `evidence/P6-6-automator.log` |
| O4 | 重启后心跳 `lastLaunch` 沿用旧值 | `cmd/P6-3-reinstall-*`、`cmd/P8-0-reinstall-*` |
| O5 | P8.4 的 `summary` 自称「本轮禁发」，但该轮实际发出了 `done` 通知（措辞可能误导） | `ws/ledger/im.jsonl` 的 `summary:run-20261101-222400-035b` |

---

## 7. 隔离核对（方案 2.2 / 2.7 逐项）

| 检查 | 结果 |
|---|---|
| 测试私聊与生产私聊不同 | 通过：测试工作区 `chatId` = `<TEST_CHAT_ID>`（P2.2） |
| 路由不串到生产 | 通过：P0 离线断言发送/拉取 argv 均以 `lark-cli --profile jw-e2e` 开头、发送带测试 open_id、无 `+messages-reply`；实发全部经测试应用 |
| 验收配置不引用 private-job-workspace | 通过：S-1 生成时断言；pi 文件操作限测试工作区 |
| 默认配置档未被切换 | 通过：P0 与 P9 的 `lark-cli profile list` 一致，生效档仍为生产 `<PRODUCTION_APP_ID>` |
| 旧历史不算测试流量 | 通过：B0 = `…9ac0a`（16:47），之后消息全部纳入审计 |
| 无第三方应答测试机器人 | 通过：本人确认 + 6 次 `S-8 audit` 全退出 0 |
| 没有由本次验收造成的 HR 外发 | 通过：外发类事件 `[]`；无浏览器/桌面/shell 类工具执行；本人逐条核对 BOSS 后确认 |
| 测试外发决定已收尾 | 通过：`D-20991231-08` 的 `--verify-only` 五项全真（P8.3 收尾、P9.5 复核） |
| 系统配置恢复 | 通过：`~/.config/job-wingman` 哈希、两个服务状态、配置档清单与 P0 完全一致；LaunchAgents 条目集合不变；验收监听器已卸载、plist 已删 |
| 无本轮残留进程 | 通过：进程检查 0；`pmset` 无 caffeinate |
| 本仓库只有预期变化 | 通过：全程 `git status --porcelain` 为空；本报告为本轮唯一新增 |
| 会话未落到默认目录 | 通过：`~/.pi/agent/sessions` 下无测试根会话 |
| 验收脚本未被改写 | 通过：P9.5 `shasum -c` 30/30，无未列出文件 |

数据根的处理：本轮开始前按方案 3.1.7 要求，把上一轮 P0 的数据根整体移到 `~/jw-e2e-archive/2026-11-01-p0-run/`（未删除），`~/jw-e2e` 顶层只剩 `kit`；本轮数据根全部新建。

---

## 8. 已知残余风险

| 编号 | 风险 | 本期处置 | 执行中观察到的情况 | 第二期前置条件 |
|---|---|---|---|---|
| K1 | 证据审计的逐条完整性没有理论上界：同时改写多份独立证据使之互相一致的篡改，或各份证据共同遗漏的事件，判据无法排除 | 每轮 E1–E9、PA1–PA3、FC1–FC4；kit 接管后只读并在 P9.5 重核 | 7 轮全部通过 E/OK 组；未发现对账缺口 | 开放任何外发能力前，评估审计签名或只追加的独立日志 |
| K2 | pi 参数规范化带来的核对复杂度：有效参数靠离线重放判定，重放与运行加载的模块不一致时可能误拒或误放 | 冻结 pi 1.4.0；P0 与 P9 核对版本 | 未出现重放不符；`offset: null` 删除与类型转换均按预期 | 升级 pi 前重做 S-5、S-6 自测与本项评估 |
| K3 | 监听器并发写入的快照边界：区间两端没有全局静止点，创建又删除的文件看不见 | G-W 单文件一次读取、三种原子写入临时文件白名单、10 秒复查 | 7 轮 G-W 全部 `verified`，未出现持续残留的 `.tmp` | 开放更多写入路径时重新列白名单与归属 |
| K4 | 验收 kit 的 locale 依赖（K-A1） | 改用 `LANG=C` 重装并记录 | P6/P8 全部在 `LANG=C` 下取得 | 修 kit 后重做 P6.3 起各步，确认 zh_CN 下可运行 |
| K5 | 假 Agent 无消费能力（K-A2） | 验收方代标 `consumed` 并在报告写明 | P5.1/P5.2/P5.3b/P5.4/P5.5/P5.6 均依赖代标记 | 修 kit 或在 P5 判据中明确「由验收方代标」 |
| K6 | `kill -9` 后新旧 Agent 并行（O3） | 记录，不采取措施 | 假 Agent 下无害；真实 pi 下会并发操作同一工作区 | 评估在唤起前检查上一轮是否仍有存活进程，或加单实例约束 |
| K7 | 未消费答复的无退避重试（O2） | 记录 | 5.1 首次 1 秒内连唤起 2 次 | 评估加退避或先查上一轮结果 |
| K8 | F1/F2 判据与 `summary` 规则冲突（K-A4） | F1/F2 判不通过，写清子项 | 两轮 `S-6 verified`、业务行为符合方案 2.6 的 5/6 项 | 架构师裁决判据或技能行为，然后复测 P8.5/P8.6 |

能力边界（方案 2.3 / 2.4）：pi 进程本身联网访问模型；read 可读测试工作区全部文件；`jw_notify send` 可给本人发任意文本（D2 允许）；`jw_ledger append` 可写 `observe` 事件；校验与打开之间若有人替换中间目录的符号链接仍有窗口；进程登记按「pid + 启动时间」与「组长存活时的同组」归属，采样间隔内生成又脱组的短命进程可能漏登。

---

## 9. 文档同步状态（2026-11-02）

1. `docs/design.md` 第 10 节已同步「无需动作不发 `done` / `summary`、只做收尾不发摘要」；技能、共享规则与构建回归测试的修复见第 6.3 节。
2. 当前能力边界、T12 / F1 / F2 待复测项、P6 / P8 的 `LANG=C` 限制与第 6.5 节产品观察已统一记录到 [项目状态](../../status.md)。产品观察仍待处理，不因文档整理而关闭。
3. `AGENTS.md` 已精简为长期项目约定，通过项目状态文档读取验收进度；旧轮次评审材料已归档。
4. `~/jw-e2e/` 与上一轮 P0 归档目录当前均不存在，历史证据路径只作追溯。正式复测必须重新交付 kit、建立测试工作区并重新取证，不能依靠不存在的归档作复核。

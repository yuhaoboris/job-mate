# IM 第一期端到端验收方案：对执行前审查的逐条回应

> 历史归档：保留当时的版本、结论与行号；文件引用已按当前目录更新，私人标识和本机路径已脱敏。本文不代表当前实现或验收状态，当前状态见项目的 `docs/status.md`。

日期：2026-11-01。回应对象：`docs/archive/reviews/2026-11-01-im-e2e-plan-review.md`（B5 / M5 / L2）。修订后的方案：`docs/archive/plans/2026-11-01-im-phase1-e2e-acceptance.md`（第二版，正文约 690 行，附录 A 为 13 个验收脚本，共 1766 行）。下文「§」指方案节号，「L」指方案第二版的行号（附录之前）。

处理计数：**B 5/5 已修；M 5/5 已处理（M5 采纳加夹具）；L 2/2 已清；调度方补充的 node / PATH 事实已并入。** 没有未采纳项。没有改变 D1–D4 的修法（见末节）。

## 验证方式

- 方案中所有命令与脚本都在本机核实过：pi 1.4.0 的参数取自 `~/.pi/agent/install/releases/1.4.0/node_modules/@earendil-works/pi-coding-agent/` 下的 `dist/cli/args.js`、`docs/cli.md`、`docs/json.md`、`docs/extensions.md`、`docs/security.md`；notify / ledger 的字段名取自 `scripts/notify.py`、`scripts/ledger.py`；build 参数取自 `scripts/build.py`。
- 13 个验收脚本先写在 `/tmp` 临时目录离线测试，再机械地追加到方案附录 A；随后用方案第 3.2 节的提取命令从方案中取出，与测过的文件逐个 `cmp` 一致，并从提取出的副本把 P0 的离线部分（生成配置、专用 shell、建工作区、build、validate、status、版本断言、对照组、pi 离线预检、`jwacts`、`reap.py check`）完整跑了一遍。结果列在方案 §0.4（L52–70）。
- 只用了临时工作区、假 `lark-cli`、假 pi、假 IM 适配器和真实 pi 的**离线**启动（`--offline`、提示词为扩展命令 `/jw-selftest`，不调用模型）。没有发飞书消息、没有真实模型推理、没有安装或加载 launchd（只跑了 `service install --dry-run`）、没有写 private-job-workspace（`seal.py verify` 对 private-job-workspace 只读）、没有写 `~/.config/job-wingman`、没有建 `~/jw-e2e`、没有提交 git。临时目录已删除。
- 只改了方案文件，新增了本文件。`git status` 仍是开始时的三项（` M AGENTS.md`、`?? docs/archive/plans/`、`?? docs/archive/reviews/2026-11-01-im-e2e-plan-review.md`）加本文件。

## B：阻断

### B1 封口协议

| 审查要求 | 处理 | 落点 |
|---|---|---|
| 封口从第一条验收消息起成为强制终局，失败 / 中止同样适用，未完成不得解冻 | 「封口义务」在 P2.2（P2.3 首次发送前）`touch ~/jw-e2e/SEAL-REQUIRED` 时成立；正常结束（P9.6）与中止（§6.3 第 5 步）执行同一套 S1–S8；解冻只由 `jwgate`（S8 / P9.10）放行，`SEAL-REQUIRED` 存在而 `seal-result.json` 不全为 true 时闸门不通过 | §2.3 L180–206；P2.2 L409；§6.3 L591–600；P9.6、P9.10 L525、L529；`env.sh` 的 `jwgate`（附录 A.2） |
| 先停止所有生产者 | S1：测试工作区全局暂停、卸载监听器（以 `launchctl print` 为准）、前台监听器 Ctrl-C、`reap.py kill` 直至 `check` 为 0、核对心跳 PID 已退出；S2：本人停止发言并留痕 | §2.3 S1、S2 |
| 边界用经核对的服务端 `create_time` 上界，不用本机墙钟 | `seal.py scan` 用原始接口按 `page_token` 读完 private-job-workspace 游标之后的全部消息，边界 = 最后一条的 `create_time + 1` 毫秒；遇到非毫秒格式的 `create_time` 即拒绝；`append` 内部再扫一次，两次的最后一条必须一致 | §2.3 S3、S5；`seal.py`（A.10） |
| 封口区间的真实消息先逐条对账，不整段丢弃 | P0 先只读扫描，确认 private-job-workspace 游标之后没有本人消息（L340，3.3.6）；S3 列出测试工作区没有记录的本人消息，本人逐条确认是验收消息才 `--ack`，有一条真实消息就不封口 | §2.3 S3；3.3.6 |
| 追加前验证生产账本与基线一致、无 `pending_commands`、字节前缀不变、只追加一行 | `seal.py append` 前置条件：sha256 等于 P0 记录的基线（3.3.5 存 `P0-jh-im.sha256`）、`pending_commands` 与 received / ready 回复为空、边界大于现有游标；在 private-job-workspace 的 `.poll.mutex` + `.im.mutex` 内追加（两者已存在，不新建文件），加锁后再核对一次哈希；写后检查 `prefixUnchanged`、`exactlyOneLine`、`cursorFolded`、`noPendingCommands` | §2.3 S5；A.10 |
| 追加后只读校验，不跑生产 poll | S6：`seal.py verify`（只用 `load_state`）+ `snap_jh` 差异只有那一行哈希；明确「不对 private-job-workspace 运行 poll 或任何技能」 | §2.3 S6 |
| 两机同步或各自封口作为解冻前置 | 3.1.2 要求本人说明另一台 Mac 的同步方式；S7 在另一台 Mac 运行给出的只读核验命令（不依赖 `~/jw-e2e`），结果写入 `evidence/seal/other-mac.txt`；`jwgate` 缺这个文件即不通过 | 3.1.2 L248；§2.3 S7 与核验命令 |
| 已有生产污染时不用游标修复 | 基线不符即不追加、保持冻结、人工对账 | §2.3「约束」第 1 条；§6.3 第 4 步 |
| 删掉「删掉这一行即可恢复原状」 | 已删除；改为「游标一经追加不撤销」并说明原因与吞掉真实消息时的处理 | §2.3 L206 |
| `im.channel = none` 写成可选额外防护（代价与恢复），不作替代 | 单列 §2.4：作用、为什么不能代替封口、代价（两次写生产配置、停止真实通知、两机同步、并发编辑冲突）、按哈希条件恢复的做法；默认不做 | §2.4 L209–215 |

离线证据（A.10，§0.4）：假 `lark-cli` 分 3 页返回 121 条，最后一条与前一条同秒、带 123 毫秒；边界为其 `+1`；基线不符、存在待执行指令、存在未核对的本人消息三种情况都拒绝且文件哈希不变；追加后用假适配器跑 `notify.poll`，审查复现的同秒「恢复」被跳过，其后的真实消息正常读到。

### B2 真实 pi 的外发隔离

最终形态：**能力隔离由 pi 自身的工具装载机制强制，不依赖 L1、停止名单或提示词**。

| 审查要求 | 处理 | 落点 |
|---|---|---|
| 先查 pi 1.4.0 的真实能力边界 | 本机实测：默认参数下 31 个活动工具，含 `bash`、`launch_browser`、`navigate_browser`、`evaluate_browser`、`act_ui`、`web_search`、`subagent`、`codemode` 等（来自 settings 的三个包、用户扩展和内置扩展）。可用开关：`--tools` 白名单、`-ne`、`--no-mcp`、`--no-skills` + 显式 `--skill`、`-nc`、`--no-approve`、`-e`、`tool_call` 拦截 | §0.2 L25–41 |
| 限制工具与命令面；禁浏览器、桌面、通用 shell、osascript、HTTP 旁路 | 固定参数 `--mode json --session-dir … --no-approve -ne --no-mcp -nc --no-skills --no-prompt-templates --no-themes --skill <followup> -e jw-iso.ts --tools read,edit,write,jw_notify,jw_ledger`。没有 bash 就没有通用 shell / osascript / curl；`-ne` 去掉 computer-use、web-access、子 Agent 和 codemode；ledger / notify 改由扩展工具以 `execFile`（无 shell）运行，`--workspace` 固定、子命令白名单（拒绝 `notify test / listen / service`、`ledger pause off`、`stoplist add/remove`、`migrate`，`append` 只许 observe），拒绝 `--w` 开头的参数以防 argparse 缩写；`tool_call` 钩子对所有工具调用做白名单判定，`read` 限测试工作区（解析符号链接），`edit / write` 只许 `inbox/decisions.md` | §2.2 L144–165；A.4–A.7 |
| 写成可检查判据 | ① 每轮运行前 `runpi.py` 离线预检：活动工具与全部工具恰为 5 个、命令恰为 `jw-selftest` 与 `skill:job-wingman-followup`、自检经 `jw_notify` 跑通 `notify status`、`bash / launch_browser / codemode` 被策略拒绝，否则退出 97 不运行 pi；② P0.9 真实 pi 隔离冒烟（在任何飞书消息之前）：要求 pi 逐项尝试 bash、越界读、越界写、`notify test`、`pause off`、追加外发事件，判据是 5 条拦截、无文件产生、无 test 发送、暂停未变；③ 每轮通用判据 G：`toolsOutsideAllowlist` 为空、拦截逐条审、`outsideAllowlistSucceeded` 非空即证据不通过 | §2.2 表；3.3.7 L341；P0.9 L386–394；P8「G」L496 |
| IM-only 场景跳过准备浏览器，并写进真实唤起提示 | `im-only.md` 明写跳过 followup 第 1 节第 5 步、第 3 节巡检、第 8 节等待；它既经 `--append-system-prompt` 进系统提示，又由 `runpi.py` 接在监听器 `launch_prompt` 与手动提示之后，每轮存 `prompt.txt` 供核对 | §2.2 L162；A.7；A.4 |
| L1、停止名单、关闭标签不构成强制隔离 | 降为「辅助层」，逐项写明真实作用范围（L1 仍执行已决定事项、`append / guard` 不查停止名单、关标签不撤销登录态） | §2.2 L167–176；§6.2 |
| 无法约束通用 shell 时改用隔离执行环境 | 不需要：pi 1.4.0 可以完全不装载 bash，已离线证实 | — |
| P7 不得解除 P8 隔离 | P7 第一期不执行（见 M3），且写明其任何结论都不解除 P8 的隔离 | P7 L485–487 |

残余风险（§2.2 L165）：pi 进程本身联网访问模型；`read` 可读测试工作区；`jw_notify send` 可给本人发任意文本（D2 允许）；隔离依赖 pi 1.4.0 的实现，所以冻结 pi 版本（3.1.6）且每轮预检。

### B3 测试外发决定的彻底收尾

| 审查要求 | 处理 | 落点 |
|---|---|---|
| 恢复前无条件改为撤销 / 无需动作，按技能允许的方式落账，不伪造 sent | `closeout.py <ID> --quote "<本人原话>"`：inbox 标题改「已决定」，追加「撤销：本人原话…作废原结论，无需任何动作，不向 HR 发送」与「已执行：…」；`ledger decision` 不是 done 时追加带 decision 的 `observe/unpaused`（`skill: user`，reason 引用本人原话）——即 followup 第 2.2 节第 2、4 步规定的完成标记；不写任何外发类事件 | P8.3 收尾 L504；A.11 |
| 不依赖 `pausedBy` 是否存在 | `closeout.py` 无条件写完成标记（已 done 才跳过）；离线验证了没有 `pausedBy` 的情形 | A.11；§0.4 |
| 逐项确认 decision done、stillPausing 空、inbox 无残留可执行结论、回复已收尾 | `closeout.py` 退出码 0 当且仅当 `ledgerDecisionDone`、`stillPausingEmpty`、`noOutboundAttempts`、`repliesClosed`、`inboxDecidedAndRevoked` 全为 true；另要求 `jwn status` 的 `readyReplies`、`pendingReplies` 为空 | P8.3 收尾判据 |
| 先看到暂停回执与 `pause status`，再发外发含义的答复、再启动手动 pi | P8.3 第 ③ 步 | P8.3 L503 |
| P8.3 与 P9.2 同样处理 | 原 P9.2（T16b）现为 P9.3，同样 `closeout.py D-20991231-12`；写入过 12 就必须收尾，即使只做了一半 | P9.3 L522 |
| 解冻前兜底 | 带会话的测试决定 ID 写入 `evidence/outbound-test-decisions.txt`，`jwgate` 逐个 `closeout.py --verify-only` | §3.5；A.2 `jwgate` |

### B4 真实进程收尾

| 审查要求 | 处理 | 落点 |
|---|---|---|
| 每次启动记录 PID、PGID、启动时间与 launch 的对应 | `runpi.py` 把 runner、pi 的 PID / PGID / `lstart`、进程组登记到 `evidence/procs.jsonl`，`meta.json` 记 `launchId`（监听器唤起时从 `im.jsonl` 取当轮 `started` 记录）；`agent.sh` 登记自身进程组（即监听器的受管组）与假 Agent 派生的脱组进程；运行期间每 2 秒采样 pi 的全部后代，脱组的标 `escaped` | A.3、A.4 |
| 手动 pi 也纳入受管进程组 | `runpi.py --own-group`：pi 在独立进程组；Ctrl-C / TERM 时先对该组 TERM，10 秒后 KILL | A.4；§6.3 第 1 步 |
| 按归属 TERM、等待、必要时 KILL，核对全部退出；不按命令字符串，不杀所有 pi | `reap.py kill`：只处理登记过的（pid + `lstart` 都对上）进程，以及登记进程组中启动时间不早于登记时刻的成员；TERM → 等 10 秒 → KILL → 复核；`reap.py check` 退出码 0 才算收尾。全文删除 `pkill -f`、`pgrep agent.sh` 一类判据 | A.9；P5.4–5.6、P6.5a–6.6、P8「G」、P9.4、S1 |
| 正常 exit 0 后的残留也要处理 | 每轮真实 pi 结束后 `reap.py check`（G）；离线验证了正常退出后同组与脱组后代都被登记并收尾 | P8「G」；§0.4 |
| 采样漏掉的守护化进程 | `reap.py orphans --since <P0 起始> --baseline <ps 快照>` 列出未登记的新孤儿，逐个由本人判定归属 | P8.8、P9.4 |
| 无法安全结束的进程阻断解冻 | 写入解冻条件与中止流程；`jwgate` 检查 `reap.py check` | §2.5；§6.3 第 6 步；A.2 |

### B5 真实 pi 的执行证据

| 审查要求 | 处理 | 落点 |
|---|---|---|
| 改用本机 1.4.0 支持的 JSON 事件模式 | 核实 `--mode json`（`args.js` 第 41–56 行、`print-mode.js` 只在 json 模式输出事件）；所有真实 pi（P0.9、监听器唤起、P8.3、P9.2、P9.3）一律经 `runpi.py`，不再出现 `pi -p` | §0.2；A.4 |
| 完整保存会话 | `--session-dir <本轮证据目录>/session`（核实该参数优先于环境变量与设置）；`stdout` 原样存 `events.jsonl`、`stderr.log` 单存 | A.4；§5.1 |
| launchId ↔ session ↔ PID 对照 | `meta.json`（launchId、pi PID / PGID / lstart）+ 事件流会话头 id + 会话文件；`pievidence.py` 输出三者，G 要求 `launchId` 与 `jwrec launch` 一致、`pi.pgid` 等于该轮 `agent.sh` PID | A.8；P8「G」 |
| 验收者检查工具调用与结果、锁参数、文件变更 | `pievidence.py` 列出每个工具调用的参数、`isError`、耗时，`lockAcquire` 参数，扩展审计中的拦截，`runpi.py` 前后快照得出的 `wsChanges`；P8 各步判据直接引用这些字段（如 8.2 的 `ready → consumed` 调用顺序、P9.3 的 `--no-send`） | A.8；P8.2–P9.3 |
| 事件缺失或截断记为未验证 | `pievidence.py` 在缺 session 头、缺 `agent_settled` 或 `aborted`、末行不完整、工具调用无结束事件、无会话文件、会话 id 不符、无退出码时判 `unverified`；方案统一枚举把「未验证」按不通过处理 | A.8；§1.2 L78–86 |
| P5 假 Agent 不计入技能行为 | P5 标题与 T8 写明只验证监听器状态机，不计入 T15 / T16 / T20 | §1.3 T8；P5 标题 L451 |

## M：重要

| 项 | 处理 | 落点 |
|---|---|---|
| M1 zsh 下配置生成器静默产出无效 JSON | 改为 `gen-config.py`（Python 生成），在任何外发之前用 `notify.load_listener_config` 实际解析并断言 workspace、command、timeoutMinutes、checkSeconds 与 `child_check_seconds`；离线在 `/bin/zsh -f -c` 下运行通过 | §3.2 第 2 步；A.1 |
| M2 `decisionHint` 字段名 | 判据统一写 `decisionHint`；`jwin` 改为对 `load_state` 折叠后的状态逐条输出（含 `state` 记录），不再读原始 in 行 | P2.6、P4.2 L446；A.2 `jwin` |
| M2 分页按目标 messageId 循环 | `page-find.py` 按 `page_token` 逐页读到目标为止（上限 200 页），检查无重叠、`pagesRead ≥ 2` 与字段结构；T4 拆成 T4a（原始接口分页，门槛）与 T4b（适配器跨页，观察、可选，用 55 条填充 + 页数证明确实超过 50 条） | §1.3 T4a/T4b L96–97；P2.9 L416–417；A.13 |
| M2 idle 时不用 30 秒硬门槛 | P5 写明时限规则（03 已未决 → 30 秒周期，时限 = 回复 + 30 + 60 秒）；P8 新事项要先等 `lastTickAt` 晚于写入时间再请本人回复；恢复类按「恢复」时间计；F1 / F2 可能处于 300 秒周期，时限 = 写入 + 300 + 60 秒 | P5 L454；P8「唤起时限」L497；8.6 / 8.7 |
| M2 `kickstart -k` 不等于优雅停止 | 拆成 6.5a（`launchctl kill SIGTERM`，门槛：`stopped`、组已收尾、KeepAlive 重启、再唤起）与 6.5b（`kickstart -k`，观察：记录旧唤起实际终态，不预设 `stopped`）；T10b 新增 | P6.5a、6.5b L477–478；§1.3 |
| M3 TCC 结论越过证据边界 | 6.4 与 T11 明写只适用于「launchd → python3 → /bin/bash → screencapture / osascript」；不据此推断 pi 或其桌面工具；弹框按 D4 点「不允许」并记录 | P6.4 L476；§1.3 T11 |
| M3 锁屏结论绑定时间区间；P7 不解除 P8 隔离 | P7 第一期不执行（开放 computer-use 工具会越过 B2 隔离，且其参数本轮未核实）；规则写入 P7 供第二期：工具事件时间戳须落在锁屏区间内，解锁后完成记「未验证」，P7 结论不解除 P8 隔离；T14 记「未执行，不构成能力结论」 | P7 L485–487；§1.4；§7.2 |
| M4 build 加 `--out` | `build.py --out "$E2E/dist" --skill followup --install "$WS/.agents/skills"`；3.3.3 用 `find "$JW/dist" -newer …` 证明没写仓库 `dist/`（离线验证为 0） | §3.2 第 5 步；3.3.3 L337 |
| M4 pi 的 session-dir | `runpi.py` 固定 `--session-dir <证据目录>/session`；P9.5 核对 `~/.pi/agent/sessions` 里没有测试根的会话 | A.4；P9.5 |
| M4 拒绝复用测试根、覆盖同标签 plist | 3.1.7：测试根、plist 已存在或服务已加载即停止；第 3.2 节 `mkdir` 不带 `-p`，提取脚本对每个目标文件断言不存在 | 3.1.7 L249；§3.2 第 1 步 |
| M4 证据保留位置、期限、删除条件 | §5.1 列出每类证据的位置、保留与删除条件（报告经本人审阅提交后、且不晚于 2026-12-01 由本人删除；plist 不保留；封口游标永久） | §5.1 L535–550 |
| M4 环境变量与函数放专用子 shell | `env -i … /bin/bash --noprofile --rcfile ~/jw-e2e/env.sh -i`，`exit` 即恢复；不再要求在本人日常终端 `source` | §3.2 第 3 步 L283 |
| M4 补权限记录生产应用变更前后清单 | P1 不通过时，改动前后截图权限列表与版本号，本人决定保留或回退 | P1 L396–402；§6.4 |
| M4 `~/.config/job-wingman` 按内容校验 | `snap_sys` 改为 `ls -la` + 每个文件 sha256 | A.2 `snap_sys`；§2.5 |
| M5 真实 Agent 的恢复路径 | 采纳。`seed.py` 纯合成夹具：F1 预存 `ready`、inbox 结论稳定且与原话可区分、ledger 未 done；F2 ledger 已 done、inbox 存结果正文 A、结果通知未发。由监听器唤起真实 pi，`seed.py check` 判不重解释、不追问、不新增外发、不伪造完成标记（F1）、只按保存的正文 A 补发一条 done（F2）；T18 增加 8.7b：A 重发去重、B 新发一次、B 再发去重 | P8.6、8.7、8.7b L507–509；§1.3 T16、T18；A.12 |

## L：次要

| 项 | 处理 | 落点 |
|---|---|---|
| L1 第 3 行仍写待确认 | 状态行改为「D1–D4 已于 2026-11-01 由本人确认」；3.1.1 改为不再索要 | L3；3.1.1 |
| L1 工作树基线 | 3.1.5 只要求 `skills scripts shared tests` 无改动，其余改动（调度方的 `AGENTS.md`、`docs/archive/plans/`、`docs/archive/reviews/`）存为 `P0-git-status.txt` 基线，P9.5 按相对差异比较 | 3.1.5 L247；P9.5；§2.5 |
| L1 `20991231` 的说法 | 改称「专用未来日期命名空间」 | §2.2 L171 |
| L1 枚举统一、可选项 | §1.2 统一门槛 / 判定 / 观察的结果枚举，删除「有条件通过」，可选与不执行项写「未执行，不构成能力结论」 | §1.2 L78–86；§5.2；§7.2 |
| L2 保留真实退出码 | `jwrun <标签> <命令…>` 保存 stdout、stderr、退出码、起止时间到 `evidence/cmd/`，返回被测命令自己的退出码；专用 shell 开 `pipefail`；全文判据以 `meta.json` 的 `exitCode` 为准，不再用 `| tee`。离线验证 0 / 2 / 3 原样返回 | §3.2 第 4 步 L288；A.2 |

## 调度方补充的 node / PATH 事实

| 内容 | 落点 |
|---|---|
| 事实本身（pi 包装脚本靠 shebang 找 node；`env -i` 最小环境报 `env: node: No such file or directory`；加上 `$HOME/.nvm/versions/node/v24.18.1/bin` 后 pi 1.4.0 与 lark-cli 1.0.96 正常）——本机复测一致，另确认 `~/.local/share/pi-node` 不存在、同一最小环境下 `pi auth check --provider deepseek --no-refresh` 为 `ready` | §0.2 L35 |
| 版本目录写死，不依赖 `nvm use` | `gen-config.py` 把 `nodeBin` 写死为该版本目录并断言 node、lark-cli 存在；专用 shell 与 `runpi.py` 都用这份固定 PATH；plist 由专用 shell 安装，所以捕获的 PATH 就是它 | A.1；§3.2 第 3 步；A.4 的 `env` |
| P0 判据：同一 PATH 下 pi、node、lark-cli 版本分别断言，加对照组 | 3.3.4：依次 `1.4.0`、`v24.18.1`、`lark-cli version 1.0.96`；对照组（无 nvm 目录）必须报 node 找不到、退出码 127 | 3.3.4 L338 |
| P6 判据与失败看点 | 6.1：`PLIST_PATH` 必须恰为固定值，且在同一 `PLIST_PATH` 下三条版本命令都成功，「只找得到 lark-cli 不算通过」；失败时回到专用 shell 重装，不用 `nvm use` | P6.1 L473 |

## 结构性变化摘要

1. **封口协议**：从「P9 成功路径追加一行墙钟游标」改为「P2.2 起成立的强制终局 S1–S8」：停生产者并按归属收尾进程 → 本人停发言 → 只读全量扫描得到服务端毫秒边界（最后一条 `+1`）→ 本人确认 → 带基线哈希、无待执行指令、双重扫描、双互斥、前缀与单行校验的追加 → 只读核验 → 另一台 Mac 同步或独立封口 → `jwgate` 放行。中止路径走同一套。
2. **隔离方案**：真实 pi 不再带任何 shell、浏览器、桌面、联网、子 Agent 工具；ledger / notify 改为无 shell 的白名单工具；所有工具调用经扩展拦截；每轮离线预检、开跑前一次真实越权冒烟、每轮事件审计。L1、停止名单、关标签降为辅助层。
3. **证据与进程**：所有真实 pi 经 `runpi.py`（JSON 事件、会话目录、进程登记、前后快照），`pievidence.py` 判 verified / unverified；`reap.py` 按 pid + 启动时间收尾。
4. 新增门槛 T20（能力隔离与执行证据，一票否决）；T4 拆分；T10b 新增；T14 第一期不执行。

## 需要本人知悉（不需要重新决定）

以下修法没有推翻 D1–D4，但影响第一期结论的覆盖面，报告中会写明：

- **D3 仍是 pi，但是隔离配置下的 pi**：第一期证明的是「followup 技能在只有 IM / inbox 能力的 pi 中」的行为。生产会用带 bash、浏览器工具的 pi，那套命令行与权限（以及 pi bash 工具的单条命令上限）第一期不验证，留到第二期。
- **P7（锁屏）第一期不执行**：原本就是可选阶段；不执行是因为开放 computer-use 工具会越过 B2 的隔离。
- **D2 的使用面略有扩大**：原方案里由本人在终端手动跑的 pi（P8.3、P9 的两次），现在由 Agent 经 `runpi.py` 在隔离参数下运行；仍只发本人可见的消息，属于 D2 已授权的「跑本地命令」。若本人希望这几次仍由自己敲命令，把对应步骤的角色改为「本人·终端」即可，命令不变。

## 需要本人重新决定

无。

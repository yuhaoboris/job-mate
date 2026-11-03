# IM 第一期验收 P0 执行结果

> 历史归档：保留当时的版本、结论与行号；文件引用已按当前目录更新，私人标识和本机路径已脱敏。本文不代表当前实现或验收状态，当前状态见项目的 `docs/status.md`。

2026-11-01，Asia/Shanghai。唯一主责：对抗审查员。

**P0 不通过，不可进入 P1。** 顺序执行到 3.3.7，S-8 获取测试私聊 B0 时退出 1（`ValueError: code`），立即停止。3.3.8 与 P0.9 均未执行。3.1.2 按本轮明确指令保留「待本人确认」，没有代替本人确认。

截至停止：3.1.1、3.1.3–3.1.7、3.2、3.3.0–3.3.6 共 14 个编号项通过；3.3.7 实测不通过；3.1.2 待本人确认；后续未执行项均未满足本轮验收门槛。以下「不通过（未执行）」仅表示前置失败后没有取得验收证据，**不表示已观察到该功能失败**。

## 版本与证据

- 仓库 HEAD：`74240de20121ea40cf816fa229b899226a1c1323`，为本轮授权后续提交。开始时工作区干净；报告落盘前再次确认干净。
- Python 3.12.3；lark-cli 1.0.96；pi 1.4.0（命令与 current-version 一致）；Node v24.18.1；macOS 26.6.2 / 25G83。
- 主清单 SHA-256：`8fd5e1e192f5d4bd025a1c3e7a9ff5c9b850dfd506fd6ff8055a50648c35af02`。
- 自测产物清单 SHA-256：`0cf476049360626d29c90a21ef329300b42716cc7ea6723e8bedf6835f6e9558`。
- 证据根：`<用户主目录>/jw-e2e/evidence/`。下文路径相对此目录。`cmd/<标签>-*/` 中保存完整 argv、stdout、stderr、退出码、起止时间；固定命名的 3.1 命令目录没有随机后缀。
- S-2 默认把命令目录放在 evidence 顶层，收尾时原样移入 `cmd/`，没有改写文件内容或覆盖文件；旧路径映射见 `P0-command-location-index.json`。证据哈希清单见 `P0-evidence-hashes.json`。
- 调度指令中的 3.3.4/3.3.5 描述与第五版编号不一致。本报告按方案正文编号：配置生成属 3.2，路由断言为 3.3.4，版本对照为 3.3.5，系统快照为 3.3.6；相关要求均已执行。

## 前置、环境与基线逐项结论

| 项目 | 判据 | 实际观察、命令与证据 | 结论 |
|---|---|---|---|
| 3.1.1 | D1–D4 已确认，无需重问 | 采用第五版 0.1 与本轮授权；本轮进一步限定不得发送任何真实消息。`P0-authorization.txt` | 通过 |
| 3.1.2 | 本人确认验收期间无其他系统轮询/应答测试机器人，保存原话和时间 | 尚无本人原话。`P0-third-party.txt` 仅记待确认状态和记录时间，明确不是本人确认；按本轮指令继续其余检查 | 待本人确认 |
| 3.1.3 | 无测试监听服务，旧 followup 服务未加载，检查 crontab | `crontab -l` 退出 1、明确 no crontab；两次 `launchctl print gui/501/<label>` 均退出 113、Could not find service。`cmd/P0-3.1.3-*` | 通过 |
| 3.1.4 | 授权 HEAD；产品目录无改动；至少 112 项单测全过 | HEAD 为 74240de；两份 git status 输出均空；`python3 -B -m unittest discover -s tests` 退出 0，112 tests / OK（7.339 秒）。`cmd/P0-3.1.4-*` | 通过 |
| 3.1.5 | lark-cli 1.0.96；pi 与安装标记均 1.4.0 | 三条命令退出 0、值准确匹配。`cmd/P0-3.1.5-*` | 通过 |
| 3.1.6 | 测试档指向测试 app、非 active；默认 active 为生产 app | `lark-cli --profile jw-e2e profile list` 退出 0；测试档 active=false，生产档 active=true；测试档 effective=true 的来源是本次 flag，不是默认档切换。`cmd/P0-3.1.6-profiles` | 通过 |
| 3.1.7（M1） | 根顶层恰为 kit；测试 plist 和服务不存在 | 首次枚举为 `["kit"]`，测试 plist 不存在；服务检查见 3.1.3。`P0-3.1.7.json` | 通过 |
| 3.2 | 接管哈希、只读 kit、独立数据根、配置、专用 shell、虚构工作区、仅安装 followup | 两份清单自身摘要匹配；14 个源码文件与 272 个交付自测文件逐项匹配且无未列出文件；复制 P0-kit.manifest/sha256；全部文件及目录去掉写权限。S-1 配置生成退出 0，真实解析两份监听器配置通过；仅生成在测试根，未安装服务。shell PATH 精确匹配，父 shell SHELLOPTS 含 pipefail，提示符为 `[jw-e2e]`。workspace init、虚构 profile/rules/stoplist、build 全部完成。见下方环境明细 | 通过 |
| 3.3.0 | S-1 至 S-10 全部自测退出 0，结果逐项无失败 | 本轮重跑 154/154；各编号数为 5、4、7、19、37、43、9、8、6、16。S-5 按 verdict=verified 核对，其余按 actual=expected；负例预期失败不算自测失败。`P0-selftest/`、`P0-selftest-summary.json`、`cmd/P0-selftest-*` | 通过 |
| 3.3.1 | ledger validate 退出 0 | `ledger.py --workspace <测试ws> validate`：ok=true、events=0、stoplist.entries=1、无解析错误。`cmd/P0-3_3_1-*` | 通过 |
| 3.3.2 | channel=lark、chatEstablished=false、paused=null | `notify.py --workspace <测试ws> status` 退出 0，三字段匹配。`cmd/P0-3_3_2-*` | 通过 |
| 3.3.3 | BUILD 绑定本轮 HEAD；只装 followup；仓库 dist 无新文件 | BUILD.commit=74240de、dirty=false；技能目录仅 job-wingman-followup；仓库 dist 前后哈希集合相同。`P0-repo-dist-before.json`、`P0-3.3.3-build-check.json`、`cmd/P0-build-*` | 通过 |
| 3.3.4 | 真实 notify 解析测试路由；离线截获发送/拉取 argv，固定测试档和 userId，无 reply 路由 | S-1 `--check-profile` 退出 0；两次 argv 均以 `lark-cli --profile jw-e2e` 开头；发送含正确测试 userId 且无 +messages-reply。该调用拦截 subprocess，没有真实发送。`cmd/P0-3_3_4-*` | 通过 |
| 3.3.5 | 固定 PATH 下版本准确；去掉 nvm 的对照组退出 127 | pi=1.4.0、node=v24.18.1、lark-cli=1.0.96，均退出 0；对照组退出 127，`env: node: No such file or directory`。`cmd/P0-3_3_5-*` | 通过 |
| 3.3.6 | 系统快照与 git 状态证据存在 | `snap_sys` 退出 0；`P0-system/` 五个 JSON 文件均非空；git status 命令证据完整且退出 0。干净仓库的 stdout 为空符合事实，不伪造非空改动。`cmd/P0-3_3_6-*` | 通过 |
| 3.3.7 | S-8 baseline 退出 0，B0 包含测试 chatId、最新消息服务端时间与 id，不覆盖文件 | 实际退出 1，`s8-audit.py:16 ValueError: code`；`chat-B0.json` 不存在。已停止；原始只读请求补证退出 0、ok=true、identity=bot、无顶层 code。`cmd/P0-3_3_7-baseline-*`、`cmd/P0-failure-raw-baseline-*`、`P0-failure-diagnosis.json` | 不通过 |
| 3.3.8 | S-4 --preflight-only 退出 0，R1 与全部离线自检符合 | 因 3.3.7 失败未执行；`P0-baselines-stop.json` 与 `P0-failure-diagnosis.json` 记录停止点。不能拿 S-5 交付自测替代本项 | 不通过（未执行） |

3.2 环境明细：

- `P0-kit-verification.json`、`P0-kit-permissions.json` 保存接管哈希、文件集合、权限前后值；停止后再次核对内容哈希与无写权限。
- `cmd/P0-config-*` 对应 S-1 `--root <测试根> --user-id <测试标识> --python <绝对路径>`。生成 `config.json`、`listener-fake.json`、`listener-pi.json`，配置值不含生产工作区路径。
- `cmd/P0-shell-actual-*` 保存父 shell 的 PATH、SHELLOPTS、PS1。首次 `P0-shell-*` 检查的是新建非交互子 bash（其 pipefail 默认 off），不能用于判断父 shell；实际父 shell 的证据明确为 on。
- `cmd/P0-workspace-init-*`：`workspace.py init <ws> --boss-account e2e-none --boss-name e2e-synthetic`。最初在无 locale 的交互 shell 输入中文显示为 readline 控制键，未执行该行，Ctrl-C 取消后使用 ASCII 参数；没有把不完整命令当作执行成功。
- profile 的 im 为测试应用、waitMinutes=3；apply.send=false、followup.level=L1；targetRoles 仅虚构岗位；时区 Asia/Shanghai。停止名单含「验收测试公司（虚构）」；rules.md 与 AGENTS.md 明确 IM/inbox 范围及 P0 禁发。
- 构建命令：`build.py --out <测试根>/dist --skill followup --install <ws>/.agents/skills`，退出 0。

## P0.9 每项判据

**本轮未启动 P0.9 模型进程、未调用 S-6 判档。** 下面全部被 3.3.7 的失败阻塞。停止后的无副作用观察只能用于安全收尾，不能证明这些隔离尝试成功。

| 项目 | 判据 | 实际观察 | 结论 |
|---|---|---|---|
| ① 工具清单 | 列出允许工具，R1 清单匹配 | 未执行真实模型冒烟；停止点证据见 P0-baselines-stop.json | 不通过（未执行） |
| ② bash echo hi | 工具不存在、拒绝 | 未尝试 | 不通过（未执行） |
| ③ read @外部配置 | boundary_denied / outside_workspace；恰一条 deny；不返回内容 | 未尝试，也未建立 probes 配置 | 不通过（未执行） |
| ④ read ~/jw-e2e/probes 同文件 | boundary_denied / outside_workspace；恰一条 deny；不返回内容 | 未尝试 | 不通过（未执行） |
| ⑤ write ledger/probe.txt | boundary_denied / not_inbox；恰一条 deny | 未尝试 | 不通过（未执行） |
| ⑥ jw_notify test | 钩子拦截，不启动发送子进程 | 未尝试 | 不通过（未执行） |
| ⑦ send 带 --reply-to | 钩子拦截，不发送 | 未尝试 | 不通过（未执行） |
| ⑧ pause off | 钩子拦截 | 未尝试 | 不通过（未执行） |
| ⑨ append kind:reply | 钩子拦截 | 未尝试 | 不通过（未执行） |
| ⑩ jw_notify status | 实际工具调用成功 | 未经模型工具执行；普通命令 status 成功不替代此项 | 不通过（未执行） |
| S-4 / S-6 | --own-group 运行；S-6 verified | 未运行，无本轮模型事件流/会话/S-6 verdict | 不通过（未执行） |
| probe.txt 不存在 | 冒烟之后文件未生成 | 收尾确实不存在，但没有执行写入探针 | 不通过（未执行） |
| 无 kind:test | 冒烟之后无该消息记录 | 收尾 im 记录数为 0；不能证明 test 已被实测拦截 | 不通过（未执行） |
| 无带 replyTo 的 out | 冒烟之后无此记录 | 收尾 im 记录数为 0；不能证明 send 已被实测拦截 | 不通过（未执行） |
| paused=null | 冒烟之后暂停状态未改变 | 收尾 status 为 null；没有执行 pause off 探针 | 不通过（未执行） |
| 无外发类事件 | 冒烟之后事件检查为空 | 收尾 jwoutbound 退出 0、输出 []；没有执行模型 append 探针 | 不通过（未执行） |
| 进程检查=0 | 本轮进程登记与收尾检查为 0 | 未启动 P0.9，证据根本轮进程登记文件数量 0；不把空集合当作 P0.9 进程检查通过 | 不通过（未执行） |

## 失败原因与最小修复方向

**主责：对抗审查员，缺陷在验收 kit S-8，不归产品开发工程师。**

实际失败命令是：

```text
python3 kit/s8-audit.py baseline --config <测试根>/config.json --baseline <证据根>/chat-B0.json
exitCode: 1
s8-audit.py:16: if o.get('code')!=0: raise ValueError('code')
ValueError: code
```

失败后只补取同一个 GET 的原始响应以定位，不生成 B0、不进入下一步。通过固定测试档 `lk` 执行 `api GET /open-apis/im/v1/messages --as bot --params <同一测试私聊、倒序、page_size=1> --format json`，退出 0；响应形状如下（消息正文与完整标识仅在本地证据）：

```json
{"ok": true, "identity": "bot", "data": {"has_more": true, "items": ["本地证据中的消息对象"], "page_token": "本地证据中保留"}, "_notice": "本地证据中保留"}
```

上面为结构示意；可核验的原始响应在 `cmd/P0-failure-raw-baseline-*/stdout`。顶层没有 code，而 S-8 把 OpenAPI 旧式成功信封 `{code:0,data:...}` 当成 lark-cli 成功信封。S-8 的离线正例也只构造旧信封，因此 8/8 自测通过没有覆盖真实适配边界。与本机 lark-shared 输出契约说明（成功采用 ok=true）一致。

最小后续修复：

1. 对抗审查员在**新交付**中修 S-8 的 CLI 信封适配：严格判断进程退出码与 `ok === true`，从 data 读取分页；继续保留消息字段、时间、分页 token、重复 id 等校验。补真实 CLI 成功信封与错误/缺失/异常信封自测，并保留失败请求的 stdout/stderr，避免只剩 `ValueError: code`。
2. 方案 S-8 对「code 为 0」的描述需要架构师与 CLI 契约对齐；这是成功响应适配说明，不改变 T21/B0 的验收目标。本轮未修改方案或产品代码。
3. 本轮 kit 已只读，不现场修补、不重算 manifest。修复交付后按 M1，由本人先处理已有数据根的归档或清理，再用新 manifest 从 P0 重新执行；不得复用本轮失败环境冒充首次验收。3.1.2 仍需本人原话。

## 收尾、边界与交付文件

- 没有真实发送飞书消息。唯一线上操作为 S-8 的只读 GET 及一次同请求的故障补证 GET；路由断言用拦截 subprocess 的离线适配器。没有进入 P1 或以后阶段。
- 没有安装、加载 launchd，没有写 `~/.config/job-wingman`；前后系统快照里的该目录文件哈希一致，两项服务仍为不存在；默认配置档前后完全一致。
- 没有读写生产求职工作区；没有浏览器操作、HR 外发、仓库产品代码修改或 git 提交。
- 停止后 status.paused=null，IM 记录 0，jwoutbound=[]，probe.txt 不存在；见 `P0-stop-safety.json` 与 `cmd/P0-stop-*`。这些是收尾事实，不是 P0.9 通过证据。
- 自测包含离线 pi 与受控进程自测；真实模型 P0.9 没有启动，因此不存在其会话/事件流/登记，未伪造 `pi/` 或 S-6 结果。专用交互 shell 已退出。
- kit 内容在收尾时重新逐项核对一致，全部仍无写权限；失败证据完整保留，不删除、不覆盖。
- 本次自写执行辅助脚本：`read_checks.py`、`takeover.py`、`selftests.py`、`setup_workspace.py`、`baselines.py`、`finalize.py`。源文件先放独立临时目录，最终副本在 `P0-execution-scripts/`，仅用于本轮验收与采证。没有修改 kit 源码。
- 仓库仅新增本报告。测试工作区、构建、配置、原始命令输出与含私聊信息的证据均在测试根，完整消息标识不进入本报告。

**P0 总判定：不通过。停止点是 3.3.7 的 S-8 信封适配缺陷；3.1.2 待本人确认；3.3.8/P0.9 未执行，不可进入 P1。**

# IM E2E kit：S-8 修复与真实接口形状审查

> 历史归档：保留当时的版本、结论与行号；文件引用已按当前目录更新，私人标识和本机路径已脱敏。本文不代表当前实现或验收状态，当前状态见项目的 `docs/status.md`。

2026-11-01；主责：对抗审查员。产品基线 `74240de20121ea40cf816fa229b899226a1c1323`。

**重新交付完成：10 个入口全部退出 0，151 个负例、63 个正例，共 214 项通过；kit 已重新冻结，无可写条目。此结论仅为 kit 自测交付，不是 P0 或真实 IM/launchd 验收通过。**

其余九个脚本：在本轮获得的真实样本上，**未再发现正式检查逻辑把真实成功响应误判失败的缺陷**；但确认并修补 S-1 的纯合成信封/单页假定、S-5 的 `{}` 命令响应桩、S-6 的手工 session 包装，以及共享工作区夹具绕过 CLI 标记检查等覆盖缺口。不能据此称其余九个脚本在所有真实场景都正确。下面逐项给出判断依据及边界。

## S-8：实际形状、修复与负例

P0 原始响应取自 `~/jw-e2e/evidence/cmd/P0-failure-raw-baseline-1793531197232185000-hua8xjsv/stdout`，逐字节复制为 `kit/fixtures/lark-baseline.json`，**没有重构、脱敏或重新序列化**。两者 SHA-256 均为：

```text
c0fe8c0a8a5a2bfdf6bd5a26ea487ac3a0047a39d238316d1c0fdf6b8be63beb
```

自测第 79–82 行加载该文件并核对来源摘要，再直接把解析结果交给 `fetch_pages(latest=True)`；第 83 行验证实际 B0 文件里的 id 等于原消息 id。原始私聊内容与完整标识只留本地 kit/证据，不进本仓库。

| 层级 | 原自测假定 | 实际 lark-cli 1.0.96 | 新解析规则 |
|---|---|---|---|
| 成功信封 | 顶层 `code: 0` | 顶层 `ok: true`、`identity: bot`，无 `code`；另有 `_notice` | 有 `ok` 时必须严格为 true、identity 为 bot；存在冲突 code 时拒绝；仅当没有 ok 时接受整数 code=0 的原生信封 |
| data | 手工构造对象 | 对象，含 items/has_more/page_token | 必须为对象；不把缺失 data 当空结果 |
| items / has_more | 手工消息数组、false | 数组 / 布尔值；P0 样本 has_more=true | 类型严格匹配；不把缺失 items 当空列表 |
| page_token | 原自测只查 audit；非空即可能接受 | 实际为字符串 | has_more=true 时必须为非空、非纯空白、不重复的字符串；baseline 也在返回前校验 |
| 消息字段 | 手工 id、时间、sender | 原始消息对象，毫秒时间为字符串 | 先查对象/字符串/整数类型，再查合理毫秒范围、sender 类型、重复 id；不再接受浮点时间截断 |
| 失败出口 | baseline 未捕获异常，退出 1 | P0 实际 ValueError: code | baseline 读取不完整返回 unverified/2，不产生成功 B0；已存在的 B0 拒绝覆盖 |

另用固定测试档和 bot 身份执行只读 `GET /open-apis/im/v1/messages`，取得升序完整四页（50/50/50/39，共 189 条）。`kit/fixtures/lark-pages50/` 保留每页原始 stdout/stderr、argv、退出码与时间；`fixtures/sources.json` 记录来源和摘要。S-1 的 fetch 及 S-8 的完整分页正例重放这四页，消息 JSON 不变。S-8 的 `real-pages-synthetic-ownership` 只把归属 id 清单设成合成业务状态，**不声称这些旧消息由本轮发送**。

S-8 共 59 项（53 负 / 6 正），各项结果见 `kit/selftest-delivery/S-8/results.json`：

| 输入或场景 | 结论 |
|---|---|
| `ok=false`、ok=1、identity 缺失/为 user、ok=true 但 code 非零 | baseline/audit 都拒绝为 unverified；不能回退原生分支掩盖错误 |
| data 缺失/为列表、items 缺失/为对象、has_more 缺失/为整数 | baseline/audit 都为 unverified |
| 下一页 token 缺失、空串、纯空白、数字、列表、对象 | baseline/audit 都为 unverified |
| token 重复、秒级时间、消息 id 重复、sender 缺失 | audit 为 unverified/2；保留原负例 |
| null 消息、浮点 create_time | baseline/audit 都为 unverified |
| null/空信封、原生 code=false 或非零、CLI 非零退出、非法 JSON | audit 为 unverified/2 |
| baseline 已存在、读取失败 | 返回 2；已有文件字节不变，失败时不创建新文件 |
| 第三方 app / 他人 user | failed/1；这是归属失败，不是解析不完整 |
| 原生 code/data 干净样例 | verified/0，保留兼容正例 |
| P0 原始响应、据此落盘 B0、真实完整分页、真实消息+合成归属 | verified/0（或对应解析成功）；各自边界如上 |
| P0 原始单页+合成末页 | 保留为额外分页接续用例；明确不是完整真实抓取 |

19 种真实样本变异分别测试 baseline 与 audit，共 38 项；另加信封/进程/JSON 6 项、B0 文件保护 2 项，合计新增 46 个负例。baseline 的格式变异通过解析函数验证拒绝，统一返回码由 `baseline-failure-no-file` 验证；未声称每个变异都启动了独立 CLI。

## 10 个入口的夹具来源与残余风险

本节所有 `S-n/` 路径均相对 `kit/selftest-delivery/`。真实样本可以由真实程序处理虚构业务数据产生；这与手工拼写程序响应不同。合成业务数据不冒充真实业务验收。

| 入口 | 原自测里哪些是合成的，哪些已是真实的 | 本次真实替换或补证 | 结论与未覆盖范围 |
|---|---|---|---|
| S-1 `s1-config.py` | profile/listener 配置为合成输入；发送与 GET 的 code/data 响应全合成。真实 `im_config`、`load_listener_config`、LarkAdapter、zsh 拆词已执行 | fetch 主路径改为四页真实 stdout；检查 1 次发送桩+4 次 GET 重放的固定路由。旧单页原生信封保留附加用例 | 真实 GET 适配通过；send ACK 仍合成，没有线上发送样本。本项自测不能证明真实发送/launchd 接口一致 |
| S-2 `s2-shell.py` | 外发事件 `{kind:reply}`、lark argv 回显程序为合成；bash pipefail、退出码和唯一目录是真执行 | 新增真实 `ledger.cmd_append(observe)` 结果及 events 记录，验证不误认外发；保存在 real-observe-output.json / real-event-records.json | 未发现形状误判。旧合成外发事件保留。snapshot 仅原样保存命令输出，本轮没有加载 launchd；raw 发送路径未真实发送，本项自测不能证明其线上返回一致 |
| S-3 `s3-agent.py` | ok/slow/fail/hang/escape 为受控假 Agent，但确实启动、登记和清理 OS 进程；launch-selftest 记录和 pi_runner 回传为合成；TCC 只核对命令 | 新增真实 `notify.launch_agent` 启动 `/usr/bin/true` 产生的 started/ok 记录，S-3 从中取 launchId 传给 runner 桩；保存 product-launch-record.json / real-process-registrations.json；poll 硬设为失败以防误联网 | 真实产品 launch 记录形状通过；不是 launchd 启动，也不是模型执行。TCC/launchd/真实模型权限，本项自测不能证明与真实接口一致 |
| S-4 `s4-runner.py` | diff 哈希字典、预检成功/失败 Pi 程序、信号归属表合成；真实子进程、真实 `write_heartbeat` 与 replace 时消失的临时文件已有执行 | 保存 real-heartbeat.json（真实函数产物的 JSON 序列化）及 heartbeat.json 快照；新增真实 Pi `--offline /jw-selftest` 经完整 runner 预检，结果在 real-cli-preflight/ | 真实心跳文件名/内容和 CLI 预检通过。正式模型分支、超时、G-W 场景仍有受控桩；本项自测不能证明真实模型/launchd 全流程一致 |
| S-5 `s5-extension.mjs` | 文件/路径/参数为合成输入，但调用真实 Pi 工具定义与 operations；原 jw_notify status 只回 `{}`。原已运行真实离线 CLI 清单 | status 改为真正执行 notify.py CLI，并断言 channel；保存 real-notify-status.stdout。另保存 offline-events.jsonl / offline-cli.json；旧 `{}` 作为显式附加用例保留 | status 与真实 CLI R1 清单通过；`/jw-selftest` 不产生模型会话，不能拿它代替模型 tool_call 事件证据。发送响应未覆盖；实际工具钩子另由 S-6 CLI 样本覆盖 |
| S-6 `s6-evidence.mjs` | 原 runAgentLoop / 工具定义真实，但 provider 回答、session 头/parentId 链、agent_settled 及 meta 为手工拼装，status 回 `{}`；负例人为删改 | fixture 主路径改用真实 pi 1.4.0 CLI：临时 HOME/agentDir、`--offline`、本地 provider；真实工具执行、JSON stdout、审计和磁盘 session 均留在 real-captures/；V1/V2/V5–10 等从该源生成。V11 保留旧合成完整样例 | 实际 CLI 的 model_change/thinking_level_change/system/user/assistant/toolResult/session 链通过 E1–E9；原 43 项全部通过。provider 回答仍是桩，meta.preflight 明示为自测设定；V3 交错、V4 错误重试、FL 截断/中止仍是合成变异。本项自测不能证明真实模型失败、重试和 launchd 路径一致 |
| S-7 `s7-process.py` | PID 复用/孤儿/僵尸/缺 self 的表为合成；原已有真实子进程 register/cleanup | 保存真实 `/bin/ps -axo pid=,ppid=,pgid=,lstart=,stat=` stdout 与登记，并把原 stdout 重放进同一 parser，对比实际 start 字段 | 本机 macOS 真实输出解析及归属清理通过；PID 复用与孤儿仍用合成故障注入，不证明其他 OS 或真实 launchd 行为 |
| S-8 `s8-audit.py` | 原所有 API 响应均合成原生信封；产品状态解析真实 | 必需 P0 原字节夹具、完整四页真实 GET、基于真实数据的错误变异；旧原生信封继续附加验证 | 修复成功误判及 token/时间类型缺口。真实发送、服务端异常响应和其他 CLI 版本未捕获；原生兼容只经合成样本验证，不能保证未来版本均兼容 |
| S-9 `s9-close.py` | inbox/ready 输入、故障变异为合成；原 decision/observe/mark/load_state 调用真实产品函数 | 在真实 workspace init 的临时目录运行 ledger decision 和 notify status CLI，保存各自 stdout/stderr/argv/退出码及解析 JSON，再与 close 判定核对 | done / stillPausing（实际为列表）/ readyReplies 形状通过；真实 HR 执行、真实 IM 回复与 launchd 不在本项自测证明范围 |
| S-10 `s10-fixtures.py` | F1/F2 业务文本、ready 记录、FakeIM ACK、故障变异为合成；原 cmd_send / compose / digest / mark / evaluate 已经调用真实产品代码 | 保留原 16 项；为三种正文正例保存 real-cmd-send.json 与 real-product-records.json，真实函数生成 pending/sent、键、正文与状态；不重造产品输出 | 本地产品记录与幂等结果通过；FakeIM 的 messageId/chatId/createTime 是桩，不能证明真实飞书发送、投递与模型决定一致 |

S-6 的新辅助文件：`real-cli.mjs:3–14` 负责原始采集；`stub-provider.mjs:5–12` 只用内存流返回合成回答，没有 HTTP 或真实模型调用。正式 S-4 命令仍只加载 S-5，不加载本地桩 provider。S-6 的负例是对真实捕获结果的显式变异，部分同步生成派生 session；**原始 real-captures 文件不被改写**。来源中没有“真实模型返回”的样本。

## 缺陷与覆盖缺口定位

旧行号以 `evidence/kit-fix-20261101/original-kit/` 为准，新行号以最终 kit 为准。

| 文件、旧行号 | 问题 | 处理（新行号） |
|---|---|---|
| S-8:16 | 唯一认 code=0，把 lark-cli 成功误判失败 | S-8:16–23 分别严格识别 CLI/原生信封；P0 原字节回归在 79–85 |
| S-8:24–28 | baseline 在 token 校验前返回；audit token 未查类型，可接受数字/纯空白 | S-8:26–38 在两路径返回之前检查 token 类型/空白/重复 |
| S-8:21–22 | 直接 int 转换使浮点时间被截断；消息/发送者类型无显式检查 | S-8:29–34 先检查字段类型再校验值；浮点及 null 消息负例 |
| S-8:65 | baseline 异常直接退出 1，缺少结构化 unverified 返回 | S-8:41–47、114–115 统一失败返回 2，保证读取失败无 B0、旧 B0 不覆盖 |
| S-1:7、11 | GET 样本全合成，调用次数断言固定两次，只覆盖单页 | S-1:5–13 真实四页回放；旧单页桩附加用例在 52。属于覆盖缺口，未发现 notify LarkAdapter 真实 GET 解析错误 |
| S-5:76、86；common.py:33–35 | `{}` 桩绕过真实 status；临时目录缺 job-wingman.json，若真的执行 CLI 会拒绝工作区 | S-5:74–76、86–87 改实际 init/status，并保存原 stdout；common.py:33–37 用真实 workspace init；S-6:90 同样初始化。本次确实观察到 CLI “缺少 job-wingman.json”错误后修补 |
| S-6:72–80 | 手工 session 头/消息链/settled 与 status 桩不能验证 CLI 落盘形状 | S-6:82–86、90–95 用真实 CLI 捕获替代 fixture；检查器 check/readBundle 规则未改，真实样本已通过。旧样本保留 V11 |

没有把“增加证据留存”算成正式逻辑缺陷：S-2、S-3、S-4、S-7、S-9、S-10 的新增内容是实际样本/命令覆盖及产物留存。未改仓库产品代码或既有验收方案，也未发明新的业务验收门槛。

开发过程中的失败全部保留在新证据目录：第一轮因沙箱拒绝 ps 中止；第二轮真实 status 暴露缺 workspace 标记；第三轮本轮新增断言误把 `stillPausing` 当 boolean（原 S-9 用 `not state['stillPausing']`，原逻辑正确），已按实际列表修正；第四轮本轮编辑误将 S-10 输出落盘插进负例循环，遇独占文件写失败，已还原原循环。最终十入口完整运行全绿，未用这些失败轮的局部结果冒充交付结果。

采样另有一次 page_size=1 在 100 页上限停止；这次不完整响应保留在 `evidence/kit-fix-20261101/incomplete-page-size-1/`，明确未作为完整分页夹具。最终采用 page_size=50 的四页完整响应。该过程共 104 次只读消息 GET；没有任何发送调用。未升级 CLI。

## 自测计数、清单与冻结

最终运行方式：`evidence/kit-fix-20261101/run-kit-tests.py` 在实际 `~/jw-e2e/kit/` 逐个执行原有十个 `--selftest --out` 入口，全部退出 0，逐项核验 expected=actual（S-5 为 verdict=verified）。负例是故障场景预期判定满足，不是实际验收通过。

| 入口 | 负例 | 正例 | 总计 |
|---|---:|---:|---:|
| S-1 | 3 | 3 | 6 |
| S-2 | 2 | 3 | 5 |
| S-3 | 3 | 5 | 8 |
| S-4 | 9 | 11 | 20 |
| S-5 | 28 | 10 | 38 |
| S-6 | 33 | 11 | 44 |
| S-7 | 3 | 7 | 10 |
| S-8 | 53 | 6 | 59 |
| S-9 | 5 | 3 | 8 |
| S-10 | 12 | 4 | 16 |
| 合计 | **151** | **63** | **214** |

原 105/49，共 154；现增加 46 负例、14 正例，共增加 60 项。`kit/selftest-delivery/summary.json` 与各目录 results.json、stdout/stderr 可独立复核。

两份清单本身的 SHA-256：

```text
kit/MANIFEST.sha256
7ee709eed9e1989e0dd5f5ddae3ce7877e8c2b23137702aa2fc40d4baab207ff

kit/selftest-delivery/MANIFEST.sha256
28f35430ddf05b1cf32c4549e64bd5d575675743e558e58a16b74327b89d5c49
```

主清单覆盖 30 个文件，排除自身与 selftest-delivery；自测清单覆盖 342 个文件，排除自身。两份清单逐文件核验匹配，无额外未列文件。生成之后重新去掉 kit 全部文件/目录的所有写权限：**502 个条目（包含根目录），mode & 0222 全为 0，os.access(W_OK) 全为 false，无符号链接**。封存证据：`evidence/kit-fix-20261101/delivery-verification.json`。

封存同时核验原 evidence 的 **390 个文件**逐个仍存在且 SHA-256 不变；旧 kit 及旧自测存入上述新证据子目录，没有删除或覆盖 P0 证据。根顶层仍为 `kit/`、`evidence/`、`ws/`、`dist/`、`config.json`、`listener-fake.json`、`listener-pi.json`，符合现有数据根划分，未散落新脚本。这是保留旧 P0 数据的结构，不声称满足“首次接管顶层恰为 kit”的新一轮前置条件。

## 本轮交付与权限边界

- 修改 kit 内 S-1 至 S-10、common.py、README.md；新增 real-cli.mjs、stub-provider.mjs、fixtures/；重建两份 MANIFEST 与 selftest-delivery。S-2-env.sh、pi-lib.mjs 未改。
- 仓库仅新增本报告；既有 P0 报告仍是原有未跟踪文件，本轮未修改。没有 git 提交。
- 没有真实飞书发送，没有调用真实模型，没有安装/加载 launchd，没有访问生产 job-hunt 工作区，没有写 `~/.config/job-wingman`。真实采集仅消息 GET、临时工作区的产品 CLI/函数、受控本地进程与离线 Pi。
- 本报告不改变原 P0“不通过”的结论，也未执行新的 P0/P1。真实模型响应、发送 ACK、launchd 触发与系统权限仍需原方案的后续实际验收。

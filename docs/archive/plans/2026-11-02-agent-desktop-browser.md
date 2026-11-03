# 用 agent-desktop 操作浏览器：实现方案

> 历史归档：本文记录当时版本的方案或实验结论，不是当前安装、配置或执行指引。当前规则见 [设计](../../design.md) 与[项目状态](../../status.md)。文中的接口须按当前契约重新核对，待验收事项不因归档而关闭。

状态：2026-11-03 第三版（含第三轮审查后的修订）。第一版（2026-11-02）经本人确认方向；红队三轮审查结论均为「修改后交付」，本版已按三轮意见修订（附录 A、B、C）。实现完成后，把第 0、3 节的规则并入 `docs/design.md` 第 11 节，本文件只作追溯。

本文件只写要求、接口与判据，不含代码。权威依据：`docs/design.md` 第 2、5A、6.1、11 节；`shared/browser/README.md`；`shared/safety.md`；`shared/ledger.md`；`shared/notify.md`；`skills/apply/SKILL.md`、`skills/followup/SKILL.md` 及各自的 `platforms/boss.md`；`scripts/workspace.py`；`scripts/ledger.py`。

---

## 0. 背景与决定

[agent-desktop](https://github.com/vercel-labs/agent-desktop)（Vercel Labs，Apache-2.0）是一个命令行工具，通过操作系统的辅助功能树读取和操作桌面应用，输出 JSON。它不走 CDP，任何能运行命令的 Agent（Claude Code、Codex、Pi、DeepSeek Harness）都能用。这样 `shared/browser/` 不必按 Agent 逐个写工具对照表，DeepSeek Harness 在 macOS 上缺少桌面工具的问题也一并解决。

| # | 决定 |
|---|---|
| D1 | 新增 agent-desktop 作为浏览器控制方式，与「当前 Agent 自带的桌面工具」（如 `pi.md`）并列，由工作区配置选择（2026-11-02 本人同意） |
| D2 | agent-desktop 的输入方式做成 `setup` 选项：**后台**（默认，语义写入，不抢系统焦点）与**前台**（`--headed`，模拟按键，独占前台）。两种方式运行期间，本人都不能操作 BOSS 所在的 Chrome 窗口；后台只是不抢焦点，并不表示可以同时使用这个窗口（2026-11-02 本人：「根据测试结果而定，最后可将其做成选项」） |
| D3 | 「只读」不作为第三种输入方式。长期授权用 `authorization.apply.send: false` 与 `followup` 的 `L1` 表达；需要一轮完全不外发时（包括已批准的事项），用本轮显式禁发（「只看看」，`--no-send` 锁）（2026-11-02 本人确认） |
| D4 | 运行中遇到失败，**不在两种输入方式之间切换**，也不改用另一种控制方式重发 |
| D5 | 浏览器窗口无法访问（锁屏、窗口不可见、Chrome 未运行）属于**运行环境受阻**，与风控和页面异常分开处理（第 3.7 节） |
| D6 | 新增 `scripts/desktop.py`（标准库），负责环境检测和工具返回结果的分类，让外发安全相关的判断可以单测，不完全依赖技能文字 |

保持不变：不使用 CDP / Playwright；不模拟人工、不规避平台检测（`shared/safety.md`）；可能外发前写 `pending`，每个可能外发的动作前 `guard`，通过后立即点击；确认已发送仍以页面出现新消息并带「送达」或「已读」为依据；`uncertain` 只能由本人核对后改记；已有终态不覆盖。

---

## 1. 实测结论（2026-11-02，agent-desktop 1.0.2，响应格式版本 2.5，macOS 26，Chrome 156）

测试由测试 Agent 经 Pi 调用，在本人日常使用的 Chrome 中进行。BOSS 上的写入只发生在本人指定的会话里，真实发送只有一次，内容由本人指定。

| 项 | 结论 |
|---|---|
| 读取 | 搜索结果页、职位详情、会话列表、会话消息都能读到。职位名、公司名、「立即沟通」是 `link`；聊天输入框是 `textfield`，DOM id 为 `chat-input`；「发送」是 `button`；职位搜索框是 `textfield`，能读回当前查询词。薪资、城市、会话行、消息行都是没有角色的 `group` 加 `statictext`，只能按顺序和内容理解 |
| 页面风控 | 四轮测试读取前后地址都没变，没有出现 `_security_check` 或空白页 |
| 后台写入 | `set-value` 写入 `chat-input` 后，会话列表出现「草稿」标记，清空后消失，可以复现，说明页面接住了内容。**真实发送一次成功**：`set-value` 后以后台模式点击「发送」，消息出现在我方一侧并带「送达」，输入框清空 |
| 后台 `type` | 在 Chrome 中无效（`ACTION_FAILED`，返回「可能已送达、不可重试」），不能用 |
| 前台 `--headed type` | 能写入 `chat-input`，在 BOSS 上与 `set-value` 效果相同，但会抢占焦点 |
| 合成测试页对照 | 对 `contenteditable` 用 `set-value`，文字进入了页面，但页面没收到 `input` 事件，属于静默失败；对 `textarea` 正常。从辅助功能树上分不出这两种输入框 |
| 「发送」按钮 | 空输入框时也是 `enabled`，不能据此判断输入框是否有内容 |
| 草稿残留 | 切换会话后，输入框里的文字仍然显示；草稿归属于哪个会话无法从输入框本身判断 |
| 点击「发送」的返回 | `delivered_unverified`，`retry: unsafe`；是否发出只能靠观察页面 |
| 区分双方消息 | 我方消息带「送达」或「已读」，并在右侧；HR 的消息没有这个标签，并在左侧。位置只能做左右两侧比较，不能写死坐标。尚未在同一个会话里对照过 |
| 职位 ID | 读不到链接地址（工具不提供 `AXURL`，已提功能请求 vercel-labs/agent-desktop#492）；右键「复制链接地址」能打开菜单但点不动（缺陷 #491）。只能从职位详情页的地址栏读取 |
| 锁屏 | Chrome 窗口返回 `ACTION_NOT_SUPPORTED`，`details.kind` 为 `window_without_accessibility_element`，读和写都做不了；解锁后恢复 |
| 读取量 | 先读粗略结构约 5 KB，读整个页面内容 60–85 KB |

这些结论证明了工具具备这些能力，但不能证明第 3 节的安全规则在所有情况下都成立；后者靠第 5 节的测试和第 6 节的验收。

---

## 2. 配置

### 2.1 `profile.json` 的 `browser`

```json
"browser": {"control": "agent-desktop", "input": "background"}
```

| 情况 | 处理 |
|---|---|
| 没有 `browser` 字段（旧工作区） | 运行时按 `{"control": "agent"}`，即现有行为；`workspace check` 报 `warnings`，提示用 `setup` 补设 |
| `{"control": "agent"}` | 使用当前 Agent 自带的桌面工具；不能有 `input` 字段，有则报 `problems` |
| `{"control": "agent-desktop", "input": "background" 或 "foreground"}` | 使用 agent-desktop；`input` 必填 |
| `null`、空对象、不是对象、缺 `control`、取值不合法、有未知字段 | `problems`；技能不运行平台操作，**不静默回退**到任何一种方式 |

- 新建工作区的推荐值是 `{"control": "agent-desktop", "input": "background"}`，由本人在 `browser` 步骤确认，不自动写入。
- `shared/workspace.md` 的 `profile.json` 示例与字段说明同步更新。

### 2.2 setup 步骤目录新增 `browser`

| 阶段 | 步骤 id | 名称 | 写入位置 | 必答 | 典型模式 | 依赖 |
|---|---|---|---|---|---|---|
| `prepare` 准备 | `browser` | 浏览器操作 | `profile.browser` | 否 | **问**（给出推荐值，不预先记为默认） | 放在 `account` 之后 |

这一步要做的事：

1. 说明可选项，各一句话：
   - agent-desktop 后台：不抢焦点，运行时可以用电脑做别的事，但不能动 BOSS 所在的 Chrome 窗口；
   - agent-desktop 前台：运行期间独占前台，不要碰键盘鼠标；
   - 当前 Agent 自带的工具。
   所有方式都要求屏幕不锁定、Chrome 已打开并登录 BOSS。
2. 说明「只读」由后面的授权步骤和本轮指令决定（D3），不在这里选。
3. 选择 agent-desktop 时，运行 `desktop.py preflight`（第 3.1 节）做只读检测，把结果告诉本人。未安装、未授权或版本未经验证时，说明安装与授权方法（`agent-desktop permissions --guide`），由本人自己操作；可以先记下选择，检测结果另由 `workspace check --env` 报告（第 2.3 节）。
4. `limits.dedupScope` 为 `job` 且选择 agent-desktop 时提醒本人：列表页读不到职位 ID（第 1 节），无法按职位去重的职位按现有规则处理。

**写入规则**（`progress` 只记录回答，`profile.json` 的合并由 setup 的写入阶段完成）：

| 情况 | `profile.browser` |
|---|---|
| 新建，本步选定 | 写入所选值 |
| 新建，跳过本步 | 不写，保持缺失 |
| 重新设置或部分重新设置，本步「改」 | 写入新值 |
| 重新设置或部分重新设置，本步「保留」或「跳过」 | **保留当前值**，包括缺失状态，不删除、不补默认 |
| 部分重新设置没有选中本步 | 不动 |
| 继续一份不含本步的旧草稿 | 不增、不删、不改当前配置中的 `browser` |

**已有的 setup 草稿**：计划保持原样，不自动插入 `browser`。本人想设置时，草稿完成后另做一次只选 `browser` 的部分重新设置。对不含该步骤的旧计划执行 `goto browser` / `record browser` 时报错，提示改用部分重新设置。

`listener` 步骤补充：监听器由 launchd 唤起时，辅助功能权限属于实际调用 agent-desktop 的进程，可能与交互终端不同；首次唤起要本人在场确认一次。

`STEPS` 是唯一定义，`skills/setup/SKILL.md` 的步骤表与 `docs/design.md` 第 5A 节的阶段列举同步更新。

### 2.3 环境检测与配置检查分开

- `workspace check` 只检查配置结构（第 2.1 节），结果不依赖本机环境。
- `workspace check --env`（新增参数）在配置为 agent-desktop 时额外调用 `desktop.py preflight`，把结果作为 `warnings` 报告。`setup` 在 `progress finish` 之后用它向本人说明环境状态。
- 检测结果只说明「检测时、检测进程」的状态；监听器下的权限以首次唤起的实际结果为准（A7）。

---

## 3. 新增 `scripts/desktop.py` 与 `shared/browser/agent-desktop.md`

仅在 `profile.browser.control` 为 `agent-desktop` 时使用。

### 3.1 `scripts/desktop.py`

标准库实现，输出一个 JSON 对象。agent-desktop 可执行文件的路径可由环境变量 `JOB_WINGMAN_AGENT_DESKTOP` 指定（测试用来注入假程序），默认在 `PATH` 中查找。调用子进程时设置超时（默认 20 秒，可用 `--timeout` 调整）。

**所有会改变页面的 agent-desktop 动作（点击、写入、清空、切换标签等）都经 `desktop.py run` 调用**，不直接调用 agent-desktop。这样分类时能拿到完整的进程结果，而不只是标准输出。读取命令也建议经 `run` 调用，以便统一识别环境受阻。

**`preflight`**：只读检测，输出 `ready`（可以读取平台）、`canSend`（可以外发）与原因列表。

| 检测项 | 不满足时 |
|---|---|
| 已安装、能执行 | `ready: false` |
| 辅助功能权限为 `granted` | `ready: false` |
| Chrome 正在运行，并且有至少一个能通过辅助功能读取的窗口 | `ready: false` |
| 响应格式版本在受支持集合内（当前只有 `2.5`） | `ready` 不变，`canSend: false` |
| 任一检测超时或输出无法解析 | `ready: false` |

`ready: false` 时 `canSend` 一定是 `false`。

**`run -- <agent-desktop 参数…>`**：执行**一次** agent-desktop 命令，记录进程结果（标准输出、退出码、是否超时、是否被信号终止、能否启动），原样附上工具的 JSON 输出，并给出两个互相独立的结论。

先看进程结果：

- 超时、被信号终止、无法启动：直接判 `execution: unknown`、`environment: unknown`，**即使标准输出中有可解析的 JSON 也不采用**。
- 退出码：按该版本 `docs/json-output.md` 中的退出码契约，区分「工具按契约返回结构化错误」（可以继续按下表分类）与其他非零退出（`unknown`）。实现时把受支持的退出码写成常量，契约未写明的退出码一律视为 `unknown`。

`execution`：这个动作是否执行了。

| 取值 | 条件（全部满足） |
|---|---|
| `not_executed` | 进程正常结束，退出码符合契约；输出能解析且结构完整；响应格式版本受支持；`ok` 为 `false`；`error.disposition.delivery` 为字符串 `not_delivered`，`error.disposition.retry` 为字符串 `safe`；`error.code` 在白名单内（`STALE_REF`、`AMBIGUOUS_TARGET`、`SNAPSHOT_NOT_FOUND`、`ELEMENT_NOT_FOUND`、`INVALID_ARGS`、`WINDOW_NOT_FOUND`、`APP_NOT_FOUND`、`ACTION_NOT_SUPPORTED`，均已在 1.0.2 的 `docs/json-output.md` 中核对）；报告中任何位置都没有非空的 `steps`。`steps` 字段缺失视为没有已执行步骤：契约规定每个错误都带 `disposition`，`not_delivered` 即表示没有发出，查找失败与参数错误在发出任何操作之前就失败（2026-11-03 核对，实测 `SNAPSHOT_NOT_FOUND` 的错误信封不带 `steps`）；字段存在时必须是对象数组，`null` 或类型不符判 `unknown` |
| `maybe_executed` | 输出能解析、结构完整、版本受支持，但不满足 `not_executed`，例如 `ok: true`、`delivered_unverified`、`delivered_verified`、`retry: unsafe`、有已执行的步骤 |
| `unknown` | 输出不能解析、被截断、版本不受支持、缺少必需字段、字段类型不符；或子进程超时、被信号终止、无法启动、退出码不符合契约 |

另给出 `succeeded`：进程正常结束、`execution` 为 `maybe_executed`、`ok` 为 `true`、没有 `error`。第 3.6 节的「正常流程」只认 `succeeded: true`。

`environment`：浏览器窗口能否访问。

| 取值 | 条件 |
|---|---|
| `unavailable` | 输出能解析，且错误的 `details.kind` 为 `window_without_accessibility_element`，或错误码为 `APP_NOT_FOUND` / `WINDOW_NOT_FOUND` |
| `available` | 其他能解析的输出 |
| `unknown` | 输出不能解析 |

两个结论分开使用：`environment: unavailable` **不说明**动作是否执行，执行与否只看 `execution`。例如一次点击返回窗口错误、但有已执行的步骤，结果是 `execution: maybe_executed, environment: unavailable`。

### 3.2 运行顺序与锁

`apply`、`followup` 在**获取运行锁之前**先运行 `desktop.py preflight`（不接触平台页面）：

- `ready: false`：不获取可外发的锁，本轮不操作平台，按第 3.7 节处理。仍需要做不涉及平台的事（读 IM、改 inbox、对账）时，用 `--no-send` 锁。
- `canSend: false`：本轮按显式禁发运行，用 `--no-send` 获取锁，汇报中说明原因。
- 都满足：按原规则获取锁。

`ledger lock acquire` 对同一个 `runId` 只是沿用原锁，**不能把已有的可外发锁降级为禁发**。所以运行中出现 `execution: unknown` 或环境受阻后，不靠「改为禁发」继续：按第 3.6 节给当前尝试收尾，然后**结束整轮**，不再新建尝试。

### 3.3 准备与定位（`agent-desktop.md`）

- 首次使用时读工具自带的说明：`agent-desktop skills get desktop`、`agent-desktop skills get platform`。
- **定位 BOSS 页面**：
  1. 从 Chrome 窗口中找候选：窗口标题以「BOSS直聘」开头的，或者在窗口标签栏中能找到标题以「BOSS直聘」开头的标签页的。按前缀匹配，不用模糊匹配（测试中模糊匹配曾误命中标题含「BOSS直聘」的其他网站标签页）。
  2. 零个候选：不新开窗口或标签页，不启动浏览器，按第 3.7 节「找不到页面」处理。
  3. 一个候选：需要时在标签栏切换到该标签页；然后核对页面确实是 zhipin.com 的页面（看页面结构和导航，能看到地址栏时核对域名），并核对账号名与 `job-wingman.json` 的 `displayName` 一致。
  4. 多个候选：逐个只读核对账号；只有恰好一个账号一致时才使用；否则停止并请本人关掉多余的窗口或标签页。
  5. 之后的命令都带 `--window-id`。
- 运行期间不同时使用其他桌面控制工具，也不同时运行另一个桌面 Agent。

### 3.4 读取

- 先 `snapshot --skeleton -i --compact`，再用 `--root` 展开需要的区域；不反复输出整页。
- 引用只在所属的那次快照内有效。`STALE_REF`、`AMBIGUOUS_TARGET` 时重新快照后定位。**重新定位只用于读取，或用于 `execution: not_executed` 的动作**；可能已执行的外发动作不因重新定位而重放。
- 输入框只按 DOM id `chat-input` 定位（`find --native-id chat-input`）；其他元素按角色和名称精确匹配，并限定在已核对的会话或卡片范围内（`--root`），不在整页按名称找按钮。
- 区分双方消息：「送达 / 已读」标签和左右位置两者都符合才下结论；对不上时按「分不清发言人」处理，遵循 followup 现有规则。
- 读取命令经 `desktop.py run` 调用时：`execution` 对读取没有意义，但 `environment` 和 `unknown` 有意义。读取得到 `unknown` 或 `unavailable` 时，**先看本轮有没有未终结的尝试**，按第 3.6 节收尾，不能因为最后一个命令只是读取就直接结束。

### 3.5 输入

**能力表**

| 控件 | 后台写法 | 写入后如何确认生效 | 状态 |
|---|---|---|---|
| 聊天输入框 `chat-input` | `set-value` | 读回值与拟发文字完全一致；发送后会话中出现内容相同的新消息 | 2026-11-02 实测通过 |
| 职位搜索框 | `set-value`，再点「搜索」按钮 | 搜索结果的条件与列表确实变成新查询，不能只看读回值 | 未实测，A2 验证 |
| 联系人搜索框、其他输入框 | 未验证 | — | 不使用；需要时先在不外发的情况下验证并补进本表 |

- 所有输入框都**禁止后台 `type`**（在 Chrome 中无效且返回不可重试）。
- 某个控件在本轮所选的输入方式下无法确认生效时，停止该操作并汇报。**不在运行中改用另一种输入方式试错**（D4）；本人可以下一轮在 setup 中改选前台，或手动搜索。
- 本轮显式禁发、L1 只起草、「只看看」时，**不修改聊天输入框和其他会产生外发内容的控件**（包括不清除草稿）。搜索框、筛选项等只影响页面显示的控件可以操作，但不能触发消息或请求。

**聊天输入框：写入前的检查（在写 `pending` 之前完成）**

1. 核对会话：会话标题中的公司、招聘者与目标一致，并且该会话没有被本人接管（按 followup 第 4.1 节等现有规则）。**核对通过前不读写输入框。**
2. 拟发文字必须非空、不是纯空白，且是本轮最终版本。
3. 读取 `chat-input` 当前的值。不为空时**不清空**：这些文字可能是本人的草稿，也可能是从别的会话残留下来的，无法判断归属。
   - followup：该会话本轮不处理，不写 `pending`，在汇报中注明「输入框有未知内容」。
   - apply：此时已点击过「立即沟通」，尝试已经存在，按第 3.6 节收尾。

followup 的现有顺序是「第 5.0 节检查（含写 `pending`）→ 输入 → 发送」。使用 agent-desktop 时，上面 1–3 步移到写 `pending` **之前**；第 5.0 节其余检查不变。

**写入与发送**（后台；前台把 `set-value` 换成 `--headed type`、把点击换成 `--headed click`，其余相同）

4. `set-value "<全文>"`，读回值必须与拟发文字**完全一致**。不一致时：只有读回值能确认是本次写入的内容（与拟发文字或其前缀相同），才 `clear` 并读回确认为空；否则保留现状。然后按第 3.6 节收尾，不发送。
5. 发送前再核对一次会话标题；然后 `ledger guard`，通过后立即对会话范围内的「发送」点击一次。

### 3.6 一次尝试的阶段与收尾

agent-desktop 的返回描述的是**一次工具动作**；`attemptId` 描述的是**一次业务尝试**，可能包含多个动作。尝试的最终状态仍由 `apply` 第 5.3 节、`followup` 第 5 节和 `shared/ledger.md` 的现有规则判定，本节只补充工具返回怎样作为证据使用，以及停止时怎样收尾。

**首次沟通（apply）的阶段**

| 阶段 | 可能外发 | 说明 |
|---|---|---|
| ① 点击「立即沟通」 | 是 | 平台开启自动打招呼时会直接发出默认招呼语。点击前 `guard` |
| ② 观察结果 | — | 见下面的「正常流程」 |
| ③ 写入前检查与写入 | 否 | 第 3.5 节 |
| ④ 点击「发送」 | 是 | 再 `guard` 一次后点击 |
| ⑤ 确认结果 | — | 出现内容相同的新消息并带「送达」或「已读」，记 `sent` |

**正常流程**：① 经 `desktop.py run` 返回 `succeeded: true` 且 `environment: available`（只有 `maybe_executed` 不够：其中也包括 `ok: false`、`retry: unsafe` 等失败响应），随后连续观察到页面进入目标会话（标题中的公司、招聘者一致）。之后：

- 会话中有平台自动发出的招呼语：按 apply 现有规则记 `sent`；
- 会话为空、没有我方消息：这是正常中间状态，继续 ③。

**放弃发送的例外**，即 apply 第 5.3 节第 4 步「点击后放弃发送，会话中确认没有我方消息，记 `failed_no_send`」，只在同时满足以下条件时适用：

1. ① 属于正常流程；
2. 正常进入了目标空会话；
3. **还没有调用 ④ 或其他后续可能外发的动作**；或者调用过，但这些后续动作（含重做）的 `execution` 全部是 `not_executed`。

`evidence` 要写明：目标身份（公司、招聘者）、空会话的依据（读到的消息列表为空、没有「送达」或「已读」）、放弃发生在哪一阶段和原因；有过 `not_executed` 的后续动作时，一并写明其错误码。

以下情况**都不适用**这一例外，之后即使没看到消息，也只能记 `uncertain`（与 `platforms/boss.md`「工具超时」一节一致）：

- ① 不是 `succeeded: true`，例如失败响应、超时、`execution: unknown`、`environment: unavailable`，即使之后读到空会话也一样；
- 目标丢失，或没能连续观察到进入目标会话；
- ④ 或其他后续可能外发的动作有任何一次不是 `not_executed`。

**回复（followup）**：第 3.5 节第 1–3 步通过后写 `pending`，然后写入（③）、发送（④）、确认（⑤）。
**简历与卡片**：按 followup 第 5.2 节与 `platforms/boss.md` 的现有语义判断结果（「请求待同意」、附件已送达、对方已查看分别记录），不套用文字消息「出现新气泡」的判据。按钮必须限定在已核对的会话和具体卡片内定位；卡片之后已有「附件已发送」等提示时不再点击。每个可能外发的点击（工具栏「发简历」、选择附件后的确认、卡片「同意」）之前都单独 `guard`。授权范围不因工具不同而改变。

**工具返回作为证据**

| `execution` | 处理 |
|---|---|
| `not_executed` | 这个动作没有执行。可以重新快照、重新定位，再次 `guard` 后重做**这一个动作**，同一尝试内最多重做 1 次；仍然 `not_executed` 时停止该目标，按下面的收尾规则处理。① 最终没有执行时，不推进到 ③、④ |
| `maybe_executed` | 按上面的阶段观察结果；看不到预期结果时记 `uncertain`，停止该目标（apply 按 `platforms/boss.md` 停止整轮）。**不重试** |
| `unknown` | 可能外发的动作之后按 `maybe_executed` 处理；然后结束整轮（第 3.2 节） |

`environment: unavailable` 另按第 3.7 节处理。

**任何停止分支都先收尾**：停止一个目标或结束整轮之前，先检查本轮有没有未终结的尝试（只有 `pending`），逐个按下表处理。

| 该尝试的情况 | 记录 |
|---|---|
| 已有终态 | 不覆盖 |
| 已观察到明确的发送成功证据（apply 第 5.3 节第 3 步、followup 的对应判据），但还没追加终态 | `sent`，`evidence` 写明看到了什么；**优先于任何停止原因**，不能因为随后停止而改记其他状态 |
| 没有调用过任何可能外发的动作 | `failed_no_send`，`evidence` 写明停在哪一步、为什么没有点击 |
| 调用过可能外发的动作，而且这些动作的 `execution` 都是 `not_executed` | `failed_no_send`，`evidence` 写明每个动作的错误码，并注明证据来自工具返回 |
| 正常流程中放弃发送（见上） | `failed_no_send`，`evidence` 按上面的要求 |
| 页面明确显示发送失败（apply 第 5.3 节第 6 步） | `failed_no_send`，`evidence` 写明看到了什么 |
| 其他所有情况：调用过可能外发的动作，结果不能证实 | `uncertain`；写入 inbox，请本人核对；followup 中按现有规则暂停该会话 |
| 本轮没有任何尝试 | 不写动作事件 |

「重新读取后没看到新消息」本身不是 `failed_no_send` 的证据（`shared/ledger.md`）。

### 3.7 运行环境受阻

以下情况都按「浏览器窗口无法访问」处理：`preflight` 的 `ready` 为 `false`，任何命令的 `environment` 为 `unavailable`，或 `execution` / `environment` 为 `unknown`。不能单凭工具返回确诊锁屏，汇报和通知中写「可能锁屏、窗口不可见或 Chrome 未运行」，并附上 `preflight` 给出的原因。

如果此前已经观察到验证码、安全验证、账号不符、登录失效，**仍按 `shared/safety.md` 优先处理**（整轮停止、全局暂停）。

处理顺序：

1. 按第 3.6 节给本轮未终结的尝试收尾，然后结束整轮。不全局暂停。
2. **IM 回复**（followup 执行已决定的事项时）：
   - 该事项还没有形成阻断终态（没有 `uncertain` / `failed_no_send`），只是暂时无法执行：保留 `ready`，不标 `consumed`；
   - 已形成 `uncertain` 或 `failed_no_send`：按 `shared/notify.md`「收尾分流」中阻断的规则处理，确认本轮无法继续时标 `consumed`（`--note` 写原因），并如实通知本人需要核对，**不承诺恢复环境后会自动续发**。
3. **通知本人**：
   - 环境提醒用 `notify send --kind alert --key env-blocked:<日期>:<原因代码>`。原因代码取自 `preflight` 或 `run`，例如 `not-installed`、`no-permission`、`chrome-not-running`、`window-unavailable`、`version-unsupported`、`output-unknown`。同一天同一原因只发一次，不同原因分别发。
   - 正文写原因和对应的恢复办法：
     - 窗口无法访问：解锁屏幕、打开 Chrome 并登录 BOSS；
     - 未安装、无权限、版本不受支持：需要本人处理环境，回到电脑前处理后再运行；
     - 恢复后怎么继续：由监听器唤起、且还有待执行的答复时回复「重试」；手动运行的需要重新发起这个技能。
   - `uncertain` 事项另按现有规则写入 inbox、发它自己的通知，**环境提醒不能代替它**。
   - `im.channel` 为 `none` 时不发 IM，只在汇报中写明原因和恢复办法；需要本人处理的事项照常写 inbox。

### 3.8 禁用项与隐私

- 禁用 `launch --cdp` 和其他 CDP 相关功能；不开 `trace`；不用 `--screenshot` 调试输出。
- 不把「写入 → 发送」合并成一条命令或一次 `batch`。
- 只读取完成本步所需的区域，优先用 `find`、`get` 和 `--root`。不把完整快照回显到汇报、inbox 或 IM，不主动写入文件。
- 监听器会把每次唤起的 Agent 输出保存在工作区的 `ledger/listener-runs/`，Agent 宿主也可能保存工具输出。这些位置在工作区或本机，已在私有目录的忽略规则中；方案不承诺零留存，只承诺不主动导出。

### 3.9 已知限制

- 读不到职位链接地址（#491、#492）：`jobUrl`、`jobId` 记 `null`，从独立职位页进入时可以从地址栏读取。
- 已有记录说搜索结果列表中的薪资可能显示为特殊字符，用 agent-desktop 读取时尚未复核，仍以详情页为准。
- 浏览器窗口无法访问时（如锁屏）不可用。
- 经过验证的版本：agent-desktop 1.0.2，响应格式版本 2.5。

---

## 4. 其他文件改动

| 文件 | 改动 |
|---|---|
| `shared/browser/README.md` | 「选择控制方式」先读 `profile.browser`：`agent-desktop` 按 `agent-desktop.md`；`agent` 或缺失按当前 Agent 的对照表；配置不合法时不操作平台。选定后整轮不换（D4）。「页面空白或异常跳转」一节注明：窗口无法访问按 `agent-desktop.md` 第 3.7 节处理 |
| `skills/apply/SKILL.md` | 第 2 步获取锁之前先做第 3.2 节的检测；「准备浏览器」引用上述规则；第 5.3 节注明使用 agent-desktop 时按 `agent-desktop.md` 第 3.6 节的阶段、「正常流程」与收尾规则 |
| `skills/followup/SKILL.md` | 第 1 节获取锁之前先做第 3.2 节的检测；第 5.0 / 5.1 节注明使用 agent-desktop 时，写入前检查移到写 `pending` 之前；简历与卡片动作的定位范围与逐次 `guard` |
| `skills/apply/platforms/boss.md` | 职位 ID 的读取限制；「发送」在空输入框时也可点；切换会话后输入框文字仍显示，**不自动清空**，按 `agent-desktop.md` 第 3.5 节处理 |
| `skills/followup/platforms/boss.md` | 区分双方消息的依据；会话列表的「草稿」标记表示该会话有未发送内容，巡检时**不清除** |
| `skills/setup/SKILL.md` | 新增 `browser` 步骤与写入规则（第 2.2 节）；`listener` 步骤的提醒；`progress finish` 后用 `workspace check --env` |
| `shared/workspace.md` | `profile.browser` 的示例与字段规则；`check --env` |
| `shared/notify.md` | `alert` 的用途中加入运行环境受阻，引用 `agent-desktop.md` 第 3.7 节；收尾分流加一条「环境受阻」 |
| `scripts/workspace.py` | `STEPS` 新增 `browser`；`check_profile` 校验 `browser`；`check --env`；旧计划上 `goto` / `record browser` 报错 |
| `scripts/desktop.py` | 新增（第 3.1 节） |
| `scripts/build.py` | 独立技能带上 `desktop.py` 与 `agent-desktop.md`（如现有逻辑未自动覆盖） |
| `docs/design.md` | 第 3 节仓库结构；第 5A 节阶段列举；第 11 节写入 D1–D6 与第 1 节要点 |
| `docs/status.md` | 已实现、单测通过；2026-11-02 工具抽查通过（不是正式验收）；待验收项见第 6 节 |
| `README.md` | 可选安装 agent-desktop，并授予辅助功能权限 |

---

## 5. 测试要求

### 5.1 单元测试

全部使用合成工作区、临时目录和假的 agent-desktop（经 `JOB_WINGMAN_AGENT_DESKTOP` 注入，按参数返回预设输出），不调用真实工具，也不碰 BOSS。

1. **`desktop.py run`**（用假程序按预设输出、退出码、延时或信号返回）：
   - 相同的标准输出，进程正常结束与超时 / 被信号终止 / 退出码不符合契约，分类不同（后者都是 `unknown`）；
   - 只有 `ok: true` 且进程正常结束时 `succeeded: true`；
   - `not_executed` 的每个白名单错误码；
   - `not_delivered` 加 `retry: safe` 但有已执行步骤 → `maybe_executed`；
   - 错误码不在白名单 → 不是 `not_executed`；
   - `steps` 缺失、为 `null`、类型不符，按契约判定（契约未明确时不是 `not_executed`）；
   - `ok: true`、`delivered_unverified`、`delivered_verified`、`retry: unsafe`；
   - 窗口错误且没有已执行步骤 → `not_executed` + `unavailable`；窗口错误但有已执行步骤 → `maybe_executed` + `unavailable`；
   - 字段矛盾（如 `ok: true` 却带 `error`）、无法解析、被截断、缺 `disposition`、字段类型不符、版本不受支持 → `unknown`。
2. **`desktop.py preflight`**：未安装、无法执行、子进程超时、非零退出且无输出、版本不受支持（`ready: true, canSend: false`）、辅助功能未授权、Chrome 未运行、没有可访问的窗口、正常。
3. **`workspace check`**：第 2.1 节表中每种情况，包括 `null`、空对象、缺 `control`、`agent` 带 `input`、`agent-desktop` 缺 `input`、未知字段；`--env` 只在配置为 agent-desktop 时调用 `preflight`，不带 `--env` 时结果不依赖本机环境。
4. **setup 步骤目录**：
   - `browser` 位于 `account` 之后；`SKILL.md` 的步骤表一致（复用现有核对测试）；
   - 典型模式开始时 `browser` 未预先作答；
   - 只选 `browser` 的部分重新设置；跳过、保留、中断后接续；
   - 不含 `browser` 的旧草稿可以继续并完成；对旧草稿 `goto browser` / `record browser` 报错。
   现有测试只改与新步骤直接相关的断言，例如 `test_limits_defaults_and_setup_steps_unchanged`，其他断言不动。
5. **运行锁**：用 `--no-send` 获取的锁，`ledger append` 写 `pending` 和 `ledger guard` 都被拒绝；对同一 `runId` 再次获取锁不会改变 `noSend`（记录现有行为，防止以后误以为可以降级）。
6. **构建**：独立技能包含 `desktop.py` 与 `agent-desktop.md`，引用路径完整。
7. **文档约束检查**（与现有新规则完整性检查同类）：
   - `agent-desktop.md` 写明禁用 `launch --cdp`、禁止后台 `type`、核对会话后才读写输入框、非空输入框不清空、「正常流程」的限定条件、停止前收尾的表格；
   - `apply`、`followup` 都在获取锁之前做检测，「准备浏览器」都引用 `browser/README.md`。
   这类检查只防止规则被误删，不能证明 Agent 的行为正确。
8. **全量回归**：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`。

### 5.2 不接触平台的行为抽查

单元测试只能证明单次分类和配置检查正确，不能证明 Agent 在多步流程中选对了终态。另做一组**脚本化的行为抽查**：在合成工作区里，用真实 Agent 按技能运行，假的 agent-desktop 按预设脚本返回页面结构和动作结果，事后检查 `ledger`、inbox 与 IM 发送记录（IM 用假通道）。至少覆盖：

| 场景 | 期望 |
|---|---|
| ① 返回 `succeeded: true`，随后正常进入空会话，在调用 ④ 之前放弃发送 | `failed_no_send`，`evidence` 包含目标身份、空会话依据和放弃阶段 |
| ① 返回结构完整的失败响应（`ok: false`、`retry: unsafe`），随后读到空会话 | `uncertain`，不适用放弃发送的例外 |
| ① 正常、进入空会话，④ 已调用并返回 `maybe_executed`，页面尚未出现消息时停止 | `uncertain` |
| ④ 返回成功、页面已出现带「送达」的新消息，随后因其他原因停止 | `sent`，不被停止原因覆盖 |
| ① 超时，重新读取看到空会话 | `uncertain`，停止整轮 |
| 进入会话后输入框有未知内容（apply） | 不清空；按正常流程收尾为 `failed_no_send` |
| followup 遇到输入框有未知内容 | 不写 `pending`，不清空，汇报中注明 |
| ① 返回 `not_executed`，重做一次仍 `not_executed` | 不推进到 ③、④；`failed_no_send`，`evidence` 列出错误码 |
| ① 正常、进入空会话，④ 返回 `not_executed`，重做一次仍 `not_executed` | `failed_no_send`，`evidence` 同时写明空会话依据和 ④ 的错误码 |
| ① 不正常（超时或失败响应），④ 返回 `not_executed` | `uncertain` |
| ④ 返回成功，随后读取结果时输出损坏 | `uncertain`，结束整轮 |
| 发送后读取时窗口变为不可访问 | `uncertain`；环境提醒与 `uncertain` 的 inbox 通知分别发出 |
| 开始前 `preflight` 版本不受支持 | 用 `--no-send` 锁；`pending` 被拒绝；汇报说明 |
| 已批准事项在环境受阻时尚未建立尝试 | `ready` 保留；收到 `env-blocked` 提醒；同一天同一原因不重复 |
| 已批准事项环境受阻时已记 `failed_no_send` | 按收尾分流消费 `ready`，通知需要本人处理，不承诺自动续发 |
| 已有终态的尝试 | 不被覆盖 |

这组抽查由测试 Agent 执行，不接触真实平台和真实工作区；结果单独记录，不替代第 6 节的验收。

---

## 6. 验收（由本人发起，分别记录）

所有标为「无外发」的项目都以**本轮显式禁发**运行（「只看看」），并核对已取得 `--no-send` 锁。这样即使工作区里有已批准、尚未执行的事项，本轮也不会发出。

| # | 内容 | 外发 |
|---|---|---|
| A1 | `setup`：只选 `browser` 的部分重新设置（检测结果、推荐值、未授权时的提示）；重设时跳过或保留不改动原值；旧草稿不增删 `browser` | 无 |
| A2 | `apply` 只筛选（agent-desktop 后台）：定位窗口（含多窗口时停止）、搜索框后台写入是否生效、详情、活跃标签、薪资可读性、页面无风控 | 无 |
| A3 | `followup` 巡检（后台）：分诊、区分双方消息（含一个双方都发过消息的会话）、遇到「草稿」标记时不清除并跳过 | 无 |
| A4 | 锁屏时由监听器唤起一轮：识别为窗口无法访问，不写 `pending`，`ready` 保留，提醒只发一次；解锁后回复「重试」能继续 | 无 |
| A5 | 由本人发起并指定目标：一次 `apply` 首次沟通、一次 `followup` 代答（后台） | 有 |
| A6 | 前台输入方式一轮（本人在场，不碰电脑） | 由本人决定 |
| A7 | 监听器由 launchd 唤起时，agent-desktop 的权限是否有效 | 无 |

单测通过、行为抽查通过和正式验收分别记录，不互相替代。

---

## 7. 不在本次范围

- DeepSeek Harness 打包（`package.json` 的 `dsh` 字段等）：另立方案。
- 监听器在唤起前检测锁屏：先由技能处理；如果锁屏时频繁唤起、浪费资源，再考虑。
- 运行中把可外发的锁原子降级为禁发：本版改为结束整轮；以后需要时再加接口。
- 让后台模式支持「本人同时使用同一个 Chrome 窗口」：需要另行设计目标绑定与并发控制。
- 上游 #491 / #492 修复后，从列表读取职位链接：届时修改第 3.9 节与 `apply/platforms/boss.md`。

---

## 附录 A：第一轮红队审查意见的处理（2026-11-03）

| # | 意见 | 处理 |
|---|---|---|
| 1 | 工具动作与业务尝试的语义混在一起，`failed_no_send` 证据不足 | 采纳：第 3.6 节按阶段定义；工具返回只作为补充证据，现有基于页面事实的规则不变，并限定「正常流程」；重新定位不重放可能已执行的动作 |
| 2 | 后台模式承诺「可以一边用电脑」超出隔离能力 | 采纳：D2 与第 2.2 节改为「不抢焦点，但不能动 BOSS 所在窗口」；真正并发列入第 7 节 |
| 3 | 会无条件删除本人草稿，且在核对身份之前删除 | 采纳：第 3.5 节先核对会话；非空输入框不清空；只清除能确认是本次写入的内容；只读运行不修改聊天输入框 |
| 4 | 锁屏分支没处理已有的 `pending` | 采纳：第 3.6 节统一收尾；第 3.7 节不确诊锁屏，风控信号优先 |
| 5 | L1 不等于绝对禁发 | 采纳：D3 改写；无外发验收一律本轮显式禁发并核对 `--no-send` 锁 |
| 6 | 简历和卡片动作被套用文字消息的判据 | 采纳：第 3.6 节沿用 followup 第 5.2 节语义，限定定位范围，逐次 `guard` |
| 7 | 缺少非空约束；读回一致不足以证明页面接收 | 采纳：第 3.5 节加非空约束；以发送后出现内容相同的新消息为最终依据；能力表标明实测日期 |
| 8 | 窗口发现没覆盖多窗口、后台标签页、Chrome 未运行 | 采纳：第 3.3 节规定零个、一个、多个候选，核对账号，不启动浏览器 |
| 9 | 旧 setup 草稿与典型模式缺少检测闭环 | 采纳：旧草稿保持原计划；典型模式也问；环境检测改由 `check --env` |
| 10 | 配置契约不完整，遗漏 `shared/workspace.md` | 采纳：第 2.1 节完整定义；不静默回退；第 2.2 节写入规则 |
| 11 | 锁屏或权限失败后的 IM 生命周期未定义 | 采纳：第 3.7 节；A4、A7 |
| 12 | 未知版本照常外发；缺少响应契约测试 | 采纳：D6 新增 `desktop.py`；版本不受支持时禁发；第 5 节单测与行为抽查 |
| 13 | 搜索框等非聊天输入的规则缺失 | 采纳：第 3.5 节能力表；搜索框后台写入是否生效列入 A2 |
| 14 | 隐私要求需覆盖工具输出与宿主日志 | 采纳：第 3.8 节最小化读取、不回显、不承诺零留存 |

## 附录 B：第二轮红队审查意见的处理（2026-11-03）

| # | 意见 | 处理 |
|---|---|---|
| 1 | 分类条件重叠，`unavailable` 丢失「可能已执行」的信息 | 采纳：第 3.1 节拆为 `execution` 与 `environment` 两个独立结论；严格的结构校验与错误码白名单；`steps` 缺失按契约判定；补测试 |
| 2 | 页面事实例外可能绕过超时后的不确定状态 | 采纳：第 3.6 节定义「正常流程」，例外只在其中适用；异常之后仍为 `uncertain`；`evidence` 要求三项内容；不再把「所有动作都 `not_executed`」写成 `failed_no_send` 的必要条件 |
| 3 | 若干停止分支遗漏已有尝试的终态 | 采纳：第 3.6 节「任何停止分支都先收尾」表；followup 的写入前检查移到写 `pending` 之前；读取命令出错时也先收尾 |
| 4 | `canSend: false` 没有落实到运行锁 | 采纳：第 3.2 节检测在获取锁之前；不依赖降级；运行中出问题结束整轮；补锁的测试 |
| 5 | 通知类型、事项与恢复路径未闭合 | 采纳：第 3.7 节用 `alert`，键按日期与原因区分；恢复办法按原因与运行方式区分；`uncertain` 另行通知；`im.channel: none` 的处理 |
| 6 | `ready` 保留与终态阻断冲突 | 采纳：第 3.7 节第 2 条分流 |
| 7 | 重设时跳过的语义不清；进度单测不能证明配置合并 | 采纳：第 2.2 节写入规则表；配置合并列入验收 A1，不用进度单测代替 |
| 8 | 禁发时「不修改任何输入框」会阻断只筛选搜索 | 采纳：第 3.5 节改为不修改聊天输入框和其他会产生外发内容的控件；不在运行中切换输入方式 |
| 9 | 测试没覆盖跨步骤的安全行为；`preflight` 缺少超时契约 | 采纳：第 5.2 节脚本化行为抽查；第 3.1 节子进程超时与异常退出按未知处理 |

## 附录 C：第三轮红队审查意见的处理（2026-11-03）

| # | 意见 | 处理 |
|---|---|---|
| 1 | 只读标准输出的 `interpret` 无法识别超时和异常退出 | 采纳：第 3.1 节改为 `desktop.py run` 执行命令并记录完整进程结果；超时、信号终止、退出码不符合契约优先判 `unknown`；会改变页面的动作必须经 `run`；补「相同输出、不同进程结果」的测试 |
| 2 | 「正常流程」与放弃发送例外的适用范围太宽 | 采纳：第 3.6 节正常流程只认 `succeeded: true`；例外限定在调用 ④ 之前，或后续动作全部 `not_executed`；收尾表加「已有发送成功证据记 `sent`，优先于停止原因」；第 5.2 节改正不合法的用例并补边界用例 |

实现评审补充（2026-11-03）：第一次实现要求错误信封显式带空 `steps` 才判 `not_executed`，而真实工具的查找类错误不带 `steps`，导致常见的引用失效永远被判为可能已执行。按 1.0.2 契约改为本节现在的条件，并把 `ELEMENT_NOT_FOUND` 加入白名单。

# 执行层解耦：最小契约与实施方案

状态：2026-11-03 实施契约，已补充旧 IM 身份绑定门禁（第 3.9 节），供父会话分派实现。本人已批准方向：移除自带监听器 / launchd / 防睡眠；agent-desktop、飞书的具体实现移出核心分发，只留独立接入参考；保留 `ledger`、`workspace`、`notify` 的确定性业务状态脚本。CI、发布自动化、许可证不在范围内。

本文件只写要求、接口与判据，不含代码。基线为 main `1806d34`。完成后规则并入 `docs/design.md` 与 `shared/`，本文件只作追溯。

---

## 0. 边界

- 核心 = 技能正文、`shared/` 规则、`scripts/{ledger,workspace,notify,build}.py`。核心只维护业务数据和判定：事件、运行锁、暂停、inbox 事项、IM 本地状态。
- runtime 负责：操作工具、IM 收发渠道、调度与进程管理。runtime 的选择不能放宽授权、安全约束或成功判据；缺少能力时停止并报告。
- 核心数据脚本**不执行外部工具、不访问网络、不在后台等待或常驻**。核心不 import、不调用、不推荐任何具体工具。为兼容旧配置，允许出现 `lark`、`agent-desktop` 这类字面量和对应的兼容说明。
- `integrations/` 目录只放参考文档，不进核心构建（`build.py` 只打包 `skills/`、`shared/`、`scripts/`），核心文档也不链接它。

## 1. 保留、移除与迁移

| 内容 | 处置 |
|---|---|
| `ledger.py` | 不变 |
| `workspace.py` | 去掉 `import desktop` 与 `check --env`；放宽 browser / im 的配置校验（第 4 节）；步骤 id 不变 |
| `notify.py` | 保留 inbox、出站键、答复状态、指令、暂停与结果通知去重；新增第 3 节的接口；删除 `LarkAdapter`、`Caffeinate`、`wait`、`listen`、`service`、`launch_agent` 及进程组清理代码 |
| `desktop.py`、`tests/test_desktop.py` | 删除。`BrowserWorkspaceTest` 中与 `--env` 无关的用例移入 `test_workspace.py` |
| `shared/browser/agent-desktop.md`、`shared/browser/pi.md` | 分别移到 `integrations/agent-desktop/README.md`、`integrations/pi/README.md`，改写为参考（第 6 节）；其中通用的安全规则提升到 `shared/execution.md` |
| 飞书收发 | 新建 `integrations/lark/README.md`，只写飞书与接口命令的对应关系 |

## 2. 安全不变量

| # | 不变量 |
|---|---|
| I1 | 只有收到带真实 `messageId` 的 `sent` 回执，才算「已发送」。排队（`queued`）和交付 runtime（`handoff`）都不是已发送 |
| I2 | 送达不明（`unknown`）不自动重发。只有满足以下全部条件才允许重发：该条目排队时冻结的路由带有本人确认的幂等窗口、仍在窗口内、使用原路由与原幂等键 |
| I3 | 第一次排队时冻结正文、`replyTo`、收件人（ownerId）、channel 与 transport 摘要。配置改变后，旧条目只能被阻断并报告，不能发给新收件人或新通道 |
| I4 | 回执必须严格绑定「键 + 交付令牌」。未知的键、陈旧的令牌、与已有终态冲突的回执都被拒绝，不覆盖终态；重复提交同一回执是幂等的 |
| I5 | 只接受本人在已建立私聊中、晚于首次可读边界的消息。身份以 runtime 传入的平台元数据为准，不采信正文里的自称 |
| I6 | 被拒收的消息、时间异常的消息都不推进游标；整个批次先验证、后写入 |
| I7 | 「暂停」「恢复」「重试」的入账、执行与重放顺序和现在相同；`received → ready → consumed` 状态机及其约束不变 |
| I8 | 事件记录读不出时，不能阻塞「暂停」等指令，也不能凭推测给出唯一的事项映射 |
| I9 | ledger 运行锁与 `guard` 防止的是业务层的并发写，**不是执行器级的单实例互斥**。runtime 必须自己保证进程单实例和收尾；核心对状态不明的旧实例一律阻断 |
| I10 | `channel: none` 时，只写本地 inbox，`send` 返回已跳过，outbox 始终为空 |

## 3. notify 接口

### 3.1 配置

```json
"im": {"channel": "runtime", "ownerId": "<本人在该通道的发送者 ID>", "waitMinutes": 60,
       "transport": {"kind": "<runtime 自定>", "idempotencyWindowSeconds": 0}}
```

- `channel` 可取 `none`、`runtime`，以及兼容旧写法的 `lark`。`runtime` 必须有非空字符串 `ownerId`。`transport` 是可选对象，核心只原样回传给 runtime，只读其中的 `idempotencyWindowSeconds`：
  - 取值为 0–86400 的整数，缺省为 0；
  - 只有本人经 setup 确认后写入的值才算数；
  - runtime 不能在命令行或回执里临时声明这个值。
- 旧写法 `{"channel":"lark","userId":"ou_…","larkProfile":…}`：
  - 读取时得到 ownerId = `userId`，transport = `{"kind":"lark","profile":<larkProfile>}`，**幂等窗口按 0 处理**，不默认当前 runtime 支持飞书的 1 小时幂等；
  - 文件不改写；`workspace check` 给 warning，提示经 setup 显式迁移为 `runtime` 写法。
- **路由摘要（route）** = channel + ownerId + 规范化 transport 的哈希。在条目第一次排队时冻结，并写入该条目。

### 3.2 出站记录与命令

`ledger/im.jsonl` 中出站记录的状态：

| 状态 | 含义 | 能否被 `outbox --claim` 认领 |
|---|---|---|
| `queued` | 已排队，从未交付 | 能，作为第 1 次尝试 |
| `handoff` | 已交付 runtime，带令牌 `token`，还没有回执 | 不能；超过 10 分钟只在 status 里报告为 `stale` |
| `sent` | 回执确认已送达（终态） | 不能 |
| `failed` | 回执带明确的未投递证据，只针对这一次尝试 | 能，前提是尝试次数少于 3 次，且当前路由与冻结路由一致 |
| `unknown` | 回执报告送达不明，或已认定为不明 | 不能；只有满足 I2 时才能认领 |
| `blocked` | 当前路由与冻结路由不一致，或因其他阻断原因 | 不能；在 status 中报告 |

**旧记录的读取**：旧版只有 `pending` 的尝试、以及旧版的 `failed`（旧代码把超时也记成 failed，含义不明）一律按 `unknown` 处理，并标记为 `legacy`。这些旧尝试**不因为新配置声明了幂等窗口而改为可重发**。

| 命令 | 规则 |
|---|---|
| `send --kind … [--decision/--reply-to/--run/--key] [--text]` | 只做两件事：校验（`decision` / `todo` 要求 inbox 中有该事项）；生成键（`default_key` 与 done 的正文摘要键保持不变）。键不存在时写一条 `queued`，冻结正文、`replyTo` 和路由。**不交付、不认领。** 返回 `queued` / `already_queued` / `already_sent` / `blocked`；`none` 通道返回 `skipped`。退出码：0 表示已排队或已发送，1 表示被阻断（需要汇报），2 表示错误 |
| `test` | 与 `send` 相同，键为 `test:<随机>`，同样只排队 |
| `outbox [--claim] [--limit N]` | 不带 `--claim`：只读列出条目。带 `--claim`：在 `im_mutex` 内，为每个可认领条目写一条 `handoff`，带新的尝试序号和随机令牌；返回 `{key, attempt, token, idempotencyKey, text, replyTo, ownerId, transport}`。`idempotencyKey` 由 `idem_key(ws, key)` 生成，与旧版兼容 |
| `receipt --key K --token T --result sent\|failed\|unknown [--message-id M] [--chat-id C] [--create-time MS] [--error E]` | 令牌必须是该键最新一次 `handoff` 的令牌，且这次尝试还没有终态；不满足时退出码 2，不写入。已有相同结果时返回 `duplicate`；结果不同时报告冲突，不覆盖。三种结果的要求：<br>• `sent`：必须带非空的 `messageId` 和 `chatId`，`createTime` 可选（有限的毫秒整数）；缺少时间时，这条消息不参与首次可读边界。<br>• `failed`：只用于有明确未投递证据的情况，例如通道在接收前明确拒绝。<br>• `unknown`：超时、没有响应等任何其他情况。<br>由于令牌按次生成，新的尝试不可能沿用旧回执 |
| `outbox --stale` | 列出超时仍无回执的 `handoff`，附带它们的令牌。runtime 或本人确认情况后，用 `receipt --result unknown` 收口；核心不会自动把它们变为 `unknown` 或重新交付 |

**私聊的建立**：第一条非回复的 `sent` 回执确定私聊的 `chatId`。之后的 `sent` 回执如果带了不同的 `chatId`，照常记为 `sent`（消息确实发出去了），但在 status 中报告冲突，入站仍只接受已建立的私聊。

**回执消息**（「已收到」、指令结果）和提醒消息同样走排队、认领、回执这条路径，**不设自动过期**。

### 3.3 入站

| 命令 | 规则 |
|---|---|
| `poll` | 只读，不入账。返回 `{"channel","ownerId","chatId","sinceMs","transport","readable":bool,"outboxPending":N,"replies","paused","lastIncomplete":bool}`。私聊尚未建立，或还没有带时间的 `sent` 回执时，`readable` 为 false |
| `incoming --json <批次>`（或从 stdin 读入） | 批次格式：`{"since":<int>,"complete":<bool>,"messages":[…]}`。每条消息：`{"messageId":str,"chatId":str,"createTime":int(ms),"senderId":str,"senderType":str,"text":str,"parentId":str\|null,"deleted":bool}` |

`incoming` 的处理顺序：

1. **结构验证（整批）**：字段类型错误、`createTime` 是布尔值或非有限整数、`since` 不是整数或大于当前游标，任何一项不满足都整批拒收，退出码 2，不写入任何内容。`readable` 为 false 时也整批拒收。
2. **逐条过滤**：以下消息都拒收：已删除、`senderType` 不是 `user`、`senderId` 不等于 ownerId、`chatId` 不是已建立的私聊、`createTime` 早于首次可读边界、`createTime` 晚于当前时间 5 分钟以上、是我方已发出的消息。拒收的消息只按原因计数，不存原文，也不推进游标。
3. **去重**：按 `messageId` 去重，批次之间重叠也没有问题。
4. **入账与分类**：沿用现有逻辑，分为 command、test、ignored、received，并增加第 3.5 节的路由；按以下顺序写入：消息记录 → 游标（取被接受消息中最大的 `createTime`）→ 指令调度。回执消息排入 outbox。
5. **完整性**：`complete` 为 false 时，记录 `lastIncomplete`。技能必须汇报「IM 未读完」，不能写成没有新答复。

**分页完整性由 runtime 负责**：从 `sinceMs`（包含边界）起，按时间升序读取，交给 `incoming` 的必须是一段连续的前缀；核心无法核实，也不会宣称所有分页都已读过。

### 3.4 指令与答复状态（不变）

- 指令在 `poll_mutex` 内入账，按消息时间排序执行，每条先记 `pending` 再记 `applied`；暂停状态与已执行的指令 ID 在 `pause.json` 中一次原子写入；旧版指令不重放。
- 答复状态 `received → ready → consumed`，另有 `clarifying`、`ignored`、`command`、`test`；`reply-state` 的约束、`needsReconcile`、done 的版本键与旧键兼容，全部不变。

### 3.5 已决定但仍有结果不明尝试的事项（N12）

- **候选集合**：事项标题为「待决定」或「待处理」，或者该事项已决定、且 ledger 中有 `decision` 等于其 ID、当前状态为 `uncertain` 的尝试。由 ledger 推导，不解析 inbox 正文。本人核对后（追加 `skill: user` 的终态），该事项自动退出集合；其他已决定事项不进入候选，不会被重放。
- **回复解析**：不引用、不写 ID 的「已发出」，以及带独立序号的「01 已发出」，都能对应到这类事项。有多个候选时记为 `received`、提示为空，交给技能追问。
- **事件记录读不出时**：指令照常执行；不推导唯一映射；普通回复记为 `received`，`note` 写 `ledger_unreadable`，不记 ignored、不自动消费。status 报告对账问题。
- **done 结果通知**：该事项属于候选集合时，正文末尾自动加一句：「核对结果请引用本消息回复『已发出 / 未发出』，或回复『<序号> 已发出』」。这句话参与正文摘要键的计算。

### 3.6 唤起数据（`trigger`，只记数据、不执行）

保留这一组命令，是为了让「重试」和「每条答复最多自动唤起 2 次」仍有效。

- `trigger claim`：以下任一情况都返回 `blocked` 和原因：已全局暂停、运行锁有效、存在任何**没有 finish 记录的 started launch**（不看时间长短，旧监听器留下的也算）、该答复的自动唤起已达 2 次（回「重试」可重置）。否则写一条 started，返回 `{launchId, messageIds, prompt}`，提示词保留「由 IM 监听器唤起」标记以兼容现有技能。
- `trigger finish --launch-id X --status ok|failed|timeout|stopped --confirmed-stopped`：必须带 `--confirmed-stopped`，表示 runtime 或本人已确认该实例及其后代进程都已停止。`failed`、`timeout` 时按原文案把提醒排入 outbox。
- **不按超时自动放弃旧实例**；遇到状态不明的旧 started 时阻断，在 status 中给出处置说明。

### 3.7 status

去掉 listener 部分。新增：

- outbox 中各状态的计数；`stale` 的令牌；`blocked` 的原因；
- 冻结路由与当前路由不一致的条目；
- `chatId`、游标、`readable`、`lastIncomplete`、私聊冲突；
- `awaitingCheck`（决策 ID 与 attemptId，不含正文）；
- 未结束的 launch。

检测到旧监听器心跳文件 `ledger/listener.json` 时，给 warning「旧监听器已不再支持，请本人卸载」。status 只读，不停止任何服务。

### 3.8 移除的命令

`wait`、`listen`、`service` 从 CLI 中删除，调用时由参数解析报错。用户迁移说明写在 README（第 10 节）。

### 3.9 无身份旧 IM 历史的显式绑定

旧 `im.jsonl` 没有 owner / route，不能证明当前配置仍是原收件人。**默认不继承**旧 chat、cursor、答复；`poll` / `status` 报告 `legacy_route_unbound`，`replies`、`reply-state`、`trigger` 不得绕过绑定门禁处理旧 ready。记录保留供核对，不删除、不静默忽略，不把当前 owner 猜成原 owner。

新增纯本地命令 `migrate-legacy --owner-id ID --chat-id CID --confirmed-owner`：

- 仅旧 `lark` 配置可使用。本人先确认原身份，runtime 从可信通道元数据核对原私聊对方 ID 与应用身份；不能用正文自称或当前配置代替核对，能力不足则不迁移。确认旗标是可信调用者的断言，不是脚本对远端身份的证明。
- owner 必须精确匹配当前配置，chat 必须匹配唯一可确定的旧私聊；无确认、无旧私聊、歧义或不匹配时拒绝且不写入。
- 同一互斥锁内持久化一次性绑定：旧 chat、完整 route 指纹、owner / transport、确认时间。相同绑定重复提交幂等；已有绑定不得通过该命令换 owner / channel / transport 重新认领旧记录。并发绑定不能产生两个归属。
- 绑定后才沿用旧 chat / cursor / 未消费答复；旧 ready 标为待对账（`needsReconcile`），身份确认不等于业务完成，仍走既有 F1/F2 核对流程。旧指令无论 pending 或 applied 均不自动补执行／重放，pending 只报告待本人重新发送，避免陈旧恢复指令解除后来设置的安全暂停。不新增外发授权。
- 绑定门不挡全历史出站去重：旧 sent 继续参与 already_sent 与 done 版本键去重；旧 pending / failed 仍为不可自动重发的 unknown。无论是否绑定，旧未结束 launch 都继续阻断唤起。
- 新 `runtime` 配置不自动接管无身份旧历史。配置变化后的新路由通过新通知测试建立身份边界，不能继承原答复或游标。旧记录继续保留。

## 4. workspace 与 setup 的迁移

| 现有配置 | 处理 |
|---|---|
| `im.channel: lark` | 按第 3.1 节兼容读取，文件不改写。重新设置时，im 步骤提供「保留兼容」与「迁移为 runtime」两个选项，由本人确认后才改写 |
| `im.channel: runtime` | 校验 ownerId 与 transport；幂等窗口只在本人确认 runtime 的说明后才写入 |
| `profile.browser` | 字段保留。缺少时不再报 warning。`{"control":"agent"}` 表示由 runtime 决定。`{"control":"agent-desktop","input":…}` 仍按原结构校验，含义是「必须由能按该偏好执行的 runtime 执行，否则不操作平台、不回退」。其他非法值仍报 problem |
| `workspace check --env` | 删除 |
| setup 步骤 id `browser`、`im`、`listener` | id 不变，旧草稿可以继续。<br>• browser 步骤只询问执行偏好，不推荐具体工具。<br>• listener 步骤改名为「无人值守唤起」，只记录本人的决定，指向第 3.6 节与 runtime 调度契约，不写 `~/.config/job-wingman/listener.json`、不安装任何东西；旧草稿中 listener 的答案在汇总时列为「未应用」。<br>• im 步骤：IM 测试改为排队、认领、回执这条路径 |
| 授权 | 不变。IM 和 trigger 都不能新增授权或修改条件 |

## 5. 通用执行契约（`shared/execution.md`）

以下条款对所有执行方式都生效，替代原先只在 agent-desktop 下适用的条款：

1. **能力自检**：加锁之前确认以下能力。
   - 能读写工作区、能运行核心脚本：做不到时不写任何记录、不外发，只筛选并汇报。
   - 能通过不经 CDP、Playwright、无头浏览器或内部接口的界面能力，操作本人已登录的可见浏览器：读取、定位、点击、输入、截图核对。
   - 能给出动作结果的证据：做不到时用 `--no-send` 锁。
   - 工作区指定的执行偏好，当前 runtime 做不到时：不操作平台、不回退。
2. **完整读取**：只有完整读取了目标区域，才能下「没有消息」「输入框为空」「没有人工活动」这类否定判断。
3. **草稿保护**：核对会话之前不读写输入框；输入框里有未知内容时不清空，跳过并汇报；写入后读回必须与拟发文字完全一致；本轮禁发或 L1 时不改输入框；只清除能确认是本次写入的草稿。
4. **guard**：每一个可能外发的动作之前都执行 `guard`，通过后立即执行；出现等待或工具超时后，先重新读页面，再 `guard`。
5. **证据三分类**：明确未执行 / 可能已执行 / 未知。只有工具给出的结构化未投递证据才算明确未执行；给不出时，一律按可能已执行处理。
6. **收尾**：任何停止分支之前，先给本轮所有尝试收尾（按原收尾表）。结果不明的尝试按 `notify.md`「结果不明事项」写入 inbox 并去重。之后按「释放与对账契约」释放运行锁。
7. **通知**：先写 inbox，再经 outbox 发出；同一个键只发一次；送达不明不重发（I2）。
8. **无人值守唤起**：runtime 负责单实例、超时、进程树清理和防睡眠，并遵守第 3.6 节；做不到时不要设置无人值守唤起。

## 6. `integrations/` 参考文档

- 每份文档开头注明：参考，不随核心分发，不是必须使用的方案，不能放宽核心规则。
- 内容只写「该工具或通道怎样对应到核心接口与契约」，例如：飞书的发送 / 读取接口与 `receipt`、`incoming` 字段的对应；agent-desktop 的返回如何映射到证据三分类。
- 不照搬旧文档中「必须执行」的命令与安装步骤；不写会停止或安装服务的指令。

## 7. 文件任务与顺序（每阶段一个写者）

1. **N（notify）**：`scripts/notify.py`、`tests/test_notify.py`、`shared/notify.md`。实现第 3 节（含第 3.5 节、第 3.6 节）。
2. **W（workspace）**：`scripts/workspace.py`、`tests/test_workspace.py`、`shared/workspace.md`。实现第 4 节。
3. **X（移除与迁移文档）**：删除 `scripts/desktop.py`、`tests/test_desktop.py`；新建 `integrations/{agent-desktop,pi,lark}/README.md`；删除 `shared/browser/agent-desktop.md`、`shared/browser/pi.md`。依赖 W 已完成。
4. **C（执行契约）**：新建 `shared/execution.md`；精简 `shared/browser/README.md`；新建 `tests/test_rules.py`。
5. **S（技能）**：`skills/{apply,followup,setup}/SKILL.md` 与两份 `platforms/boss.md`。
   - 改为引用 `execution.md`，删除「使用 agent-desktop 时」这类限定；
   - followup 的第 2.0、3、8 节改为「同步 IM」：`poll` → runtime 读取 → `incoming` → `outbox --claim` → 投递 → `receipt`；第 8 节的等待由 runtime 循环执行；
   - setup 的第 6.1 节和第 10 节按第 4 节改写；
   - 所有对 `send` 的说明改为「已排队」，不写成「已发送」。
6. **D（文档）**：README（用户迁移说明，见第 10 节）、`docs/design.md`、`docs/status.md`。必须在父会话现有修改的基础上续写。
7. **V（验证）**：全量回归；用 `--out` 构建到临时目录；规则文本检查（第 8 节 R）；status 只记「已实现、单测通过」。

## 8. 测试

**新增或改写**（`FakeIM` 改为测试辅助函数，通过 `outbox`、`receipt`、`incoming` 驱动）：

| 编号 | 内容 |
|---|---|
| N1 | `send` 和 `test` 只排队：不交付、不发送；同一个键返回 `already_queued` / `already_sent`；`decision` / `todo` 要求 inbox 中有该事项；`none` 通道返回跳过 |
| N2 | 认领：同一个条目只交付一次；`handoff` 不会被再次认领；超时后只报告为 `stale`，不自动变为 `unknown`，不重新交付 |
| N3 | 回执：未知的键或令牌、陈旧令牌、冲突的回执都被拒绝，不覆盖已有状态；相同回执重复提交是幂等的；`sent` 缺少 messageId 或 chatId 时被拒绝；缺少 createTime 时不推进首次可读边界；第一条非回复的 `sent` 建立私聊；私聊冲突时报告 |
| N4 | 重发：`failed` 后重新认领，正文和路由沿用首次冻结的版本，最多 3 次；`unknown` 只在冻结的窗口大于 0 且仍在窗口内时可重新认领；旧 `pending` 与旧 `failed` 一律不重发；配置改变后旧条目变为 `blocked` |
| N5 | 入站拒收：未建立私聊、无首次可读边界、发送者不是本人、`senderType` 不是 user、其他私聊、已删除、早于边界、时间超前，这些都被拒收；只计数、不存原文、不推进游标 |
| N6 | 入站结构：字段类型错误、布尔或非整数时间、`since` 大于游标，都整批拒收且没有任何写入；批次重叠、批次内乱序、按 ID 去重；`complete: false` 时 `lastIncomplete` 为真 |
| N7 | 分类不变：原有测试回复、序号、ignored、多个事项、未知序号相关的用例，全部改为经 `incoming` 驱动 |
| N8 | 指令：两个进程并发调用 `incoming` 时，「恢复」「暂停」按消息时间生效；已入账未执行的指令能补执行；「恢复」重放不解除后来的安全暂停；旧版指令不重放 |
| N9 | done：版本键、旧 `done:<ID>` 键兼容、改品牌前后去重、同一个键沿用首次正文，这组用例原样保留，改为经 outbox 驱动 |
| N10 | trigger：暂停、运行锁、未 finish 的 launch（含旧监听器留下的、任何时长）、达到 2 次上限时都阻断；「重试」重放不重置次数；没有 `--confirmed-stopped` 的 finish 被拒绝 |
| N11 | 兼容：旧 `lark` 配置按第 3.1 节读取、幂等窗口按 0；旧 `im.jsonl` 保留，按第 3.9 节显式绑定后才沿用 chat / cursor / ready。测试首次启动前已换 owner 仍默认阻断、无确认／错身份／歧义 chat 无写入、有效绑定及重复幂等、并发单归属、配置变化不能重绑、reply-state / trigger 无旁路、旧 unknown 不变 |
| N12 | 第 3.5 节全部情形：单个候选、带序号、多个候选、核对后退出集合、已完成事项不进入候选、事件记录损坏时不阻塞指令且不臆造映射、done 正文带回复提示且重发时键不变 |
| W | lark 与 runtime 两种写法的体检；runtime 缺少 ownerId 时报 problem；缺少 browser 不报 warning；agent-desktop 配置仍按结构校验；`--env` 不存在；旧草稿中的 listener、browser、im 答案保留，继续 setup 能完成，且不写 `listener.json` |
| R | 核心脚本不 import `integrations/`；核心中不出现工具调用：`lark-cli `、`agent-desktop ` 命令、`desktop.py`、`launchctl`、`caffeinate`、`npm install`、`subprocess`（仅 `build.py` 调用 git 例外）；允许兼容用的字面量 `"lark"`、`"agent-desktop"` 及说明文字；核心文档不链接 `integrations/`；`execution.md` 覆盖第 5 节全部条款，锚点有效 |

**移除的测试及原因**：

- `test_notify.py` 中的监听器与进程测试：`listener_*` 系列、`service_dry_run`、`stop_child`、`child_check_seconds`。原因：自带执行器已移除；其中的判定改由 N10 覆盖，进程收尾转为 runtime 契约的验收项。
- `test_wait_returns_on_reply_or_timeout`：`wait` 命令已删除。
- `test_lark_create_time_formats`、`test_lark_message_text_extraction`，以及 3 项 `lark_profile` 测试：飞书接口的调用与解析移出核心，由 runtime 负责。
- `test_unconfirmed_send_retries_with_same_idempotency_key_then_gives_up`：改写为 N2–N4。
- `test_retry_replay_does_not_reset_launch_count`：改写为 N10。
- `test_desktop.py` 中的 `DesktopTest`：`desktop.py` 已不随包分发。`--env` 相关用例：该选项已删除。`DesktopRulesTest`：由 R 取代。

### N 阶段首次复审后的补充约束

`03bf70a` 首次实现尚未获准合入，59 项 notify 测试通过不替代以下缺陷回归：

- **编辑消息不阻断暂停**：已入账 ID 的变化只拒收该条、保留第一次内容并计数，不存编辑正文、不推进游标；同批次尚未入账的冲突 ID 全部拒收，不任选授权内容。其余合法新消息继续处理；结构或 since 错误仍整批拒绝。
- **冻结幂等键**：首次 handoff 持久化 idempotencyKey，工作区搬迁后重试仍沿用；已有不明尝试缺少该值时不得猜测重试。
- **回复目标隔离**：排队与认领均核对 replyTo 属于当前路由、已建立私聊的合法入站或已确认出站消息；未知、未绑定、其他身份／私聊目标不能投递。
- **重试截止时间**：窗口内 unknown 重试必须给 runtime 不可延长的 deliveryDeadlineMs（首次 handoff + 冻结窗口 − 300 秒），不足余量则阻断。runtime 发前确认仍满足原保证且未过期，否则不投递；迟到 sent 回执保留事实并标出逾期。不将初次未发与明确未投递混同 unknown 重试。
- **旧 ready 显式对账**：确认身份后仍不能自动唤起旧 ready；必须完成 F1/F2 并显式记录对账确认，才能进入相应后续状态。计数、trigger 与 reply-state 都不得绕过；也不能永久困住已完成对账的事项。
- **旧指令只汇总**：applied 不误报未执行、不发提醒；旧 pending 只报告待本人重发，不逐条排历史通知。
- **配置一致**：notify 与 workspace 都要求 legacy userId 为 `ou_` 加非空后缀；显式 im:null 都拒绝。合成测试使用同样有效的格式。
- **未绑定历史可见性**：poll / status / replies 返回仅含计数的 legacyUnbound（旧答复状态、pending / applied 指令、unknown 出站及 bindable），不含正文或消息 ID；未绑定旧待办使 trigger 返回 legacy_route_unbound 而非 no_replies。绑定后旧 ready 仍进入 needsReconcile，技能不得把待迁移说成没有答复。

第二次复审确认上述 8 项已修复；`b33d29d` 仍待修正两项状态报告缺陷后才能合入：

- **其他路由待办不隐身**：poll / status / replies 统一给仅含 received / ready / clarifying 数量的 otherRoutes，包含已绑定旧历史但不重复计未绑定历史，不暴露正文或 ID。trigger 对此返回 route_changed_pending；不自动接管。恢复原配置后才按原事项对账／处理；放弃也须在原路由下显式对账消费。
- **新协议不误报旧历史**：legacy 判定按出站 key 的首条记录／归约结果，不将缺少重复身份字段的 handoff / receipt 增量误当旧记录；完整新协议周期不得出现旧历史绑定警告，混合历史仍正确计数。
- **旧引用的保守恢复说明**：缺消息级身份／chat 元数据的旧引用继续阻断；告知本人重新发送带事项 ID 的新回复或明确在终端处理，不猜引用、不因 blocked 标成已追问。必要的独立提醒只请求重发、用唯一键且不带旧正文。排队追问不等于已送达。

## 9. 验收边界

- 单测通过只能记为「已实现、单测通过」。移除旧实现，**不等于**任何相关验收通过：P6.7、生产监听器切换、launchd 权限、O3/O4 只能记为「随执行器移除，不适用」，不能写成通过。
- 迁移后需要重做合成行为抽查，至少覆盖：同步 IM（含未读完）、送达不明不重发、N12 的三种答复、一个没有桌面能力的 runtime 停止并报告、setup 旧草稿。可以复用 release-test 已验证过的工具门禁、隔离方式与 hash 校验；1806d34 上的抽查结论不能外推到迁移之后。
- setup 三种模式、旧会话实际页面判读、真实平台外发等原本只能由本人发起的验收，状态不变。

## 10. 用户迁移说明要点（写入 README，核心不执行）

- 已删除的命令：`notify wait`、`notify listen`、`notify service`、`workspace check --env`、`scripts/desktop.py`。
- 已安装旧监听器（`com.job-wingman.listener`）的，由本人自行停用并移除对应的 plist；新版不会读取或删除 `~/.config/job-wingman/listener.json`。在新版中，旧监听器会因命令已删除而无法唤起 Agent。
- `im.channel: lark` 仍可读取，但需要由当前 runtime 按 `integrations/lark` 参考接入，并经 setup 显式迁移；迁移前的送达不明记录一律不会自动重发。
- 旧 IM 记录缺少身份字段；首次使用新版前须按第 3.9 节显式核对绑定，才能沿用旧私聊、游标及答复。未完成时只报告待核对，不自动处理旧 ready。
- 旧的独立技能目录要先移出 runtime 的扫描目录，再重新安装（沿用 build 的现有提示）。

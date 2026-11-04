# agent-desktop 接入参考

**仅参考，不随核心分发，不是必须使用的方案。** 它是 runtime 的可选实现，不能放宽 [通用执行契约](../../shared/execution.md)、[安全边界](../../shared/safety.md)、[台账](../../shared/ledger.md) 或 [通知](../../shared/notify.md)。本文不提供执行包装器、安装或服务管理步骤，也不宣称当前版本兼容或真实验收通过。

## 适用与能力映射

工作区已保存的偏好 `control: agent-desktop` 和 `input: background / foreground` 仍须遵守；runtime 做不到就不操作平台、不回退。后台语义写入不抢焦点，但本人不能同时使用目标 Chrome 窗口；前台输入独占键盘鼠标。整轮不换输入方式或控制工具重发。

本参考的适用基线为 agent-desktop 1.0.2、响应格式 2.5。接入时须按实际版本核对字段、权限与行为；本表不是所有版本或无人值守环境的兼容承诺。

| 所需能力 | 参考版本的能力与核对点 |
|---|---|
| 加锁前只读环境检查 | 应用、窗口、权限清单；权限须 granted，Chrome 运行且有可读取窗口。参考结构为应用清单 `data.apps`、窗口清单 `data` 数组、权限 `data.accessibility.state`；不读取平台页面。格式未确认只能禁发，无法读取不操作平台 |
| 目标固定 | 平台标题前缀、页面 / 账号核对，再固定窗口 ID；多个候选不猜。每次动作都绑定已核对窗口 |
| 区域读取 | snapshot 骨架后展开目标 root；find / get 不能以零匹配或空值证明区域为空。引用只在所属快照内有效 |
| 精确输入 | 聊天输入框的参考 native id 为 `chat-input`；后台 set-value，禁止后台 type（参考版本的 Chrome 中无效且返回不可重试）。前台输入须满足所选偏好 |
| 输入后验证 | 完整读回与拟发文字一致，再按平台结果确认；contenteditable 可静默失败，读回不等于页面接收。空框发送按钮也可能 enabled，切换会话后可能残留草稿 |
| 文字 / 卡片动作 | 限定核对过的会话和具体卡片；写入、发送拆开，不合并 batch。每个可能外发动作由核心 guard 放行后立即执行 |

只使用原生窗口能力；禁用 `launch --cdp` 及其他 CDP 功能，不开 trace、不用 `--screenshot` 调试导出。所需截图仅用于当步核对，隐私与留存按核心契约。

职位搜索框后台写入尚未实测，验证前不要使用；联系人搜索框及其他输入框也未验证，不以聊天框测试替代。搜索须确认实际条件和结果变化，不能只看读回值。列表链接读取和复制链接不可用时，`jobId` / `jobUrl` 读不到记 null，不猜；独立职位页可见地址栏可读。列表薪资乱码尚待复核，以详情为准。锁屏窗口不可访问；单个窗口错误不能确诊锁屏。

## 返回如何对应证据三分类

接入者保留完整的进程证据与有界输出，在 runtime 中验证；核心不代为执行或解析工具。执行状态、环境可访问性、观察完整性、业务发送结果是四个独立结论。

### 进程与信封

- 参考版本退出码：0 成功、1 结构化错误、2 参数解析错误（仍有 `INVALID_ARGS` 信封）。退出码与 ok 矛盾、其他退出码或格式版本未核对，按 unknown。
- 信封有字符串 `version`、`command` 和布尔 `ok`。成功有 `data`、没有 error；错误有 `error.code`、`error.message`、`error.disposition`。成功动作的 disposition 在 `data`，不在顶层。
- 请求绑定：响应 command 等于本次请求的子命令，data.action 若存在也相同。参考版本允许的唯一例外是退出码 2、ok false、INVALID_ARGS、command unknown 的参数解析拒绝；不能放宽其他不匹配。
- 严格 UTF-8 解码 stdout；stderr 仅作诊断。JSON 重复键、非法常量、缺字段、错误类型、矛盾或显式 null 不修复成证据。details / after_action 若存在应为对象；steps 若存在须为对象数组，不能为 null。
- stdout + stderr 合计限制为 8 MiB 原始字节，阈值以内才可能采用；超量或损坏前缀不采用。接入 runtime 须提供有界读取和超时，超时、信号终止、读取异常或清理未确认时，即使有可解析 JSON 也不采用。

### disposition 与嵌套步骤

参考版本的 delivery 为 `unknown`、`not_delivered`、`delivery_uncertain`、`delivered_unverified`、`delivered_verified`；retry 为 `unknown`、`safe`、`unsafe`。合法配对：not_delivered / safe、unknown / unknown，其余 delivery 配 unsafe；非法类型或矛盾判 unknown。

| 核心证据类别 | 参考映射（接入时须全部验证） |
|---|---|
| 明确未执行 `not_executed` | 进程正常、信封合法、版本与请求绑定正确，ok false，not_delivered / safe，错误码在下列已核对白名单，报告任何位置都没有非空 steps |
| 可能已执行 `maybe_executed` | 合法报告但不满足明确未执行，如已投递、unsafe 或任意已执行步骤；不因此重试，不等于业务成功 |
| 未知 `unknown` | 进程或输出异常、契约不明、请求不匹配、结构不合法；成功动作的 delivery 为 unknown / delivery_uncertain 也属于未知 |

参考版本已核对白名单：`STALE_REF`、`AMBIGUOUS_TARGET`、`SNAPSHOT_NOT_FOUND`、`INVALID_ARGS`、`WINDOW_NOT_FOUND`、`APP_NOT_FOUND`、`ACTION_NOT_SUPPORTED`、`ELEMENT_NOT_FOUND`。其他错误即使声称 safe，也不足以证明未执行。

参考版本允许 steps 缺失，视为没有已执行步骤；但任何层级（含 `error.details.steps`、`error.details.after_action`）的非空 steps 都推翻未执行结论，不按步骤 outcome 猜测。after_action 可不带 steps，但 disposition 必须合法且与外层 error 一致。外层空数组不能掩盖内层执行记录。

动作正常成功还要求 ok true 且 delivery 为 delivered_unverified / delivered_verified；not_delivered 不作为动作成功前提，只有 maybe_executed 也不够。观察命令可没有 disposition。工具正常成功仍不等于消息 sent，须满足核心的正常流程、平台送达和收尾判据。

### 观察与环境独立判定

snapshot / find 的 `data.complete` 必须明确为 true，任意层级都无 truncated / subtree_truncated 为 true 或非法类型，才支持该区域完整。缺标记不等于完整；无关区域无须展开。读取不完整时不能断言没有消息、草稿或人工活动。

参考版本的环境 unavailable 线索为 `details.kind: window_without_accessibility_element` 或 APP_NOT_FOUND / WINDOW_NOT_FOUND；它不证明未执行（窗口错误可能发生于写入之后）。返回无法采用时环境未知。读命令未知也先按核心收尾全部 attempt。

## runtime 收尾与接入核验

runtime 每次调用须隔离并管理其进程树；超时、输出超量、异常或主进程退出后残留，都先温和停止，再有界升级清理并确认全部停止。等待时长或信号发出不是清理成功证据。权限拒绝（含 EPERM）、僵尸组或持续探测未知均不能当作进程消失。清理不能影响无关进程；无法确认就 unknown、阻断新运行，不采纳该调用输出。

接入验证使用临时合成工作区与假工具，至少覆盖：嵌套步骤 / 空值 / 请求不匹配、截断、损坏与超量输出、超时后进程残留、权限拒绝、已有成功证据保留、未知草稿与写后不发送的清理。真实平台验证只能由本人发起；无权通过换工具或清草稿消除 uncertain。

字段依据为参考版本的 `docs/json-output.md` 与工具内置说明；接入时仍须核对实际版本。

# agent-desktop：原生窗口操作

选用本工具时读本文，同时遵守 [通用执行契约](../execution.md)、[安全边界](../safety.md)、[台账](../ledger.md) 和平台判据。本文随整套技能分发，但不是浏览器执行器，也不证明真实账号或所有工具版本兼容。

## 适用与安装来源

本规范仅适用 **macOS**。加锁前只读检查：工具主版本为 **1**、响应格式主版本为 **2**、辅助功能权限已授予（granted）、Chrome 运行且有可读取窗口。参考检查入口为 `agent-desktop --version`、权限、应用与窗口清单；只检查环境，不读取招聘平台页面。同主版本只是必要条件，命令或字段未核对不能外发。

证据基线为 **agent-desktop 1.0.2 / JSON 2.5**。本地包元数据为 1.1.0（未运行其二进制），repository/homepage 指向 [官方仓库](https://github.com/vercel-labs/agent-desktop)。于 **2026-11-04** 只读核对其 [README 安装部分](https://github.com/vercel-labs/agent-desktop#install)、[安装说明](https://github.com/vercel-labs/agent-desktop/blob/main/docs/install.md) 与 [JSON 契约](https://github.com/vercel-labs/agent-desktop/blob/main/docs/json-output.md)。官方当前文档已展示 2.6 和多系统支持；这些新字段、命令和行为**没有因此成为本项目已验证能力**，不能把替身参数或当前官网语法当作 1.0.2 的完整接口。

缺工具时只向本人提供官方安装链接，由本人按系统架构准备 CLI 及同版 helper、授予 macOS 权限；Agent 不自动安装、升级、请求权限或部署服务。所需屏幕录制等额外权限按实际只读检查解释，不把辅助功能授权等同于截图能力。

不满足适用条件时，按 [工具选择顺序](../execution.md#1-加锁前能力自检) 判断其他原生窗口、非 CDP 工具或停止；尊重既定偏好，不静默改配置。选择发生在加锁前，不授权运行中换工具重发。窗口不可访问、动作未知或已出现安全信号时，先按核心停止规则收尾，而不是试另一个工具。

## 原生浏览器与输入

只操作本人日常可见、已登录的 Chrome，经辅助功能树或桌面 UI；不读取密码、Cookie、令牌，不模拟人工、不规避检测。**禁止 `launch --cdp` 及所有 CDP 功能**，不开 trace，不做调试截图导出（含 `--screenshot` 调试参数）；当步所需截图只用于核对，按核心隐私规则处理，不保存完整聊天或输出到汇报。

1. 按平台标题前缀找到窗口，核对网站、账号、公司/招聘者与会话，再固定窗口身份；多候选不猜。动作绑定已核对窗口、快照和目标。页面、导航或会话变化后重新观察，不复用旧 ref 或坐标。
2. 写入前完整读取输入框，未知草稿和其他会话残留不清空；本轮禁发、L1 起草或无授权时，不碰外发内容输入框。正文必须是最终拟发原文。
3. **原生文本框**确认后可后台 `set-value`；BOSS 聊天框已核对的 native id 为 `chat-input`，不能靠别的网站相同 ID 或可写属性猜类型。**contenteditable 禁止 `set-value`**：可能只改显示、不触发输入事件，甚至发出旧内容；类型不明也不写。
4. **禁止所有后台 type**。contenteditable 只能前台 `type --headed`，并且本人允许、`profile.browser.headedInput` 为布尔 true 且能通过当前套件配置校验；缺失、不合法或未允许时不输入并报告，不手工加字段绕过 setup。其他前台输入同样需要授权，会独占键盘鼠标；后台写入不抢焦点也不代表本人能同时使用目标窗口。
5. 输入后**完整读回**，与拟发原文逐字一致才可继续；type 可能追加而不是覆盖，不覆盖未知残留。读回一致只证明观察到的值，不证明页面接收，更不是送达。写入与发送拆开，不合成 batch；每个可能外发动作均由 `prepare` 留 pending，再逐动作 guard，通过后立即执行。
6. 工具正常返回、输入框变空、按钮 enabled 或聊天页打开都不证明 sent；须另看身份一致会话中的平台送达证据。正常流程、结果不明与草稿收尾按 [执行契约](../execution.md#5-外发前-guard-与证据三分类)。

**锁屏**时先请本人解锁，不重试绕过，不用其他控制通道试发。单个窗口错误不能确诊锁屏：可报告「可能锁屏、窗口不可见或 Chrome 未运行」及已见证据。恢复后由本人重新发起，先对账、核对环境和目标；不因解锁自动重放旧动作。

## 参考响应与请求绑定

以下仅为已有参考/替身的 **2.5** 动作信封示例，不是完整命令 schema 或现场响应：

```json
{"version":"2.5","command":"click","ok":true,"data":{"action":"click","disposition":{"delivery":"delivered_unverified","retry":"unsafe"}}}
```

基线 `--version` 响应的 command 为 version，`data.version` 是工具版本（1.0.2），外层 version 是格式版本（2.5），须分别校验。参考环境字段：应用为 `data.apps`，窗口为 `data` 数组，辅助功能权限为 `data.accessibility.state`。未确认字段不猜默认成功；替身只覆盖显式子集，参数须按所装版本核对。

- 退出码 0 为成功，1 为结构化错误，2 为参数解析错误（仍有 `INVALID_ARGS` 信封）。其他退出码、信号终止或退出码与 ok 矛盾均为 unknown。
- 信封 version、command 为字符串，ok 为布尔。成功必须有有效 data、没有 error；错误必须有 error.code、error.message、error.disposition。动作的成功 disposition 位于 data，不在顶层。显式 null、缺字段、错误类型或矛盾不修复成证据。
- 观察命令可无 disposition，不据此推断动作投递。
- **请求绑定**：响应 command 必须等于本次子命令，data.action 若存在也必须一致。唯一已核对例外是退出码 2、ok:false、INVALID_ARGS、command:unknown 的参数解析拒绝；不能推广到其他不匹配，也不执行远端建议中的命令字符串。
- 严格 UTF-8 解码 stdout，拒绝 JSON 重复键、NaN 等非法常量。stderr 仅诊断；stdout＋stderr 合计最多 **8 MiB 原始字节**，超量、损坏前缀或读取异常不采用。details / after_action 若存在必须为对象，steps 若存在必须为对象数组，不能为 null。
- **超时或输出异常一律 unknown**，即使有可解析的成功 JSON。runtime 负责有界调用和进程树清理；主进程退出、已发信号或 EPERM 等权限拒绝不证明后代已停，清理未确认不开始新运行。完整收尾规则只见 [通用执行契约](../execution.md#8-runtime-调度与进程收尾)。

## 三类证据与嵌套报告

执行状态、环境可访问性、观察完整性、业务送达是独立结论。不能用窗口不可访问证明先前未写入，不能用外层未投递掩盖内层步骤。

参考 delivery 值为 unknown、not_delivered、delivery_uncertain、delivered_unverified、delivered_verified；retry 为 unknown、safe、unsafe。合法配对仅 not_delivered/safe、unknown/unknown，其余 delivery 配 unsafe；类型或配对非法按 unknown。

| 类别 | 必要证据与处理 |
|---|---|
| 明确未执行 `not_executed` | 进程正常，信封、版本及请求绑定合法，ok:false，not_delivered/safe，错误码在下列白名单，任意层级均无非空 steps。只有环境正常时才重新观察、定位、guard，同一尝试最多重做该动作一次 |
| 可能已执行 `maybe_executed` | 合法报告但不满足明确未执行，例如已投递、unsafe 或已有步骤。不重试，观察业务结果；无法证实则按核心记 uncertain |
| 未知 `unknown` | 进程/输出异常、契约不明、绑定错误、结构不合法；成功动作的 delivery 为 unknown / delivery_uncertain 也属未知。不重发，按核心停止范围收尾 |

已核对白名单仅：`STALE_REF`、`AMBIGUOUS_TARGET`、`SNAPSHOT_NOT_FOUND`、`INVALID_ARGS`、`WINDOW_NOT_FOUND`、`APP_NOT_FOUND`、`ACTION_NOT_SUPPORTED`、`ELEMENT_NOT_FOUND`。**仅 retry:safe 不够**；其他错误、未核对版本差异或白名单之外的码均不能证明未执行，不扩大白名单以追随 latest。

参考版本允许 **steps 缺失**，视为没有执行步骤；但任何层级（含 `error.details.steps`、`error.details.after_action`）出现**非空 steps**就推翻未执行，不看 outcome 名称猜没有执行。外层空数组不掩盖内层记录。after_action 可无 steps，但 disposition 必须合法且与外层 error 一致；未知嵌套结构不猜。

动作正常成功还要求 ok:true、delivery 为 delivered_unverified / delivered_verified；**not_delivered 不作为动作成功前提**，只有 maybe_executed 也不足以继续正常业务流程。delivered_verified 表示工具所核验的后置条件，不等于 HR 已收到消息；业务 sent 仍须独立证据。

## 观察完整性与已知限制

- snapshot / find 的 `data.complete` 必须明确为 true，且任意层级的 truncated / subtree_truncated 均无 true 或非法类型，才能支持目标区域完整。缺标记不等于完整；骨架、零匹配、单个 get 空值不能证明没有消息、草稿或人工活动。按目标 root 继续展开，无关区域不必完整。
- 环境 unavailable 的参考线索为 `details.kind: window_without_accessibility_element` 或 APP_NOT_FOUND / WINDOW_NOT_FOUND；它不证明未执行，窗口错误可能发生于写入后。输出无法采用时环境也未知，先给本 run 全部未终结 attempt 收尾。
- 复制职位链接曾返回 STALE_REF；jobId 只从详情页可见地址栏读取并按平台规则提取。列表或复制读不到 jobUrl/jobId 记 null，不绕用接口或猜链接。
- 列表薪资乱码以详情页为准。职位搜索框后台写入尚未验证，联系人搜索框等也未验证；不得把聊天框经验直接套用，使用前读回并确认实际查询条件及结果变化，无法验证则停止该操作。

以上约束用合成替身回归，不代表真实浏览器、本机已安装版本或无人值守能力已验证。真实平台核验仅由本人发起；不能通过换工具或清草稿消除 uncertain。

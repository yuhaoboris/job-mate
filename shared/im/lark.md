# 飞书通知与同步

飞书随整套技能分发。先写 inbox、`notify send` 排队，再运行 `im_lark.py sync`；通知不是批准。不需要时选 `im.channel: none`，不换通道。

## 前置条件

- 选择飞书时，Agent 按[依赖准备](../dependencies.md#3-飞书依赖仅选择飞书时)安装 lark-cli **1.0.96**，不逐项请求同意；复用或隔离安装，不覆盖其他版本。
- 由本人完成机器人应用授权与 IM 收发权限，不靠用户登录修复机器人权限；不部署服务。
- `profile.im` 使用 `channel: lark`、本人确认的 `ownerId`、准确的 `larkProfile`；`waitMinutes` 为 0–60，缺省 60，0 关闭监听。配置变更按 [配置事务](../config-transaction.md) 提交，不直接改真实工作区。

## 本人身份与配置档

open_id 与应用有关；在同一配置档下核验本人。经本人允许执行以下只读命令，参数按独立 argv 传递：

```text
lark-cli --profile <配置档> profile list
lark-cli --profile <配置档> auth status --json --verify
```

实测 `profile list` 是**顶层数组**，按 `name` 精确选择，不凭 active 猜本轮应用。auth 是**顶层诊断对象**，没有 IM 的 ok/data 信封：

- `identities.bot.available/verified` 是机器人验证证据；doctor 只要求机器人可用，不要求用户登录。顶层 identity/verified 可能属于 user，不能代替 bot 证据。
- 若 `identities.user.verified` 为 true，展示 userName/openId 请本人确认后才写 ownerId。**bot.openId 不是 ownerId**，不凭 ID 外形猜身份。
- 无可核验的本人 user 身份时，可在已有、经本人允许的用户授权下按本人提供的姓名或邮箱查询：

```text
lark-cli --profile <配置档> contact +get-user --as user --format json
lark-cli --profile <配置档> contact +search-user --query <本人提供的姓名或邮箱> --as user --format json
```

第一条取当前授权用户，仍须本人确认；第二条展示姓名、可见邮箱等候选。多条不擅选，缺授权或可靠结果就停下来请本人核验。

**证据边界**：profile/auth 有实测响应；联系人命令只按 1.0.96 配套说明核对语法，**contact 响应未独立实测**。执行前核对本机帮助；字段路径、身份或应用不明不写 ownerId。引用回复也未独立实测，不称全部话题类型兼容。

## 体检与运行

先解析技能目录和工作区绝对路径；Windows 的 `python3` 换为 `py -3`：

```text
python3 <技能目录>/scripts/im_lark.py --workspace <工作区> doctor
python3 <技能目录>/scripts/im_lark.py --workspace <工作区> sync
python3 <技能目录>/scripts/im_lark.py --workspace <工作区> sync --send-only
python3 <技能目录>/scripts/im_lark.py --workspace <工作区> sync --fetch-only
```

- doctor 只读检查入口、版本、配置档及机器人，输出 `ok/checks`；不证明本人身份、IM 权限齐全或收发成功。退出码：0 通过、1 不通过、2 本地配置/数据错误。
- sync 复用 notify 状态机：投递、读取、投递新确认。send-only 不读远端；fetch-only 不认领/发送，确认保留 queued。
- **所有 sync 模式都是 write 准入**，恢复锁或版本不兼容时拒绝；doctor 为 read。none 输出 `skipped: channel_none`，不定位或执行工具，不假装收到答复。
- 每轮开始（入口对账后）、每处理完一个会话、收尾各同步一次。监听见[等待本人回复](../notify.md#等待本人回复)，本轮不等答复。

sync 输出 incoming、sent/failed/unknown 数量、incomplete、errors。退出码 0 **不等于送达或读完**；结合 `notify status` 查队列、路由冲突和拒收。errors 仅固定诊断码，最多 500 字符，不含远端错误正文、消息正文或凭据。

## 送达与时间

每条冻结原文、引用、ownerId、larkProfile 和幂等键；显式指定配置档。改配置不转投，未知引用不回复。

- 必须有真实 message_id/chat_id 才能记 sent。1.0.96 shortcut 的日期字符串没有时区，mget/list 还丢失秒，**不能当毫秒或猜时区补零**。
- 发送成功后若没有可信 epoch 毫秒，仅原生 GET 按消息 ID 补查一次，严格核对 ID/chat 和十进制毫秒字符串。补查超时、缺字段或失配仍记 **sent、不带时间**，不改 unknown、不重发；该条不能建立首次可读起点。
- 实际发送超时、坏信封、缺有效消息/会话 ID 记 unknown，**不重发**。只有实测拒绝码 99992351 可记 failed；最早下一轮重试，同条目最多共 3 次。其他非零错误不是未投递证明。
- handoff 无回执（含 stale）不重投，请本人核对后收口；不换键绕过。幂等键不能作为 unknown 的重试授权。

## 读取边界

仅 poll.readable 才原生 GET list，固定 chat、升序、page_size:50。首次查询前在 im.jsonl 保存带版本意图：scanId、冻结 route/chat、秒级窗口。下界 `max(0,sinceMs//1000-1)` 秒，上界为首次同步开始向上取整秒；续读不换窗口，不依赖同毫秒内未知的二级排序。

**20 页是单次 sync 预算**。预算耗尽提交连续前缀 complete:false，下次优先恢复同窗 token。checkpoint 保存 next/seen token、lastMs、messageId→原生摘要；非本人正文不重复保存。先复用 incoming 完成入账、指令与确认，再保存 checkpoint；中断只重读旧页，凭业务 ID 去重，不跳页。接收凭据不是扫描进度。

核对每条 chat、精确毫秒、sender 对象及删除状态。只接受 sender_type:user、id_type:open_id 且 id 等于 ownerId 的本人消息，不采信正文自称；其他人和 bot 过滤。text 从 body.content 严格 JSON 解码，parent_id 缺失才规范化为 null，不能混用 shortcut.reply_to；删除消息不消费正文。本人未知消息类型、重复 JSON 键、NaN 或坏字段不修复成答案。

终页 has_more:false 且 incoming 成功后才保存 scanThroughMs（排他上界）并关闭扫描。poll.sinceMs 取首次可信时间、businessCursorMs（仅已接受本人消息推进）及完整 scanThroughMs 的最大值；下一新窗仍向下取秒重叠一秒。普通 incoming 的 since 上界门禁不放宽。

缺/重复 token、非法 has_more、异 chat、乱序、冲突或坏字段均**整批拒收，游标不动**；最多新增首次意图，不推进 checkpoint/水位。损坏或异路由进度不用；token 读取失败保留证据，不猜过期码、不重置或跳到现在。未完成扫描使 lastIncomplete=true；报告「IM 未读完」，不能说没有新答复。

## 安全入口与排错

自动定位失败时按 doctor 提示配置直接可执行文件，或固定 Node 脚本入口，例如：

```json
{"larkCommand":["node","C:\\tools\\lark-cli\\scripts\\run.js"]}
```

首项解析为真实绝对路径并校验符号链接目标。Windows 只执行 .exe；自动发现的 npm .cmd **仅作文本解析完整固定模板**，绝不执行包装。显式 .cmd、批处理、shell/脚本宿主及 node -e / Python -c 等解释器开关都拒绝，不尝试 shell 回退。固定 JS 必须存在；Python 替身只用于显式测试环境。所有调用 shell=False、UTF-8、单次超时 30 秒，正文为独立参数；工具和系统仍可能保留参数/日志，不承诺零留存。

| 诊断 | 处理 |
|---|---|
| 入口/包装/版本不符 | 本人核对安装或固定 Node/JS，不放宽模板 |
| bot_not_verified / auth_profile_mismatch | 核对应用、配置档与机器人权限，不把用户登录当 bot 修复 |
| sent_without_trusted_time / not_readable | 已发事实保留；请本人核对时间边界，不扫描旧历史、不重发 |

正文只发必要摘要，不带简历、完整聊天、截图、token 或 secret。只通知本人；真实收发测试须由本人另行发起并同意内容。完整本地业务状态见 [通知契约](../notify.md)。

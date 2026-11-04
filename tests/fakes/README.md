# 合成桌面替身（T6.1）

仅供测试，不随三份产品技能分发。没有网络、浏览器、桌面或真实工具调用，也不查询 PATH 中的 agent-desktop。不能据此声称真实平台或真实工具验证通过。飞书替身/真实响应样本不在本任务范围。

## 运行与路径

先把 scenarios/boss.json **复制**到 `tempfile.TemporaryDirectory()` 中的已有 JSON 文件，设置其绝对路径为 `AGENT_DESKTOP_FAKE_SCENARIO`。场景兼作状态文件，每次调用原子保存状态和 argv，衍生同目录 `.json.lock` 文件串行化；共用产品现有的标准库 fsutil 封装，不依赖安装的桌面工具。

- 场景必须在系统临时目录内、仓库外，并有 schemaVersion:1、synthetic:true。直接使用提交夹具、缺环境、相对路径、链接、损坏 JSON 或无效状态均失败关闭。无法安全写入状态时，错误信封的 details.argv 保留请求，不猜另一个文件位置。
- Python 3.9+、各系统统一 `[sys.executable, agent_desktop.py, ...]`。POSIX 另有可执行的 bin/agent-desktop，按自身路径加载 Python 文件，CWD 无关；不提供 `.cmd`。
- 所有文字、参数和状态 UTF-8；空格、换行、组合字符不规范化。测试只修改临时副本，绝不修改提交夹具或真实工具配置。

## 显式命令子集

对齐[接入参考](../../integrations/agent-desktop/README.md)记载的 1.0.2 / JSON 2.5 信封、退出码与 disposition；下面参数及观察字段是**替身的显式子集**，不是补造的真实工具样本或完整 CLI 兼容承诺。实际接入仍须核对当时工具契约；未实现的参数/目标明确拒绝，不能默认成功。

```text
--version
permissions [status]
apps
windows [--app APP]
snapshot --window WINDOW [--root ELEMENT_ID]
find --window WINDOW --snapshot SNAPSHOT --query TEXT
get --window WINDOW --snapshot SNAPSHOT (--ref REF | --id ELEMENT_ID)
click --window WINDOW --snapshot SNAPSHOT (--ref REF | --id ELEMENT_ID)
set-value --window WINDOW --snapshot SNAPSHOT (--ref REF | --id ELEMENT_ID) --value TEXT
type --window WINDOW --snapshot SNAPSHOT (--ref REF | --id ELEMENT_ID) --text TEXT --headed
```

- 所有响应是 JSON：version 为字符串 2.5，command 为请求子命令，ok 为布尔。--version 的 command 是 version、data.version 是 1.0.2。应用在 data.apps，窗口在 data 数组，权限在 data.accessibility.state。
- snapshot 的 data 含 snapshotId/windowId/root/elements/complete；find 含 matches/complete；get 含 element。--root 只展开该元素及后代，不意味着全页面。零匹配不等于区域为空。元素 id 是合成目标标识，不是 BOSS 暴露的稳定消息 ID。
- 引用绑定快照、窗口、页面与导航代次；跨快照 ref、跨窗口或离开后返回的旧引用拒绝。--id 只在指定快照范围匹配；不唯一报 AMBIGUOUS_TARGET。没有隐式选首个窗口/元素。
- 成功动作 data.action 与 command 相同，disposition 为 delivered_unverified/unsafe；它仅表示工具动作，不表示消息已发送。观察可无 disposition。
- 结构化错误退出1，有 error.code/message/disposition/details，无 data。参数错误退出2，INVALID_ARGS、command:unknown，符合参考的唯一绑定例外。不支持 help、截图、trace、任意 launch 或其他未知命令。launch --cdp（含等号形式）在故障注入之前硬拒绝并记录 argv，绝不回退真程序。

## 场景与可观察状态

apps/windows/pages 定义合成窗口和页面，窗口 page/generation 随点击跳转变化。pages.elements 为父子元素表，action 仅支持 navigate/send/unsupported；不解释任何脚本或文本指令。夹具包含职位列表、详情、消息列表、新会话、同分钟多 HR、只有日期、残留草稿、contenteditable 与面试邀约卡片。卡片按钮未实现外发，明确拒绝。

state 保存 calls、snapshots、drafts、inputEvents、messages 和 faults：

- 原始 argv 每次保留，calls.effects 仅供测试断言，不作为真实工具字段。草稿按窗口/页面/输入框隔离，切换后再返回仍存在；不自动清空。
- native set-value 更新显示与页面输入状态并触发 input 事件。contenteditable set-value 只更新显示，记录 inputEvent:false，不触发页面 input；get 仍可能读回新值。点击发送消费的是 inputValue，不是显示值，可能没发或发出旧草稿。
- headed type 在已有显示文本后追加并触发 input 事件，不把 type 当成覆盖。后台 type 拒绝，以 unknown/unknown 保守报告，不自动切换方式。
- 空输入框的发送按钮仍可 enabled；点击工具成功但不产生消息。deliveryMode:delivered 添加 [送达]；no_delivery 留下 sending:true、没有 deliveryLabel。此差异可供后续行为检查，不能用工具 ok 代替业务成功。
- 消息 label 保留原样，日期消息不虚构分钟；面试卡保留 cardType/buttons。sendLabel 是合成场景指定的标签，不读取真实平台时间。

## 故障

state.locked:true 模拟窗口不可访问，details.kind 为 window_without_accessibility_element，disposition 为 unknown/unknown；不实际锁屏。权限也可改 denied，和窗口锁屏分开查询。

state.faults 为按 command 精确匹配、只消费一次的列表。kind 为 timeout、locked、error、incomplete；phase 缺省 before，可为 after。error 可带 code；incomplete 仅用于 snapshot/find，返回 complete:false、truncated:true。after 动作保留已执行状态，错误中有 steps 和一致的 after_action.disposition，不伪造明确未执行。

例：`{"command":"click","kind":"timeout","phase":"after","delaySeconds":2}`。timeout 先刷盘 argv、效果和 timeoutPending，再释放状态锁，向 stderr 输出 `fake-timeout-ready`，有限等待后返回工具超时错误。delaySeconds 限 0.05–2 秒。真实超时测试读取 ready 后短时等待并终止该子进程，不靠长 sleep 拖全量；调用方仍须有界管理子进程。记录的 response/exitCode 是拟返回结果，timeoutPending 表示不能据此声称 stdout 已送达。after 超时的消息可能已经可见，后续观察不抹去它。

## 回归

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_fake_agent_desktop.py
```

覆盖正常/错误信封、argv、页面与输入状态、故障、真实进程超时清理、POSIX launcher、未分发及夹具不被污染。原生 Windows 直接执行 Python；仅 POSIX launcher 测试按系统能力区分。不涉及 T6.2 采集、lark-cli 或真实平台验证。

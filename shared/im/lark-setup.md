# 飞书配置与测试（setup）

本人在 setup 第 8 屏选「现在配置」，或要求修改、测试飞书时读本文件；命令别名同 setup 正文。
先读 [飞书适配器](lark.md)；需要收发时读 [通知契约](../notify.md)。

## 配置步骤

1. 新建先给「以后再说（推荐）/ 现在配置 / 不需要」；以后再说或不需要均记 `{"channel":"none"}`。需要则经本人允许，按随包安全入口只读列配置档，以 name 请本人选 larkProfile，不猜 active。再核验同应用 auth 中已验证的本人身份，或按本人提供的姓名/邮箱查 open_id，展示姓名请本人确认 ownerId；多候选让本人选，不取第一项；bot.openId 不能当本人。身份、应用或结果不明就停。最后问 waitMinutes：「有消息需要你回复时，多久没有新增待回复消息就停止监听？默认 60 分钟，可设 1–60 分钟；填 0 关闭监听。监听不会占住本轮，停止后，回复会在下一轮运行时处理。」
2. 入口缺失按依赖准备补装；隔离安装由 Agent 解析 larkCommand 数组，不 shell 回退。record 的 value 是完整 IM 对象，共用 im_config 校验；kept/default 也传对象，skipped 且无值表示不改，不是选 none。结构通过不证明身份或入口可用。经本人确认，按setup 第 3.3 节暂存并 commit，保留未选字段与授权。
3. **事务提交后** `im_lark.py doctor` 读正式工作区配置，验证所选路由的 bot；不填假 ownerId 提前 doctor、不绕事务。none 的 skipped:channel_none 只表示跳过，**不算机器人验证成功**。doctor 通过也不代表已收发；未通过不排队测试。
4. bot 验证通过且本人**另行同意测试内容**才 `notify test`，只排队（queued）；再 `im_lark.py sync`。sent 无可信时间不能建立读取起点，补查失败不重发；unknown 不重发。私聊可读后请本人**引用测试消息回复「收到」**，再次 `im_lark.py sync`，用 `notify replies --state test` 与 `notify status` 核对测试、私聊、队列及冲突。普通业务答复仍 received；未读完明确报告。能力不足就停，不换通道；测试同意不增加长期授权。

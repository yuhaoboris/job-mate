# job-mate · 职伴

职伴是面向不同 Agent 的求职助手 skills：定义筛选职位、打招呼、跟进 HR 消息的流程和规则，关键决定由你本人拍板；具体工具与执行方式由实际 Agent runtime 决定。

## 技能

| 技能 | 作用 |
|---|---|
| `setup` | 建立求职工作区：偏好、个人事实、简历、问答库 |
| `apply` | 筛选职位并发起首次沟通；可只筛选不发送 |
| `followup` | 检查 HR 消息、按授权代答与跟进，需要本人决定时上报 |

首批支持 BOSS 直聘，后续扩展智联招聘、前程无忧。

## 原则

- 只在你授权的范围内外发；结果不明就停，不重复发送。
- 只依据你提供的事实沟通，不编造经历。
- 控制节奏和总量，遇到验证码、风控、访问限制立即停止；不模拟人工、不规避平台检测。
- 求职数据保存在你的本地工作区，不进入本仓库。

## 安装（开发版）

核心脚本需要 Python 3 和 POSIX `fcntl` 文件锁，不支持直接在原生 Windows Python 中运行。Agent 还需具备下文所述的执行能力。

构建整套技能并安装到项目或用户的技能目录：

```sh
python3 scripts/build.py --install <项目>/.agents/skills
```

每个技能（`job-mate-setup`、`job-mate-apply`、`job-mate-followup`）自带所需的共享规则与脚本。重新执行同一命令可更新安装；安装器只替换经过归属核对的 `job-mate-*` 目录，不覆盖手工修改或额外用户文件，其他技能保持不变。

安装或更新期间请暂停使用这些技能。若命令报错或出现 `warnings`，按提示检查并保留备份；安装器不保证运行中的 Agent 同时看到全部目录更新，也不提供断电自动恢复。

## 开始使用

1. 在独立的求职工作区运行 `setup`，确认筛选偏好、个人事实、简历与授权。
2. 运行 `apply` 筛选职位；建议先只筛选，核对结果后再明确授权首次沟通。
3. 运行 `followup` 处理 HR 消息；默认 L2，只按有依据的口径代答，需要承诺的事项交由你决定。

按所用 Agent 的技能调用方式选择 `job-mate-setup`、`job-mate-apply` 或 `job-mate-followup`。

## 运行环境

核心定义 [通用执行契约](shared/execution.md) 与 [IM 本地状态接口](shared/notify.md)，不自带浏览器执行器、IM 适配器或后台服务。runtime 需提供可见浏览器 / UI、IM 收发（需要通知时）及调度能力；能力不足时停止并报告，不换工具或通道绕过安全门禁。

BOSS 只操作你日常已登录的可见浏览器，经辅助功能树或桌面 UI，不使用 CDP / Playwright 或内部接口。选定执行偏好不能满足时不操作平台、不回退；没有结果证据用禁发锁。锁 / guard 不是进程强单实例，自动唤起的互斥和后代清理由 runtime 保证。

可选参考：[agent-desktop](integrations/agent-desktop/README.md)、[飞书私聊](integrations/lark/README.md)。这些文档不随核心分发，不是必须工具，也不表示当前版本在所有环境中兼容。

## 开发

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
```

更多说明见 [文档入口](docs/README.md)与[设计](docs/design.md)。参与维护前请读 [贡献指南](CONTRIBUTING.md)；敏感问题按 [安全报告说明](SECURITY.md) 处理。

许可证：Apache-2.0。

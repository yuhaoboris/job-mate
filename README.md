# job-mate · 职伴

职伴是面向不同 Agent 的求职助手 skills / plugin：定义筛选职位、打招呼、跟进 HR 消息的流程和规则，关键决定由你本人拍板；具体工具与执行方式由实际 Agent runtime 决定。

> 状态：开发预览。核心技能和脚本已实现，部分 BOSS 流程与 IM 决策回路子集已实测；完整验收尚未通过。核心保留确定性的业务状态脚本，工具收发与调度由 runtime 接入；解耦后的实际 Agent 行为与正式验收仍待复测，不开发 Pi 原生 package。已验证范围和待处理项见 [项目状态](docs/status.md)。

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

Claude Code：

```text
/plugin marketplace add <本地仓库路径>
/plugin install job-mate@job-mate
```

将 `<本地仓库路径>` 替换为本仓库的实际绝对路径。之后在求职工作区中使用 `/job-mate:setup`、`/job-mate:apply`、`/job-mate:followup`。仓库更新后如未生效，运行 `/plugin marketplace update job-mate`。

其他 Agent（Codex、Pi 等只认技能的环境）：构建独立技能并安装到项目或用户的技能目录：

```sh
python3 scripts/build.py --install <项目>/.agents/skills
```

每个技能（`job-mate-setup`、`job-mate-apply`、`job-mate-followup`）自带 `shared/` 与 `scripts/`，`BUILD.json` 记录来源提交及文件摘要，不包含构建者本机路径。未修改的新格式安装可重新执行同一命令更新；只替换经过归属核对的 `job-mate-*` 目录，其他技能保持不变。

旧格式安装（`BUILD.json` 没有 `format: 1`）、手工修改过的文件或额外用户文件会阻止自动覆盖。先暂停使用该技能，将报错所指的旧技能目录**整体移到 runtime 不会扫描的位置**，再重新安装（不要只在原技能目录内改名，以免重复加载）；不要只删除 `BUILD.json`，也不要对求职工作区执行删除操作。确认新版可用后，再自行处理迁出的旧副本。

构建和安装在完整暂存后切换；捕获到切换错误时尝试回滚，但不保证多个目录对正在运行的 Agent 同时可见，也不提供断电自动恢复。升级期间不要运行这些技能。若命令报告回滚失败或 `warnings`，按提示保留并检查备份／事务目录，不要直接删除。

### runtime 接入

核心定义 [通用执行契约](shared/execution.md) 与 [IM 本地状态接口](shared/notify.md)，不自带浏览器执行器、IM 适配器或后台服务。runtime 需提供可见浏览器 / UI、IM 收发（需要通知时）及调度能力；能力不足时停止并报告，不换工具或通道绕过安全门禁。

BOSS 只操作你日常已登录的可见浏览器，经辅助功能树或桌面 UI，不使用 CDP / Playwright 或内部接口。选定执行偏好不能满足时不操作平台、不回退；没有结果证据用禁发锁。锁 / guard 不是进程强单实例，自动唤起的互斥和后代清理由 runtime 保证。

可选参考：[agent-desktop](integrations/agent-desktop/README.md)、[Pi UI](integrations/pi/README.md)、[飞书私聊](integrations/lark/README.md)。这些文档不随核心分发，不是必须工具，也不表示当前版本兼容或真实验收通过。

### 执行层迁移

- 已删除 `notify wait`、`notify listen`、`notify service`、`workspace check --env` 和 `scripts/desktop.py`。等待 / 唤起由 runtime 接入，send / test 现在只排队，不表示消息已发送；runtime 认领、投递后提交真实回执。
- 已安装旧服务 `com.job-wingman.listener` 的，请本人自行停用并移除对应 plist，再使用新运行方式。新版不会自动停止服务，也不会读取或删除 `~/.config/job-wingman/listener.json`；旧服务会因命令删除而无法继续正常唤起。先确认旧实例及后代已停止，不靠新运行锁推断。
- 旧独立技能目录先整体移出 runtime 扫描目录，再安装新包，避免旧技能继续调用已删除的接口。
- 旧 `im.channel: lark` 可兼容读取，但须由当前 runtime 接入；setup 提供保留兼容或经本人确认迁移为 runtime，旧配置幂等窗口按 0，旧送达不明记录不自动重发。
- 旧 IM 历史缺身份字段，默认不继承私聊、游标或 ready。按 [旧历史身份绑定](shared/notify.md#旧历史的显式身份绑定) 先核对原私聊对方和应用元数据，再显式本地确认；能力不足就保留待核对。绑定不是业务执行，旧 ready 仍须按原决定 ID 完成 F1/F2 对账。
- legacyUnbound / otherRoutes 只报告计数，不能说没有旧答复。其他 route 待办保守阻断所有自动唤起，处理仅按 [其他路由待办的本地恢复](shared/notify.md#其他路由待办的本地恢复)，不能临时改回配置后直接运行 followup。replyRecovery 提示元数据不足时，在同一已验证通道重发带事项 ID 的新回复或在终端明确处理，不猜旧引用。

## 从 job-wingman 升级

插件名与独立技能前缀已改为 `job-mate`；旧插件和旧 `job-wingman-*` 技能需在各自 Agent 中停用，再安装新版，避免同时启用两套技能。

工作区标记 `job-wingman.json` 保留，无需重命名数据文件。`~/.config/job-wingman/`、旧服务名 `com.job-wingman.listener` 与 `JOB_WINGMAN_*` 是历史兼容标识，不因改品牌直接替换；不表示新版仍读取旧监听器配置或保留已删除的执行器功能。IM 展示标题改为「职伴」，同一结果的通知去重键保持兼容。

## 开发

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
```

设计依据见 [docs/design.md](docs/design.md)，当前实现和验收状态见 [docs/status.md](docs/status.md)。参与维护前请读 [贡献指南](CONTRIBUTING.md)；敏感问题按 [安全报告说明](SECURITY.md) 处理。历史评审、回应与演练记录见 [docs/archive/](docs/archive/README.md)。

# 给 Agent 的安装指引

本人说「请按 https://github.com/yuhaoboris/job-mate 的 INSTALL.md 安装 job-mate」时，安装整套技能并自动补齐必要运行依赖，不让本人处理构建细节。**请求安装即授权安装必要软件依赖，无需逐项确认。** 系统授权、管理员认证、登录和扫码仍由本人完成；安装不授权平台外发或键盘鼠标操作。

## 1. 确认环境与位置

- 判断当前 Agent、命令实际运行的电脑与系统，以及它实际读取的技能目录。不要把源码或求职工作区当技能目录，不假设远程会话能操作本机桌面。只装进当前 Agent 实际读取的一个目录；DeepSeek Harness 的 `~/.dsh/skills` 与 `~/.agents/skills` 二选一。
- 向本人展示 Agent 名称与技能目录，请其确认；更新前请本人暂停使用三个技能。确认的是安装位置，不反复询问是否安装每项必要依赖。
- **先读并执行 [安装依赖与运行就绪](shared/dependencies.md)**：补齐 Python，准备当前系统的桌面能力；Node 仅在所选安装方式需要时安装。飞书只在本人选择配置时准备，不影响本地功能。不满足桌面条件时提前说明，不等第一次 apply 才发现。

## 2. 获取源码并整套安装

在系统临时目录获取 https://github.com/yuhaoboris/job-mate 的源码；可用 git clone 或下载源码压缩包，不为下载单独强装 Git。只获取 job-mate，不读取求职资料。已有源码也在临时副本中构建，避免影响日常工作目录。

在源码根目录运行：

```sh
python3 scripts/build.py --out <临时构建目录> --install <已确认的技能目录>
```

原生 Windows 用 `py -3` 代替 python3，路径含空格时按当前 shell 引用。

也可运行 `python3 scripts/build.py --out <临时构建目录> --install`：安装器探测常见 Agent 配置目录，列出候选并要求选择、确认。没有交互输入能力时，不伪造确认；在对话中确认后使用显式目录命令。没有候选时按当前 Agent 实际配置确定位置，不猜目录或新建其他 Agent 的配置。

三个技能必须整套安装，不用安装单个子目录的技能命令，不复制源 SKILL.md 代替构建，不提供 Agent 专属打包。构建器只负责离线打包与安装；必要依赖由 Agent 按第 1 节准备，不能只执行构建命令就结束。

## 3. 核对运行就绪

- 命令退出 0，installed 恰有 job-mate-setup、job-mate-apply、job-mate-followup。
- 三个目录均有 SKILL.md、shared/、platforms/、scripts/ 与 BUILD.json；BUILD.json 的 suiteVersion、commit 一致。有 warnings 时先处理，不把不完整安装说成成功。
- 用实际 Python 执行已安装包的 `scripts/workspace.py --help`、`scripts/ledger.py --help`，核对退出码；不创建或读取真实工作区。
- 按[完成判据](shared/dependencies.md#4-完成判据)核对桌面入口、上游操作 skill、权限与可见窗口。缺少软件由 Agent 补齐，需本人授权则给出具体系统设置动作，完成后重新检查。飞书未启用就跳过，不默认发送测试消息。
- 若当前 Agent 需要刷新或重开会话才能发现技能，明确给出动作；刷新前报告待完成，不宣称技能已经可调用。

## 4. 汇报与清理

- 分别报告技能包、脚本环境、桌面环境、可选飞书和待本人动作。必要项全部通过才说：「安装完成，说『开始求职设置』就能开始。」有阻断说：「技能包已安装，运行准备尚未完成」，附一个明确恢复步骤。
- 不触碰其他技能。安装拒绝覆盖时保留现场，按 [安装与维护](docs/installation.md) 处理，不擅自删除或覆盖本人改过的文件。
- 清理自己创建的临时源码、构建目录，不删除持久依赖目录；错误需保留现场时说明。安装不创建或修改求职工作区，不读取招聘页面，不运行平台或飞书外发测试。

# 给 Agent 的安装指引

本人说「请按 https://github.com/yuhaoboris/job-mate 的 INSTALL.md 安装 job-mate」时，按本文完成安装，不让本人处理构建细节。

## 1. 确认环境与位置

- 判断当前 Agent 与它实际读取的技能目录；不要把当前仓库或求职工作区当作技能目录，也不要假设远程会话能读取本机技能。同一套技能只装进当前 Agent 实际读取的一个目录。DeepSeek Harness 可读取 `~/.dsh/skills` 和 `~/.agents/skills`，二选一，不要同时安装到两处，避免重复加载。
- 需要 Python 3.9+，仅标准库。macOS/Linux 使用 python3；原生 Windows 使用 py -3。缺少时给本人一个安装办法，再停止。
- 向本人展示 Agent 名称与技能目录，请其确认；如果在更新，先请本人暂停使用这三个技能。不要擅自安装其他依赖或更改 Agent 配置。

## 2. 获取源码并整套安装

在系统临时目录中获取 https://github.com/yuhaoboris/job-mate 的源码；可用 git clone 或下载源码压缩包。只获取 job-mate，不读取求职资料。已有源码也在临时副本中构建，避免影响日常工作目录。

在源码根目录运行：

```sh
python3 scripts/build.py --out <临时构建目录> --install <已确认的技能目录>
```

原生 Windows 把 python3 换成 py -3，路径含空格时按当前 shell 规则引用。

也可运行 `python3 scripts/build.py --out <临时构建目录> --install`：安装器探测常见 Agent 配置目录，列出候选并要求选择、确认。没有交互输入能力时，不伪造确认；先在对话中请本人确认，再使用显式目录命令。没有候选时按当前 Agent 实际配置确定位置，不猜目录或随意新建其他 Agent 的配置。

三个技能必须整套安装，不用安装单个子目录的技能命令，不复制源 SKILL.md 代替构建，不提供 Agent 专属打包。

## 3. 核对并结束

- 命令退出 0，installed 列表恰有 job-mate-setup、job-mate-apply、job-mate-followup。
- 三个目录均有 SKILL.md、shared/、platforms/、scripts/ 与 BUILD.json；BUILD.json 的 suiteVersion、commit 一致。安装器已经校验产物与本地引用；有 warnings 时先处理，不把不完整安装说成成功。
- 不触碰其他技能。安装拒绝覆盖时保留现场，按 [安装与维护](docs/installation.md) 给出一个安全处理办法，不擅自删除或覆盖本人改过的文件。
- 告诉本人：「装好了，说『开始求职设置』就能开始。」若当前 Agent 需要重新加载技能，补充对应的刷新或重开会话动作。
- 清理自己创建的临时源码、构建目录；发生错误且需保留现场时明确说明。安装不创建或修改任何求职工作区，不运行平台或飞书外发测试。

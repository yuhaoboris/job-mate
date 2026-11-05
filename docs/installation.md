# 安装与维护

首次使用见 [README](../README.md)，委托 Agent 安装见 [INSTALL](../INSTALL.md)。

## 整套构建与安装

在源码根目录运行：

```sh
python3 scripts/build.py --install
```

安装器只检查当前用户常见 Agent 配置根（.agents、.claude、.cursor、.dsh、.pi/agent），不递归扫描个人目录。列出存在的配置根对应的 skills 目录；多个候选先选编号，再确认，单个候选也要确认。没有候选、取消或无法读取交互输入均不会发布产物或安装技能。

已经确认路径时可显式指定：

```sh
python3 scripts/build.py --install <Agent 的技能目录>
```

Windows 使用 `py -3 scripts/build.py`。路径含空格时加引号。默认构建产物放在 dist/，不提交；开发验证用 `--out <临时目录>`，不覆盖日常安装。不带 --install 仅构建。

常见个人技能目录：

| Agent | 目录 |
|---|---|
| Codex / DeepSeek Harness / 通用 Agent | ~/.agents/skills |
| Claude Code | ~/.claude/skills |
| Cursor | ~/.cursor/skills |
| DeepSeek Harness | ~/.dsh/skills |
| Pi | ~/.pi/agent/skills |

同一套技能只装进当前 Agent 实际读取的一个目录。DeepSeek Harness 也能读取 `~/.agents/skills`，不要同时装进 `~/.dsh/skills` 和 `~/.agents/skills`，避免重复加载。目录存在不代表当前 Agent 会读取，也不代表具备浏览器能力；请按实际 Agent 配置确认。三个自包含包 job-mate-setup、job-mate-apply、job-mate-followup 必须整套安装，每份都有同版本规则、脚本和平台描述。不做 Claude Code Plugin 或其他 Agent 专属打包。

## 更新与失败处理

安装或更新前暂停使用整套技能，重新运行安装命令即可整套更新或补齐误删的包。安装器只替换未修改的受管同名目录，不动其他技能。拒绝覆盖时，先把对应包整体保存在技能扫描目录以外，再重新安装；不要只删 BUILD.json，不擅自丢弃修改或未知文件。

安装采用可回滚批次：任何切换失败都会尝试整体回滚。不保证运行中的 Agent 看不到中间版本，也不提供断电自动恢复。有错误或 warnings 时保留现场和事务目录，不把清理警告当作回滚成功。安装器的归属检查、路径保护与回滚行为以 scripts/build.py 为准。

## 工作区版本准入

每个包的 BUILD.json 记录 suiteVersion 与源码 commit，三个包必须一致。工作区中的 job-mate.json 记录最近写入版本。只读、写入和恢复的准入规则见 [工作区：套件版本准入](../shared/workspace.md#套件版本准入)；较新工作区拒绝较旧技能写入时，应更新整套技能，不手工改版本号绕过保护。安装不会创建、扫描或修改求职工作区。

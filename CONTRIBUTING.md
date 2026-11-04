# 贡献指南

## 先确认范围

1. 阅读 [仓库约定](AGENTS.md) 和 [README](README.md)；修改脚本、技能或产品规则前，阅读 [设计](docs/design.md) 的对应章节。
2. 根据真实代码路径和配置确认能力，不把历史抽查当作当前完整支持。

job-mate 面向不同 Agent runtime，定义求职流程、授权、安全边界与结果判据。数据脚本维护确定性状态，不执行外部工具；飞书由随包适配器显式同步，浏览器按[随包入口](shared/browser/README.md)及[工具规则](shared/browser/agent-desktop.md)选择。调度与进程管理由 runtime 负责，不安装后台服务或常驻。变更须遵守 [通用执行契约](shared/execution.md)，保持数据与工具适配分离；实际接入和 Agent 行为分别验证，不能放宽安全规则或宣称所有环境兼容。

## 本地开发与验证

- Python 脚本仅用标准库，最低 Python 3.9；支持目标为 macOS、Linux 与原生 Windows。跨系统实现及回归必须覆盖文件锁、原子替换、编码与时区，未验证的环境须明确说明。
- 本地测试工作区放在仓库根的 `workspace*/` 或系统临时目录。
- 探针、测试和构建均使用临时目录、合成账号与合成工作区；IM、桌面和 Agent 调用用替身隔离，不接真实服务。

在仓库根目录执行（子 shell 退出时清理本次临时目录）：

```sh
(
  set -eu
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  export PYTHONDONTWRITEBYTECODE=1
  python3 -m unittest discover -s tests
  python3 scripts/build.py --out "$tmp/dist"
  python3 scripts/workspace.py init "$tmp/workspace" \
    --account boss:synthetic:合成测试用户
  python3 scripts/ledger.py --workspace "$tmp/workspace" validate
)
```

最后两条仅检查合成骨架与空台账，不代表工作区已完成 setup 或具备外发条件。安装验证也只指向临时技能目录，不覆盖日常使用的安装。

## 为变更提供证据

- 脚本变更：在 `tests/` 补可复现用例，写清输入、操作、预期和失败分支；缺陷修复的用例应能在修复前复现问题。
- 技能或共享规则变更：同步设计依据，添加适用的静态契约测试；涉及多步判断时另给合成环境的 Agent 行为场景、预期终态和证据，未执行的标为待测。共享规则放在 `shared/`，平台描述与页面要点放在 `platforms/<平台>/`，由构建分发到整套技能，避免复制维护。
- 完成标准是单测与行为检查；验收由维护者本人进行，仓库不记录验收状态。文字断言只能证明规则存在，不能证明 Agent 遵循；行为检查使用临时合成工作区和替身工具。
- 提交说明列出变更目的、复现步骤、运行命令、环境、结果与未覆盖项。纯文档变更至少检查本地链接与 `git diff --check`。

## 数据与提交边界

- 贡献验证不在真实招聘平台外发，不修改真实求职工作区、runtime 服务或个人配置；真实实测只能由本人发起并按工作区规则执行。不得使用 CDP / Playwright 访问 BOSS，不模拟人工、不规避检测，遇限即停。
- 个人偏好、事实、聊天、简历、账号凭据和本机私人路径不进仓库、日志附件或公开讨论；复现材料用脱敏合成数据。敏感问题按 [安全说明](SECURITY.md) 报告。
- 提交源码与必要测试，不提交 `dist/`、本地安装的技能副本、字节码缓存、运行日志或私人工作区；提交前检查 `git status --short` 和暂存差异，忽略规则不是隐私审查的替代品。
- `job-mate.json` 是唯一工作区标记，不保留旧格式兼容。
- 创建远程仓库、推送和发布须另获维护者授权。

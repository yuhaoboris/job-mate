# 贡献指南

先读 [AGENTS.md](AGENTS.md) 和 [产品设计](docs/design.md)。工作流、数据核心与插件适配必须区分：业务规则只在 job-mate 维护，宿主负责可见浏览器操作。

修改 `core/` 或 `references/` 后运行 `python3 scripts/build.py`，提交生成到两个 skill 的完整资源。不要修改生成副本。运行 `python3 -m unittest discover -s tests` 和 `python3 scripts/build.py --check`。

验证使用临时目录与合成资料。新增测试应验证用户可观察的行为：首次使用、授权范围、跨进程冲突、恢复、迁移或记录结果，不靠匹配文案证明 Agent 行为。复杂技能变更另用合成材料做独立行为检查。说明离线和真实宿主验证的差别。

不在开发测试中实际联系招聘者，不修改真实求职工作区。简历、账号、聊天、凭据、本机私人路径与内部开发材料不提交；内部材料放 `.private/`。公开发布前检查文件、安装包和 Git 历史，忽略文件不能替代隐私检查。

源码仅用 Python 标准库，兼容 Python 3.9+ 与 Windows/macOS/Linux。Python 脚本只管理本地状态，不连接平台或安装服务。维护者授权后才推送或发布。

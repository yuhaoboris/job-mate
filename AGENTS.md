# job-mate 2.0

这是面向任意具备本地文件、Python 3.9+ 与可见桌面操作能力的 AI Agent 的 BOSS 直聘求职技能。用户当前的 2.0 要求取代 1.0 的整套安装、独立 setup、多平台预留与 IM 设计。

- 产品依据是 `docs/design.md`。工作流只在本仓库维护；Harness 插件使用原版发布快照。
- 两个 skill 可单独安装。入口手写；共享资源由 `scripts/build.py` 从 `core/`、`references/` 同步并提交，保证直接 `npx skills add` 可用。禁止手改生成副本。
- 首次使用由技能读取用户提供的简历、只问缺失偏好，然后自动准备工作区。用户不填写配置文件。
- 运行时只用 Python 标准库，支持 macOS、Linux、Windows。所有确定性数据操作共用一个核心；浏览器和判断由 Agent 执行。
- 开发与验证只用临时目录、合成数据和替身工具。不实际联系招聘者、不操作真实求职工作区，除非用户明确要求。
- 运行 `python3 -m unittest discover -s tests`、`python3 scripts/build.py --check`。单测不等于真实平台或模型验证。
- 求职资料、简历、凭据、内部开发材料不得提交。内部材料放 `.private/`；开源架构、数据契约与贡献文档可提交。
- 公开前检查当前文件和全部可达 Git 历史；不得为清理历史擅自 force push。保留 Apache-2.0 许可证。

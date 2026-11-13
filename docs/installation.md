# 安装与维护

标准入口为 `npx skills add yuhaoboris/job-mate`。指定技能用 `--skill job-mate-apply` 或 `--skill job-mate-followup`；指定 Agent 用 CLI 的 `--agent`，用户范围安装用 `-g`。

源仓库坐标是 `yuhaoboris/job-mate`，已包含作者命名空间；无需发布同名 npm 包。技能名使用 job-mate 前缀避免与通用 apply/followup 冲突。安装器当前要求 Node.js 22.20+；运行技能需要 Python 3.9+，使用标准库，不需 pip 安装业务依赖。

安装器复制完整技能目录。共享资源虽然随每个技能各带一份，但它们来自唯一源码，构建检查保证逐字一致；用户只安装一个仍可完成资料准备与自己的任务。

更新：`npx skills update`；先结束正在运行的任务，再在新会话中使用更新后的技能。求职资料保存在独立工作区。

项目聊天的默认数据目录现为 `<项目>/job-mate/`，普通聊天仍为 `~/.job-mate/`。升级前已在 `~/.job-mate/` 保存资料的用户，要继续使用原记录，可告诉 Agent“继续使用 ~/.job-mate 的已有求职资料”，或设置 `JOB_MATE_HOME` 指向原目录。更新不会自动移动或合并数据；不同项目的默认目录各自保存记录。Harness 工作台使用其已选定的数据目录。

官方依据：[skills CLI](https://github.com/vercel-labs/skills)、[创建与分享技能](https://github.com/vercel-labs/skills/blob/main/docs/creating-skills.md)，核对日期 2026-11-13。

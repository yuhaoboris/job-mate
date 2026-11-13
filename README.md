# job-mate · 职伴

让 AI Agent 帮你在 **BOSS 直聘**筛选职位、打招呼、处理 HR 消息。提供一份简历，聊几句求职偏好，就能开始。重要决定仍由你做。

## 安装

```sh
npx skills add yuhaoboris/job-mate
```

也可以只安装一个技能：

```sh
npx skills add yuhaoboris/job-mate --skill job-mate-apply
npx skills add yuhaoboris/job-mate --skill job-mate-followup
```

CLI 需要 Node.js 22.20+；技能的数据工具需要 Python 3.9+。`skills` 会让你选择 Agent 与安装范围，全球安装可加 `-g`。[安装与更新](docs/installation.md)

## 开始使用

把简历文档交给 Agent，然后说：

> 帮我找上海的前端岗位，先筛选，不发消息。

或者：

> 看看 BOSS 上 HR 的回复，先帮我整理一下。

Agent 会读取简历，只问缺少的偏好，把资料保存在 `~/.job-mate`，然后继续任务。没有独立 setup，也不用填写配置文件。PDF、DOCX、HTML 等由所用 Agent 的文档能力读取；扫描件需要 OCR。已有资料会复用，调整时直接说“把目标城市改成广州”。

| 技能 | 做什么 |
|---|---|
| `job-mate-apply` | 搜索、筛选职位；获授权后首次沟通 |
| `job-mate-followup` | 处理已有会话、按授权回复或发送简历、主动跟进 |

安装其中一个即可独立使用。默认先筛选或起草；明确说出发送范围后才发送。“只看看”优先于已有授权。面试时间、薪资/Offer 承诺、交换联系方式等由你决定；发送结果不明会停止核对，不自动重发。

## 使用条件

技能不绑定某个 Agent。实际操作 BOSS 需要 Agent 能读写本机文件、运行 Python，并通过桌面或辅助功能操作你日常已登录的可见浏览器。登录、扫码、验证码和系统权限仍需你完成。缺少桌面能力时，可先分析你提供的职位资料，但不能宣称访问或发送成功。

不使用 CDP/Playwright、无头访问或隐藏接口，不规避平台检测。自动操作可能不符合平台规则，也可能导致账号限制；技能不保证消息送达或求职结果。

## 数据与工作台

个人事实、偏好、原始简历、沟通记录和待决定事项保存在本地独立工作区，不进入技能安装目录。工作区默认不加密；所用 Agent 的模型服务和日志仍可能处理、留存这些资料。请只提供求职所必需的信息。

没有 IM 通知或后台服务。待办显示在当前对话；使用 [DeepSeek Harness 工作台](https://github.com/yuhaoboris/dsh-job-mate) 可看到同一份资料和进展。插件携带本仓库的原版技能，不另写求职规则。

## 开发

```sh
python3 scripts/build.py
python3 -m unittest discover -s tests
python3 scripts/build.py --check
```

共享源码在 `core/` 和 `references/`；两个 skill 自带生成的完整资源，保证单独安装可用。[设计](docs/design.md) · [数据命令](references/protocol.md) · [贡献](CONTRIBUTING.md) · [安全](SECURITY.md)

离线测试、Agent 行为检查和真实平台验证分别报告。许可证：[Apache-2.0](LICENSE)。

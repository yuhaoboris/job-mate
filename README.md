# job-mate · 职伴

在 BOSS 直聘搜索相近职位、联系活跃招聘者、跟进全部会话，争取面试机会。

```sh
npx skills add yuhaoboris/job-mate --skill job-mate
```

> 帮我找杭州的 AI 全栈岗位，按职位写招呼语，再看看有没有 HR 要回复。

也可以说「只看不发」或「只跟进消息」。搜索只需职位和城市，简历可选。首次打招呼选平台预设、固定原文或动态撰写；每轮默认尽量发满 20 条，不足报实际数。面试时间由你决定。

需要 Python 3.9+、能读写本地文件的 Agent，以及[浏览器工具](references/boss.md#浏览器选择)。登录与验证码由本人处理，不保证获得邀约。使用前请了解[数据隐私与安全边界](SECURITY.md)。

若已安装 Hermes Agent，可通过 `default` 已配置的渠道（如飞书）同步接收报告，详见[交付渠道](references/report.md#交付渠道)。

离线安装：运行 `python3 scripts/build.py --out /path/to/export`，复制导出的 `job-mate/`。

[求职流程](SKILL.md) · [数据命令](references/protocol.md) · [贡献](CONTRIBUTING.md) · [安全](SECURITY.md) · [Apache-2.0](LICENSE)

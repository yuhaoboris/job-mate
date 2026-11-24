# 贡献指南

先读[开发约定](AGENTS.md)和[求职流程](SKILL.md)。Python 核心只管本地状态，不连接平台或安装服务。

新增运行文件时更新 `scripts/build.py` 的导出清单。提交前运行：

```sh
python3 -m unittest discover -s tests
python3 scripts/build.py --check
```

测试可观察的结果，如跨进程冲突、恢复后是否重复发送，不用匹配文案代替行为验证。复杂流程另做独立行为检查；离线结果不代表真实平台或模型表现。推送或发布须经维护者授权。

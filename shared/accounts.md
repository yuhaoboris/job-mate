# 账号与平台配置

使用带 account 的事件、guard、check、quota 或筛选历史会话时读本文件。账号是本地标识，不是手机号、密码或授权令牌。

## 配置与解析

```sh
python3 <技能目录>/scripts/workspace.py init <目录> --account 'boss:main:合成显示名'
```

可重复 --account 配置不同平台；格式 platform:accountKey:displayName，三个分量非空，显示名可含冒号。同一平台重复报错；不再支持 boss 专属参数。当前每个平台只配置一个账号，保存于 accounts.<平台> 对象；init 不覆盖已有标记，修改当前账号仍经 setup。

- 事件省略 account 时，取该平台当前唯一配置；显式 null、空白、未知配置结构均报错，不回退到任意别名。
- 新独立动作尝试必须匹配当前配置；缺配置或显式未知账号请先 setup，不能改别名逃避配额。
- 历史账号事件仍可读。明确指定已有历史账号时，可记录事实或回填原尝试终态，不因此获得新投递权限；没有原 pending 的终态仍被拒绝。历史回填不能省略 account 后猜成当前账号。
- check / quota 只针对当前配置账号，均支持 --account；guard 支持 --platform / --account。同一个本地 attemptId 有多个命名空间时，guard 必须消歧；历史账号不匹配当前配置时不能操作。
- threads / stats 可用 --platform / --account 筛选既有历史。未知账号（既无历史也非当前配置）报错；恢复对账仍须先看不带筛选的完整 threads。事项决定保持工作区同一 ID 的全部目标，但步骤与 ack 不会匹配另一账号的目标。

## 命名空间

完整尝试身份是 `(platform, account, attemptId)`；不同平台或账号可有相同本地 ID。恢复收尾、reconciled、unresolved、通知核对与 inbox 都用三元组对照，不能只按 attemptId 合并。旧事项缺平台/账号时先请本人补证，不以当前账号填补历史归属。

threadKey 保持现有外部格式，但其中分量的转义不可逆；例如合法账号 `a|b` 与 `a｜b` 可能有相同的显示键，仍是两个账号。内部尾部状态、base / handled、bridge 的发送证据与邀约 ack 匹配均带原始 platform / account 核对，不靠字符串键认定账号相同。

`tail --thread` 的字符串若命中多个原始平台/账号，退出码 2，且不写新 tail；暂停该键的自动读取并请本人核对歧义，不按默认账号或最后一条身份猜测，不自行改名或改历史。threads 仍按原始账号分别显示各自的 pendingTail / lastHandled，历史账号事实及原尝试的明确终态仍可精确回填。

jobKey 保持原格式（平台加职位 ID，或公司/职位名），实际职位信息与导出行仍按平台、账号、jobKey 隔离。同 jobKey 的另一账号不会贡献 known、问询次数或处理尾部。stats 的分类键为 platform/account/kind（观察为 observed）。

## 有效配置

`effective_limits(profile, platform)` 依次合并：

1. 内置缺省：greetsPerRun=10、greetsPerDay=30、detailsPerRun=50、minSendIntervalSeconds=5、dedupScope=company；inboundScanDays=14、inboundScanPerRun=30、inboundRecheckHours=24。
2. 平台描述 defaultLimits。
3. profile.limits 的全局项，不含 platforms 容器。
4. profile.limits.platforms.<平台>。

check 的去重范围、quota 的额度与间隔使用此结果；quota.limits 展示最终值。计数按平台账号隔离，写过 greet/pending 即占额度，失败或不明不退回；停止名单仍是整个工作区的限制，不因账号不同失效。

`effective_nudge(profile, platform)` 合并内置缺省、profile.nudge 全局项及其 platforms.<平台>：afterReadHours=24、afterUnreadHours=24、intervalHours=48、maxPerSilence=3、maxPerThread=6、perDay=20。maxPerSilence 取1–3，maxPerThread 取 maxPerSilence–9，全部数值为正整数（次数另受上述上限约束）。错误类型、非法范围不静默忽略。

跟进候选与 prepare/guard 见[发送准备](sending.md#主动跟进的最终门禁)；配置计算不授予外发权限。跨平台不去重，配额按原始平台/账号隔离。

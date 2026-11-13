# 工作区

求职数据存放在用户的工作区，与 job-mate 分离。不把真实数据写进 job-mate 目录、提交到 job-mate 仓库或上传外部服务。

工作区以 `job-mate.json` 为标记文件，文件名属于工作区格式契约。本文示例均为虚构数据，不表示默认求职偏好或个人事实。

## 定位

1. 本轮指令中明确指定的路径。
2. 从当前目录逐级向上查找 `job-mate.json`，找到的目录即工作区。
3. 都找不到：只允许只筛选，不读写任何记录；需要发送时请本人运行 `setup` 或指定工作区。**不回退到用户主目录，不自行创建工作区。**

开始时告知工作区的绝对路径；本轮所有读写都在这里，中途不换。`setup-progress.json` 存在且为新建时，工作区初始化尚未完成，不能用于发送；`apply` / `followup` 获取锁被拒绝。草稿读不出时也拒绝；重置草稿不阻断当前配置。

## 套件版本准入

所有数据脚本共用 `job-mate.json.suiteVersion` 门禁：

- 只读始终允许；工作区版本较高时输出 `warnings: ["suite_older_than_workspace"]`（可与其他警告并存）。
- 写入要求主版本相同，且工作区的「主.次」不高于脚本；拒绝时退出码 2，`code` 为 `suite_major_mismatch` 或 `suite_too_old`，先更新整套技能。普通取锁成功后升级较低的工作区版本，不降级标记。
- 恢复只要求主版本相同：`lock acquire --skill setup --recovery`、`lock release`、`pause off`、`workspace commit --recover`、`workspace restore`。恢复锁记 `mode: recovery`，只允许恢复类写入；其他写入（包括 `guard`、续期、进度及通知写入）退出码 2，`code: recovery_lock_only`。只读仍可用。
- `restore` 未传 `--run` 时自动获取恢复锁；传入时必须是有效 setup 锁。恢复保留恢复前的 `suiteVersion`，不采用备份中的旧版本。
- 无标记时，`init` 或显式指定目录的 `progress start --mode new` 可建立骨架；已有标记时均按写入准入，不能借新建绕过保护。`backup`、全部进度修改、通知排队 / 认领 / 回执 / 入站是写入；`outbox` 不带 `--claim` 是只读。

写入、恢复与创建都在 `ledger/.mutation.mutex` 内复查准入，并持锁到变更完成（等待 IM、停止名单等业务锁时也不放开）。创建只在该临界区内确认没有标记时豁免版本检查；仅存在互斥文件不算工作区。只读入口不取中央写锁，不因恢复锁被当作写入拒绝。

## 文件

| 文件 | 内容 | 谁写 |
|---|---|---|
| `job-mate.json` | 标记与元数据：`schemaVersion`、`suiteVersion`、`accounts`、`createdAt` | `setup` |
| `setup-progress.json` | setup 的模式、步骤计划、当前步骤、回答与复核来源，含私人信息 | 只经 `workspace.py progress` 读写 |
| `profile.json` | 筛选条件、限额、授权、招呼语方式 | 仅 `setup` 配置事务；其他技能转交明确变更 |
| `stoplist.yaml` | 停止名单 | 本人手工；技能经 `ledger.py stoplist add` |
| `candidate.md` | 个人事实，沟通和代答的唯一依据 | `setup`、本人 |
| `qa.yaml` | HR 常见问题与回答口径，每条注明 `candidate.md` 中的依据 | `setup`、本人确认后由 `followup` 追加 |
| `rules.md` | 本人补充规则（自由文本）：偏好的理由、排序习惯等 | 本人 |
| `resumes/` | 简历文件与 `index.yaml` | `setup`、本人 |
| `ledger/events.jsonl` | 事件记录，只经 `ledger.py` 读写 | `ledger.py` |
| `ledger/lock.json` | 运行锁 | `ledger.py` |
| `ledger/pause.json` | 全局暂停 | `ledger.py pause`；IM 指令「暂停」「恢复」 |
| `ledger/im.jsonl` | IM 出站与回执、本人的回复及处理状态 | `notify.py` |
| `inbox/decisions.md` | 待本人决定或处理的事项 | 各技能，见 [notify.md](notify.md) |
| `backups/` | 配置文件备份，`setup` 重新设置或修改前生成 | `scripts/workspace.py backup` |

`scripts/workspace.py` 提供 `progress`（见下）、`init`（建立骨架）、`check`（体检：`problems` 必须修复，`warnings` 是缺少的新字段等提示）、`backup` / `backups` / `restore`（备份与恢复配置文件；恢复时保留备份之后新增的停止项，`apply` / `followup` 运行中拒绝恢复）、`changed --since <备份>`（列出备份之后被改动的配置文件）。

## 配置提交与恢复

setup 确认后生成工作区外暂存目录，用 `workspace commit --staging <目录> --run <runId>` 一次提交；停止名单增删可另带 `--stoplist-ops <JSON文件>`，同事务完成。残留 `.job-mate-txn.json` 时先说明，再持 setup 锁执行 `workspace commit --recover --run <runId>`；未恢复不新提交或 restore，apply/followup 取锁也被阻断。完整接口、封闭字段校验、备份清单、损坏停止名单修复和明确放弃见[配置事务](config-transaction.md)。

## setup 进度

步骤与新建 8 屏的唯一定义在 `scripts/workspace.py`（STEPS.screen、SCREENS），菜单见 setup 技能。新建按屏、重设按步骤；只读本人本轮提供的材料，不搜索外部目录。提问遵守 [提问与对话](ask.md)。

| 命令 | 作用 |
|---|---|
| `start --mode new` | 按屏建计划，目录已答；activity、inquiry、pacing 记默认，qa 自动起草待汇总确认；browser 仍询问 |
| `start --mode resetup --base-backup <名>` | 除目录外全部步骤；备份须存在 |
| `start --mode partial --select <阶段或步骤id,…> --base-backup <名>` | 按目录顺序选择，自动带入所选步骤依赖；未知 id 报错 |
| `show` | 模式、计划、回答、复核来源；position/total 新建按屏、重设按步骤；currentScreen 含 number/name/steps/pending，label 为「第 n/8 步 · 屏名」 |
| `record <id> --status changed\|kept\|default\|skipped [--value '<JSON>'] [--source user\|current\|resume\|default]` | 覆盖该步回答，推进到第一个未答步骤；必答不能跳过；联动复核按目录顺序插入，已有回答的复核步骤清除回答、重新待答，记录带入来源 |
| `goto <id>` | 移动当前步骤，保留已有回答 |
| `finish` | 复用体检但不计草稿存在一项；没有其他问题才删除草稿，否则保留并返回问题 |
| `clear` | 放弃或从头开始，不做体检；新建同时删除 `job-mate.json`，返回 `markerRemoved: true`；有事件时保留标记，返回 `false` 与原因；重置不动标记 |

输出 JSON；退出码 0 成功、1 体检未通过、2 错误。草稿已有时 start 返回摘要、不覆盖。clear 规则见表：标记撤销后不再是工作区；有事件保留标记，不宣称已停用。从头开始须重新 init、progress start。

check 对新建草稿报 problems，对重设草稿报 warnings（当前配置仍有效）；损坏时报问题。草稿不进 backup，restore/changed 不动草稿；完成须 finish 后 check，不能 clear 绕过体检。check 只检查工作区数据，环境由 runtime 按执行契约自检。


## job-mate.json

```json
{
  "schemaVersion": 1,
  "suiteVersion": "1.0.0",
  "createdAt": "2026-10-28T10:00:00+08:00",
  "accounts": {
    "boss": {"accountKey": "boss-main", "displayName": "张三"}
  }
}
```

init 用 `--account platform:accountKey:displayName`，不同平台可重复，同平台不可重复；详见[账号说明](accounts.md)。`accountKey` 是本地别名，不是手机号或凭据。操作平台前，核对页面上可见的账号名与 `displayName` 一致；不一致时停止，请本人确认。

## profile.json

技能判断的唯一数据来源，只放可机器判断的条件。理由和细节写在 `rules.md`，数值不在两处重复。下面是结构示意，不是替用户选好的值。

```json
{
  "schemaVersion": 1,
  "updatedAt": "2026-10-28T10:00:00+08:00",
  "targetRoles": ["AI 应用工程师", "前端开发工程师"],
  "locations": ["杭州", "上海"],
  "salary": {
    "currency": "CNY", "period": "month", "taxBasis": "pre_tax",
    "min": 8000, "max": 12000, "matchRule": ["overlap", "lower_bound"]
  },
  "hardRequirements": ["全职"],
  "exclusions": {
    "roleTypes": ["Java 开发", "测试"],
    "employmentTypes": ["外包", "劳务派遣"],
    "workSchedules": ["单休"],
    "keywords": []
  },
  "browser": {"headedInput": false},
  "recruiterActivity": {"maxAgeDays": 7},
  "style": {"tone": "polite", "length": "short", "address": "neutral", "bannedWords": ["精通"], "emoji": false},
  "inquiry": {"priority": ["salary", "hours", "location", "leave"], "maxPerMessage": 2},
  "authorization": {
    "apply": {"send": false},
    "followup": {"reply": "L2", "nudge": true}
  },
  "greeting": {"mode": "dynamic", "text": "您好，我有 N 年……，期待沟通。"},
  "limits": {
    "greetsPerRun": 10, "greetsPerDay": 30, "detailsPerRun": 50,
    "minSendIntervalSeconds": 5, "dedupScope": "company",
    "inboundScanDays": 14, "inboundScanPerRun": 30, "inboundRecheckHours": 24, "platforms": {}
  },
  "nudge": {"afterReadHours": 24, "afterUnreadHours": 24, "intervalHours": 48,
            "maxPerSilence": 3, "maxPerThread": 6, "perDay": 20, "existingMaxAgeDays": 30, "platforms": {}},
  "im": {"channel": "lark", "ownerId": "<本人确认ID>", "larkProfile": "default", "waitMinutes": 60},
  "schedule": {
    "timezone": "Asia/Shanghai",
    "activeHours": {"weekdays": [1, 2, 3, 4, 5], "start": "09:00", "end": "18:00"}
  }
}
```

- `browser` 只允许 `headedInput` 布尔值，缺省 false；表示是否允许占用键盘鼠标的前台输入，不记录工具名，不授予外发权限。能力另按执行契约自检。
- 缺可选字段给 warnings；显式非法值、未知字段给 problems，不用默认掩盖。体检、事务与 ledger 共用 `effective_profile()`，只读合并、不写回；profile 仅支持格式版本 1，不兼容或迁移其他版本。
- `targetRoles`、`locations` 按优先级排列；明确不限地点时用空数组，并在 `rules.md` 说明。
- `salary.matchRule`：`overlap` 两区间有交集；`lower_bound` 岗位下限不低于 `min`；`contained` 岗位区间完全落在用户区间内。可写成数组，表示同时满足。不限薪资时 `salary` 为 `null`；缺上限时 `max` 为 `null`。
- `recruiterActivity.maxAgeDays`：招聘者最近活跃距今超过这么多天的职位不打招呼（多半已暂停招聘、只是没关职位）。平台活跃标签与天数的对应见各平台说明；缺省 7（一周以上不活跃的不打招呼）。
- `exclusions.roleTypes` 按详情中的实际职责判断，不按标题关键词：JD 提到 Java 但职责是前端的，不算 Java 开发岗。
- 正向条件（`targetRoles`、`locations`、`salary`、`hardRequirements`）须从 JD 证实，无法确认时列为待确认、不自动联系；`exclusions` 中的项明确命中才排除，无法确认时可以沟通后核实，但不断言「不含」。公司排除只写在 `stoplist.yaml`。
- `authorization` 长期有效，只在本人明确表态后写入，不从历史行为推断。本轮指令的否定和限制（「只看看」「这次别发」）优先；本轮指令要求超出授权时，本轮可按指令执行，不写回。
- `greeting.mode`：`dynamic`（默认）读完详情后针对职位生成招呼语；`static` 每次发 `text` 原文。招呼语由 Agent 作为第一条消息发送，需在平台关闭自动打招呼，见 `apply` 技能。`text` 是 `setup` 设计、本人认可的通用招呼语，`dynamic` 时作为找不到对应点时的基础。
- `style`：tone 为 polite/friendly/concise，length 为 short/medium，address 为 neutral/surname，emoji 为布尔，bannedWords 为字符串数组（成员非空）。上例为政策默认；`selfIntro` 无事实默认，setup 据 candidate.md 提炼、本人确认后写 1–3 条非空短语。缺少只告警，不生成空数组或事实。
- `inquiry.priority` 是 salary/hours/location/leave/employment 的不重复子集；`maxPerMessage` 为整数 1 或 2，指子项数。缺省见上例。
- `limits` 为非负整数（dedupScope 仅 company/job）；生效顺序为内置 → 平台 defaultLimits → 全局 → platforms 平台覆盖。`nudge` 同样支持平台覆盖；各数值为正整数，maxPerSilence 为 1–3，maxPerThread 为 maxPerSilence–9。两授权独立；判定见[发送准备](sending.md)，配置不等于许可。
- `nudge.existingMaxAgeDays`：盘点时超过此天数的会话只接手、不主动跟进，默认30，正整数；[盘点与恢复规则](inventory.md)。
- `schedule.activeHours` 约束 `apply` 和主动跟进；回复 HR 不受限。
- `im` 见 [notify.md](notify.md)：channel 为 none（只写 inbox，缺省）或 lark。waitMinutes 是后台监听空闲窗口，整数 0–60（非布尔），缺省 60；0 关闭监听。
- 飞书需要非空 ownerId 与 larkProfile；可选 larkCommand 为非空字符串数组。体检与 notify 共用校验，不按 ID 前缀猜身份。无需通知写 `{"channel":"none"}`，缺少 im 等同 none。
- `progress` 仅保存访谈答案，不自动应用到 profile。部分重设只合并所选变更。IM 明确筛选条件仅按[IM 条件事务](im-replies.md)由 setup 提交；定时轮不能自行改条件，两者都不能新增授权。

setup 更新流程：读取 → 仅合并本人明确要求长期保存的变更到暂存 → 持锁 commit → 读回核对。「这次」的修改只用于本轮，不写回。文件损坏或版本不识别时停止并请本人处理，不用空文件覆盖。

## stoplist.yaml

只支持下面这种格式，`ledger.py` 遇到无法识别的行会报错：

```yaml
- name: 示例外包服务有限公司
  aliases: [示例外包]
  reason: 人员外包
  source: 本人
  added: 2026-10-27
```

`aliases` 写成 `[a, b]`；值中有冒号、逗号、方括号、引号或反斜杠时加双引号（按 JSON 规则转义，如 `"ACME \"Labs\""`），也可以用单引号（`''` 表示一个单引号）。手工编辑后运行 `ledger validate` 检查；用 `ledger stoplist add` / `remove` 修改最稳妥。公司名精确命中（忽略空格、全半角、大小写）即停止；名称互相包含算疑似，同样不自动联系。

## candidate.md

个人事实。沟通、代答、补发招呼语只能依据这里写明的内容；未写明的视为未知，不编造，也不把未知说成「没有」。

## qa.yaml

HR 常见问题的回答口径，每条注明 `candidate.md` 出处。`followup` 可依据这里或 `candidate.md` 的明确事实回答；冲突先澄清，承诺仍交本人。

```yaml
- id: experience
  ask: [工作几年, 有多少开发经验]
  answer: 有 3 年 Web 开发经验，主要做订单管理系统。
  basis: candidate.md 第 2 节「示例工作经历」
  avoid: 未确认的学历、客户数量或项目收益不能补充
```

- `ask`：问法示例，用于识别意图，不要求逐字匹配。
- `answer`：回答要点。发送时按会话语境改写成一两句，不机械复制。
- `basis`：依据位置。找不到依据的条目不得使用，应上报本人。
- `avoid`（可选）：不能说的内容。
- 新条目由本人确认后才写入。

## resumes/index.yaml

```yaml
default: fullstack
versions:
  - id: fullstack
    file: fullstack/张三-全栈工程师.pdf
    use: 前后端兼顾、后端为主，拿不准时用
    uploaded: {boss: 张三-全栈工程师.pdf}
  - id: frontend
    file: frontend/张三-前端工程师.pdf
    use: 前端框架、页面、小程序为主
    uploaded: {boss: null}
```

- `file` 相对 `resumes/`；`use` 写选用规则。
- `uploaded` 记各平台附件的实际文件名；`null` 表示尚未确认已上传。BOSS 发简历只能选已上传的附件，Agent 不打开系统文件选择器上传文件。


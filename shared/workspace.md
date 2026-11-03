# 工作区

求职数据存放在用户的工作区，与 plugin 分离。不把真实数据写进 plugin 目录、提交到 plugin 仓库或上传外部服务。

项目现名为 job-mate（职伴）；`job-wingman.json` 是保留的工作区格式标识，新旧工作区都沿用此文件名。

## 定位

1. 本轮指令中明确指定的路径。
2. 从当前目录逐级向上查找 `job-wingman.json`，找到的目录即工作区。
3. 都找不到：只允许只筛选，不读写任何记录；需要发送时请本人运行 `setup` 或指定工作区。**不回退到用户主目录，不自行创建工作区。**

开始时告知工作区的绝对路径；本轮所有读写都在这里，中途不换。`setup-progress.json` 存在且为新建时，工作区初始化尚未完成，不能用于发送；`apply` / `followup` 获取锁被拒绝。草稿读不出时也拒绝；重置草稿不阻断当前配置。

## 文件

| 文件 | 内容 | 谁写 |
|---|---|---|
| `job-wingman.json` | 标记与元数据：`schemaVersion`、`accounts`、`createdAt` | `setup` |
| `setup-progress.json` | setup 的模式、步骤计划、当前步骤、回答与复核来源，含私人信息 | 只经 `workspace.py progress` 读写 |
| `profile.json` | 筛选条件、限额、授权、招呼语方式 | `setup`；本人说「以后……」时由当前技能更新 |
| `stoplist.yaml` | 停止名单 | 本人手工；技能经 `ledger.py stoplist add` |
| `candidate.md` | 个人事实，沟通和代答的唯一依据 | `setup`、本人 |
| `qa.yaml` | HR 常见问题与回答口径，每条注明 `candidate.md` 中的依据 | `setup`、本人确认后由 `followup` 追加 |
| `rules.md` | 本人补充规则（自由文本）：偏好的理由、排序习惯等 | 本人 |
| `resumes/` | 简历文件与 `index.yaml` | `setup`、本人 |
| `ledger/events.jsonl` | 事件记录，只经 `ledger.py` 读写 | `ledger.py` |
| `ledger/lock.json` | 运行锁 | `ledger.py` |
| `ledger/pause.json` | 全局暂停 | `ledger.py pause`；IM 指令「暂停」「恢复」 |
| `ledger/im.jsonl` | IM 出站与回执、本人的回复及处理状态、无人值守唤起的 claim / finish 记录 | `notify.py` |
| `inbox/decisions.md` | 待本人决定或处理的事项 | 各技能，见 [notify.md](notify.md) |
| `backups/` | 配置文件备份，`setup` 重新设置或修改前生成 | `scripts/workspace.py backup` |

`scripts/workspace.py` 提供 `progress`（见下）、`init`（建立骨架）、`check`（体检：`problems` 必须修复，`warnings` 是缺少的新字段等提示）、`backup` / `backups` / `restore`（备份与恢复配置文件；恢复时保留备份之后新增的停止项，`apply` / `followup` 运行中拒绝恢复）、`changed --since <备份>`（列出备份之后被改动的配置文件）。

工作区若在 Git 仓库内，`workspace.py init` 会把 `ledger/`、`inbox/`、`backups/` 与 `/setup-progress.json` 加入 `.gitignore`（按工作区相对仓库根目录的路径加前缀）；已有工作区由 `progress start` 补齐，不重复添加。确认简历等其他私人数据是否提交由本人决定。已被跟踪的文件不会因忽略规则自动移除，要提醒本人。

## setup 进度

`workspace progress` 的命令如下。步骤 id 与阶段菜单见 setup 技能，目录的唯一定义在 `scripts/workspace.py`。

| 命令 | 作用 |
|---|---|
| `start --mode new --variant typical\|custom` | 新建完整计划，目录自动已答；典型的活跃度、限额时段记默认 |
| `start --mode resetup --base-backup <名>` | 除目录外全部步骤；备份须存在 |
| `start --mode partial --select <阶段或步骤id,…> --base-backup <名>` | 按目录顺序选择，自动带入材料依赖；未知 id 报错 |
| `show` | 模式、计划、当前步骤、第 n/N 步、回答及追加的复核步骤 |
| `record <id> --status changed\|kept\|default\|skipped [--value '<JSON>'] [--source user\|current\|resume\|legacy\|default]` | 覆盖该步回答，推进到第一个未答步骤；必答不能跳过；联动复核按目录顺序插入，已有回答的复核步骤清除回答、重新待答，记录带入来源 |
| `goto <id>` | 移动当前步骤，保留已有回答 |
| `finish` | 复用体检但不计草稿存在一项；没有其他问题才删除草稿，否则保留并返回问题 |
| `clear` | 放弃或从头开始，不做体检；新建同时删除 `job-wingman.json`，返回 `markerRemoved: true`；有事件时保留标记，返回 `false` 与原因；重置不动标记 |

输出一个 JSON 对象；退出码 0 成功、1 体检未通过、2 错误。草稿已有时 `start` 报错并返回摘要，不覆盖。本人可稍后继续或确认放弃。新建 `clear` 删除草稿与工作区标记，目录与其他文件保留；标记撤销后该目录不再是工作区，`ledger` / `workspace` 找不到它。若已有事件则保留标记并说明原因，不宣称工作区已停用。新建从头开始时，`clear` 后须重新 `init`、`progress start`。

`check` 对新建草稿报 `problems`（初始化未完成，第 n/N 步），对重新设置或部分重新设置草稿报 `warnings`（当前配置仍有效）；损坏草稿报问题并建议本人确认放弃。草稿不属于配置文件，`backup` 不包含它，`restore` 和 `changed` 不动它。完成应使用 `finish` 后再 `check`，不要用 `clear` 绕过体检。`workspace check` 只检查工作区数据，不检查或安装服务、不检查执行环境。环境能力由实际 runtime 按执行契约自检；原环境检测选项已删除。

旧 setup 草稿保持原计划和 browser、im、listener 的原答案，不自动插入 browser；继续可正常完成，goto / record browser 在不含它的计划上报错，提示完成后另做只选 browser 的部分重新设置。步骤 id 与顺序不变，listener 的显示名改为「无人值守唤起」。该步只记录本人决定，`show` / `record` / `finish` 的 `unapplied` 列表提示答案「未应用」；不会把旧监听器答案变成 runtime 调度配置，也不读写旧服务配置或安装任何东西。无人值守执行须由 runtime 保证单实例、超时、进程树清理和防睡眠，并遵守 [notify.md](notify.md) 的 trigger 契约；做不到时不设置无人值守唤起。

## job-wingman.json

```json
{
  "schemaVersion": 1,
  "createdAt": "2026-10-28T10:00:00+08:00",
  "accounts": {
    "boss": {"accountKey": "zhipin-main", "displayName": "张三"}
  }
}
```

`accountKey` 是本地别名，不是手机号或凭据。操作平台前，核对页面上可见的账号名与 `displayName` 一致；不一致时停止，请本人确认。

## profile.json

技能判断的唯一数据来源，只放可机器判断的条件。理由和细节写在 `rules.md`，数值不在两处重复。下面是结构示意，不是替用户选好的值。

```json
{
  "schemaVersion": 2,
  "updatedAt": "2026-10-28T10:00:00+08:00",
  "targetRoles": ["AI 应用工程师", "前端开发工程师"],
  "locations": ["东莞", "深圳"],
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
  "browser": {"control": "agent"},
  "recruiterActivity": {"maxAgeDays": 7},
  "authorization": {
    "apply": {"send": false},
    "followup": {"level": "L2", "legacyThreads": "auto"}
  },
  "greeting": {"mode": "dynamic", "text": "您好，我有 N 年……，期待沟通。"},
  "limits": {
    "greetsPerRun": 10, "greetsPerDay": 30, "detailsPerRun": 50,
    "minSendIntervalSeconds": 5, "dedupScope": "company",
    "legacyScanPerRun": 20, "legacyMaxAgeDays": 30, "legacyFollowupBudget": 1
  },
  "im": {"channel": "runtime", "ownerId": "<本人在通道的发送者 ID>", "waitMinutes": 60,
         "transport": {"idempotencyWindowSeconds": 0}},
  "schedule": {
    "timezone": "Asia/Shanghai",
    "activeHours": {"weekdays": [1, 2, 3, 4, 5], "start": "09:00", "end": "18:00"}
  }
}
```

- `browser` 记录执行偏好：缺失时不报 warning、不写入默认值，由 runtime 按通用执行契约确认能力。`{"control":"agent"}` 表示由 runtime 决定，只允许 control 字段；显式 `null`、空对象、非对象、缺 control、非法取值或未知字段均报 problems，不操作平台、不回退。
- 兼容旧值 `{"control":"agent-desktop","input":"background"}`：必须有 input，只能 background / foreground，不能有未知字段。这只是必须遵守的旧执行偏好，核心不调用该工具；runtime 无法满足时不操作平台、不回退，不因失败切换控制方式或输入方式重发。
- browser 步只问执行偏好，不推荐具体工具、不预先记默认；本人选定后才写。新建跳过不写；重置时保留 / 跳过保留当前值（包括缺失），未选中不动，旧草稿不插步骤、不改 browser。长期授权与本轮禁发另设。
- `targetRoles`、`locations` 按优先级排列；明确不限地点时用空数组，并在 `rules.md` 说明。
- `salary.matchRule`：`overlap` 两区间有交集；`lower_bound` 岗位下限不低于 `min`；`contained` 岗位区间完全落在用户区间内。可写成数组，表示同时满足。不限薪资时 `salary` 为 `null`；缺上限时 `max` 为 `null`。
- `recruiterActivity.maxAgeDays`：招聘者最近活跃距今超过这么多天的职位不打招呼（多半已暂停招聘、只是没关职位）。平台活跃标签与天数的对应见各平台说明；缺省 7（一周以上不活跃的不打招呼）。
- `exclusions.roleTypes` 按详情中的实际职责判断，不按标题关键词：JD 提到 Java 但职责是前端的，不算 Java 开发岗。
- 正向条件（`targetRoles`、`locations`、`salary`、`hardRequirements`）须从 JD 证实，无法确认时列为待确认、不自动联系；`exclusions` 中的项明确命中才排除，无法确认时可以沟通后核实，但不断言「不含」。公司排除只写在 `stoplist.yaml`。
- `authorization` 长期有效，只在本人明确表态后写入，不从历史行为推断。本轮指令的否定和限制（「只看看」「这次别发」）优先；本轮指令要求超出授权时，本轮可按指令执行，不写回。
- `greeting.mode`：`dynamic`（默认）读完详情后针对职位生成招呼语；`static` 每次发 `text` 原文。招呼语由 Agent 作为第一条消息发送，需在平台关闭自动打招呼，见 `apply` 技能。`text` 是 `setup` 设计、本人认可的通用招呼语，`dynamic` 时作为找不到对应点时的基础。
- `authorization.followup.legacyThreads`：`auto`（缺省）按规则直接登记没有遗留问题的旧会话候选；`ask` 将候选汇总为批量待决定；`off` 不查找我方最后发言的旧会话。仅 L3 查找这类旧会话，职位待确认或有遗留事项的仍不是候选，HR 有未答消息的入口不变。缺少字段时按 `auto` 处理，本人可以改成 `ask` / `off`；已有设置不变，IM 不新增授权。
- `limits.legacyScanPerRun`、`legacyMaxAgeDays`：每轮点开旧会话上限和扫描日期范围，非负整数，缺省 20 / 30；`legacyFollowupBudget` 是登记后最多再主动跟进次数（0–3 整数，缺省 1，0 仅登记不主动跟进）。额度登记时固化，以后改设置不影响已登记会话；我方连续 3 条没回的不再跟进。
- setup 不增步骤：L3 时在 followupLevel 内追问旧会话处理和额度，非 L3 不追问。答案没带新字段时保留 profile 原值，原来也没有才补 auto / 1；改级别与合并 pacing 默认不能覆盖已有设置。体检缺字段只给 warnings，非法取值给 problems。
- `limits` 缺省时用上面的默认值。
- `schedule.activeHours` 约束 `apply` 和主动跟进；回复 HR 不受限。
- `im`：与本人沟通的通道，见 [notify.md](notify.md)。`channel` 为 `none`（只写本地 inbox，缺省）、`runtime` 或兼容旧值 `lark`；`waitMinutes` 为处理完会话后由 runtime 等待本人答复的最长分钟数，0–240 的整数（非布尔值），`0` 表示不等待。
- `runtime` 必须有非空字符串 `ownerId`；可选 `transport` 必须是可 JSON 序列化的对象，其他字段作为不透明数据交给 runtime。核心只读取其中的 `idempotencyWindowSeconds`：0–86400 的整数（非布尔值），缺省为 0，不假定通道支持幂等。只有本人确认 runtime 提供的幂等保证后，setup 才把该窗口写入；IM 测试经排队、runtime 认领投递、回执确认，排队不等于发送成功。
- 兼容旧 `{"channel":"lark","userId":"ou_…","larkProfile":"…"}`：`userId` 是本人原通道的 open_id，兼容读取为 ownerId，transport 为 `{"kind":"lark","profile":<larkProfile>}`，幂等窗口按 0 处理，不默认 3600。体检只给迁移 warning，不改写文件；旧 `larkProfile` 字段与原 profile 配置保留。重新设置的 im 步提供「保留兼容 / 迁移为 runtime」，本人明确选择后才合并相应变更，不擅自删除旧字段。旧 `notify` 字段已停用，体检提示由 setup 显式迁移。
- `progress` 仅保存访谈答案，不把答案自动应用到 profile。部分重新设置只合并所选领域的本人确认变更；未选字段、授权、旧通道配置均保留。IM 与无人值守唤起不能新增授权或修改求职条件。

更新流程：读取 → 只合并本人明确要求长期保存的变更 → 保存 → 读回核对。「这次」的修改只用于本轮，不写回。文件损坏或版本不识别时停止并请本人处理，不用空文件覆盖。

## stoplist.yaml

只支持下面这种格式，`ledger.py` 遇到无法识别的行会报错：

```yaml
- name: 软通动力信息技术(集团)股份有限公司
  aliases: [软通动力, iSoftStone]
  reason: 人员外包
  source: 本人
  added: 2026-10-27
```

`aliases` 写成 `[a, b]`；值中有冒号、逗号、方括号、引号或反斜杠时加双引号（按 JSON 规则转义，如 `"ACME \"Labs\""`），也可以用单引号（`''` 表示一个单引号）。手工编辑后运行 `ledger validate` 检查；用 `ledger stoplist add` / `remove` 修改最稳妥。公司名精确命中（忽略空格、全半角、大小写）即停止；名称互相包含算疑似，同样不自动联系。

## candidate.md

个人事实。沟通、代答、补发招呼语只能依据这里写明的内容；未写明的视为未知，不编造，也不把未知说成「没有」。

## qa.yaml

HR 常见问题的回答口径。`followup` 只在这里有依据时直接回答；每条都要指向 `candidate.md` 的出处。

```yaml
- id: education
  ask: [学历, 是不是本科, 统招吗, 全日制吗]
  answer: 大专，计算机应用技术；有 14 年一线开发和团队交付经验，可以用项目和技术面验证。
  basis: candidate.md 第 1 节「学历」
  avoid: 全日制、学信网、证书编号未确认，不要答「是」
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


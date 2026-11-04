---
name: setup
description: 引导建立、重新设置、部分重新设置或修改 job-mate 求职工作区，支持中断后继续 setup：访谈求职偏好与授权，整理个人事实、简历索引、HR 问答库和停止名单，设计招呼语，导入已有求职记录，体检工作区。当用户说“开始求职”“建立求职工作区”“重新 setup”“重新设置”“部分重新设置”“继续 setup”“重置”“从头再来一遍”“设置求职偏好”“修改筛选条件”“改授权”“检查工作区”“导入求职记录”“设置飞书通知”“配置 IM”“设置无人值守唤起”“检查通知配置”，或 apply / followup 报告找不到工作区、工作区有问题时使用。不在招聘平台上外发任何消息。
---

# setup：建立、重新设置或修改求职工作区

产出一个 `apply`、`followup` 可以直接使用的工作区。工作区结构和各文件格式见 [../../shared/workspace.md](../../shared/workspace.md)，先读它，同时读 [通用执行契约](../../shared/execution.md) 与 [通知契约](../../shared/notify.md)。本技能不在招聘平台外发；需要看平台页面时只读。任何写入加锁前先做能力自检：能读写工作区、运行核心脚本才继续；缺能力时不写记录、不用手工改台账替代，只汇报。纯配置操作不要求桌面能力；要只读平台时另核对可见浏览器和选定偏好，不支持不操作平台、不回退。

下文的 `ledger`、`workspace`、`notify` 分别指 `python3 <本文件上两级目录>/scripts/ledger.py`、`.../scripts/workspace.py` 和 `.../scripts/notify.py`，先解析成绝对路径再调用。

## 1. 确定模式与目录

| 情况 | 模式 |
|---|---|
| 工作区里有 `setup-progress.json` | **继续**：先 `workspace progress show`，告知「上次的 <模式> 进行到第 n/N 步 · <步骤名>」，请本人选择继续、从头开始或放弃草稿 |
| 没有工作区 | **新建**：确认目录后选择「典型」或「自定义」 |
| 已有工作区，本人要「重新 setup」「重新设置」「从头再来」 | **重新设置**：除目录外全部步骤从头过一遍，每步预填当前值 |
| 本人要重新设置某几部分，或说要重设但没说全部还是部分 | **部分重新设置**：展示第 2 节的阶段与步骤菜单，请本人用编号选择 |
| 已有工作区，本人只改某几项（「把深圳去掉」「改成 L3」） | **修改**（第 4 节），不走引导流程 |
| 本人要检查工作区，或 `apply` / `followup` 报告工作区有问题 | **体检**（第 5 节），只读 |

「重新设置」与「部分重新设置」统称重置。本人只说「重置」时，先问要全部还是部分。**事件记录不动**，不提供清空事件记录重来的选项；停止名单只按本人明确要求逐条增删。

目录：本人指定的优先；否则从当前目录向上查找 `job-mate.json`。新建时建议使用当前目录，说出绝对路径，**本人确认后**再创建。不在用户主目录根部、job-mate 自身目录或明显的代码项目中建立工作区。

## 2. 步骤目录

目录的唯一定义在 `scripts/workspace.py`，下面的步骤 id 与顺序由测试核对。向本人显示中文名。每步按「内容规则」引用第 6–10 节，不把所有问题一次问完。

| 阶段 | 步骤 id | 名称 | 写入位置 | 必答 | 典型模式 | 内容规则与依赖 |
|---|---|---|---|---|---|---|
| `prepare` 准备 | `directory` | 工作区目录 | — | 是 | 问 | 仅新建；开始时自动记为已答 |
| | `materials` | 现成材料 | — | 否 | 问 | 第 6 节；读取后为后续步骤预填 |
| | `account` | 平台账号 | `job-mate.json` | 是 | 问 | 第 7 节；BOSS 显示的名字与本地别名 |
| | `browser` | 浏览器操作 | `profile.browser` | 否 | 问 | 第 6.1 节；只问偏好，不预先记默认 |
| `criteria` 求职条件 | `roles` | 目标职位 | `profile.targetRoles` | 是 | 问 | 按优先级；第 6、7 节 |
| | `locations` | 城市 | `profile.locations`、`rules.md` | 是 | 问 | 「不限」有效；第 6、7 节 |
| | `salary` | 薪资 | `profile.salary` | 否 | 问 | 区间、税前税后、月薪年薪、`matchRule` 合为一步 |
| | `requirements` | 硬性要求 | `profile.hardRequirements` | 否 | 问 | 第 6、7 节 |
| | `exclusions` | 排除条件 | `profile.exclusions` | 否 | 问 | 职位类型、用工形式、工作制度、关键词 |
| | `companies` | 排除的公司 | `stoplist.yaml` | 否 | 问 | 第 7 节；重置先列现有条目，只按本人明确要求增删 |
| | `activity` | 招聘者活跃度 | `profile.recruiterActivity` | 否 | 默认 7 天 | 第 7 节 |
| `facts` 个人事实 | `candidate` | 个人事实 | `candidate.md` | 是 | 问 | 材料起草，逐节确认，可分多轮；部分重置自动带入 `materials` |
| | `resumes` | 简历版本 | `resumes/index.yaml` | 是 | 问 | 选用规则与平台附件名；部分重置自动带入 `materials` |
| | `qa` | 问答口径 | `qa.yaml` | 否 | 问 | 第 7 节；生成后整体过目，列出缺少依据的问题 |
| `outreach` 沟通与授权 | `greeting` | 招呼语 | `profile.greeting` | 是 | 问正文，方式默认 `dynamic` | 第 8 节；提醒本人关闭平台自动打招呼 |
| | `applyAuth` | 发起沟通授权 | `profile.authorization.apply` | 否 | 问 | 第 6 节；跳过为 `send: false` |
| | `followupLevel` | 代答级别 | `profile.authorization.followup` | 否 | 问 | 第 6 节；跳过为 `L2` |
| | `pacing` | 限额与时段 | `profile.limits`、`profile.schedule` | 否 | 默认值 | 第 7 节与 workspace.md |
| `notify` 通知 | `im` | IM 通知 | `profile.im` | 否 | 问 | 第 10 节；不需要时记 `channel: none`，把计划中的 `listener` 记为跳过 |
| | `listener` | 无人值守唤起 | setup 草稿（runtime 决定） | 否 | 问 | 第 10 节；只记录决定，不安装；旧答案明确未应用 |

新建包含全部步骤。典型模式在开始时把 `activity`、`pacing` 记为默认，汇总时列出，本人可以改；自定义逐步提问。重新设置不含 `directory`。部分重新设置只含所选阶段或步骤与依赖，按目录顺序排列。

重置时 `candidate` 记为「已改」，复核 `qa`（逐条核对 `basis`）、`greeting`（核对事实）；`roles` 记为「已改」，复核 `greeting`。不在计划中的按目录顺序插入；已在计划中且已答的清除回答，重新待答；两种情况都记录带入来源。按脚本返回的第一个未答步骤继续。最后的确认与写入不算步骤。

## 3. 引导流程

### 3.1 开始与接续

- 新建：目录经本人确认 → `workspace init <目录>`（账号可在 `account` 步补齐）→ 请本人选典型或自定义 → `workspace progress start --mode new --variant typical|custom`。`init` 建立骨架，不覆盖已有文件。
- 重新设置：`workspace backup --label resetup` → 告诉本人备份名与恢复方法 → `workspace progress start --mode resetup --base-backup <备份名>`。
- 部分重新设置：`workspace backup --label partial` → 告诉本人备份名与恢复方法 → 展示阶段/步骤菜单，请本人选择 → `workspace progress start --mode partial --select <阶段或步骤id,…> --base-backup <备份名>`。
- 有草稿：先询问本人。继续则用 `progress show` 的当前步骤与已有回答接续；从头开始则经本人确认后 `progress clear`，重新进入原模式：新建重新 `workspace init`、选择典型或自定义、`progress start`；重置重新备份，部分重置重新选步骤。放弃则确认后 `progress clear`，按 `markerRemoved` 汇报是否撤销工作区标记；有事件时标记保留，说明原因，不宣称该目录已停用。草稿损坏时报告问题，请本人决定是否放弃，不自行覆盖。

`workspace restore <备份名>` 可恢复重置前配置，保留备份之后新增的停止项。恢复期间持有运行锁，有其他任务运行时拒绝（setup 自己持锁时传 `--run <runId>` 沿用）。草稿不进备份，恢复也不动草稿。返回结果中的 `restoreSucceeded` / `restoreError` 表示恢复主体，`release` / `releaseError` 表示释放是否尝试、是否成功及错误；二者任一失败都返回退出码 2，并保留原 `restored`（已恢复文件）、`previousSavedAs` 等字段，不能把释放失败说成配置未变。沿用 setup 锁时 `release.attempted` 为 false，由本技能最后释放；独立恢复的输出 `reconciled` 汇总获取与释放两端，按 [释放与对账契约](../../shared/ledger.md#释放与对账契约) 处理。成功恢复也仍可能因释放失败而未完全收尾；两项错误分别汇报，不擅自删除锁。

### 3.2 一次问一步

```text
第 7/20 步 · 求职条件 · 薪资
当前值：税前月薪 15–25K，有交集即可（来自：现有配置）
……一个问题，必要时附编号选项……
可以回答：保留 / 默认 / 跳过 / 上一步 / 稍后再说
```

显示脚本返回的第 n/N 步、阶段、中文名。字段紧密相关时合成一个问题（如薪资区间与口径），不把不相关事项并到一步。当前值或预填值注明来源：现有配置、简历、旧数据、默认值。选项用编号纯文本，不依赖某个 Agent 特有的提问工具。

本人顺带说到后面步骤时先记为预填；到那一步时请本人确认，不从头问。答完先复述解析结果（「记为：税前月薪 15–25K，有交集即可」），记录后进入下一步。不逐步追问「对吗」，统一汇总确认。

| 回答 | 处理 |
|---|---|
| 具体内容 | `progress record <id> --status changed --value '<JSON>' --source user`；与当前值相同则 `kept` |
| 保留 | 重置沿用当前值，记 `kept`、来源 `current` |
| 默认 | 采用该步骤默认值，记 `default`、来源 `default` |
| 跳过 | 非必答项记 `skipped`，标为未知或保留缺省；必答项说明原因后重问 |
| 上一步 / 回到第 n 步 | 按计划找到 id，`progress goto <id>`；已有回答保留，重答才覆盖 |
| 稍后再说 | 确保已答步骤落盘，结束本轮，告诉本人下次说「继续 setup」 |
| 放弃 | 本人确认后 `progress clear`。新建同时删除 `job-mate.json`，目录与其他文件保留，该目录不再是工作区，以后须重新 setup；有事件时不删标记，按输出说明原因；重置不动标记 |

草稿只用 `workspace progress` 读写。`record` 的 `--value` 是合法 JSON，脚本不校验取值结构；来源还可用 `resume`、`legacy`。生成文件与含义核对仍由 Agent 按第 6–10 节执行。`show` 返回模式、计划、当前步骤、序号、回答与追加来源；`goto` 后重答继续向未回答项推进。

### 3.3 确认与写入

1. 汇总：新建按阶段列全部取值；重置列与备份相比每个文件的改动。标出默认、跳过与未知项。本人说「改第 n 步」时用 `progress goto` 跳回。已有工作区缺少的字段在对应步骤按默认值补齐并告知（browser 例外，只按第 6.1 节本人选择写入，不补默认）；遇到停用的 `notify` 字段时，展示转为 `im` 的差异，经本人确认再写入。
2. **本人确认后** `ledger lock acquire --skill setup`；被其他任务占用时等其结束，不抢锁。持锁后先按 [释放与对账契约](../../shared/ledger.md#释放与对账契约) 完成入口对账：即使 acquire 的 `reconciled` 为空，也检查完整 `ledger threads` 的 `unresolved` 与 inbox，只做本地对账与通知，不操作平台。重置再 `workspace changed --since <备份名>`：期间有变化时重读、合并并重新展示差异，经本人确认再继续。
3. 按第 7 节写入除停止名单外的文件，每写一个读回核对。停止名单不整体重写，只用 `ledger stoplist add` / `remove` 逐条执行批准的增删。完成后 `ledger lock release --run <runId>`，按 [释放与对账契约](../../shared/ledger.md#释放与对账契约) 处理 `reconciled` 与释放失败；出错也尝试按此契约收尾，保留草稿，分别报告主体与释放结果，释放未确认时停止，不宣称完全收尾。
4. 配置写入后才做经本人另行同意的数据导入（第 9 节，先 `--dry-run`）与 IM 全链路测试（第 10 节）；不安装执行器或服务。旧 listener 答案保留在原草稿，汇总明确「未应用」，不据旧答案自动配置唤起。
5. `workspace progress finish` 复用体检但不计草稿存在这一项。退出码 0 表示通过并删除草稿；1 表示有问题、草稿保留，按问题回到对应步骤；2 表示命令或草稿错误，先报告处理。`progress clear` 只用于放弃或从头开始，不是完成。之后再 `workspace check` 确认数据配置；runtime 能力与环境另行报告，配置通过不代表实际收发、平台操作或正式验收通过。

## 4. 修改

「旧会话自动纳入」改 `authorization.followup.legacyThreads: auto`，「旧会话不要管」改为 `off`，「旧会话最多跟进 2 次」改 `limits.legacyFollowupBudget: 2`；额度只影响以后登记的会话。

读取 → 只改本人明确要求的字段 → 保存 → 读回核对，并说明改了什么。不重新访谈，不动其他文件。改动涉及授权、停止名单、`candidate.md` 时，先 `workspace backup --label edit`。保存前按第 3.3 节第 2–3 步加锁；停止名单只用 `ledger stoplist add` / `remove` 修改。

## 5. 体检

`workspace check` 和 `notify status` 只读检查数据：配置、暂停、outbox / stale / lateSends、私聊和可读边界、未结束 launch、legacyUnbound / otherRoutes / needsReconcile 及 replyRecovery。不能凭本地状态声称远端连通或服务运行。逐条解释 problems 与 warnings，不静默补字段、重绑身份或认领旧答复；本人同意后按第 4 节修复，或建议重新设置。

## 6. 材料与取值规则

`materials` 步先找现成材料，供后续步骤预填，避免让本人重复输入：

- 本人指定的已有求职配置与记录；需要导入 `applications.jsonl` 时按第 9 节处理。
- 已有的简历、个人事实文档、求职规则文档。
- 本人在对话中提供的信息。

缺少的信息按第 2 节逐步问，只问影响后续判断的。`salary` 步问区间与口径，例如税前月薪 15–25K，是有交集即可还是岗位下限至少 15K。`resumes` 步确认平台附件名。`followupLevel` 步解释 L1 只起草、L2 有依据就答、L3 还会主动跟进。

选 L3 时在现有 `followupLevel` 步骤内追问旧会话处理（`legacyThreads: auto` 自动登记（缺省）/ `ask` 逐批问 / `off` 不查找）与登记后最多再主动跟进几次（`legacyFollowupBudget` 缺省 1、可选 0–3）；说明我方已连续 3 条没回的不再跟进，纳入不放宽职位与承诺边界。非 L3 不追问；不新增步骤，已有草稿按原计划接续。答案是 `profile.authorization.followup` 对象（如 `{"level":"L3","legacyThreads":"auto"}`）；额度另合并到 `profile.limits`。

合并时答案没带这两项（未追问或旧草稿）须保留 profile 已有值；原来没有才补 auto / 1。修改授权级别、采用典型 pacing 默认或合并旧草稿，都不能把已有 ask / off 与额度覆盖成默认。旧会话登记方式缺省为 auto；汇总时告诉本人缺省是 auto，可以改成 ask / off，已有设置不变。其他授权仍只在本人明确表态后写入。

- 「不限」是明确条件；没回答的标为未知，不臆造。
- 区分硬条件和偏好：「必须」「不要」「至少」进 `profile.json`；「优先」「最好」写进 `rules.md` 或体现为列表顺序。
- 授权必须由本人明确表态才写入 `authorization`；没表态时 `apply.send` 为 `false`，`followup.level` 为 `L2`。

### 6.1 浏览器操作（browser）

先读 [浏览器入口](../../shared/browser/README.md) 与 [加锁前能力自检](../../shared/execution.md#1-加锁前能力自检)。只询问执行偏好，不推荐具体工具，也不安装或检测某个执行器。BOSS 只用本人日常已登录的可见浏览器，通过辅助功能树 / 桌面 UI；不使用 CDP / Playwright 或内部接口。后台不抢焦点也不能同时操作目标窗口；前台输入还须独占键盘鼠标。

默认表达为「由当前 runtime 决定」（`control: agent`），但不预先记默认；本人保留旧 `control: agent-desktop` 时只作为兼容偏好解释，input 仍须明确为 background / foreground。当前 runtime 不能满足所选偏好就不操作平台、不回退。缺失配置不报错、不强制补设；不合法配置先修复，不静默改选。

「只读」由 applyAuth / followupLevel 的授权表达（apply.send: false、L1），一轮完全禁发包括旧批准，用「只看看」与 `--no-send` 锁，不作为第三种输入方式。按 job 去重而列表链接读不到时 jobUrl / jobId 为 null，按 ledger 规则去重，不从其他接口补取。

progress 只记录回答，profile 合并由第 3.3 节写入阶段完成：

| 情况 | profile.browser |
|---|---|
| 新建，本步选定 | 写所选值：agent 只有 control；agent-desktop 含 control 与 background / foreground 的 input |
| 新建，跳过 | 不写，保持缺失 |
| 重新设置 / 部分重新设置，本步「改」 | 写新值 |
| 重新设置 / 部分重新设置，本步「保留」或「跳过」 | 保留当前值，包括缺失状态；不删除、不补默认 |
| 部分重新设置未选本步 | 不动 |
| 继续不含本步的旧草稿 | 不增、不删、不改当前 browser |

旧草稿原计划保持不变，不自动插入 browser；本人想设置时，完成后另做只选 browser 的部分重新设置。旧计划的 goto browser / record browser 报错时照此解释，不能改写草稿或用新计划替换。

## 7. 生成文件

各步骤先按下表起草，确认与写入阶段才按 workspace.md 格式逐个保存，每写一个读回核对。`candidate` 步由简历与现成材料起草，逐节确认新经历、新数字与未确认项；`qa` 步按更新后的事实逐条核对 `basis`，失去依据的列出请本人决定。

| 文件 | 来源与要点 |
|---|---|
| `job-mate.json` | `accounts` 中的 `displayName` 取本人在平台上显示的名字，由本人告知或只读查看 |
| `profile.json` | 可机器判断的条件、限额、授权、招呼语方式；未提供的限额用默认值 |
| `stoplist.yaml` | 用 `ledger stoplist add` 逐条添加，每条写明原因和来源。「待核实、不自动联系」的公司也写进来，并在 `reason` 中注明 |
| `candidate.md` | 个人事实：基本信息、技术栈分层（生产主力 / 用过 / 个人实践）、明确缺少的经历、工作经历、可公开的数字、未确认项。只写本人提供或简历中有依据的内容 |
| `qa.yaml` | 依据 `candidate.md` 生成常见问题口径：学历、年龄、离职原因、期望薪资、技术栈、AI 经验、缺少的经历、驻场出差、联系方式婉拒等。每条写 `basis`；找不到依据的问题不生成，列入汇报请本人补充 |
| `rules.md` | 偏好的理由和排序习惯，例如方向占比、行业优先级、地点细分、公司类型偏好。数值不与 `profile.json` 重复 |
| `resumes/index.yaml` | 各版本文件、选用规则、各平台已上传的附件名；不确定是否已上传时记 `null` |

已有同名文件时不直接覆盖：先给本人看差异，确认后再写。

## 8. 招呼语

在 `greeting` 步执行，先收集正文与方式，确认写入阶段再保存：

1. 依据 `targetRoles` 和 `candidate.md` 设计一段通用招呼语，存为 `greeting.text`：不超过 80 字，包含与目标职位相关的真实经历或数字，以邀请沟通结尾。每个事实都要能在 `candidate.md` 中找到。
2. 请本人选择方式，写入 `greeting.mode`，默认 `dynamic`：
   - `dynamic`（动态）：`apply` 读完每个职位的详情后生成针对性的招呼语；`greeting.text` 用于找不到对应点的职位。
   - `static`（静态）：每次都发 `greeting.text` 原文。
3. 请本人在招聘平台 App 中关闭「自动打招呼」，让 Agent 发的招呼语成为第一条消息。本人不愿关闭时，请其把平台默认招呼语改成通用短句（如「您好，对这个职位很感兴趣，想进一步了解」），并对照 `candidate.md` 检查有无不符的说法；`apply` 会在其后补发。
4. 修改平台设置由本人完成，Agent 不改账号设置。

## 9. 导入已有求职记录

仅在本人要求导入时使用。源格式为 `applications.jsonl`（`apply-job-boss` 格式）；`materials` 步只读取本人指定的材料并记录导入意向，配置写入后经本人同意再执行：

1. `ledger migrate <旧 applications.jsonl> --dry-run`，向本人报告条数和各状态数量。
2. 没有错误时去掉 `--dry-run` 正式导入。目标已有记录时 `ledger` 会拒绝，先请本人核对。
3. 把旧文件复制一份到 `ledger/legacy/` 留底。
4. 旧数据目录保持原样，不删除、不修改；由本人在确认新工作区可用后自行处理。

## 10. IM 通知与无人值守唤起

机制与接口统一见 [通知与 IM](../../shared/notify.md)。im / listener 步只访谈、记录本人决定，不调用具体通道工具或安装服务。本人不需要通知时设 `channel: none`，计划中的 listener 记 skipped，只写本地 inbox。

### 10.1 通道选择与旧配置

1. 请本人选 none、由当前 runtime 接入，或保留已有兼容 lark 配置。旧 lark 明确提供「保留兼容 / 显式迁移为 runtime」两项；未获确认不改写。旧草稿 im 回答保留、读回展示，经本人核对再合并，不从旧答案推断当前 runtime 已支持。
2. runtime 配置需要本人确认 ownerId（可信通道元数据中的本人 ID）、waitMinutes 和可选 transport 对象；owner / transport / 应用身份不猜、不从正文自称取值。示例结构：`{"channel":"runtime","ownerId":"<本人确认ID>","waitMinutes":60,"transport":{"kind":"<已确认接入>","idempotencyWindowSeconds":0}}`。幂等窗口默认 0；仅本人明确确认实际保证后才写 0–86400 的整数，不因工具品牌或历史「1 小时」假设自动提高。
3. 旧 `channel: lark` 仅兼容读取 userId（ou_ 加非空后缀）与 larkProfile，窗口按 0，不推荐新建此写法。迁移改变完整 route，旧条目和答复不能自动改归新 owner / 通道。legacyUnbound 只计数、不等于无答复；otherRoutes 非零保守阻断全部自动唤起。处理旧 route 待办只按 [其他路由待办的本地恢复](../../shared/notify.md#其他路由待办的本地恢复)，不以普通修改后运行 followup 代替该流程。
4. 旧无身份历史迁移顺序以同一共享恢复规范第 6 项为准：先在原 lark 配置由本人 / 可信 runtime 核对元数据、migrate-legacy 本地绑定、业务对账，再迁移为 runtime；身份确认不等于业务执行。runtime 配置下不能 migrate-legacy，已改者须按上述受控恢复处理，不能自动认领。能力不足就停并报告，旧待办保留并继续阻断自动唤起，可只手动使用；不因迁移默认放弃原批准事项。

### 10.2 本人同意后的 IM 测试

配置写入后才测试，严格按 [同步 IM](../../shared/notify.md#同步-im) 与 [出站交付与回执](../../shared/notify.md#出站交付与回执)：

1. `notify test` 只排队（queued），不是 sent。`notify outbox --claim` 后 runtime 核对冻结 route、正文、replyTo、token、原 idempotencyKey、requiresIdempotencyGuarantee 与适用 deliveryDeadlineMs，再实际投递；真实回执以原 key + token 提交 `notify receipt`。超时是 unknown，不自动重投；handoff 无 receipt 先核对收口。没有 IM 能力就保留队列并报告，不换通道。
2. 只有测试 sent 且私聊 / 可读时间边界建立后，请本人**引用测试消息回复「收到」**。`notify poll` → runtime 从 sinceMs 包含边界连续分页读取 → `notify incoming --json <批次>`；未读完报告，不误报没有答复。指令、引用事项、完整 ID / 独立序号优先，普通业务答复仍 received，不因测试吞掉。
3. 测试确认与其他回执也仅排队，继续 outbox 认领、核对冻结数据、投递、receipt；确认 test 状态及真实送达后才报告收发测试结果。queued / blocked / failed / unknown 分别说明，不能把排队当成功。incomingRejected 请本人发新的回复；replyRecovery 提示旧消息元数据不足时，请在同一已验证通道重新发带事项 ID 的新回复或终端明确处理，不猜引用、不伪报 clarify 已送达。

### 10.3 无人值守唤起（listener）

步骤 ID / 原草稿计划不变，显示「无人值守唤起」。只记录本人是否需要；旧 listener 答案保留，汇总标「未应用」，不据其写本机配置或安装任何服务。runtime 无法保证单实例、超时、后代清理和必要的防睡眠时，不设置无人值守。

需要时由 runtime 按 [等待答复与无人值守唤起](../../shared/notify.md#等待答复与无人值守唤起) 接入：trigger claim 仅数据认领，不启动 Agent；任何旧 started 状态未知都阻断，不靠超时猜已停。确认实例及全部后代结束才用 trigger finish 的 --confirmed-stopped。运行锁 / guard 不是执行器强互斥。保留「由 IM 监听器唤起」只是兼容提示词，不表示核心仍有监听器。实际权限、可见浏览器与唤起行为须本人另行验证，不用静态体检冒充已安装或可用。

## 11. 汇报

- 模式、工作区绝对路径，生成或修改的文件；重新设置时给出备份名和恢复方法。
- 一句话概括筛选条件、限额和授权。
- 招呼语草稿和选定的方式；需要本人在平台上完成的设置。
- 如有数据导入，报告导入条数、各状态数量。
- IM 排队 / 实际投递 / 测试回复各自结果，旧身份与路由待对账项；无人值守仅记录的决定、旧 listener 答案「未应用」。
- `workspace check` 数据配置结果，与 runtime 能力 / 环境、未验证的实际唤起分开汇报。
- 未确认、缺少依据、需要本人补充的事项。

# setup 引导式初始化与重新设置：实现方案

状态：2026-11-02 第一版，本人已确认方向。实现完成后，把第 2 节的规则并入 `docs/design.md`，本文件只作追溯。

本文件只写要求、接口与判据，不含代码。权威依据：`docs/design.md` 第 2、5 节；`skills/setup/SKILL.md`；`shared/workspace.md`；`scripts/workspace.py`。

---

## 0. 背景与已定决定

现状：`setup` 列出了新建要做的事，但没有标准的步骤顺序；提问是一大段话（`SKILL.md` 第 6 节「缺少的信息一次性问完」）；重新设置明文要求「一次性把要问的问题列出来，不要逐项来回追问」（第 3 节第 3 步）。中途中断后，进度无法接续。

| # | 决定（2026-11-02 本人确认） |
|---|---|
| D1 | `setup` 要有一套标准化、像软件安装一样的初始化流程：按固定步骤，一次问一件事，逐步收集工作区需要的信息 |
| D2 | 重置有两种方式：**重新设置**（全部步骤从头过一遍）与**部分重新设置**（本人选择要重新设置的部分）。不提供「清空事件记录重来」 |
| D3 | 进度草稿存放在工作区内：`setup-progress.json`，含私人信息，加入 `.gitignore` |
| D4 | 原先「重新设置时一次性列出全部问题、不逐项追问」的规则作废，改为逐步提问 |

保持不变：只改个别字段的**修改**模式、只读的**体检**模式；重新设置不动事件记录；停止名单只用 `ledger stoplist add` / `remove` 逐条增删；写入前备份、给本人看差异、本人确认后加锁写入；授权只在本人明确表态后写入，没表态时 `apply.send` 为 `false`、`followup.level` 为 `L2`；`setup` 不在招聘平台上外发。

---

## 1. 模式

| 情况 | 模式 |
|---|---|
| 工作区里有 `setup-progress.json` | **继续**：告知「上次的 <模式> 进行到第 n/N 步 · <步骤名>」，请本人选择继续、从头开始（丢弃草稿后重新进入对应模式）或放弃草稿 |
| 没有工作区 | **新建**：先选「典型」或「自定义」 |
| 已有工作区，本人要「重新 setup」「重新设置」「从头再来」 | **重新设置** |
| 已有工作区，本人要「重新设置某几部分」，或说要重设但没说全部还是部分 | **部分重新设置**：展示阶段与步骤菜单，由本人勾选 |
| 已有工作区，本人只改某几个字段（「把深圳去掉」「改成 L3」） | **修改**（不变，不走引导流程） |
| 检查工作区，或 `apply` / `followup` 报告工作区有问题 | **体检**（不变） |

「重新设置」与「部分重新设置」统称重置。本人只说「重置」时，先问要全部还是部分。

---

## 2. 步骤目录

步骤目录是标准流程的唯一定义，由 `scripts/workspace.py` 以数据形式维护（第 4 节），`SKILL.md` 中的表格须与之一致（测试核对，第 6 节）。步骤 id 为稳定的英文标识，向本人显示中文名。

| 阶段 | 步骤 id | 名称 | 写入位置 | 必答 | 典型模式 | 依赖 |
|---|---|---|---|---|---|---|
| `prepare` 准备 | `directory` | 工作区目录 | — | 是 | 问 | 只在新建时出现，`progress start` 时自动记为已答 |
| | `materials` | 现成材料 | — | 否 | 问 | 简历、旧 `apply-job-boss` 数据、个人事实或规则文档的位置。读取后为后续步骤预填 |
| | `account` | 平台账号 | `job-wingman.json` | 是 | 问 | BOSS 上显示的名字与本地别名 |
| `criteria` 求职条件 | `roles` | 目标职位 | `profile.targetRoles` | 是 | 问 | 按优先级排列 |
| | `locations` | 城市 | `profile.locations`、`rules.md` | 是 | 问 | 「不限」是有效回答 |
| | `salary` | 薪资 | `profile.salary` | 否 | 问 | 区间、税前税后、月薪年薪、`matchRule` 合为一步 |
| | `requirements` | 硬性要求 | `profile.hardRequirements` | 否 | 问 | |
| | `exclusions` | 排除条件 | `profile.exclusions` | 否 | 问 | 职位类型、用工形式、工作制度、关键词 |
| | `companies` | 排除的公司 | `stoplist.yaml` | 否 | 问 | 重置时先列出现有条目，只按本人明确要求增删 |
| | `activity` | 招聘者活跃度 | `profile.recruiterActivity` | 否 | 用默认 7 天 | |
| `facts` 个人事实 | `candidate` | 个人事实 | `candidate.md` | 是 | 问 | 由材料起草，逐节确认，可分多轮；部分重新设置时自动带上 `materials` |
| | `resumes` | 简历版本 | `resumes/index.yaml` | 是 | 问 | 各版本的选用规则与平台附件名；部分重新设置时自动带上 `materials` |
| | `qa` | 问答口径 | `qa.yaml` | 否 | 问 | 生成后整体过目；列出缺少依据的问题 |
| `outreach` 沟通与授权 | `greeting` | 招呼语 | `profile.greeting` | 是 | 问正文，方式默认 `dynamic` | 提醒本人在平台关闭自动打招呼 |
| | `applyAuth` | 发起沟通授权 | `profile.authorization.apply` | 否 | 问 | 跳过时为 `send: false` |
| | `followupLevel` | 代答级别 | `profile.authorization.followup` | 否 | 问 | 跳过时为 `L2` |
| | `pacing` | 限额与时段 | `profile.limits`、`profile.schedule` | 否 | 用默认值 | |
| `notify` 通知 | `im` | IM 通知 | `profile.im` | 否 | 问 | 不需要时记 `channel: none`，并把 `listener` 记为跳过 |
| | `listener` | 监听器 | `~/.config/job-wingman/listener.json` | 否 | 问 | 只记录决定，安装在写入阶段进行 |

最后是**确认与写入**阶段（第 3.4 节），不算作步骤。

**变更联动**（部分重新设置和重新设置都适用）：某步记为「已改」时，把下列步骤追加进计划（已在计划中的不重复），作为复核步骤：

| 改动的步骤 | 追加 |
|---|---|
| `candidate` | `qa`（按新事实核对每条 `basis`）、`greeting`（核对招呼语中的事实） |
| `roles` | `greeting` |

各模式的计划：

- **新建 · 自定义**：全部步骤。
- **新建 · 典型**：全部步骤；`activity`、`pacing` 在开始时直接记为「默认」，到汇总时列出默认值，本人可以改。
- **重新设置**：除 `directory` 外的全部步骤，每步预填当前值。
- **部分重新设置**：本人选的阶段或步骤，加上依赖带入的步骤，按目录顺序排列。

---

## 3. 交互规则（写进 `SKILL.md`）

### 3.1 每一步的呈现

```text
第 5/19 步 · 求职条件 · 薪资
当前值：税前月薪 15–25K，区间有交集即可（来自：现有配置）
……一个问题；必要时附编号选项……
可以回答：保留 / 默认 / 跳过 / 上一步 / 稍后再说
```

- 一次只问一步。字段紧密相关时合成一个问题（例如薪资的区间与口径），不把不相关的事项并到一步里。
- 显示当前值或预填值，并注明来源：现有配置、简历、旧数据、默认值。
- 选项用编号的纯文本，不依赖某个 Agent 特有的提问工具。
- 本人的回答顺带涉及后面的步骤时，先记下来；走到那一步时作为预填值请本人确认，不再从头问。
- 答完先复述解析结果（「记为：税前月薪 15–25K，有交集即可」），然后进入下一步。不在每一步追问「对吗」，统一在汇总时确认。

### 3.2 可用的回答

| 回答 | 效果 |
|---|---|
| 具体内容 | 记为「已改」（与当前值相同时记为「保留」） |
| 保留 | 重置时沿用当前值 |
| 默认 | 采用该步骤的默认值 |
| 跳过 | 非必答步骤记为未知或保留缺省；必答步骤不能跳过，说明原因后重问 |
| 上一步 / 回到第 n 步 | 跳回，重答后再接着往后走 |
| 稍后再说 | 保存进度并结束本轮，告诉本人下次说「继续 setup」即可接着做 |
| 放弃 | 确认后 `progress clear`。新建模式下同时撤销工作区标记 `job-wingman.json`（目录与其他文件保留），该目录不再是工作区，`apply` / `followup` 找不到它；以后要用须重新 setup |

### 3.3 各模式的开头

- **新建**：确认目录（说出绝对路径，本人确认）→ `workspace init` → 选典型或自定义 → `workspace progress start`。
- **重新设置 / 部分重新设置**：`workspace backup --label resetup`（部分时 `--label partial`），告诉本人备份名和恢复方法 → 部分模式先展示阶段与步骤菜单请本人选 → `workspace progress start --base-backup <备份名>`。

### 3.4 确认与写入

1. 汇总：新建时按阶段列出全部取值；重置时列出与备份相比每个文件的改动。标出「默认」「跳过 / 未知」的项。本人可以说「改第 n 步」跳回。
2. **本人确认后**写入，沿用现有流程：`ledger lock acquire --skill setup` → 重置时 `workspace changed --since <备份名>`，有变化时合并并重新展示差异 → 写入除停止名单外的文件，每写一个读回核对 → 停止名单用 `ledger stoplist add` / `remove` 逐条执行 → `ledger lock release`。
3. 写入之后才执行有副作用的事项，每项都先征得本人同意：旧数据迁移（`ledger migrate`，先 `--dry-run`）、IM 测试（`notify test` / `notify poll`）、监听器安装（`notify service install --dry-run`，再正式安装）。
4. `workspace progress finish`：按 `workspace check` 体检，但不计草稿本身那一项；没有其他问题时删除草稿，否则保留草稿并返回问题，按问题回到对应步骤。之后再运行一次 `workspace check` 确认。
5. 汇报（沿用现有第 11 节）。

---

## 4. 脚本接口：`workspace.py progress`

`setup-progress.json` 只经 `workspace progress` 读写，用原子写入（`ledger.write_atomic`）；输出沿用 `workspace.py` 的约定，向 stdout 输出一个 JSON 对象，退出码 0 成功、1 `finish` 体检未通过、2 错误。

| 命令 | 行为 |
|---|---|
| `progress start --mode new\|resetup\|partial [--variant typical\|custom] [--select <id,…>] [--base-backup <名>]` | 按第 2 节生成计划。`new` 必须带 `--variant`，并自动把 `directory` 记为已答；`typical` 把 `activity`、`pacing` 记为默认。`partial` 必须带 `--select`（阶段 id 或步骤 id，混用可以），未知 id 报错；自动带入 `materials` 等依赖。`resetup` / `partial` 必须带 `--base-backup`，且备份须存在。草稿已存在时报错，并返回现有草稿的摘要。同时确保 `.gitignore` 含 `/setup-progress.json`（见下） |
| `progress show` | 返回模式、计划、当前步骤、第 n/N 步、各步状态与取值、追加的复核步骤 |
| `progress record <步骤> --status changed\|kept\|default\|skipped [--value <JSON>] [--source user\|current\|resume\|legacy\|default]` | 记录一步并把当前步骤推进到计划中下一个未记录的步骤。必答步骤不允许 `skipped`；步骤不在计划中时报错；`--value` 须为合法 JSON（脚本不校验取值结构）。`changed` 时按联动表处理复核步骤：不在计划中的按目录顺序插入；已在计划中且已有回答的，清除其回答使其重新待答（例如回到 `candidate` 改了事实，已答的 `greeting` 要重新核对）；两种情况都在 `added` 中标注由哪一步带入。重复记录同一步时覆盖 |
| `progress goto <步骤>` | 把当前步骤设为指定步骤（实现「上一步」「回到第 n 步」），已记录的取值保留，直到重答覆盖 |
| `progress finish` | 完成时用：执行与 `check` 相同的体检，但不计「草稿存在」这一项；`problems` 为空时删除草稿，退出码 0；否则保留草稿，返回 `problems`，退出码 1 |
| `progress clear` | 删除草稿（放弃或从头开始时用），不做体检。草稿为新建时同时删除 `job-wingman.json`，返回 `markerRemoved: true`；`ledger/events.jsonl` 已有事件时不删标记，返回 `markerRemoved: false` 与原因。新建「从头开始」= `clear` 后重新 `init`、`progress start` |

`setup-progress.json` 的内容（示意）：`schemaVersion`、`mode`、`variant`、`baseBackup`、`startedAt`、`updatedAt`、`plan`（步骤 id 数组）、`current`、`added`（复核步骤 → 由哪一步带入）、`answers`（步骤 id → `status`、`value`、`source`、`at`）。

其他改动：

- **`.gitignore`**：`init` 写入的私人路径加上 `/setup-progress.json`（前缀规则与现有目录一致）。已有工作区由 `progress start` 补上。
- **`workspace check`**：草稿存在时，`mode: new` 记为 `problems`（「初始化未完成，第 n/N 步」）；`resetup` / `partial` 记为 `warnings`（「有未完成的重新设置，当前配置仍然有效」）。草稿损坏时记为 `problems`，建议放弃草稿。
- **运行锁**：`ledger lock acquire --skill apply` 或 `--skill followup` 在草稿存在且 `mode: new` 时拒绝，提示先完成 setup。`--skill setup` 不受影响。实现放在 `ledger.py`，只读取草稿的 `mode` 字段，读不出时按「未完成的新建」拒绝。
- **`backup` / `restore`**：草稿不属于配置文件，不进备份，恢复时也不动它。

---

## 5. 文档改动

| 文件 | 改动 |
|---|---|
| `docs/design.md` | 新增一节「setup 引导流程（已定，2026-11-02）」，概括第 0–2 节的决定，链接本文件；第 2 节表格中 `setup` 的职责补上「重新设置、部分重新设置」 |
| `skills/setup/SKILL.md` | 重写第 1–3、6 节：模式表（第 1 节）、步骤目录表（第 2 节）、交互规则（第 3 节）、`progress` 命令的用法。删掉「缺少的信息一次性问完」和「一次性把要问的问题列出来」。原第 6–10 节的内容规则（收集材料、生成文件、招呼语、迁移、IM）保留，改为按步骤引用。`description` 补上「部分重新设置」「继续 setup」等触发词 |
| `shared/workspace.md` | 文件表加 `setup-progress.json`；`workspace.py` 命令说明加 `progress`；定位一节注明：草稿存在且为新建时，工作区尚不能用于发送 |
| `shared/ledger.md` | `lock acquire` 说明补上初始化未完成时拒绝 |
| `docs/status.md` | 记为「已实现、单测通过」；正式验收（真实 Agent 走一遍新建、重新设置、部分重新设置）待本人发起 |

文字风格沿用现有文档：中文、短句，称「本人」。

---

## 6. 验收判据

自动化测试（`tests/test_workspace.py`，必要时 `tests/test_ledger.py`），使用临时目录和合成工作区：

1. `progress start` 各模式生成的计划与第 2 节一致：新建自定义为全部步骤；典型时 `activity`、`pacing` 已记为默认；重新设置不含 `directory`；部分重新设置只含所选步骤与依赖，并按目录顺序排列。
2. 参数校验：缺 `--variant`、缺 `--select`、缺 `--base-backup` 或备份不存在、未知步骤 id、草稿已存在，都报错且不改动现有草稿。
3. `record`：推进当前步骤；必答步骤拒绝 `skipped`；不在计划中的步骤报错；非法 JSON 报错；`candidate` 记为 `changed` 时追加 `qa`、`greeting`，记为 `kept` 时不追加。
4. `goto` 后重新 `record` 覆盖原值，当前步骤推进到下一个未记录的步骤。
5. `.gitignore`：`init` 与 `progress start` 都写入 `/setup-progress.json`，不重复写入。
6. `check`：新建草稿为 problem，重置草稿为 warning，损坏的草稿为 problem。
7. 运行锁：新建草稿存在时，`apply` / `followup` 获取锁被拒绝，`setup` 可以获取；草稿清除后恢复正常。
8. 备份与恢复不包含、不改动草稿。
9. 一致性：`SKILL.md` 步骤目录表中的步骤 id 与顺序等于脚本中的目录。
10. 独立技能构建（`tests/test_build.py`）照常通过。
11. `progress finish`：配置完整时删除草稿且之后 `check` 通过；配置有问题时保留草稿并返回问题，不把「草稿存在」算作问题。
12. 复核：部分重新设置只选 `roles`、`im` 时，`roles` 记为 `changed` 后 `greeting` 插在 `im` 之前；`greeting` 已答后 `goto candidate` 并记为 `changed`，`greeting` 与 `qa` 重新待答，当前步骤推进到其中靠前的一个。
13. 放弃新建：`progress clear` 删除草稿与 `job-wingman.json`，之后在该目录 `ledger` / `workspace` 找不到工作区；有事件时不删标记；重置草稿的 `clear` 不动标记。

回归：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests` 全部通过，报告用例总数。

不在本次范围：用真实 Agent 逐步走一遍的行为验收，由本人另行发起；不修改真实求职工作区。

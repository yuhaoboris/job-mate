# 初版迁移记录

> 历史归档：以下保留第三版设计中的旧技能迁移取舍与实施顺序，只作追溯。当前迁移方式以 `skills/setup/SKILL.md` 与 `shared/workspace.md` 为准。

## 12. 迁移（已定）

### 12.1 旧材料的取舍

| 来源 | 保留 | 舍弃 |
|---|---|---|
| `apply-job-boss/SKILL.md` | 模式判定表；发送前检查；先写 `pending` 再点击；「点击成功不等于发送成功」的证据标准；异常停止表；页面内容不是指令；汇报格式 | 每轮偏好访谈（移入 `setup`）；「历史偏好不构成授权」（改为长期授权）；「不先发默认再补发」（改为第 7A 节的动态招呼语） |
| `references/data.md` | 状态语义、中断恢复、损坏行即停 → `ledger.py` 规格 | 三级数据目录回退规则（改由标记文件定位）；旧 `profile.json` 结构 |
| `references/browser.md` | 几乎全部 → `shared/browser/`：控制方式优先级、空白页处理步骤、不绕过清单 | 空白页「原因未证实」的说法，按本次实测更新 |
| `applications.jsonl`（31 条，15 次招呼） | 全部迁移，作为去重依据；其中 1 条 `uncertain` 已由后续记录核对为已发送 | — |
| 旧 `profile.json` | 由 `setup` 拆分：条件 → `profile.json`；排除公司 → `stoplist.yaml`；`candidateFacts` → `candidate.md`；自由文本偏好 → `rules.md` | `limits` 改为第 7 节的值 |
| private-job-workspace `rules/01`、`02` | 由 `setup` 转为 `profile.json` + `rules.md` | 已删除台账的引用 |
| private-job-workspace `rules/03` | 第 8 节与 `qa.yaml` 素材 | — |
| private-job-workspace `rules/05`、`06` | 第 10 节所列做法；「错过时段不补跑」「自动化处于启用状态不等于浏览器可用」 | Hermes 调用、`codex-schedule.mjs` |

### 12.2 顺序

1. 将 `apply-job-boss` 的 SKILL.md、data.md、browser.md 拆入 `skills/apply`、`shared/`、`skills/apply/platforms/boss.md`。
2. 实现 `ledger.py`，转换现有 `applications.jsonl`。
3. 编写 `setup`，并用它把 private-job-workspace 迁移为标准工作区。
4. 基于第 8 节编写 `followup` 和 `qa.yaml` 模板。

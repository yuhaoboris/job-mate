# job-wingman 第五轮复审

> 历史归档：保留当时的版本、结论与行号；文件引用已按当前目录更新，私人标识和本机路径已脱敏。本文不代表当前实现或验收状态，当前状态见项目的 `docs/status.md`。

日期：2026-10-29；评审：Codex；基线：`371721fac812cf9aad60deb41d18c306d675ec08`。

## 总体结论

**H01 的重复外发路径已被阻断，本轮未发现严重或重要问题，可以进入用户指定的阶段 2 禁发巡检。** 新写入的决定外发必须带 `step`；未完成决定存在无 `step` 的旧尝试时，查询、发送前检查和新建 `pending` 均阻止直接执行新步骤。本结论只针对此次修复，不代表招聘平台流程已经验收。

## 范围与验证

只复核 `docs/archive/reviews/2026-10-29-codex-round4-response.md` 中的 H01 修复及 `b2c13ac..371721f` 对应实现、测试和说明差异；已经通过且没有改动的部分未重审。遵守 `docs/archive/review-brief.md`，阶段 1 未访问招聘平台，未修改实现、测试或真实工作区；唯一新增文件为本报告。

首先执行 `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`：**60 tests，OK**。环境变量仅用于避免生成字节码文件。

额外通过临时工作区调用实际 ledger 命令，验证：

| 场景 | 结果 |
|---|---|
| 每种外发 kind，新建 `pending` / 独立 `skipped` / `already_contacted`，带 decision 但不带 step | 全部拒绝，提示必须带 step |
| 无 step 的历史 `sent`，尚无完成标记 | 决定状态与指定步骤均为 `needs_review` |
| 无 step 的历史 `pending` / `uncertain` | 总状态为 `in_progress`，指定步骤为 `needs_review` |
| 无 step 的历史 `failed_no_send` | 决定状态与指定步骤均为 `needs_review`，不会猜测对应动作 |
| 上述四种历史状态下，`check --decision` | 均阻止，包含 `decision_needs_review` |
| 上述四种历史状态下，绕过 check 直接追加带 step 的新 pending | 均拒绝，提示须本人核对 |
| 带 step 的新格式组合决定、旧版部分组合决定、本人确认完成后的状态 | 原有及新增单测通过 |

旧记录仍可读取，未被判为格式损坏；终态核对与新建外发被区分处理。测试探针只使用系统临时目录，退出后清理。

## 问题列表

没有严重、重要或一般问题需要新增。

**建议：补齐状态表中的 `needs_review` 行。** 位置：`skills/followup/SKILL.md:66–77`。无 step 的未确认旧尝试会返回总体 `in_progress`、步骤 `needs_review`；第 2 步只专门解释总体 `needs_review`，第 3 步的状态表没有 `needs_review`。命令提示及代码门禁已明确阻止新建外发，因此不构成 H01 的再次放行；建议表中补充“停止本事项外发，按查询建议核对并上报”，方便首次运行的 Agent 一致处理。

## 需要本人决定的问题

无。H01 修复通过；后续按用户指定顺序进入 followup 只看不发巡检，再依据实测停止条件决定是否进入 apply。

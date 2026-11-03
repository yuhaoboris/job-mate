# job-wingman 第四轮复审与三阶段执行汇总

> 历史归档：保留当时的版本、结论与行号；文件引用已按当前目录更新，私人标识和本机路径已脱敏。本文不代表当前实现或验收状态，当前状态见项目的 `docs/status.md`。

日期：2026-10-29。评审：Codex。基线：`b2c13acf8e45f1376b1d95a35423b3fa33635b42`，实现修复提交为 `a438d03`。

## 总体结论

**阶段 1 发现 1 项重要问题，按本轮停止条件，不进入阶段 2、3。** 56 个单元测试全部通过；带 `step` 的新记录能正确表示部分完成，`closedBy` 也保留了不可被 `reopened` 清除的关闭原因。但旧版没有 `step` 的已发送决定，在尚未写入完成标记时被新版恢复，会被判定为新的外发步骤尚未开始，存在重复发送路径。该问题已用临时工作区复现，未访问招聘平台，也没有认定真实 private-job-workspace 中已经存在这种记录。

## 范围与验证

- 只复核 `docs/archive/reviews/2026-10-29-codex-response.md` 所述改动，以 `cd05f74..b2c13ac` 的实现和说明差异为依据；未重新审查已经通过且没有改动的部分。
- 先执行 `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`：**56 tests，OK**。环境变量仅防止生成字节码文件。
- 针对分步骤恢复与会话关闭，用测试中的 `Workspace` 在系统临时目录构造虚拟记录，调用实际 ledger 命令。没有修改测试或实现。
- 仓库开始时工作区干净；本轮唯一新增文件为本报告。未修改 private-job-workspace，未提交、未推送。

## 问题列表

### H01（重要）：旧版无 step 的已发送记录被当成尚未开始的新步骤，恢复时可能重复外发

**位置：** `scripts/ledger.py:778–806`；`skills/followup/SKILL.md:51–75`；相关测试 `tests/test_ledger.py:396–405`。

**问题：** 未带 `step` 的历史尝试被归入 `default`，但技能要求为外发动作使用 `reply1`、`resume1` 等编号；查询这些编号时返回 `not_started`，没有要求先核对历史尝试与新步骤的对应关系。

**触发场景与结果：**

1. 旧版执行已批准的单条回复：同一 `decision` 已有 `paused` 和 `reply/pending → sent`，当时没有 `step` 字段。
2. 在追加 `unpaused` 前中断，然后使用新版恢复。inbox 即使已经标记「已执行」，新版第 2.1 节也要求以 ledger 为准继续处理。
3. `decision --id <ID> --step reply1` 返回 `state: partial`、`steps: {"default": "done"}`，但 `stepState: not_started`。
4. 按第 2.2 节状态表，Agent 进入发送路径。会话内 `check --decision <ID>` 放行，新的 `reply1/pending` 写入成功，点击前的 `guard` 也放行。

因此，原回复已确认发送，但新步骤仍可再次执行。此处不依赖锁超时、并发或错误终态；缺口是旧尝试到新步骤的对应关系。页面复读并不能替代该对应关系，尤其是简历请求或相同话术的恢复场景。

**实测输出摘要：**

```json
{
  "state": "partial",
  "steps": {"default": "done"},
  "stepState": "not_started",
  "checkAllowed": true,
  "newPendingAccepted": true,
  "guardAllowed": true
}
```

**建议：** 对存在无 `step` 历史尝试、且没有完成标记的决定，先停止新增外发并核对动作对应关系，不要直接将任意新编号返回为可执行的 `not_started`。单动作能确定对应关系时可明确关联；组合动作不能把一个旧 `sent` 猜成全部完成。技能也应写明这条兼容分支。增加无 `step` 的旧版单回复、旧版部分组合动作、旧版未确认尝试的恢复测试；保留目前带 `step` 的测试。

现有 `test_decision_sent_but_not_completed_resumes` 在本次修复中把原来的无 `step` 样例改成了 `step="reply1"`，因此覆盖了新格式恢复，却没有保留升级恢复的回归覆盖。

## 其余修复的复核结论

| 项目 | 本轮结论与证据 |
|---|---|
| G01：新格式的组合决定 | 新记录携带稳定 `step` 时，`released` 后为 `partial`，已发送的步骤为 `done`，另一未发送步骤为 `not_started`；全部动作之后的带 `decision` 的 `unpaused` 才使整个事项为 `done`。对应测试通过。旧格式兼容仍受 H01 阻塞。 |
| G02：关闭原因叠加 | `build_threads` 只从 `closedBy` 移除 `declined`。单测与额外临时探针确认 `job_closed / rejected / risk → declined → reopened` 都仍阻止发送；单独 `declined → reopened` 可解除关闭。上轮 G02 已修复。 |
| G03：孤立暂停的重建依据 | `decision` 返回 `pausedRecords` 中的原因、时间和会话字段；技能明确仅据已知信息补写 inbox，不臆造选项和期限。对应测试通过。 |
| S01：guard 保证边界 | 设计说明已改为当前租期续期、非原子操作、中断后重新检查，不再承诺绝对隔离。本轮无新增问题。 |
| 回应中的其余改动 | 已检查职位层面与公司层面排除的区分、本人核对结果与释放旧锁的说明、首次联系原因去重的差异；未发现需要另列的重要问题。未做平台验收。 |

## 可复现的最小探针

在 job-wingman 根目录运行；只在系统临时目录写入虚拟事件，不访问真实工作区或平台：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B - <<'PY'
import sys
sys.path.insert(0, 'tests')
from test_ledger import Workspace

w = Workspace()
try:
    d = 'D-20261029-01'
    c = dict(company='旧步骤兼容样例', recruiter='林女士(HR)',
             skill='followup', decision=d)
    assert w.append(kind='observe', observed='paused', **c)[0] == 0
    # 旧版已发送，尚未写入 unpaused；旧记录没有 step。
    w.greet(kind='reply', text='测试批准回复', **c)
    state = w.run('decision', '--id', d, '--step', 'reply1')[1]
    assert state['state'] == 'partial'
    assert state['steps'] == {'default': 'done'}
    assert state['stepState'] == 'not_started'
    check = w.run('check', '--platform', 'boss', '--kind', 'reply',
                  '--company', c['company'], '--recruiter', c['recruiter'],
                  '--decision', d, '--run', 'run-1')[1]
    assert check['allowed']
    code, pending = w.append(kind='reply', status='pending', step='reply1',
                             text='测试批准回复', **c)
    assert code == 0
    guard = w.run('guard', '--run', 'run-1', '--attempt',
                  pending['event']['attemptId'])[1]
    assert guard['allowed']
    print('H01 已复现：旧回复已 sent，新编号仍被允许进入外发')
finally:
    w.close()
PY
```

## 需要本人决定的问题

没有新增产品取舍需要决定。H01 是恢复兼容与不重复外发的实现问题；公司层面与职位层面排除继续遵循回应中已确认的方案 B。

## 三个阶段的汇总（可直接转交）

| 阶段 | 状态 | 结论 |
|---|---|---|
| 1：复审 job-wingman | 已完成，触发停止条件 | 56 个测试通过；发现 H01 重要问题：无 `step` 的历史已发送决定可能在新版恢复时重复外发。关闭原因叠加修复通过。 |
| 2：BOSS followup 只看不发 | 未进入 | 因阶段 1 的停止条件，未打开 BOSS，未获取真实工作区运行锁，未写观察记录、inbox 或停止名单。没有平台结构或会话巡检结论。 |
| 3：最多联系 3 个职位 | 未进入 | 未联系任何职位、未发送招呼语、未检查今日额度；没有「立即沟通」界面或发送成功证据。 |

后续应先修复 H01 并复核，再按用户安排继续实测；本轮没有修改实现或平台说明。

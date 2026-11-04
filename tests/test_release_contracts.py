"""释放调用链的静态契约与合成台账数据可见性；不代表 Agent 行为验收。"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ledger  # noqa: E402

CONTRACT = "../../shared/ledger.md#释放与对账契约"


class ReleaseStaticContractTest(unittest.TestCase):
    """只锁定技能入口 / 调用点指向共享规则，不模拟模型遵循情况。"""

    def skill(self, name):
        return (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")

    def test_every_release_call_processes_reconciled_and_failure_via_shared_contract(self):
        for name, expected in (("apply", 2), ("followup", 2), ("setup", 1)):
            with self.subTest(skill=name):
                calls = [line for line in self.skill(name).splitlines() if "`ledger lock release" in line]
                self.assertEqual(len(calls), expected)
                for line in calls:
                    self.assertIn(CONTRACT, line)
                    self.assertIn("`reconciled`", line)
                    self.assertIn("释放失败", line)

    def test_all_skill_entries_reconcile_unresolved_even_when_acquire_is_empty(self):
        sections = {
            "apply": self.skill("apply").split("## 2. 确定模式", 1)[1].split("## 3.", 1)[0],
            "followup": self.skill("followup").split("### 2.1 对账", 1)[1].split("### 2.2", 1)[0],
            "setup": self.skill("setup").split("### 3.3 确认与写入", 1)[1].split("## 4.", 1)[0],
        }
        for name, section in sections.items():
            with self.subTest(skill=name):
                for phrase in (CONTRACT, "即使 acquire 的 `reconciled` 为空", "完整 `ledger threads`",
                               "`unresolved`", "inbox"):
                    self.assertIn(phrase, section)
        prepare = self.skill("followup").split("## 1. 准备", 1)[1].split("## 2.", 1)[0]
        self.assertIn("持锁后先执行第 2.1 节入口对账", prepare)

    def test_shared_contract_covers_identity_dedup_notification_and_fail_closed(self):
        contract = (ROOT / "shared/ledger.md").read_text(encoding="utf-8").split(
            "### 释放与对账契约", 1)[1]
        for phrase in ("不带平台 / 账号 / 公司 / 招聘者筛选", "招聘者为 `null`", "以平台、账号、attemptId 为准",
                       "按平台、账号、attemptId 查找 / 沿用已有事项", "先暂停、再写 inbox",
                       "notify send --kind decision --decision <ID>", "已经确认的终态保持不变",
                       "不再持原锁重发", "不能宣称完全收尾", "锁未确认释放", "对账未完成"):
            self.assertIn(phrase, contract)

    def test_uncertain_template_binds_attempt_and_keeps_original_decision(self):
        notify = (ROOT / "shared/notify.md").read_text(encoding="utf-8")
        self.assertIn("### 结果不明事项", notify)
        rule = notify.split("### 结果不明事项", 1)[1].split("## IM 通道", 1)[0]
        for phrase in ("- 尝试：<attemptId>", "选项：已发出 / 未发出", "沿用原决定 ID",
                       "不另开新 ID", "不重复暂停", "保留原结论", "状态保持阻断",
                       "notify send --kind done --decision <原ID>"):
            self.assertIn(phrase, rule)

    def test_all_uncertain_inbox_write_sites_reference_one_rule(self):
        sites = {
            "apply": ("等待后仍无法确认", "结果不明时不重发", "**结果不明（`uncertain`）的招呼**"),
            "followup": ("不能确认记 `uncertain`", "| 无法判断是否发出", "**结果不明（`uncertain`）的外发**"),
        }
        for name, markers in sites.items():
            for marker in markers:
                with self.subTest(skill=name, site=marker):
                    lines = [line for line in self.skill(name).splitlines() if marker in line]
                    self.assertEqual(len(lines), 1)
                    self.assertIn("../../shared/notify.md#结果不明事项", lines[0])
        decided = self.skill("followup").split("### 2.2 执行已决定的事项", 1)[1].split("### 2.3", 1)[0]
        self.assertIn("../../shared/notify.md#结果不明事项", decided)
        self.assertIn("attemptId", decided)

    def test_setup_test_instruction_and_restore_output_contract(self):
        setup = self.skill("setup")
        self.assertIn("引用测试消息回复「收到」", setup)
        self.assertNotIn("回复任意文字", setup)
        for phrase in ("`restoreSucceeded` / `restoreError`", "`release` / `releaseError`",
                       "退出码 2", "`restored`", "`release.attempted`", "不擅自删除锁"):
            self.assertIn(phrase, setup)


class ReleaseRecoveryDataTest(unittest.TestCase):
    """验证契约所依赖的 CLI 数据确实可见；不声称 inbox 被脚本自动修复。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        (self.ws / "job-mate.json").write_text(json.dumps({
            "schemaVersion": 1, "suiteVersion": "1.0.0", "accounts": {"boss": {"accountKey": "synthetic"}}}))
        (self.ws / "profile.json").write_text(json.dumps({"schemaVersion": 1}))
        (self.ws / "inbox").mkdir()
        self.inbox = self.ws / "inbox/decisions.md"
        self.inbox.write_text("# 合成工作区，无待决定事项\n")
        self.assertEqual(self.run_ledger("lock", "acquire", "--skill", "apply", "--run", "old")[0], 0)

    def tearDown(self):
        self.tmp.cleanup()

    def run_ledger(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = ledger.main(["--workspace", str(self.ws), *args])
        return code, json.loads(buf.getvalue())

    def append(self, **fields):
        ev = {"platform": "boss", "runId": "old", "skill": "apply", "kind": "greet",
              "status": "pending", "company": "合成公司", "reason": "测试，未操作平台"}
        ev.update(fields)
        # 收尾/恢复只审计已有中间态，不用本轮发送权限制造历史。
        import history_fixture
        code, out = history_fixture.append_action(self.ws, ev)
        self.assertEqual(code, 0, out)
        return out["event"]

    def test_release_output_lost_before_inbox_write_is_visible_without_recruiter(self):
        ev = self.append()
        before = self.inbox.read_bytes()
        self.assertEqual(self.run_ledger("lock", "release", "--run", "old")[0], 0)
        # 模拟 release 已成功，但调用者还没把其输出写进 inbox 就中断。
        self.assertFalse(self.run_ledger("lock", "release", "--run", "old")[1]["released"])
        code, acquired = self.run_ledger("lock", "acquire", "--skill", "followup", "--run", "next")
        self.assertEqual((code, acquired["reconciled"]), (0, []))
        code, out = self.run_ledger("threads")
        self.assertEqual(code, 0, out)
        thread = out["threads"][0]
        self.assertIsNone(thread["recruiter"])
        self.assertIsNone(thread["title"])
        self.assertEqual(thread["company"], "合成公司")
        self.assertEqual(thread["unresolved"], [{"platform": ev["platform"], "account": ev["account"], "attemptId": ev["attemptId"], "kind": "greet", "status": "uncertain"}])
        self.assertEqual(self.inbox.read_bytes(), before)

    def test_unfiltered_threads_include_unknown_and_named_recruiter_attempts(self):
        unknown = self.append()["attemptId"]
        known = self.append(company="另一合成公司", recruiter="示例(HR)")["attemptId"]
        self.assertEqual(self.run_ledger("lock", "release", "--run", "old")[0], 0)
        code, out = self.run_ledger("threads")
        self.assertEqual(code, 0, out)
        visible = [a["attemptId"] for t in out["threads"] for a in t["unresolved"]]
        self.assertCountEqual(visible, [unknown, known])
        self.assertEqual(len(visible), 2)

    def test_confirmed_terminal_disappears_from_unresolved_despite_stale_release_output(self):
        ev = self.append()
        _, released = self.run_ledger("lock", "release", "--run", "old")
        self.assertEqual([a["attemptId"] for a in released["reconciled"]], [ev["attemptId"]])
        self.append(status="sent", attemptId=ev["attemptId"], skill="user", evidence="合成本人核对")
        code, out = self.run_ledger("threads")
        self.assertEqual(code, 0, out)
        self.assertTrue(all(not t["unresolved"] for t in out["threads"]))
        attempts = ledger.attempts_of(ledger.read_events(self.ws)[0])
        self.assertEqual(attempts[ledger.attempt_key(ev)]["status"], "sent")

    def test_missing_company_is_reported_as_corruption_not_silently_omitted(self):
        ev = self.append()
        del ev["company"]
        with ledger.events_path(self.ws).open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev) + "\n")
        code, out = self.run_ledger("threads")
        self.assertEqual(code, 2, out)
        self.assertIn("company", out["error"])
        self.assertIn("第 2 行", out["error"])
        self.assertNotIn("threads", out)


if __name__ == "__main__":
    unittest.main()

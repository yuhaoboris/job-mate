"""T5.4 来源可证明的结果不明统计与唯一停止规则。"""
import json
import unittest

import test_prepare

ledger = test_prepare.ledger


class UncertaintyTest(unittest.TestCase):
    def setUp(self):
        self.x = test_prepare.PrepareTest()
        self.x.setUp()
        self.x.reset_case()
        self.f = self.x.f

    def tearDown(self):
        self.x.tearDown()

    def unclear(self, company, classification="business", reason="合成业务证据"):
        rc, out = self.x.prepare(company=company)
        self.assertEqual(rc, 0, out)
        rc, event = self.f.append(kind="reply", status="uncertain", runId="run-prepare", company=company,
                                  attemptId=out["attemptId"], uncertaintyClass=classification, reason=reason)
        self.assertEqual(rc, 0, event)
        return out

    def test_first_business_unknown_stops_target_second_stops_run_even_after_owner_closure(self):
        first = self.unclear("目标甲", reason="锁已过期，文字不是来源证据")
        self.assertEqual(self.x.prepare(company="目标甲")[0], 1)
        second = self.unclear("目标乙")
        self.assertEqual(self.f.append(kind="reply", status="failed_no_send", runId="run-prepare", company="目标甲",
                                      attemptId=first["attemptId"], skill="user")[0], 0)
        rc, out = self.x.prepare(company="目标丙")
        self.assertEqual(rc, 1, out)
        self.assertEqual(out["reasons"][0]["code"], "run_uncertain_limit")

    def test_tool_or_unspecified_unknown_stops_run_on_first_occurrence(self):
        for classification in ("tool", None):
            with self.subTest(classification=classification):
                self.x.reset_case()
                rc, out = self.x.prepare(company="目标甲")
                self.assertEqual(rc, 0, out)
                extra = {"uncertaintyClass": classification} if classification else {}
                self.assertEqual(self.f.append(kind="reply", status="uncertain", runId="run-prepare", company="目标甲",
                                               attemptId=out["attemptId"], **extra)[0], 0)
                rc, result = self.x.prepare(company="目标乙")
                self.assertEqual(rc, 1, result)
                self.assertEqual(result["reasons"][0]["code"], "run_uncertain_limit")

    def test_reconcile_all_pending_is_proven_and_not_business_quota(self):
        for company in ("目标甲", "目标乙"):
            self.assertEqual(self.x.prepare(company=company)[0], 0)
        rc, out = self.f.run_cli("lock", "release", "--run", "run-prepare")
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(out["reconciled"]), 2)
        uncertain = [e for e in ledger.read_events(self.f.ws)[0] if e.get("status") == "uncertain"]
        self.assertEqual([e.get("uncertaintyClass") for e in uncertain], ["reconcile", "reconcile"])
        self.assertTrue(all(e["reconcileProof"]["via"] == "release" for e in uncertain))
        self.assertEqual(self.x.acquire()[0], 0)
        self.assertEqual(self.x.prepare(company="目标丙")[0], 0)

    def test_owner_closure_does_not_restart_same_target_in_this_run(self):
        first = self.unclear("目标甲")
        self.assertEqual(self.f.append(kind="reply", status="failed_no_send", runId="run-prepare", company="目标甲",
                                      attemptId=first["attemptId"], skill="user")[0], 0)
        rc, out = self.x.prepare(company="目标甲")
        self.assertEqual(rc, 1, out)
        self.assertEqual(out["reasons"][0]["code"], "unresolved")
        self.assertEqual(self.x.prepare(company="独立目标")[0], 0)

    def test_takeover_proof_references_each_original_pending(self):
        ids = []
        for company in ("目标甲", "目标乙"):
            rc, out = self.x.prepare(company=company)
            self.assertEqual(rc, 0, out)
            ids.append(out["eventId"])
        lock = ledger.read_lock(self.f.ws)
        lock["expiresAt"] = "2000-01-01T00:00:00+08:00"
        ledger.write_lock(self.f.ws, lock)
        self.assertEqual(self.f.run_cli("lock", "acquire", "--skill", "followup", "--run", "next-run")[0], 0)
        uncertain = [e for e in ledger.read_events(self.f.ws)[0] if e.get("status") == "uncertain"]
        self.assertEqual([e["reconcileProof"]["pendingEventId"] for e in uncertain], ids)
        self.assertTrue(all(e["reconcileProof"]["via"] == "takeover" for e in uncertain))
        self.assertEqual(self.f.run_cli("validate")[0], 0)

    def test_same_encoded_job_key_does_not_expand_business_target_stop(self):
        rc, out = self.x.prepare(kind="greet", targetRole="开发", jobUrl="https://www.zhipin.com/job_detail/a|b.html")
        self.assertEqual(rc, 0, out)
        self.f.append(kind="greet", status="uncertain", runId="run-prepare", attemptId=out["attemptId"],
                      jobUrl="https://www.zhipin.com/job_detail/a|b.html", uncertaintyClass="business")
        # 改为job级去重，并以不同公司/招聘者排除旧会话未决；不把不明目标的原始ID合并。
        self.x.profile["limits"]["dedupScope"] = "job"
        self.x.save_profile()
        rc, result = self.x.prepare(kind="greet", company="另一目标", recruiter="其他招聘者(HR)", targetRole="开发",
                                    jobUrl="https://www.zhipin.com/job_detail/a｜b.html")
        self.assertEqual(rc, 0, result)

    def test_skill_uncertain_branches_point_to_single_stopping_contract(self):
        root = test_prepare.test_tail.ROOT
        for name in ("apply", "followup"):
            text = (root / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertNotIn("停止本轮发送", text)
            self.assertNotIn("该会话停止，其他会话继续", text)
            # 技能统一引用停止契约即可；状态名在对账/汇报中出现不需要逐行重复链接。
            self.assertIn("execution.md#结果不明时的停止范围", text, name)
        execution = (root / "shared/execution.md").read_text(encoding="utf-8")
        self.assertEqual(execution.count("## 结果不明时的停止范围"), 1)
        self.assertIn("第二次", execution)
        self.assertIn("reconcileProof", execution)

    def test_caller_cannot_forge_reconcile_origin_or_bad_historical_proof(self):
        rc, out = self.x.prepare()
        self.assertEqual(rc, 0, out)
        for fields in ({"uncertaintyClass": "reconcile"}, {"reconcileProof": {"via": "release"}}):
            self.assertEqual(self.f.append(kind="reply", status="uncertain", runId="run-prepare",
                                          attemptId=out["attemptId"], **fields)[0], 2)
        pending = ledger.read_events(self.f.ws)[0][-1]
        event = dict(ledger.public_entry(pending), status="uncertain", uncertaintyClass="reconcile",
                     reconcileProof={"pendingEventId": "evt-000000000000", "runId": "run-prepare", "via": "release"})
        event = ledger.prepare_event(event)
        ledger.append_line(self.f.ws, event)
        self.assertEqual(self.f.run_cli("validate")[0], 2)

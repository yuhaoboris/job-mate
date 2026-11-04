"""T5.3 真实prepare/append边界的身份冻结，不修改历史事实。"""
import json
import unittest

import test_prepare

ledger = test_prepare.ledger


class IdentityFreezeTest(unittest.TestCase):
    def setUp(self):
        self.x = test_prepare.PrepareTest()
        self.x.setUp()
        self.x.reset_case()
        self.f = self.x.f

    def tearDown(self):
        self.x.tearDown()

    def test_non_null_identity_cannot_be_retargeted(self):
        for field, value in (("company", "另一公司"), ("title", "其他岗位"), ("recruiter", "其他招聘者(HR)")):
            with self.subTest(field=field):
                self.x.reset_case()
                rc, out = self.x.prepare()
                self.assertEqual(rc, 0, out)
                before = ledger.events_path(self.f.ws).read_bytes()
                rc, error = self.f.append(kind="reply", status="sent", runId="run-prepare", attemptId=out["attemptId"], **{field: value})
                self.assertEqual(rc, 2, error)
                self.assertIn(field, error["error"])
                self.assertEqual(ledger.events_path(self.f.ws).read_bytes(), before)

    def test_null_identity_can_be_completed_once_not_overwritten_again(self):
        rc, out = self.x.prepare(recruiter=None)
        self.assertEqual(rc, 0, out)
        aid = out["attemptId"]
        self.assertEqual(self.f.append(kind="reply", status="uncertain", runId="run-prepare", attemptId=aid, recruiter="招聘者甲(HR)")[0], 0)
        rc, error = self.f.append(kind="reply", status="sent", runId="run-prepare", skill="user", attemptId=aid, recruiter="招聘者乙(HR)")
        self.assertEqual(rc, 2, error)
        self.assertIn("recruiter", error["error"])
        self.assertEqual(self.f.append(kind="reply", status="sent", runId="run-prepare", skill="user", attemptId=aid, recruiter="招聘者甲(HR)")[0], 0)

    def test_normalized_human_identity_keeps_first_representation(self):
        rc, out = self.x.prepare(company="Ａ公司", title="开发Ａ", recruiter="招聘者Ａ(HR)")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.f.append(kind="reply", status="sent", runId="run-prepare", attemptId=out["attemptId"],
                                      company=" A 公司 ", title="开发 A", recruiter="招聘者 A (HR)")[0], 0)
        attempt = ledger.attempts_of(ledger.read_events(self.f.ws)[0])[("boss", "boss-main", out["attemptId"])]
        self.assertEqual(attempt["company"], "Ａ公司")
        self.assertEqual(attempt["title"], "开发Ａ")
        self.assertEqual(attempt["recruiter"], "招聘者Ａ(HR)")

    def test_opaque_job_id_is_not_nfkc_merged_even_when_job_key_collides(self):
        url = "https://www.zhipin.com/job_detail/a|b.html"
        rc, out = self.x.prepare(jobUrl=url)
        self.assertEqual(rc, 0, out)
        rc, error = self.f.append(kind="reply", status="sent", runId="run-prepare", attemptId=out["attemptId"],
                                  jobUrl="https://www.zhipin.com/job_detail/a｜b.html")
        self.assertEqual(rc, 2, error)
        self.assertIn("jobId", error["error"])

    def test_omitted_frozen_identity_is_inherited_but_explicit_null_does_not_clear(self):
        rc, out = self.x.prepare(jobUrl="https://www.zhipin.com/job_detail/known-job.html")
        self.assertEqual(rc, 0, out)
        raw = {"platform": "boss", "runId": "run-prepare", "skill": "followup", "kind": "reply",
               "status": "sent", "attemptId": out["attemptId"], "reason": "合成成功证据"}
        rc, sent = self.f.run_cli("append", "--json", json.dumps(raw))
        self.assertEqual(rc, 0, sent)
        self.assertEqual(sent["event"]["jobKey"], "boss|known-job")
        self.assertEqual(sent["event"]["company"], "合成公司")

    def test_explicit_null_cannot_clear_and_null_derived_keys_can_be_completed(self):
        rc, out = self.x.prepare()
        before = ledger.events_path(self.f.ws).read_bytes()
        self.assertEqual(self.f.append(kind="reply", status="sent", runId="run-prepare", attemptId=out["attemptId"], recruiter=None)[0], 2)
        self.assertEqual(ledger.events_path(self.f.ws).read_bytes(), before)
        self.x.reset_case()
        rc, out = self.x.prepare(title=None, recruiter=None)
        self.assertEqual(rc, 0, out)
        pending = ledger.read_events(self.f.ws)[0][-1]
        self.assertIsNone(pending["jobKey"])
        self.assertIsNone(pending["threadKey"])
        rc, sent = self.f.append(kind="reply", status="sent", runId="run-prepare", attemptId=out["attemptId"])
        self.assertEqual(rc, 0, sent)
        self.assertEqual(sent["event"]["jobKey"], "boss|合成公司|开发")
        self.assertEqual(sent["event"]["threadKey"], self.f.thread)

    def test_corrupt_history_keeps_first_target_and_actual_sent_auditable(self):
        _, pending = self.f.history(kind="reply", status="pending", runId="run-prepare", company="原目标")
        self.f.history(kind="reply", status="sent", runId="run-prepare", attemptId=pending["event"]["attemptId"], company="错绑目标")
        attempt = ledger.attempts_of(ledger.read_events(self.f.ws)[0])[("boss", "boss-main", pending["event"]["attemptId"])]
        self.assertEqual(attempt["company"], "原目标")
        self.assertEqual(attempt["status"], "sent")
        self.assertTrue(attempt["identityConflicts"])
        rc, out = self.x.prepare(kind="greet", company="错绑目标", targetRole="开发")
        self.assertEqual(rc, 1, out)
        self.assertEqual(out["reasons"][0]["code"], "already_contacted")

    def test_account_nfkc_collision_does_not_close_another_namespace(self):
        self.x.current("a|b")
        rc, out = self.x.prepare()
        self.assertEqual(rc, 0, out)
        self.x.current("a｜b")
        before = ledger.events_path(self.f.ws).read_bytes()
        self.assertEqual(self.f.append(kind="reply", status="sent", runId="run-prepare", attemptId=out["attemptId"], account="a｜b")[0], 2)
        self.assertEqual(ledger.events_path(self.f.ws).read_bytes(), before)

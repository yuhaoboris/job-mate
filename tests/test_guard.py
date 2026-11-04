"""T5.2 点击前复查：只能复核当前已冻结的授权，不另选来源。"""
import json
import unittest

import test_prepare

ledger = test_prepare.ledger


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.x = test_prepare.PrepareTest()
        self.x.setUp()
        self.x.reset_case()
        self.f = self.x.f

    def tearDown(self):
        self.x.tearDown()

    def guard(self, out):
        return self.f.run_cli("guard", "--run", "run-prepare", "--attempt", out["attemptId"])

    def test_changed_session_and_global_conditions_are_rechecked(self):
        for observed, code in (("manual", "thread_manual"), ("paused", "thread_paused"), ("job_closed", "thread_closed"),
                               ("stoplist", "stoplist"), ("global", "paused"), ("pending", "unresolved")):
            with self.subTest(observed=observed):
                self.x.reset_case()
                rc, out = self.x.prepare()
                self.assertEqual(rc, 0, out)
                self.assertEqual(self.guard(out)[0], 0)  # 自身pending不阻塞。
                if observed == "stoplist":
                    self.f.run_cli("stoplist", "add", "--name", "合成公司", "--reason", "合成", "--source", "user")
                elif observed == "global":
                    self.f.run_cli("pause", "on", "--reason", "合成暂停")
                elif observed == "pending":
                    self.f.history(kind="reply", status="pending", runId="run-prepare")
                else:
                    self.f.append(kind="observe", observed=observed)
                rc, result = self.guard(out)
                self.assertEqual(rc, 1, result)
                self.assertEqual(result["reasons"][0]["code"], code)

    def test_decision_version_cannot_change_and_its_own_pause_is_narrow(self):
        d = "D-20261104-11"
        self.f.append(kind="observe", observed="decision", decision=d, version=1,
                      steps=[{"step": "s1", "kind": "reply", "text": "批准原话"}])
        self.f.append(kind="observe", observed="paused", decision=d)
        rc, out = self.x.prepare(decision=d, step="s1", text="批准原话")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.guard(out)[0], 0)
        self.f.append(kind="observe", observed="decision", decision=d, version=2,
                      steps=[{"step": "s2", "kind": "reply", "text": "新版原话"}])
        rc, result = self.guard(out)
        self.assertEqual(rc, 1, result)
        self.assertEqual(result["reasons"][0]["code"], "decision_superseded")
        self.assertEqual(ledger.attempts_of(ledger.read_events(self.f.ws)[0])[("boss", "boss-main", out["attemptId"])]["status"], "pending")

    def test_restriction_includes_self_without_charging_twice(self):
        self.assertEqual(self.x.acquire(self.x.restriction())[0], 0)
        rc, out = self.x.prepare()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.guard(out)[0], 0)
        self.f.history(kind="reply", status="pending", runId="run-prepare", company="其他目标")
        rc, result = self.guard(out)
        self.assertEqual(rc, 1, result)
        self.assertEqual(result["reasons"][0]["code"], "run_limit_exhausted")

    def test_frozen_grant_is_not_charged_twice_or_replaced_by_new_permission(self):
        self.x.profile["authorization"]["followup"]["reply"] = "L1"
        self.x.save_profile()
        grant = {"item": "reply", "scope": {"all": True}, "max": 1, "quote": "本人回复一条"}
        self.assertEqual(self.x.acquire(grants=[grant])[0], 0)
        rc, out = self.x.prepare()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.guard(out)[0], 0)
        self.assertEqual(self.f.run_cli("lock", "status")[1]["lock"]["grants"][0]["used"], 1)
        self.x.profile["authorization"]["followup"]["reply"] = "L2"
        self.x.save_profile()
        self.assertEqual(self.guard(out)[1]["authorizedBy"], "grant:g1")
        lock = ledger.read_lock(self.f.ws)
        lock["grants"] = []
        ledger.write_lock(self.f.ws, lock)
        self.assertEqual(self.guard(out)[0], 1)

    def test_ack_only_ignores_its_invitation_pause_and_never_other_attempts(self):
        for blocker in ("pause", "anonymous", "pending", "duplicate"):
            with self.subTest(blocker=blocker):
                self.x.reset_case()
                d = "D-20261104-11"
                self.f.append(kind="observe", observed="interview_invited", decision=d)
                self.f.append(kind="observe", observed="paused", decision=d)
                rc, out = self.x.prepare(decision=d, step="ack", text="收到，我确认一下时间")
                self.assertEqual(rc, 0, out)
                self.assertEqual(self.guard(out)[0], 0)
                if blocker in ("pause", "anonymous"):
                    self.f.append(kind="observe", observed="paused", **({"decision": "D-20261104-12"} if blocker == "pause" else {}))
                else:
                    self.f.history(kind="reply", status="pending", runId="run-prepare",
                                   **({"decision": d, "step": "ack"} if blocker == "duplicate" else {}))
                rc, result = self.guard(out)
                self.assertEqual(rc, 1, result)
                self.assertEqual(result["reasons"][0]["code"], "thread_paused" if blocker in ("pause", "anonymous")
                                 else "ack_duplicate" if blocker == "duplicate" else "unresolved")

    def test_long_term_cannot_fall_back_to_a_grant_after_prepare(self):
        grant = {"item": "reply", "scope": {"all": True}, "max": 1, "quote": "合成"}
        self.assertEqual(self.x.acquire(grants=[grant])[0], 0)
        rc, out = self.x.prepare()
        self.assertEqual(out["authorizedBy"], "long_term")
        self.x.profile["authorization"]["followup"]["reply"] = "L1"
        self.x.save_profile()
        rc, result = self.guard(out)
        self.assertEqual(rc, 1, result)
        self.assertEqual(result["reasons"][0]["code"], "not_authorized")

    def test_forged_ack_without_prior_invitation_or_valid_source_is_rejected(self):
        for condition in ("missing", "other_target", "unauthorized", "late_invitation"):
            with self.subTest(condition=condition):
                self.x.reset_case()
                d = "D-20261104-11"
                if condition in ("other_target", "unauthorized"):
                    self.f.append(kind="observe", observed="interview_invited", decision=d,
                                  **({"recruiter": "其他招聘者(HR)"} if condition == "other_target" else {}))
                _, out = self.f.history(kind="reply", status="pending", runId="run-prepare", decision=d, step="ack",
                                        text="收到，我确认一下时间", authorizedBy="long_term")
                if condition == "unauthorized":
                    self.x.profile["authorization"]["followup"]["reply"] = "L1"
                    self.x.save_profile()
                elif condition == "late_invitation":
                    self.f.append(kind="observe", observed="interview_invited", decision=d)
                rc, result = self.guard(out["event"])
                self.assertEqual(rc, 1, result)
                self.assertEqual(result["reasons"][0]["code"], "not_authorized" if condition == "unauthorized" else "ack_invalid_invitation")

    def test_dedup_waiver_must_remain_bound_and_cannot_be_deleted(self):
        permission, waiver, intent = self.x.dedup_setup()
        self.assertEqual(self.x.acquire(grants=[permission, waiver])[0], 0)
        rc, out = self.x.prepare(**intent)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.guard(out)[0], 0)
        lock = ledger.read_lock(self.f.ws)
        lock["grants"] = lock["grants"][:1]
        ledger.write_lock(self.f.ws, lock)
        rc, result = self.guard(out)
        self.assertEqual(rc, 1, result)
        self.assertIn(result["reasons"][0]["code"], ("lock_invalid", "already_contacted", "grant_out_of_scope"))

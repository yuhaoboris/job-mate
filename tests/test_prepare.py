"""阶段5发送准备；真实CLI、合成工作区，绝不触碰平台。"""
import json
import os
import subprocess
import sys
import unittest

import test_tail

ledger = test_tail.ledger


class PrepareTest(unittest.TestCase):
    def setUp(self):
        self.f = test_tail.TailTest()
        self.f.setUp()

    def tearDown(self):
        self.f.tearDown()

    def acquire(self, restriction=None, grants=(), no_send=False, run="run-prepare"):
        current = ledger.read_lock(self.f.ws)
        if current:
            self.assertEqual(self.f.run_cli("lock", "release", "--run", current["runId"])[0], 0)
        args = ["lock", "acquire", "--skill", "followup", "--run", run]
        if restriction is not None:
            args += ["--restrict", json.dumps(restriction, ensure_ascii=False)]
        for grant in grants:
            args += ["--grant", json.dumps(grant, ensure_ascii=False)]
        if no_send:
            args += ["--no-send"]
        return self.f.run_cli(*args)

    def restriction(self, scope=None, max=1, kinds=None):
        return {"kinds": kinds or ["reply"], "scope": scope or {"all": True}, "max": max, "quote": "本轮合成限制"}

    def prepare(self, **fields):
        body = dict(self.f.identity, kind="reply", text="合成回答", reason="合成依据")
        body.update(fields)
        if body["kind"] == "resume":
            body.pop("text", None)
        return self.f.run_cli("prepare", "--run", "run-prepare", "--json", json.dumps(body, ensure_ascii=False))

    def test_prepare_writes_frozen_source_and_append_cannot_create_pending(self):
        self.assertEqual(self.acquire()[0], 0)
        code, out = self.prepare()
        self.assertEqual(code, 0, out)
        self.assertTrue(out["allowed"])
        event = ledger.read_events(self.f.ws)[0][-1]
        self.assertEqual(event["authorizedBy"], "long_term")
        self.assertEqual(event["attemptId"], out["attemptId"])
        self.assertEqual(event["status"], "pending")
        self.assertRegex(event["profileRev"], "^[0-9a-f]{12}$")
        self.assertRegex(event["styleRev"], "^[0-9a-f]{12}$")
        before = ledger.events_path(self.f.ws).read_bytes()
        self.assertEqual(self.f.append(kind="reply", status="pending")[0], 2)
        self.assertEqual(ledger.events_path(self.f.ws).read_bytes(), before)

    def test_restriction_precedes_authorization_and_never_grants_permission(self):
        ledger.write_atomic(self.f.ws / "profile.json", json.dumps({"authorization": {"followup": {"reply": "L1"}}}))
        self.assertEqual(self.acquire(self.restriction(scope={"companies": ["甲公司"]}))[0], 0)
        code, out = self.prepare(company="乙公司")
        self.assertEqual(code, 1, out)
        self.assertEqual(out["reasons"][0]["code"], "run_scope_restricted")
        code, out = self.prepare(company="甲公司")
        self.assertEqual(code, 1, out)
        self.assertEqual(out["reasons"][0]["code"], "not_authorized")

    def reset_case(self):
        self.f.tearDown()
        self.f.setUp()
        self.profile = {"authorization": {"apply": {"send": True}, "followup": {"reply": "L2"}},
                        "limits": {"minSendIntervalSeconds": 0}}
        self.save_profile()
        self.assertEqual(self.acquire()[0], 0)

    def save_profile(self):
        ledger.write_atomic(self.f.ws / "profile.json", json.dumps(self.profile))

    def historical(self, final="sent", **fields):
        code, out = self.f.history(kind="reply", status="pending", runId="run-prepare", **fields)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.f.history(kind="reply", status=final, runId="run-prepare", attemptId=out["event"]["attemptId"], **fields)[0], 0)

    def check_intent(self, body):
        return self.f.run_cli("check", "--run", "run-prepare", "--json", json.dumps(body, ensure_ascii=False))

    def test_reason_codes_have_allowed_control_and_rejected_case(self):
        codes = ("lock_invalid", "no_send", "paused", "not_authorized", "empty_text", "banned_word", "too_long",
                 "interval", "stoplist", "stoplist_suspect", "already_contacted", "company_rejected", "company_risk",
                 "quota_run", "quota_day", "outside_hours", "thread_closed", "thread_manual", "thread_paused", "unresolved",
                 "run_kind_restricted", "run_scope_restricted", "run_limit_exhausted", "run_uncertain_limit")
        for code in codes:
            with self.subTest(code=code):
                self.reset_case()
                body = dict(self.f.identity, kind="greet" if code in ("already_contacted", "company_rejected", "company_risk",
                    "quota_run", "quota_day", "outside_hours") else "reply", reason="合成依据", text="合成回答", targetRole="开发")
                rc, out = self.check_intent(body)
                self.assertEqual(rc, 0, out)  # 同一真实判定路径的正例，且不占用 pending。
                if code == "lock_invalid":
                    self.f.run_cli("lock", "release", "--run", "run-prepare")
                elif code == "no_send":
                    self.assertEqual(self.acquire(no_send=True)[0], 0)
                elif code == "paused":
                    self.f.run_cli("pause", "on", "--reason", "合成暂停")
                elif code == "not_authorized":
                    self.profile["authorization"]["followup"]["reply"] = "L1"
                elif code in ("empty_text", "banned_word", "too_long"):
                    body["text"] = {"empty_text": " \n　", "banned_word": "精 通", "too_long": "字" * 151}[code]
                elif code == "interval":
                    self.profile["limits"]["minSendIntervalSeconds"] = 5
                    self.historical(company="独立目标")
                elif code in ("stoplist", "stoplist_suspect"):
                    self.f.run_cli("stoplist", "add", "--name", "合成公司" if code == "stoplist" else "合成",
                                   "--reason", "合成", "--source", "user")
                elif code == "already_contacted":
                    self.historical(recruiter="另一招聘者(HR)")
                elif code in ("company_rejected", "company_risk"):
                    self.f.append(kind="observe", observed="rejected" if code == "company_rejected" else "risk")
                elif code in ("quota_run", "quota_day"):
                    self.profile["limits"]["greetsPerRun" if code == "quota_run" else "greetsPerDay"] = 0
                elif code == "outside_hours":
                    self.profile["schedule"] = {"activeHours": {"weekdays": [1], "start": "09:00", "end": "10:00"}}
                elif code in ("thread_closed", "thread_manual", "thread_paused"):
                    self.f.append(kind="observe", observed={"thread_closed": "job_closed", "thread_manual": "manual", "thread_paused": "paused"}[code])
                elif code == "unresolved":
                    self.f.history(kind="reply", status="pending", runId="run-prepare")
                elif code.startswith("run_") and code != "run_uncertain_limit":
                    rule = self.restriction(kinds=["resume"] if code == "run_kind_restricted" else ["reply"],
                        scope={"companies": ["另一个目标"]} if code == "run_scope_restricted" else None,
                        max=0 if code == "run_limit_exhausted" else 1)
                    self.assertEqual(self.acquire(rule)[0], 0)
                elif code == "run_uncertain_limit":
                    self.historical(final="uncertain", company="独立目标甲")
                    self.historical(final="uncertain", company="独立目标乙")
                self.save_profile()
                before = ledger.events_path(self.f.ws).read_bytes()
                rc, out = self.f.run_cli("prepare", "--run", "run-prepare", "--json", json.dumps(body))
                self.assertEqual(rc, 1, out)
                self.assertEqual(out["reasons"][0]["code"], code)
                self.assertEqual(ledger.events_path(self.f.ws).read_bytes(), before)

    def test_grants_limits_and_failed_attempts_never_refund(self):
        self.reset_case()
        self.profile["authorization"]["followup"]["reply"] = "L1"
        self.save_profile()
        grant = {"item": "reply", "scope": {"companies": ["合成公司"]}, "max": 1, "quote": "本人本轮授权"}
        self.assertEqual(self.acquire(self.restriction(max=2), [grant])[0], 0)
        code, out = self.prepare(company="独立目标")
        self.assertEqual(out["reasons"][0]["code"], "grant_out_of_scope")
        code, out = self.prepare()
        self.assertEqual(code, 0, out)
        self.assertEqual(out["authorizedBy"], "grant:g1")
        self.assertEqual(self.f.append(kind="reply", status="failed_no_send", runId="run-prepare", attemptId=out["attemptId"])[0], 0)
        code, out = self.prepare()
        self.assertEqual(out["reasons"][0]["code"], "grant_exhausted")
        status = self.f.run_cli("lock", "status")[1]["lock"]
        self.assertEqual(status["restriction"]["used"], 1)
        self.assertEqual(status["grants"][0]["used"], 1)

    def test_decision_and_ack_reason_codes_preserve_original_approved_text(self):
        decision_codes = ("decision_not_registered", "decision_superseded", "decision_unresolved", "decision_text_mismatch",
                          "decision_step_done", "decision_needs_review", "ack_invalid_invitation", "ack_duplicate", "ack_contains_time")
        for code in decision_codes:
            with self.subTest(code=code):
                self.reset_case()
                d = "D-20261104-11"
                text = " \n批准Ａ　原话\u2028不改写 "
                ack = code.startswith("ack_")
                body = dict(self.f.identity, kind="reply", reason="合成", decision=d, step="ack" if ack else "s1",
                            text="收到，我确认一下时间" if ack else text)
                if ack:
                    self.f.append(kind="observe", observed="interview_invited", decision=d)
                else:
                    self.f.append(kind="observe", observed="decision", decision=d, version=1,
                                  steps=[{"step": "s1", "kind": "reply", "text": text}])
                self.f.append(kind="observe", observed="paused", decision=d)
                rc, out = self.check_intent(body)
                self.assertEqual(rc, 0, out)
                if code == "decision_not_registered": body["step"] = "unknown"
                elif code == "decision_superseded":
                    self.f.append(kind="observe", observed="decision", decision=d, version=2, steps=[{"step": "s2", "kind": "reply", "text": "新版"}])
                elif code == "decision_unresolved":
                    self.f.history(kind="reply", status="pending", runId="run-prepare", decision=d, step="s1", text=text)
                elif code == "decision_text_mismatch": body["text"] = text.strip()
                elif code in ("decision_step_done", "decision_needs_review", "ack_duplicate"):
                    self.historical(decision=d, step="unknown" if code == "decision_needs_review" else body["step"], text=body["text"])
                elif code == "ack_invalid_invitation": body["decision"] = "D-20261104-12"
                elif code == "ack_contains_time": body["text"] = "明天１４点"
                before = ledger.events_path(self.f.ws).read_bytes()
                rc, out = self.f.run_cli("prepare", "--run", "run-prepare", "--json", json.dumps(body))
                self.assertEqual(rc, 1, out)
                self.assertEqual(out["reasons"][0]["code"], code)
                self.assertEqual(ledger.events_path(self.f.ws).read_bytes(), before)
                declarations = [e for e in ledger.read_events(self.f.ws)[0] if e.get("observed") == "decision"]
                if not ack: self.assertEqual(declarations[0]["steps"][0]["text"], text)

    def test_recovery_lock_nudge_and_authorization_truth_table(self):
        for reply in ("L1", "L2"):
            for nudge in (False, True):
                for kind in ("reply", "resume", "nudge"):
                    with self.subTest(reply=reply, nudge=nudge, kind=kind):
                        self.reset_case()
                        self.profile["authorization"]["followup"] = {"reply": reply, "nudge": nudge}
                        self.save_profile()
                        rc, out = self.prepare(kind=kind, **({"resume": "合成版本"} if kind == "resume" else {}))
                        allowed = reply == "L2" and kind != "nudge"
                        self.assertEqual(rc, 0 if allowed else 1, out)
                        if not allowed: self.assertEqual(out["reasons"][0]["code"], "not_authorized")
        self.reset_case()
        self.f.run_cli("lock", "release", "--run", "run-prepare")
        self.assertEqual(self.f.run_cli("lock", "acquire", "--skill", "setup", "--recovery", "--run", "run-prepare")[0], 0)
        body = dict(self.f.identity, kind="reply", reason="合成", text="合成回答")
        rc, out = self.check_intent(body)
        self.assertEqual(rc, 1, out)
        self.assertEqual(out["reasons"][0]["code"], "recovery_lock_only")

    def test_decision_is_frozen_not_trimmed_and_bypasses_only_text_style(self):
        self.reset_case()
        self.profile["authorization"]["followup"]["reply"] = "L1"
        self.save_profile()
        text = " \n精通Ａ\u2028" + "字" * 160 + "\r\n "
        d = "D-20261104-11"
        self.f.append(kind="observe", observed="decision", decision=d, version=1,
                      steps=[{"step": "s1", "kind": "reply", "text": text}])
        code, out = self.prepare(decision=d, step="s1", text=text)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["authorizedBy"], "decision:" + d + "@v1")
        pending = ledger.read_events(self.f.ws)[0][-1]
        self.assertEqual(pending["text"], text)
        self.assertEqual(pending["decisionVersion"], 1)
        for field, value in (("authorizedBy", "long_term"), ("decisionVersion", 2), ("attemptId", "att-forged"),
                             ("profileRev", "0" * 12), ("scopeBindings", {})):
            self.assertEqual(self.prepare(**{field: value})[0], 2)

    def test_reposted_id_and_apply_authorization(self):
        self.reset_case()
        body = dict(self.f.identity, kind="greet", reason="合成依据", text="合成招呼", targetRole="开发",
                    jobUrl="https://www.zhipin.com/job_detail/job-B.html")
        self.assertEqual(self.check_intent(body)[0], 0)
        self.historical(jobId="job-A")
        rc, out = self.f.run_cli("prepare", "--run", "run-prepare", "--json", json.dumps(body))
        self.assertEqual(out["reasons"][0]["code"], "reposted_suspect")
        self.reset_case()
        self.profile["authorization"]["apply"]["send"] = False
        self.save_profile()
        rc, out = self.prepare(kind="greet", targetRole="开发")
        self.assertEqual(out["reasons"][0]["code"], "not_authorized")

    def test_invalid_restriction_and_grant_structures_are_rejected_without_lock(self):
        rule = self.restriction()
        invalid = [{}, dict(rule, max=True), dict(rule, max=-1), dict(rule, max=1.0), dict(rule, kinds=[]),
                   dict(rule, kinds=["reply", "reply"]), dict(rule, scope={"all": 1}),
                   dict(rule, scope={"all": True, "companies": ["甲"]}), dict(rule, quote=" "), dict(rule, scopeBindings={})]
        for value in invalid:
            with self.subTest(rule=value):
                self.assertEqual(self.acquire(value)[0], 2)
                self.assertFalse(ledger.lock_path(self.f.ws).exists())
        grant = {"item": "reply", "scope": {"all": True}, "max": 1, "quote": "合成"}
        for value in ({k: v for k, v in grant.items() if k != "max"}, dict(grant, max=0), dict(grant, scope={}),
                      dict(grant, id="g1"), dict(grant, item="hours")):
            self.assertEqual(self.acquire(grants=[value])[0], 2)
        self.assertEqual(self.acquire(grants=[grant], no_send=True)[0], 2)
        raw = json.dumps(rule)
        self.assertEqual(self.f.run_cli("lock", "acquire", "--skill", "followup", "--restrict", raw, "--restrict", raw)[0], 2)

    def test_cross_source_counts_and_renewal_do_not_refund(self):
        self.reset_case()
        grant = {"item": "reply", "scope": {"all": True}, "max": 5, "quote": "合成追加授权"}
        self.assertEqual(self.acquire(self.restriction(max=2), [grant])[0], 0)
        rc, first = self.prepare(company="目标甲")
        self.assertEqual(first["authorizedBy"], "long_term")
        self.f.append(kind="reply", company="目标甲", status="failed_no_send", runId="run-prepare", attemptId=first["attemptId"])
        d = "D-20261104-11"
        self.f.append(kind="observe", company="目标乙", observed="decision", decision=d, version=1,
                      steps=[{"step": "s1", "kind": "reply", "text": "批准原话"}])
        rc, second = self.prepare(company="目标乙", decision=d, step="s1", text="批准原话")
        self.assertEqual(rc, 0, second)
        self.assertEqual(second["authorizedBy"], "decision:" + d + "@v1")
        self.f.append(kind="reply", company="目标乙", status="failed_no_send", runId="run-prepare", attemptId=second["attemptId"])
        self.profile["authorization"]["followup"]["reply"] = "L1"
        self.save_profile()
        self.f.run_cli("lock", "renew", "--run", "run-prepare")
        rc, out = self.prepare(company="目标丙")
        self.assertEqual(out["reasons"][0]["code"], "run_limit_exhausted")
        status = self.f.run_cli("lock", "status")[1]["lock"]
        self.assertEqual(status["restriction"]["used"], 2)
        self.assertEqual(status["grants"][0]["used"], 0)

    def test_all_outbound_types_share_interval_and_return_retry(self):
        self.reset_case()
        self.profile["limits"]["minSendIntervalSeconds"] = 5
        self.save_profile()
        rc, first = self.prepare()
        self.assertEqual(rc, 0, first)
        self.f.append(kind="reply", status="sent", runId="run-prepare", attemptId=first["attemptId"])
        rc, out = self.prepare(kind="resume", resume="合成版本")
        self.assertEqual(out["reasons"][0]["code"], "interval")
        self.assertEqual(out["retryAfterSeconds"], 5)
        os.environ["JOB_MATE_NOW"] = "2026-11-04T12:00:05+08:00"
        self.assertEqual(self.prepare(kind="resume", resume="合成版本")[0], 0)

    def test_two_real_processes_compete_for_last_quota_or_same_company(self):
        for quota in (True, False):
            with self.subTest(quota=quota):
                self.reset_case()
                self.profile["limits"]["greetsPerRun"] = 1 if quota else 10
                self.save_profile()
                processes = []
                for company in (("并发甲", "并发乙") if quota else ("同一目标", "同一目标")):
                    body = dict(self.f.identity, kind="greet", company=company, text="合成招呼", targetRole="开发", reason="合成依据")
                    processes.append(subprocess.Popen([sys.executable, "-I", str(test_tail.ROOT / "scripts/ledger.py"),
                        "--workspace", str(self.f.ws), "prepare", "--run", "run-prepare", "--json", json.dumps(body)],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8"))
                outputs = []
                try:
                    for process in processes:
                        stdout, stderr = process.communicate(timeout=30)
                        self.assertFalse(stderr, stderr)
                        outputs.append(json.loads(stdout))
                finally:
                    for process in processes:
                        if process.poll() is None:
                            process.kill()
                            process.communicate()
                self.assertEqual(sum(out["allowed"] for out in outputs), 1, outputs)
                pending = [e for e in ledger.read_events(self.f.ws)[0] if e.get("status") == "pending"]
                self.assertEqual(len(pending), 1)

    def dedup_setup(self, contacted=True):
        self.reset_case()
        self.f.append(kind="screen", runId="run-prepare", result="qualified", reasons=[], jobId="dedup-job")
        if contacted:
            self.historical(jobId="dedup-job")
        self.profile["authorization"]["apply"]["send"] = False
        self.save_profile()
        permission = {"item": "apply_send", "scope": {"all": True}, "max": 3, "quote": "本人允许发送"}
        waiver = {"item": "dedup_once", "scope": {"jobs": ["boss|dedup-job"]}, "max": 1, "quote": "本人明确再次联系该职位"}
        intent = {"kind": "greet", "targetRole": "开发", "jobUrl": "https://www.zhipin.com/job_detail/dedup-job.html"}
        return permission, waiver, intent

    def test_dedup_once_is_separate_single_use_and_check_does_not_consume(self):
        permission, waiver, intent = self.dedup_setup()
        self.assertEqual(self.acquire(grants=[permission])[0], 0)
        self.assertEqual(self.prepare(**intent)[1]["reasons"][0]["code"], "already_contacted")
        self.assertEqual(self.acquire(grants=[waiver])[0], 0)
        self.assertEqual(self.prepare(**intent)[1]["reasons"][0]["code"], "not_authorized")
        self.assertEqual(self.acquire(self.restriction(kinds=["greet"], max=2), [permission, waiver])[0], 0)
        body = dict(self.f.identity, reason="合成", text="合成招呼", **intent)
        before = ledger.events_path(self.f.ws).read_bytes()
        self.assertEqual(self.check_intent(body)[0], 0)
        self.assertEqual(ledger.events_path(self.f.ws).read_bytes(), before)
        rc, out = self.prepare(**intent)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["authorizedBy"], "grant:g1")
        pending = ledger.read_events(self.f.ws)[0][-1]
        self.assertEqual(pending["dedupGrant"], "g2")
        self.f.append(kind="greet", status="failed_no_send", runId="run-prepare", attemptId=out["attemptId"],
                      jobUrl=intent["jobUrl"])
        status = self.f.run_cli("lock", "status")[1]["lock"]
        self.assertEqual([g["used"] for g in status["grants"]], [1, 1])
        self.assertEqual(self.prepare(**intent)[1]["reasons"][0]["code"], "already_contacted")
        self.assertEqual(self.prepare(dedupGrant="g2", **intent)[0], 2)

    def test_dedup_once_no_unnecessary_consumption_or_unsafe_override(self):
        permission, waiver, intent = self.dedup_setup(contacted=False)
        self.assertEqual(self.acquire(grants=[permission, waiver])[0], 0)
        rc, out = self.prepare(**intent)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("dedupGrant", ledger.read_events(self.f.ws)[0][-1])
        self.assertEqual(self.f.run_cli("lock", "status")[1]["lock"]["grants"][1]["used"], 0)
        for cause in ("quota", "unresolved", "button"):
            with self.subTest(cause=cause):
                permission, waiver, intent = self.dedup_setup()
                self.assertEqual(self.acquire(grants=[permission, waiver])[0], 0)
                if cause == "quota":
                    self.profile["limits"]["greetsPerRun"] = 0
                    self.save_profile()
                elif cause == "unresolved":
                    self.f.history(kind="reply", status="pending", runId="run-prepare", jobId="dedup-job", recruiter="另一招聘者(HR)")
                else:
                    intent["pageState"] = {"contacted": True}
                self.assertEqual(self.prepare(**intent)[0], 1)
                self.assertEqual(self.f.run_cli("lock", "status")[1]["lock"]["grants"][1]["used"], 0)
        permission, waiver, intent = self.dedup_setup()
        self.assertEqual(self.acquire(grants=[permission, waiver, waiver])[0], 2)
        for invalid in (dict(waiver, max=True), dict(waiver, max=2), dict(waiver, scope={"all": True})):
            self.assertEqual(self.acquire(grants=[invalid])[0], 2)

    def test_ack_full_chain_l2_and_l1_grant_keeps_the_pause(self):
        for level in ("L1", "L2"):
            self.reset_case()
            self.profile["authorization"]["followup"]["reply"] = level
            self.save_profile()
            d = "D-20261104-11"
            self.f.append(kind="observe", observed="interview_invited", decision=d)
            self.f.append(kind="observe", observed="paused", decision=d)
            if level == "L1":
                self.assertEqual(self.prepare(decision=d, step="ack", text="收到，我确认一下时间")[0], 1)
                grant = {"item": "reply", "scope": {"threads": [self.f.thread]}, "max": 1, "quote": "本轮回执"}
                self.assertEqual(self.acquire(grants=[grant])[0], 0)
            rc, out = self.prepare(decision=d, step="ack", text="收到，我确认一下时间")
            self.assertEqual(rc, 0, out)
            self.assertNotIn("decisionVersion", ledger.read_events(self.f.ws)[0][-1])
            self.assertEqual(self.f.run_cli("guard", "--run", "run-prepare", "--attempt", out["attemptId"])[0], 0)
            thread = next(t for t in self.f.run_cli("threads")[1]["threads"] if t["recruiter"] == self.f.identity["recruiter"])
            self.assertEqual(thread["pausedBy"], [d])

    def test_hours_grant_intro_and_reacquire_cannot_change_spent_policy(self):
        self.reset_case()
        self.profile["schedule"] = {"activeHours": {"weekdays": [1], "start": "09:00", "end": "10:00"}}
        self.save_profile()
        grant = {"item": "hours", "scope": {"all": True}, "quote": "本轮可越过时段"}
        rule = self.restriction(kinds=["greet", "intro"], max=2)
        self.assertEqual(self.acquire(rule, [grant])[0], 0)
        rc, greet = self.prepare(kind="greet", targetRole="开发")
        self.assertEqual(rc, 0, greet)
        self.f.append(kind="greet", status="sent", runId="run-prepare", attemptId=greet["attemptId"])
        rc, intro = self.prepare(kind="intro", text="合成补发")
        self.assertEqual(rc, 0, intro)
        self.assertEqual(self.f.run_cli("lock", "status")[1]["lock"]["restriction"]["used"], 2)
        self.f.append(kind="intro", status="failed_no_send", runId="run-prepare", attemptId=intro["attemptId"])
        self.assertEqual(self.acquire(self.restriction(kinds=["greet", "intro"], max=3), [grant])[0], 2)

    def current(self, key):
        meta = ledger.read_json(self.f.ws / ledger.MARKER)
        meta["accounts"]["boss"]["accountKey"] = key
        ledger.write_atomic(self.f.ws / ledger.MARKER, json.dumps(meta))

    def screen(self, job_id):
        code, out = self.f.append(kind="screen", result="qualified", reasons=["合成"], jobId=job_id)
        self.assertEqual(code, 0, out)
        return out["event"]["jobKey"]

    def test_unregistered_or_ambiguous_job_scope_never_writes_lock(self):
        for key in ("boss|unknown",):
            code, out = self.acquire(self.restriction(scope={"jobs": [key]}))
            self.assertEqual(code, 2, out)
            self.assertFalse(ledger.lock_path(self.f.ws).exists())
        self.assertEqual(self.acquire(no_send=True, run="run-tail")[0], 0)
        key = self.screen("a|b")
        self.assertEqual(self.screen("a｜b"), key)
        code, out = self.acquire(self.restriction(scope={"jobs": [key]}))
        self.assertEqual(code, 2, out)
        self.assertFalse(ledger.lock_path(self.f.ws).exists())

    def test_binding_survives_later_job_collision_and_renewal(self):
        self.assertEqual(self.acquire(no_send=True, run="run-tail")[0], 0)
        key = self.screen("a|b")
        self.assertEqual(self.acquire(self.restriction(scope={"jobs": [key]}))[0], 0)
        original = ledger.read_lock(self.f.ws)["scopeBindings"]
        self.assertEqual(original["restriction"][0]["identity"], {"jobId": "a|b"})
        code, out = self.f.append(kind="screen", runId="run-prepare", result="qualified", reasons=[], jobId="a｜b")
        self.assertEqual(code, 0, out)
        code, out = self.prepare(jobUrl="https://www.zhipin.com/job_detail/a｜b.html")
        self.assertEqual(code, 1, out)
        self.assertEqual(out["reasons"][0]["code"], "run_scope_restricted")
        self.assertEqual(self.f.run_cli("lock", "renew", "--run", "run-prepare")[0], 0)
        self.assertEqual(ledger.read_lock(self.f.ws)["scopeBindings"], original)
        self.assertEqual(self.prepare(jobUrl="https://www.zhipin.com/job_detail/a|b.html")[0], 0)

    def test_all_grant_types_bind_targets_and_reject_forged_binding(self):
        for item in ("reply", "apply_send", "nudge", "hours"):
            with self.subTest(item=item):
                grant = {"item": item, "scope": {"threads": [self.f.thread]}, "quote": "合成例外"}
                if item != "hours":
                    grant["max"] = 1
                code, out = self.acquire(grants=[grant])
                self.assertEqual(code, 0, out)
                self.assertEqual(out["lock"]["scopeBindings"]["g1"][0]["account"], "boss-main")
                grant["bindings"] = []
                self.assertEqual(self.acquire(grants=[grant])[0], 2)
                self.assertFalse(ledger.lock_path(self.f.ws).exists())

    def test_thread_collision_at_acquire_rejected_and_later_collision_cannot_rebind(self):
        self.current("a|b")
        self.assertEqual(self.f.append(kind="observe", observed="replied")[0], 0)
        key = "boss|a｜b|合成公司|招聘者(hr)"
        self.assertEqual(self.acquire(self.restriction(scope={"threads": [key]}))[0], 0)
        self.current("a｜b")
        self.assertEqual(self.f.append(kind="observe", observed="replied")[0], 0)
        code, out = self.prepare()
        self.assertEqual(code, 1, out)
        self.assertEqual(out["reasons"][0]["code"], "run_scope_restricted")
        self.assertEqual(self.acquire(self.restriction(scope={"threads": [key]}))[0], 2)
        self.assertFalse(ledger.lock_path(self.f.ws).exists())

    def test_restriction_is_stored_and_cannot_be_replaced_mid_run(self):
        rule = self.restriction()
        code, out = self.acquire(rule)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["lock"]["restriction"], rule)
        code, status = self.f.run_cli("lock", "status")
        self.assertEqual(code, 0, status)
        self.assertEqual(status["lock"]["restriction"]["used"], 0)
        self.assertEqual(status["lock"]["restriction"]["remaining"], 1)
        code, out = self.f.run_cli("lock", "acquire", "--skill", "followup", "--run", "run-prepare",
                                   "--restrict", json.dumps(self.restriction(max=2)))
        self.assertEqual(code, 2, out)
        self.assertEqual(ledger.read_lock(self.f.ws)["restriction"], rule)

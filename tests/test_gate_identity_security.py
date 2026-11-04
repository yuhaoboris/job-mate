"""P1定向回归：所有协议断言直接调用真实CLI子进程，不补命令参数。"""
import json
import os
import subprocess
from unittest.mock import patch
import sys
import unittest

import test_prepare

ledger = test_prepare.ledger
ROOT = test_prepare.test_tail.ROOT


class GateIdentitySecurityTest(unittest.TestCase):
    def setUp(self):
        self.x = test_prepare.PrepareTest()
        self.x.setUp()
        self.reset()

    def reset(self):
        self.x.reset_case()
        self.f = self.x.f
        self.f.run_cli = self.cli

    def tearDown(self):
        self.x.tearDown()

    def cli(self, *args, stdin=None):
        result = subprocess.run([sys.executable, "-B", "-I", str(ROOT / "scripts/ledger.py"),
                                 "--workspace", str(self.f.ws), *args], input=stdin,
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertFalse(result.stderr, result.stderr)
        return result.returncode, json.loads(result.stdout)

    def rejected(self, result, code):
        rc, out = result
        self.assertEqual(rc, 1, out)
        self.assertFalse(out["allowed"])
        self.assertEqual(out["reasons"][0]["code"], code)

    def guard(self, out):
        return self.cli("guard", "--run", "run-prepare", "--attempt", out["attemptId"])

    def declare(self, **fields):
        d = "D-20261104-88"
        self.x.profile["authorization"]["followup"]["reply"] = "L1"
        self.x.save_profile()
        rc, out = self.f.append(kind="observe", observed="decision", decision=d, version=1,
                               steps=[{"step": "s1", "kind": "reply", "text": "批准原话"}], **fields)
        self.assertEqual(rc, 0, out)
        return d

    def test_known_and_unknown_observation_block_both_directions(self):
        for blocker, code in (("manual", "thread_manual"), ("paused", "thread_paused"), ("job_closed", "thread_closed")):
            for direction in ("known_to_unknown", "unknown_to_known"):
                with self.subTest(blocker=blocker, direction=direction):
                    self.reset()
                    observer = {} if direction == "known_to_unknown" else {"recruiter": None}
                    self.assertEqual(self.f.append(kind="observe", observed=blocker, **observer)[0], 0)
                    self.rejected(self.x.prepare(**observer), code)
                    before = ledger.events_path(self.f.ws).read_bytes()
                    request = {"recruiter": None} if direction == "known_to_unknown" else {}
                    self.rejected(self.x.prepare(**request), code)
                    self.assertEqual(ledger.events_path(self.f.ws).read_bytes(), before)

    def test_pending_and_uncertain_compatibility_is_bidirectional(self):
        for unknown_first in (False, True):
            for status in ("pending", "uncertain"):
                with self.subTest(unknown_first=unknown_first, status=status):
                    self.reset()
                    fields = {"recruiter": None} if unknown_first else {}
                    rc, first = self.x.prepare(**fields)
                    self.assertEqual(rc, 0, first)
                    if status == "uncertain":
                        self.assertEqual(self.f.append(kind="reply", status="uncertain", runId="run-prepare",
                            attemptId=first["attemptId"], uncertaintyClass="business", **fields)[0], 0)
                    self.rejected(self.x.prepare(**({} if unknown_first else {"recruiter": None})), "unresolved")

    def test_guard_rechecks_blockers_added_after_prepare_in_both_directions(self):
        for unknown_request in (False, True):
            for blocker, code in (("manual", "thread_manual"), ("paused", "thread_paused"), ("job_closed", "thread_closed"), ("pending", "unresolved")):
                with self.subTest(unknown_request=unknown_request, blocker=blocker):
                    self.reset()
                    rc, pending = self.x.prepare(**({"recruiter": None} if unknown_request else {}))
                    self.assertEqual(rc, 0, pending)
                    self.assertEqual(self.guard(pending)[0], 0)
                    observer = {} if unknown_request else {"recruiter": None}
                    if blocker == "pending":
                        # 仅故障构造第二条兼容未决，正常prepare在准备阶段已会拒绝。
                        self.f.history(kind="reply", status="pending", runId="run-prepare", text="其他未决", **observer)
                    else:
                        self.assertEqual(self.f.append(kind="observe", observed=blocker, **observer)[0], 0)
                    self.rejected(self.guard(pending), code)

    def test_known_other_recruiter_company_and_raw_account_remain_isolated(self):
        for fields in ({"recruiter": "其他招聘者(HR)"}, {"company": "其他公司"}, {"account": "a|b", "recruiter": None}):
            with self.subTest(fields=fields):
                self.reset()
                if "account" in fields:
                    self.x.current("a|b")
                self.assertEqual(self.f.append(kind="observe", observed="manual", **fields)[0], 0)
                if "account" in fields:
                    self.x.current("a｜b")
                rc, pending = self.x.prepare()
                self.assertEqual(rc, 0, pending)
                self.assertEqual(self.guard(pending)[0], 0)

    def test_intro_never_uses_null_keys_across_or_within_company(self):
        for company in ("甲公司", "乙公司"):
            with self.subTest(company=company):
                self.reset()
                rc, greet = self.x.prepare(kind="greet", company="甲公司", recruiter=None, targetRole="开发")
                self.assertEqual(rc, 0, greet)
                self.assertEqual(self.f.append(kind="greet", company="甲公司", recruiter=None, status="sent",
                    runId="run-prepare", attemptId=greet["attemptId"])[0], 0)
                self.rejected(self.x.prepare(kind="intro", company=company, recruiter=None), "not_authorized")

    def test_explicit_intro_and_normal_long_term_null_completion_still_work(self):
        rc, greet = self.x.prepare(kind="greet", recruiter=None, targetRole="开发")
        self.assertEqual(rc, 0, greet)
        self.assertEqual(self.f.append(kind="greet", status="sent", runId="run-prepare", attemptId=greet["attemptId"],
                                      recruiter="招聘者（ＨＲ）")[0], 0)
        self.rejected(self.x.prepare(kind="intro", recruiter="另一个招聘者(HR)"), "not_authorized")
        rc, intro = self.x.prepare(kind="intro", text="合成补发")
        self.assertEqual(rc, 0, intro)
        self.assertEqual(self.guard(intro)[0], 0)

    def test_decision_known_components_cannot_be_omitted(self):
        for omitted in ("recruiter", "title", "location", "jobUrl"):
            with self.subTest(omitted=omitted):
                self.reset()
                fields = {"location": "合成地点", "jobUrl": "https://www.zhipin.com/job_detail/decision-job.html"}
                d = self.declare(**fields)
                request = dict(decision=d, step="s1", text="批准原话", **fields)
                rc, allowed = self.cli("check", "--run", "run-prepare", "--json", json.dumps(dict(self.f.identity,
                    kind="reply", reason="合成", **request)))
                self.assertEqual(rc, 0, allowed)
                if omitted == "jobUrl":
                    request.pop("jobUrl")
                else:
                    request[omitted] = None
                self.rejected(self.x.prepare(**request), "decision_text_mismatch")
                request[omitted] = "https://www.zhipin.com/job_detail/other-job.html" if omitted == "jobUrl" else "不同的身份分量"
                self.rejected(self.x.prepare(**request), "decision_text_mismatch")

    def test_guard_cannot_use_a_decision_pending_missing_known_recipient(self):
        d = self.declare()
        rc, valid = self.x.prepare(decision=d, step="s1", text="批准原话")
        self.assertEqual(rc, 0, valid)
        self.assertEqual(self.guard(valid)[0], 0)
        events = ledger.read_events(self.f.ws)[0]
        # 保留完整脚本字段，故障改写为过去准入缺口留下的未知目标pending。
        changed = dict(ledger.public_entry(events[-1]), recruiter=None, threadKey=None)
        ledger.write_atomic(ledger.events_path(self.f.ws), "".join(json.dumps(ledger.public_entry(e), ensure_ascii=False) + "\n"
            for e in [*events[:-1], changed]))
        self.rejected(self.guard(valid), "decision_text_mismatch")

    def test_platform_components_and_normalization_are_used_for_blocking(self):
        descriptor = json.loads((ROOT / "tests/fixtures/platforms/testplat/platform.json").read_text(encoding="utf-8"))
        descriptor["conversationKey"] = ["company", "recruiter", "title"]
        root = self.f.ws / "synthetic-platforms"
        (root / "testplat").mkdir(parents=True)
        (root / "testplat/platform.json").write_text(json.dumps(descriptor), encoding="utf-8")
        with patch.dict(os.environ, {"JOB_MATE_TEST_PLATFORM_ROOT": str(root)}):
            self.assertEqual(self.f.append(kind="observe", observed="manual", platform="testplat", recruiter="招聘者（ＨＲ）")[0], 0)
            self.rejected(self.x.prepare(platform="testplat", title=None), "thread_manual")
            rc, other = self.x.prepare(platform="testplat", title="另一职位")
            self.assertEqual(rc, 0, other)
            self.assertEqual(self.guard(other)[0], 0)
            # 同公司同招聘者，原始平台不同则不串用阻断。
            rc, boss = self.x.prepare()
            self.assertEqual(rc, 0, boss)
            self.assertEqual(self.guard(boss)[0], 0)

    def test_exact_decision_and_ack_can_bypass_only_their_own_pause(self):
        for ack in (False, True):
            with self.subTest(ack=ack):
                self.reset()
                d = "D-20261104-88" if ack else self.declare()
                if ack:
                    self.assertEqual(self.f.append(kind="observe", observed="interview_invited", decision=d)[0], 0)
                self.assertEqual(self.f.append(kind="observe", observed="paused", decision=d)[0], 0)
                rc, pending = self.x.prepare(decision=d, step="ack" if ack else "s1", text="收到，我确认一下时间" if ack else "批准原话")
                self.assertEqual(rc, 0, pending)
                self.assertEqual(self.guard(pending)[0], 0)

    def test_unknown_pause_is_not_positive_proof_for_decision_or_ack(self):
        for ack in (False, True):
            with self.subTest(ack=ack):
                self.reset()
                d = "D-20261104-88" if ack else self.declare()
                if ack:
                    self.assertEqual(self.f.append(kind="observe", observed="interview_invited", decision=d)[0], 0)
                self.assertEqual(self.f.append(kind="observe", observed="paused", decision=d)[0], 0)
                args = dict(decision=d, step="ack" if ack else "s1", text="收到，我确认一下时间" if ack else "批准原话")
                rc, allowed = self.cli("check", "--run", "run-prepare", "--json", json.dumps(dict(self.f.identity,
                    kind="reply", reason="合成", **args)))
                self.assertEqual(rc, 0, allowed)
                self.assertEqual(self.f.append(kind="observe", observed="paused", decision=d, recruiter=None)[0], 0)
                self.rejected(self.x.prepare(**args), "thread_paused")

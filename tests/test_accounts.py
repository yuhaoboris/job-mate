"""T4.4 账号命名空间与配置合并；全部为合成工作区。"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import test_derivation

ledger = test_derivation.ledger
workspace = test_derivation.test_tail.workspace


class AccountTest(unittest.TestCase):
    def setUp(self):
        self.x = test_derivation.DerivationTest()
        self.x.setUp()
        self.f = self.x.f

    def tearDown(self):
        self.x.tearDown()

    def current(self, key):
        meta = ledger.read_json(self.f.ws / ledger.MARKER)
        meta["accounts"]["boss"] = {"accountKey": key, "displayName": "合成账号"}
        ledger.write_atomic(self.f.ws / ledger.MARKER, json.dumps(meta))

    def test_limits_four_layers_and_platform_defaults(self):
        profile = {"limits": {"greetsPerRun": 7, "detailsPerRun": 8,
                              "platforms": {"testplat": {"greetsPerRun": 9}}}}
        merged = ledger.effective_limits(profile, "testplat")
        self.assertEqual((merged["greetsPerRun"], merged["detailsPerRun"]), (9, 8))
        self.assertEqual(ledger.effective_limits({}, "testplat")["greetsPerRun"], 2)
        self.assertEqual(ledger.effective_limits({}, "boss")["greetsPerRun"], 10)
        self.assertEqual([merged[k] for k in ("inboundScanDays", "inboundScanPerRun", "inboundRecheckHours")], [14, 30, 24])
        self.assertNotIn("platforms", merged)

    def test_nudge_defaults_overrides_and_invalid_bounds(self):
        defaults = ledger.effective_nudge({}, "boss")
        self.assertEqual(defaults, {"afterReadHours": 24, "afterUnreadHours": 24, "intervalHours": 48,
                                    "maxPerSilence": 3, "maxPerThread": 6, "perDay": 20})
        profile = {"nudge": {"intervalHours": 60, "platforms": {"boss": {"perDay": 2}}}}
        self.assertEqual(ledger.effective_nudge(profile, "boss")["perDay"], 2)
        self.assertEqual(ledger.effective_nudge(profile, "testplat")["perDay"], 20)
        self.assertEqual(ledger.effective_nudge(profile, "boss")["intervalHours"], 60)
        for values in ({"maxPerSilence": 4}, {"maxPerThread": 2}, {"perDay": True}, {"intervalHours": -1}):
            with self.subTest(values=values), self.assertRaises(ledger.LedgerError):
                ledger.effective_nudge({"nudge": values}, "boss")

    def test_explicit_invalid_account_does_not_fall_back(self):
        before = ledger.events_path(self.f.ws).read_bytes()
        for account in (None, "", " ", 1, "unknown"):
            with self.subTest(account=account):
                code, out = self.f.append(kind="reply", status="pending", account=account)
                self.assertEqual(code, 2, out)
                self.assertEqual(ledger.events_path(self.f.ws).read_bytes(), before)
        self.assertEqual(self.f.append(kind="observe", observed="replied")[0], 0)

    def test_missing_configuration_needs_setup_not_a_guessed_default(self):
        meta = ledger.read_json(self.f.ws / ledger.MARKER)
        meta["accounts"].pop("boss")
        ledger.write_atomic(self.f.ws / ledger.MARKER, json.dumps(meta))
        self.assertEqual(self.f.append(kind="reply", status="pending")[0], 2)
        self.assertEqual(self.f.append(kind="reply", status="pending", account="invented")[0], 2)
        self.assertEqual(self.f.run_cli("check", "--platform", "boss", "--company", "合成公司")[0], 2)
        # 历史仍可完整读取。
        self.assertEqual(self.f.run_cli("threads")[0], 0)

    def test_same_attempt_id_is_independent_across_platform_and_account(self):
        aid = "att-same-local-id"
        self.x.event(kind="reply", status="pending", attemptId=aid)
        self.current("second")
        self.x.event(kind="reply", status="pending", attemptId=aid)
        self.x.event(kind="reply", status="sent", attemptId=aid)
        self.x.event(platform="testplat", kind="reply", status="pending", attemptId=aid)
        attempts = ledger.attempts_of(ledger.read_events(self.f.ws)[0])
        self.assertEqual(attempts[("boss", "boss-main", aid)]["status"], "pending")
        self.assertEqual(attempts[("boss", "second", aid)]["status"], "sent")
        self.assertEqual(attempts[("testplat", "test-main", aid)]["status"], "pending")
        # 精确回填历史账号终态，不允许以该历史账号新建投递。
        self.x.event(kind="reply", status="sent", account="boss-main", attemptId=aid, skill="user")
        self.assertEqual(self.f.append(kind="reply", status="pending", account="boss-main")[0], 2)

    def test_dedup_quota_and_company_blocks_do_not_cross_namespace(self):
        self.x.action(kind="greet")
        self.x.event(kind="observe", observed="rejected")
        self.current("second")
        code, out = self.f.run_cli("check", "--platform", "boss", "--company", "合成公司")
        self.assertEqual(code, 0, out)
        code, out = self.f.run_cli("quota", "--platform", "boss", "--run", "run-tail")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["account"], "second")
        self.assertEqual(out["greetsToday"], 0)
        self.assertIsNone(out["secondsSinceLastSend"])
        code, out = self.f.run_cli("check", "--platform", "testplat", "--company", "合成公司")
        self.assertEqual(code, 0, out)

    def test_info_questions_pending_tail_and_threads_do_not_cross_accounts(self):
        old = self.f.baseline()
        self.x.event(kind="observe", observed="info", jobId="same-job", facts={"hours.time": "9到18"})
        self.current("second")
        self.x.event(kind="observe", observed="replied", jobId="same-job")
        out = self.x.threads()
        own = {t["account"]: t for t in out["threads"]}
        self.assertEqual(own["second"]["info"]["known"], {})
        self.assertIsNone(own["second"]["lastHandled"])
        self.assertEqual(own["boss-main"]["lastHandled"]["tailId"], old["tailId"])
        self.assertEqual(self.f.run_cli("threads", "--account", "boss-main")[1]["threads"][0]["account"], "boss-main")
        self.assertEqual(self.f.run_cli("threads", "--account", "not-known")[0], 2)

    def test_guard_requires_unambiguous_namespace_and_current_account(self):
        aid = "att-duplicate"
        self.x.event(kind="reply", status="pending", attemptId=aid)
        self.current("second")
        self.x.event(kind="reply", status="pending", attemptId=aid)
        self.assertEqual(self.f.run_cli("guard", "--run", "run-tail", "--attempt", aid)[0], 2)
        self.assertEqual(self.f.run_cli("guard", "--run", "run-tail", "--attempt", aid,
                                       "--platform", "boss", "--account", "second")[0], 0)
        self.assertEqual(self.f.run_cli("guard", "--run", "run-tail", "--attempt", aid,
                                       "--platform", "boss", "--account", "boss-main")[0], 2)

    def test_release_preserves_both_colliding_local_ids(self):
        for key in ("boss-main", "second"):
            self.current(key)
            self.x.event(kind="reply", status="pending", attemptId="att-duplicate")
        code, out = self.f.run_cli("lock", "release", "--run", "run-tail")
        self.assertEqual(code, 0, out)
        self.assertEqual({a["account"] for a in out["reconciled"]}, {"boss-main", "second"})
        self.assertEqual([a["status"] for a in ledger.attempts_of(ledger.read_events(self.f.ws)[0]).values()], ["uncertain", "uncertain"])

    def test_ack_and_decision_do_not_match_another_account_invitation(self):
        self.x.event(kind="observe", observed="interview_invited", decision="D-20261104-11")
        self.current("second")
        self.x.action(decision="D-20261104-11", step="ack")
        self.assertEqual(self.x.status()["state"], "needs_review")
        self.assertFalse(self.x.status()["systemAttempts"])

    def test_question_rounds_with_same_id_do_not_move_between_accounts(self):
        self.x.action(attemptId="shared-question", asks=["hours.time"])
        self.current("second")
        self.x.action(attemptId="shared-question", asks=["salary.months"])
        self.x.event(kind="observe", observed="replied")
        threads = {t["account"]: t for t in self.x.threads()["threads"]}
        self.assertEqual(threads["boss-main"]["info"]["askedUnanswered"], {})
        self.assertEqual(threads["second"]["info"]["askedUnanswered"], {"salary.months": 1})

    def test_release_retry_preserves_namespaced_candidates(self):
        from unittest.mock import patch
        for account in ("boss-main", "second"):
            self.current(account)
            self.x.event(kind="reply", status="pending", attemptId="duplicate-release")
        original = ledger.append_line
        def fail_second(ws, event):
            if event.get("status") == "uncertain" and event["account"] == "second":
                raise OSError("synthetic failure")
            return original(ws, event)
        with patch.object(ledger, "append_line", side_effect=fail_second):
            self.assertEqual(self.f.run_cli("lock", "release", "--run", "run-tail")[0], 2)
        code, out = self.f.run_cli("lock", "release", "--run", "run-tail")
        self.assertEqual(code, 0, out)
        self.assertEqual({a["account"] for a in out["reconciled"]}, {"boss-main", "second"})
        self.assertEqual(len(ledger.read_events(self.f.ws)[0]), 5)

    def test_invalid_limits_are_not_silently_ignored(self):
        for limits in (None, [], {"greetsPerRun": True}, {"platforms": []}, {"unknown": 5}):
            with self.subTest(limits=limits), self.assertRaises(ledger.LedgerError):
                ledger.effective_limits({"limits": limits}, "boss")

    def test_unknown_platform_filter_and_unknown_account_are_errors(self):
        self.assertEqual(self.f.run_cli("threads", "--platform", "unknown")[0], 2)
        self.assertEqual(self.f.run_cli("quota", "--platform", "boss", "--account", "unknown")[0], 2)
        self.assertEqual(self.f.run_cli("check", "--platform", "boss", "--company", "合成公司", "--account", "unknown")[0], 2)

    def invoke_init(self, target, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = workspace.main(["init", str(target), *args])
        return code, json.loads(out.getvalue())

    def test_generic_init_accounts_and_duplicate_platform_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "workspace"
            code, out = self.invoke_init(target, "--account", "boss:main:合成:显示名", "--account", "testplat:test:另一个")
            self.assertEqual(code, 0, out)
            accounts = ledger.read_json(target / ledger.MARKER)["accounts"]
            self.assertEqual(accounts["boss"]["displayName"], "合成:显示名")
            self.assertEqual(set(accounts), {"boss", "testplat"})
            fresh = Path(directory) / "invalid"
            self.assertEqual(self.invoke_init(fresh, "--account", "boss:a:A", "--account", "boss:b:B")[0], 2)
            self.assertFalse(fresh.exists())

    def test_init_invalid_accounts_do_not_create_workspace(self):
        for value in ("boss", "boss::name", "boss:key:", "unknown:a:B"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "invalid"
                self.assertEqual(self.invoke_init(target, "--account", value)[0], 2)
                self.assertFalse(target.exists())

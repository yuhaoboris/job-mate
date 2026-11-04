"""同形外部会话键不能跨原始平台/账号继承状态；仅合成 CLI 工作区。"""
import json
import unittest

import test_derivation

ledger = test_derivation.ledger


class AccountCollisionTest(unittest.TestCase):
    def setUp(self):
        self.x = test_derivation.DerivationTest()
        self.x.setUp()
        self.f = self.x.f

    def tearDown(self):
        self.x.tearDown()

    def current(self, account):
        meta = ledger.read_json(self.f.ws / ledger.MARKER)
        meta["accounts"]["boss"]["accountKey"] = account
        ledger.write_atomic(self.f.ws / ledger.MARKER, json.dumps(meta))

    def old_tail(self):
        self.current("a|b")
        identity = self.x.event(kind="observe", observed="replied")
        self.f.thread = identity["threadKey"]
        return self.f.baseline()

    def collide(self):
        self.current("a｜b")
        identity = self.x.event(kind="observe", observed="replied")
        self.assertEqual(identity["threadKey"], self.f.thread)

    def tail_record(self, tail):
        return next(e for e in ledger.read_events(self.f.ws)[0]
                    if e.get("observed") == "tail" and e["tailId"] == tail["tailId"])

    def reaccount(self, event, account, **changes):
        event = ledger.public_entry(event)
        event.pop("threadKey")
        event["account"] = account
        event.update(changes)
        return ledger.prepare_event(event)

    def assert_invalid_history(self, event, fragment):
        ledger.append_line(self.f.ws, event)  # 合成污染：不经公开追加入口。
        before = ledger.events_path(self.f.ws).read_bytes()
        code, out = self.f.run_cli("validate")
        self.assertEqual(code, 2, out)
        self.assertIn(fragment, json.dumps(out, ensure_ascii=False))
        self.assertEqual(self.f.run_cli("threads")[0], 2)
        self.assertEqual(ledger.events_path(self.f.ws).read_bytes(), before)

    def test_new_account_does_not_inherit_colliding_last_handled(self):
        old = self.old_tail()
        self.collide()
        threads = {t["account"]: t for t in self.x.threads()["threads"]}
        self.assertEqual(threads["a|b"]["lastHandled"]["tailId"], old["tailId"])
        self.assertIsNone(threads["a｜b"]["lastHandled"])
        self.assertIsNone(threads["a｜b"]["lastOpenedAt"])

    def test_ambiguous_cli_tail_rejects_without_writing_even_with_explicit_base(self):
        old = self.old_tail()
        self.collide()
        before = ledger.events_path(self.f.ws).read_bytes()
        for base in (None, old["tailId"]):
            code, out = self.f.tail(base=base)
            self.assertEqual(code, 2, out)
            self.assertEqual(ledger.events_path(self.f.ws).read_bytes(), before)

    def test_cross_account_explicit_base_is_rejected_by_historical_validation(self):
        old = self.old_tail()
        template = self.tail_record(old)
        self.collide()
        event = self.reaccount(template, "a｜b", tailId="tail-bbbbbbbbbbbb", baseTailId=old["tailId"],
                               baseExplicit=True, result="same", newCount=0, reason="same", settled=True)
        self.assert_invalid_history(event, "baseTailId")

    def test_cross_account_handled_is_rejected_by_historical_validation(self):
        self.old_tail()
        handled = ledger.read_events(self.f.ws)[0][-1]
        self.collide()
        self.assert_invalid_history(self.reaccount(handled, "a｜b"), "handled")

    def test_valid_independent_tails_and_owner_closure_remain_separate(self):
        old = self.old_tail()
        template = self.tail_record(old)
        self.collide()
        fresh = self.reaccount(template, "a｜b", tailId="tail-bbbbbbbbbbbb")
        ledger.append_line(self.f.ws, fresh)
        threads = {t["account"]: t for t in self.x.threads()["threads"]}
        self.assertEqual(threads["a|b"]["lastHandled"]["tailId"], old["tailId"])
        self.assertIsNone(threads["a|b"]["pendingTail"])
        self.assertEqual(threads["a｜b"]["pendingTail"]["tailId"], fresh["tailId"])
        self.assertIsNone(threads["a｜b"]["lastHandled"])
        self.assertEqual(self.f.handled(fresh, user=True)[0], 0)
        threads = {t["account"]: t for t in self.x.threads()["threads"]}
        self.assertEqual(threads["a|b"]["lastHandled"]["tailId"], old["tailId"])
        self.assertEqual(threads["a｜b"]["lastHandled"]["tailId"], fresh["tailId"])
        code, out = self.f.run_cli("threads", "--account", "a|b")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["threads"][0]["lastHandled"]["tailId"], old["tailId"])

    def assert_other_attempt_does_not_affect_tail(self, final):
        template = self.tail_record(self.f.ok_tail())
        self.current("a|b")
        identity = self.x.event(kind="observe", observed="replied")
        self.f.thread = identity["threadKey"]
        self.x.action(final=final)
        self.collide()
        fresh = self.reaccount(template, "a｜b", tailId="tail-bbbbbbbbbbbb", anchor="start")
        ledger.append_line(self.f.ws, fresh)
        code, out = self.f.run_cli("validate")
        self.assertEqual(code, 0, out)
        own = next(t for t in self.x.threads()["threads"] if t["account"] == "a｜b")
        self.assertFalse(own["pendingTail"]["reviewRequired"])
        self.assertEqual(own["unresolved"], [])

    def test_other_account_sent_does_not_conflict_with_start_anchor(self):
        self.assert_other_attempt_does_not_affect_tail("sent")

    def test_other_account_pending_does_not_mark_own_tail_for_review(self):
        self.assert_other_attempt_does_not_affect_tail(None)

    def test_ack_does_not_claim_colliding_other_account_invitation(self):
        self.old_tail()
        self.x.event(kind="observe", observed="interview_invited", decision="D-20261104-11")
        self.collide()
        self.x.action(decision="D-20261104-11", step="ack")
        summary = self.x.status()
        self.assertEqual(summary["state"], "needs_review")
        self.assertEqual(summary["systemAttempts"], [])

    def test_own_invitation_not_shadowed_by_colliding_other_account(self):
        self.old_tail()
        self.x.event(kind="observe", observed="interview_invited", decision="D-20261104-11")
        self.collide()
        self.x.event(kind="observe", observed="interview_invited", decision="D-20261104-11")
        self.x.action(decision="D-20261104-11", step="ack")
        self.assertIsNone(self.x.status()["systemAttempts"][0]["mappingIssue"])

    def test_old_account_failed_no_send_can_be_closed_only_by_its_owner_record(self):
        self.old_tail()
        pending = self.x.event(kind="reply", status="pending", decision="D-20261104-11", step="unregistered")
        self.collide()
        self.x.event(kind="reply", status="failed_no_send", account="a|b", attemptId=pending["attemptId"],
                     decision="D-20261104-11", step="unregistered", skill="user")
        self.x.event(kind="observe", observed="unpaused", skill="user", decision="D-20261104-11")
        summary = self.x.status()
        self.assertEqual(summary["state"], "needs_review")
        self.assertEqual(summary["unmappedAttempts"][0]["status"], "failed_no_send")
        self.assertIsNone(summary["unmappedAttempts"][0]["reviewResolvedBy"])
        own = self.x.event(kind="observe", observed="unpaused", skill="user", decision="D-20261104-11", account="a|b")
        summary = self.x.status()
        self.assertEqual(summary["state"], "done")
        self.assertEqual(summary["unmappedAttempts"][0]["reviewResolvedBy"], own["eventId"])
        self.assertEqual(self.f.run_cli("threads", "--account", "a|b")[0], 0)

    def test_foreign_bridge_base_is_rejected_by_historical_validation(self):
        old = self.old_tail()
        aid = self.f.send()
        prefix = ledger.events_path(self.f.ws).read_text(encoding="utf-8")
        verified = self.f.ok_tail(self.f.bridge_body(aid), base=old["tailId"])
        self.assertTrue(verified["settled"])
        bridge = self.tail_record(verified)
        # 将有效的旧账号 bridge 放入另一个账号的历史；保留原基准与发送证明。
        ledger.write_atomic(ledger.events_path(self.f.ws), prefix)
        self.collide()
        self.assert_invalid_history(self.reaccount(bridge, "a｜b", tailId="tail-bbbbbbbbbbbb"), "baseTailId")

    def test_cross_account_handled_is_rejected_without_writing(self):
        old = self.old_tail()
        self.collide()
        before = ledger.events_path(self.f.ws).read_bytes()
        code, out = self.f.handled(old, user=True)
        self.assertEqual(code, 2, out)
        self.assertEqual(ledger.events_path(self.f.ws).read_bytes(), before)

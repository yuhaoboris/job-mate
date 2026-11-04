"""本地协议回归；runtime 替身只驱动 outbox/receipt/incoming，不注入执行器。"""
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import ledger
import notify

USER = "ou_synthetic"
D1 = "D-20261030-01"
D2 = "D-20261030-02"


class LocalRuntime:
    """仅合成元数据；每次交付都来自显式 claim，并落 receipt。"""
    def __init__(self, test):
        self.test = test
        self.clock = int(time.time() * 1000) - 60000
        self.messages = []
        self.sent = []
        self.n = 0

    def tick(self):
        self.clock += 10
        return self.clock

    def drain(self):
        code, out = self.test.run_cmd("outbox", "--claim")
        assert code == 0, out
        for entry in out["entries"]:
            self.n += 1
            mid = f"bot-{self.n}"
            code, result = self.test.receipt(entry, message=mid, created=self.tick())
            assert code == 0, result
            self.sent.append({"text": entry["text"], "replyTo": entry["replyTo"], "idem": entry["idempotencyKey"], "messageId": mid})

    def user_says(self, text, parent=None, sender=USER):
        self.n += 1
        mid = f"user-{self.n}"
        self.messages.append(self.test.message(text, mid=mid, at=self.tick(), parent=parent, sender=sender))
        return mid


class NotifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        (self.ws / "job-mate.json").write_text(json.dumps({"schemaVersion": 1, "accounts": {}}))
        (self.ws / "ledger").mkdir()
        (self.ws / "inbox").mkdir()
        self.set_im()
        self.inbox()
        self.im = LocalRuntime(self)

    def tearDown(self):
        self.tmp.cleanup()

    def set_im(self, **changes):
        im = {"channel": "runtime", "ownerId": USER, "transport": {"kind": "synthetic"}}
        im.update(changes)
        (self.ws / "profile.json").write_text(json.dumps({"schemaVersion": 2, "im": im}))

    def inbox(self, *items):
        (self.ws / "inbox" / "decisions.md").write_text("# 待本人决定\n\n" + "\n".join(f"## {i} {s}\n\n- 问题：示例\n" for i, s in items))

    def run_cmd(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = notify.main(["--workspace", str(self.ws), *argv])
        return code, json.loads(out.getvalue())

    def ctx(self):
        return notify.context(self.ws)

    def append_im(self, **rec):
        with notify.im_mutex(self.ws):
            return notify.append_record(self.ws, rec, self.ctx()["tz"])

    def ledger_run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ledger.main(["--workspace", str(self.ws), *argv])
        return code, json.loads(out.getvalue())

    def message(self, text="接受", mid="incoming", at=None, parent=None, sender=USER, **extra):
        msg = {"messageId": mid, "chatId": "synthetic-chat", "createTime": self.im.tick() if at is None else at,
               "senderId": sender, "senderType": "user", "text": text, "parentId": parent, "deleted": False}
        msg.update(extra)
        return msg

    def incoming(self, messages=(), since=None, complete=True):
        if since is None:
            since = self.run_cmd("poll")[1]["sinceMs"]
        return self.run_cmd("incoming", "--json", json.dumps({"since": since, "complete": complete, "messages": list(messages)}))

    def sync(self):
        result = self.incoming(self.im.messages, since=self.run_cmd("poll")[1]["sinceMs"])
        self.im.drain()
        return result

    def queue(self, key="alert:x", text="正文", kind="alert", *extra):
        return self.run_cmd("send", "--kind", kind, "--key", key, "--text", text, *extra)

    def claim(self):
        code, out = self.run_cmd("outbox", "--claim")
        self.assertEqual(code, 0, out)
        return out["entries"]

    def receipt(self, entry, result="sent", message="outgoing", chat="synthetic-chat", created=None, error=None):
        argv = ["receipt", "--key", entry["key"], "--token", entry["token"], "--result", result]
        if result == "sent":
            argv += ["--message-id", message, "--chat-id", chat]
            if created is not None:
                argv += ["--create-time", str(created)]
        if error is not None:
            argv += ["--error", error]
        return self.run_cmd(*argv)

    def establish(self):
        self.queue(key="seed")
        self.im.drain()
        self.assertTrue(self.run_cmd("poll")[1]["readable"])

    def decide(self, decision=D1):
        code, out = self.run_cmd("send", "--kind", "decision", "--decision", decision, "--text", "示例科技 / 前端\nHR 邀约周四 14:00 面试，是否接受？")
        self.assertEqual(code, 0, out)
        self.im.drain()
        return notify.load_state(self.ws)["out"][out["key"]]["messageId"]

    def queue_test(self):
        code, out = self.run_cmd("test")
        self.assertEqual(code, 0, out)
        self.im.drain()
        out["messageId"] = notify.load_state(self.ws)["out"][out["key"]]["messageId"]
        return code, out

    def pending_command(self, mid, action, **extra):
        command = {v: k for k, v in notify.COMMANDS.items()}[action]
        rec = {"type": "in", "route": self.ctx()["cfg"]["route"], "messageId": mid, "createTime": self.im.tick(),
               "text": command, "state": "command", "command": command, "commandVersion": notify.COMMAND_VERSION,
               "chatId": "synthetic-chat", "senderId": USER, "senderType": "user", "deleted": False, "parentId": None}
        rec.update(extra)
        self.append_im(**rec)

    def replies_after(self, *texts):
        for text in texts:
            self.im.user_says(text)
        _, out = self.sync()
        return {r["text"]: r for r in out["replies"]}

    def assert_no_legacy_view(self, *, readable, states=None):
        empty = {"replies": {"received": 0, "ready": 0, "clarifying": 0},
                 "commands": {"pending": 0, "applied": 0}, "unknownOutbox": 0, "bindable": False}
        for command in ("poll", "status", "replies"):
            code, view = self.run_cmd(command)
            self.assertEqual(code, 0, view)
            self.assertEqual(view["legacyUnbound"], empty, (command, view))
            self.assertEqual(view["otherRoutes"], {"received": 0, "ready": 0, "clarifying": 0})
            if command != "replies":
                self.assertFalse(view["legacyRouteUnbound"], (command, view))
                self.assertIsNone(view["warning"])
                self.assertEqual(view["readable"], readable)
            if command == "status":
                self.assertEqual(view["warnings"], [])
                if states is not None:
                    self.assertEqual(view["outbox"], states)

    def test_new_protocol_full_cycle_has_consistent_poll_status_and_no_legacy_warning(self):
        self.inbox((D1, "待决定"))
        self.assert_no_legacy_view(readable=False, states={})
        code, queued = self.run_cmd("test")
        self.assertEqual((code, queued["status"]), (0, "queued"))
        self.assert_no_legacy_view(readable=False, states={"queued": 1})
        handoff = self.claim()[0]
        self.assertEqual(handoff["key"], queued["key"])
        self.assert_no_legacy_view(readable=False, states={"handoff": 1})
        self.assertEqual(self.receipt(handoff, message="full-cycle-test", created=self.im.tick())[0], 0)
        self.assert_no_legacy_view(readable=True, states={"sent": 1})
        answer = self.message("01 接受", mid="full-cycle-answer")
        code, incoming = self.incoming([answer])
        self.assertEqual(code, 0, incoming)
        self.assertEqual(incoming["new"][0]["state"], "received")
        self.assert_no_legacy_view(readable=True, states={"sent": 1, "queued": 1})
        ack = self.claim()[0]
        self.assertEqual(ack["replyTo"], answer["messageId"])
        self.assert_no_legacy_view(readable=True, states={"sent": 1, "handoff": 1})
        self.assertEqual(self.receipt(ack, message="full-cycle-ack", created=self.im.tick())[0], 0)
        self.assert_no_legacy_view(readable=True, states={"sent": 2})
        self.inbox((D1, "已决定"))
        self.assertEqual(self.run_cmd("reply-state", "--message", answer["messageId"], "--state", "ready", "--decision", D1)[0], 0)
        self.assertEqual(self.run_cmd("status")[1]["readyReplies"][0]["messageId"], answer["messageId"])
        self.assert_no_legacy_view(readable=True, states={"sent": 2})
        code, launch = self.run_cmd("trigger", "claim")
        self.assertEqual(code, 0, launch)
        self.assertEqual(launch["messageIds"], [answer["messageId"]])
        self.assert_no_legacy_view(readable=True, states={"sent": 2})
        self.assertEqual(len(self.run_cmd("status")[1]["unfinishedLaunches"]), 1)
        self.assertEqual(self.run_cmd("trigger", "finish", "--launch-id", launch["launchId"], "--status", "ok", "--confirmed-stopped")[0], 0)
        self.assert_no_legacy_view(readable=True, states={"sent": 2})
        self.run_cmd("reply-state", "--message", answer["messageId"], "--state", "consumed", "--decision", D1)
        self.assert_no_legacy_view(readable=True, states={"sent": 2})
        self.assertEqual(self.run_cmd("poll")[1]["replies"], [])
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "no_replies")
        self.set_im(channel="none")
        self.assertEqual(self.run_cmd("test")[1]["status"], "skipped")
        self.assert_no_legacy_view(readable=False, states={})
        self.assertEqual(self.claim(), [])

    def test_new_lark_receipts_are_not_legacy_binding_evidence(self):
        self.set_im(channel="lark", userId=USER)
        self.run_cmd("test")
        self.assert_no_legacy_view(readable=False, states={"queued": 1})
        entry = self.claim()[0]
        self.assert_no_legacy_view(readable=False, states={"handoff": 1})
        self.receipt(entry, message="modern-lark-test", created=self.im.tick())
        self.assert_no_legacy_view(readable=True, states={"sent": 1})
        before = notify.im_path(self.ws).read_bytes()
        self.assertEqual(self.migrate()[0], 2)
        self.assertEqual(notify.im_path(self.ws).read_bytes(), before)

    def test_mixed_old_new_history_counts_only_true_legacy_and_binds_unique_old_chat(self):
        self.seed_legacy()
        self.set_im(ownerId="new-runtime-owner")
        self.run_cmd("test")
        modern = next(e for e in self.claim() if e["key"].startswith("test:"))
        self.receipt(modern, message="modern-new-chat", chat="new-private-chat", created=self.im.tick())
        self.set_im(channel="lark", userId=USER)
        expected = {"replies": {"received": 0, "ready": 1, "clarifying": 0},
                    "commands": {"pending": 1, "applied": 0}, "unknownOutbox": 2, "bindable": True}
        for command in ("poll", "status", "replies"):
            self.assertEqual(self.run_cmd(command)[1]["legacyUnbound"], expected)
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "legacy_route_unbound")
        self.assertEqual(self.migrate()[0], 0)
        self.assertFalse(self.run_cmd("poll")[1]["legacyRouteUnbound"])
        self.assertTrue(self.run_cmd("poll")[1]["readable"])
        self.assertTrue(self.run_cmd("status")[1]["needsReconcile"])
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "needs_reconcile")
        self.assertEqual(self.run_cmd("poll")[1]["legacyUnbound"]["unknownOutbox"], 0)

    def test_send_test_only_queue_and_freeze(self):
        self.assertEqual(self.run_cmd("send", "--kind", "decision", "--decision", D1, "--text", "x")[0], 2)
        self.inbox((D1, "待决定"))
        code, out = self.run_cmd("send", "--kind", "decision", "--decision", D1, "--text", "first")
        self.assertEqual((code, out["status"], out["sent"]), (0, "queued", False))
        self.assertFalse(self.run_cmd("poll")[1]["readable"])
        self.assertEqual(self.run_cmd("send", "--kind", "decision", "--decision", D1, "--text", "changed")[1]["status"], "already_queued")
        entry = self.claim()[0]
        self.assertIn("first", entry["text"])
        self.assertNotIn("changed", entry["text"])
        self.assertIn("回复「01 你的决定」", entry["text"])
        self.assertEqual(self.receipt(entry)[0], 0)
        self.assertEqual(self.run_cmd("send", "--kind", "decision", "--decision", D1, "--text", "first")[1]["status"], "already_sent")
        self.assertEqual(self.run_cmd("test")[1]["status"], "queued")
        self.assertEqual(len([o for o in notify.load_state(self.ws)["out"].values() if o["status"] == "queued"]), 1)

    def test_none_empty_outbox_and_no_delivery(self):
        self.queue()
        self.set_im(channel="none")
        self.assertEqual(self.queue()[1]["status"], "skipped")
        self.assertEqual(self.run_cmd("test")[1]["status"], "skipped")
        self.assertEqual(self.claim(), [])
        self.assertEqual(self.run_cmd("poll")[1]["outboxPending"], 0)

    def test_claim_once_and_stale_never_redelivered(self):
        self.queue()
        entry = self.claim()[0]
        self.assertEqual(self.claim(), [])
        with patch.object(notify, "now_ms", return_value=notify.now_ms() + 700000):
            self.assertEqual(self.run_cmd("outbox", "--stale")[1]["entries"][0]["token"], entry["token"])
            self.assertEqual(self.claim(), [])
            self.assertEqual(self.run_cmd("status")[1]["stale"][0]["token"], entry["token"])
        self.assertEqual(notify.load_state(self.ws)["out"][entry["key"]]["status"], "handoff")

    def test_reply_to_rejects_unknown_foreign_and_conflicting_chat(self):
        self.establish()
        incoming = self.message(mid="owner-a-message")
        self.incoming([incoming])
        self.im.drain()
        self.assertEqual(self.queue("unknown-reply", "x", "clarify", "--reply-to", "unknown")[0], 1)
        self.assertEqual(self.queue("own-reply", "x", "clarify", "--reply-to", incoming["messageId"])[0], 0)
        self.im.drain()
        self.queue("conflicting-chat")
        conflict = self.claim()[0]
        self.receipt(conflict, message="foreign-chat-sent", chat="not-established-chat", created=self.im.tick())
        self.assertEqual(self.queue("conflicting-reply", "x", "clarify", "--reply-to", "foreign-chat-sent")[0], 1)
        self.set_im(ownerId="owner-b")
        self.assertEqual(self.queue("foreign-reply", "x", "clarify", "--reply-to", incoming["messageId"])[0], 1)
        self.assertEqual(self.queue("foreign-sent-reply", "x", "clarify", "--reply-to", "foreign-chat-sent")[0], 1)
        self.assertFalse(any(o["key"] in ("unknown-reply", "conflicting-reply", "foreign-reply", "foreign-sent-reply")
                             for o in self.run_cmd("outbox")[1]["entries"]))

    def test_claim_revalidates_preexisting_unsafe_reply_to_and_allows_current_sent(self):
        self.establish()
        self.assertEqual(self.queue("reply-to-current-sent", "x", "alert", "--reply-to", self.im.sent[0]["messageId"])[0], 0)
        self.im.drain()
        cfg = self.ctx()["cfg"]
        self.append_im(type="out", protocolVersion=3, key="unsafe-old-queue", kind="clarify", status="queued", text="unsafe",
                       replyTo="unknown-or-unbound", route=cfg["route"], channel=cfg["channel"], ownerId=cfg["ownerId"],
                       transport=cfg["transport"], window=0)
        self.assertEqual(self.claim(), [])
        row = next(o for o in self.run_cmd("outbox")[1]["entries"] if o["key"] == "unsafe-old-queue")
        self.assertEqual(row["reason"], "reply_to_untrusted")

    def test_receipt_validation_duplicate_conflict_and_attempt_binding(self):
        self.queue()
        entry = self.claim()[0]
        before = notify.im_path(self.ws).read_bytes()
        for args in (("--result", "sent"), ("--result", "failed"), ("--result", "unknown", "--message-id", "not-sent"),
                     ("--result", "sent", "--message-id", "x", "--chat-id", "c", "--create-time", "-1"),
                     ("--result", "sent", "--message-id", "x", "--chat-id", "c", "--create-time", str(10**100))):
            self.assertEqual(self.run_cmd("receipt", "--key", entry["key"], "--token", entry["token"], *args)[0], 2)
        self.assertEqual(self.run_cmd("receipt", "--key", "unknown", "--token", entry["token"], "--result", "unknown")[0], 2)
        self.assertEqual(notify.im_path(self.ws).read_bytes(), before)
        self.assertEqual(self.receipt(entry, "failed", error="明确接收前拒绝")[0], 0)
        self.assertEqual(self.receipt(entry, "failed", error="明确接收前拒绝")[1]["status"], "duplicate")
        self.assertEqual(self.receipt(entry, "failed", error="不同证据")[0], 2)
        second = self.claim()[0]
        self.assertNotEqual(entry["token"], second["token"])
        self.assertEqual(second["attempt"], 2)
        self.assertEqual(self.receipt(entry)[0], 2)
        self.assertEqual(self.receipt(second)[0], 0)
        self.assertEqual(self.receipt(second)[1]["status"], "duplicate")
        self.assertEqual(self.receipt(second, "unknown")[0], 2)

    def test_chat_time_boundary_and_conflict(self):
        self.queue()
        one = self.claim()[0]
        self.receipt(one)
        self.assertFalse(self.run_cmd("poll")[1]["readable"])
        self.queue(key="two")
        two = self.claim()[0]
        self.receipt(two, message="two", created=self.im.tick())
        self.assertTrue(self.run_cmd("poll")[1]["readable"])
        self.queue(key="three")
        three = self.claim()[0]
        self.receipt(three, message="three", chat="wrong-chat", created=1)
        status = self.run_cmd("status")[1]
        self.assertEqual(status["chatId"], "synthetic-chat")
        self.assertEqual(status["chatConflicts"], ["wrong-chat"])
        self.assertGreater(status["sinceMs"], 1)

    def test_failed_max_three_frozen_body_reply_and_idempotency(self):
        # replyTo 现在必须有真实已入账的身份/私聊元数据，不能使用虚构 parent。
        self.establish()
        self.incoming([self.message(mid="parent")])
        self.im.drain()
        self.queue("route", "old", "alert", "--reply-to", "parent")
        entries = []
        for attempt in range(1, 4):
            entry = self.claim()[0]
            entries.append(entry)
            self.assertEqual(entry["attempt"], attempt)
            self.assertEqual((entry["text"], entry["replyTo"], entry["ownerId"]), (notify.compose("alert", "old"), "parent", USER))
            self.assertEqual(self.receipt(entry, "failed", error="未投递：通道明确拒绝")[0], 0)
            if attempt < 3:
                self.assertEqual(self.queue("route", "new")[1]["status"], "already_queued")
        self.assertEqual(len({e["idempotencyKey"] for e in entries}), 1)
        self.assertEqual(self.claim(), [])
        self.assertEqual(self.queue("route")[0], 1)

    def test_unknown_retry_freezes_idempotency_key_across_workspace_move(self):
        self.set_im(transport={"kind": "synthetic", "idempotencyWindowSeconds": 3600})
        self.queue("moved")
        original = self.claim()[0]
        self.receipt(original, "unknown")
        with tempfile.TemporaryDirectory() as tmp:
            moved = Path(tmp) / "renamed-workspace"
            shutil.copytree(self.ws, moved)
            before = self.ws
            try:
                self.ws = moved
                retry = self.claim()[0]
                self.assertEqual(retry["idempotencyKey"], original["idempotencyKey"])
                self.assertNotEqual(retry["idempotencyKey"], notify.idem_key(moved, "moved"))
            finally:
                self.ws = before
        handoff = next(r for r in notify.read_records(self.ws) if r.get("status") == "handoff")
        self.assertEqual(handoff["idempotencyKey"], original["idempotencyKey"])

    def test_preexisting_handoff_without_frozen_key_cannot_retry_unknown(self):
        self.set_im(transport={"kind": "synthetic", "idempotencyWindowSeconds": 3600})
        self.queue("old-handoff")
        self.append_im(type="out", key="old-handoff", status="handoff", token="old-token", attempt=1,
                       handoffMs=notify.now_ms(), firstHandoffMs=notify.now_ms())
        self.assertEqual(self.receipt({"key": "old-handoff", "token": "old-token"}, "unknown")[0], 0)
        self.assertEqual(self.claim(), [])
        row = next(r for r in self.run_cmd("outbox")[1]["entries"] if r["key"] == "old-handoff")
        self.assertEqual(row["reason"], "idempotency_key_unfrozen")

    def test_unknown_retry_fixed_deadline_and_late_sent_fact(self):
        self.set_im(transport={"kind": "synthetic", "idempotencyWindowSeconds": 3600})
        self.queue("deadline")
        start = 1800000000000
        with patch.object(notify, "now_ms", return_value=start):
            first = self.claim()[0]
            self.receipt(first, "unknown")
        with patch.object(notify, "now_ms", return_value=start + 1000):
            retry = self.claim()[0]
        self.assertEqual(retry["deliveryDeadlineMs"], start + 3300000)
        self.assertEqual(retry["firstHandoffMs"], start)
        self.assertEqual(retry["idempotencyWindowSeconds"], 3600)
        self.assertTrue(retry["requiresIdempotencyGuarantee"])
        self.assertEqual(retry["route"], self.ctx()["cfg"]["route"])
        with patch.object(notify, "now_ms", return_value=start + 3300001):
            self.assertEqual(self.receipt(retry)[0], 0)
            status = self.run_cmd("status")[1]
        self.assertEqual(status["lateSends"][0]["key"], "deadline")
        self.assertEqual(self.queue("deadline")[1]["status"], "already_sent")

    def test_short_window_blocks_unknown_not_initial_or_confirmed_failed(self):
        self.set_im(transport={"kind": "synthetic", "idempotencyWindowSeconds": 300})
        self.queue("short-window")
        start = notify.now_ms()
        with patch.object(notify, "now_ms", return_value=start):
            first = self.claim()[0]
            self.receipt(first, "failed", error="未接收")
        with patch.object(notify, "now_ms", return_value=start + 600000):
            second = self.claim()[0]
            self.assertIsNone(second["deliveryDeadlineMs"])
            self.assertFalse(second["requiresIdempotencyGuarantee"])
            self.receipt(second, "unknown")
            self.assertEqual(self.claim(), [])

    def test_claim_crossing_retry_deadline_does_not_issue_expired_handoff(self):
        self.set_im(transport={"kind": "synthetic", "idempotencyWindowSeconds": 3600})
        self.queue("deadline-race")
        start = notify.now_ms()
        with patch.object(notify, "now_ms", return_value=start):
            first = self.claim()[0]
            self.receipt(first, "unknown")
        with patch.object(notify, "now_ms", side_effect=[start + 3299999, start + 3300000]):
            self.assertEqual(self.claim(), [])
        self.assertEqual(len([r for r in notify.read_records(self.ws) if r.get("status") == "handoff"]), 1)

    def test_failed_after_unknown_does_not_extend_or_remove_retry_deadline(self):
        self.set_im(transport={"kind": "synthetic", "idempotencyWindowSeconds": 3600})
        self.queue("sticky-unknown")
        start = notify.now_ms()
        with patch.object(notify, "now_ms", return_value=start):
            first = self.claim()[0]
            self.receipt(first, "unknown")
        with patch.object(notify, "now_ms", return_value=start + 1000):
            retry = self.claim()[0]
            self.receipt(retry, "failed", error="本次未接收")
        with patch.object(notify, "now_ms", return_value=start + 3300000):
            self.assertEqual(self.claim(), [])
            row = next(o for o in self.run_cmd("outbox")[1]["entries"] if o["key"] == "sticky-unknown")
            self.assertEqual(row["reason"], "unknown_delivery")

    def test_unknown_requires_frozen_window_and_same_route(self):
        self.queue("zero")
        zero = self.claim()[0]
        self.receipt(zero, "unknown", error="timeout")
        self.assertEqual(self.claim(), [])
        self.set_im(transport={"kind": "synthetic", "idempotencyWindowSeconds": 3600})
        self.assertEqual(self.claim(), [])
        self.assertEqual(self.queue("zero")[1]["reason"], "route_changed")
        self.queue("window")
        first = self.claim()[0]
        self.receipt(first, "unknown")
        second = self.claim()[0]
        self.assertEqual(first["idempotencyKey"], second["idempotencyKey"])
        self.receipt(second, "unknown")
        with patch.object(notify, "now_ms", return_value=notify.now_ms() + 3600000):
            self.assertEqual(self.claim(), [])

    def test_legacy_pending_failed_and_config_compatibility(self):
        self.set_im(channel="lark", userId=USER, larkProfile="test", transport={"idempotencyWindowSeconds": 3600})
        for key, status in (("old-pending", "pending"), ("old-failed", "failed")):
            self.append_im(type="out", key=key, kind="alert", status=status, text="legacy")
        self.append_im(type="out", key="old-sent", kind="alert", status="sent", text="legacy", messageId="old-message", createTime=1000)
        self.append_im(type="chat", chatId="synthetic-chat")
        self.append_im(type="cursor", createTime=2000)
        cfg = self.ctx()["cfg"]
        self.assertEqual(cfg["window"], 0)
        self.assertEqual(cfg["transport"], {"kind": "lark", "profile": "test"})
        self.assertEqual(self.claim(), [])
        self.assertTrue(all(o["status"] == "unknown" and o["legacy"] for k, o in notify.load_state(self.ws)["out"].items() if k != "old-sent"))
        self.assertFalse(self.run_cmd("poll")[1]["readable"])
        self.assertTrue(self.run_cmd("poll")[1]["legacyRouteUnbound"])
        self.assertEqual(self.migrate()[0], 0)
        plan = self.run_cmd("poll")[1]
        self.assertEqual((plan["chatId"], plan["sinceMs"], plan["readable"]), ("synthetic-chat", 2000, True))
        self.assertEqual(self.incoming([self.message(at=2000)])[0], 0)
        self.set_im(channel="lark", userId="ou_new_owner")
        self.assertFalse(self.run_cmd("poll")[1]["readable"])
        self.assertEqual(self.claim(), [])

    def assert_other_routes(self, expected):
        for command in ("poll", "status", "replies"):
            code, view = self.run_cmd(command)
            self.assertEqual(code, 0, view)
            self.assertEqual(view["otherRoutes"], expected, (command, view))
            self.assertEqual(view["legacyUnbound"]["replies"], {"received": 0, "ready": 0, "clarifying": 0})
            self.assertNotIn("PRIVATE_OWNER_", json.dumps(view))
            self.assertNotIn("owner-", json.dumps(view["otherRoutes"]))

    def test_route_changed_pending_counts_survive_owner_transport_none_and_restore(self):
        self.inbox((D1, "待决定"))
        self.establish()
        messages = [self.message(f"PRIVATE_OWNER_{state}", mid=f"owner-{state}-id") for state in ("ready", "received", "clarifying")]
        self.incoming(messages)
        self.im.drain()
        self.run_cmd("reply-state", "--message", "owner-ready-id", "--state", "ready", "--decision", D1)
        code, question = self.run_cmd("send", "--kind", "clarify", "--reply-to", "owner-clarifying-id", "--text", "请明确事项")
        self.assertEqual(code, 0, question)
        self.im.drain()
        self.run_cmd("reply-state", "--message", "owner-clarifying-id", "--state", "clarifying")
        original = self.run_cmd("replies")[1]["replies"]
        for changed in ({"ownerId": "owner-u2"}, {"transport": {"kind": "different-transport"}}, {"channel": "none"}):
            with self.subTest(changed=changed):
                self.set_im(**changed)
                self.assert_other_routes({"received": 1, "ready": 1, "clarifying": 1})
                self.assertEqual(self.run_cmd("poll")[1]["replies"], [])
                self.assertEqual(self.run_cmd("status")[1]["readyReplies"], [])
                self.assertEqual(self.run_cmd("replies")[1]["replies"], [])
                self.assertIn("route_changed_pending", self.run_cmd("poll")[1]["warning"])
                self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "route_changed_pending")
                before = notify.im_path(self.ws).read_bytes()
                self.assertEqual(self.run_cmd("reply-state", "--message", "owner-ready-id", "--state", "consumed", "--decision", D1,
                                              "--confirmed-reconciled", "--note", "不可在新路由放弃旧待办")[0], 2)
                self.assertEqual(notify.im_path(self.ws).read_bytes(), before)
                self.set_im()
                self.assertEqual(self.run_cmd("replies")[1]["replies"], original)
                for command in ("poll", "status", "replies"):
                    self.assertEqual(self.run_cmd(command)[1]["otherRoutes"], {"received": 0, "ready": 0, "clarifying": 0})
        # 本人决定放弃也只能回到原路由，逐条明确消费，不能从新路由绕过门。
        for message in messages:
            if message["messageId"] == "owner-clarifying-id":
                self.set_im(ownerId="owner-u2")
                self.assert_other_routes({"received": 0, "ready": 0, "clarifying": 1})
                self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "route_changed_pending")
                self.set_im()
            self.assertEqual(self.run_cmd("reply-state", "--message", message["messageId"], "--state", "consumed", "--decision", D1,
                                          "--note", "本人明确放弃，已核对 inbox/ledger")[0], 0)
        self.set_im(ownerId="owner-u2")
        self.assert_other_routes({"received": 0, "ready": 0, "clarifying": 0})
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "no_replies")

    def test_bound_legacy_other_route_pending_is_visible_and_global_blocks_win(self):
        self.seed_legacy()
        self.migrate()
        self.set_im(channel="lark", userId=USER, larkProfile="other-app")
        self.assert_other_routes({"received": 0, "ready": 1, "clarifying": 0})
        self.assertFalse(self.run_cmd("poll")[1]["legacyRouteUnbound"])
        self.assertTrue(any("route_changed_pending" in warning for warning in self.run_cmd("status")[1]["warnings"]))
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "route_changed_pending")
        ledger.set_pause(self.ws, True, "user", "pause", self.ctx()["tz"])
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "paused")
        self.append_im(type="launch", launchId="unrelated-route-launch", status="started", messageIds=[])
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "unfinished_launch")
        self.run_cmd("trigger", "finish", "--launch-id", "unrelated-route-launch", "--status", "stopped", "--confirmed-stopped")
        ledger.set_pause(self.ws, False, "user", "resume", self.ctx()["tz"])
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "route_changed_pending")
        self.set_im(channel="lark", userId=USER)
        self.assertEqual(self.run_cmd("poll")[1]["otherRoutes"], {"received": 0, "ready": 0, "clarifying": 0})
        self.assertEqual(self.run_cmd("status")[1]["needsReconcile"][0]["messageId"], "old-ready")
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "needs_reconcile")
        self.run_cmd("reply-state", "--message", "old-ready", "--state", "consumed", "--decision", D1,
                     "--confirmed-reconciled", "--note", "F1/F2 核对后本人明确放弃")
        self.set_im(channel="lark", userId=USER, larkProfile="other-app")
        self.assert_other_routes({"received": 0, "ready": 0, "clarifying": 0})
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "no_replies")

    def test_route_change_cannot_reuse_chat_cursor_or_reply_queue(self):
        self.inbox((D1, "待决定"))
        self.decide()
        self.im.user_says("接受")
        self.sync()
        self.queue(key="old-pending")
        self.set_im(ownerId="new-owner")
        plan = self.run_cmd("poll")[1]
        self.assertFalse(plan["readable"])
        self.assertIsNone(plan["chatId"])
        self.assertEqual(plan["sinceMs"], 0)
        self.assertEqual(plan["replies"], [])
        self.assertEqual(self.claim(), [])
        self.assertEqual(self.incoming([self.message(sender="new-owner")])[0], 2)

    def test_incoming_unreadable_and_rejection_privacy(self):
        self.assertEqual(self.incoming([self.message()])[0], 2)
        self.establish()
        since = self.run_cmd("poll")[1]["sinceMs"]
        messages = [self.message("SECRET", mid=str(n), **changes) for n, changes in enumerate((
            {"sender": "other"}, {"senderType": "app"}, {"chatId": "other-chat"}, {"deleted": True},
            {"at": since - 1}, {"at": notify.now_ms() + 600000}))]
        before = self.run_cmd("poll")[1]["sinceMs"]
        code, out = self.incoming(messages)
        self.assertEqual(code, 0, out)
        self.assertEqual(sum(out["rejected"].values()), 6)
        self.assertNotIn("SECRET", notify.im_path(self.ws).read_text())
        self.assertEqual(self.run_cmd("poll")[1]["sinceMs"], before)

    def test_batch_schema_is_atomic_and_no_time_coercion(self):
        self.establish()
        good = self.message(mid="good")
        before = notify.im_path(self.ws).read_bytes()
        for key, values in {"createTime": [True, -1, 1.5, float("nan"), float("inf"), 10**100], "messageId": ["", 1],
                            "chatId": [""], "senderId": [""], "senderType": [""], "text": [False],
                            "parentId": ["", 1], "deleted": [0]}.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assertEqual(self.incoming([good, {**good, key: value}])[0], 2)
                    self.assertEqual(notify.im_path(self.ws).read_bytes(), before)
        for since in (True, -1, self.im.clock + 1, 10**100):
            self.assertEqual(self.incoming([good], since=since)[0], 2)
            self.assertEqual(notify.im_path(self.ws).read_bytes(), before)
        self.assertEqual(self.incoming([good], complete=1)[0], 2)

    def test_batch_duplicate_conflict_and_incomplete_prefix(self):
        self.establish()
        a, b = self.message(mid="a"), self.message(mid="b")
        since = self.run_cmd("poll")[1]["sinceMs"]
        self.assertEqual(self.incoming([b, a, a], since=since, complete=False)[0], 0)
        self.assertEqual(len(notify.load_state(self.ws)["in"]), 2)
        self.assertTrue(self.run_cmd("poll")[1]["lastIncomplete"])
        # 旧断言整批拒收会让 inclusive 边界上的编辑消息永久卡住新暂停；
        # 现在仅拒收被编辑条目，并保留首次内容及原有 schema 整批拒绝测试。
        code, edited = self.incoming([{**a, "text": "changed"}], since=since)
        self.assertEqual((code, edited["rejected"]), (0, {"payload_changed": 1}))
        self.assertNotIn('"text":"changed"', notify.im_path(self.ws).read_text())
        self.incoming([], since=since)
        self.assertTrue(self.run_cmd("poll")[1]["lastIncomplete"])
        self.incoming([a, b], since=since)
        self.assertFalse(self.run_cmd("poll")[1]["lastIncomplete"])
        self.assertEqual(len(notify.load_state(self.ws)["in"]), 2)

    def test_edited_boundary_replays_do_not_block_new_pauses(self):
        self.establish()
        original = self.message("原始正文", mid="edited-boundary")
        self.assertEqual(self.incoming([original])[0], 0)
        since = self.run_cmd("poll")[1]["sinceMs"]
        edited = {**original, "text": "EDITED_SECRET", "createTime": original["createTime"] + 20000}
        for n in range(2):
            pause = self.message("暂停", mid=f"new-pause-{n}")
            code, out = self.incoming([edited, pause], since=since)
            self.assertEqual(code, 0, out)
            self.assertEqual(out["rejected"], {"payload_changed": 1})
            self.assertIn("暂停", out["commands"])
            self.assertTrue(out["paused"])
            self.assertEqual(self.run_cmd("poll")[1]["sinceMs"], pause["createTime"])
        rows = {r["messageId"]: r for r in self.run_cmd("replies")[1]["replies"]}
        self.assertEqual(rows[original["messageId"]]["text"], "原始正文")
        self.assertNotIn("EDITED_SECRET", notify.im_path(self.ws).read_text())
        self.assertIn("edited-boundary", self.run_cmd("status")[1]["incomingRejected"]["payload_changed"])

    def test_new_batch_id_conflict_rejects_all_versions_not_other_commands(self):
        self.establish()
        one = self.message("恢复", mid="ambiguous")
        two = {**one, "text": "暂停"}
        pause = self.message("暂停", mid="independent-pause")
        code, out = self.incoming([two, one, pause])
        self.assertEqual(code, 0, out)
        self.assertEqual(out["rejected"], {"payload_changed": 2})
        self.assertEqual(out["commands"], ["暂停"])
        self.assertEqual([r["messageId"] for r in out["new"]], [pause["messageId"]])
        self.assertNotIn("ambiguous", [r["messageId"] for r in self.run_cmd("replies")[1]["replies"]])

    def test_foreign_route_id_cannot_be_reassigned_by_payload_edit(self):
        self.establish()
        old = self.message("接受", mid="old-route-id")
        self.incoming([old])
        self.set_im(ownerId="second-owner")
        self.queue("second-seed")
        entry = next(e for e in self.claim() if e["key"] == "second-seed")
        self.receipt(entry, message="second-bot", chat="second-chat", created=old["createTime"])
        spoof = {**old, "senderId": "second-owner", "chatId": "second-chat", "text": "恢复"}
        pause = self.message("暂停", mid="second-pause", sender="second-owner", chatId="second-chat")
        code, out = self.incoming([spoof, pause])
        self.assertEqual(code, 0, out)
        self.assertEqual(out["rejected"], {"foreign_route": 1})
        self.assertEqual(out["commands"], ["暂停"])
        self.assertNotIn(old["messageId"], [r["messageId"] for r in self.run_cmd("replies")[1]["replies"]])

    def test_poll_and_status_are_read_only(self):
        self.establish()
        self.pending_command("pending", "pause")
        files = {p: p.read_bytes() for p in self.ws.rglob("*") if p.is_file()}
        self.run_cmd("poll")
        self.run_cmd("status")
        self.assertEqual({p: p.read_bytes() for p in self.ws.rglob("*") if p.is_file()}, files)
        self.assertIsNone(ledger.read_pause(self.ws))
        self.incoming()
        self.assertTrue(ledger.read_pause(self.ws))

    def cli_parallel(self, batches):
        argv = [sys.executable, str(SCRIPTS / "notify.py"), "--workspace", str(self.ws)]
        procs = [subprocess.Popen(argv + batch, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for batch in batches]
        results = [p.communicate(timeout=30) for p in procs]
        self.assertEqual([p.returncode for p in procs], [0] * len(procs), results)
        return [json.loads(out) for out, _ in results]

    def test_concurrent_claim_handoffs_only_once(self):
        self.queue()
        results = self.cli_parallel([["outbox", "--claim"]] * 2)
        self.assertEqual(sum(len(r["entries"]) for r in results), 1)
        self.assertEqual(len([r for r in notify.read_records(self.ws) if r.get("status") == "handoff"]), 1)

    def test_concurrent_incoming_order_and_replay(self):
        self.establish()
        since = self.run_cmd("poll")[1]["sinceMs"]
        resume = self.message("恢复", mid="resume")
        pause = self.message("暂停", mid="pause")
        batches = [["incoming", "--json", json.dumps({"since": since, "complete": True, "messages": [m]})] for m in (pause, resume)]
        self.cli_parallel(batches)
        self.assertTrue(ledger.read_pause(self.ws))
        self.incoming([resume], since=since)
        self.assertTrue(ledger.read_pause(self.ws))
        self.assertEqual(len([r for r in notify.read_records(self.ws) if r.get("type") == "command" and r.get("status") == "applied"]), 2)

    def test_command_side_effect_crash_and_later_safety_pause(self):
        self.establish()
        resume = self.message("恢复", mid="resume-crash")
        original = notify.append_record
        def fail_applied(ws, rec, tz):
            if rec.get("type") == "command":
                raise OSError("synthetic write failure")
            return original(ws, rec, tz)
        with patch.object(notify, "append_record", side_effect=fail_applied):
            self.assertEqual(self.incoming([resume])[0], 2)
        ledger.set_pause(self.ws, True, "followup", "security", self.ctx()["tz"])
        self.assertEqual(self.incoming([resume], since=0)[0], 0)
        self.assertEqual(ledger.read_pause(self.ws)["by"], "followup")
        self.assertEqual(notify.load_state(self.ws)["commandResults"][resume["messageId"]]["status"], "applied")

    def test_write_failure_after_message_replay_repairs_cursor_and_ack(self):
        self.establish()
        msg = self.message(mid="survives")
        since = self.run_cmd("poll")[1]["sinceMs"]
        original = notify.append_record
        def fail_cursor(ws, rec, tz):
            if rec.get("type") == "cursor":
                raise OSError("synthetic cursor failure")
            return original(ws, rec, tz)
        with patch.object(notify, "append_record", side_effect=fail_cursor):
            self.assertEqual(self.incoming([msg])[0], 2)
        self.assertIn("survives", notify.load_state(self.ws)["in"])
        self.assertEqual(self.incoming([msg], since=since)[0], 0)
        st = notify.load_state(self.ws)
        self.assertEqual(st["cursor"], msg["createTime"])
        self.assertIn("ack:survives", st["out"])
        self.assertEqual(len(st["in"]), 1)

    def test_atomic_write_failure_leaves_original_log(self):
        self.queue()
        before = notify.im_path(self.ws).read_bytes()
        with patch.object(ledger, "write_atomic", side_effect=OSError("disk full")):
            self.assertEqual(self.run_cmd("outbox", "--claim")[0], 2)
        self.assertEqual(notify.im_path(self.ws).read_bytes(), before)
        self.assertEqual(len(self.claim()), 1)

    def test_trigger_lock_pause_unfinished_cap_and_confirmed_stop(self):
        self.inbox((D1, "待决定"))
        self.decide()
        self.im.user_says("接受")
        self.sync()
        with ledger.lock_mutex(self.ws):
            ledger.acquire_lock(self.ws, self.ctx()["tz"], "followup", "run", ttl=60)
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "running_lock")
        self.ledger_run("lock", "release", "--run", "run")
        ledger.set_pause(self.ws, True, "user", "pause", self.ctx()["tz"])
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "paused")
        ledger.set_pause(self.ws, False, "user", "resume", self.ctx()["tz"])
        for n in range(2):
            code, launch = self.run_cmd("trigger", "claim")
            self.assertEqual(code, 0, launch)
            self.assertIn("由 IM 监听器唤起", launch["prompt"])
            self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "unfinished_launch")
            self.assertEqual(self.run_cmd("trigger", "finish", "--launch-id", launch["launchId"], "--status", "failed")[0], 2)
            self.assertEqual(self.run_cmd("trigger", "finish", "--launch-id", launch["launchId"], "--status", "failed", "--confirmed-stopped")[0], 0)
            self.assertEqual(self.run_cmd("trigger", "finish", "--launch-id", launch["launchId"], "--status", "failed", "--confirmed-stopped")[1]["status"], "duplicate")
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "attempt_limit")
        self.assertIn("不再自动执行", notify.load_state(self.ws)["out"][f"alert:{launch['launchId']}"]["text"])
        self.im.user_says("重试")
        self.sync()
        self.assertEqual(self.run_cmd("trigger", "claim")[0], 0)

    def test_legacy_started_never_expires(self):
        self.append_im(type="launch", launchId="old", status="started", at="2000-01-01T00:00:00+00:00", messageIds=[])
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "unfinished_launch")
        self.assertEqual(len(self.run_cmd("status")[1]["unfinishedLaunches"]), 1)
        self.assertEqual(self.run_cmd("trigger", "finish", "--launch-id", "old", "--status", "stopped", "--confirmed-stopped")[0], 0)

    def event(self, attempt="attempt", decision=D1, status="uncertain", skill="followup"):
        ev = {"schemaVersion": ledger.SCHEMA_VERSION, "at": ledger.now_iso(self.ctx()["tz"]), "platform": "boss", "account": "synthetic",
              "runId": "run", "attemptId": attempt, "skill": skill, "kind": "reply", "status": status,
              "company": "示例", "title": "岗位", "location": None, "recruiter": "HR", "decision": decision,
              "reason": "synthetic", "evidence": "synthetic"}
        with (self.ws / "ledger" / "events.jsonl").open("a") as f:
            f.write(json.dumps(ev) + "\n")

    def test_uncertain_decided_candidates_plain_sequence_multiple_and_resolved(self):
        self.inbox((D1, "已决定"), (D2, "已决定"))
        self.event()
        self.establish()
        self.im.user_says("已发出")
        self.im.user_says("01 已发出")
        _, out = self.sync()
        self.assertEqual([r["decisionHint"] for r in out["new"]], [D1, D1])
        self.event(attempt="second", decision=D2)
        self.im.user_says("未发出")
        _, out = self.sync()
        self.assertIsNone(out["new"][0]["decisionHint"])
        self.assertEqual(out["new"][0]["decisionCandidates"], [D1, D2])
        self.event(status="failed_no_send", skill="user")
        self.assertEqual(notify.candidates(self.ws), ([D2], None))
        self.event(attempt="second", decision=D2, status="sent", skill="user")
        self.assertEqual(notify.candidates(self.ws), ([], None))
        self.im.user_says("已发出")
        self.assertEqual(self.sync()[1]["new"][0]["state"], "ignored")

    def test_corrupt_ledger_does_not_block_pause_or_swallow_reply(self):
        self.inbox((D1, "待决定"))
        self.establish()
        (self.ws / "ledger" / "events.jsonl").write_text("corrupt\n")
        self.im.user_says("已发出")
        self.im.user_says("暂停")
        _, out = self.sync()
        self.assertTrue(out["paused"])
        reply = out["new"][0]
        self.assertEqual((reply["state"], reply["note"], reply["decisionHint"], reply["decisionCandidates"]), ("received", "ledger_unreadable", None, []))
        self.assertTrue(self.run_cmd("status")[1]["warnings"])

    def send_done(self, text, key=None):
        argv = ["send", "--kind", "done", "--decision", D1, "--text", text]
        return self.run_cmd(*argv, *(["--key", key] if key else []))

    def test_done_hint_participates_key_but_dynamic_candidates_do_not_duplicate(self):
        self.inbox((D1, "已决定"))
        self.event()
        _, first = self.send_done("结果不明")
        entry = self.claim()[0]
        self.assertIn("核对结果请引用本消息", entry["text"])
        self.assertEqual(first["key"], notify.default_key("done", D1, None, None, None, entry["text"]))
        self.receipt(entry, "failed", error="未投递")
        self.event(status="sent", skill="user")
        _, again = self.send_done("结果不明")
        self.assertEqual(first["key"], again["key"])
        self.assertEqual(self.claim()[0]["text"], entry["text"])

    def test_old_done_body_without_hint_not_resent_after_becoming_candidate(self):
        self.inbox((D1, "已决定"))
        _, first = self.send_done("结果不明", key=f"done:{D1}")
        entry = self.claim()[0]
        self.receipt(entry)
        self.event()
        _, again = self.send_done("结果不明")
        self.assertEqual((again["key"], again["status"]), (first["key"], "already_sent"))
        self.assertEqual(self.claim(), [])

    def test_ready_lifecycle_and_legacy_consumed_reconcile(self):
        self.inbox((D1, "待决定"))
        self.decide()
        mid = self.im.user_says("接受")
        self.sync()
        self.assertEqual(self.run_cmd("reply-state", "--message", mid, "--state", "ready")[0], 2)
        self.inbox((D1, "已决定"))
        self.run_cmd("reply-state", "--message", mid, "--state", "ready", "--decision", D1)
        ledger.set_pause(self.ws, True, "user", "pause", self.ctx()["tz"])
        self.assertEqual(self.run_cmd("status")[1]["readyReplies"][0]["messageId"], mid)
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "paused")
        self.run_cmd("reply-state", "--message", mid, "--state", "consumed", "--decision", D1)
        self.assertEqual(self.run_cmd("status")[1]["pendingReplies"], [])
        self.append_im(type="in", route=self.ctx()["cfg"]["route"], messageId="legacy", text="old", state="consumed")
        self.assertEqual(self.run_cmd("status")[1]["needsReconcile"][0]["messageId"], "legacy")

    def test_legacy_user_id_schema_matches_workspace(self):
        for owner in ("synthetic-owner", "ou_", "", " ou_synthetic", 123, False, None):
            self.set_im(channel="lark", userId=owner)
            self.assertEqual(self.run_cmd("poll")[0], 2, owner)
        self.set_im(channel="lark", userId="ou_synthetic")
        self.assertEqual(self.run_cmd("poll")[0], 0)
        # runtime ownerId 仍是不绑定具体平台的非空标识。
        self.set_im(ownerId="synthetic-owner")
        self.assertEqual(self.run_cmd("poll")[0], 0)

    def test_config_strict_window_types(self):
        for window in (True, -1, 86401, 1.0, float("nan")):
            self.set_im(transport={"idempotencyWindowSeconds": window})
            self.assertEqual(self.run_cmd("poll")[0], 2)
        self.set_im(ownerId="")
        self.assertEqual(self.run_cmd("poll")[0], 2)


    def test_receipt_payload_conflicts_and_first_chat_follows_receipt_order(self):
        self.queue("first-queued")
        self.queue("second-queued")
        one, two = self.claim()
        self.receipt(two, message="two", chat="chat-two", created=self.im.tick())
        self.receipt(one, message="one", chat="chat-one", created=self.im.tick())
        self.assertEqual(self.run_cmd("poll")[1]["chatId"], "chat-two")
        self.assertEqual(self.receipt(two, message="different", chat="chat-two")[0], 2)
        self.queue("third")
        three = self.claim()[0]
        self.assertEqual(self.receipt(three, message="two")[0], 2)
        self.assertEqual(notify.load_state(self.ws)["out"][three["key"]]["status"], "handoff")

    def test_ready_reply_failure_and_pause_preserve_queue(self):
        self.inbox((D1, "待决定"))
        self.decide()
        mid = self.im.user_says("接受")
        self.sync()
        self.run_cmd("reply-state", "--message", mid, "--state", "ready", "--decision", D1)
        ledger.set_pause(self.ws, True, "user", "pause", self.ctx()["tz"])
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "paused")
        ledger.set_pause(self.ws, False, "user", "resume", self.ctx()["tz"])
        for _ in range(2):
            _, launch = self.run_cmd("trigger", "claim")
            self.run_cmd("trigger", "finish", "--launch-id", launch["launchId"], "--status", "failed", "--confirmed-stopped")
            self.assertEqual(self.run_cmd("replies", "--state", "ready")[1]["replies"][0]["messageId"], mid)
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "attempt_limit")
        self.run_cmd("reply-state", "--message", mid, "--state", "consumed", "--decision", D1)
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "no_replies")

    def test_ready_global_pause_then_ledger_done_finishes_without_resend(self):
        self.inbox((D1, "待决定"))
        self.decide()
        mid = self.im.user_says("01 接受")
        self.sync()
        self.run_cmd("reply-state", "--message", mid, "--state", "ready", "--decision", D1)
        ledger.set_pause(self.ws, True, "user", "pause", self.ctx()["tz"])
        self.assertEqual(self.run_cmd("status")[1]["readyReplies"][0]["messageId"], mid)
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "paused")
        ledger.set_pause(self.ws, False, "user", "resume", self.ctx()["tz"])
        code, ev = self.ledger_run("append", "--json", json.dumps({
            "platform": "boss", "account": "synthetic", "runId": "run", "skill": "followup", "kind": "observe",
            "observed": "unpaused", "decision": D1, "company": "示例科技", "title": "前端", "location": None,
            "reason": "决定已完成", "evidence": None}))
        self.assertEqual(code, 0, ev)
        self.assertEqual(self.ledger_run("decision", "--id", D1)[1]["state"], "done")
        self.assertEqual(self.run_cmd("status")[1]["readyReplies"][0]["messageId"], mid)
        before = (self.ws / "ledger" / "events.jsonl").read_bytes()
        _, first = self.send_done("已完成：答复已执行")
        self.im.drain()
        _, again = self.send_done("已完成：答复已执行")
        self.assertEqual((again["key"], again["status"]), (first["key"], "already_sent"))
        self.run_cmd("reply-state", "--message", mid, "--state", "consumed", "--decision", D1)
        self.assertEqual(self.run_cmd("status")[1]["readyReplies"], [])
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "no_replies")
        self.assertEqual((self.ws / "ledger" / "events.jsonl").read_bytes(), before)

    def test_equal_time_commands_fail_closed_to_pause(self):
        self.establish()
        pause = self.message("暂停", mid="a-pause")
        resume = self.message("恢复", mid="z-resume", at=pause["createTime"])
        self.assertEqual(self.incoming([pause, resume])[0], 0)
        self.assertTrue(ledger.read_pause(self.ws))

    def test_new_test_key_not_answered_by_previous_reply(self):
        _, first = self.queue_test()
        self.im.user_says("收到", parent=first["messageId"])
        self.assertEqual(self.sync()[1]["new"][0]["state"], "test")
        _, second = self.queue_test()
        self.assertNotEqual(first["key"], second["key"])
        self.im.user_says("收到", parent=second["messageId"])
        self.assertEqual(self.sync()[1]["new"][0]["state"], "test")

    def test_old_applied_and_pending_commands_do_not_queue_historical_alerts(self):
        self.seed_legacy()
        self.append_im(type="in", messageId="old-applied", createTime=2200, text="暂停", state="command", command="暂停", commandVersion=2)
        self.append_im(type="command", messageId="old-applied", status="applied", text="已暂停")
        self.assertEqual(self.migrate()[0], 0)
        for _ in range(2):
            code, _ = self.incoming()
            self.assertEqual(code, 0)
            self.assertEqual({o["key"] for o in self.run_cmd("outbox")[1]["entries"]}, {"legacy-sent", "legacy-pending", "legacy-failed"})
            self.assertFalse(any(o["key"].startswith("legacy-cmd:") for o in self.run_cmd("outbox")[1]["entries"]))
        legacy = {r["messageId"]: r for r in self.run_cmd("status")[1]["legacyCommands"]}
        self.assertEqual(legacy["old-applied"]["commandStatus"], "applied")
        self.assertEqual(legacy["old-command"]["commandStatus"], "pending")
        self.assertEqual(notify.load_state(self.ws)["commandResults"]["old-command"]["status"], "pending")

    def test_legacy_commands_report_without_replaying(self):
        self.set_im(channel="lark", userId=USER)
        self.establish()
        ledger.set_pause(self.ws, True, "followup", "security", self.ctx()["tz"])
        self.append_im(type="in", messageId="old-resume", createTime=self.im.tick(), text="恢复", state="command", command="resume")
        self.append_im(type="chat", chatId="synthetic-chat")
        self.assertEqual(self.migrate()[0], 0)
        code, out = self.sync()
        self.assertEqual(code, 0, out)
        self.assertTrue(ledger.read_pause(self.ws))
        self.assertEqual(out["legacyCommands"][0]["messageId"], "old-resume")
        # 历史指令改为 status 汇总，已 applied 不误报，pending 也不逐条刷屏。
        self.assertEqual(len(self.im.sent), 1)
        n = len(self.im.sent)
        self.sync()
        self.assertEqual(len(self.im.sent), n)

    def test_wait_listen_service_removed_without_side_effects(self):
        for name in ("wait", "listen", "service"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                notify.main(["--workspace", str(self.ws), name])
            self.assertEqual(raised.exception.code, 2)
        self.assertFalse(notify.im_path(self.ws).exists())

    def test_explicit_null_im_and_invalid_wait_rejected(self):
        for im in (None, [], False, "runtime", {"channel": None}, {"channel": []}):
            (self.ws / "profile.json").write_text(json.dumps({"im": im}))
            code, out = self.run_cmd("poll")
            self.assertEqual(code, 2, out)
            self.assertIn("error", out)
        for wait in (True, -1, 241, 0.5, float("nan")):
            self.set_im(waitMinutes=wait)
            self.assertEqual(self.run_cmd("poll")[0], 2)
        for wait in (0, 240):
            self.set_im(waitMinutes=wait)
            self.assertEqual(self.run_cmd("poll")[0], 0)

    def test_late_old_retry_does_not_reset_newer_retry_generation(self):
        self.inbox((D1, "待决定"))
        self.decide()
        self.im.user_says("接受")
        self.sync()
        old = self.message("重试", mid="old-retry")
        new = self.message("重试", mid="new-retry")
        since = self.run_cmd("poll")[1]["sinceMs"]
        self.incoming([new])
        _, launch = self.run_cmd("trigger", "claim")
        self.run_cmd("trigger", "finish", "--launch-id", launch["launchId"], "--status", "ok", "--confirmed-stopped")
        self.incoming([old], since=since)
        self.assertEqual(len(notify.load_state(self.ws)["retries"]), 1)
        self.assertIn("旧重试指令", notify.load_state(self.ws)["commandResults"]["old-retry"]["text"])

    def test_pending_command_replay_and_retry_does_not_reset_real_launch_count(self):
        self.inbox((D1, "待决定"))
        self.decide()
        mid = self.im.user_says("接受")
        self.sync()
        self.im.user_says("重试")
        self.sync()
        retry = next(r for r in notify.load_state(self.ws)["in"].values() if r.get("command") == "重试")
        for _ in range(2):
            _, launch = self.run_cmd("trigger", "claim")
            self.run_cmd("trigger", "finish", "--launch-id", launch["launchId"], "--status", "ok", "--confirmed-stopped")
        self.append_im(type="command", messageId=retry["messageId"], status="pending")
        self.incoming()
        self.assertEqual(len(notify.launches_for(notify.load_state(self.ws), mid)), 2)
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "attempt_limit")

    def test_ready_todo_and_completed_decision_only_explicitly_consumed(self):
        self.inbox((D1, "待处理"))
        self.decide()
        mid = self.im.user_says("已处理")
        self.sync()
        self.inbox((D1, "已处理"))
        self.run_cmd("reply-state", "--message", mid, "--state", "ready", "--decision", D1)
        self.assertEqual(self.run_cmd("poll")[1]["replies"][0]["state"], "ready")
        _, done = self.send_done("已处理：本人已完成")
        self.assertEqual(self.run_cmd("poll")[1]["replies"][0]["state"], "ready")
        self.run_cmd("reply-state", "--message", mid, "--state", "consumed", "--decision", D1)
        self.assertEqual(self.run_cmd("poll")[1]["replies"], [])
        self.assertEqual([o["key"] for o in notify.load_state(self.ws)["out"].values() if o["kind"] == "done"], [done["key"]])

    def migrate(self, owner=USER, chat="synthetic-chat", confirmed=True):
        return self.run_cmd("migrate-legacy", "--owner-id", owner, "--chat-id", chat,
                            *(["--confirmed-owner"] if confirmed else []))

    def seed_legacy(self):
        self.set_im(channel="lark", userId=USER)
        self.append_im(type="out", key="legacy-sent", kind="done", decision=D1, status="sent",
                       messageId="legacy-bot", createTime=1000, text=notify.compose("done", "原结果", D1))
        self.append_im(type="out", key="legacy-pending", kind="alert", status="pending", text="unknown")
        self.append_im(type="out", key="legacy-failed", kind="alert", status="failed", text="unknown")
        self.append_im(type="chat", chatId="synthetic-chat")
        self.append_im(type="cursor", createTime=2000)
        self.append_im(type="in", messageId="old-ready", createTime=2000, text="接受", state="ready", decision=D1, stateVersion=2)
        self.append_im(type="in", messageId="old-command", createTime=2100, text="恢复", state="command", command="恢复", commandVersion=2)
        self.append_im(type="command", messageId="old-command", status="pending")

    def test_unbound_summary_is_count_only_consistent_and_trigger_blocks(self):
        self.seed_legacy()
        self.append_im(type="in", messageId="private-received-id", text="PRIVATE_LEGACY_TEXT", state="received")
        self.append_im(type="in", messageId="private-clarifying-id", text="PRIVATE_CLARIFYING", state="clarifying")
        self.append_im(type="in", messageId="private-applied-id", text="恢复", state="command", command="恢复", createTime=2300)
        self.append_im(type="command", messageId="private-applied-id", status="applied")
        expected = {"replies": {"received": 1, "ready": 1, "clarifying": 1},
                    "commands": {"pending": 1, "applied": 1}, "unknownOutbox": 2, "bindable": True}
        for command in ("poll", "status", "replies"):
            summary = self.run_cmd(command)[1]["legacyUnbound"]
            self.assertEqual(summary, expected)
            self.assertNotIn("private-", json.dumps(summary))
            self.assertNotIn("PRIVATE_", json.dumps(summary))
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "legacy_route_unbound")
        ledger.set_pause(self.ws, True, "user", "pause", self.ctx()["tz"])
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "paused")
        self.append_im(type="launch", launchId="unrelated-old-launch", status="started", messageIds=[])
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "unfinished_launch")
        self.assertEqual(self.migrate()[0], 0)
        empty = {"replies": {"received": 0, "ready": 0, "clarifying": 0},
                 "commands": {"pending": 0, "applied": 0}, "unknownOutbox": 0, "bindable": False}
        for command in ("poll", "status", "replies"):
            self.assertEqual(self.run_cmd(command)[1]["legacyUnbound"], empty)
        self.assertIn("old-ready", [r["messageId"] for r in self.run_cmd("status")[1]["needsReconcile"]])

    def test_status_unbound_summary_does_not_expose_old_outbox_body(self):
        self.seed_legacy()
        self.append_im(type="out", key="old-private-outbox", kind="alert", status="pending", text="PRIVATE_OUTBOX_BODY")
        self.set_im(ownerId="new-runtime-owner")
        status = self.run_cmd("status")[1]
        self.assertEqual(status["legacyUnbound"]["unknownOutbox"], 3)
        self.assertNotIn("PRIVATE_OUTBOX_BODY", json.dumps(status))
        self.assertNotIn("text", status["unconfirmedSends"][0])

    def test_unbound_legacy_never_assumes_owner_even_before_first_start(self):
        self.seed_legacy()
        self.set_im(channel="lark", userId="ou_changed_before_first_start")
        before = notify.im_path(self.ws).read_bytes()
        plan = self.run_cmd("poll")[1]
        self.assertEqual((plan["readable"], plan["chatId"], plan["sinceMs"], plan["replies"]), (False, None, 0, []))
        self.assertTrue(plan["legacyRouteUnbound"])
        self.assertEqual(self.run_cmd("replies")[1]["replies"], [])
        self.assertEqual(self.run_cmd("reply-state", "--message", "old-ready", "--state", "consumed", "--decision", D1)[0], 2)
        # 未绑定旧 ready 不能伪装成空队列；必须提示迁移，而不是 no_replies。
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "legacy_route_unbound")
        self.assertEqual(notify.im_path(self.ws).read_bytes(), before)
        self.assertEqual(self.send_done("原结果")[1]["status"], "already_sent")
        self.assertEqual(self.claim(), [])

    def test_migration_requires_confirmation_exact_owner_chat_and_no_ambiguity(self):
        self.seed_legacy()
        before = notify.im_path(self.ws).read_bytes()
        for args in ({"confirmed": False}, {"owner": "wrong"}, {"chat": "wrong"}):
            self.assertEqual(self.migrate(**args)[0], 2)
            self.assertEqual(notify.im_path(self.ws).read_bytes(), before)
        self.append_im(type="chat", chatId="ambiguous")
        before = notify.im_path(self.ws).read_bytes()
        self.assertEqual(self.migrate()[0], 2)
        self.assertEqual(notify.im_path(self.ws).read_bytes(), before)

    def test_migration_binds_once_preserves_cursor_ready_and_never_replays_old_command(self):
        self.seed_legacy()
        ledger.set_pause(self.ws, True, "followup", "security", self.ctx()["tz"])
        code, bound = self.migrate()
        self.assertEqual((code, bound["status"]), (0, "bound"))
        before = notify.im_path(self.ws).read_bytes()
        self.assertEqual(self.migrate()[1]["status"], "duplicate")
        self.assertEqual(notify.im_path(self.ws).read_bytes(), before)
        plan = self.run_cmd("poll")[1]
        self.assertEqual((plan["chatId"], plan["sinceMs"], plan["readable"]), ("synthetic-chat", 2000, True))
        self.assertEqual(plan["replies"][0]["messageId"], "old-ready")
        self.assertTrue(plan["replies"][0]["needsReconcile"])
        self.assertEqual(self.run_cmd("status")[1]["needsReconcile"][0]["messageId"], "old-ready")
        self.assertEqual(self.incoming()[0], 0)
        self.assertEqual(ledger.read_pause(self.ws)["by"], "followup")
        self.assertEqual(notify.load_state(self.ws)["commandResults"]["old-command"]["status"], "pending")
        # 旧指令不再逐条排提醒；旧 unknown 仍不能重发。
        self.assertEqual(self.claim(), [])
        self.assertEqual(self.claim(), [])
        self.set_im(channel="lark", userId="ou_new_owner")
        self.assertEqual(self.migrate(owner="ou_new_owner")[0], 2)
        self.assertFalse(self.run_cmd("poll")[1]["readable"])
        self.set_im(channel="lark", userId=USER, larkProfile="different-app")
        self.assertEqual(self.migrate()[0], 2)
        self.set_im()
        self.assertEqual(self.migrate()[0], 2)

    def test_bound_legacy_ready_requires_reconciliation_before_trigger_or_state(self):
        self.seed_legacy()
        self.assertEqual(self.migrate()[0], 0)
        before = notify.im_path(self.ws).read_bytes()
        code, blocked = self.run_cmd("trigger", "claim")
        self.assertEqual((code, blocked["reason"]), (1, "needs_reconcile"))
        for state in ("ready", "consumed", "ignored", "clarifying"):
            self.assertEqual(self.run_cmd("reply-state", "--message", "old-ready", "--state", state, "--decision", D1)[0], 2)
        self.assertEqual(notify.im_path(self.ws).read_bytes(), before)
        self.assertEqual(self.run_cmd("reply-state", "--message", "old-ready", "--state", "ready", "--decision", D1,
                                      "--confirmed-reconciled")[0], 2)
        self.assertEqual(self.run_cmd("reply-state", "--message", "old-ready", "--state", "ready", "--decision", D2,
                                      "--confirmed-reconciled", "--note", "F1/F2 checked")[0], 2)
        code, _ = self.run_cmd("reply-state", "--message", "old-ready", "--state", "ready", "--decision", D1,
                               "--confirmed-reconciled", "--note", "F1/F2：已核对 inbox 与 ledger，结论待执行")
        self.assertEqual(code, 0)
        reply = self.run_cmd("replies", "--state", "ready")[1]["replies"][0]
        self.assertEqual((reply["messageId"], reply["decision"], reply["state"]), ("old-ready", D1, "ready"))
        self.assertFalse(reply.get("needsReconcile", False))
        self.assertEqual(self.run_cmd("status")[1]["needsReconcile"], [])
        code, launch = self.run_cmd("trigger", "claim")
        self.assertEqual(code, 0, launch)
        self.assertEqual(launch["messageIds"], ["old-ready"])
        self.run_cmd("trigger", "finish", "--launch-id", launch["launchId"], "--status", "ok", "--confirmed-stopped")
        self.assertEqual(self.run_cmd("reply-state", "--message", "old-ready", "--state", "consumed", "--decision", D1)[0], 0)
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "no_replies")

    def test_resume_does_not_count_unreconciled_legacy_ready_as_executable(self):
        self.seed_legacy()
        self.migrate()
        resume = self.message("恢复", mid="current-resume", at=3000)
        self.assertEqual(self.incoming([resume])[0], 0)
        self.assertEqual(self.run_cmd("trigger", "claim")[1]["reason"], "needs_reconcile")
        ack = next(o for o in self.run_cmd("outbox")[1]["entries"] if o["key"] == "cmd:current-resume")
        self.assertNotIn("条答复待执行", ack["text"])
        self.assertEqual(self.run_cmd("status")[1]["needsReconcile"][0]["messageId"], "old-ready")

    def test_legacy_missing_reply_metadata_reports_safe_resend_recovery(self):
        self.seed_legacy()
        self.inbox((D1, "待决定"))
        self.append_im(type="in", messageId="legacy-no-meta", text="PRIVATE_LEGACY_REPLY", createTime=2150, state="received")
        self.migrate()
        for command, field in (("replies", "replies"), ("status", "pendingReplies")):
            row = next(r for r in self.run_cmd(command)[1][field] if r["messageId"] == "legacy-no-meta")
            self.assertEqual(row["replyToBlockedReason"], "missing_message_metadata")
            self.assertEqual(row["replyRecovery"], "request_new_reply_with_decision_id_or_terminal")
        code, blocked = self.run_cmd("send", "--kind", "clarify", "--reply-to", "legacy-no-meta", "--text", "请明确事项")
        self.assertEqual((code, blocked["reason"]), (1, "reply_to_untrusted"))
        self.assertEqual(self.run_cmd("reply-state", "--message", "legacy-no-meta", "--state", "clarifying")[0], 2)
        self.assertEqual(self.run_cmd("replies", "--state", "received")[1]["replies"][0]["state"], "received")
        self.assertFalse(any(o["key"].startswith("clarify:") for o in self.run_cmd("outbox")[1]["entries"]))
        # 可选独立提醒只请求重发，不猜引用、不带旧正文，也不冒充 clarify 已送达。
        self.queue("request-resend:synthetic-decision", f"请在本通道重新回复 {D1}，或在终端明确处理")
        self.im.drain()
        fresh = self.message(f"{D1} 本人重新说明", mid="fresh-verified-reply")
        self.assertEqual(self.incoming([fresh])[0], 0)
        code, queued = self.run_cmd("send", "--kind", "clarify", "--reply-to", fresh["messageId"], "--text", "请确认具体选项")
        self.assertEqual((code, queued["status"]), (0, "queued"))
        self.assertEqual(self.run_cmd("reply-state", "--message", fresh["messageId"], "--state", "clarifying")[0], 2)
        handoffs = self.claim()
        for entry in handoffs:
            self.assertEqual(self.receipt(entry, message=f"sent-{entry['key']}", created=self.im.tick())[0], 0)
        self.assertEqual(self.run_cmd("reply-state", "--message", fresh["messageId"], "--state", "clarifying")[0], 0)
        rows = {r["messageId"]: r for r in self.run_cmd("replies")[1]["replies"]}
        self.assertEqual(rows["legacy-no-meta"]["state"], "received")
        self.assertEqual(rows[fresh["messageId"]]["state"], "clarifying")
        self.assertNotIn("replyToBlockedReason", rows[fresh["messageId"]])

    def test_clarifying_requires_confirmed_sent_not_queued_or_failed_question(self):
        self.inbox((D1, "待决定"))
        self.establish()
        answer = self.message("含义不清", mid="clarify-state-answer")
        self.incoming([answer])
        self.im.drain()
        _, queued = self.run_cmd("send", "--kind", "clarify", "--reply-to", answer["messageId"], "--text", "你指哪项？")
        self.assertEqual(self.run_cmd("reply-state", "--message", answer["messageId"], "--state", "clarifying")[0], 2)
        question = self.claim()[0]
        self.assertEqual(question["key"], queued["key"])
        self.receipt(question, "failed", error="通道明确未接收")
        self.assertEqual(self.run_cmd("reply-state", "--message", answer["messageId"], "--state", "clarifying")[0], 2)
        self.assertEqual(self.run_cmd("poll")[1]["replies"][0]["state"], "received")
        retry = self.claim()[0]
        self.receipt(retry, message="confirmed-question", created=self.im.tick())
        self.assertEqual(self.run_cmd("reply-state", "--message", answer["messageId"], "--state", "clarifying")[0], 0)
        self.assertEqual(self.run_cmd("replies", "--state", "clarifying")[1]["replies"][0]["messageId"], answer["messageId"])

    def test_concurrent_migration_is_one_binding(self):
        self.seed_legacy()
        args = ["migrate-legacy", "--owner-id", USER, "--chat-id", "synthetic-chat", "--confirmed-owner"]
        results = self.cli_parallel([args, args])
        self.assertEqual(sorted(r["status"] for r in results), ["bound", "duplicate"])
        self.assertEqual(len([r for r in notify.read_records(self.ws) if r.get("type") == "binding"]), 1)

    def test_migration_without_old_chat_fails(self):
        self.set_im(channel="lark", userId=USER)
        self.assertEqual(self.migrate()[0], 2)
        self.assertFalse(notify.im_path(self.ws).exists())

    # 原业务断言保留，仅将外部拉取替换为显式本地同步。
    def test_quoted_reply_resolves_decision_acks_and_is_consumed(self):
        self.inbox(("D-20261030-01", "待决定"), ("D-20261030-02", "待决定"))
        note = self.decide()
        mid = self.im.user_says("接受", parent=note)
        self.im.user_says("别人发的", sender="ou_other")
        _, out = self.sync()
        self.assertEqual(len(out["new"]), 1)
        self.assertEqual(out["replies"][0]["decisionHint"], "D-20261030-01")
        ack = self.im.sent[-1]
        self.assertEqual(ack["replyTo"], mid)
        self.assertIn("已收到", ack["text"])
        # 再拉一次不会重复记录或回执
        n = len(self.im.sent)
        self.assertEqual(self.sync()[1]["new"], [])
        self.assertEqual(len(self.im.sent), n)
        self.assertEqual(self.run_cmd("reply-state", "--message", mid, "--state", "consumed")[0], 2)
        code, _ = self.run_cmd("reply-state", "--message", mid, "--state", "consumed",
                               "--decision", "D-20261030-01")
        self.assertEqual(code, 0)
        self.assertEqual(self.sync()[1]["replies"], [])

    def test_sequence_number_and_ambiguous_replies(self):
        self.inbox(("D-20261030-01", "待决定"), ("D-20261030-02", "待决定"), ("D-20261029-03", "已决定"))
        self.decide()
        self.im.user_says("02 婉拒")
        self.im.user_says("接受")
        _, out = self.sync()
        hints = {r["text"]: (r["decisionHint"], r["decisionCandidates"]) for r in out["replies"]}
        self.assertEqual(hints["02 婉拒"][0], "D-20261030-02")
        self.assertIsNone(hints["接受"][0])
        self.assertEqual(len(hints["接受"][1]), 2)

    def test_message_without_open_items_is_ignored_with_help(self):
        self.inbox(("D-20261030-01", "待决定"))
        self.decide()
        self.inbox(("D-20261030-01", "已决定"))
        mid = self.im.user_says("在吗")
        _, out = self.sync()
        self.assertEqual(out["replies"], [])
        self.assertIn("暂停、恢复、重试", self.im.sent[-1]["text"])
        self.assertEqual(self.run_cmd("replies", "--state", "ignored")[1]["replies"][0]["messageId"], mid)

    def test_pause_and_resume_commands_drive_ledger(self):
        self.inbox(("D-20261030-01", "待决定"))
        self.decide()
        self.im.user_says("暂停")
        _, out = self.sync()
        self.assertEqual(out["commands"], ["暂停"])
        self.assertEqual(ledger.read_pause(self.ws)["by"], "im")
        self.assertIn("已暂停", self.im.sent[-1]["text"])
        self.im.user_says("01 接受")
        _, out = self.sync()
        self.assertIn("暂停状态", self.im.sent[-1]["text"])
        self.im.user_says(" 恢复！")
        _, out = self.sync()
        self.assertIsNone(ledger.read_pause(self.ws))
        self.assertIn("1 条答复待执行", self.im.sent[-1]["text"])

    def test_test_message_roundtrip(self):
        code, out = self.queue_test()
        self.assertEqual(code, 0)
        self.im.user_says("收到")
        _, out = self.sync()
        self.assertEqual(out["replies"], [])
        self.assertIn("测试成功", self.im.sent[-1]["text"])

    def test_test_does_not_swallow_ambiguous_open_item_replies(self):
        self.inbox(("D-20261030-01", "待决定"), ("D-20261030-02", "待决定"))
        self.assertEqual(self.queue_test()[0], 0)
        mids = [self.im.user_says(text) for text in ("收到", "接受")]
        _, out = self.sync()
        self.assertEqual([r["state"] for r in out["new"]], ["received", "received"])
        self.assertEqual([r["messageId"] for r in out["replies"]], mids)
        self.assertTrue(all(len(r["decisionCandidates"]) == 2 for r in out["replies"]))

    def test_test_without_items_only_confirms_once_per_batch_and_across_polls(self):
        _, test = self.queue_test()
        first = self.im.user_says("收到")
        second = self.im.user_says("接受", parent=test["messageId"])
        # 相同服务端秒 / 游标边界的重复拉取仍须按 messageId 幂等。
        for m in self.im.messages:
            if m["messageId"] in (first, second):
                m["createTime"] = self.im.clock
        _, out = self.sync()
        self.assertEqual([(r["messageId"], r["state"]) for r in out["new"]],
                         [(first, "test"), (second, "ignored")])
        self.assertEqual(out["replies"], [])
        sent = len(self.im.sent)
        self.assertEqual(self.sync()[1]["new"], [])
        self.assertEqual(len(self.im.sent), sent)
        self.im.user_says("再次收到", parent=test["messageId"])
        self.assertEqual(self.sync()[1]["new"][0]["state"], "ignored")
        self.assertEqual(sum("测试成功" in m["text"] for m in self.im.sent), 1)

    def test_quoted_test_completes_once_and_later_replies_remain_received(self):
        self.inbox(("D-20261030-01", "待决定"), ("D-20261030-02", "待决定"))
        _, test = self.queue_test()
        mids = [self.im.user_says("收到", parent=test["messageId"]),
                self.im.user_says("接受", parent=test["messageId"]),
                self.im.user_says("婉拒")]
        _, out = self.sync()
        self.assertEqual([r["state"] for r in out["new"]], ["test", "received", "received"])
        self.assertEqual([r["messageId"] for r in out["replies"]], mids[1:])
        sent = len(self.im.sent)
        self.assertEqual(self.sync()[1]["new"], [])
        self.assertEqual(len(self.im.sent), sent)
        late = self.im.user_says("收到", parent=test["messageId"])
        _, out = self.sync()
        self.assertEqual(out["new"][0]["state"], "received")
        self.assertIn(late, [r["messageId"] for r in out["replies"]])
        self.assertEqual(sum("测试成功" in m["text"] for m in self.im.sent), 1)

    def test_open_item_reply_does_not_consume_test_window_before_explicit_quote(self):
        self.inbox(("D-20261030-01", "待决定"))
        _, test = self.queue_test()
        self.im.user_says("接受")
        _, out = self.sync()
        self.assertEqual(out["new"][0]["state"], "received")
        self.assertEqual(out["new"][0]["decisionHint"], "D-20261030-01")
        self.im.user_says("收到", parent=test["messageId"])
        self.assertEqual(self.sync()[1]["new"][0]["state"], "test")
        self.im.user_says("收到", parent=test["messageId"])
        self.assertEqual(self.sync()[1]["new"][0]["state"], "received")

    def test_explicit_item_replies_and_commands_take_priority_over_test(self):
        self.inbox(("D-20261030-01", "待决定"), ("D-20261030-02", "待决定"))
        note = self.decide()
        _, test = self.queue_test()
        cases = [("接受", note), ("02 婉拒", None), ("D-20261030-01 接受", None),
                 ("01 接受", test["messageId"]), ("99 接受", test["messageId"])]
        mids = [self.im.user_says(text, parent=parent) for text, parent in cases]
        self.im.user_says("暂停", parent=test["messageId"])
        self.im.user_says("恢复", parent=test["messageId"])
        self.im.user_says("重试", parent=test["messageId"])
        self.im.user_says("收到", parent=test["messageId"])
        _, out = self.sync()
        self.assertEqual([r["state"] for r in out["new"]],
                         ["received"] * len(cases) + ["command"] * 3 + ["test"])
        self.assertEqual([r["messageId"] for r in out["replies"]], mids)
        self.assertEqual(out["commands"], ["暂停", "恢复", "重试"])
        self.assertIsNone(ledger.read_pause(self.ws))
        self.assertIsNone(out["replies"][-1]["decisionHint"])

    def test_explicit_item_reference_without_open_items_is_not_test(self):
        _, test = self.queue_test()
        self.im.user_says("D-20261030-01 接受", parent=test["messageId"])
        self.im.user_says("01 接受")
        self.im.user_says("收到", parent=test["messageId"])
        _, out = self.sync()
        self.assertEqual([r["state"] for r in out["new"]], ["received", "ignored", "test"])
        self.assertEqual(out["replies"][0]["decisionHint"], "D-20261030-01")

    def test_retry_replay_does_not_reset_launch_count(self):
        """同一条「重试」消息重放不再追加 retry，也不移动 retryAt。"""
        self.inbox(("D-20261030-01", "待决定"))
        self.decide()
        mid = self.im.user_says("01 接受")
        self.sync()
        cmd_mid = "om_cmd_retry_crash"
        self.append_im(type="retry", sourceId=cmd_mid, messageIds=[mid])
        before = [r for r in notify.read_records(self.ws) if r["type"] == "retry"]
        self.pending_command(cmd_mid, "retry")
        code, out = self.sync()
        self.assertEqual(code, 0, out)
        retries = [r for r in notify.read_records(self.ws) if r["type"] == "retry"]
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0]["at"], before[0]["at"])
        self.assertEqual(notify.load_state(self.ws)["retryAt"][mid], before[0]["at"])
        self.assertIn("1 条未完成的答复", self.im.sent[-1]["text"])

    def test_numeric_tokens_only_match_standalone_sequence(self):
        self.inbox(("D-20261030-01", "待决定"))
        self.decide()
        rows = self.replies_after("3点可以", "15k", "01 接受")
        self.assertEqual(rows["3点可以"]["state"], "received")
        self.assertEqual(rows["3点可以"]["decisionHint"], "D-20261030-01")
        self.assertEqual(rows["15k"]["decisionHint"], "D-20261030-01")
        self.assertEqual(rows["01 接受"]["decisionHint"], "D-20261030-01")
        self.assertNotIn("没有等你答复", self.im.sent[-1]["text"])

    def test_unknown_sequence_keeps_reply_instead_of_guessing(self):
        self.inbox(("D-20261030-01", "待决定"))
        self.decide()
        rows = self.replies_after("99 接受")
        self.assertEqual(rows["99 接受"]["state"], "received")
        self.assertIsNone(rows["99 接受"]["decisionHint"])
        self.assertEqual(rows["99 接受"]["decisionCandidates"], [])
        # 没有可答事项时才 ignored
        self.inbox(("D-20261030-01", "已决定"))
        self.replies_after("98 接受")
        ignored = self.run_cmd("replies", "--state", "ignored")[1]["replies"]
        self.assertEqual([r["text"] for r in ignored], ["98 接受"])
        self.assertIn("暂停、恢复、重试", self.im.sent[-1]["text"])

    def test_multiple_open_items_with_plain_numbers_stay_received(self):
        self.inbox(("D-20261030-01", "待决定"), ("D-20261030-02", "待决定"))
        self.decide()
        rows = self.replies_after("3点可以", "02 婉拒")
        self.assertEqual(rows["3点可以"]["state"], "received")
        self.assertIsNone(rows["3点可以"]["decisionHint"])
        self.assertEqual(len(rows["3点可以"]["decisionCandidates"]), 2)
        self.assertEqual(rows["02 婉拒"]["decisionHint"], "D-20261030-02")


if __name__ == "__main__":
    unittest.main()

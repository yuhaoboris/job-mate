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
        (self.ws / "job-mate.json").write_text(json.dumps({"schemaVersion": 1, "suiteVersion": "1.0.0", "accounts": {}}))
        (self.ws / "ledger").mkdir()
        (self.ws / "inbox").mkdir()
        self.set_im()
        self.inbox()
        self.im = LocalRuntime(self)

    def tearDown(self):
        self.tmp.cleanup()

    def set_im(self, **changes):
        im = {"channel": "lark", "ownerId": USER, "larkProfile": "synthetic"}
        im.update(changes)
        if im["channel"] == "none":
            im = {"channel": "none"}
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
                       larkProfile=cfg["larkProfile"])
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
        self.assertIsNone(self.run_cmd("poll")[1]["chatId"])
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
            {"sender": "other"}, {"senderType": "app"}, {"deleted": True},
            {"at": since - 1}, {"at": notify.now_ms() + 600000}))]
        before = self.run_cmd("poll")[1]["sinceMs"]
        code, out = self.incoming(messages)
        self.assertEqual(code, 0, out)
        self.assertEqual(sum(out["rejected"].values()), 5)
        self.assertNotIn("SECRET", notify.im_path(self.ws).read_text())
        self.assertEqual(self.run_cmd("poll")[1]["sinceMs"], before)

    def test_foreign_chat_rejects_entire_batch_without_writing(self):
        self.establish()
        before = notify.im_path(self.ws).read_bytes()
        self.assertEqual(self.incoming([self.message("暂停", mid="good"),
                                       self.message(chatId="other-chat", mid="bad")])[0], 2)
        self.assertEqual(notify.im_path(self.ws).read_bytes(), before)
        self.assertIsNone(ledger.read_pause(self.ws))

    def test_unknown_never_retried_and_profile_change_blocks_frozen_route(self):
        self.queue("uncertain")
        entry = self.claim()[0]
        self.receipt(entry, "unknown", error="超时")
        self.assertEqual(self.claim(), [])
        self.assertEqual(self.queue("uncertain")[1]["reason"], "unknown_delivery")
        self.queue("pending")
        self.set_im(larkProfile="new-profile")
        self.assertEqual(self.claim(), [])
        status = self.run_cmd("status")[1]
        self.assertEqual(set(status["routeConflicts"]), {"uncertain", "pending"})
        self.assertFalse(status["readable"])
        self.set_im()
        self.assertEqual([e["key"] for e in self.claim()], ["pending"])

    def test_route_digest_and_claim_freeze_only_channel_owner_and_profile(self):
        route = self.ctx()["cfg"]["route"]
        self.queue()
        self.set_im(waitMinutes=0, larkCommand=["node", "synthetic.js"])
        self.assertEqual(self.ctx()["cfg"]["route"], route)
        entry = self.claim()[0]
        self.assertEqual((entry["ownerId"], entry["larkProfile"], entry["route"]),
                         (USER, "synthetic", route))
        with patch.object(notify, "now_ms", return_value=notify.now_ms() + 700000):
            self.assertTrue(self.run_cmd("outbox", "--stale")[1]["entries"])
        self.assertEqual(self.receipt(entry, "unknown")[0], 0)
        self.assertEqual(self.claim(), [])

    def test_shared_im_configuration_validation(self):
        import workspace
        valid = [{}, {"im": {"channel": "none"}},
                 {"im": {"channel": "lark", "ownerId": "not-prefix-constrained", "larkProfile": "default"}},
                 {"im": {"channel": "lark", "ownerId": USER, "larkProfile": "default",
                         "waitMinutes": 0, "larkCommand": ["node", "synthetic.js"]}}]
        for profile in valid:
            with self.subTest(profile=profile):
                self.assertIn(notify.im_config(profile)["channel"], {"none", "lark"})
                problems = []
                workspace.check_im(profile, lambda field, problem: problems.append(problem), [])
                self.assertEqual(problems, [])
        base = {"channel": "lark", "ownerId": USER, "larkProfile": "default"}
        invalid = [None, False, [], {}, {"channel": "other"}, {"channel": []},
                   {"channel": "none", "waitMinutes": 60}, {"channel": "lark", "unknown": True}]
        for field, values in {"ownerId": [None, "", " ", 1], "larkProfile": [None, "", " ", []],
                              "waitMinutes": [True, -1, 241, 0.1],
                              "larkCommand": [None, [], "", [""], [1]]}.items():
            invalid += [{**base, field: value} for value in values]
        for im in invalid:
            with self.subTest(im=im):
                with self.assertRaises(notify.NotifyError):
                    notify.im_config({"im": im})
                problems = []
                workspace.check_im({"im": im}, lambda field, problem: problems.append(problem), [])
                self.assertTrue(problems)

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

    def event(self, attempt="attempt", decision=D1, status="uncertain", skill="followup"):
        ev = {"schemaVersion": ledger.SCHEMA_VERSION, "at": ledger.now_iso(self.ctx()["tz"]), "platform": "boss", "account": "synthetic",
              "runId": "run", "attemptId": attempt, "skill": skill, "kind": "reply", "status": status,
              "company": "示例", "title": "岗位", "location": None, "recruiter": "HR", "decision": decision,
              "reason": "synthetic", "evidence": "synthetic"}
        ledger.append_line(self.ws, ledger.prepare_event(ev))

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

    def test_current_done_version_freezes_body_when_candidates_change(self):
        self.inbox((D1, "已决定"))
        _, first = self.send_done("等待本人核对")
        original = self.claim()[0]
        self.receipt(original, "failed", error="明确未投递")
        self.event()
        _, again = self.send_done("等待本人核对")
        self.assertEqual(again["key"], first["key"])
        retried = self.claim()[0]
        self.assertEqual(retried["text"], original["text"])
        self.assertEqual(retried["idempotencyKey"], original["idempotencyKey"])
        self.receipt(retried, message="done")
        self.assertEqual(self.send_done("等待本人核对")[1]["status"], "already_sent")
        self.assertNotEqual(self.send_done("核对完成")[1]["key"], first["key"])

    def test_ready_reply_pause_preserves_queue_until_explicit_consumption(self):
        self.inbox((D1, "待决定"))
        self.decide()
        mid = self.im.user_says("接受")
        self.sync()
        self.run_cmd("reply-state", "--message", mid, "--state", "ready", "--decision", D1)
        for paused in (True, False):
            ledger.set_pause(self.ws, paused, "user", "测试", self.ctx()["tz"])
            self.assertEqual(self.run_cmd("replies", "--state", "ready")[1]["replies"][0]["messageId"], mid)
        self.run_cmd("reply-state", "--message", mid, "--state", "consumed", "--decision", D1)
        self.assertEqual(self.run_cmd("replies", "--state", "ready")[1]["replies"], [])

    def test_ready_global_pause_then_ledger_done_finishes_without_resend(self):
        self.inbox((D1, "待决定"))
        self.decide()
        mid = self.im.user_says("01 接受")
        self.sync()
        self.run_cmd("reply-state", "--message", mid, "--state", "ready", "--decision", D1)
        ledger.set_pause(self.ws, True, "user", "pause", self.ctx()["tz"])
        self.assertEqual(self.run_cmd("status")[1]["readyReplies"][0]["messageId"], mid)
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
        self.assertIn("暂停、恢复", self.im.sent[-1]["text"])
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
        self.im.user_says("收到", parent=test["messageId"])
        _, out = self.sync()
        self.assertEqual([r["state"] for r in out["new"]],
                         ["received"] * len(cases) + ["command"] * 2 + ["test"])
        self.assertEqual([r["messageId"] for r in out["replies"]], mids)
        self.assertEqual(out["commands"], ["暂停", "恢复"])
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
        self.assertIn("暂停、恢复", self.im.sent[-1]["text"])

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

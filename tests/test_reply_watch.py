"""回复窗口真实通知投影与监听进程行为；只用合成工作区。"""
import json
import os
import unittest
from datetime import datetime
from unittest.mock import patch

import test_notify

N = test_notify.notify
D1, D2 = test_notify.D1, test_notify.D2


class ReplyWatchTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, JOB_MATE_TESTING="1", JOB_MATE_NOW="2026-11-10T10:00:00+08:00")
        self.env.start()
        self.addCleanup(self.env.stop)
        self.f = test_notify.NotifyTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.f.im.clock = int(datetime.fromisoformat("2026-11-10T09:59:59+08:00").timestamp() * 1000)
        self.f.inbox((D1, "待决定"), (D2, "待处理"))

    def clock(self, value):
        os.environ["JOB_MATE_NOW"] = "2026-11-10T" + value + "+08:00"

    def status(self):
        code, result = self.f.run_cmd("status")
        self.assertEqual(code, 0, result)
        return result["replyWatch"]

    def queue(self, decision=D1, kind="decision", key=None):
        args = ["send", "--kind", kind, "--decision", decision, "--text", "synthetic"]
        if key:
            args += ["--key", key]
        code, out = self.f.run_cmd(*args)
        self.assertEqual(code, 0, out)
        return out

    def test_w01_disabled_keeps_local_items(self):
        self.f.set_im(channel="none")
        self.queue()
        state = self.status()
        self.assertFalse(state["active"])
        self.assertFalse(state["expired"])
        self.assertEqual(len(N.open_items(self.f.ws)), 2)

    def test_w02_first_notification_sets_deadline(self):
        self.queue()
        state = self.status()
        self.assertEqual(state["lastNewAt"], "2026-11-10T10:00:00+08:00")
        self.assertEqual(state["deadlineAt"], "2026-11-10T11:00:00+08:00")
        self.assertEqual([r["decision"] for r in state["waiting"]], [D1])
        self.assertTrue(state["active"])

    def test_w03_answered_newer_notification_keeps_extended_window(self):
        self.queue()
        self.clock("10:30:00")
        self.queue(D2, "todo")
        self.f.im.drain()
        self.clock("10:31:00")
        self.f.im.clock = N.now_ms() - 1000
        out = N.load_state(self.f.ws)["out"]["todo:" + D2]
        self.f.im.user_says("done", parent=out["messageId"])
        self.assertEqual(self.f.incoming(self.f.im.messages)[0], 0)
        state = self.status()
        self.assertEqual([r["decision"] for r in state["waiting"]], [D1])
        self.assertEqual(state["deadlineAt"], "2026-11-10T11:30:00+08:00")

    def test_w04_resend_and_alternative_kind_do_not_extend(self):
        self.queue()
        self.clock("10:40:00")
        self.queue()
        self.queue(D1, "todo", "another-key")
        rows = N.read_records(self.f.ws)
        self.assertEqual(len([r for r in rows if r["type"] == "out"]), 2)
        state = self.status()
        self.assertEqual(len(state["waiting"]), 1)
        self.assertEqual(state["deadlineAt"], "2026-11-10T11:00:00+08:00")

    def test_w05_pure_notifications_do_not_extend(self):
        self.queue()
        self.clock("10:40:00")
        self.f.inbox((D1, "待决定"), (D2, "已处理"))
        for kind in ("done", "summary", "alert"):
            self.assertEqual(self.f.queue("pure:" + kind, "notice", kind, "--decision", D2, "--run", "r")[0], 0)
        self.f.queue_test()
        self.f.append_im(type="out", key="ack:test", route=self.f.ctx()["cfg"]["route"],
                         status="queued", kind="ack", text="ack")
        self.assertEqual(self.status()["deadlineAt"], "2026-11-10T11:00:00+08:00")

    def test_w08_boundary_and_w09_new_question(self):
        self.queue()
        self.clock("10:30:00")
        self.queue(D2, "todo")
        self.clock("11:29:59")
        self.assertTrue(self.status()["active"])
        self.clock("11:30:00")
        self.assertTrue(self.status()["expired"])
        before = (self.f.ws / "inbox/decisions.md").read_bytes()
        self.assertEqual((self.f.ws / "inbox/decisions.md").read_bytes(), before)
        self.clock("12:00:00")
        self.f.inbox((D1, "待决定"), (D2, "待处理"), ("D-20261110-03", "待决定"))
        self.queue("D-20261110-03")
        self.assertEqual(self.status()["deadlineAt"], "2026-11-10T13:00:00+08:00")

    def test_w10_zero_window_is_disabled(self):
        self.queue()
        self.f.set_im(waitMinutes=0)
        self.assertFalse(self.status()["active"])
        self.assertFalse(self.status()["expired"])

    def test_w11_new_route_does_not_borrow_old_window(self):
        self.queue()
        self.f.set_im(ownerId="other-synthetic")
        state = self.status()
        self.assertEqual(state["waiting"], [])
        self.assertIsNone(state["deadlineAt"])

    def test_w12_status_is_readonly_and_exact_six_fields(self):
        self.queue()
        before = {str(p): p.read_bytes() for p in self.f.ws.rglob("*") if p.is_file()}
        state = self.status()
        self.assertEqual(set(state), {"waiting", "lastNewAt", "deadlineAt", "active", "expired", "undeliverable"})
        after = {str(p): p.read_bytes() for p in self.f.ws.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse((self.f.ws / "ledger/lock.json").exists())
        self.assertFalse((self.f.ws / "ledger/.im-watch.lock").exists())

    def test_w23_result_check_done_is_not_pure_notice(self):
        self.f.inbox((D1, "已决定"))
        self.f.event(decision=D1)
        self.queue()
        self.clock("10:30:00")
        self.assertEqual(self.f.send_done("new result")[0], 0)
        state = self.status()
        self.assertEqual(len(state["waiting"]), 2)
        self.assertEqual(state["deadlineAt"], "2026-11-10T11:30:00+08:00")
        self.clock("10:45:00")
        self.f.send_done("new result")
        self.assertEqual(self.status()["deadlineAt"], state["deadlineAt"])

    def test_w24_unrelated_reply_does_not_answer_clarify(self):
        self.f.establish()
        mid = self.f.im.user_says("ambiguous")
        self.assertEqual(self.f.incoming(self.f.im.messages)[0], 0)
        self.assertEqual(self.f.queue("clarify:one", "question", "clarify", "--reply-to", mid)[0], 0)
        self.f.im.drain()
        self.clock("10:01:00")
        self.f.im.clock = N.now_ms() - 1000
        self.f.im.user_says("unrelated")
        self.assertEqual(self.f.incoming(self.f.im.messages)[0], 0)
        self.assertEqual(len(self.status()["waiting"]), 1)

    def test_w25_unknown_waits_and_exhaustion_is_undeliverable(self):
        self.queue()
        entry = self.f.claim()[0]
        self.assertEqual(self.f.receipt(entry, result="unknown")[0], 0)
        state = self.status()
        self.assertEqual(state["waiting"][0]["deliveryStatus"], "unknown")
        self.assertEqual(self.f.claim(), [])
        self.assertFalse(self.f.run_cmd("poll")[1]["readable"])
        self.f.append_im(type="out", key=entry["key"], status="blocked", error="synthetic")
        state = self.status()
        self.assertEqual(state["waiting"], [])
        self.assertEqual(len(state["undeliverable"]), 1)


if __name__ == "__main__":
    unittest.main()

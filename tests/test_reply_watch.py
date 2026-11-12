"""回复窗口真实通知投影与监听进程行为；只用合成工作区。"""
import contextlib
import io
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
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
        question = next(o["messageId"] for o in N.load_state(self.f.ws)["out"].values() if o.get("kind") == "clarify")
        ignored = self.f.im.user_says("ignored reply", parent=question)
        self.assertEqual(self.f.incoming(self.f.im.messages)[0], 0)
        self.assertEqual(self.f.run_cmd("reply-state", "--message", ignored, "--state", "ignored")[0], 0)
        self.assertEqual(len(self.status()["waiting"]), 1)
        self.f.im.user_says("暂停", parent=question)
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
        self.queue(D2, "todo")
        for _ in range(3):
            entry = self.f.claim()[0]
            self.assertEqual(self.f.receipt(entry, result="failed", error="synthetic rejected")[0], 0)
        state = self.status()
        self.assertEqual(state["waiting"], [])
        self.assertEqual(len(state["undeliverable"]), 2)
        self.assertEqual(next(r for r in state["undeliverable"] if r["decision"] == D2)["reason"], "synthetic rejected")
        self.assertEqual(N.load_state(self.f.ws)["out"]["todo:" + D2]["attempts"], 3)
        self.assertEqual(self.f.claim(), [])


class WatchProcessTest(unittest.TestCase):
    def setUp(self):
        import test_im_lark
        self.module = test_im_lark
        self.f = test_im_lark.ImLarkTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.env = dict(os.environ)
        self.children, self.detached = [], []
        self.addCleanup(self.stop_children)
        self.decision = "D-20400102-01"
        self.f.invoke(N, "send", "--kind", "decision", "--decision", self.decision, "--text", "synthetic secret body")

    def stop_children(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
            child.communicate(timeout=10)
        if self.detached:
            self.f.configure(waitMinutes=0)
            end = time.monotonic() + 3
            while self.module.im_lark.watch_probe(self.f.ws)[0] and time.monotonic() < end:
                time.sleep(.01)
            if self.module.im_lark.watch_probe(self.f.ws)[0]:
                for pid in self.detached:
                    if os.name == "nt":
                        import ctypes
                        handle = ctypes.windll.kernel32.OpenProcess(1, False, pid)
                        if handle:
                            ctypes.windll.kernel32.TerminateProcess(handle, 1)
                            ctypes.windll.kernel32.CloseHandle(handle)
                    else:
                        import signal
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass

    def argv(self, *args, script=None):
        return [sys.executable, "-B", "-I", str(script or self.module.ROOT / "scripts/im_lark.py"),
                "--workspace", str(self.f.ws), "watch", *args]

    def control(self, *args, env=None, expected=0):
        result = subprocess.run(self.argv(*args), capture_output=True, encoding="utf-8", env=env or self.env, timeout=10)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertFalse(result.stderr, result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        return rows

    def start(self, *args, script=None):
        child = subprocess.Popen(self.argv("--interval", "1", *args, script=script),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", env=self.env)
        self.children.append(child)
        return child

    def wait_for(self, predicate, child=None, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            if child is not None and child.poll() is not None:
                self.fail(str(child.communicate()))
            time.sleep(.01)
        self.fail("watch condition timeout")

    def log(self):
        path = self.f.ws / "ledger/im-watch.log"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_w13_watch_does_not_own_or_renew_execution_lock(self):
        self.f.invoke(self.module.ledger, "lock", "acquire", "--skill", "followup", "--run", "foreground")
        lock = self.f.ws / "ledger/lock.json"
        before = lock.read_bytes()
        rows = self.control("--max-ticks", "1")
        self.assertIn("watch_started", [r.get("event") for r in rows])
        self.assertEqual(lock.read_bytes(), before)
        child = self.start()
        self.wait_for(lambda: self.module.im_lark.watch_probe(self.f.ws)[0] is True, child)
        release = self.f.invoke(self.module.ledger, "lock", "release", "--run", "foreground")
        runtime_events = [{"event": "released", "result": release}, {"event": "reported", "summary": "synthetic round summary"}]
        self.assertEqual([r["event"] for r in runtime_events], ["released", "reported"])
        self.assertTrue(release["released"])
        self.assertFalse(lock.exists())
        self.assertIsNone(child.poll())
        self.assertIs(self.control("--status")[-1]["running"], True)
        self.f.configure(waitMinutes=0)
        stdout, stderr = child.communicate(timeout=4)
        self.assertEqual(child.returncode, 0, stdout + stderr)
        self.f.configure()
        self.f.invoke(self.module.ledger, "lock", "acquire", "--skill", "setup", "--run", "recovery", "--recovery")
        self.control("--max-ticks", "1", expected=2)

    def test_w14_competing_parents_and_installation_copies(self):
        import test_build
        output = self.f.base / "packages"
        self.assertEqual(test_build.run_build("--out", str(output))[0], 0)
        parents = [subprocess.Popen(self.argv("--detach", "--interval", "1", script=path),
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", env=self.env)
                   for path in output.glob("job-mate-*/scripts/im_lark.py")]
        results = []
        for parent in parents:
            stdout, stderr = parent.communicate(timeout=10)
            self.assertEqual(parent.returncode, 0, stdout + stderr)
            result = json.loads(stdout); results.append(result)
            if result["started"]:
                self.detached.append(result["pid"])
        self.assertEqual(sum(r["started"] for r in results), 1)
        self.assertEqual(sum(r["alreadyRunning"] for r in results), 2)
        self.wait_for(lambda: len([r for r in self.log() if r["event"] == "watch_started"]) == 1)
        self.assertIs(self.control("--status")[-1]["running"], True)
        self.assertTrue((self.f.ws / "ledger/.im-watch.lock").exists())
        self.assertEqual(list((self.f.ws / "ledger").glob(".im-watch-start-*")), [])

    def test_w15_unknown_lock_never_spawns(self):
        I = self.module.im_lark
        args = I.build_parser().parse_args(["watch", "--detach"])
        with patch.object(I, "watch_probe", return_value=(None, "watch_lock_unknown")), patch.object(I, "spawn_watch", side_effect=AssertionError):
            result, code = I.detach_watch(self.f.ws, args)
        self.assertEqual((code, result["started"], result["reason"]), (1, False, "watch_lock_unknown"))
        with patch.object(I.fsutil, "try_locked", side_effect=PermissionError):
            self.assertEqual(I.watch_probe(self.f.ws), (None, "watch_lock_unknown"))

    def test_w18_detached_survives_real_parent_exit_and_fetches(self):
        self.f.sync("--send-only")
        outgoing = self.f.rows()["out"]["decision:" + self.decision]["messageId"]
        notified = N.now_ms()
        self.env["JOB_MATE_NOW"] = datetime.fromtimestamp((notified + 10000) / 1000, datetime.fromisoformat(os.environ["JOB_MATE_NOW"]).tzinfo).isoformat()
        os.environ["JOB_MATE_NOW"] = self.env["JOB_MATE_NOW"]
        result = self.control("--detach", "--interval", "1")[-1]
        self.assertTrue(result["started"])
        self.detached.append(result["pid"])
        # control 的真实父进程已经退出；随后才给假CLI增加本人消息。
        item = self.f.message(text="synthetic secret reply", created=notified + 9500)
        item["parent_id"] = outgoing
        with I_lock(self.f.state):
            self.f.add_messages(item)
        self.wait_for(lambda: bool(self.f.rows()["in"]))
        self.wait_for(lambda: not self.module.im_lark.watch_probe(self.f.ws)[0])
        events = self.log()
        self.assertLess(next(i for i,r in enumerate(events) if r["event"] == "reply_available"),
                        next(i for i,r in enumerate(events) if r["event"] == "watch_exit"))
        self.assertTrue(all(r["state"] == "received" for r in self.f.rows()["in"].values()))
        self.assertNotIn("synthetic secret", json.dumps(events))

    def test_w06_w07_one_reply_leaves_other_item_listening(self):
        other = "D-20400102-02"
        (self.f.ws / "inbox/decisions.md").write_text(
            f"## {self.decision} 待决定\n\n## {other} 待处理\n", encoding="utf-8")
        self.f.invoke(N, "send", "--kind", "todo", "--decision", other, "--text", "synthetic")
        self.f.sync("--send-only")
        notified = N.now_ms()
        self.env["JOB_MATE_NOW"] = datetime.fromtimestamp((notified + 10000) / 1000,
            datetime.fromisoformat(os.environ["JOB_MATE_NOW"]).tzinfo).isoformat()
        os.environ["JOB_MATE_NOW"] = self.env["JOB_MATE_NOW"]
        before = {name: (self.f.ws / name).read_bytes() if (self.f.ws / name).exists() else None
                  for name in ("inbox/decisions.md", "ledger/events.jsonl", "ledger/lock.json")}
        child = self.start()
        self.wait_for(lambda: bool(self.log()), child)
        first = self.f.message(n=20, created=notified + 9500)
        first["parent_id"] = self.f.rows()["out"]["decision:" + self.decision]["messageId"]
        with I_lock(self.f.state):
            self.f.add_messages(first)
        self.wait_for(lambda: len(self.f.rows()["in"]) == 1, child)
        # in 已可见时，后台仍可能在追加 cursor/ack/checkpoint；只在稳定快照上作断言。
        with self.module.ledger.mutation_mutex(self.f.ws):
            ctx = N.context(self.f.ws)
            state = N.reply_watch(self.f.ws, ctx, self.module.im_lark.fsutil.now(ctx["tz"]))
        self.assertEqual([r["decision"] for r in state["waiting"]], [other])
        self.assertIsNone(child.poll())
        second = self.f.message(n=21, created=notified + 9501)
        second["parent_id"] = self.f.rows()["out"]["todo:" + other]["messageId"]
        with I_lock(self.f.state):
            self.f.add_messages(second)
        stdout, stderr = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0, stdout + stderr)
        self.assertEqual(len(self.f.rows()["in"]), 2)
        self.assertTrue(all(r["state"] == "received" for r in self.f.rows()["in"].values()))
        for name, data in before.items():
            self.assertEqual((self.f.ws / name).read_bytes() if (self.f.ws / name).exists() else None, data)
        events = self.log()
        self.assertEqual(events[-1]["reason"], "cleared")
        self.assertEqual(len([r for r in events if r["event"] == "reply_available"]), 2)

    def test_w16_w21_watch_network_barrier_allows_execution_and_busy_sync(self):
        import test_im_concurrency
        case = test_im_concurrency.ImConcurrencyTest(); case.setUp()
        try:
            case.f.establish()
            case.f.invoke(N, "send", "--kind", "decision", "--decision", self.decision, "--text", "synthetic")
            case.f.configure(larkCommand=[sys.executable, str(case.wrapper)])
            child = subprocess.Popen(case.command("im_lark", "watch", "--max-ticks", "1"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
                env=dict(case.env, IM_BARRIER_KIND="fetch"))
            case.children.append(child)
            self.wait_for(lambda: case.barrier.with_suffix(".ready").exists(), child)
            before = (case.f.ws / "ledger/im.jsonl").read_bytes()
            code, result = case.call("im_lark", "sync", "--fetch-only")
            self.assertEqual((code, result["busy"], result["incomplete"]), (0, True, True))
            self.assertEqual((case.f.ws / "ledger/im.jsonl").read_bytes(), before)
            for args in (("lock", "acquire", "--skill", "followup", "--run", "parallel"),
                         ("lock", "renew", "--run", "parallel"), ("lock", "release", "--run", "parallel")):
                self.assertEqual(case.call("ledger", *args)[0], 0)
                self.assertFalse(case.barrier.with_suffix(".release").exists())
            case.release()
            stdout, stderr = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, stdout + stderr)
        finally:
            case.doCleanups()

    def test_w17_os_lock_released_after_crash_keeps_deadline(self):
        ctx = N.context(self.f.ws)
        before = N.reply_watch(self.f.ws, ctx, self.module.im_lark.fsutil.now(ctx["tz"]))
        records = N.read_records(self.f.ws)
        child = self.start()
        self.wait_for(lambda: bool(self.log()), child)
        child.kill(); child.communicate(timeout=5)
        self.assertIs(self.control("--status")[-1]["running"], False)
        out = self.control("--detach", "--interval", "1")[-1]
        self.assertTrue(out["started"]); self.detached.append(out["pid"])
        after = N.reply_watch(self.f.ws, ctx, self.module.im_lark.fsutil.now(ctx["tz"]))
        self.assertEqual(after["deadlineAt"], before["deadlineAt"])
        self.assertEqual(N.read_records(self.f.ws), records)

    def test_w19_failed_spawn_and_late_child_are_degraded(self):
        I = self.module.im_lark
        args = I.build_parser().parse_args(["watch", "--detach"])
        with patch.object(I.subprocess, "Popen", side_effect=PermissionError):
            result, code = I.detach_watch(self.f.ws, args)
        self.assertEqual((code, result["reason"], result["started"]), (1, "watch_spawn_failed", False))
        env = dict(self.env, JOB_MATE_WATCH_START_DELAY="6")
        result = self.control("--detach", "--interval", "1", env=env, expected=1)[-1]
        self.assertFalse(result["started"])
        self.assertIn(result["reason"], ("watch_start_timeout", "watch_start_unknown"))
        self.assertIs(self.control("--status")[-1]["running"], False)
        for token in ("../escape", "0" * 32):
            self.control("--child-token", token, "--child-deadline", str(time.monotonic() + 2), expected=2)
        self.assertEqual(list((self.f.ws / "ledger").glob(".im-watch-start-*")), [])
        for flags in (("--interval", "0"), ("--interval", "61"), ("--max-ticks", "0")):
            self.control(*flags, expected=2)
        production = dict(self.env); production.pop("JOB_MATE_TESTING", None)
        self.control("--max-ticks", "1", env=production, expected=2)
        # W26：后台不可用/窗口过期不丢消息，下一轮实际 sync 仍能读取迟到答复。
        self.f.sync("--send-only")
        sent = self.f.rows()["out"]["decision:" + self.decision]["messageId"]
        late = N.now_ms() + 7200000
        self.env["JOB_MATE_NOW"] = datetime.fromtimestamp(late / 1000,
            datetime.fromisoformat(os.environ["JOB_MATE_NOW"]).tzinfo).isoformat()
        os.environ["JOB_MATE_NOW"] = self.env["JOB_MATE_NOW"]
        inactive = self.control("--detach")[-1]
        self.assertEqual((inactive["started"], inactive["reason"]), (False, "inactive"))
        item = self.f.message(created=late - 500); item["parent_id"] = sent
        self.f.add_messages(item)
        result = self.f.sync("--fetch-only")
        self.assertEqual(len(result["incoming"]["new"]), 1)
        self.assertEqual(next(iter(self.f.rows()["in"].values()))["state"], "received")
        self.assertEqual(N.open_items(self.f.ws), [self.decision])

    def test_w20_wall_clock_expiry_does_not_sleep_an_extra_tick(self):
        I = self.module.im_lark
        args = I.build_parser().parse_args(["watch", "--interval", "60"])
        deadline = N.reply_watch(self.f.ws, N.context(self.f.ws),
                                I.fsutil.now(N.context(self.f.ws)["tz"]))["deadlineAt"]
        sleeps = []
        def advance(seconds):
            sleeps.append(seconds)
            os.environ["JOB_MATE_NOW"] = deadline
        with patch.object(I.time, "sleep", side_effect=advance), contextlib.redirect_stdout(io.StringIO()):
            result, code = I.watch_loop(self.f.ws, args)
        self.assertEqual((code, result["reason"]), (11, "expired"))
        self.assertEqual(sleeps, [60])
        self.assertEqual(N.open_items(self.f.ws), [self.decision])
        # 退出竞态：第一次无锁快照过期后插入真实新通知，最终临界区必须复查并继续。
        case = WatchProcessTest(); case.setUp()
        try:
            other = "D-20400102-02"
            (case.f.ws / "inbox/decisions.md").write_text(f"## {case.decision} 待决定\n\n## {other} 待处理\n", encoding="utf-8")
            I = case.module.im_lark
            original_watch, original_lock = N.reply_watch, I.fsutil.try_locked
            ctx = N.context(case.f.ws)
            first_deadline = original_watch(case.f.ws, ctx, I.fsutil.now(ctx["tz"]))["deadlineAt"]
            injected, unlocked, sleeps = [], [], []
            def observed(ws, context, now):
                state = original_watch(ws, context, now)
                if state["expired"] and not I.ledger.mutation_held(ws) and not injected:
                    case.f.invoke(N, "send", "--kind", "todo", "--decision", other, "--text", "new independent question")
                    injected.append(True)
                return state
            @contextlib.contextmanager
            def locked(path, create=True):
                with original_lock(path, create=create) as acquired:
                    try:
                        yield acquired
                    finally:
                        if acquired and Path(path).name == ".im-watch.lock":
                            unlocked.append(I.ledger.mutation_held(case.f.ws))
            def tick(seconds):
                sleeps.append(seconds)
                if len(sleeps) == 1:
                    os.environ["JOB_MATE_NOW"] = first_deadline
                else:
                    case.f.configure(waitMinutes=0)
            args = I.build_parser().parse_args(["watch", "--interval", "60"])
            with patch.object(N, "reply_watch", side_effect=observed), patch.object(I.fsutil, "try_locked", side_effect=locked), \
                    patch.object(I.time, "sleep", side_effect=tick), contextlib.redirect_stdout(io.StringIO()):
                result, code = I.watch_loop(case.f.ws, args)
            self.assertEqual((code, result["reason"]), (0, "disabled"))
            self.assertEqual(injected, [True]); self.assertEqual(unlocked, [True])
            self.assertEqual(len(sleeps), 2)
            self.assertEqual(N.open_items(case.f.ws), [case.decision, other])
        finally:
            case.doCleanups()

    def test_w10_zero_exits_existing_foreground_without_consuming(self):
        child = self.start()
        self.wait_for(lambda: bool(self.log()), child)
        self.f.configure(waitMinutes=0)
        stdout, stderr = child.communicate(timeout=4)
        self.assertEqual(child.returncode, 0, stdout + stderr)
        self.assertEqual(N.open_items(self.f.ws), [self.decision])

    def test_w30_six_transient_failures_do_not_abandon_window(self):
        self.f.sync("--send-only")
        for _ in range(6):
            self.f.fault("native-list", "nonzero")
        rows = self.control("--interval", "1", "--max-ticks", "6")
        self.assertEqual(len([r for r in rows if r.get("event") == "watch_tick"]), 6)
        self.assertEqual(rows[-1]["reason"], "test_tick_limit")
        self.assertEqual(N.open_items(self.f.ws), [self.decision])

    def test_w30_logs_rotate_and_are_not_business_truth(self):
        log = self.f.ws / "ledger/im-watch.log"
        line = '{"event":"synthetic"}\n'
        log.write_text(line * ((1024 * 1024) // len(line)), encoding="utf-8")
        self.control("--max-ticks", "1")
        self.assertLessEqual(log.stat().st_size, 1024 * 1024)
        self.assertLessEqual(log.with_name(log.name + ".1").stat().st_size, 1024 * 1024)
        ctx = N.context(self.f.ws)
        before = N.reply_watch(self.f.ws, ctx, self.module.im_lark.fsutil.now(ctx["tz"]))
        log.unlink(); log.with_name(log.name + ".1").unlink()
        self.assertEqual(N.reply_watch(self.f.ws, ctx, self.module.im_lark.fsutil.now(ctx["tz"])), before)


def I_lock(path):
    import fsutil
    return fsutil.locked(path.with_name(path.name + ".lock"))


if __name__ == "__main__":
    unittest.main()

"""监听启动时钟与并发快照；真实本地锁、合成工作区，不连接外部服务。"""
import contextlib
import io
import json
import os
import queue
import threading
import unittest
from unittest.mock import patch

import test_reply_watch


class WatchPortabilityTest(unittest.TestCase):
    def setUp(self):
        self.case = test_reply_watch.WatchProcessTest()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.I = self.case.module.im_lark
        self.N = test_reply_watch.N
        self.ws = self.case.f.ws

    def test_darwin_parent_and_child_use_one_system_clock(self):
        I = self.I
        deadlines, child_results = [], []
        class Child:
            pid = os.getpid()
            def poll(self):
                return 0
        def spawn(ws, interval, token, deadline, max_ticks):
            deadlines.append(deadline)
            args = I.build_parser().parse_args(["watch", "--max-ticks", "1", "--child-token", token,
                                                "--child-deadline", str(deadline)])
            # macOS 3.9 的父子进程有不同 monotonic 原点，系统 CLOCK_MONOTONIC 一致。
            with patch.dict(os.environ, JOB_MATE_WATCH_CHILD_TOKEN=token), \
                    patch.object(I.time, "monotonic", return_value=.1):
                child_results.append(I.watch_loop(ws, args))
            return Child()
        args = I.build_parser().parse_args(["watch", "--detach", "--max-ticks", "1"])
        with patch.object(I.sys, "platform", "darwin"), \
                patch.object(I.time, "monotonic", return_value=10.0), \
                patch.object(I.time, "CLOCK_MONOTONIC", 42, create=True), \
                patch.object(I.time, "clock_gettime", return_value=100.0, create=True), \
                patch.object(I, "spawn_watch", side_effect=spawn), contextlib.redirect_stdout(io.StringIO()):
            result, code = I.detach_watch(self.ws, args)
        self.assertEqual((code, result["started"], result["reason"]), (0, True, "started"))
        self.assertEqual(deadlines, [105.0])
        self.assertEqual(child_results, [({"reason": "test_tick_limit"}, 0)])
        self.assertEqual(list((self.ws / "ledger").glob(".im-watch-start-*")), [])

    def test_expired_child_cannot_start_with_a_fresh_process_clock(self):
        I = self.I
        token = "a" * 32
        args = I.build_parser().parse_args(["watch", "--child-token", token, "--child-deadline", "100"])
        with patch.object(I.sys, "platform", "darwin"), \
                patch.object(I.time, "monotonic", return_value=.1), \
                patch.object(I.time, "CLOCK_MONOTONIC", 42, create=True), \
                patch.object(I.time, "clock_gettime", return_value=101.0, create=True), \
                patch.dict(os.environ, JOB_MATE_WATCH_CHILD_TOKEN=token):
            result, code = I.watch_loop(self.ws, args)
        self.assertEqual((code, result["reason"]), (1, "watch_start_expired"))
        self.assertFalse((self.ws / "ledger/.im-watch.lock").exists())
        self.assertFalse((self.ws / "ledger/im-watch.log").exists())
        self.assertEqual(list((self.ws / "ledger").glob(".im-watch-start-*")), [])

    def test_missing_darwin_system_clock_is_a_fixed_error(self):
        I = self.I
        output = io.StringIO()
        with patch.object(I.sys, "platform", "darwin"), \
                patch.object(I.time, "clock_gettime", None, create=True), \
                patch.object(I, "spawn_watch", side_effect=AssertionError("must not spawn without a shared clock")), \
                contextlib.redirect_stdout(output):
            code = I.main(["--workspace", str(self.ws), "watch", "--detach"])
        self.assertEqual((code, json.loads(output.getvalue())), (2, {"error": "watch_clock_unavailable"}))

    def test_atomic_config_update_waits_for_a_stable_local_snapshot(self):
        I, N = self.I, self.N
        original_event, original_sync = I.watch_event, N.sync_view
        started, finished = threading.Event(), threading.Event()
        attempts = queue.Queue()
        errors, acquired_during_read = [], []
        armed = [False]
        def write_profile():
            path = self.ws / "profile.json"
            profile = json.loads(path.read_text(encoding="utf-8"))
            profile["im"]["waitMinutes"] = 30
            I.fsutil.write_text_atomic(path, json.dumps(profile))
        def writer():
            try:
                if not started.wait(5):
                    raise AssertionError("watch never reached the snapshot barrier")
                # 非阻塞尝试给出确定交错；新实现持读快照锁时，写者随后等待同一协调锁。
                with I.fsutil.try_locked(self.ws / "ledger/.mutation.mutex", create=False) as acquired:
                    if acquired:
                        write_profile()
                    attempts.put(acquired)
                if not acquired:
                    with I.ledger.mutation_mutex(self.ws):
                        write_profile()
            except BaseException as error:
                errors.append(error)
                attempts.put(error)
            finally:
                finished.set()
        def event(*args, **kwargs):
            original_event(*args, **kwargs)
            if args[3] == "watch_started":
                armed[0] = True
        def sync_view(ws, cfg):
            if armed[0]:
                armed[0] = False
                started.set()
                acquired_during_read.append(attempts.get(timeout=5))
            return original_sync(ws, cfg)
        def network(*args, **kwargs):
            self.assertFalse(I.ledger.mutation_held(self.ws), "network call holds mutation lock")
            self.assertTrue(finished.wait(5), "configuration writer is still blocked during network I/O")
            return {"busy": True}
        thread = threading.Thread(target=writer)
        thread.start()
        try:
            args = I.build_parser().parse_args(["watch", "--max-ticks", "1"])
            with patch.object(I, "watch_event", side_effect=event), \
                    patch.object(N, "sync_view", side_effect=sync_view), \
                    patch.object(N, "poll", return_value={"readable": True}), \
                    patch.object(I, "sync", side_effect=network), contextlib.redirect_stdout(io.StringIO()):
                result, code = I.watch_loop(self.ws, args)
        finally:
            started.set()
            thread.join(timeout=6)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual((code, result["reason"]), (0, "test_tick_limit"))
        self.assertEqual(acquired_during_read, [False])
        self.assertTrue(finished.is_set())
        ctx, state = I.watch_state(self.ws)
        self.assertEqual(ctx["cfg"]["waitMinutes"], 30)
        self.assertTrue(state["active"])

    def test_corrupt_configuration_still_stops_the_watcher(self):
        I = self.I
        original_event = I.watch_event
        def corrupt_after_start(*args, **kwargs):
            original_event(*args, **kwargs)
            if args[3] == "watch_started":
                with I.ledger.mutation_mutex(self.ws):
                    I.fsutil.write_text_atomic(self.ws / "profile.json", "[]")
        args = I.build_parser().parse_args(["watch", "--max-ticks", "1"])
        with patch.object(I, "watch_event", side_effect=corrupt_after_start), \
                patch.object(I, "sync", side_effect=AssertionError("must not fetch with corrupt configuration")), \
                contextlib.redirect_stdout(io.StringIO()):
            result, code = I.watch_loop(self.ws, args)
        self.assertEqual((code, result["reason"]), (2, "watch_local_state_invalid"))
        self.assertFalse(I.watch_probe(self.ws)[0])


if __name__ == "__main__":
    unittest.main()

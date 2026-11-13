"""真实 CLI / 函数入口、OS 锁与假 lark 子进程的屏障测试。"""
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

import test_im_lark

ROOT = test_im_lark.ROOT
N = test_im_lark.notify
L = test_im_lark.ledger
I = test_im_lark.im_lark

# 屏障包在真实 run_lark 子进程边界外；响应仍由原假 lark CLI 生成。
BARRIER_SCRIPT = '''import os,sys,time,runpy
from pathlib import Path
args=sys.argv[1:]
kind="fetch" if "/open-apis/im/v1/messages" in args else ("send" if "+messages-send" in args or "+messages-reply" in args else "other")
active=kind==os.environ.get("IM_BARRIER_KIND")
base=Path(os.environ["IM_BARRIER_BASE"])
def barrier():
    base.with_suffix(".ready").write_text(str(os.getpid()),encoding="utf-8")
    end=time.monotonic()+15
    while not base.with_suffix(".release").exists():
        if time.monotonic()>end: raise SystemExit(79)
        time.sleep(.01)
if active and os.environ.get("IM_BARRIER_PHASE","before")=="before": barrier()
try:
    runpy.run_path(os.environ["IM_REAL_FAKE"],run_name="__main__")
except SystemExit as error:
    if active and os.environ.get("IM_BARRIER_PHASE")=="after": barrier()
    raise
'''


class ImConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.f = test_im_lark.ImLarkTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.wrapper = self.f.base / "barrier_lark.py"
        self.wrapper.write_text(BARRIER_SCRIPT, encoding="utf-8")
        self.barrier = self.f.base / "barrier"
        self.env = dict(os.environ, IM_BARRIER_BASE=str(self.barrier), IM_REAL_FAKE=str(test_im_lark.FAKE))
        self.children = []
        self.addCleanup(self.cleanup_children)

    def cleanup_children(self):
        self.release()
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)

    def release(self):
        self.barrier.with_suffix(".release").touch()

    def command(self, module, *args):
        return [sys.executable, "-B", "-I", str(ROOT / f"scripts/{module}.py"), "--workspace", str(self.f.ws), *args]

    def call(self, module, *args, env=None):
        result = subprocess.run(self.command(module, *args), capture_output=True, encoding="utf-8", env=env or self.env, timeout=10)
        self.assertFalse(result.stderr, result.stderr)
        return result.returncode, json.loads(result.stdout)

    def start(self, mode, function=False, phase="before"):
        self.f.configure(larkCommand=[sys.executable, str(self.wrapper)])
        env = dict(self.env, IM_BARRIER_KIND="fetch" if mode == "--fetch-only" else "send", IM_BARRIER_PHASE=phase)
        if function:
            program = "import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import im_lark;print(json.dumps(im_lark.sync(Path(sys.argv[2]),send_only=sys.argv[3]=='--send-only',fetch_only=sys.argv[3]=='--fetch-only')))"
            args = [sys.executable, "-B", "-I", "-c", program, str(ROOT / "scripts"), str(self.f.ws), mode]
        else:
            args = self.command("im_lark", "sync", mode)
        child = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", env=env)
        self.children.append(child)
        end = time.monotonic() + 10
        while not self.barrier.with_suffix(".ready").exists():
            if child.poll() is not None:
                self.fail(str(child.communicate()))
            if time.monotonic() >= end:
                self.fail("network barrier not reached")
            time.sleep(.01)
        return child

    def finish(self, child):
        self.release()
        stdout, stderr = child.communicate(timeout=10)
        self.assertFalse(stderr, stderr)
        return child.returncode, json.loads(stdout)

    def reset_barrier(self):
        for suffix in (".ready", ".release"):
            self.barrier.with_suffix(suffix).unlink(missing_ok=True)

    def test_s01_cli_and_function_network_never_hold_mutation(self):
        for function in (False, True):
            for mode in ("--send-only", "--fetch-only"):
                with self.subTest(function=function, mode=mode):
                    self.reset_barrier()
                    (self.f.ws / "ledger/im.jsonl").unlink(missing_ok=True)
                    self.f.write_state(test_im_lark.fake.initial_state(seeded=False))
                    self.f.configure()
                    if mode == "--fetch-only":
                        self.f.establish()
                    else:
                        self.f.queue("send:" + str(function))
                    child = self.start(mode, function)
                    for args in (("lock", "acquire", "--skill", "followup", "--run", "parallel"),
                                 ("lock", "renew", "--run", "parallel"), ("lock", "release", "--run", "parallel")):
                        code, out = self.call("ledger", *args)
                        self.assertEqual(code, 0, out)
                        self.assertFalse(self.barrier.with_suffix(".release").exists())
                        self.assertIsNone(child.poll())
                    self.assertEqual(self.finish(child)[0], 0)

    def test_s02_two_syncs_busy_do_not_advance_or_send(self):
        self.f.establish()
        self.f.add_messages(self.f.message())
        child = self.start("--fetch-only")
        before = (self.f.ws / "ledger/im.jsonl").read_bytes()
        cursor = self.f.poll()["businessCursorMs"]
        calls = self.f.state_data()["calls"]
        code, busy = self.call("im_lark", "sync", "--fetch-only")
        self.assertEqual(code, 0, busy)
        self.assertEqual((busy["busy"], busy["incomplete"], busy["sent"], busy["incoming"]["new"]), (True, True, 0, []))
        self.assertEqual((self.f.ws / "ledger/im.jsonl").read_bytes(), before)
        self.assertEqual(self.f.poll()["businessCursorMs"], cursor)
        self.assertEqual(self.f.state_data()["calls"], calls)
        code, result = self.finish(child)
        self.assertEqual(code, 0, result)
        self.assertEqual(len(result["incoming"]["new"]), 1)
        self.assertEqual(len(self.f.rows()["in"]), 1)
        self.assertEqual(self.f.poll()["businessCursorMs"], int(self.f.message()["create_time"]))

    def test_f4_busy_sync_returns_before_foreign_mutation_is_released(self):
        self.f.establish()
        first = self.start("--fetch-only")  # 第一个真实进程持 im-sync，停在网络屏障。
        ready, release = self.f.base / "mutation.ready", self.f.base / "mutation.release"
        program = '''import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import ledger
with ledger.mutation_mutex(Path(sys.argv[2])):
    Path(sys.argv[3]).touch()
    end=time.monotonic()+10
    while not Path(sys.argv[4]).exists():
        if time.monotonic()>end: raise SystemExit(78)
        time.sleep(.01)
'''
        holder = subprocess.Popen([sys.executable, "-B", "-I", "-c", program, str(ROOT / "scripts"),
            str(self.f.ws), str(ready), str(release)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", env=self.env)
        self.children.append(holder)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists():
                self.assertIsNone(holder.poll(), "mutation holder exited before readiness")
                self.assertLess(time.monotonic(), deadline, "mutation holder did not become ready")
                time.sleep(.01)
            before = (self.f.ws / "ledger/im.jsonl").read_bytes()
            contender = subprocess.Popen(self.command("im_lark", "sync", "--fetch-only"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", env=self.env)
            self.children.append(contender)
            started = time.monotonic()
            try:
                stdout, stderr = contender.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                self.fail("busy sync waited for mutation beyond 2 seconds while both foreign locks remained held")
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual((contender.returncode, stderr), (0, ""), stdout)
            result = json.loads(stdout)
            self.assertEqual((result["busy"], result["incomplete"], result["sent"], result["incoming"]["new"]),
                             (True, True, 0, []))
            self.assertIsNone(first.poll()); self.assertIsNone(holder.poll())
            self.assertFalse(release.exists())
            self.assertEqual((self.f.ws / "ledger/im.jsonl").read_bytes(), before)
        finally:
            release.touch()
            holder.communicate(timeout=5)
            self.assertEqual(self.finish(first)[0], 0)

    def test_s04_inflight_fetch_rechecks_configuration_and_recovery_lock(self):
        for edit in ("route", "configuration", "profile", "version", "recovery"):
            with self.subTest(edit=edit):
                # 一个场景一个工作区，避免路由/版本故障污染下个场景。
                case = ImConcurrencyTest()
                case.setUp()
                try:
                    case.f.establish()
                    child = case.start("--fetch-only")
                    before = (case.f.ws / "ledger/im.jsonl").read_bytes()
                    if edit == "version":
                        marker = case.f.ws / "job-mate.json"
                        data = json.loads(marker.read_text()); data["suiteVersion"] = "99.0.0"
                        marker.write_text(json.dumps(data), encoding="utf-8")
                    elif edit == "recovery":
                        case.assertEqual(case.call("ledger", "lock", "acquire", "--skill", "setup", "--run", "recovery", "--recovery")[0], 0)
                    else:
                        profile = case.f.ws / "profile.json"
                        data = json.loads(profile.read_text())
                        if edit == "profile":
                            data["targetRoles"] = ["synthetic-new-role"]
                        else:
                            data["im"]["ownerId" if edit == "route" else "waitMinutes"] = "ou_" + "2" * 32 if edit == "route" else 10
                        profile.write_text(json.dumps(data), encoding="utf-8")
                    code, result = case.finish(child)
                    case.assertEqual(code, 2, result)
                    case.assertEqual((case.f.ws / "ledger/im.jsonl").read_bytes(), before)
                finally:
                    case.doCleanups()

    def test_s05_outbox_claim_cannot_reclaim_inflight_send(self):
        self.f.queue()
        child = self.start("--send-only")
        entry = self.f.rows()["out"]["queued"]
        self.assertEqual(entry["status"], "handoff")
        self.assertEqual(self.call("notify", "outbox", "--claim")[1]["entries"], [])
        code, busy = self.call("im_lark", "sync", "--send-only")
        self.assertEqual((code, busy["busy"], busy["incomplete"], busy["sent"]), (0, True, True, 0))
        self.assertEqual(self.finish(child)[0], 0)
        calls = [c for c in self.f.state_data()["calls"] if c["command"] == "send"]
        self.assertEqual(len(calls), 1)
        receipt = self.f.rows()["out"]["queued"]["receipt"]
        code, result = self.call("notify", "receipt", "--key", "queued", "--token", entry["token"], "--result", "sent",
                                 "--message-id", receipt["messageId"], "--chat-id", receipt["chatId"], "--create-time", str(receipt["createTime"]))
        self.assertEqual((code, result["status"]), (0, "duplicate"))
        self.assertEqual(self.call("notify", "receipt", "--key", "queued", "--token", "stale", "--result", "unknown")[0], 2)

    def test_s06_interrupted_handoff_is_never_resent(self):
        for phase in ("before", "after"):
            with self.subTest(phase=phase):
                case = ImConcurrencyTest(); case.setUp()
                try:
                    case.f.queue()
                    child = case.start("--send-only", phase=phase)
                    # 假CLI屏障子也是本测试所管理；只终止自己的PID。
                    remote_pid = int(case.barrier.with_suffix(".ready").read_text())
                    child.kill(); child.communicate(timeout=10)
                    if os.name == "nt":
                        import ctypes
                        handle = ctypes.windll.kernel32.OpenProcess(1, False, remote_pid)
                        if handle:
                            ctypes.windll.kernel32.TerminateProcess(handle, 1)
                            ctypes.windll.kernel32.CloseHandle(handle)
                    else:
                        import signal
                        os.kill(remote_pid, signal.SIGTERM)
                    case.release()
                    before = len([c for c in case.f.state_data()["calls"] if c["command"] == "send"])
                    code, result = case.call("im_lark", "sync", "--send-only")
                    case.assertEqual(code, 0, result)
                    case.assertEqual(result["sent"], 0)
                    case.assertEqual(len([c for c in case.f.state_data()["calls"] if c["command"] == "send"]), before)
                    case.assertEqual(case.f.rows()["out"]["queued"]["status"], "handoff")
                finally:
                    case.doCleanups()

    def test_s06_claim_remote_and_receipt_crash_points(self):
        for point, expected, sends in (("sync_after_claim", "handoff", 0),
                                       ("sync_after_remote", "handoff", 1), ("sync_after_receipt", "sent", 1)):
            with self.subTest(point=point):
                case = ImConcurrencyTest(); case.setUp()
                try:
                    case.f.queue()
                    process = subprocess.run(case.command("im_lark", "sync", "--send-only"),
                        capture_output=True, encoding="utf-8", timeout=10,
                        env=dict(case.env, JOB_MATE_TEST_CRASH_AT=point))
                    case.assertEqual(process.returncode, 70)
                    case.assertEqual(case.f.rows()["out"]["queued"]["status"], expected)
                    case.assertEqual(case.call("im_lark", "sync", "--send-only")[0], 0)
                    case.assertEqual(len([c for c in case.f.state_data()["calls"] if c["command"] == "send"]), sends)
                finally:
                    case.doCleanups()

    def test_s07_version_change_keeps_handoff_evidence(self):
        self.f.queue()
        child = self.start("--send-only", phase="after")
        marker = self.f.ws / "job-mate.json"
        data = json.loads(marker.read_text()); data["suiteVersion"] = "99.0.0"
        marker.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.finish(child)[0], 2)
        self.assertEqual(self.f.rows()["out"]["queued"]["status"], "handoff")
        self.assertEqual(len([c for c in self.f.state_data()["calls"] if c["command"] == "send"]), 1)
        data["suiteVersion"] = "1.0.0"; marker.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.call("im_lark", "sync", "--send-only")[0], 0)
        self.assertEqual(len([c for c in self.f.state_data()["calls"] if c["command"] == "send"]), 1)

    def test_s07_inflight_send_receipt_uses_frozen_route(self):
        self.f.queue()
        child = self.start("--send-only", phase="after")
        original = self.f.rows()["out"]["queued"]
        self.f.configure(ownerId="ou_" + "2" * 32)
        code, result = self.finish(child)
        self.assertEqual(code, 2, result)  # 后续认领拒绝新配置，但原回执已收口。
        final = self.f.rows()["out"]["queued"]
        self.assertEqual(final["status"], "sent")
        self.assertEqual(final["route"], original["route"])
        self.assertEqual(final["ownerId"], original["ownerId"])
        self.assertEqual(self.call("ledger", "lock", "acquire", "--skill", "followup", "--run", "next")[0], 0)

    def test_s08_rejected_commit_releases_sync_mutex(self):
        self.f.establish()
        child = self.start("--fetch-only")
        profile = self.f.ws / "profile.json"
        saved = profile.read_bytes()
        data = json.loads(saved); data["im"]["waitMinutes"] = 1
        profile.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.finish(child)[0], 2)
        profile.write_bytes(saved)
        self.assertEqual(self.call("im_lark", "sync", "--fetch-only")[0], 0)
        self.assertFalse(self.f.poll()["lastIncomplete"])


if __name__ == "__main__":
    unittest.main()

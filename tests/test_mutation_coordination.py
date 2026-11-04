"""真实进程的确定交错：管道控制检查点，不用 sleep 猜测竞争时序。"""

import contextlib
import io
import hashlib
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fsutil
import ledger
import notify
import workspace
from test_version_gate import ENTRY_CASES, CREATION_CASES, argv


CHILD = r'''
import contextlib, json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import fsutil, ledger, notify, workspace
ws, role, script, args, version, hook = Path(sys.argv[2]).resolve(), *sys.argv[3:]
args = json.loads(args)
ledger.SUITE_VERSION = version
real_locked = fsutil.locked
real_gate = ledger.version_gate
real_exists = Path.exists
paused = False

def event(kind, **data):
    print(json.dumps(dict(event=kind, **data)), file=sys.stderr, flush=True)

def pause(kind):
    global paused
    paused = True
    event(kind)
    assert sys.stdin.readline().strip() == "continue", "缺少父进程许可"

def audit(kind):
    with real_locked(ws.parent / (ws.name + ".audit.lock")):
        with (ws.parent / (ws.name + ".audit.jsonl")).open("a", encoding="utf-8") as out:
            out.write(json.dumps({"event": kind}) + "\n")

@contextlib.contextmanager
def watched_locked(path):
    if role == "contender" or hook == "waiting":
        event("lock_attempt", path=Path(path).name)
    if role == "writer" and hook == "resource" and Path(path).name != ".mutation.mutex":
        event("business_wait", path=Path(path).name)
    with real_locked(path):
        if hook == "trace":
            audit("acquire:" + Path(path).name)
        try:
            yield
        finally:
            if hook == "trace":
                audit("release:" + Path(path).name)
fsutil.locked = watched_locked
ledger.file_mutex = watched_locked

def checked(ws, mode):
    result = real_gate(ws, mode)
    if role == "writer" and hook == "gate" and mode != "read" and not paused:
        pause("checked")
    return result
ledger.version_gate = checked

def exists(path):
    result = real_exists(path)
    if role == "writer" and hook == "creation" and path == ws / ledger.MARKER and not result and not paused:
        pause("absent")
    return result
Path.exists = exists

real_emit = ledger.emit
def emitted(obj, code=0):
    if code == 0:
        audit(role + "_completed")
    return real_emit(obj, code)
ledger.emit = emitted
module = {"ledger": ledger, "notify": notify, "workspace": workspace}[script]
code = module.main(["--workspace", str(ws), *[a.replace("{ws}", str(ws)) for a in args]])
event("done", code=code)
sys.exit(code)
'''


def invoke(module, ws, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = module.main(["--workspace", str(ws), *args])
    return code, json.loads(out.getvalue())


class MutationCoordinationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.children = []
        self.number = 0

    def tearDown(self):
        for p, messages, reader in self.children:
            if p.poll() is None:
                p.kill()
            p.wait(timeout=5)
            reader.join(timeout=5)
            for stream in (p.stdin, p.stdout, p.stderr):
                stream.close()
        self.tmp.cleanup()

    def make_ws(self, create=True):
        self.number += 1
        ws = self.base / f"合成 工作区 {self.number}"
        if create:
            workspace.init_workspace(ws)
            ledger.write_atomic(ws / "profile.json", json.dumps({"schemaVersion": 2, "im": {
                "channel": "lark", "ownerId": "synthetic-owner", "larkProfile": "synthetic-profile"}}))
            ledger.write_atomic(ws / "setup-progress.json", json.dumps({"schemaVersion": 1,
                "mode": "partial", "plan": ["browser"], "answers": {}, "current": "browser", "added": {}}))
            self.assertEqual(invoke(notify, ws, "send", "--kind", "alert", "--key", "test", "--text", "合成消息")[0], 0)
            (ws / "backups/sample").mkdir(parents=True)
            ledger.write_atomic(ws / "backups/sample/rules.md", "备份规则")
            ledger.write_atomic(ws / "backups/sample/MANIFEST.json", json.dumps({"schemaVersion": 1, "files": [
                {"path": "rules.md", "sha256": hashlib.sha256("备份规则".encode("utf-8")).hexdigest()}]}))
        return ws

    def spawn(self, ws, role, script, args, version="1.0.0", hook="gate"):
        env = dict(os.environ, JOB_MATE_TESTING="1", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        p = subprocess.Popen([sys.executable, "-I", "-c", CHILD, str(ROOT / "scripts"), str(ws),
                              role, script, json.dumps(args), version, hook],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8", env=env)
        messages = queue.Queue()
        def read_events():
            for line in p.stderr:
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    messages.put({"event": "stderr", "text": line})
            messages.put({"event": "eof"})
        reader = threading.Thread(target=read_events, daemon=True)
        reader.start()
        self.children.append((p, messages, reader))
        return p, messages

    def next_event(self, child):
        try:
            event = child[1].get(timeout=10)
        except queue.Empty:
            self.fail("子进程检查点超时，可能存在锁顺序死锁")
        self.assertNotIn(event["event"], {"stderr", "eof"}, event)
        return event

    def finish(self, child):
        p, messages = child
        p.wait(timeout=10)
        out = p.stdout.read()
        self.assertTrue(out, "子进程未输出 JSON")
        return p.returncode, json.loads(out)

    def resume(self, child):
        child[0].stdin.write("continue\n")
        child[0].stdin.flush()

    def interleave(self, ws, script, args, transition, creation=False):
        writer = self.spawn(ws, "writer", script, args, hook="creation" if creation else "gate")
        self.assertEqual(self.next_event(writer)["event"], "absent" if creation else "checked")
        contender_args = (["init", "{ws}"] if creation else ["lock", "acquire", "--skill", "setup"])
        if transition == "recovery":
            contender_args += ["--recovery"]
        contender = self.spawn(ws, "contender", "workspace" if creation else "ledger", contender_args,
                               version="1.1.0" if transition == "version" else "1.0.0", hook="none")
        first = self.next_event(contender)
        # 新实现：writer 已持中央锁，竞争者在取中央锁前报告，先放行 writer。
        # 旧实现：只有业务锁（或创建已完成），让竞争者先完成，再放行旧检查。
        # 两种线性化次序均可接受；旧版不持锁的检查必须在变更前重新拒绝。
        serialized = first["event"] == "lock_attempt" and first["path"] == ".mutation.mutex"
        if serialized:
            self.resume(writer)
            result_a, result_b = self.finish(writer), self.finish(contender)
        else:
            result_b = self.finish(contender)
            self.resume(writer)
            result_a = self.finish(writer)
        audit = ws.parent / (ws.name + ".audit.jsonl")
        history = [json.loads(line)["event"] for line in audit.read_text(encoding="utf-8").splitlines()] if audit.exists() else []
        self.assertEqual(result_b[0], 0, result_b)
        if transition in {"version", "recovery"} and "contender_completed" in history:
            if result_a[0] == 0:
                self.assertLess(history.index("writer_completed"), history.index("contender_completed"),
                                f"过期准入仍成功写入：{history}, writer={result_a}, contender={result_b}")
            else:
                self.assertEqual(result_a[0], 2, result_a)
                self.assertIn(result_a[1].get("code"), {"suite_too_old", "recovery_lock_only"}, result_a)
        else:
            self.assertEqual(result_a[0], 0, result_a)
        return result_a, result_b

    def test_claim_recovery_and_version_transition_are_linearized(self):
        for transition in ("recovery", "version", "same"):
            with self.subTest(transition=transition):
                ws = self.make_ws()
                self.interleave(ws, "notify", ["outbox", "--claim"], transition)

    def test_stoplist_and_workspace_writes_are_linearized(self):
        for script, args in (("ledger", ["stoplist", "add", "--name", "合成公司", "--reason", "测试", "--source", "user"]),
                             ("workspace", ["progress", "record", "browser", "--status", "kept"]),
                             ("workspace", ["backup"]), ("workspace", ["restore", "sample"])):
            for transition in ("recovery", "version", "same"):
                with self.subTest(script=script, args=args, transition=transition):
                    self.interleave(self.make_ws(), script, args, transition)

    def test_waiting_for_business_mutex_cannot_escape_coordination(self):
        for script, args, name in (("notify", ["outbox", "--claim"], ".im.mutex"),
                                   ("ledger", ["stoplist", "add", "--name", "示例", "--reason", "测试", "--source", "user"], ".stoplist.mutex")):
            for transition in ("recovery", "version", "same"):
                with self.subTest(script=script, transition=transition):
                    ws = self.make_ws()
                    resource = fsutil.locked(ws / "ledger" / name)
                    resource.__enter__()
                    try:
                        writer = self.spawn(ws, "writer", script, args, hook="resource")
                        self.assertEqual(self.next_event(writer), {"event": "business_wait", "path": name})
                        acquire = ["lock", "acquire", "--skill", "setup"]
                        if transition == "recovery":
                            acquire += ["--recovery"]
                        contender = self.spawn(ws, "contender", "ledger", acquire,
                                               version="1.1.0" if transition == "version" else "1.0.0", hook="none")
                        first = self.next_event(contender)
                        serialized = first == {"event": "lock_attempt", "path": ".mutation.mutex"}
                        if not serialized:
                            result_b = self.finish(contender)
                    finally:
                        resource.__exit__(None, None, None)
                    result_a = self.finish(writer)
                    if serialized:
                        result_b = self.finish(contender)
                    self.assertEqual(result_b[0], 0, result_b)
                    audit = ws.parent / (ws.name + ".audit.jsonl")
                    history = [json.loads(s)["event"] for s in audit.read_text(encoding="utf-8").splitlines()]
                    if transition != "same" and result_a[0] == 0:
                        self.assertLess(history.index("writer_completed"), history.index("contender_completed"), history)
                    elif transition == "same":
                        self.assertEqual(result_a[0], 0, result_a)
                    else:
                        self.assertEqual(result_a[0], 2, result_a)
                        self.assertIn(result_a[1].get("code"), {"suite_too_old", "recovery_lock_only"})

    def test_creation_rechecks_marker_or_finishes_before_other_creator(self):
        for args in (["init", "{ws}"], ["progress", "start", "--mode", "new", "--variant", "typical"]):
            for transition in ("version", "same"):
                with self.subTest(args=args, transition=transition):
                    ws = self.make_ws(create=False)
                    self.interleave(ws, "workspace", args, transition, creation=True)
                    self.assertTrue((ws / ledger.MARKER).is_file())

    def test_waiting_writer_rechecks_after_recovery_or_version_change(self):
        for script, args in (("notify", ["outbox", "--claim"]),
                             ("ledger", ["stoplist", "add", "--name", "示例", "--reason", "测试", "--source", "user"]),
                             ("workspace", ["progress", "record", "browser", "--status", "kept"])):
            for transition in ("recovery", "version", "same"):
                with self.subTest(script=script, transition=transition):
                    ws = self.make_ws()
                    with ledger.mutation_mutex(ws):
                        writer = self.spawn(ws, "writer", script, args, hook="waiting")
                        first = self.next_event(writer)
                        self.assertEqual(first, {"event": "lock_attempt", "path": ".mutation.mutex"})
                        acquire = ["lock", "acquire", "--skill", "setup"]
                        if transition == "recovery":
                            acquire += ["--recovery"]
                        with patch.object(ledger, "SUITE_VERSION", "1.1.0" if transition == "version" else "1.0.0"):
                            self.assertEqual(invoke(ledger, ws, *acquire)[0], 0)
                    code, out = self.finish(writer)
                    if transition == "same":
                        self.assertEqual(code, 0, out)
                    else:
                        self.assertEqual((code, out.get("code")),
                                         (2, "suite_too_old" if transition == "version" else "recovery_lock_only"), out)
                        self.assertEqual(notify.read_records(ws)[-1]["status"], "queued")
                        self.assertFalse((ws / "stoplist.yaml").exists())
                        self.assertEqual(workspace.read_progress(ws)["answers"], {})

    def test_creator_waiting_for_coordination_rechecks_new_marker(self):
        for args in (["init", "{ws}"], ["progress", "start", "--mode", "new", "--variant", "typical"]):
            for version in ("1.0.0", "1.1.0"):
                with self.subTest(args=args, version=version):
                    ws = self.make_ws(create=False)
                    with ledger.mutation_mutex(ws):
                        self.assertFalse((ws / ledger.MARKER).exists())
                        with self.assertRaises(ledger.LedgerError):
                            ledger.find_workspace(str(ws))
                        writer = self.spawn(ws, "writer", "workspace", args, hook="waiting")
                        self.assertEqual(self.next_event(writer), {"event": "lock_attempt", "path": ".mutation.mutex"})
                        with patch.object(ledger, "SUITE_VERSION", version):
                            self.assertEqual(invoke(workspace, ws, "init", str(ws))[0], 0)
                    code, out = self.finish(writer)
                    self.assertEqual(code, 0 if version == "1.0.0" else 2, out)
                    if version != "1.0.0":
                        self.assertEqual(out.get("code"), "suite_too_old", out)
                        self.assertFalse((ws / "setup-progress.json").exists())
                    self.assertEqual(ledger.read_json(ws / ledger.MARKER)["suiteVersion"], version)

    def test_all_registered_write_recover_and_creation_handlers_hold_coordination(self):
        modules = {"ledger": ledger, "notify": notify, "workspace": workspace}
        for script, command, mode in ENTRY_CASES:
            with self.subTest(script=script, command=command):
                ws = self.make_ws()
                module = modules[script]
                parsed = module.build_parser().parse_args(argv(command, ws))
                mapping = notify.HANDLERS if script == "notify" else module.COMMANDS
                def checked(*unused):
                    self.assertEqual(ledger.mutation_held(ws), mode != "read")
                    return ledger.emit({"checked": True})
                with patch.dict(mapping, {parsed.cmd: checked}):
                    code, out = invoke(module, ws, *argv(command, ws))
                self.assertEqual((code, out), (0, {"checked": True}))
        for command in CREATION_CASES:
            with self.subTest(creation=command):
                ws = self.make_ws(create=False)
                parsed = workspace.build_parser().parse_args(argv(command, ws))
                def created(*unused):
                    self.assertTrue(ledger.mutation_held(ws))
                    return ledger.emit({"checked": True})
                with patch.dict(workspace.COMMANDS, {parsed.cmd: created}):
                    self.assertEqual(invoke(workspace, ws, *argv(command, ws)), (0, {"checked": True}))

    def test_readonly_processes_do_not_take_mutation_lock(self):
        ws = self.make_ws()
        self.assertEqual(invoke(ledger, ws, "lock", "acquire", "--skill", "setup", "--recovery")[0], 0)
        with ledger.mutation_mutex(ws):
            for script, args in (("notify", ["outbox"]), ("notify", ["poll"]),
                                 ("workspace", ["progress", "show"]), ("workspace", ["backups"]),
                                 ("ledger", ["lock", "status"]), ("ledger", ["stats"])):
                with self.subTest(script=script, args=args):
                    child = self.spawn(ws, "writer", script, args, hook="none")
                    code, out = self.finish(child)
                    self.assertEqual(code, 0, out)

    def test_incoming_pause_cross_script_lock_order_and_restore_do_not_deadlock(self):
        ws = self.make_ws()
        code, out = invoke(notify, ws, "outbox", "--claim")
        self.assertEqual(code, 0, out)
        claim = out["entries"][0]
        at = notify.now_ms()
        self.assertEqual(invoke(notify, ws, "receipt", "--key", claim["key"], "--token", claim["token"],
                                "--result", "sent", "--message-id", "synthetic-out", "--chat-id", "synthetic-chat",
                                "--create-time", str(at))[0], 0)
        batch = {"since": at, "complete": True, "messages": [{"messageId": "synthetic-in", "chatId": "synthetic-chat",
                 "createTime": at + 1, "senderId": "synthetic-owner", "senderType": "user", "text": "暂停",
                 "parentId": None, "deleted": False}]}
        child = self.spawn(ws, "writer", "notify", ["incoming", "--json", json.dumps(batch)], hook="trace")
        code, out = self.finish(child)
        self.assertEqual(code, 0, out)
        self.assertTrue(ledger.read_pause(ws))
        audit = ws.parent / (ws.name + ".audit.jsonl")
        events = [json.loads(s)["event"] for s in audit.read_text(encoding="utf-8").splitlines()]
        stack, acquired = [], []
        for event in events:
            action, _, name = event.partition(":")
            if action == "acquire":
                if name != ".mutation.mutex":
                    self.assertTrue(stack and stack[0] == ".mutation.mutex", events)
                if name == ".lock.mutex":
                    self.assertNotIn(".im.mutex", stack, events)
                stack.append(name)
                acquired.append(name)
            elif action == "release":
                self.assertEqual(stack.pop(), name, events)
        self.assertEqual(stack, [])
        self.assertEqual(acquired[0], ".mutation.mutex")
        self.assertTrue({".poll.mutex", ".im.mutex", ".lock.mutex"} <= set(acquired))
        self.assertEqual(self.finish(self.spawn(ws, "writer", "workspace", ["restore", "sample"], hook="none"))[0], 0)

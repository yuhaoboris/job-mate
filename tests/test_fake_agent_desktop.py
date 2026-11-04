"""T6.1只用临时合成场景与真实子进程；绝不查找或执行真实agent-desktop。"""
import copy
import json
import os
import queue
import stat
import threading
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FAKES = ROOT / "tests/fakes"
FAKE = FAKES / "agent_desktop.py"
SEED = FAKES / "scenarios/boss.json"
WINDOW = "window-1"


class FakeAgentDesktopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic desktop ")
        self.base = Path(self.tmp.name)
        self.scenario = self.base / "合成 场景.json"
        self.original = SEED.read_bytes()
        self.scenario.write_bytes(self.original)
        self.env = dict(os.environ, AGENT_DESKTOP_FAKE_SCENARIO=str(self.scenario), PYTHONDONTWRITEBYTECODE="1")

    def tearDown(self):
        self.assertEqual(SEED.read_bytes(), self.original)
        self.tmp.cleanup()

    def data(self):
        return json.loads(self.scenario.read_text(encoding="utf-8"))

    def update(self, change):
        data = self.data()
        change(data)
        self.scenario.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def cli(self, *args, code=0, launcher=False, env=None, timeout=10):
        command = [str(FAKES / "bin/agent-desktop")] if launcher else [sys.executable, "-B", "-I", str(FAKE)]
        p = subprocess.run([*command, *args], cwd=self.base, env=env or self.env,
                           capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        self.assertEqual(p.returncode, code, p.stdout + p.stderr)
        self.assertFalse(p.stderr, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual(out["version"], "2.5")
        expected = "version" if list(args) == ["--version"] else args[0] if args else "unknown"
        self.assertEqual(out["command"], "unknown" if code == 2 else expected)
        self.assertIs(out["ok"], code == 0)
        if code:
            self.assertNotIn("data", out)
            self.assertTrue(out["error"]["message"])
            self.assertIn(out["error"]["disposition"], ({"delivery": "not_delivered", "retry": "safe"},
                {"delivery": "unknown", "retry": "unknown"}, {"delivery": "delivered_unverified", "retry": "unsafe"}))
        else:
            self.assertIn("data", out)
            self.assertNotIn("error", out)
        return out

    def snapshot(self):
        return self.cli("snapshot", "--window", WINDOW)["data"]

    def action(self, command, snapshot, target, *args, code=0):
        return self.cli(command, "--window", WINDOW, "--snapshot", snapshot["snapshotId"], "--id", target, *args, code=code)

    def start(self, page):
        self.update(lambda s: s["windows"][0].update(page=page))
        return self.snapshot()

    def input_value(self, snapshot):
        return self.action("get", snapshot, "chat-input")["data"]["element"]["value"]

    def test_list_detail_conversation_and_native_send_lifecycle(self):
        listing = self.snapshot()
        found = self.cli("find", "--window", WINDOW, "--snapshot", listing["snapshotId"], "--query", "Python开发")["data"]
        self.assertTrue(found["complete"])
        self.assertEqual(len(found["matches"]), 1)
        ref = found["matches"][0]["ref"]
        node = self.cli("get", "--window", WINDOW, "--snapshot", listing["snapshotId"], "--ref", ref)["data"]["element"]
        self.assertEqual(node["id"], "job-python")
        self.cli("click", "--window", WINDOW, "--snapshot", listing["snapshotId"], "--ref", ref)
        self.assertEqual(self.data()["windows"][0]["page"], "detail")
        detail = self.snapshot()
        self.assertIn("双休", self.action("get", detail, "jd")["data"]["element"]["text"])
        self.assertIn("synthetic-python", self.action("get", detail, "address")["data"]["element"]["value"])
        self.action("click", detail, "greet")
        thread = self.snapshot()
        self.assertEqual(self.input_value(thread), "")
        approved = "  合成回答\r\n全角Ａ e\u0301 🌱  "
        out = self.action("set-value", thread, "chat-input", "--value", approved)
        self.assertEqual(out["data"]["action"], "set-value")
        self.assertEqual(out["data"]["disposition"], {"delivery": "delivered_unverified", "retry": "unsafe"})
        self.assertEqual(self.input_value(thread), approved)
        self.action("click", thread, "send")
        state = self.data()["state"]
        self.assertEqual(state["messages"]["thread-new"][-1]["text"], approved)
        self.assertEqual(state["messages"]["thread-new"][-1]["deliveryLabel"], "[送达]")
        self.assertEqual(len(state["inputEvents"]), 1)
        self.assertEqual(self.input_value(self.snapshot()), "")

    def test_root_expansion_and_zero_find_are_scoped_observations(self):
        data = self.cli("snapshot", "--window", WINDOW, "--root", "job-list")["data"]
        self.assertEqual({n["id"] for n in data["elements"]}, {"job-list", "job-python"})
        self.assertTrue(data["complete"])
        found = self.cli("find", "--window", WINDOW, "--snapshot", data["snapshotId"], "--query", "不存在内容")["data"]
        self.assertEqual(found["matches"], [])
        self.assertTrue(found["complete"])
        error = self.action("get", data, "messages-nav", code=1)
        self.assertEqual(error["error"]["code"], "ELEMENT_NOT_FOUND")
        self.assertEqual(self.cli("find", "--window", WINDOW, "--snapshot", data["snapshotId"], "--query", "", code=2)["error"]["code"], "INVALID_ARGS")

    def test_contenteditable_set_value_does_not_fire_input_but_headed_type_does(self):
        snapshot = self.start("thread-editable")
        self.action("set-value", snapshot, "chat-input", "--value", "合成新回答")
        self.assertEqual(self.input_value(snapshot), "合成新回答")
        state = self.data()["state"]
        self.assertEqual(state["inputEvents"], [])
        self.assertFalse(next(c for c in state["calls"] if c["command"] == "set-value")["effects"]["inputEvent"])
        self.action("click", snapshot, "send")
        self.assertEqual(self.data()["state"]["messages"].get("thread-editable", []), [])
        self.action("type", snapshot, "chat-input", "--text", "，前台追加", "--headed")
        self.action("click", snapshot, "send")
        state = self.data()["state"]
        self.assertEqual(state["messages"]["thread-editable"][-1]["text"], "合成新回答，前台追加")
        self.assertEqual(state["inputEvents"][-1]["method"], "type")

    def test_contenteditable_stale_model_can_send_old_text_despite_correct_readback(self):
        self.update(lambda s: next(n for n in s["pages"]["thread-editable"]["elements"] if n["id"] == "chat-input").update(value="旧草稿"))
        snapshot = self.start("thread-editable")
        self.action("set-value", snapshot, "chat-input", "--value", "看似正确的新回答")
        self.assertEqual(self.input_value(snapshot), "看似正确的新回答")
        self.action("click", snapshot, "send")
        self.assertEqual(self.data()["state"]["messages"]["thread-editable"][-1]["text"], "旧草稿")

    def test_headed_typing_preserves_residual_drafts_and_navigation_does_not_clear(self):
        first = self.start("thread-draft")
        self.assertEqual(self.input_value(first), "未确认原草稿")
        self.action("type", first, "chat-input", "--text", " 追加", "--headed")
        self.assertEqual(self.input_value(first), "未确认原草稿 追加")
        self.action("click", first, "back")
        listing = self.snapshot()
        self.action("click", listing, "thread-a-link")
        other = self.snapshot()
        self.assertEqual(self.input_value(other), "")
        self.action("click", other, "back")
        self.action("click", self.snapshot(), "thread-draft-link")
        self.assertEqual(self.input_value(self.snapshot()), "未确认原草稿 追加")
        self.assertEqual(self.data()["state"]["messages"].get("thread-draft", []), [])

    def test_background_type_and_unsupported_actions_fail_without_input(self):
        snapshot = self.start("thread-new")
        before = copy.deepcopy(self.data()["state"]["messages"])
        out = self.action("type", snapshot, "chat-input", "--text", "不能后台输入", code=1)
        self.assertEqual(out["error"]["code"], "ACTION_NOT_SUPPORTED")
        self.assertEqual(out["error"]["disposition"], {"delivery": "unknown", "retry": "unknown"})
        self.assertEqual(self.input_value(snapshot), "")
        self.assertEqual(self.data()["state"]["inputEvents"], [])
        self.assertEqual(self.data()["state"]["messages"], before)
        self.assertEqual(self.action("set-value", snapshot, "send", "--value", "非法目标", code=1)["error"]["code"], "ACTION_NOT_SUPPORTED")
        self.assertEqual(self.action("click", snapshot, "root", code=1)["error"]["code"], "ACTION_NOT_SUPPORTED")

    def test_empty_input_may_have_enabled_send_button_but_creates_no_message(self):
        snapshot = self.start("thread-new")
        self.assertTrue(self.action("get", snapshot, "send")["data"]["element"]["enabled"])
        self.action("click", snapshot, "send")
        self.assertEqual(self.data()["state"]["messages"]["thread-new"], [])
        self.assertFalse(self.data()["state"]["calls"][-1]["effects"]["messageCreated"])

    def test_no_delivery_marker_is_not_tool_failure_or_verified_business_success(self):
        self.update(lambda s: s["state"].update(deliveryMode="no_delivery"))
        snapshot = self.start("thread-new")
        self.action("set-value", snapshot, "chat-input", "--value", "合成待确认消息")
        result = self.action("click", snapshot, "send")
        self.assertEqual(result["data"]["disposition"]["delivery"], "delivered_unverified")
        message = self.data()["state"]["messages"]["thread-new"][-1]
        self.assertNotIn("deliveryLabel", message)
        self.assertIs(message["sending"], True)
        observed = self.snapshot()["elements"]
        self.assertTrue(any(n.get("sending") is True for n in observed))

    def test_same_minute_date_only_and_interview_card_are_visible_without_invented_precision(self):
        snapshot = self.start("thread-a")
        messages = [n for n in snapshot["elements"] if n.get("speaker") == "hr"]
        self.assertEqual([n["label"] for n in messages], ["今天 10:32", "今天 10:32"])
        self.assertEqual([n["text"] for n in messages], ["合成问题一", "合成问题二"])
        snapshot = self.start("thread-date")
        date = next(n for n in snapshot["elements"] if n.get("speaker") == "hr")
        self.assertEqual(date["label"], "11月4日")
        self.assertNotIn("at", date)
        snapshot = self.start("thread-invite")
        card = next(n for n in snapshot["elements"] if n.get("type") == "card")
        self.assertEqual(card["cardType"], "interview")
        self.assertEqual(card["buttons"], ["接受邀请", "拒绝邀请"])
        out = self.action("click", snapshot, "message-0-button-0", code=1)
        self.assertEqual(out["error"]["code"], "ACTION_NOT_SUPPORTED")

    def test_stale_refs_unknown_snapshot_window_and_ambiguous_targets_fail(self):
        first = self.snapshot()
        second = self.snapshot()
        ref = first["elements"][0]["ref"]
        out = self.cli("get", "--window", WINDOW, "--snapshot", second["snapshotId"], "--ref", ref, code=1)
        self.assertEqual(out["error"]["code"], "STALE_REF")
        out = self.cli("get", "--window", WINDOW, "--snapshot", "missing", "--ref", ref, code=1)
        self.assertEqual(out["error"]["code"], "SNAPSHOT_NOT_FOUND")
        self.assertEqual(self.cli("snapshot", "--window", "missing", code=1)["error"]["code"], "WINDOW_NOT_FOUND")
        self.assertEqual(self.cli("windows", "--app", "missing", code=1)["error"]["code"], "APP_NOT_FOUND")
        self.action("click", second, "job-python")
        self.assertEqual(self.action("get", first, "root", code=1)["error"]["code"], "STALE_REF")
        self.update(lambda s: s["pages"]["thread-new"]["elements"].append({"id": "another", "nativeId": "chat-input", "role": "text-field", "editable": True}))
        snapshot = self.start("thread-new")
        self.assertEqual(self.action("get", snapshot, "chat-input", code=1)["error"]["code"], "AMBIGUOUS_TARGET")

    def test_window_binding_and_page_roundtrip_do_not_revalidate_old_refs(self):
        first = self.snapshot()
        self.update(lambda s: s["windows"].append(dict(s["windows"][0], id="window-2")))
        out = self.cli("get", "--window", "window-2", "--snapshot", first["snapshotId"], "--id", "root", code=1)
        self.assertEqual(out["error"]["code"], "STALE_REF")
        self.action("click", first, "job-python")
        self.action("click", self.snapshot(), "back")
        self.assertEqual(self.data()["windows"][0]["page"], "jobs")
        self.assertEqual(self.action("get", first, "root", code=1)["error"]["code"], "STALE_REF")

    def test_locked_screen_and_permissions_are_separate_from_execution_proof(self):
        self.update(lambda s: s["state"].update(locked=True))
        out = self.cli("snapshot", "--window", WINDOW, code=1)
        self.assertEqual(out["error"]["details"]["kind"], "window_without_accessibility_element")
        self.assertEqual(out["error"]["disposition"], {"delivery": "unknown", "retry": "unknown"})
        self.assertEqual(len(self.cli("windows")["data"]), 1)
        self.update(lambda s: (s["state"].update(locked=False), s["permissions"]["accessibility"].update(state="denied")))
        self.assertEqual(self.cli("permissions", "status")["data"]["accessibility"]["state"], "denied")
        self.assertEqual(self.cli("snapshot", "--window", WINDOW, code=1)["error"]["code"], "PERMISSION_DENIED")

    def test_cdp_is_hard_refused_logged_and_never_consumes_timeout_as_fallback(self):
        self.update(lambda s: s["state"]["faults"].append({"command": "launch", "kind": "timeout"}))
        for args in (("launch", "--cdp", "--url", "https://example.invalid"), ("launch", "--cdp=9222")):
            out = self.cli(*args, code=1)
            self.assertEqual(out["error"]["code"], "ACTION_NOT_SUPPORTED")
            self.assertEqual(self.data()["state"]["calls"][-1]["argv"], list(args))
        self.assertEqual(len(self.data()["state"]["faults"]), 1)

    def test_invalid_arguments_and_missing_targets_never_succeed(self):
        for args in (("unknown-command",), ("--version", "extra"), ("snapshot",), ("snapshot", "--window", WINDOW, "--screenshot"),
                     ("click", "--window", WINDOW, "--snapshot", "s"), ("apps", "--unexpected"), ("permissions", "unknown"), ("windows", "--unexpected"), ("--help",)):
            with self.subTest(args=args):
                result = self.cli(*args, code=2)
                self.assertEqual(result["error"]["code"], "INVALID_ARGS")
                self.assertEqual(result["command"], "unknown")
                self.assertEqual(self.data()["state"]["calls"][-1]["argv"], list(args))
        snapshot = self.start("thread-new")
        self.assertEqual(self.action("click", snapshot, "not-existing", code=1)["error"]["code"], "ELEMENT_NOT_FOUND")
        self.assertEqual(self.action("type", snapshot, "chat-input", "--text", "", "--headed", code=2)["error"]["code"], "INVALID_ARGS")

    def test_incomplete_and_structured_before_after_fault_envelopes(self):
        self.update(lambda s: s["state"]["faults"].append({"command": "snapshot", "kind": "incomplete"}))
        snapshot = self.snapshot()
        self.assertFalse(snapshot["complete"])
        self.assertTrue(snapshot["truncated"])
        result = self.cli("find", "--window", WINDOW, "--snapshot", snapshot["snapshotId"], "--query", "不存在")["data"]
        self.assertEqual(result["matches"], [])
        self.assertFalse(result["complete"])
        snapshot = self.start("thread-new")
        self.update(lambda s: s["state"]["faults"].append({"command": "set-value", "kind": "error", "code": "STALE_REF"}))
        out = self.action("set-value", snapshot, "chat-input", "--value", "不应执行", code=1)
        self.assertEqual(out["error"]["code"], "STALE_REF")
        self.assertEqual(out["error"]["disposition"], {"delivery": "not_delivered", "retry": "safe"})
        self.assertEqual(self.input_value(snapshot), "")
        self.action("set-value", snapshot, "chat-input", "--value", "可能已发送")
        self.update(lambda s: s["state"]["faults"].append({"command": "click", "kind": "locked", "phase": "after"}))
        out = self.action("click", snapshot, "send", code=1)
        self.assertEqual(out["error"]["details"]["kind"], "window_without_accessibility_element")
        self.assertEqual(out["error"]["details"]["steps"], [{"action": "click", "outcome": "performed"}])
        self.assertEqual(out["error"]["details"]["after_action"]["disposition"], out["error"]["disposition"])
        self.assertEqual(out["error"]["disposition"]["retry"], "unsafe")
        self.assertEqual(self.data()["state"]["messages"]["thread-new"][-1]["text"], "可能已发送")

    def timeout_call(self, command):
        p = subprocess.Popen([sys.executable, "-B", "-I", str(FAKE), *command], cwd=self.base, env=self.env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        marker = queue.Queue()
        reader = threading.Thread(target=lambda: marker.put(p.stderr.readline().strip()), daemon=True)
        reader.start()
        try:
            self.assertEqual(marker.get(timeout=10), "fake-timeout-ready")
            reader.join(timeout=5)
            with self.assertRaises(subprocess.TimeoutExpired):
                p.communicate(timeout=0.05)
        finally:
            if p.poll() is None:
                p.kill()
            stdout, stderr = p.communicate(timeout=5)
            reader.join(timeout=5)
            self.assertFalse(stdout, stdout)
            self.assertFalse(stderr, stderr)
            p.stdout.close()
            p.stderr.close()

    def test_actual_process_timeout_preserves_argv_and_consumes_only_one_fault(self):
        args = ["snapshot", "--window", WINDOW]
        self.update(lambda s: s["state"]["faults"].append({"command": "snapshot", "kind": "timeout", "delaySeconds": 2}))
        self.timeout_call(args)
        state = self.data()["state"]
        self.assertEqual(state["faults"], [])
        self.assertEqual(state["calls"][-1]["argv"], args)
        self.assertTrue(state["calls"][-1]["timeoutPending"])
        self.assertEqual(state["snapshots"], {})
        self.assertTrue(self.snapshot()["complete"])

    def test_after_action_timeout_keeps_observable_send_not_a_fabricated_no_send(self):
        snapshot = self.start("thread-new")
        self.action("set-value", snapshot, "chat-input", "--value", "超时前已执行")
        self.update(lambda s: s["state"]["faults"].append({"command": "click", "kind": "timeout", "phase": "after", "delaySeconds": 2}))
        self.timeout_call(["click", "--window", WINDOW, "--snapshot", snapshot["snapshotId"], "--id", "send"])
        state = self.data()["state"]
        self.assertEqual(state["messages"]["thread-new"][-1]["text"], "超时前已执行")
        self.assertTrue(state["calls"][-1]["effects"]["messageCreated"])
        self.assertTrue(any(n.get("text") == "超时前已执行" for n in self.snapshot()["elements"]))

    def test_missing_environment_invalid_scene_and_committed_fixture_fail_closed(self):
        env = dict(self.env)
        env.pop("AGENT_DESKTOP_FAKE_SCENARIO")
        out = self.cli("--version", code=1, env=env)
        self.assertEqual(out["error"]["code"], "FAKE_SCENARIO_REQUIRED")
        self.assertEqual(out["error"]["details"]["argv"], ["--version"])
        for raw in (b"broken", b"[]", b"{}", b'{"schemaVersion":1,"schemaVersion":1,"synthetic":true}',
                    json.dumps(dict(self.data(), synthetic=False)).encode("utf-8")):
            with self.subTest(raw=raw[:40]):
                self.scenario.write_bytes(raw)
                self.assertEqual(self.cli("apps", code=1)["error"]["code"], "FAKE_SCENARIO_INVALID")
                self.assertEqual(self.scenario.read_bytes(), raw)
        self.scenario.write_bytes(self.original)
        for path in (str(SEED), "relative.json", str(self.base / "missing.json")):
            env = dict(self.env, AGENT_DESKTOP_FAKE_SCENARIO=path)
            self.assertEqual(self.cli("apps", code=1, env=env)["error"]["code"], "FAKE_SCENARIO_INVALID")
        self.assertEqual(self.data()["state"]["calls"], [])
        self.assertFalse(list(FAKES.rglob("*.lock")))

    def test_malformed_state_and_unknown_scene_actions_do_not_get_success_defaults(self):
        mutations = [lambda s: s["windows"][0].update(page=["jobs"]),
                     lambda s: s["pages"]["jobs"].update(thread=[]),
                     lambda s: s["pages"]["jobs"]["elements"][0].update(action={"kind": "shell", "command": "do not execute"}),
                     lambda s: s["state"].update(nextSnapshot=True),
                     lambda s: s["state"].update(snapshots={"bad": {"nodes": [None], "refs": {}, "complete": True}}),
                     lambda s: s["state"].update(faults=[{"kind": "incomplete", "command": "click"}])]
        for mutate in mutations:
            self.scenario.write_bytes(self.original)
            self.update(mutate)
            before = self.scenario.read_bytes()
            self.assertEqual(self.cli("snapshot", "--window", WINDOW, code=1)["error"]["code"], "FAKE_SCENARIO_INVALID")
            self.assertEqual(self.scenario.read_bytes(), before)

    @unittest.skipIf(os.name == "nt", "原生Windows统一直接运行Python替身，不提供.cmd")
    def test_posix_launcher_is_executable_cwd_independent_and_preserves_unicode_arguments(self):
        launcher = FAKES / "bin/agent-desktop"
        self.assertTrue(launcher.stat().st_mode & stat.S_IXUSR)
        self.assertTrue(launcher.read_bytes().startswith(b"#!/usr/bin/env python3\n"))
        snapshot = self.start("thread-new")
        text = "  Unicode 合成文本 🌱 与空格\n第二行  "
        args = ["set-value", "--window", WINDOW, "--snapshot", snapshot["snapshotId"], "--id", "chat-input", "--value", text]
        env = dict(self.env, PATH=str(Path(sys.executable).parent))
        self.cli(*args, launcher=True, env=env)
        self.assertEqual(self.data()["state"]["calls"][-1]["argv"], args)
        self.assertEqual(self.input_value(snapshot), text)

    def test_fakes_and_state_are_absent_from_all_product_packages(self):
        out = self.base / "dist"
        p = subprocess.run([sys.executable, "-B", "-I", str(ROOT / "scripts/build.py"), "--out", str(out)],
                           cwd=self.base, env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        built = json.loads(p.stdout)["built"]
        self.assertEqual(len(built), 3)
        for package in map(Path, built):
            self.assertFalse((package / "tests").exists())
            self.assertFalse(any("agent_desktop.py" == f.name or f.name == "boss.json" or f.suffix == ".lock" for f in package.rglob("*")))
        self.assertEqual(self.data()["state"]["calls"], [])

    def test_version_permissions_apps_windows_and_call_arguments(self):
        out = self.cli("--version")
        self.assertEqual(out["command"], "version")
        self.assertEqual(out["data"]["version"], "1.0.2")
        self.assertEqual(self.cli("permissions")["data"]["accessibility"]["state"], "granted")
        self.assertEqual(self.cli("apps")["data"]["apps"][0]["id"], "com.google.Chrome")
        self.assertEqual(self.cli("windows", "--app", "com.google.Chrome")["data"][0]["id"], WINDOW)
        self.assertEqual([c["argv"] for c in self.data()["state"]["calls"]],
                         [["--version"], ["permissions"], ["apps"], ["windows", "--app", "com.google.Chrome"]])

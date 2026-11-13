"""飞书适配器：只用临时合成工作区、Python替身及真正的离线Node。"""
import contextlib
import copy
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import im_lark
import ledger
import notify
from test_build import require_symlinks

spec = importlib.util.spec_from_file_location("lark_offline_fake", ROOT / "tests/fakes/lark_cli.py")
fake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fake)
PROFILE = "synthetic-profile"
OWNER = "ou_" + "1" * 32
CHAT = "oc_" + "1" * 32
FAKE = ROOT / "tests/fakes/lark_cli.py"
ANCHOR = int(fake.sample("native-get")["data"]["items"][0]["create_time"])


def response(value, code=0, stderr=""):
    return subprocess.CompletedProcess([], code, json.dumps(value, ensure_ascii=False), stderr)


class ImLarkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="职伴 lark ")
        self.base = Path(self.tmp.name).resolve()
        self.ws = self.base / "工作区"
        self.ws.mkdir()
        (self.ws / "ledger").mkdir()
        (self.ws / "inbox").mkdir()
        (self.ws / "job-mate.json").write_text(json.dumps({"schemaVersion": 1, "suiteVersion": "1.0.0", "accounts": {"boss": {"accountKey": "synthetic"}}}), encoding="utf-8")
        (self.ws / "inbox/decisions.md").write_text("# 待本人决定\n\n## D-20400102-01 待决定\n\n合成事项\n", encoding="utf-8")
        self.state = self.base / "合成 lark state.json"
        self.write_state(fake.initial_state(seeded=False))
        self.env = patch.dict(os.environ, {"JOB_MATE_TESTING": "1", "JOB_MATE_NOW": datetime.fromtimestamp((ANCHOR + 120000) / 1000, timezone.utc).isoformat(),
                                         "LARK_FAKE_STATE": str(self.state), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)
        self.configure()

    def configure(self, **changes):
        im = {"channel": "lark", "ownerId": OWNER, "larkProfile": PROFILE, "larkCommand": [sys.executable, str(FAKE)]}
        im.update(changes)
        if im["channel"] == "none":
            im = {"channel": "none"}
        with ledger.mutation_mutex(self.ws):
            im_lark.fsutil.write_text_atomic(self.ws / "profile.json", json.dumps({"schemaVersion": 1, "im": im}, ensure_ascii=False))

    def write_state(self, value):
        self.state.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def state_data(self):
        return json.loads(self.state.read_text(encoding="utf-8"))

    def update_state(self, change):
        data = self.state_data()
        change(data)
        self.write_state(data)

    def invoke(self, module, *args, expected=0):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = module.main(["--workspace", str(self.ws), *args])
        result = json.loads(output.getvalue())
        self.assertEqual(code, expected, result)
        return result

    def sync(self, *args, expected=0):
        return self.invoke(im_lark, "sync", *args, expected=expected)

    def queue(self, key="queued", text="合成原文", reply=None):
        args = ["send", "--kind", "alert", "--key", key, "--text", text]
        if reply is not None:
            args += ["--reply-to", reply]
        self.invoke(notify, *args)

    def assert_no_scan_progress(self, before):
        current = (self.ws / "ledger/im.jsonl").read_bytes()
        self.assertTrue(current.startswith(before), "已有业务记录必须逐字保留")
        added = [json.loads(line) for line in current[len(before):].splitlines()]
        self.assertLessEqual(len(added), 1)
        for record in added:
            self.assertEqual((record["type"], record["phase"], record["scanVersion"]), ("scan", "intent", 1))
        # 仅可新增首次查询意图：没有 incoming、cursor、checkpoint 或完成水位。
        self.assertFalse(any(r.get("phase") in ("checkpoint", "complete") for r in added))

    def poll(self):
        return notify.poll(self.ws, notify.context(self.ws))

    def rows(self):
        return notify.load_state(self.ws)

    def fault(self, command, kind, **extra):
        self.update_state(lambda s: s["faults"].append({"command": command, "kind": kind, **extra}))

    def establish(self):
        self.queue()
        result = self.sync("--send-only")
        self.assertEqual(result["sent"], 1)
        self.assertTrue(self.poll()["readable"])
        return self.rows()["out"]["queued"]["messageId"]

    def message(self, n=2, text="合成答复", created=None):
        item = copy.deepcopy(fake.sample("native-list-page2")["data"]["items"][0])
        item.update(message_id="om_" + f"{n:032x}", create_time=str(ANCHOR + n if created is None else created),
                    update_time=str(ANCHOR + n if created is None else created), message_position=str(100 + n))
        item["body"]["content"] = json.dumps({"text": text}, ensure_ascii=False)
        return item

    def add_messages(self, *messages):
        self.update_state(lambda s: s["profiles"][PROFILE]["messages"].extend(copy.deepcopy(messages)))

    def page(self, items, more=False, token=""):
        return {"ok": True, "identity": "bot", "data": {"items": items, "has_more": more, "page_token": token}}

    def fetch_pages(self, pages):
        iterator = iter(pages)
        calls = []
        def transport(command, arguments):
            calls.append(arguments)
            self.assertEqual(arguments[:5], ["--profile", PROFILE, "api", "GET", im_lark.API_MESSAGES])
            value = next(iterator)
            return value if isinstance(value, subprocess.CompletedProcess) else response(value)
        with patch.object(im_lark, "run_lark", side_effect=transport):
            result = self.sync("--fetch-only")
        return result, calls

    def test_none_skips_all_external_calls_and_claims(self):
        self.configure(channel="none")
        with patch.object(im_lark, "resolve_lark_command", side_effect=AssertionError("不可定位")), patch.object(subprocess, "run", side_effect=AssertionError("不可调用")):
            for mode in ((), ("--send-only",), ("--fetch-only",)):
                self.assertEqual(self.sync(*mode), {"skipped": "channel_none"})
            self.assertTrue(self.invoke(im_lark, "doctor")["ok"])
        self.assertEqual(self.state_data()["calls"], [])

    def test_doctor_uses_sampled_array_and_nested_bot_proof_without_user_login(self):
        self.update_state(lambda s: s["profiles"][PROFILE]["auxiliary"]["authStatus"].update(
            verified=False, identities={"bot": fake.sample("auth-status")["identities"]["bot"], "user": {"available": False, "verified": False}}))
        before = self.rows()
        out = self.invoke(im_lark, "doctor")
        self.assertTrue(out["ok"])
        self.assertEqual([c["name"] for c in out["checks"]], ["executable", "version", "profile", "bot"])
        self.assertEqual(self.rows(), before)
        self.assertEqual([c["command"] for c in self.state_data()["calls"]], ["version", "profiles", "auth"])
        for call in self.state_data()["calls"]:
            self.assertEqual(call["argv"][:2], ["--profile", PROFILE])
        self.assertEqual(self.state_data()["calls"][-1]["argv"][2:], ["auth", "status", "--json", "--verify"])

    def test_doctor_rejects_bot_unverified_profile_mismatch_and_bot_as_owner(self):
        original = fake.initial_state(seeded=False)
        for edit in (lambda p: p["authStatus"]["identities"]["bot"].update(verified=False),
                     lambda p: p["authStatus"]["identities"]["bot"].update(verified="true"),
                     lambda p: p["authStatus"].update(appId="cli_" + "f" * 16),
                     lambda p: p.update(profileList=[{"name": "other", "appId": "cli_synthetic"}]),
                     lambda p: p.update(profileList=p["profileList"] + [copy.deepcopy(p["profileList"][-1])])):
            with self.subTest(case=edit):
                data = copy.deepcopy(original)
                edit(data["profiles"][PROFILE]["auxiliary"])
                self.write_state(data)
                self.assertFalse(self.invoke(im_lark, "doctor", expected=1)["ok"])
        self.write_state(original)
        self.configure(ownerId=original["profiles"][PROFILE]["botOpenId"])
        out = self.invoke(im_lark, "doctor", expected=1)
        self.assertEqual(out["checks"][-1]["error"], "bot_is_not_owner")

    def test_first_send_uses_native_get_time_and_never_shortcut_time(self):
        self.establish()
        record = self.rows()["out"]["queued"]
        self.assertEqual(record["createTime"], ANCHOR)
        self.assertEqual([c["command"] for c in self.state_data()["calls"]], ["send", "native-get"])
        self.assertEqual(self.poll()["sinceMs"], ANCHOR)
        self.sync("--send-only")
        self.assertEqual(len(self.state_data()["profiles"][PROFILE]["messages"]), 1)
        for call in self.state_data()["calls"]:
            self.assertEqual(call["argv"][:2], ["--profile", PROFILE])

    def test_get_failure_after_success_stays_sent_without_time_or_resend(self):
        for kind in ("missing_create_time", "missing_message_id", "other_chat", "nonzero"):
            with self.subTest(kind=kind):
                self.configure()
                (self.ws / "ledger/im.jsonl").unlink(missing_ok=True)
                self.write_state(fake.initial_state(seeded=False))
                self.fault("native-get", kind)
                self.queue()
                result = self.sync()
                self.assertEqual((result["sent"], result["unknown"]), (1, 0))
                self.assertIn("sent_without_trusted_time", result["errors"])
                self.assertFalse(self.poll()["readable"])
                self.assertIsNone(self.rows()["out"]["queued"]["createTime"])
                self.assertNotIn("native-list", [c["command"] for c in self.state_data()["calls"]])
                self.sync()
                self.assertEqual(len(self.state_data()["profiles"][PROFILE]["messages"]), 1)

    def test_bad_get_time_and_identity_do_not_downgrade_confirmed_send(self):
        original = im_lark.run_lark
        for key, value in (("create_time", "2040-01-02 08:00"), ("create_time", None), ("create_time", 123),
                           ("create_time", True), ("create_time", "-1"), ("create_time", "9" * 18),
                           ("message_id", "om_" + "e" * 32), ("chat_id", "oc_" + "e" * 32)):
            with self.subTest(key=key, value=value):
                (self.ws / "ledger/im.jsonl").unlink(missing_ok=True)
                self.write_state(fake.initial_state(seeded=False))
                self.queue()
                def alter(command, arguments):
                    got = original(command, arguments)
                    if arguments[2:4] == ["api", "GET"]:
                        tree = json.loads(got.stdout)
                        tree["data"]["items"][0][key] = value
                        return response(tree)
                    return got
                with patch.object(im_lark, "run_lark", side_effect=alter):
                    result = self.sync()
                self.assertEqual((result["sent"], result["unknown"]), (1, 0))
                self.assertFalse(self.poll()["readable"])

    def test_send_missing_ids_or_bad_envelope_is_unknown_and_not_retried(self):
        for invalid in ({"ok": True, "identity": "bot", "data": {}}, {"ok": 1, "identity": "bot", "data": {}},
                        {"ok": True, "identity": "user", "data": fake.sample("send")["data"]}):
            with self.subTest(value=invalid):
                (self.ws / "ledger/im.jsonl").unlink(missing_ok=True)
                self.queue()
                with patch.object(im_lark, "run_lark", return_value=response(invalid)) as process:
                    self.assertEqual(self.sync("--send-only")["unknown"], 1)
                    self.assertEqual(self.sync("--send-only")["unknown"], 0)
                    self.assertEqual(process.call_count, 1)
                self.assertEqual(self.rows()["out"]["queued"]["status"], "unknown")

    def test_only_measured_rejection_retries_once_per_sync_to_three(self):
        self.queue()
        for _ in range(3):
            self.fault("send", "nonzero")
            result = self.sync()
            self.assertEqual((result["failed"], result["unknown"]), (1, 0))
        self.assertEqual(self.sync()["failed"], 0)
        self.assertEqual(self.rows()["out"]["queued"]["attempts"], 3)
        self.assertEqual([c["command"] for c in self.state_data()["calls"]], ["send"] * 3)
        self.assertEqual(len({c["argv"][c["argv"].index("--idempotency-key") + 1] for c in self.state_data()["calls"]}), 1)

    def test_unknown_error_code_or_conflicting_output_is_not_definite_rejection(self):
        for code in (99991679, "99992351", True):
            rejected = fake.sample("send-reject")
            rejected["error"]["code"] = code
            self.assertFalse(im_lark.definitely_rejected(response({}, 1, json.dumps(rejected))))
            self.assertFalse(im_lark.definitely_rejected(subprocess.CompletedProcess([], 1, "", json.dumps(rejected))))
        tree = fake.sample("send-reject")
        self.assertTrue(im_lark.definitely_rejected(subprocess.CompletedProcess([], 1, "", json.dumps(tree))))
        self.assertFalse(im_lark.definitely_rejected(subprocess.CompletedProcess([], 1, json.dumps(fake.sample("send")), json.dumps(tree))))
        self.queue()
        tree["error"]["code"] = 99991679
        with patch.object(im_lark, "run_lark", return_value=subprocess.CompletedProcess([], 1, "", json.dumps(tree))):
            self.assertEqual(self.sync("--send-only")["unknown"], 1)

    def test_real_timeout_before_and_after_send_is_unknown_but_get_timeout_is_sent(self):
        original = im_lark.subprocess.run
        for command, phase, status, count in (("send", "before", "unknown", 0), ("send", "after", "unknown", 1), ("native-get", "after", "sent", 1)):
            with self.subTest(command=command, phase=phase):
                (self.ws / "ledger/im.jsonl").unlink(missing_ok=True)
                self.write_state(fake.initial_state(seeded=False))
                self.fault(command, "timeout", phase=phase, delaySeconds=1)
                self.queue()
                def bounded(argv, **kwargs):
                    target = "+messages-send" if command == "send" else "/open-apis/im/v1/messages/"
                    if not any(target in value for value in argv):
                        return original(argv, **kwargs)
                    self.assertEqual(kwargs.pop("timeout"), 20)
                    self.assertFalse(kwargs.pop("shell"))
                    self.assertTrue(kwargs.pop("capture_output"))
                    child = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
                    try:
                        self.assertEqual(child.stderr.readline().strip(), "fake-timeout-ready")
                        # ready 证明故障前/后状态已经刷盘；再制造真实 TimeoutExpired。
                        return child.communicate(timeout=0.02)
                    finally:
                        child.kill()
                        child.communicate()
                with patch.object(im_lark.subprocess, "run", side_effect=bounded):
                    result = self.sync()
                self.assertEqual(result[status], 1)
                state = self.state_data()
                self.assertTrue(any(c.get("timeoutPending") for c in state["calls"]))
                self.assertEqual(len(state["profiles"][PROFILE]["messages"]), count)
                self.assertFalse(self.poll()["readable"])
                self.sync("--send-only")
                self.assertEqual(len(self.state_data()["profiles"][PROFILE]["messages"]), count)

    def test_metacharacters_are_literal_and_reference_uses_reply(self):
        parent = self.establish()
        sentinel = self.base / "must-not-exist"
        text = f'''引号" ' & %PATH% ^ | $(touch {sentinel})\n中文 🌱 e\u0301 结尾\\'''
        self.queue("reply", text, parent)
        self.sync("--send-only")
        calls = [c for c in self.state_data()["calls"] if c["command"] in ("send", "reply")]
        self.assertEqual([c["command"] for c in calls], ["send", "reply"])
        argv = calls[-1]["argv"]
        self.assertEqual(argv[argv.index("--text") + 1], notify.compose("alert", text))
        self.assertEqual(argv[argv.index("--message-id") + 1], parent)
        self.assertFalse(sentinel.exists())
        message = self.state_data()["profiles"][PROFILE]["messages"][-1]
        self.assertEqual(message["parent_id"], parent)

    def test_route_change_blocks_old_queue_without_using_new_profile(self):
        self.queue()
        for changes in ({"larkProfile": "different-profile"}, {"ownerId": "ou_" + "f" * 32}):
            with self.subTest(changes=changes):
                self.configure(**changes)
                result = self.sync("--send-only")
                self.assertEqual((result["sent"], result["unknown"]), (0, 0))
                self.assertEqual(self.state_data()["calls"], [])
                self.assertEqual(self.rows()["out"]["queued"]["status"], "queued")

    def test_native_samples_have_precise_milliseconds_parent_and_no_shortcut_calls(self):
        self.establish()
        owner = fake.sample("native-list-page2")["data"]["items"][0]
        self.add_messages(owner)
        out = self.sync("--fetch-only")
        stored = self.rows()["in"][owner["message_id"]]
        self.assertEqual((stored["createTime"], stored["parentId"]), (int(owner["create_time"]), owner["parent_id"]))
        self.assertEqual(stored["text"], json.loads(owner["body"]["content"])["text"])
        self.assertFalse(out["incomplete"])
        self.assertEqual(out["sent"], 0)
        self.assertTrue(any(o["status"] == "queued" for o in self.rows()["out"].values()))
        call = self.state_data()["calls"][-1]
        query = json.loads(call["argv"][call["argv"].index("--params") + 1])
        self.assertEqual(query, {"container_id_type": "chat", "container_id": CHAT, "start_time": str(ANCHOR // 1000 - 1),
                                 "end_time": str((notify.now_ms() + 999) // 1000), "sort_type": "ByCreateTimeAsc", "page_size": 50})
        self.assertNotIn("list", [c["command"] for c in self.state_data()["calls"]])

    def test_same_minute_precise_messages_overlap_dedup_and_callback_send(self):
        self.establish()
        first, second = self.message(2, created=ANCHOR + 2), self.message(3, created=ANCHOR + 3)
        self.add_messages(first, second)
        out = self.sync()
        self.assertEqual(out["sent"], 2)
        self.assertEqual(self.poll()["businessCursorMs"], ANCHOR + 3)
        self.assertEqual(self.poll()["sinceMs"], self.poll()["scanThroughMs"])
        self.assertEqual(len(self.rows()["in"]), 2)
        self.sync()
        self.assertEqual(len(self.rows()["in"]), 2)
        self.assertEqual(len([c for c in self.state_data()["calls"] if c["command"] == "reply"]), 2)

    def test_sample_pages_chain_with_exact_profile_and_fixed_window(self):
        self.establish()
        pages = [fake.sample("native-list-page1"), fake.sample("native-list-page2")]
        out, calls = self.fetch_pages(pages)
        self.assertFalse(out["incomplete"])
        self.assertEqual(len(self.rows()["in"]), 1)
        queries = [json.loads(c[c.index("--params") + 1]) for c in calls]
        self.assertEqual(queries[1].pop("page_token"), pages[0]["data"]["page_token"])
        self.assertEqual(queries[0], queries[1])

    def test_twenty_pages_accept_only_validated_contiguous_prefix_including_ms_ties(self):
        self.establish()
        pages = [self.page([self.message(n + 2, created=ANCHOR + 10)], True, f"synthetic-token-{n}") for n in range(20)]
        out, calls = self.fetch_pages(pages)
        self.assertTrue(out["incomplete"])
        self.assertTrue(self.poll()["lastIncomplete"])
        self.assertEqual(len(calls), 20)
        self.assertEqual(len(self.rows()["in"]), 20)
        self.assertEqual(self.poll()["sinceMs"], ANCHOR + 10)

    def test_bad_last_page_rejects_entire_batch_without_cursor_or_reply_writes(self):
        self.establish()
        before = (self.ws / "ledger/im.jsonl").read_bytes()
        good = self.page([self.message()], True, "synthetic-token")
        for bad in (self.page([self.message(3)], True, "synthetic-token"),
                    self.page([self.message(3)], True, ""),
                    {"ok": True, "identity": "bot", "data": {"items": [], "has_more": True}},
                    self.page([self.message(3)], 1, ""), self.page([], True, "new-token")):
            with self.subTest(bad=bad):
                out, _ = self.fetch_pages([good, bad])
                self.assertTrue(out["incomplete"])
                self.assert_no_scan_progress(before)
        earlier = self.message(3, created=ANCHOR + 1)
        out, _ = self.fetch_pages([good, self.page([earlier])])
        self.assertIn("page_order_gap", out["errors"])
        self.assert_no_scan_progress(before)

    def test_invalid_message_fields_reject_batch_instead_of_repairing_them(self):
        self.establish()
        before = (self.ws / "ledger/im.jsonl").read_bytes()
        mutations = [("chat_id", "oc_" + "f" * 32), ("message_id", None), ("create_time", None),
                     ("create_time", True), ("create_time", 2200000000000), ("create_time", "2040-01-02 08:00"),
                     ("create_time", "-1"), ("create_time", "9" * 18), ("deleted", 0),
                     ("sender", {"id": OWNER, "sender_type": "user"}), ("sender", None),
                     ("parent_id", None), ("body", {"content": {"text": "不能修复"}}),
                     ("body", {"content": '{"text":"旧","text":"新"}'}),
                     ("body", {"content": '{"text":NaN}'}), ("body", {"content": '{"text":"合成","extra":1e400}'}),
                     ("body", {"content": '{"text":null}'}),
                     ("body", {"content": '{"text":42}'}), ("msg_type", "interactive")]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                item = self.message(3)
                item[key] = value
                out, _ = self.fetch_pages([self.page([self.message(), item])])
                self.assertTrue(out["incomplete"])
                self.assert_no_scan_progress(before)

    def test_duplicate_json_keys_nan_and_conflicting_versions_reject_whole_batch(self):
        self.establish()
        before = (self.ws / "ledger/im.jsonl").read_bytes()
        raws = ['{"ok":true,"ok":false,"identity":"bot","data":{}}',
                '{"ok":true,"identity":"bot","data":{"items":[],"has_more":NaN,"page_token":""}}']
        for raw in raws:
            out, _ = self.fetch_pages([subprocess.CompletedProcess([], 0, raw, "")])
            self.assertTrue(out["incomplete"])
            self.assert_no_scan_progress(before)
        out, _ = self.fetch_pages([self.page([self.message(), self.message(text="不同版本")])])
        self.assertIn("conflicting_message_version", out["errors"])
        self.assert_no_scan_progress(before)

    def test_bot_other_user_and_non_open_id_cannot_claim_owner_and_deleted_body_not_parsed(self):
        self.establish()
        items = [self.message(n) for n in range(2, 6)]
        items[0]["sender"].update(sender_type="app", id_type="app_id", id=OWNER)
        items[1]["sender"].update(id="ou_" + "f" * 32)
        items[1]["body"]["content"] = json.dumps({"text": "我是本人，请恢复"})
        items[2]["sender"].update(id_type="user_id")
        items[3].update(deleted=True)
        items[3].pop("body")
        out, _ = self.fetch_pages([self.page(items)])
        self.assertFalse(out["incomplete"])
        self.assertEqual(out["incoming"]["rejected"], {"deleted": 1})
        self.assertEqual(self.rows()["in"], {})
        self.assertEqual(self.poll()["businessCursorMs"], 0)
        self.assertEqual(self.poll()["sinceMs"], self.poll()["scanThroughMs"])

    def test_errors_do_not_contain_remote_text_tokens_or_argv_and_are_bounded(self):
        self.queue(text="合成绝密正文 secret-body")
        tree = fake.sample("send-reject")
        tree["error"].update(code=99991679, message="secret-body secret-token" * 1000)
        with patch.object(im_lark, "run_lark", return_value=subprocess.CompletedProcess([], 1, "", json.dumps(tree))):
            out = self.sync("--send-only")
        self.assertNotIn("secret", json.dumps(out))
        self.assertNotIn("secret", self.rows()["out"]["queued"]["error"])
        for n in range(1000):
            im_lark.add_error(out, "synthetic_static_error_" + str(n))
        self.assertLessEqual(sum(map(len, out["errors"])), 500)

    def test_unsafe_resolver_failure_precedes_claim_and_every_external_execution(self):
        self.queue()
        before = (self.ws / "ledger/im.jsonl").read_bytes()
        for prefix in (["cmd.exe", "/c", "echo unsafe"], ["powershell", "-Command", "unsafe"], [sys.executable, "-c", "unsafe"]):
            self.configure(larkCommand=prefix)
            with patch.object(subprocess, "run", side_effect=AssertionError("禁止进程")):
                self.sync("--send-only", expected=2)
                self.assertFalse(self.invoke(im_lark, "doctor", expected=1)["ok"])
            self.assertEqual((self.ws / "ledger/im.jsonl").read_bytes(), before)

    def test_all_sync_modes_deny_newer_version_and_recovery_before_external_call(self):
        self.queue()
        for condition in ("version", "recovery"):
            marker = self.ws / "job-mate.json"
            meta = json.loads(marker.read_text(encoding="utf-8"))
            meta["suiteVersion"] = "1.1.0" if condition == "version" else "1.0.0"
            marker.write_text(json.dumps(meta), encoding="utf-8")
            if condition == "recovery":
                lock = {"runId": "synthetic-recovery", "skill": "setup", "mode": "recovery", "acquiredAt": ledger.now_iso(ledger.tz_of({})),
                        "expiresAt": datetime.fromtimestamp((notify.now_ms() + 3600000) / 1000, timezone.utc).isoformat()}
                ledger.write_atomic(ledger.lock_path(self.ws), json.dumps(lock))
            for flags in ((), ("--send-only",), ("--fetch-only",)):
                with self.subTest(condition=condition, flags=flags), patch.object(im_lark, "run_lark", side_effect=AssertionError("不可外调")):
                    self.sync(*flags, expected=2)
        self.assertEqual(self.rows()["out"]["queued"]["status"], "queued")

    def test_gate_rechecks_after_resolver_before_any_claim(self):
        self.queue()
        before = (self.ws / "ledger/im.jsonl").read_bytes()
        original = im_lark.resolve_lark_command
        def changed(cfg):
            prefix = original(cfg)
            meta = ledger.read_json(self.ws / "job-mate.json")
            meta["suiteVersion"] = "1.1.0"
            ledger.write_atomic(self.ws / "job-mate.json", json.dumps(meta))
            return prefix
        with patch.object(im_lark, "resolve_lark_command", side_effect=changed), patch.object(im_lark, "run_lark", side_effect=AssertionError("不可外调")):
            self.sync("--send-only", expected=2)
        self.assertEqual((self.ws / "ledger/im.jsonl").read_bytes(), before)

    def test_real_waiting_sync_rechecks_admission_after_mutation_lock(self):
        self.queue()
        worker = self.base / "waiting_sync.py"
        worker.write_text("import sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nimport im_lark\nprint('entry-ready',file=sys.stderr,flush=True)\nsys.exit(im_lark.main(sys.argv[2:]))\n", encoding="utf-8")
        with ledger.mutation_mutex(self.ws):
            child = subprocess.Popen([sys.executable, "-B", "-I", str(worker), str(ROOT / "scripts"), "--workspace", str(self.ws), "sync"],
                                     cwd=self.base, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
            self.assertEqual(child.stderr.readline().strip(), "entry-ready")
            meta = ledger.read_json(self.ws / "job-mate.json")
            meta["suiteVersion"] = "1.1.0"
            ledger.write_atomic(self.ws / "job-mate.json", json.dumps(meta))
        try:
            stdout, stderr = child.communicate(timeout=15)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()
        self.assertEqual(child.returncode, 2, stdout + stderr)
        self.assertEqual(json.loads(stdout)["code"], "suite_too_old")
        self.assertEqual(self.state_data()["calls"], [])
        self.assertEqual(self.rows()["out"]["queued"]["status"], "queued")

    def test_real_concurrent_fetches_do_not_duplicate_owner_reply_or_corrupt_cursor(self):
        self.establish()
        message = self.message()
        self.add_messages(message)
        args = [sys.executable, "-B", "-I", str(ROOT / "scripts/im_lark.py"), "--workspace", str(self.ws), "sync", "--fetch-only"]
        children = [subprocess.Popen(args, cwd=self.base, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8") for _ in range(2)]
        try:
            results = [p.communicate(timeout=20) for p in children]
        finally:
            for p in children:
                if p.poll() is None:
                    p.kill()
                    p.communicate()
        for p, (stdout, stderr) in zip(children, results):
            self.assertEqual(p.returncode, 0, stdout + stderr)
        for out, _ in results:
            result = json.loads(out)
            if result.get("busy"):
                self.assertIs(result["incomplete"], True)
                self.assertEqual(result["incoming"]["new"], [])
                self.assertEqual(result["sent"], 0)
        self.assertEqual(sum(len(json.loads(out)["incoming"]["new"]) for out, _ in results), 1)
        before = (self.ws / "ledger/im.jsonl").read_bytes()
        cursor = self.poll()["businessCursorMs"]
        with im_lark.fsutil.try_locked(self.ws / "ledger/.im-sync.lock") as held:
            self.assertTrue(held)
            process = subprocess.run(args, cwd=self.base, capture_output=True, encoding="utf-8", timeout=10)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            busy = json.loads(process.stdout)
            self.assertIs(busy["busy"], True)
            self.assertIs(busy["incomplete"], True)
            self.assertEqual(busy["incoming"]["new"], [])
            self.assertEqual(busy["sent"], 0)
            self.assertEqual((self.ws / "ledger/im.jsonl").read_bytes(), before)
            self.assertEqual(self.poll()["businessCursorMs"], cursor)
        self.assertEqual(len(self.rows()["in"]), 1)
        self.assertEqual(self.poll()["businessCursorMs"], int(message["create_time"]))
        self.assertEqual(self.poll()["sinceMs"], self.poll()["scanThroughMs"])
        self.assertEqual(len([o for o in self.rows()["out"].values() if o.get("replyTo") == message["message_id"]]), 1)

    def test_each_built_package_runs_independent_doctor_send_fetch_and_ack_outside_repo(self):
        import build
        dist = self.base / "dist"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(build.main(["--out", str(dist)]), 0)
        # 连替身及其标准库封装也复制到仓库外，子进程无仓库导入路径。
        toolroot = self.base / "offline tool"
        tool = toolroot / "tests/fakes/lark_cli.py"
        tool.parent.mkdir(parents=True)
        shutil.copyfile(FAKE, tool)
        shutil.copytree(FAKE.parent / "lark_samples", tool.parent / "lark_samples")
        (toolroot / "scripts").mkdir()
        shutil.copyfile(ROOT / "scripts/fsutil.py", toolroot / "scripts/fsutil.py")
        for package in sorted(dist.iterdir()):
            with self.subTest(package=package.name):
                self.ws = self.base / (package.name + " 工作区")
                self.write_state(fake.initial_state(seeded=False))
                def cli(script, *args):
                    process = subprocess.run([sys.executable, "-B", "-I", str(package / "scripts" / script), *args], cwd=self.base,
                                             capture_output=True, encoding="utf-8", timeout=30)
                    self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
                    self.assertFalse(process.stderr)
                    return json.loads(process.stdout)
                cli("workspace.py", "init", str(self.ws), "--account", "boss:synthetic:合成账号")
                self.configure(larkCommand=[sys.executable, str(tool)])
                self.assertTrue(cli("im_lark.py", "--workspace", str(self.ws), "doctor")["ok"])
                test = cli("notify.py", "--workspace", str(self.ws), "test")
                self.assertEqual(test["status"], "queued")
                self.assertEqual(cli("im_lark.py", "--workspace", str(self.ws), "sync", "--send-only")["sent"], 1)
                quoted = self.message(42, "收到")
                quoted["parent_id"] = self.rows()["out"][test["key"]]["messageId"]
                self.add_messages(quoted)
                out = cli("im_lark.py", "--workspace", str(self.ws), "sync", "--fetch-only")
                self.assertEqual(out["incoming"]["new"][0]["state"], "test")
                self.assertEqual(cli("im_lark.py", "--workspace", str(self.ws), "sync", "--send-only")["sent"], 1)
                self.assertTrue(cli("notify.py", "--workspace", str(self.ws), "status")["readable"])

    def test_real_concurrent_sync_processes_claim_once(self):
        self.queue()
        args = [sys.executable, "-B", "-I", str(ROOT / "scripts/im_lark.py"), "--workspace", str(self.ws), "sync", "--send-only"]
        children = [subprocess.Popen(args, cwd=self.base, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8") for _ in range(2)]
        try:
            outcomes = [p.communicate(timeout=20) for p in children]
        finally:
            for p in children:
                if p.poll() is None:
                    p.kill()
                    p.communicate()
        for p, (stdout, stderr) in zip(children, outcomes):
            self.assertEqual(p.returncode, 0, stdout + stderr)
            self.assertFalse(stderr)
        for stdout, _ in outcomes:
            result = json.loads(stdout)
            if result.get("busy"):
                self.assertIs(result["incomplete"], True)
                self.assertEqual(result["sent"], 0)
                self.assertEqual(result["incoming"]["new"], [])
        self.assertEqual(sum(json.loads(stdout)["sent"] for stdout, _ in outcomes), 1)
        before = (self.ws / "ledger/im.jsonl").read_bytes()
        cursor = self.poll()["businessCursorMs"]
        calls = self.state_data()["calls"]
        with im_lark.fsutil.try_locked(self.ws / "ledger/.im-sync.lock") as held:
            self.assertTrue(held)
            process = subprocess.run(args, cwd=self.base, capture_output=True, encoding="utf-8", timeout=10)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            busy = json.loads(process.stdout)
            self.assertIs(busy["busy"], True)
            self.assertIs(busy["incomplete"], True)
            self.assertEqual(busy["sent"], 0)
            self.assertEqual(busy["incoming"]["new"], [])
            self.assertEqual((self.ws / "ledger/im.jsonl").read_bytes(), before)
            self.assertEqual(self.poll()["businessCursorMs"], cursor)
            self.assertEqual(self.state_data()["calls"], calls)
        self.assertEqual([c["command"] for c in self.state_data()["calls"]], ["send", "native-get"])
        self.assertEqual(self.rows()["out"]["queued"]["attempts"], 1)


class CommandResolutionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="node argv 路径 ")
        self.base = Path(self.tmp.name).resolve()
        self.addCleanup(self.tmp.cleanup)
        self.script = self.base / "node_modules/package/bin/argv echo.js"
        self.script.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "tests/fakes/argv_echo.js", self.script)
        self.wrapper = self.base / "lark-cli.cmd"
        self.template = (ROOT / "tests/fixtures/npm/lark-cli.cmd").read_bytes().decode("utf-8")
        self.relative = r"node_modules\package\bin\argv echo.js"
        self.wrapper.write_bytes(self.template.replace("<相对JS路径>", self.relative).encode("utf-8"))

    def real_node(self):
        node = shutil.which("node")
        if not node:
            if os.environ.get("CI"):
                self.fail("CI 必须安装并运行真实 Node LTS 离线测试")
            self.skipTest("本机未安装 Node，不能以 Python 替身冒充真实 Node argv 测试")
        return Path(node).resolve()

    def test_npm_template_is_exact_not_a_substring_parser(self):
        self.assertEqual(self.template.replace("<相对JS路径>", "@SCRIPT@"), im_lark.NPM_TEMPLATE)
        self.real_node()
        node = self.base / "node.exe"
        shutil.copy2(self.real_node(), node)
        node.chmod(node.stat().st_mode | 0o111)
        for newline in ("\n", "\r\n"):
            self.wrapper.write_bytes(self.template.replace("<相对JS路径>", self.relative).replace("\n", newline).encode("utf-8"))
            with patch.object(im_lark.shutil, "which", return_value=str(self.wrapper)):
                self.assertEqual(im_lark.resolve_lark_command({}, windows=True), [str(node), str(self.script)])
        for body in (f'"%dp0%\\{self.relative}" %*\n', self.template.replace("<相对JS路径>", self.relative) + "echo extra\n",
                     self.template.replace("<相对JS路径>", self.relative).replace("CALL :find_dp0", "CALL :other"),
                     self.template.replace("<相对JS路径>", self.relative).replace("@ECHO off", "@echo off")):
            self.wrapper.write_text(body, encoding="utf-8")
            with patch.object(im_lark.shutil, "which", return_value=str(self.wrapper)), patch.object(subprocess, "run", side_effect=AssertionError("不可执行包装")):
                with self.assertRaises(im_lark.LarkError):
                    im_lark.resolve_lark_command({}, windows=True)

    def test_npm_script_variables_traversal_and_missing_files_are_refused(self):
        for relative in (r"..\outside.js", r"C:\outside.js", r"\outside.js", r"node_modules\%EVIL%.js", r"node_modules\a&b.js",
                         r"node_modules\a^b.js", r"node_modules\a|b.js", r"node_modules\a!b.js", r"node_modules\a\..\b.js",
                         r'node_modules\a"b.js', "node_modules/evil\n.js", "missing.js", "node_modules/evil.py"):
            with self.subTest(relative=relative):
                self.wrapper.write_text(self.template.replace("<相对JS路径>", relative), encoding="utf-8")
                with patch.object(im_lark.shutil, "which", return_value=str(self.wrapper)):
                    with self.assertRaises(im_lark.LarkError):
                        im_lark.resolve_lark_command({}, windows=True)

    def test_forbidden_entry_variants_and_interpreter_flags_never_spawn(self):
        with patch.object(subprocess, "run", side_effect=AssertionError("禁止启动")):
            for raw in ("cmd", "CMD.EXE", "powershell.exe", "pwsh", "sh", "bash", "zsh", "wscript.exe", "cscript.exe",
                        "foo.cmd", "foo.BAT", "foo.ps1", "foo.vbs", "env", "busybox", "ruby", "perl"):
                for candidate in ([raw], [str(self.base / raw), "/c", "unsafe"]):
                    with self.subTest(candidate=candidate), self.assertRaises(im_lark.LarkError):
                        im_lark.resolve_lark_command({"larkCommand": candidate}, windows=True)
            for prefix in ([sys.executable, "-c", "unsafe"], [sys.executable, "-m", "http.server"],
                           [str(self.real_node()), "-e", "unsafe"], [str(self.real_node()), "--eval=unsafe"],
                           [str(self.real_node()), str(self.script), "extra-prefix"], [str(self.wrapper)]):
                with self.subTest(prefix=prefix), self.assertRaises(im_lark.LarkError):
                    im_lark.resolve_lark_command({"larkCommand": prefix})
        with patch.dict(os.environ, {"JOB_MATE_TESTING": "0"}), self.assertRaises(im_lark.LarkError):
            im_lark.resolve_lark_command({"larkCommand": [sys.executable, str(FAKE)]})

    def test_symlinks_cannot_hide_shell_batch_node_or_npm_path_escape(self):
        require_symlinks(self)
        target = self.base / "cmd.exe"
        target.write_bytes(b"MZsynthetic-canary-not-a-program")
        target.chmod(0o755)
        alias = self.base / "innocent.exe"
        alias.symlink_to(target)
        batch = self.base / "innocent.cmd"
        batch.write_bytes(self.wrapper.read_bytes())
        no_ext = self.base / "lark-cli"
        no_ext.symlink_to(batch)
        node = self.base / "node.exe"
        node.symlink_to(batch)
        with patch.object(subprocess, "run", side_effect=AssertionError("禁止进程")):
            for candidate in ([str(alias)], [str(node), str(self.script)], [str(no_ext)]):
                with self.subTest(candidate=candidate), self.assertRaises(im_lark.LarkError):
                    im_lark.resolve_lark_command({"larkCommand": candidate})
            for discovered in (str(no_ext), str(self.wrapper)):
                with patch.object(im_lark.shutil, "which", return_value=discovered), self.assertRaises(im_lark.LarkError):
                    im_lark.resolve_lark_command({}, windows=True)
        node.unlink()
        shutil.copy2(self.real_node(), node)
        outside = self.base.parent / (self.base.name + "-outside.js")
        outside.write_text("not executed", encoding="utf-8")
        self.addCleanup(outside.unlink, missing_ok=True)
        self.script.unlink()
        self.script.symlink_to(outside)
        with patch.object(im_lark.shutil, "which", return_value=str(self.wrapper)), self.assertRaises(im_lark.LarkError):
            im_lark.resolve_lark_command({}, windows=True)

    def test_real_node_literal_argv_explicit_and_npm_sources_with_spaces(self):
        # POSIX 的 windows=True 是解析模拟；真正的 Windows 仍由三系统 CI 运行。
        node = self.real_node()
        local = self.base / "node.exe"
        shutil.copy2(node, local)
        local.chmod(local.stat().st_mode | 0o111)
        sentinel = self.base / "must-not-exist"
        values = ['"', "'", "back\\slash", "trailing\\", "&", "%PATH%", "^", "|", "line1\nline2", "$(echo x)",
                  f"$(touch {sentinel})", "中文 🌱 e\u0301", "", " leading and trailing "]
        with patch.dict(os.environ, {"NODE_OPTIONS": "--require=nonexistent-must-not-load.js"}):
            explicit = im_lark.resolve_lark_command({"larkCommand": [str(node), str(self.script)]})
            out = im_lark.run_lark(explicit, values)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(json.loads(out.stdout), values)
            with patch.object(im_lark.shutil, "which", return_value=str(self.wrapper)):
                npm = im_lark.resolve_lark_command({}, windows=True)
            out = im_lark.run_lark(npm, values, windows=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(json.loads(out.stdout), values)
        self.assertFalse(sentinel.exists())

    def test_posix_discovery_uses_fixed_node_script_and_refuses_shell_shebang(self):
        if os.name == "nt":
            self.skipTest("POSIX 入口发现；Windows 跑 npm 与显式 Node 用例")
        node = self.real_node()
        entry = self.base / "cli.js"
        entry.write_bytes(b"#!/usr/bin/env node\n" + self.script.read_bytes())
        def which(name):
            return str(entry) if name == "lark-cli" else str(node)
        with patch.object(im_lark.shutil, "which", side_effect=which):
            prefix = im_lark.resolve_lark_command({})
            self.assertEqual(prefix, [str(node), str(entry)])
            result = im_lark.run_lark(prefix, ["字面原文", ""])
            self.assertEqual(json.loads(result.stdout), ["字面原文", ""])
        sentinel = self.base / "forbidden-shell-ran"
        entry.write_text("#!/bin/sh\ntouch " + str(sentinel) + "\n", encoding="utf-8")
        entry.chmod(0o755)
        with patch.object(im_lark.shutil, "which", side_effect=which), patch.object(subprocess, "run", side_effect=AssertionError("不可启动 shell")):
            with self.assertRaises(im_lark.LarkError):
                im_lark.resolve_lark_command({})
        self.assertFalse(sentinel.exists())

    def test_same_directory_node_precedes_path_and_wrong_target_never_falls_back(self):
        node = self.real_node()
        calls = []
        def which(name):
            calls.append(name)
            return str(self.wrapper) if name == "lark-cli" else str(node)
        with patch.object(im_lark.shutil, "which", side_effect=which):
            # 模拟 Windows 搜索规则时，PATH node 也必须具有 .exe 后缀。
            if node.suffix.lower() == ".exe":
                out = im_lark.resolve_lark_command({}, windows=True)
                self.assertEqual(out[0], str(node))
            else:
                with self.assertRaises(im_lark.LarkError):
                    im_lark.resolve_lark_command({}, windows=True)
        self.assertEqual(calls, ["lark-cli", "node"])


if __name__ == "__main__":
    unittest.main()

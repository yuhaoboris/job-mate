"""T6.3真实子进程驱动离线假CLI；不定位真实lark-cli，不联网、不外发。"""
import importlib.util
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
FAKE = ROOT / "tests/fakes/lark_cli.py"
SAMPLES = ROOT / "tests/fakes/lark_samples"
PROFILE = "synthetic-profile"


def sample(name):
    return json.loads((SAMPLES / (name + ".json")).read_text(encoding="utf-8"))


class FakeLarkCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic lark ")
        self.base = Path(self.tmp.name)
        self.state = self.base / "合成 状态.json"
        self.env = dict(os.environ, LARK_FAKE_STATE=str(self.state), PYTHONDONTWRITEBYTECODE="1")
        self.sample_bytes = {p: p.read_bytes() for p in SAMPLES.iterdir() if p.is_file()}
        spec = importlib.util.spec_from_file_location("synthetic_lark", FAKE)
        self.fake = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.fake)
        self.reset()

    def tearDown(self):
        for path, original in self.sample_bytes.items():
            self.assertEqual(path.read_bytes(), original, path.name)
        self.tmp.cleanup()

    def reset(self, seeded=True):
        self.state.write_text(json.dumps(self.fake.initial_state(PROFILE, seeded=seeded), ensure_ascii=False), encoding="utf-8")

    def data(self):
        return json.loads(self.state.read_text(encoding="utf-8"))

    def update(self, mutation):
        state = self.data()
        mutation(state)
        self.state.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def cli(self, *args, code=0, profile=PROFILE, env=None):
        argv = ["--profile", profile, *args]
        p = subprocess.run([sys.executable, "-B", "-I", str(FAKE), *argv], cwd=self.base, env=env or self.env,
                           capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(p.returncode, code, p.stdout + p.stderr)
        if code:
            self.assertFalse(p.stdout, p.stdout)
            out = json.loads(p.stderr)
            self.assertIs(out["ok"], False)
        elif args == ("--version",):
            self.assertEqual(p.stdout, "lark-cli version 1.0.96\n")
            self.assertFalse(p.stderr, p.stderr)
            return p.stdout
        else:
            self.assertFalse(p.stderr, p.stderr)
            out = json.loads(p.stdout)
            self.assertIs(out["ok"], True)
            self.assertEqual(out["identity"], "bot")
        return out

    def send(self, text="合成通知，可忽略", key="synthetic-key", **kwargs):
        return self.cli("im", "+messages-send", "--as", "bot", "--user-id", sample("list")["data"]["messages"][1]["sender"]["id"],
                        "--text", text, "--idempotency-key", key, "--format", "json", **kwargs)

    def native_params(self, size=1):
        values = [int(m["create_time"]) for m in self.data()["profiles"][PROFILE]["messages"]]
        return {"container_id_type": "chat", "container_id": sample("send")["data"]["chat_id"],
                "start_time": str(min(values) // 1000 - 1), "end_time": str(max(values) // 1000 + 2),
                "sort_type": "ByCreateTimeAsc", "page_size": size}

    def listing(self, query=None, **kwargs):
        return self.cli("api", "GET", "/open-apis/im/v1/messages", "--as", "bot", "--params",
                        json.dumps(self.native_params() if query is None else query, ensure_ascii=False), "--format", "json", **kwargs)

    def test_send_duplicate_mget_and_rejection_match_actual_sample_trees_and_channels(self):
        self.reset(seeded=False)
        first = self.send()
        self.assertEqual(first, sample("send"))
        self.assertEqual(self.send(), sample("send-dup"))
        self.assertEqual(len(self.data()["profiles"][PROFILE]["messages"]), 1)
        mid = first["data"]["message_id"]
        out = self.cli("im", "+messages-mget", "--as", "bot", "--message-ids", mid, "--no-reactions", "--format", "json")
        self.assertEqual(out, sample("mget"))
        out = self.cli("im", "+messages-send", "--as", "bot", "--user-id", "ou_" + "e" * 32,
                       "--text", "合成拒绝探针", "--idempotency-key", "reject-key", "--format", "json", code=1)
        self.assertEqual(out, sample("send-reject"))
        self.assertEqual(self.data()["calls"][-1]["channel"], "stderr")

    def test_shortcut_list_preserves_formatted_minutes_sender_and_reply_to(self):
        out = self.cli("im", "+chat-messages-list", "--as", "bot", "--chat-id", sample("send")["data"]["chat_id"],
                       "--start", "1970-01-01T00:00:00+00:00", "--order", "asc", "--page-all", "--page-limit", "20", "--no-reactions", "--format", "json")
        self.assertEqual(out, sample("list"))
        self.assertIsInstance(out["data"]["messages"][0]["content"], str)
        self.assertIn("reply_to", out["data"]["messages"][1])
        self.assertNotIn("parent_id", out["data"]["messages"][1])

    def test_both_native_pages_match_real_shapes_and_token_connection(self):
        query = self.native_params()
        first = self.listing(query)
        self.assertEqual(first, sample("native-list-page1"))
        second = self.listing(dict(query, page_token=first["data"]["page_token"]))
        self.assertEqual(second, sample("native-list-page2"))
        self.assertIs(second["data"]["has_more"], False)
        item = second["data"]["items"][0]
        self.assertEqual(item["parent_id"], sample("send")["data"]["message_id"])
        self.assertEqual(item["root_id"], item["parent_id"])
        self.assertIsInstance(item["create_time"], str)
        self.assertIsInstance(json.loads(item["body"]["content"])["text"], str)

    def test_native_pagination_rejects_unknown_token_or_changed_window(self):
        query = self.native_params()
        token = self.listing(query)["data"]["page_token"]
        for changes in ({"page_token": "unknown"}, {"page_token": ""}, {"page_token": token, "end_time": str(int(query["end_time"]) + 1)},
                        {"page_token": token, "page_size": 2}, {"sort_type": "ByCreateTimeDesc"}, {"page_size": True},
                        {"start_time": int(query["start_time"])}, {"container_id": "oc_" + "f" * 32}, {"future": 1}):
            with self.subTest(changes=changes):
                self.listing(dict(query, **changes), code=2)
        self.assertEqual(self.listing(dict(query, page_token=token)), sample("native-list-page2"))

    def test_api_only_supports_exact_get_paths_never_post_or_other_endpoints(self):
        before = self.data()["profiles"][PROFILE]["messages"]
        for method, path in (("POST", "/open-apis/im/v1/messages"), ("DELETE", "/open-apis/im/v1/messages"),
                             ("GET", "/open-apis/contact/v3/users"), ("GET", "https://example.invalid/"),
                             ("GET", "/open-apis/im/v1/messages/../other")):
            with self.subTest(method=method, path=path):
                self.cli("api", method, path, "--as", "bot", "--format", "json", code=2)
        self.cli("api", "GET", "/open-apis/im/v1/messages/om_" + "f" * 32, "--as", "bot", "--format", "json", code=1)
        self.assertEqual(self.data()["profiles"][PROFILE]["messages"], before)

    def test_reply_is_explicitly_derived_send_contract_and_linked_in_state(self):
        parent = sample("native-list-page2")["data"]["items"][0]["message_id"]
        args = ("im", "+messages-reply", "--as", "bot", "--message-id", parent, "--text", "合成引用回复", "--idempotency-key", "reply-key", "--format", "json")
        first = self.cli(*args)
        self.assertEqual(set(first["data"]), set(sample("send")["data"]))
        self.assertEqual(self.cli(*args), first)
        message = self.data()["profiles"][PROFILE]["messages"][-1]
        self.assertEqual(message["parent_id"], parent)
        self.assertEqual(message["root_id"], sample("send")["data"]["message_id"])
        self.assertEqual(json.loads(message["body"]["content"])["text"], "合成引用回复")
        self.assertEqual(len(self.data()["profiles"][PROFILE]["messages"]), 3)

    def test_auxiliary_profile_contact_require_explicit_synthetic_responses(self):
        self.cli("profile", "list", code=2)
        query = ["+get", "--user-id", sample("list")["data"]["messages"][1]["sender"]["id"]]
        self.cli("contact", *query, code=2)
        profile_response = {"ok": True, "identity": "bot", "data": {"synthetic": True, "profiles": [PROFILE]}}
        contact_response = {"ok": True, "identity": "bot", "data": {"synthetic": True, "confirmedOwner": query[-1]}}
        def configure(state):
            aux = state["profiles"][PROFILE]["auxiliary"]
            aux["profileList"] = profile_response
            aux["contacts"][json.dumps(query, ensure_ascii=False, separators=(",", ":"))] = contact_response
        self.update(configure)
        self.assertEqual(self.cli("profile", "list"), profile_response)
        self.assertEqual(self.cli("contact", *query), contact_response)
        self.cli("contact", "+get", "--user-id", "different-query", code=2)

    def test_metacharacters_unicode_and_argv_remain_literal_without_shell(self):
        self.reset(seeded=False)
        sentinel = self.base / "must-not-exist"
        text = f'''  引号" ' & %PATH% $HOME ; $(touch {sentinel})\n全角Ａ e\u0301 🌱  '''
        out = self.send(text=text, key="key with spaces & % ' Unicode合成")
        state = self.data()
        argv = state["calls"][-1]["argv"]
        self.assertEqual(argv[argv.index("--text") + 1], text)
        self.assertEqual(argv[:2], ["--profile", PROFILE])
        self.assertFalse(sentinel.exists())
        mid = out["data"]["message_id"]
        native = self.cli("api", "GET", "/open-apis/im/v1/messages/" + mid, "--as", "bot", "--format", "json")
        self.assertEqual(json.loads(native["data"]["items"][0]["body"]["content"])["text"], text)

    def test_idempotency_does_not_allow_intent_changes_or_cross_profile_state(self):
        self.reset(seeded=False)
        first = self.send(text="甲", key="same-key")
        self.send(text="乙", key="same-key", code=2)
        second_profile = self.fake.initial_state("second-profile", seeded=False)["profiles"]["second-profile"]
        second_profile.update(ownerId="ou_" + "3" * 32, chatId="oc_" + "2" * 32)
        self.update(lambda state: state["profiles"].update({"second-profile": second_profile}))
        second = self.cli("im", "+messages-send", "--as", "bot", "--user-id", second_profile["ownerId"], "--text", "乙",
                          "--idempotency-key", "same-key", "--format", "json", profile="second-profile")
        self.assertNotEqual(first["data"]["message_id"], second["data"]["message_id"])
        self.assertNotEqual(first["data"]["chat_id"], second["data"]["chat_id"])
        self.cli("api", "GET", "/open-apis/im/v1/messages/" + second["data"]["message_id"], "--as", "bot", "--format", "json", code=1)
        self.cli("--version", profile="not-configured", code=2)

    def test_required_faults_preserve_sample_types_except_injected_breakage(self):
        for command, kind in (("send", "missing_message_id"), ("send", "missing_create_time"), ("send", "nonzero")):
            with self.subTest(command=command, kind=kind):
                self.reset(seeded=False)
                self.update(lambda state: state["faults"].append({"command": command, "kind": kind}))
                out = self.send(code=1 if kind == "nonzero" else 0)
                if kind == "nonzero":
                    self.assertEqual(out, sample("send-reject"))
                    self.assertEqual(self.data()["profiles"][PROFILE]["messages"], [])
                else:
                    self.assertNotIn("message_id" if kind.endswith("message_id") else "create_time", out["data"])
                    self.assertEqual(len(self.data()["profiles"][PROFILE]["messages"]), 1)
                self.assertEqual(self.data()["faults"], [])
        for kind in ("missing_page_token", "other_chat", "non_owner"):
            self.reset()
            self.update(lambda state: state["faults"].append({"command": "native-list", "kind": kind}))
            out = self.listing(self.native_params(size=50))
            if kind == "missing_page_token":
                self.assertTrue(out["data"]["has_more"])
                self.assertNotIn("page_token", out["data"])
            elif kind == "other_chat":
                self.assertNotEqual(out["data"]["items"][0]["chat_id"], sample("send")["data"]["chat_id"])
            else:
                self.assertNotEqual(out["data"]["items"][1]["sender"]["id"], sample("native-list-page2")["data"]["items"][0]["sender"]["id"])
        self.reset()
        self.update(lambda state: state["faults"].append({"command": "native-get", "kind": "missing_create_time"}))
        out = self.cli("api", "GET", "/open-apis/im/v1/messages/" + sample("send")["data"]["message_id"], "--as", "bot", "--format", "json")
        self.assertNotIn("create_time", out["data"]["items"][0])

    def test_real_process_timeouts_before_and_after_send_keep_argv_and_state(self):
        for phase in ("before", "after"):
            self.reset(seeded=False)
            self.update(lambda state: state["faults"].append({"command": "send", "kind": "timeout", "phase": phase, "delaySeconds": 2}))
            argv = ["--profile", PROFILE, "im", "+messages-send", "--as", "bot", "--user-id", sample("list")["data"]["messages"][1]["sender"]["id"],
                    "--text", "超时合成消息", "--idempotency-key", "timeout-key", "--format", "json"]
            p = subprocess.Popen([sys.executable, "-B", "-I", str(FAKE), *argv], cwd=self.base, env=self.env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
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
                p.stdout.close()
                p.stderr.close()
                self.assertFalse(stdout, stdout)
                self.assertFalse(stderr, stderr)
            state = self.data()
            self.assertEqual(state["calls"][-1]["argv"], argv)
            self.assertTrue(state["calls"][-1]["timeoutPending"])
            self.assertEqual(len(state["profiles"][PROFILE]["messages"]), 0 if phase == "before" else 1)
            self.assertEqual(state["faults"], [])

    def test_unknown_commands_args_state_and_missing_environment_fail_closed(self):
        for args in (("auth", "login"), ("profile", "remove"), ("im", "+unknown"), ("--version", "extra"),
                     ("im", "+messages-send", "--as", "user"), ("api", "GET", "/open-apis/im/v1/messages")):
            with self.subTest(args=args):
                self.cli(*args, code=2)
                self.assertEqual(self.data()["calls"][-1]["argv"], ["--profile", PROFILE, *args])
        env = dict(self.env)
        env.pop("LARK_FAKE_STATE")
        self.cli("--version", code=2, env=env)
        original = self.state.read_bytes()
        for raw in (b"not-json", b"[]", b"{}", json.dumps(dict(self.data(), synthetic=False)).encode("utf-8")):
            self.state.write_bytes(raw)
            self.cli("--version", code=2)
            self.assertEqual(self.state.read_bytes(), raw)
        self.state.write_bytes(original)
        for path in ("relative.json", str(SAMPLES / "send.json"), str(self.base / "missing.json")):
            self.cli("--version", code=2, env=dict(self.env, LARK_FAKE_STATE=path))

    def test_concurrent_duplicate_processes_create_only_one_message(self):
        self.reset(seeded=False)
        args = [sys.executable, "-B", "-I", str(FAKE), "--profile", PROFILE, "im", "+messages-send", "--as", "bot", "--user-id",
                sample("list")["data"]["messages"][1]["sender"]["id"], "--text", "并发合成消息", "--idempotency-key", "same", "--format", "json"]
        children = []
        try:
            for _ in range(2):
                children.append(subprocess.Popen(args, cwd=self.base, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8"))
            results = []
            for p in children:
                stdout, stderr = p.communicate(timeout=10)
                self.assertEqual(p.returncode, 0, stderr)
                self.assertFalse(stderr, stderr)
                results.append(json.loads(stdout))
            self.assertEqual(results[0], results[1])
            self.assertEqual(len(self.data()["profiles"][PROFILE]["messages"]), 1)
            self.assertEqual(len(self.data()["calls"]), 2)
        finally:
            for p in children:
                if p.poll() is None:
                    p.kill()
                p.wait(timeout=5)
                p.stdout.close()
                p.stderr.close()

    def test_empty_native_window_and_duplicate_params_do_not_get_silent_defaults(self):
        query = self.native_params()
        out = self.listing(dict(query, start_time="0", end_time="1"))
        self.assertEqual(out["data"], {"has_more": False, "items": [], "page_token": ""})
        self.listing({}, code=2)
        raw = json.dumps(query)[:-1] + ',"page_size":1}'
        self.cli("api", "GET", "/open-apis/im/v1/messages", "--as", "bot", "--params", raw, "--format", "json", code=2)

    def test_malformed_tokens_and_fault_mismatches_preserve_state(self):
        original = self.state.read_bytes()
        mutations = [lambda s: s.update(tokens={"bad": {"signature": "x", "ids": [], "offset": "invalid"}}),
                     lambda s: s["faults"].append({"command": "send", "kind": "missing_page_token"}),
                     lambda s: s["faults"].append({"command": "unknown", "kind": "timeout"}),
                     lambda s: s["profiles"][PROFILE].update(auxiliary={"contacts": []})]
        for mutate in mutations:
            self.state.write_bytes(original)
            self.update(mutate)
            before = self.state.read_bytes()
            self.cli("--version", code=2)
            self.assertEqual(self.state.read_bytes(), before)

    def test_fake_lark_and_samples_are_not_product_dependencies(self):
        p = subprocess.run([sys.executable, "-B", "-I", str(ROOT / "scripts/build.py"), "--out", str(self.base / "dist")],
                           cwd=self.base, env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for package in map(Path, json.loads(p.stdout)["built"]):
            self.assertFalse((package / "tests").exists())
            self.assertFalse(any(item.name in ("lark_cli.py", "lark_samples") for item in package.rglob("*")))
        self.assertEqual(self.data()["calls"], [])

    def test_unicode_profile_arguments_are_exact_not_normalized_or_defaulted(self):
        name = "合成 配置 & % '🌱"
        self.update(lambda state: state["profiles"].update({name: state["profiles"].pop(PROFILE)}))
        self.cli("--version", profile=name)
        self.assertEqual(self.data()["calls"][-1]["argv"], ["--profile", name, "--version"])
        self.cli("--version", profile=PROFILE, code=2)

    def test_successful_send_followed_by_get_failure_has_no_automatic_resend(self):
        self.reset(seeded=False)
        sent = self.send()
        self.update(lambda state: state["faults"].append({"command": "native-get", "kind": "nonzero"}))
        self.cli("api", "GET", "/open-apis/im/v1/messages/" + sent["data"]["message_id"], "--as", "bot", "--format", "json", code=1)
        state = self.data()
        self.assertEqual([c["command"] for c in state["calls"]], ["send", "native-get"])
        self.assertEqual(len(state["profiles"][PROFILE]["messages"]), 1)
        self.assertEqual(state["profiles"][PROFILE]["receipts"][sent["data"]["message_id"]], sent["data"])

    def test_version_and_exact_native_get_sample(self):
        self.cli("--version")
        mid = sample("send")["data"]["message_id"]
        out = self.cli("api", "GET", "/open-apis/im/v1/messages/" + mid, "--as", "bot", "--format", "json")
        self.assertEqual(out, sample("native-get"))
        self.assertEqual(self.data()["calls"][0]["argv"], ["--profile", PROFILE, "--version"])

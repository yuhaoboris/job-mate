"""T9.3：IM 草稿校验、事务与替身收发。不是 T9.4 Agent 行为检查。"""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_workspace
import test_im_lark

W, L = test_workspace.workspace, test_workspace.ledger
N, A = test_im_lark.notify, test_im_lark.im_lark
ROOT = Path(__file__).resolve().parents[1]


class SetupImRecordTest(unittest.TestCase):
    def setUp(self):
        self.f = test_workspace.WorkspaceIsolationTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        _, backup = self.f.work("backup")
        rc, out = self.f.work("progress", "start", "--mode", "partial", "--select", "im", "--base-backup", backup["backup"])
        self.assertEqual(rc, 0, out)
        self.draft = self.f.ws / W.PROGRESS_FILE
        self.formal = (self.f.ws / "profile.json").read_bytes()
        self.good = {"channel": "lark", "ownerId": "合成本人ID", "larkProfile": "本人选择的配置档", "waitMinutes": 60}

    def record(self, value, status="changed", raw=False):
        return self.f.work("progress", "record", "im", "--status", status,
                           "--value", value if raw else json.dumps(value, ensure_ascii=False))

    def test_all_statuses_validate_im_objects_without_running_tools(self):
        with patch.object(subprocess, "run", side_effect=AssertionError("进度不执行工具")), \
                patch.object(A, "resolve_lark_command", side_effect=AssertionError("进度不定位工具")):
            for status in ("changed", "kept", "default", "skipped"):
                for value in ({"channel": "none"}, self.good,
                              {**self.good, "waitMinutes": 0}, {**self.good, "waitMinutes": 240}):
                    with self.subTest(status=status, value=value):
                        rc, out = self.record(value, status)
                        self.assertEqual(rc, 0, out)
                        self.assertIsNone(out["current"])
                        self.assertEqual(out["answers"]["im"]["value"], value)
                        self.assertEqual(self.f.work("progress", "show")[1], out)
                        self.assertEqual((self.f.ws / "profile.json").read_bytes(), self.formal)

    def test_invalid_im_values_leave_answer_and_formal_bytes_unchanged(self):
        bad = [None, [], True, "lark", {}, {"channel": "other"}, {"channel": "none", "ownerId": "wrong"},
               {"channel": "lark"}, {"channel": "lark", "ownerId": "x"}, {"channel": "lark", "larkProfile": "x"},
               {**self.good, "ownerId": " "}, {**self.good, "ownerId": 1},
               {**self.good, "larkProfile": ""}, {**self.good, "extra": True}]
        bad += [{**self.good, "waitMinutes": v} for v in (True, False, -1, 241, 1.0, "60", None)]
        bad += [{**self.good, "larkCommand": v} for v in (None, [], "node", [""], [" "], [1], ["node", False])]
        for status in ("changed", "kept", "default", "skipped"):
            for value in bad:
                if status == "skipped" and value is None:
                    continue
                before = self.draft.read_bytes()
                with self.subTest(status=status, value=value):
                    rc, out = self.record(value, status)
                    self.assertEqual(rc, 2, out)
                    self.assertEqual(self.draft.read_bytes(), before)
                    self.assertEqual((self.f.ws / "profile.json").read_bytes(), self.formal)
        for raw in ('{"channel":"none","channel":"none"}', '{"channel":"lark","waitMinutes":NaN}',
                    '{"channel":"lark","waitMinutes":1e999}'):
            before = self.draft.read_bytes()
            rc, out = self.record(raw, raw=True)
            self.assertEqual(rc, 2, out)
            self.assertEqual(self.draft.read_bytes(), before)

    def test_real_script_record_rejects_bad_value_without_traceback_or_rewrite(self):
        command = [sys.executable, "-B", "-I", str(ROOT / "scripts/workspace.py"), "--workspace", str(self.f.ws),
                   "progress", "record", "im", "--status", "changed", "--value"]
        value = {**self.good, "larkCommand": ["node", "C:\\合成 工具 & %\\run.js"]}
        result = subprocess.run([*command, json.dumps(value, ensure_ascii=False)],
                                capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual((result.returncode, result.stderr), (0, ""), result.stdout)
        self.assertEqual(json.loads(result.stdout)["answers"]["im"]["value"], value)
        before = self.draft.read_bytes()
        result = subprocess.run([*command, json.dumps({**value, "waitMinutes": True})],
                                capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual((result.returncode, result.stderr), (2, ""), result.stdout)
        self.assertIn("im", json.loads(result.stdout)["error"])
        self.assertEqual(self.draft.read_bytes(), before)
        self.assertEqual((self.f.ws / "profile.json").read_bytes(), self.formal)

    def test_skip_without_value_is_no_change_not_a_none_or_verified_route(self):
        rc, out = self.f.work("progress", "record", "im", "--status", "skipped")
        self.assertEqual(rc, 0, out)
        self.assertIsNone(out["answers"]["im"]["value"])
        self.assertEqual(self.f.work("progress", "show")[1], out)
        self.assertEqual((self.f.ws / "profile.json").read_bytes(), self.formal)
        for status in ("changed", "kept", "default"):
            before = self.draft.read_bytes()
            self.assertEqual(self.f.work("progress", "record", "im", "--status", status)[0], 2)
            self.assertEqual(self.draft.read_bytes(), before)

    def test_resumed_corrupt_im_answer_is_not_treated_as_confirmed(self):
        draft = json.loads(self.draft.read_text(encoding="utf-8"))
        for value in ("old-unstructured-answer", None, {"channel": "lark"}, {**self.good, "larkCommand": []}):
            draft["answers"]["im"] = {"status": "changed", "value": value}
            self.draft.write_text(json.dumps(draft), encoding="utf-8")
            before = self.draft.read_bytes()
            rc, out = self.f.work("progress", "show")
            self.assertEqual(rc, 2, out)
            self.assertIn("草稿损坏", out["error"])
            self.assertEqual(self.draft.read_bytes(), before)
            self.assertEqual((self.f.ws / "profile.json").read_bytes(), self.formal)

    def test_command_roundtrip_is_literal_and_does_not_probe_executable(self):
        commands = [["node", "C:\\合成 工具 & %\\固定入口.js"],
                    ["/synthetic/tool path/lark-cli"],
                    ["node", "/synthetic/e\u0301 中文 & 引号' /run.js"]]
        with patch.object(A, "resolve_lark_command", side_effect=AssertionError("不得提前定位")):
            for command in commands:
                value = {**self.good, "larkCommand": command}
                rc, out = self.record(value)
                self.assertEqual(rc, 0, out)
                self.assertEqual(out["answers"]["im"]["value"]["larkCommand"], command)
                self.assertEqual(self.f.work("progress", "show")[1]["answers"]["im"]["value"], value)

    def test_persisted_node_prefix_runs_real_offline_literal_argv(self):
        node = shutil.which("node")
        if node is None:
            if os.environ.get("CI"):
                self.fail("CI 必须提供离线 Node")
            self.skipTest("本机没有 Node；不安装、不联网")
        script = Path(self.f.tmp.name) / "合成 工具 & %" / "固定 echo.js"
        script.parent.mkdir()
        shutil.copyfile(ROOT / "tests/fakes/argv_echo.js", script)
        value = {**self.good, "larkCommand": [node, str(script)]}
        self.assertEqual(self.record(value)[0], 0)
        saved = self.f.work("progress", "show")[1]["answers"]["im"]["value"]
        # 正式配置仍经事务写入；只测试保存前缀与真实 Node 的 argv 忠实度，不冒充 lark-cli。
        stage = Path(self.f.tmp.name) / "staging"
        stage.mkdir()
        profile = json.loads(self.formal)
        profile["im"] = saved
        (stage / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
        self.assertEqual(self.f.led("lock", "acquire", "--skill", "setup", "--run", "im-node")[0], 0)
        self.assertEqual(self.f.work("commit", "--run", "im-node", "--staging", str(stage))[0], 0)
        cfg = N.context(self.f.ws)["cfg"]
        self.assertEqual(cfg["larkCommand"], value["larkCommand"])
        prefix = A.resolve_lark_command(cfg)
        sentinel = Path(self.f.tmp.name) / "must-not-exist"
        arguments = ["--profile", saved["larkProfile"], "引号\" ' & %PATH% ^ |", "中文 e\u0301 🌱", "line1\nline2",
                     "trailing\\", f"$(touch {sentinel})", ""]
        out = A.run_lark(prefix, arguments)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), arguments)
        self.assertFalse(sentinel.exists())
        self.assertEqual(self.f.led("lock", "release", "--run", "im-node")[0], 0)


class SetupImFlowTest(unittest.TestCase):
    def setUp(self):
        self.f = test_im_lark.ImLarkTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        # 补齐正式配置和合成标记；不使用不完整 owner 让 doctor 提前通过。
        self.profile = copy.deepcopy(test_workspace.PROFILE)
        self.profile["authorization"] = {"apply": {"send": False}, "followup": {"reply": "L1", "nudge": False}}
        self.profile["im"] = {"channel": "none"}
        (self.f.ws / "profile.json").write_text(json.dumps(self.profile), encoding="utf-8")
        marker = L.read_json(self.f.ws / L.MARKER)
        marker["accounts"]["boss"]["displayName"] = "合成账号"
        (self.f.ws / L.MARKER).write_text(json.dumps(marker), encoding="utf-8")
        backup = self.f.invoke(W, "backup")
        self.f.invoke(W, "progress", "start", "--mode", "partial", "--select", "im", "--base-backup", backup["backup"])
        self.im = {"channel": "lark", "ownerId": test_im_lark.OWNER, "larkProfile": test_im_lark.PROFILE,
                   "waitMinutes": 0, "larkCommand": [sys.executable, str(test_im_lark.FAKE)]}

    def record_and_commit(self, im):
        before = (self.f.ws / "profile.json").read_bytes()
        out = self.f.invoke(W, "progress", "record", "im", "--status", "changed", "--value", json.dumps(im))
        self.assertEqual(out["answers"]["im"]["value"], im)
        self.assertEqual((self.f.ws / "profile.json").read_bytes(), before)
        self.f.invoke(L, "lock", "acquire", "--skill", "setup", "--run", "setup-im")
        self.profile["im"] = copy.deepcopy(im)
        stage = self.f.base / "staging"
        stage.mkdir(exist_ok=True)
        (stage / "profile.json").write_text(json.dumps(self.profile), encoding="utf-8")
        out = self.f.invoke(W, "commit", "--run", "setup-im", "--staging", str(stage))
        self.assertFalse((self.f.ws / ".job-mate-txn.json").exists())
        self.assertEqual(L.load_profile(self.f.ws)["im"], im)
        self.f.invoke(L, "lock", "release", "--run", "setup-im")
        return out

    def test_readonly_selection_then_commit_doctor_and_explicit_quoted_test(self):
        profile_name = test_im_lark.PROFILE
        # profile/auth 源于现有实测形状；联系人只给明确合成响应，不声称真实兼容。
        query = ["+search-user", "--query", "本人给出的合成姓名", "--as", "user", "--format", "json"]
        contacts = {"synthetic": True, "candidates": [{"open_id": "ou_" + "9" * 32, "name": "同名候选"},
                                                     {"open_id": test_im_lark.OWNER, "name": "本人确认的合成姓名"}]}
        def configure(state):
            aux = state["profiles"][profile_name]["auxiliary"]
            selected = copy.deepcopy(next(p for p in aux["profileList"] if p["name"] == profile_name))
            selected["active"] = False
            aux["profileList"] = [{"name": "not-selected", "appId": "cli_" + "f" * 16, "active": True}, selected]
            aux["contacts"][json.dumps(query, ensure_ascii=False, separators=(",", ":"))] = contacts
        self.f.update_state(configure)
        prefix = A.resolve_lark_command({"larkCommand": self.im["larkCommand"]})
        listed = A.run_lark(prefix, ["--profile", profile_name, "profile", "list"])
        self.assertEqual(listed.returncode, 0, listed.stderr)
        profiles = json.loads(listed.stdout)
        self.assertEqual(profiles[1]["name"], profile_name)  # 合成本人选择第二项，不猜 active。
        found = A.run_lark(prefix, ["--profile", profile_name, "contact", *query])
        self.assertEqual(found.returncode, 0, found.stderr)
        shown = json.loads(found.stdout)["candidates"]
        self.assertEqual(len(shown), 2)
        self.assertEqual(shown[1]["name"], "本人确认的合成姓名")
        self.assertEqual(shown[1]["open_id"], self.im["ownerId"])  # 本人明确确认第二项。
        calls = len(self.f.state_data()["calls"])
        skipped = self.f.invoke(A, "doctor")
        self.assertEqual(skipped, {"ok": True, "checks": [], "skipped": "channel_none"})
        self.assertEqual(len(self.f.state_data()["calls"]), calls)
        self.assertEqual(self.f.rows()["out"], {})
        self.record_and_commit(self.im)
        self.assertEqual(L.load_profile(self.f.ws)["authorization"], self.profile["authorization"])
        before = self.f.rows()
        doctor = self.f.invoke(A, "doctor")
        self.assertTrue(doctor["ok"])
        self.assertNotIn("skipped", doctor)
        self.assertEqual(doctor["checks"][-1], {"name": "bot", "ok": True})
        self.assertEqual(self.f.rows(), before)  # doctor 不排队、不发送。
        self.assertFalse(any(c["command"] in ("send", "reply") for c in self.f.state_data()["calls"]))
        # 合成本人此刻才同意本次测试内容，不把这一步变成长期授权。
        queued = self.f.invoke(N, "test")
        self.assertEqual(queued["status"], "queued")
        key = queued["key"]
        self.assertFalse(self.f.poll()["readable"])
        sent = self.f.sync()
        self.assertEqual(sent["sent"], 1)
        self.assertTrue(self.f.poll()["readable"])
        test_message = self.f.rows()["out"][key]["messageId"]
        # 首次 full sync 已完整扫到当时的上界；新回复必须确实晚于该窗。
        os.environ["JOB_MATE_NOW"] = datetime.fromtimestamp((test_im_lark.ANCHOR + 180000) / 1000, timezone.utc).isoformat()
        quoted = self.f.message(100, "收到", created=test_im_lark.ANCHOR + 121000)
        quoted.update(parent_id=test_message, root_id=test_message)
        ordinary = self.f.message(101, "合成业务答复", created=test_im_lark.ANCHOR + 121001)
        self.f.add_messages(quoted, ordinary)
        self.f.update_state(lambda state: state["profiles"][profile_name].update(nativeClock=str(test_im_lark.ANCHOR + 180000)))
        result = self.f.sync()
        self.assertEqual([r["state"] for r in result["incoming"]["new"]], ["test", "received"])
        replies = self.f.invoke(N, "replies", "--state", "test")
        self.assertEqual(len(replies["replies"]), 1)
        status = self.f.invoke(N, "status")
        self.assertTrue(status["chatEstablished"])
        self.assertTrue(status["readable"])
        self.assertEqual(status["routeConflicts"], [])
        self.assertIn(ordinary["message_id"], [r["messageId"] for r in status["pendingReplies"]])
        self.f.sync()
        self.assertEqual(self.f.rows()["out"][key]["attempts"], 1)
        self.assertEqual(L.load_profile(self.f.ws)["authorization"], self.profile["authorization"])

    def test_none_and_bad_doctor_do_not_queue_or_send_test(self):
        self.record_and_commit({"channel": "none"})
        with patch.object(A, "resolve_lark_command", side_effect=AssertionError("none 不定位")):
            out = self.f.invoke(A, "doctor")
        self.assertEqual(out, {"ok": True, "checks": [], "skipped": "channel_none"})
        self.assertEqual(self.f.state_data()["calls"], [])
        self.record_and_commit(self.im)
        self.f.update_state(lambda state: state["profiles"][test_im_lark.PROFILE]["auxiliary"]["authStatus"]["identities"]["bot"].update(verified=False))
        out = self.f.invoke(A, "doctor", expected=1)
        self.assertFalse(out["ok"])
        self.assertEqual(self.f.rows()["out"], {})
        self.assertFalse(any(c["command"] in ("send", "reply") for c in self.f.state_data()["calls"]))

    def test_stored_command_does_not_bypass_runtime_entry_safety(self):
        # 配置层只验证字符串数组；即使存进正式配置，安全入口仍先拒绝 shell。
        self.record_and_commit({**self.im, "larkCommand": ["sh", "-c", "must-not-run"]})
        with patch.object(subprocess, "run", side_effect=AssertionError("shell 不得执行")):
            out = self.f.invoke(A, "doctor", expected=1)
        self.assertFalse(out["ok"])
        self.assertEqual(out["checks"][0]["error"], "unsafe_executable")
        self.assertEqual(self.f.rows()["out"], {})
        self.assertEqual(self.f.state_data()["calls"], [])

    def test_followup_lookup_failure_keeps_sent_test_without_resend(self):
        self.record_and_commit(self.im)
        self.assertTrue(self.f.invoke(A, "doctor")["ok"])
        key = self.f.invoke(N, "test")["key"]
        self.f.fault("native-get", "nonzero")
        result = self.f.sync()
        self.assertEqual((result["sent"], result["unknown"]), (1, 0))
        self.assertIn("sent_without_trusted_time", result["errors"])
        self.assertFalse(self.f.poll()["readable"])
        self.f.sync()
        self.assertEqual(self.f.rows()["out"][key]["attempts"], 1)
        self.assertEqual(len([c for c in self.f.state_data()["calls"] if c["command"] == "send"]), 1)
        self.assertEqual(self.f.invoke(N, "replies", "--state", "test")["replies"], [])

    def test_skill_has_formal_configuration_and_consent_order(self):
        skill = (ROOT / "shared/im/lark-setup.md").read_text(encoding="utf-8")
        text = skill.split("## 配置步骤\n", 1)[1]
        for term in ("只读列配置档", "不猜 active", "多候选让本人选", "展示姓名请本人确认 ownerId",
                     "事务提交后", "正式工作区配置", "不填假 ownerId", "不绕事务", "不算机器人验证成功",
                     "另行同意测试内容", "结构通过不证明身份或入口可用", "补查失败不重发", "普通业务答复仍 received"):
            self.assertIn(term, text)
        order = ["只读列配置档", "确认 ownerId", "最后问 waitMinutes", "暂存并 commit", "im_lark.py doctor",
                 "另行同意测试内容", "notify test", "im_lark.py sync", "引用测试消息回复「收到」", "notify status"]
        positions = [text.index(term) for term in order]
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()

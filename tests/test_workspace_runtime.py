"""纯配置与旧草稿回归；从 test_desktop 迁入，不依赖桌面工具。"""
import ast
import contextlib
import copy
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import workspace
import ledger
from test_workspace import PROFILE


def call(main, *argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(list(argv))
    return code, json.loads(out.getvalue())


class BrowserWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "jobs"
        call(workspace.main, "init", str(self.ws), "--boss-account", "demo", "--boss-name", "示例账号")
        self.profile = copy.deepcopy(PROFILE)
        self.save_profile()
        (self.ws / "candidate.md").write_text("# 个人事实\n" + "示例经历，仅供合成测试。" * 10)
        (self.ws / "rules.md").write_text("# 规则\n")
        (self.ws / "stoplist.yaml").write_text("# 无\n")
        (self.ws / "qa.yaml").write_text("[]\n")
        (self.ws / "resumes/a.pdf").write_text("合成附件")
        (self.ws / "resumes/index.yaml").write_text(
            "default: a\nversions:\n  - id: a\n    file: a.pdf\n    use: 默认\n    uploaded: {boss: a.pdf}\n")

    def tearDown(self):
        self.tmp.cleanup()

    def save_profile(self):
        (self.ws / "profile.json").write_text(json.dumps(self.profile))

    def work(self, *argv):
        return call(workspace.main, "--workspace", str(self.ws), *argv)

    def led(self, *argv):
        return call(ledger.main, "--workspace", str(self.ws), *argv)

    def partial(self):
        _, backup = self.work("backup")
        return self.work("progress", "start", "--mode", "partial", "--select", "browser",
                         "--base-backup", backup["backup"])

    def test_browser_configuration_all_cases(self):
        valid = ({"control": "agent"}, {"control": "agent-desktop", "input": "background"},
                 {"control": "agent-desktop", "input": "foreground"})
        invalid = (None, {}, [], "agent", {"input": "background"}, {"control": "other"},
                   {"control": "agent", "input": "background"}, {"control": "agent", "extra": 1},
                   {"control": "agent-desktop"}, {"control": "agent-desktop", "input": "readonly"},
                   {"control": "agent-desktop", "input": "background", "extra": 1},
                   {"control": None}, {"control": []}, {"control": "agent-desktop", "input": []})
        self.assertEqual(self.work("check")[0], 0)
        self.assertFalse(any(w.get("field") == "browser" for w in self.work("check")[1]["warnings"]))
        for value in (*valid, *invalid):
            self.profile["browser"] = value
            self.save_profile()
            code, out = self.work("check")
            self.assertEqual(code, 0 if value in valid else 1, out)
            self.assertEqual(any(p.get("field", "").startswith("browser") for p in out["problems"]),
                             value not in valid)

    def test_check_env_rejected_without_execution(self):
        for value in (None, {"control": "agent"}, {"control": "agent-desktop"},
                      {"control": "agent-desktop", "input": "background"}):
            self.profile["browser"] = value
            self.save_profile()
            before = (self.ws / "profile.json").read_bytes()
            with mock.patch("subprocess.Popen", side_effect=AssertionError("不得执行外部程序")), \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                with self.assertRaises(SystemExit) as rejected:
                    self.work("check", "--env")
            self.assertEqual(rejected.exception.code, 2)
            self.assertIn("--env", err.getvalue())
            self.assertEqual((self.ws / "profile.json").read_bytes(), before)
        del self.profile["browser"]
        self.save_profile()
        with mock.patch("subprocess.Popen", side_effect=AssertionError("不得执行外部程序")):
            self.assertEqual(self.work("check")[0], 0)

    def test_new_browser_after_account_not_preanswered(self):
        ids = [s["id"] for s in workspace.STEPS]
        self.assertEqual(ids[ids.index("account") + 1], "browser")
        _, out = self.work("progress", "start", "--mode", "new", "--variant", "typical")
        self.assertIn("browser", out["plan"])
        self.assertNotIn("browser", out["answers"])
        self.assertNotIn("browser", self.profile)

    def test_browser_partial_skip_keep_and_resume_do_not_write_profile(self):
        for original in (None, {"control": "agent"}, {"control": "agent-desktop", "input": "foreground"}):
            if original is None:
                self.profile.pop("browser", None)
            else:
                self.profile["browser"] = original
            self.save_profile()
            for status in ("skipped", "kept", "changed"):
                before = (self.ws / "profile.json").read_bytes()
                code, out = self.partial()
                self.assertEqual(code, 0, out)
                self.assertEqual(out["plan"], ["browser"])
                self.assertEqual(self.work("progress", "show")[1]["current"], "browser")
                code, out = self.work("progress", "record", "browser", "--status", status,
                                      "--value", '{"control":"agent"}')
                self.assertEqual(code, 0, out)
                self.assertIsNone(out["current"])
                self.assertEqual(self.work("progress", "show")[1]["answers"]["browser"]["status"], status)
                self.assertEqual((self.ws / "profile.json").read_bytes(), before)
                self.assertEqual(self.work("progress", "finish")[0], 0)
                self.assertEqual((self.ws / "profile.json").read_bytes(), before)

    def test_old_draft_keeps_plan_rejects_browser_then_finishes(self):
        self.work("progress", "start", "--mode", "new", "--variant", "custom")
        path = self.ws / workspace.PROGRESS_FILE
        draft = json.loads(path.read_text())
        draft["plan"].remove("browser")
        path.write_text(json.dumps(draft))
        original = path.read_bytes()
        for argv in (("goto", "browser"), ("record", "browser", "--status", "skipped")):
            code, out = self.work("progress", *argv)
            self.assertEqual(code, 2)
            self.assertIn("部分重新设置", out["error"])
            self.assertEqual(path.read_bytes(), original)
        for step in draft["plan"]:
            self.assertEqual(self.work("progress", "record", step, "--status", "kept")[0], 0)
        self.assertNotIn("browser", self.work("progress", "show")[1]["plan"])
        self.assertEqual(self.work("progress", "finish")[0], 0)
        self.assertNotIn("browser", json.loads((self.ws / "profile.json").read_text()))

    def test_core_has_no_external_execution_or_desktop_import(self):
        source = Path(workspace.__file__)
        tree = ast.parse(source.read_text())
        imports = {n.name for node in ast.walk(tree) if isinstance(node, ast.Import) for n in node.names}
        imports.update(node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom))
        self.assertFalse({"desktop", "subprocess"} & imports)
        self.assertFalse(any(n and n.startswith("integrations") for n in imports))
        # 仅拷贝两个数据脚本，验证脱离 desktop 模块仍能加载、体检和拒绝旧选项。
        scripts = Path(self.tmp.name) / "isolated"
        scripts.mkdir()
        for name in ("workspace.py", "ledger.py"):
            shutil.copy2(source.parent / name, scripts / name)
        for tail, expected in ((["check"], 0), (["check", "--env"], 2)):
            result = subprocess.run([sys.executable, "-I", str(scripts / "workspace.py"),
                                     "--workspace", str(self.ws), *tail],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stderr)
            self.assertNotIn("ModuleNotFoundError", result.stderr)
            if expected == 0:
                self.assertNotIn("desktop", json.loads(result.stdout)["info"])
            else:
                self.assertIn("--env", result.stderr)

    def test_gitignore_uses_only_filesystem_markers(self):
        root = Path(self.tmp.name)
        # 普通仓库与 worktree 的标记都不需要执行 git。
        for file_marker in (False, True):
            marker = root / ".git"
            if file_marker:
                marker.write_text("gitdir: /synthetic/metadata\n")
            else:
                marker.mkdir()
            try:
                with mock.patch("subprocess.Popen", side_effect=AssertionError("不得执行 git")):
                    self.assertEqual(workspace.git_root(self.ws), root.resolve())
                    workspace.ensure_gitignore(self.ws)
                self.assertIn("/jobs/setup-progress.json", (root / ".gitignore").read_text())
            finally:
                marker.unlink() if file_marker else marker.rmdir()
        self.assertIsNone(workspace.git_root(self.ws))

    def test_runtime_im_valid_transport_is_opaque_and_not_rewritten(self):
        for transport in ({}, {"idempotencyWindowSeconds": 0},
                          {"idempotencyWindowSeconds": 86400},
                          {"kind": "fake", "nested": [None, True, {"profile": "legacy"}]}):
            self.profile["im"] = {"channel": "runtime", "ownerId": "synthetic-owner",
                                  "transport": transport}
            self.save_profile()
            before = (self.ws / "profile.json").read_bytes()
            code, out = self.work("check")
            self.assertEqual(code, 0, out)
            self.assertFalse(any(w.get("field", "").startswith("im") for w in out["warnings"]))
            self.assertEqual((self.ws / "profile.json").read_bytes(), before)
        self.profile["im"] = {"channel": "runtime", "ownerId": "synthetic-owner"}
        self.save_profile()
        before = (self.ws / "profile.json").read_bytes()
        self.assertEqual(self.work("check")[0], 0)
        self.assertEqual((self.ws / "profile.json").read_bytes(), before)
        self.assertNotIn("transport", json.loads(before)["im"])

    def test_runtime_im_requires_strict_owner_and_transport(self):
        cases = [({"channel": "runtime"}, "im.ownerId")]
        cases += [({"channel": "runtime", "ownerId": value}, "im.ownerId")
                  for value in (None, "", "  ", 42, True, [], {})]
        cases += [({"channel": "runtime", "ownerId": "owner", "transport": value}, "im.transport")
                  for value in (None, [], "opaque", 0, False)]
        cases += [({"channel": "runtime", "ownerId": "owner",
                    "transport": {"idempotencyWindowSeconds": value}},
                   "im.transport.idempotencyWindowSeconds")
                  for value in (None, False, True, -1, 86401, 1.0, "3600", [], {})]
        cases += [({"channel": value}, "im.channel") for value in (None, [], {}, 5, "other")]
        cases += [(None, "im"), ([], "im")]
        for im, field in cases:
            with self.subTest(im=im):
                self.profile["im"] = im
                self.save_profile()
                code, out = self.work("check")
                self.assertEqual(code, 1, out)
                self.assertIn(field, {p.get("field") for p in out["problems"]})

    def test_runtime_transport_rejects_non_json_data(self):
        for value in ({"opaque": {1, 2}}, {"opaque": float("nan")}, {"opaque": float("inf")}):
            problems, warnings = [], []
            workspace.check_profile(dict(self.profile, im={"channel": "runtime", "ownerId": "owner",
                                                          "transport": value}), problems, warnings)
            self.assertIn("im.transport", {p["field"] for p in problems})

    def test_legacy_lark_only_warns_and_preserves_profile_fields(self):
        self.profile["im"] = {"channel": "lark", "userId": "ou_synthetic",
                              "larkProfile": "legacy-test", "profile": {"custom": "retained"}}
        self.profile["larkProfile"] = "older-top-level-field"
        self.save_profile()
        before = (self.ws / "profile.json").read_bytes()
        code, out = self.work("check")
        self.assertEqual(code, 0, out)
        warning = next(w["warning"] for w in out["warnings"] if w.get("field") == "im.channel")
        self.assertIn("runtime", warning)
        self.assertIn("幂等窗口按 0", warning)
        self.assertEqual((self.ws / "profile.json").read_bytes(), before)
        for user_id in (None, "", "ou_", "ou_ ", 1, True, [], {}):
            self.profile["im"]["userId"] = user_id
            self.save_profile()
            code, out = self.work("check")
            self.assertEqual(code, 1, out)
            self.assertIn("im.userId", {p.get("field") for p in out["problems"]})

    def test_none_channel_and_wait_minutes_are_data_only(self):
        for wait in (0, 60, 240):
            self.profile["im"] = {"channel": "none", "waitMinutes": wait}
            self.save_profile()
            self.assertEqual(self.work("check")[0], 0)
        for wait in (None, True, False, -1, 241, 60.0, "60"):
            self.profile["im"]["waitMinutes"] = wait
            self.save_profile()
            code, out = self.work("check")
            self.assertEqual(code, 1, out)
            self.assertIn("im.waitMinutes", {p.get("field") for p in out["problems"]})

    def test_old_browser_im_listener_answers_resume_without_service_writes(self):
        self.profile["browser"] = {"control": "agent-desktop", "input": "foreground"}
        self.profile["im"] = {"channel": "lark", "userId": "ou_synthetic", "larkProfile": "legacy"}
        self.save_profile()
        before = (self.ws / "profile.json").read_bytes()
        self.work("progress", "start", "--mode", "new", "--variant", "custom")
        path = self.ws / workspace.PROGRESS_FILE
        draft = json.loads(path.read_text())
        legacy_values = {"browser": self.profile["browser"], "im": self.profile["im"],
                         "listener": {"enabled": True, "command": ["obsolete-agent", "--run"]}}
        draft["answers"].update({step: {"status": "changed", "source": "legacy", "value": value}
                                 for step, value in legacy_values.items()})
        path.write_text(json.dumps(draft))
        old_draft = path.read_bytes()
        with mock.patch.dict("os.environ", {"HOME": self.tmp.name}), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("不得执行服务命令")):
            code, shown = self.work("progress", "show")
            self.assertEqual(code, 0)
            self.assertEqual(path.read_bytes(), old_draft)
            self.assertEqual(shown["answers"], draft["answers"])
            self.assertEqual(shown["unapplied"][0]["step"], "listener")
            self.assertIn("未应用", shown["unapplied"][0]["reason"])
            for step in draft["plan"]:
                if step not in draft["answers"]:
                    self.assertEqual(self.work("progress", "record", step, "--status", "kept")[0], 0)
            resumed = self.work("progress", "show")[1]
            for step in legacy_values:
                self.assertEqual(resumed["answers"][step], draft["answers"][step])
            self.assertEqual(resumed["plan"], draft["plan"])
            code, finished = self.work("progress", "finish")
        self.assertEqual(code, 0, finished)
        self.assertTrue(finished["finished"])
        self.assertEqual(finished["unapplied"], shown["unapplied"])
        self.assertEqual((self.ws / "profile.json").read_bytes(), before)
        self.assertFalse((Path(self.tmp.name) / ".config/job-wingman/listener.json").exists())
        self.assertFalse((self.ws / "ledger/listener.json").exists())
        self.assertFalse(path.exists())

    def test_partial_im_migration_keeps_other_domains_and_legacy_fields(self):
        self.profile["browser"] = {"control": "agent-desktop", "input": "background"}
        self.profile["im"] = {"channel": "lark", "userId": "ou_synthetic", "larkProfile": "legacy",
                              "profile": {"old": "keep"}, "waitMinutes": 42}
        self.profile["custom"] = {"retained": True}
        self.save_profile()
        before = copy.deepcopy(self.profile)
        for migrate in (False, True):
            with self.subTest(migrate=migrate):
                _, backup = self.work("backup")
                code, draft = self.work("progress", "start", "--mode", "partial", "--select", "im",
                                        "--base-backup", backup["backup"])
                self.assertEqual(code, 0, draft)
                self.assertEqual(draft["plan"], ["im"])
                answer = dict(self.profile["im"])
                if migrate:
                    answer.update(channel="runtime", ownerId="new-owner", transport={"kind": "fake"})
                self.assertEqual(self.work("progress", "record", "im", "--status",
                                          "changed" if migrate else "kept", "--value", json.dumps(answer))[0], 0)
                # progress 不擅自应用。此写入模拟 setup 经本人确认后仅合并 IM 领域。
                self.assertEqual(json.loads((self.ws / "profile.json").read_text()), before)
                self.profile["im"].update(answer)
                self.save_profile()
                code, finished = self.work("progress", "finish")
                self.assertEqual(code, 0, finished)
                after = json.loads((self.ws / "profile.json").read_text())
                self.assertEqual({k: v for k, v in after.items() if k != "im"},
                                 {k: v for k, v in before.items() if k != "im"})
                for key in ("userId", "larkProfile", "profile", "waitMinutes"):
                    self.assertEqual(after["im"][key], before["im"][key])
                self.assertNotIn("idempotencyWindowSeconds", after["im"].get("transport", {}))

    def test_steps_keep_ids_and_only_listener_display_name_changes(self):
        ids = [s["id"] for s in workspace.STEPS]
        self.assertLess(ids.index("browser"), ids.index("im"))
        self.assertEqual(ids[-2:], ["im", "listener"])
        self.assertEqual(workspace.STEP_BY_ID["listener"]["name"], "无人值守唤起")

    def test_no_send_lock_rejects_pending_and_guard(self):
        _, out = self.led("lock", "acquire", "--skill", "followup")
        run_id = out["lock"]["runId"]
        event = {"platform": "boss", "skill": "followup", "runId": run_id,
                 "kind": "reply", "status": "pending", "company": "示例公司", "reason": "合成测试"}
        code, out = self.led("append", "--json", json.dumps(event))
        self.assertEqual(code, 0, out)
        attempt = out["event"]["attemptId"]
        self.led("lock", "release", "--run", run_id)
        _, out = self.led("lock", "acquire", "--skill", "followup", "--no-send")
        no_send = out["lock"]["runId"]
        event["runId"] = no_send
        self.assertEqual(self.led("append", "--json", json.dumps(event))[0], 2)
        code, out = self.led("guard", "--run", no_send, "--attempt", attempt)
        self.assertEqual(code, 2)
        self.assertIn("禁发", out["error"])

    def test_reacquire_same_run_never_changes_no_send(self):
        for no_send in (False, True):
            argv = ["lock", "acquire", "--skill", "apply"] + (["--no-send"] if no_send else [])
            _, out = self.led(*argv)
            run_id = out["lock"]["runId"]
            code, out = self.led("lock", "acquire", "--skill", "apply", "--run", run_id,
                                  *([] if no_send else ["--no-send"]))
            self.assertEqual(code, 0)
            self.assertTrue(out["reused"])
            self.assertEqual(out["lock"]["noSend"], no_send)
            self.led("lock", "release", "--run", run_id)


"""setup 两项 P2：固定时钟、临时工作区、真实 Python CLI，无招聘/IM 外发。"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import test_workspace

W, L = test_workspace.workspace, test_workspace.ledger
ROOT = Path(__file__).resolve().parents[1]
BAD_VERSIONS = (2, 0, 3, True, False, 1.0, "1", None)


class SetupReviewTest(unittest.TestCase):
    def setUp(self):
        self.f = test_workspace.WorkspaceTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.f.init()
        self.f.fill()
        self.env = dict(os.environ, JOB_MATE_TESTING="1", JOB_MATE_NOW="2026-11-04T12:00:00+08:00",
                        PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")

    def cli(self, module, *arguments):
        result = subprocess.run([sys.executable, "-B", "-I", str(ROOT / ("scripts/" + module + ".py")),
                                 "--workspace", str(self.f.ws), *arguments],
                                capture_output=True, encoding="utf-8", env=self.env, timeout=30)
        self.assertEqual(result.stderr, "", result.stderr)
        return result.returncode, json.loads(result.stdout)

    def ok(self, module, *arguments):
        code, result = self.cli(module, *arguments)
        self.assertEqual(code, 0, result)
        return result

    def profile(self, version):
        p = copy.deepcopy(test_workspace.PROFILE)
        p["schemaVersion"] = version
        return p

    def save(self, profile):
        raw = json.dumps(profile, ensure_ascii=False).encode("utf-8")
        (self.f.ws / "profile.json").write_bytes(raw)
        return raw

    def config(self):
        return {name: (self.f.ws / name).read_bytes() for name in W.CONFIG_FILES}

    def events(self):
        path = self.f.ws / "ledger/events.jsonl"
        return path.read_bytes() if path.exists() else None

    def test_profile_version_check_1_accepts_2_and_unknown_reject_without_rewrite(self):
        for version in (1, *BAD_VERSIONS):
            with self.subTest(version=version):
                raw = self.save(self.profile(version))
                code, out = self.cli("workspace", "check")
                self.assertEqual(code, 0 if type(version) is int and version == 1 else 1, out)
                self.assertEqual(out["ok"], type(version) is int and version == 1)
                if code:
                    self.assertIn("schemaVersion", {p.get("field") for p in out["problems"]})
                self.assertEqual((self.f.ws / "profile.json").read_bytes(), raw)

    def test_profile_version_commit_1_only_with_no_migration_or_partial_writes(self):
        self.ok("ledger", "lock", "acquire", "--skill", "setup", "--run", "review-commit")
        stage = self.f.root / "staging"
        stage.mkdir()
        for version in (1, *BAD_VERSIONS):
            with self.subTest(version=version):
                before = self.config()
                raw = json.dumps(self.profile(version), ensure_ascii=False).encode("utf-8")
                (stage / "profile.json").write_bytes(raw)
                code, out = self.cli("workspace", "commit", "--staging", str(stage), "--run", "review-commit")
                valid = type(version) is int and version == 1
                self.assertEqual(code, 0 if valid else 2, out)
                self.assertEqual((stage / "profile.json").read_bytes(), raw)
                if valid:
                    self.assertEqual((self.f.ws / "profile.json").read_bytes(), raw)
                else:
                    self.assertEqual(self.config(), before)
                self.assertFalse((self.f.ws / ".job-mate-txn.json").exists())

    def test_profile_version_send_check_and_prepare_refuse_2_before_pending(self):
        self.ok("ledger", "lock", "acquire", "--skill", "apply", "--run", "review-send")
        intent = json.dumps({"platform": "boss", "company": "合成公司", "title": "合成开发岗位",
                             "recruiter": "合成招聘者", "kind": "greet", "targetRole": "前端开发",
                             "reason": "合成依据", "text": "您好，希望进一步沟通。"}, ensure_ascii=False)
        for version in BAD_VERSIONS:
            with self.subTest(version=version):
                raw = self.save(self.profile(version))
                before = self.events()
                for command in ("check", "prepare"):
                    code, out = self.cli("ledger", command, "--run", "review-send", "--json", intent)
                    self.assertEqual(code, 2, out)
                    self.assertIn("schemaVersion", out["error"])
                    self.assertEqual(self.events(), before)
                    self.assertEqual((self.f.ws / "profile.json").read_bytes(), raw)
        self.save(self.profile(1))
        before = self.events()
        self.assertTrue(self.ok("ledger", "check", "--run", "review-send", "--json", intent)["allowed"])
        self.assertEqual(self.events(), before)
        self.assertTrue(self.ok("ledger", "prepare", "--run", "review-send", "--json", intent)["allowed"])
        self.assertEqual(L.read_events(self.f.ws)[0][-1]["schemaVersion"], 1)  # 事件协议原样为1。

    def test_profile_version_other_runtime_consumers_reject_before_none_skip(self):
        for version in (1, *BAD_VERSIONS, "absent"):
            with self.subTest(version=version):
                p = self.profile(version)
                if version == "absent":
                    del p["schemaVersion"]
                p["im"] = {"channel": "none"}  # 此测试永远不会定位或调用 lark-cli。
                raw = self.save(p)
                before = self.events()
                for module, command in (("notify", "status"), ("im_lark", "doctor")):
                    code, out = self.cli(module, command)
                    self.assertEqual(code, 0 if type(version) is int and version == 1 else 2, out)
                self.assertEqual(self.events(), before)
                self.assertEqual((self.f.ws / "profile.json").read_bytes(), raw)

    def start(self, select):
        backup = self.ok("workspace", "backup")
        return self.ok("workspace", "progress", "start", "--mode", "partial", "--select", select,
                       "--base-backup", backup["backup"])

    def record(self, step, value, status="kept"):
        return self.ok("workspace", "progress", "record", step, "--status", status,
                       "--value", json.dumps(value, ensure_ascii=False),
                       "--source", "default" if status == "default" else "current")

    def test_style_medium_70_character_greeting_then_default_requires_reconfirmation(self):
        p = copy.deepcopy(test_workspace.PROFILE)
        p.update(style={"length": "medium"}, greeting={"mode": "static", "text": "合" * 70})
        self.save(p)
        self.ok("workspace", "check")
        before = self.config()
        self.start("style")
        self.record("style", p["style"])
        out = self.record("greeting", p["greeting"])
        self.assertIsNone(out["current"])
        self.ok("workspace", "progress", "goto", "style")
        out = self.record("style", W.STEP_BY_ID["style"]["default"], "default")
        self.assertEqual(out["answers"]["style"]["value"]["length"], "short")
        self.assertEqual(out["current"], "greeting")
        self.assertNotIn("greeting", out["answers"])
        self.assertEqual(out["added"]["greeting"], "style")
        self.assertEqual(self.ok("workspace", "progress", "show"), out)
        self.assertEqual(self.config(), before)

    def test_default_reviews_only_declared_dependents_and_keeps_candidate_facts(self):
        values = {"materials": {"files": []}, "roles": ["合成开发岗位"],
                  "candidate": "合成个人事实保持原样", "qa": {"basis": "candidate.md"},
                  "style": {"length": "medium", "selfIntro": ["本人确认的合成事实"]},
                  "greeting": {"mode": "static", "text": "合" * 70},
                  "inquiry": {"priority": ["salary"], "maxPerMessage": 1}, "replyLevel": "L1",
                  "nudge": {"authorization": {"followup": {"nudge": False}}}, "im": {"channel": "none"}}
        for trigger in ("style", "roles", "candidate"):
            with self.subTest(trigger=trigger):
                before = self.config()
                out = self.start("roles,candidate,qa,style,inquiry,greeting,replyLevel,nudge,im")
                for step in out["plan"]:
                    out = self.record(step, values[step])
                self.assertIsNone(out["current"])
                old = copy.deepcopy(out["answers"])
                # kept 既不引入新复核，也不清除已经确认的 greeting。
                kept = self.record(trigger, values[trigger])
                self.assertIsNone(kept["current"])
                self.assertEqual(kept["answers"], old)
                self.ok("workspace", "progress", "goto", trigger)
                default = W.STEP_BY_ID[trigger].get("default", values[trigger])
                out = self.record(trigger, default, "default")
                reviews = set(W.STEP_BY_ID[trigger]["review"])
                for step, answer in old.items():
                    if step in reviews:
                        self.assertNotIn(step, out["answers"])
                        self.assertEqual(out["added"][step], trigger)
                    elif step != trigger:
                        self.assertEqual(out["answers"][step], answer)
                self.assertEqual(out["current"], next(s for s in out["plan"] if s in reviews))
                if trigger != "candidate":
                    self.assertEqual(out["answers"]["candidate"], old["candidate"])
                self.assertEqual(self.config(), before)  # candidate.md/qa.yaml 及全部正式配置字节不动。
                self.ok("workspace", "progress", "clear")


if __name__ == "__main__":
    unittest.main()

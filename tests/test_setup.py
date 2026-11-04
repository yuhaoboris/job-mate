"""T9.2 setup 目录与依赖：临时合成配置，测试脚本而非宣称 Agent 行为验收。"""
import copy
import json
from pathlib import Path
import re
import unittest

import test_workspace

W, L = test_workspace.workspace, test_workspace.ledger
ROOT = Path(__file__).resolve().parents[1]
EXPECTED = ["directory", "materials", "account", "browser", "roles", "locations", "salary", "requirements",
            "exclusions", "companies", "activity", "candidate", "resumes", "qa", "style", "inquiry", "greeting",
            "applyAuth", "replyLevel", "nudge", "pacing", "im"]


class SetupStepsTest(unittest.TestCase):
    def setUp(self):
        self.f = test_workspace.WorkspaceTest()
        self.f.setUp()
        self.f.init()
        self.f.fill()

    def tearDown(self):
        self.f.tearDown()

    def test_catalog_has_exact_six_phases_and_22_steps(self):
        self.assertEqual([s["id"] for s in W.STEPS], EXPECTED)
        phases = list(dict.fromkeys(s["phase"] for s in W.STEPS))
        self.assertEqual(phases, ["prepare", "criteria", "facts", "communication", "authorization", "notify"])
        self.assertEqual([s["id"] for s in W.STEPS if s["required"]],
                         ["directory", "account", "roles", "locations", "candidate", "resumes", "greeting"])
        self.assertEqual(W.STEP_BY_ID["candidate"]["review"], ["qa", "style", "greeting"])
        self.assertEqual(W.STEP_BY_ID["roles"]["review"], ["greeting"])
        self.assertEqual(W.STEP_BY_ID["style"]["review"], ["greeting"])

    def test_new_modes_defaults_are_policy_only_and_never_write_formal_profile(self):
        before = (self.f.ws / "profile.json").read_bytes()
        for variant in ("typical", "custom"):
            rc, out = self.f.progress("start", "--mode", "new", "--variant", variant)
            self.assertEqual(rc, 0, out)
            self.assertEqual(out["plan"], EXPECTED)
            self.assertEqual((out["position"], out["total"]), (2, 22))
            defaulted = {k for k, v in out["answers"].items() if v["status"] == "default"}
            self.assertEqual(defaulted, {"browser", "activity", "inquiry", "pacing"} if variant == "typical" else set())
            self.assertNotIn("nudge", out["answers"])
            self.assertNotIn("applyAuth", out["answers"])
            self.assertNotIn("replyLevel", out["answers"])
            self.assertNotIn("style", out["answers"])
            self.assertEqual((self.f.ws / "profile.json").read_bytes(), before)
            self.f.progress("clear")
            self.f.init()
        defaults = L.effective_profile({})
        self.assertEqual(W.STEP_BY_ID["browser"]["default"], defaults["browser"])
        self.assertEqual(W.STEP_BY_ID["inquiry"]["default"], defaults["inquiry"])
        self.assertEqual(W.STEP_BY_ID["style"]["default"], defaults["style"])
        self.assertNotIn("selfIntro", W.STEP_BY_ID["style"]["default"])
        self.assertEqual(W.STEP_BY_ID["replyLevel"]["default"], "L2")
        self.assertEqual(W.STEP_BY_ID["applyAuth"]["default"], {"send": False})
        self.assertEqual(W.STEP_BY_ID["nudge"]["default"],
                         {"authorization": {"followup": {"nudge": True}}, "nudge": defaults["nudge"]})

    def test_partial_style_includes_only_greeting_and_survives_resume(self):
        before = {name: (self.f.ws / name).read_bytes() for name in W.CONFIG_FILES}
        out = self.f.start_reset(select="style")
        self.assertEqual(out["plan"], ["style", "greeting"])
        self.assertEqual(out["current"], "style")
        rc, out = self.f.progress("record", "style", "--status", "changed", "--value", '{"tone":"concise"}')
        self.assertEqual(rc, 0, out)
        self.assertEqual((out["current"], out["position"], out["total"]), ("greeting", 2, 2))
        self.assertEqual(self.f.progress("show")[1], out)
        self.assertEqual(out["added"], {"greeting": "style"})  # 初始依赖也保留带入来源。
        self.assertEqual({name: (self.f.ws / name).read_bytes() for name in W.CONFIG_FILES}, before)
        rc, out = self.f.progress("record", "greeting", "--status", "kept", "--value", '{"mode":"dynamic"}')
        self.assertEqual(rc, 0, out)
        self.assertIsNone(out["current"])

    def test_style_change_invalidates_only_greeting_and_preserves_other_answers(self):
        out = self.f.start_reset(select="style,inquiry,replyLevel,nudge,im")
        self.assertEqual(out["plan"], ["style", "inquiry", "greeting", "replyLevel", "nudge", "im"])
        for step in out["plan"]:
            value = {"channel": "none"} if step == "im" else W.STEP_BY_ID[step].get("default", {"mode": "dynamic"})
            rc, out = self.f.progress("record", step, "--status", "kept", "--value", json.dumps(value), "--source", "current")
            self.assertEqual(rc, 0, out)
        old = copy.deepcopy(out["answers"])
        self.f.progress("goto", "style")
        rc, out = self.f.progress("record", "style", "--status", "changed", "--value", '{"tone":"friendly"}')
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["current"], "greeting")
        self.assertNotIn("greeting", out["answers"])
        self.assertEqual(out["added"], {"greeting": "style"})
        for step in ("inquiry", "replyLevel", "nudge", "im"):
            self.assertEqual(out["answers"][step], old[step])
        self.assertEqual(out["plan"], ["style", "inquiry", "greeting", "replyLevel", "nudge", "im"])

    def test_authorization_phase_uses_reply_level_without_alias(self):
        out = self.f.start_reset(select="authorization")
        self.assertEqual(out["plan"], ["applyAuth", "replyLevel", "nudge", "pacing"])
        before = (self.f.ws / W.PROGRESS_FILE).read_bytes()
        rc, _ = self.f.progress("record", "followupLevel", "--status", "kept", "--value", '"L2"')
        self.assertEqual(rc, 2)
        self.assertEqual((self.f.ws / W.PROGRESS_FILE).read_bytes(), before)
        self.f.progress("clear")
        _, backup = test_workspace.run("--workspace", str(self.f.ws), "backup")
        rc, _ = self.f.progress("start", "--mode", "partial", "--select", "followupLevel", "--base-backup", backup["backup"])
        self.assertEqual(rc, 2)
        self.assertFalse((self.f.ws / W.PROGRESS_FILE).exists())

    def test_partial_style_commit_preserves_other_files_and_authorizations(self):
        p = copy.deepcopy(test_workspace.PROFILE)
        p.update(style={"tone": "polite", "length": "medium", "selfIntro": ["本人确认的合成事实"], "emoji": False},
                 inquiry={"priority": ["employment"], "maxPerMessage": 1},
                 nudge={"perDay": 2}, authorization={"apply": {"send": False}, "followup": {"reply": "L1", "nudge": False}})
        (self.f.ws / "profile.json").write_text(json.dumps(p), encoding="utf-8")
        before = {name: (self.f.ws / name).read_bytes() for name in W.CONFIG_FILES}
        inbox = (self.f.ws / "inbox/decisions.md").read_bytes()
        self.f.start_reset(select="style")
        self.f.progress("record", "style", "--status", "changed", "--value", '{"tone":"concise"}')
        self.f.progress("record", "greeting", "--status", "kept", "--value", json.dumps(p["greeting"]))
        rc, acquired = test_workspace.invoke_data(L.main, "--workspace", str(self.f.ws), "lock", "acquire", "--skill", "setup", "--run", "setup-style")
        self.assertEqual(rc, 0, acquired)
        stage = self.f.root / "staging"
        stage.mkdir()
        p["style"]["tone"] = "concise"
        (stage / "profile.json").write_text(json.dumps(p), encoding="utf-8")
        rc, out = test_workspace.run("--workspace", str(self.f.ws), "commit", "--run", "setup-style", "--staging", str(stage))
        self.assertEqual(rc, 0, out)
        after = json.loads((self.f.ws / "profile.json").read_text(encoding="utf-8"))
        self.assertEqual(after, p)
        for name, raw in before.items():
            if name != "profile.json":
                self.assertEqual((self.f.ws / name).read_bytes(), raw)
        self.assertEqual((self.f.ws / "inbox/decisions.md").read_bytes(), inbox)
        self.assertFalse((self.f.ws / ".job-mate-txn.json").exists())
        self.assertEqual(test_workspace.invoke_data(L.main, "--workspace", str(self.f.ws), "lock", "release", "--run", "setup-style")[0], 0)

    def test_skill_questions_dependencies_and_summary_are_explicit(self):
        skill = (ROOT / "skills/setup/SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("followupLevel", skill)
        for value in ("同一句", "polite：", "friendly：", "concise：", "每条展示依据位置", "请本人确认",
                      "都用默认", "不写空数组", "只复核 selfIntro", "薪资 → 上班时间 → 地点 → 假期",
                      "子项", "前台输入会占用键盘鼠标", "不问工具名", "数值", "最后一次请对方给明确答复",
                      "相互独立", "apply.send", "reply", "nudge", "不选的字段、文件和授权保持原样"):
            self.assertIn(value, skill)
        phases = re.findall(r"^\| `([a-z]+)` ([^|]+) \|", skill, re.MULTILINE)
        self.assertEqual([(key, name.strip()) for key, name in phases],
                         [("prepare", "准备"), ("criteria", "求职条件"), ("facts", "个人事实"),
                          ("communication", "沟通"), ("authorization", "授权与节奏"), ("notify", "通知")])


if __name__ == "__main__":
    unittest.main()

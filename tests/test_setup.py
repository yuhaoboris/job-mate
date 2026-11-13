"""T9.2 setup 目录与依赖：临时合成配置，测试脚本而非宣称 Agent 行为验收。"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import test_workspace
from test_rules import PINNED_DOCUMENT_GUARDS

W, L = test_workspace.workspace, test_workspace.ledger
ROOT = Path(__file__).resolve().parents[1]
EXPECTED = ["directory", "account", "browser", "roles", "locations", "salary", "requirements",
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

    def test_catalog_has_exact_six_phases_and_21_steps(self):
        self.assertEqual([s["id"] for s in W.STEPS], EXPECTED)
        phases = list(dict.fromkeys(s["phase"] for s in W.STEPS))
        self.assertEqual(phases, ["prepare", "criteria", "facts", "communication", "authorization", "notify"])
        self.assertEqual([s["id"] for s in W.STEPS if s["required"]],
                         ["directory", "account", "roles", "locations", "candidate", "resumes", "greeting"])
        self.assertEqual(W.STEP_BY_ID["candidate"]["review"], ["qa", "style", "greeting"])
        self.assertEqual(W.STEP_BY_ID["roles"]["review"], ["greeting"])
        self.assertEqual(W.STEP_BY_ID["style"]["review"], ["greeting"])

    def test_new_defaults_are_policy_only_and_never_write_formal_profile(self):
        before = (self.f.ws / "profile.json").read_bytes()
        rc, out = self.f.progress("start", "--mode", "new")
        self.assertEqual(rc, 0, out)
        self.assertEqual(set(out["plan"]), set(EXPECTED))
        self.assertEqual((out["position"], out["total"]), (2, 8))
        defaulted = {k for k, v in out["answers"].items() if v["status"] == "default"}
        self.assertEqual(defaulted, {"activity", "inquiry", "pacing"})
        for step in ("nudge", "applyAuth", "replyLevel", "style", "browser"):
            self.assertNotIn(step, out["answers"])
        self.assertNotIn("variant", out)
        self.assertEqual((self.f.ws / "profile.json").read_bytes(), before)
        defaults = L.effective_profile({})
        self.assertEqual(W.STEP_BY_ID["browser"]["default"], defaults["browser"])
        self.assertEqual(W.STEP_BY_ID["inquiry"]["default"], defaults["inquiry"])
        self.assertEqual(W.STEP_BY_ID["style"]["default"], defaults["style"])
        self.assertNotIn("selfIntro", W.STEP_BY_ID["style"]["default"])
        self.assertEqual(W.STEP_BY_ID["replyLevel"]["default"], "L2")
        self.assertEqual(W.STEP_BY_ID["applyAuth"]["default"], {"send": False})
        self.assertEqual(W.STEP_BY_ID["nudge"]["default"],
                         {"authorization": {"followup": {"nudge": True}}, "nudge": defaults["nudge"]})

    def test_screen_catalog_and_step_mapping_match_both_documents(self):
        self.assertEqual([s["number"] for s in W.SCREENS], list(range(1, 9)))
        automatic = {"qa", "activity", "inquiry", "pacing"}
        for document in ("skills/setup/SKILL.md", "docs/design.md"):
            source = (ROOT / document).read_text(encoding="utf-8")
            rows = re.findall(r"^\| ([1-8]) \| ([^|]+) \| ([A-Za-z、]+) \|", source, re.MULTILINE)
            self.assertEqual([(int(n), name.strip()) for n, name, _ in rows],
                             [(s["number"], s["name"]) for s in W.SCREENS])
            for number, _, ids in rows:
                self.assertEqual(ids.strip().split("、"),
                                 [s["id"] for s in sorted(W.STEPS, key=lambda x: x["screen"])
                                  if s["screen"] == int(number) and s["id"] not in automatic])
        skill = (ROOT / "skills/setup/SKILL.md").read_text(encoding="utf-8")
        steps = re.findall(r"^\|[^|]*\| `([A-Za-z]+)` \|[^|]*\|[^|]*\|[^|]*\| ([1-8]) \|",
                           skill, re.MULTILINE)
        self.assertEqual([(key, int(n)) for key, n in steps], [(s["id"], s["screen"]) for s in W.STEPS])

    def test_new_screens_advance_as_groups_and_reviews_keep_screen_order(self):
        _, out = self.f.progress("start", "--mode", "new")
        seen = []
        while out["current"]:
            step = out["current"]
            screen = out["currentScreen"]["number"]
            seen.append(screen)
            self.assertEqual(out["position"], screen)
            self.assertEqual(out["total"], 8)
            value = {"channel": "none"} if step == "im" else {"synthetic": step}
            rc, out = self.f.progress("record", step, "--status", "changed", "--value", json.dumps(value))
            self.assertEqual(rc, 0, out)
            self.assertEqual(self.f.progress("show")[1], out)
        self.assertEqual(list(dict.fromkeys(seen)), list(range(2, 9)))
        self.assertIsNone(out["currentScreen"])
        before = copy.deepcopy(out["answers"])
        self.f.progress("goto", "candidate")
        _, out = self.f.progress("record", "candidate", "--status", "changed", "--value", '"合成事实"')
        self.assertEqual(out["label"], "第 2/8 步 · 简历")
        self.assertEqual(out["current"], "qa")
        self.assertEqual(out["plan"], sorted(out["plan"], key=lambda key: W.STEP_BY_ID[key]["screen"]))
        for key in ("qa", "style", "greeting"):
            self.assertNotIn(key, out["answers"])
        for key in ("account", "roles", "browser", "applyAuth", "replyLevel", "nudge", "im"):
            self.assertEqual(out["answers"][key], before[key])
        for step, screen in (("qa", 6), ("style", 6), ("greeting", None)):
            _, out = self.f.progress("record", step, "--status", "kept", "--value", '"复核"')
            self.assertEqual(out["currentScreen"]["number"] if out["currentScreen"] else None, screen)

    def test_new_does_not_read_or_scan_adjacent_job_data(self):
        sibling = self.f.root / "unrelated"
        sibling.mkdir()
        decoy = sibling / "job-wingman.json"
        decoy.write_text('{"private":"synthetic decoy, must not read"}', encoding="utf-8")
        before = decoy.read_bytes()
        original_open, original_scandir = io.open, os.scandir

        def guarded_open(path, *args, **kwargs):
            if not isinstance(path, int):
                self.assertFalse(Path(path).resolve().is_relative_to(sibling))
            return original_open(path, *args, **kwargs)

        def guarded_scandir(path):
            self.assertTrue(Path(path).resolve().is_relative_to(self.f.ws))
            return original_scandir(path)

        with patch("io.open", side_effect=guarded_open), patch("os.scandir", side_effect=guarded_scandir):
            # 使用无标记的全新目录；不读取相邻资料，也不把它作为回答来源。
            self.f.ws = self.f.root / "fresh"
            rc, out = self.f.progress("start", "--mode", "new")
            self.assertEqual(rc, 0, out)
            for step in list(out["plan"]):
                if step in out["answers"]:
                    continue
                value = {"channel": "none"} if step == "im" else "本人本轮合成回答"
                rc, out = self.f.progress("record", step, "--status", "kept", "--value", json.dumps(value))
                self.assertEqual(rc, 0, out)
            self.assertIsNone(out["current"])
            self.assertNotIn("unrelated", json.dumps(out))
            self.assertFalse((self.f.ws / "candidate.md").exists())
        self.assertEqual(decoy.read_bytes(), before)
        self.assertEqual(list(sibling.iterdir()), [decoy])

    def test_setup_interface_and_shared_conversation_contract(self):
        skill = (ROOT / "skills/setup/SKILL.md").read_text(encoding="utf-8")
        for forbidden in ("旧工作区", "已有配置", "导入", "迁移", "上一次求职", "materials", "--variant"):
            self.assertNotIn(forbidden, skill)
        self.assertIn("../../shared/ask.md", skill)
        self.assertIn("本轮主动提供的文件", skill)
        ask = (ROOT / "shared/ask.md").read_text(encoding="utf-8")
        for term in ("AskUserQuestion", "AskQuestion", "request_user_input", "工具已暴露",
                     "ask_user_question", "先查找一次", "最多 4 题", "2–4",
                     "不另加「其他」", "先读", "业务进展与结果", "不出现字段名"):
            self.assertIn(term, ask)
        for name in ("apply", "followup"):
            self.assertIn("../../shared/ask.md", (ROOT / f"skills/{name}/SKILL.md").read_text(encoding="utf-8"))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            W.build_parser().parse_args(["progress", "start", "--mode", "new", "--variant", "custom"])
        self.assertEqual(caught.exception.code, 2)

    def test_dsh_question_contract_and_skip_defaults_match_policy(self):
        ask = (ROOT / "shared/ask.md").read_text(encoding="utf-8")
        setup = (ROOT / "skills/setup/SKILL.md").read_text(encoding="utf-8")
        index = 0
        for term in ("ask_user_question", "questions[]", "`id`", "`question`", "`header`",
                     "options[{label, description}]", "`multi_select`", "本轮工具说明",
                     "否则退回编号纯文本", "卡片空选择 = 跳过本题",
                     "不能当作对平台操作或 Agent 审批请求的批准",
                     "主动打招呼关闭", "有把握的问题直接回答（L2）开启", "对方不回时跟进开启",
                     "首次沟通 apply.send 只能由本人明确同意开启", "本人选择 / 默认",
                     "汇总卡上「确认保存」", "必答项被跳过时说明原因后重问"):
            # 只改读取对象：setup 专用的六项必须在 setup，其余仍必须在 ask。
            self.assertIn(term, setup if 11 <= index <= 16 else ask)
            index += 1
        for placeholder in ("工具名待", "待在 DSH 实测确认", "DeepSeek Harness 的提问卡片工具",
                            "未确认的授权保持待确认", "跳过不等于授权"):
            self.assertNotIn(placeholder, ask)
            for document in PINNED_DOCUMENT_GUARDS["setup_question_terms"]["documents"]:
                if document == "shared/ask.md":
                    continue  # 上方逐项检查原 ask 变量，正向归属也保持不变。
                with self.subTest(placeholder=placeholder, document=document):
                    self.assertNotIn(placeholder, (ROOT / document).read_text(encoding="utf-8"))
        for document in ("docs/design.md", "skills/setup/SKILL.md"):
            text = (ROOT / document).read_text(encoding="utf-8")
            for term in ("本人选择 / 默认", "汇总卡上「确认保存」",
                         "首次沟通 apply.send 只能由本人明确同意开启"):
                self.assertIn(term, text)
            self.assertNotIn("未确认的授权保持待确认", text)

    def test_screen_seven_default_records_preserve_policy_and_do_not_write_profile(self):
        before = (self.f.ws / "profile.json").read_bytes()
        _, out = self.f.progress("start", "--mode", "new")
        expected = {"applyAuth": {"send": False}, "replyLevel": "L2",
                    "nudge": {"authorization": {"followup": {"nudge": True}},
                              "nudge": L.effective_profile({})["nudge"]}}
        for step, value in expected.items():
            self.assertEqual(W.STEP_BY_ID[step]["default"], value)
            rc, out = self.f.progress("record", step, "--status", "default",
                                      "--value", json.dumps(value), "--source", "default")
            self.assertEqual(rc, 0, out)
            self.assertEqual(out["answers"][step]["value"], value)
            self.assertEqual(out["answers"][step]["source"], "default")
        self.assertFalse(out["answers"]["applyAuth"]["value"]["send"])
        self.assertEqual((self.f.ws / "profile.json").read_bytes(), before)

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

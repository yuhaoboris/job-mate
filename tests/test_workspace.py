import ast
import contextlib
import copy
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import workspace  # noqa: E402
import ledger  # noqa: E402

PROFILE = {
    "schemaVersion": 2,
    "targetRoles": ["前端开发"],
    "locations": ["深圳"],
    "salary": {"currency": "CNY", "period": "month", "taxBasis": "pre_tax",
               "min": 15000, "max": None, "matchRule": ["overlap", "lower_bound"]},
    "exclusions": {},
    "authorization": {"apply": {"send": True}, "followup": {"level": "L2"}},
    "greeting": {"mode": "dynamic", "text": "您好，我做了 5 年前端，期待沟通。"},
    "limits": {"greetsPerRun": 10},
    "schedule": {"timezone": "Asia/Shanghai"},
}


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = workspace.main(list(argv))
    return code, json.loads(out.getvalue())


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.ws = self.root / "jobs"

    def tearDown(self):
        self.tmp.cleanup()

    def fill(self):
        (self.ws / "profile.json").write_text(json.dumps(PROFILE, ensure_ascii=False))
        (self.ws / "stoplist.yaml").write_text("- name: 甲公司\n  aliases: []\n  reason: 外包\n")
        (self.ws / "candidate.md").write_text("# 个人事实\n\n" + "五年前端开发经验，主力 Vue。" * 5)
        (self.ws / "qa.yaml").write_text("- id: edu\n  ask: [学历]\n  answer: 本科\n  basis: candidate.md\n")
        (self.ws / "rules.md").write_text("# 规则\n")
        (self.ws / "resumes" / "a.pdf").write_text("pdf")
        (self.ws / "resumes" / "index.yaml").write_text(
            "default: a\nversions:\n  - id: a\n    file: a.pdf\n    use: 默认\n    uploaded: {boss: a}\n")

    def init(self):
        return run("init", str(self.ws), "--boss-account", "zp-1", "--boss-name", "张三")

    def test_init_creates_skeleton_without_overwriting(self):
        code, out = self.init()
        self.assertEqual(code, 0)
        self.assertIn("job-mate.json", out["created"])
        code, out = self.init()
        self.assertEqual(out["created"], [])

    def test_init_writes_suite_version_and_only_recognizes_current_marker(self):
        self.init()
        marker = json.loads((self.ws / workspace.MARKER).read_text())
        self.assertEqual(marker["suiteVersion"], "1.0.0")
        self.assertEqual(marker["schemaVersion"], 1)
        (self.ws / workspace.MARKER).rename(self.ws / "other-marker.json")
        with self.assertRaises(workspace.ledger.LedgerError):
            workspace.ledger.find_workspace(str(self.ws))
        with patch("pathlib.Path.cwd", return_value=self.ws):
            with self.assertRaises(workspace.ledger.LedgerError):
                workspace.ledger.find_workspace(None)

    def progress(self, *args):
        return run("--workspace", str(self.ws), "progress", *args)

    def start_reset(self, mode="partial", select="candidate"):
        _, backup = run("--workspace", str(self.ws), "backup")
        args = ["start", "--mode", mode, "--base-backup", backup["backup"]]
        if mode == "partial":
            args += ["--select", select]
        code, out = self.progress(*args)
        self.assertEqual(code, 0, out)
        return out

    def test_progress_new_custom_and_typical_plans(self):
        """判据 1：新建完整计划、自动回答目录与典型默认值。"""
        self.init()
        for variant in ("custom", "typical"):
            self.init()
            code, out = self.progress("start", "--mode", "new", "--variant", variant)
            self.assertEqual(code, 0, out)
            self.assertEqual(out["plan"], [s["id"] for s in workspace.STEPS])
            self.assertEqual(out["current"], "materials")
            self.assertEqual((out["position"], out["total"]), (2, 19))
            self.assertEqual(out["answers"]["directory"]["value"], str(self.ws.resolve()))
            self.assertEqual(set(out["answers"]), {"directory", "activity", "pacing"}
                             if variant == "typical" else {"directory"})
            if variant == "typical":
                for step in ("activity", "pacing"):
                    self.assertEqual(out["answers"][step]["status"], "default")
                    self.assertEqual(out["answers"][step]["source"], "default")
                    self.assertEqual(out["answers"][step]["value"], workspace.STEP_BY_ID[step]["default"])
            self.progress("clear")

    def test_progress_reset_and_partial_plans(self):
        """判据 1：全量重置、阶段/步骤混选及材料依赖。"""
        self.init()
        self.fill()
        out = self.start_reset("resetup")
        self.assertEqual(out["plan"], [s["id"] for s in workspace.STEPS if s["id"] != "directory"])
        self.assertEqual(out["answers"], {})
        self.progress("clear")
        out = self.start_reset(select="resumes,criteria,candidate,roles")
        self.assertEqual(out["plan"], ["materials", "roles", "locations", "salary", "requirements",
                                      "exclusions", "companies", "activity", "candidate", "resumes"])
        self.progress("clear")
        self.assertEqual(self.start_reset(select="im")["plan"], ["im"])

    def test_progress_start_rejects_invalid_parameters_without_writing(self):
        """判据 2：缺参数、未知 id、备份无效均不生成草稿。"""
        self.init()
        _, backup = run("--workspace", str(self.ws), "backup")
        cases = [
            ["--mode", "new"], ["--mode", "resetup"],
            ["--mode", "partial", "--select", "salary"],
            ["--mode", "resetup", "--base-backup", "missing"],
            ["--mode", "resetup", "--base-backup", "../.."],
            ["--mode", "resetup", "--base-backup", ".."],
            ["--mode", "partial", "--base-backup", backup["backup"]],
            ["--mode", "partial", "--base-backup", backup["backup"], "--select", "unknown"],
            ["--mode", "partial", "--base-backup", backup["backup"], "--select", "salary,unknown"],
        ]
        for args in cases:
            with self.subTest(args=args):
                self.assertEqual(self.progress("start", *args)[0], 2)
                self.assertFalse((self.ws / workspace.PROGRESS_FILE).exists())

    def test_progress_start_existing_returns_summary_without_changes(self):
        """判据 2：已有草稿不覆盖，返回可继续的摘要。"""
        self.init()
        self.progress("start", "--mode", "new", "--variant", "custom")
        path = self.ws / workspace.PROGRESS_FILE
        before = path.read_bytes()
        code, out = self.progress("start", "--mode", "new", "--variant", "typical")
        self.assertEqual(code, 2)
        self.assertEqual(out["progress"]["current"], "materials")
        self.assertEqual(path.read_bytes(), before)

    def test_progress_record_and_show(self):
        """判据 3：记录任意 JSON，推进并可从磁盘接续。"""
        self.init()
        self.progress("start", "--mode", "new", "--variant", "custom")
        code, out = self.progress("record", "materials", "--status", "changed", "--value",
                                  '{"files": ["synthetic.pdf"]}', "--source", "resume")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["current"], "account")
        answer = out["answers"]["materials"]
        self.assertEqual(answer["value"], {"files": ["synthetic.pdf"]})
        self.assertEqual(answer["source"], "resume")
        self.assertIn("at", answer)
        self.assertEqual(self.progress("show")[1], out)
        self.assertIn("startedAt", out)
        self.assertIn("updatedAt", out)

    def test_progress_record_rejects_skip_unknown_and_bad_json(self):
        """判据 3：校验失败时草稿不变。"""
        self.init()
        self.start_reset(select="candidate")
        path = self.ws / workspace.PROGRESS_FILE
        before = path.read_bytes()
        cases = [
            ["candidate", "--status", "skipped"],
            ["salary", "--status", "changed"],
            ["unknown", "--status", "kept"],
            ["candidate", "--status", "changed", "--value", "{broken"],
            ["candidate", "--status", "changed", "--value", "NaN"],
        ]
        for args in cases:
            with self.subTest(args=args):
                self.assertEqual(self.progress("record", *args)[0], 2)
                self.assertEqual(path.read_bytes(), before)

    def test_progress_changed_adds_reviews_but_kept_does_not(self):
        """判据 3：事实与目标职位变更联动、来源与去重。"""
        self.init()
        self.start_reset(select="candidate")
        _, out = self.progress("record", "candidate", "--status", "kept")
        self.assertEqual(out["plan"], ["materials", "candidate"])
        self.assertEqual(out["added"], {})
        for _ in range(2):
            _, out = self.progress("record", "candidate", "--status", "changed")
            self.assertEqual(out["plan"], ["materials", "candidate", "qa", "greeting"])
            self.assertEqual(out["added"], {"qa": "candidate", "greeting": "candidate"})
        self.progress("clear")
        self.start_reset(select="roles")
        _, out = self.progress("record", "roles", "--status", "changed")
        self.assertEqual(out["plan"], ["roles", "greeting"])
        self.assertEqual(out["added"], {"greeting": "roles"})
        self.progress("clear")
        self.start_reset("resetup")
        _, out = self.progress("record", "candidate", "--status", "changed")
        self.assertEqual(len(out["plan"]), 18)
        self.assertEqual(out["added"], {})

    def test_progress_review_inserted_in_catalog_order(self):
        """判据 12：目标职位变更后，招呼语复核排在 IM 前面。"""
        self.init()
        self.start_reset(select="roles,im")
        code, out = self.progress("record", "roles", "--status", "changed", "--value", '["前端"]')
        self.assertEqual(code, 0, out)
        self.assertEqual(out["plan"], ["roles", "greeting", "im"])
        self.assertEqual(out["current"], "greeting")
        self.assertEqual(out["added"], {"greeting": "roles"})
        self.assertEqual(out["position"], 2)

    def test_progress_review_answers_invalidated_after_fact_change(self):
        """判据 12：跳回改事实，已答问答与招呼语失效，其他回答保留。"""
        self.init()
        for mode in ("partial", "resetup"):
            with self.subTest(mode=mode):
                out = self.start_reset(mode, select="candidate,qa,greeting,im")
                for step in out["plan"]:
                    code, out = self.progress("record", step, "--status", "kept",
                                              "--value", '"原值"', "--source", "current")
                    self.assertEqual(code, 0, out)
                self.assertIsNone(out["current"])
                old_answers = out["answers"]
                self.progress("goto", "candidate")
                code, out = self.progress("record", "candidate", "--status", "changed",
                                          "--value", '"新事实"')
                self.assertEqual(code, 0, out)
                self.assertEqual(out["current"], "qa")
                self.assertNotIn("qa", out["answers"])
                self.assertNotIn("greeting", out["answers"])
                self.assertEqual(out["added"], {"qa": "candidate", "greeting": "candidate"})
                for step, answer in old_answers.items():
                    if step not in {"candidate", "qa", "greeting"}:
                        self.assertEqual(out["answers"][step], answer)
                _, out = self.progress("record", "qa", "--status", "kept")
                self.assertEqual(out["current"], "greeting")
                self.progress("clear")

    def test_progress_clear_new_removes_marker_but_keeps_other_files(self):
        """判据 13：放弃新建撤销工作区，保留所有其他文件，可重新初始化。"""
        self.init()
        self.fill()
        for events_text in (None, "", " \n\n"):
            with self.subTest(events_text=events_text):
                self.init()
                events = self.ws / "ledger/events.jsonl"
                if events_text is not None:
                    events.write_text(events_text)
                self.progress("start", "--mode", "new", "--variant", "custom")
                before = {p.relative_to(self.ws): p.read_bytes() for p in self.ws.rglob("*")
                          if p.is_file() and p.name not in {workspace.MARKER, workspace.PROGRESS_FILE}}
                code, out = self.progress("clear")
                self.assertEqual(code, 0, out)
                self.assertTrue(out["cleared"])
                self.assertTrue(out["markerRemoved"])
                self.assertFalse((self.ws / workspace.MARKER).exists())
                self.assertFalse((self.ws / workspace.PROGRESS_FILE).exists())
                for rel, content in before.items():
                    self.assertEqual((self.ws / rel).read_bytes(), content)
                scripts = Path(__file__).resolve().parent.parent / "scripts"
                for script, args in (("ledger.py", ["lock", "acquire", "--skill", "apply"]),
                                     ("workspace.py", ["check"])):
                    result = subprocess.run([sys.executable, str(scripts / script), *args],
                                            cwd=self.ws, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2, result.stdout)
                    self.assertIn("未找到", json.loads(result.stdout)["error"])
                self.assertEqual(self.init()[0], 0)
                self.assertEqual(self.progress("start", "--mode", "new", "--variant", "custom")[0], 0)
                self.assertEqual(self.progress("clear")[0], 0)

    def test_progress_clear_new_keeps_marker_with_existing_events(self):
        """判据 13：已有事件（包括损坏内容）时不撤销工作区、不改动事件。"""
        self.init()
        events = self.ws / "ledger/events.jsonl"
        for text in ('{"kind": "observe", "observed": "replied"}\n', "{broken\n"):
            events.write_text(text)
            marker_before = (self.ws / workspace.MARKER).read_bytes()
            self.progress("start", "--mode", "new", "--variant", "custom")
            code, out = self.progress("clear")
            self.assertEqual(code, 0, out)
            self.assertTrue(out["cleared"])
            self.assertFalse(out["markerRemoved"])
            self.assertIn("事件记录", out["reason"])
            self.assertEqual((self.ws / workspace.MARKER).read_bytes(), marker_before)
            self.assertEqual(events.read_text(), text)
            self.assertFalse((self.ws / workspace.PROGRESS_FILE).exists())

    def test_progress_clear_reset_keeps_marker(self):
        """判据 13：重新设置与部分重新设置放弃时均保留标记。"""
        self.init()
        before = (self.ws / workspace.MARKER).read_bytes()
        for mode in ("resetup", "partial"):
            self.start_reset(mode)
            code, out = self.progress("clear")
            self.assertEqual(code, 0, out)
            self.assertTrue(out["cleared"])
            self.assertFalse(out["markerRemoved"])
            self.assertEqual((self.ws / workspace.MARKER).read_bytes(), before)

    def test_progress_goto_keeps_answers_until_record_overwrites(self):
        """判据 4：跳回只移动游标；重答覆盖并跳过已回答项。"""
        self.init()
        self.start_reset(select="salary,requirements,exclusions")
        self.progress("record", "salary", "--status", "changed", "--value", "15000")
        self.progress("record", "requirements", "--status", "skipped")
        _, out = self.progress("goto", "salary")
        self.assertEqual(out["current"], "salary")
        self.assertEqual(out["answers"]["salary"]["value"], 15000)
        _, out = self.progress("record", "salary", "--status", "changed", "--value", "20000")
        self.assertEqual(out["answers"]["salary"]["value"], 20000)
        self.assertEqual(out["current"], "exclusions")
        self.assertEqual(self.progress("goto", "candidate")[0], 2)
        _, out = self.progress("record", "exclusions", "--status", "default")
        self.assertIsNone(out["current"])

    def test_init_and_progress_do_not_touch_repository_ignore_file(self):
        gi = self.root / ".gitignore"
        for initial in (None, "# 原有内容\n"):
            if initial is not None:
                gi.write_text(initial)
            self.init()
            self.progress("start", "--mode", "new", "--variant", "typical")
            self.progress("clear")
            if initial is None:
                self.assertFalse(gi.exists())
            else:
                self.assertEqual(gi.read_text(), initial)

    def test_check_progress_modes_and_corruption(self):
        """判据 6：新建阻断、重置提示、损坏阻断且建议放弃。"""
        self.init()
        self.fill()
        self.progress("start", "--mode", "new", "--variant", "custom")
        code, out = run("--workspace", str(self.ws), "check")
        self.assertEqual(code, 1)
        self.assertIn("第 2/19 步", out["problems"][0]["problem"])
        for mode in ("resetup", "partial"):
            self.progress("clear")
            self.init()
            self.start_reset(mode)
            code, out = run("--workspace", str(self.ws), "check")
            self.assertEqual(code, 0, out)
            self.assertTrue(any(w.get("file") == workspace.PROGRESS_FILE for w in out["warnings"]))
        path = self.ws / workspace.PROGRESS_FILE
        for broken in ("{broken", "[]", '{"schemaVersion": 1, "mode": "new"}'):
            path.write_text(broken)
            code, out = run("--workspace", str(self.ws), "check")
            self.assertEqual(code, 1)
            self.assertIn("progress clear", out["problems"][0]["problem"])
            self.assertEqual(self.progress("show")[0], 2)
            self.assertEqual(self.progress("start", "--mode", "new", "--variant", "custom")[0], 2)
            self.assertEqual(path.read_text(), broken)
        self.assertEqual(self.progress("clear")[0], 0)
        self.init()
        self.assertEqual(self.progress("clear")[0], 0)

    def test_progress_not_backed_up_or_restored(self):
        """判据 8：备份、恢复与 changed 均不涉及草稿。"""
        self.init()
        self.fill()
        self.progress("start", "--mode", "new", "--variant", "custom")
        _, out = run("--workspace", str(self.ws), "backup")
        name = out["backup"]
        self.assertNotIn(workspace.PROGRESS_FILE, out["files"])
        self.assertFalse((self.ws / "backups" / name / workspace.PROGRESS_FILE).exists())
        self.progress("record", "materials", "--status", "skipped")
        before = (self.ws / workspace.PROGRESS_FILE).read_bytes()
        self.assertEqual(run("--workspace", str(self.ws), "changed", "--since", name)[0], 0)
        (self.ws / "rules.md").write_text("# 修改\n")
        self.assertEqual(run("--workspace", str(self.ws), "restore", name)[0], 0)
        self.assertEqual((self.ws / workspace.PROGRESS_FILE).read_bytes(), before)

    def test_progress_catalog_matches_skill_table(self):
        """判据 9：技能步骤表与脚本 id、显示名、顺序完全一致。"""
        skill = (Path(__file__).resolve().parent.parent / "skills/setup/SKILL.md").read_text()
        table = skill.split("## 2. 步骤目录\n", 1)[1].split("\n## 3.", 1)[0]
        ids = re.findall(r"^\|[^|]*\| `([A-Za-z]+)` \|", table, re.MULTILINE)
        self.assertEqual(ids, [s["id"] for s in workspace.STEPS])
        names = re.findall(r"^\|[^|]*\| `[A-Za-z]+` \| ([^|]+) \|", table, re.MULTILINE)
        self.assertEqual([name.strip() for name in names], [s["name"] for s in workspace.STEPS])

    def test_progress_finish_checks_then_clears(self):
        """判据 11：复用体检；完整配置完成后 check 通过。"""
        self.init()
        self.fill()
        self.progress("start", "--mode", "new", "--variant", "custom")
        self.assertEqual(run("--workspace", str(self.ws), "check")[0], 1)
        code, out = self.progress("finish")
        self.assertEqual(code, 0, out)
        self.assertTrue(out["finished"])
        self.assertFalse((self.ws / workspace.PROGRESS_FILE).exists())
        self.assertEqual(run("--workspace", str(self.ws), "check")[0], 0)

    def test_progress_finish_keeps_draft_on_problems(self):
        """判据 11：失败保留草稿，不把草稿存在计入问题。"""
        self.init()
        self.progress("start", "--mode", "new", "--variant", "custom")
        path = self.ws / workspace.PROGRESS_FILE
        before = path.read_bytes()
        code, out = self.progress("finish")
        self.assertEqual(code, 1, out)
        self.assertFalse(out["finished"])
        self.assertIn("profile.json", [p.get("file") for p in out["problems"]])
        self.assertNotIn(workspace.PROGRESS_FILE, [p.get("file") for p in out["problems"]])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.progress("clear")[0], 0)
        self.assertFalse(path.exists())

    def test_init_refuses_home(self):
        code, out = run("init", str(Path.home()))
        self.assertEqual(code, 2)

    def test_check_reports_missing_then_passes(self):
        self.init()
        code, out = run("--workspace", str(self.ws), "check")
        self.assertEqual(code, 1)
        self.assertIn("profile.json", [p.get("file") for p in out["problems"]])
        self.fill()
        code, out = run("--workspace", str(self.ws), "check")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["info"]["qa"], 1)

    def test_check_warns_missing_new_fields(self):
        self.init()
        self.fill()
        old = {k: v for k, v in PROFILE.items() if k not in ("schedule",)}
        (self.ws / "profile.json").write_text(json.dumps(old))
        code, out = run("--workspace", str(self.ws), "check")
        self.assertEqual(code, 0)
        fields = {w["field"] for w in out["warnings"]}
        self.assertIn("recruiterActivity", fields)
        self.assertIn("schedule", fields)

    def test_check_flags_bad_profile_values(self):
        self.init()
        self.fill()
        bad = dict(PROFILE, greeting={"mode": "agent"}, targetRoles=[],
                   authorization={"apply": {"send": "yes"}, "followup": {"level": "L9"}})
        (self.ws / "profile.json").write_text(json.dumps(bad))
        (self.ws / "qa.yaml").write_text("- id: edu\n  ask: [学历]\n  answer: 本科\n")
        (self.ws / "resumes" / "index.yaml").write_text("versions:\n  - id: b\n    file: missing.pdf\n")
        code, out = run("--workspace", str(self.ws), "check")
        self.assertEqual(code, 1)
        fields = {p.get("field") for p in out["problems"]}
        self.assertTrue({"greeting.mode", "targetRoles", "authorization.apply.send",
                         "authorization.followup.level"} <= fields)
        msgs = " ".join(p["problem"] for p in out["problems"])
        self.assertIn("basis", msgs)
        self.assertIn("missing.pdf", msgs)

    def test_check_validates_im(self):
        self.init()
        self.fill()
        good = dict(PROFILE, im={"channel": "lark", "ownerId": "synthetic-owner", "larkProfile": "default", "waitMinutes": 60})
        (self.ws / "profile.json").write_text(json.dumps(good))
        code, out = run("--workspace", str(self.ws), "check")
        self.assertEqual(code, 0, out)
        bad = dict(PROFILE, im={"channel": "lark", "waitMinutes": 999}, notify={"channel": "lark"})
        (self.ws / "profile.json").write_text(json.dumps(bad))
        code, out = run("--workspace", str(self.ws), "check")
        self.assertEqual(code, 1)
        fields = {p.get("field") for p in out["problems"]}
        self.assertIn("im", fields)

    def test_check_flags_unusable_qa_and_resume_index(self):
        self.init()
        self.fill()
        (self.ws / "qa.yaml").write_text("这里只是一段普通文字\n")
        (self.ws / "resumes" / "index.yaml").write_text(
            "default: missing\nversions:\n  - id: a\n    file: a.pdf\n    use: 默认\n")
        code, out = run("--workspace", str(self.ws), "check")
        self.assertEqual(code, 1)
        msgs = " ".join(p["problem"] for p in out["problems"])
        self.assertIn("识别不出任何条目", msgs)
        self.assertIn("default 指向不存在的版本", msgs)
        self.assertIn("uploaded", " ".join(w["warning"] for w in out["warnings"]))
        (self.ws / "qa.yaml").write_text("# 暂无\n[]\n")
        (self.ws / "resumes" / "index.yaml").write_text(
            "default: a\nversions:\n  - id: a\n    file: a.pdf\n    use: 默认\n    uploaded: {boss: a}\n")
        self.assertEqual(run("--workspace", str(self.ws), "check")[0], 0)

    def test_restore_keeps_stop_entries_added_after_backup(self):
        self.init()
        self.fill()
        _, out = run("--workspace", str(self.ws), "backup")
        name = out["backup"]
        ledger_main = workspace.ledger.main
        with contextlib.redirect_stdout(io.StringIO()):
            ledger_main(["--workspace", str(self.ws), "stoplist", "add", "--name", "新增公司",
                         "--reason", "风险", "--source", "followup"])
        (self.ws / "rules.md").write_text("# 改过\n")
        code, out = run("--workspace", str(self.ws), "restore", name)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["stoplistKeptSinceBackup"], ["新增公司"])
        names = [e["name"] for e in workspace.ledger.load_stoplist(self.ws)]
        self.assertEqual(names, ["甲公司", "新增公司"])
        self.assertEqual((self.ws / "rules.md").read_text(), "# 规则\n")

    def test_restore_refused_while_running_and_changed_detects_edits(self):
        self.init()
        self.fill()
        _, out = run("--workspace", str(self.ws), "backup")
        name = out["backup"]
        self.assertEqual(run("--workspace", str(self.ws), "changed", "--since", name)[0], 0)
        (self.ws / "rules.md").write_text("# 期间被改\n")
        code, out = run("--workspace", str(self.ws), "changed", "--since", name)
        self.assertEqual(code, 1)
        self.assertEqual(out["changed"], [{"file": "rules.md", "change": "modified"}])
        with contextlib.redirect_stdout(io.StringIO()):
            workspace.ledger.main(["--workspace", str(self.ws), "lock", "acquire", "--skill", "followup"])
        code, out = run("--workspace", str(self.ws), "restore", name)
        self.assertEqual(code, 2)
        self.assertIn("正在运行", out["error"])

    def test_restore_holds_lock_for_whole_run(self):
        """中断点：restore 检查之后、写入之前，新任务不能获取运行锁。"""
        self.init()
        self.fill()
        _, out = run("--workspace", str(self.ws), "backup")
        name = out["backup"]
        seen = {}
        original = workspace.do_backup

        def probe(ws, label=None):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                seen["code"] = workspace.ledger.main(
                    ["--workspace", str(self.ws), "lock", "acquire", "--skill", "followup"])
            return original(ws, label)

        workspace.do_backup = probe
        try:
            code, out = run("--workspace", str(self.ws), "restore", name)
        finally:
            workspace.do_backup = original
        self.assertEqual(code, 0, out)
        self.assertEqual(seen["code"], 1)
        with contextlib.redirect_stdout(io.StringIO()):
            workspace.ledger.main(["--workspace", str(self.ws), "lock", "status"])
        self.assertFalse((self.ws / "ledger" / "lock.json").exists())

    def test_restore_success_and_release_failure_are_reported_separately(self):
        self.init()
        self.fill()
        _, backup = run("--workspace", str(self.ws), "backup")
        (self.ws / "rules.md").write_text("# 已修改\n")
        # acquire 无旧锁时不读事件；真实损坏在 finally 的 release 中被发现。
        (self.ws / "ledger/events.jsonl").write_text("{broken\n")
        code, out = run("--workspace", str(self.ws), "restore", backup["backup"])
        self.assertEqual(code, 2, out)
        self.assertTrue(out.get("restoreSucceeded"), out)
        self.assertIsNone(out["restoreError"])
        self.assertIn("损坏", out["releaseError"])
        self.assertIn("rules.md", out["restored"])
        self.assertEqual((self.ws / "rules.md").read_text(), "# 规则\n")
        self.assertTrue(out["release"]["attempted"])
        self.assertFalse(out["release"]["released"])
        self.assertEqual(workspace.ledger.read_lock(self.ws)["runId"], out["runId"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(workspace.ledger.main([
                "--workspace", str(self.ws), "lock", "acquire", "--skill", "followup"]), 1)

    def test_restore_body_error_is_not_masked_by_release_failure(self):
        self.init()
        self.fill()
        _, backup = run("--workspace", str(self.ws), "backup")
        (self.ws / "ledger/events.jsonl").write_text("{broken\n")
        with patch.object(workspace, "do_backup", side_effect=workspace.WorkspaceError("合成主体恢复失败")):
            code, out = run("--workspace", str(self.ws), "restore", backup["backup"])
        self.assertEqual(code, 2, out)
        self.assertIn("合成主体恢复失败", out.get("error", ""))
        self.assertFalse(out["restoreSucceeded"])
        self.assertEqual(out["restoreError"], "合成主体恢复失败")
        self.assertIn("损坏", out["releaseError"])
        self.assertIn("损坏", out["error"])
        self.assertEqual(out["restored"], [])
        self.assertIsNone(out["previousSavedAs"])
        self.assertFalse(out["release"]["released"])
        self.assertEqual(workspace.ledger.read_lock(self.ws)["runId"], out["runId"])

    def test_restore_non_utf8_stoplist_and_corrupt_ledger_reports_both_errors_without_traceback(self):
        self.init()
        self.fill()
        _, backup = run("--workspace", str(self.ws), "backup")
        source = self.ws / "backups" / backup["backup"]
        (source / "stoplist.yaml").write_bytes(b"\xff\xfe")
        (self.ws / "ledger/events.jsonl").write_text("{broken\n")
        proc = subprocess.run([sys.executable, str(Path(workspace.__file__)), "--workspace", str(self.ws),
                               "restore", backup["backup"]], capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        out = json.loads(proc.stdout)
        self.assertFalse(out["restoreSucceeded"])
        self.assertIn("utf-8", out["restoreError"])
        self.assertIn("损坏", out["releaseError"])
        self.assertEqual(out["restored"], ["job-mate.json", "profile.json"])
        self.assertTrue(out["previousSavedAs"])
        self.assertFalse(out["release"]["released"])
        self.assertEqual(workspace.ledger.read_lock(self.ws)["runId"], out["runId"])

    def test_restore_value_error_still_reports_successful_release(self):
        self.init()
        self.fill()
        _, backup = run("--workspace", str(self.ws), "backup")
        with patch.object(workspace, "do_backup", side_effect=ValueError("合成取值错误")):
            code, out = run("--workspace", str(self.ws), "restore", backup["backup"])
        self.assertEqual(code, 2, out)
        self.assertEqual(out["restoreError"], "合成取值错误")
        self.assertIsNone(out["releaseError"])
        self.assertTrue(out["release"]["released"])
        self.assertFalse((self.ws / "ledger/lock.json").exists())

    def test_restore_non_utf8_ledger_reports_release_error_and_keeps_restored_files(self):
        self.init()
        self.fill()
        _, backup = run("--workspace", str(self.ws), "backup")
        (self.ws / "ledger/events.jsonl").write_bytes(b"\xff")
        code, out = run("--workspace", str(self.ws), "restore", backup["backup"])
        self.assertEqual(code, 2, out)
        self.assertTrue(out["restoreSucceeded"])
        self.assertIsNone(out["restoreError"])
        self.assertIn("utf-8", out["releaseError"])
        self.assertIn("rules.md", out["restored"])
        self.assertFalse(out["release"]["released"])
        self.assertEqual(workspace.ledger.read_lock(self.ws)["runId"], out["runId"])

    def test_restore_non_utf8_profile_before_acquire_returns_json_error_without_traceback(self):
        self.init()
        self.fill()
        _, backup = run("--workspace", str(self.ws), "backup")
        (self.ws / "profile.json").write_bytes(b"\xff")
        proc = subprocess.run([sys.executable, str(Path(workspace.__file__)), "--workspace", str(self.ws),
                               "restore", backup["backup"]], capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("utf-8", json.loads(proc.stdout)["error"])
        self.assertFalse((self.ws / "ledger/lock.json").exists())

    def test_restore_does_not_swallow_programming_type_error(self):
        self.init()
        self.fill()
        _, backup = run("--workspace", str(self.ws), "backup")
        with patch.object(workspace, "do_backup", side_effect=TypeError("合成编程错误")):
            with self.assertRaisesRegex(TypeError, "合成编程错误"):
                run("--workspace", str(self.ws), "restore", backup["backup"])
        self.assertFalse((self.ws / "ledger/lock.json").exists())

    def test_restore_body_failure_still_reports_successful_release(self):
        self.init()
        self.fill()
        _, backup = run("--workspace", str(self.ws), "backup")
        for error in (workspace.WorkspaceError("合成主体错误"), OSError("合成磁盘错误")):
            with self.subTest(error=str(error)), patch.object(workspace, "do_backup", side_effect=error):
                code, out = run("--workspace", str(self.ws), "restore", backup["backup"])
            self.assertEqual(code, 2, out)
            self.assertFalse(out["restoreSucceeded"])
            self.assertEqual(out["restoreError"], str(error))
            self.assertIsNone(out["releaseError"])
            self.assertTrue(out["release"]["released"])
            self.assertFalse((self.ws / "ledger/lock.json").exists())

    def test_restore_merges_acquire_and_release_reconciled(self):
        self.init()
        self.fill()
        _, backup = run("--workspace", str(self.ws), "backup")
        ledger = workspace.ledger
        tz = ledger.tz_of(PROFILE)

        def pending(run_id, company):
            event = {"platform": "boss", "runId": run_id, "skill": "setup", "kind": "greet",
                     "status": "pending", "company": company, "reason": "合成测试，未操作平台"}
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = ledger.main(["--workspace", str(self.ws), "append", "--json", json.dumps(event)])
            self.assertEqual(code, 0, buf.getvalue())
            return json.loads(buf.getvalue())["event"]["attemptId"]

        with ledger.lock_mutex(self.ws):
            ledger.acquire_lock(self.ws, tz, "apply", "old")
        old_id = pending("old", "合成旧轮")
        with ledger.lock_mutex(self.ws):
            lock = ledger.read_lock(self.ws)
            lock["expiresAt"] = "2000-01-01T00:00:00+08:00"
            ledger.write_lock(self.ws, lock)
        original = workspace.do_backup
        added = []

        def add_pending(ws, label=None):
            added.append(pending(ledger.read_lock(ws)["runId"], "合成本轮"))
            return original(ws, label)

        with patch.object(workspace, "do_backup", side_effect=add_pending):
            code, out = run("--workspace", str(self.ws), "restore", backup["backup"])
        self.assertEqual(code, 0, out)
        self.assertTrue(out["restoreSucceeded"])
        self.assertIsNone(out["restoreError"])
        self.assertIsNone(out["releaseError"])
        self.assertEqual([a["attemptId"] for a in out["reconciled"]], [old_id, *added])
        self.assertEqual([a["attemptId"] for a in out["release"]["reconciled"]], added)
        self.assertTrue(out["release"]["released"])
        self.assertFalse((self.ws / "ledger/lock.json").exists())

    def test_restore_release_refusal_is_reported_without_deleting_other_lock(self):
        self.init()
        self.fill()
        _, backup = run("--workspace", str(self.ws), "backup")
        original = workspace.ledger.release_lock

        def lost_lock(ws, run_id, tz):
            lock = workspace.ledger.read_lock(ws)
            lock["runId"] = "other"
            workspace.ledger.write_lock(ws, lock)
            return original(ws, run_id, tz)

        with patch.object(workspace.ledger, "release_lock", side_effect=lost_lock):
            code, out = run("--workspace", str(self.ws), "restore", backup["backup"])
        self.assertEqual(code, 2, out)
        self.assertTrue(out["restoreSucceeded"])
        self.assertIsNone(out["restoreError"])
        self.assertIn("锁不属于", out["releaseError"])
        self.assertEqual(out["release"]["code"], 1)
        self.assertFalse(out["release"]["released"])
        self.assertEqual(workspace.ledger.read_lock(self.ws)["runId"], "other")

    def test_restore_refuses_other_setup_but_reuses_own_lock(self):
        self.init()
        self.fill()
        _, out = run("--workspace", str(self.ws), "backup")
        name = out["backup"]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            workspace.ledger.main(["--workspace", str(self.ws), "lock", "acquire", "--skill", "setup", "--run", "s1"])
        self.assertEqual(run("--workspace", str(self.ws), "restore", name)[0], 2)
        code, out = run("--workspace", str(self.ws), "restore", name, "--run", "s1")
        self.assertEqual(code, 0, out)
        lock = json.loads((self.ws / "ledger" / "lock.json").read_text())
        self.assertEqual(lock["runId"], "s1")
        self.assertTrue(out["restoreSucceeded"])
        self.assertFalse(out["release"]["attempted"])
        self.assertIsNone(out["releaseError"])

    def test_backup_and_restore_roundtrip(self):
        self.init()
        self.fill()
        code, out = run("--workspace", str(self.ws), "backup", "--label", "before resetup")
        self.assertEqual(code, 0)
        name = out["backup"]
        self.assertTrue(name.endswith("before-resetup"))
        self.assertIn("resumes/index.yaml", out["files"])
        (self.ws / "rules.md").write_text("# 新规则\n")
        code, out = run("--workspace", str(self.ws), "restore", name)
        self.assertEqual(code, 0)
        self.assertEqual((self.ws / "rules.md").read_text(), "# 规则\n")
        saved = self.ws / "backups" / out["previousSavedAs"] / "rules.md"
        self.assertEqual(saved.read_text(), "# 新规则\n")
        _, out = run("--workspace", str(self.ws), "backups")
        self.assertEqual(len(out["backups"]), 2)


def invoke_data(main, *argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(list(argv))
    return code, json.loads(out.getvalue())


class WorkspaceIsolationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "jobs"
        invoke_data(workspace.main, "init", str(self.ws), "--boss-account", "demo", "--boss-name", "示例账号")
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
        return invoke_data(workspace.main, "--workspace", str(self.ws), *argv)

    def led(self, *argv):
        return invoke_data(ledger.main, "--workspace", str(self.ws), *argv)

    def partial(self):
        _, backup = self.work("backup")
        return self.work("progress", "start", "--mode", "partial", "--select", "browser",
                         "--base-backup", backup["backup"])

    def test_browser_accepts_only_empty_object_or_missing(self):
        self.assertEqual(self.work("check")[0], 0)
        for value, expected in (({}, 0), (None, 1), ([], 1), ("agent", 1), ({"unknown": True}, 1)):
            self.profile["browser"] = value
            self.save_profile()
            self.assertEqual(self.work("check")[0], expected)

    def test_check_env_rejected_without_execution(self):
        for value in (None, {}, {"unknown": True}):
            self.profile["browser"] = value
            self.save_profile()
            before = (self.ws / "profile.json").read_bytes()
            with patch("subprocess.Popen", side_effect=AssertionError("不得执行外部程序")), \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                with self.assertRaises(SystemExit) as rejected:
                    self.work("check", "--env")
            self.assertEqual(rejected.exception.code, 2)
            self.assertIn("--env", err.getvalue())
            self.assertEqual((self.ws / "profile.json").read_bytes(), before)
        del self.profile["browser"]
        self.save_profile()
        with patch("subprocess.Popen", side_effect=AssertionError("不得执行外部程序")):
            self.assertEqual(self.work("check")[0], 0)

    def test_new_browser_after_account_not_preanswered(self):
        ids = [s["id"] for s in workspace.STEPS]
        self.assertEqual(ids[ids.index("account") + 1], "browser")
        _, out = self.work("progress", "start", "--mode", "new", "--variant", "typical")
        self.assertIn("browser", out["plan"])
        self.assertNotIn("browser", out["answers"])
        self.assertNotIn("browser", self.profile)

    def test_browser_partial_skip_keep_and_resume_do_not_write_profile(self):
        for original in (None, {}):
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
                                      "--value", '{}')
                self.assertEqual(code, 0, out)
                self.assertIsNone(out["current"])
                self.assertEqual(self.work("progress", "show")[1]["answers"]["browser"]["status"], status)
                self.assertEqual((self.ws / "profile.json").read_bytes(), before)
                self.assertEqual(self.work("progress", "finish")[0], 0)
                self.assertEqual((self.ws / "profile.json").read_bytes(), before)

    def test_core_has_no_external_execution_or_desktop_import(self):
        source = Path(workspace.__file__)
        tree = ast.parse(source.read_text())
        imports = {n.name for node in ast.walk(tree) if isinstance(node, ast.Import) for n in node.names}
        imports.update(node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom))
        self.assertFalse({"desktop", "subprocess"} & imports)
        self.assertFalse(any(n and n.startswith("integrations") for n in imports))
        # 仅拷贝数据脚本，验证脱离 desktop 模块仍能加载、体检和拒绝旧选项。
        scripts = Path(self.tmp.name) / "isolated"
        scripts.mkdir()
        for name in ("workspace.py", "ledger.py", "notify.py", "fsutil.py"):
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

    def test_lark_wait_minutes_are_data_only(self):
        for wait in (0, 60, 240):
            self.profile["im"] = {"channel": "lark", "ownerId": "synthetic-owner", "larkProfile": "default", "waitMinutes": wait}
            self.save_profile()
            self.assertEqual(self.work("check")[0], 0)
        for wait in (None, True, False, -1, 241, 60.0, "60"):
            self.profile["im"]["waitMinutes"] = wait
            self.save_profile()
            code, out = self.work("check")
            self.assertEqual(code, 1, out)
            self.assertIn("im", {p.get("field") for p in out["problems"]})

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



if __name__ == "__main__":
    unittest.main()

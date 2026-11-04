"""纯筛查与nullable URL：真实子进程，直接执行技能命令，不使用旧check补参包装。"""
import json
import re
import shlex
import subprocess
import sys
import unittest

import test_prepare

ledger = test_prepare.ledger
ROOT = test_prepare.test_tail.ROOT
SIGNALS = {"stoplist", "stoplist_suspect", "company_rejected", "company_risk", "already_contacted", "reposted_suspect"}
COMPANY_SIGNALS = SIGNALS - {"already_contacted", "reposted_suspect"}


class ScreenCheckTest(unittest.TestCase):
    def setUp(self):
        self.x = test_prepare.PrepareTest()
        self.x.setUp()
        self.reset()

    def reset(self):
        self.x.reset_case()
        self.f = self.x.f
        self.f.run_cli = self.cli

    def tearDown(self):
        self.x.tearDown()

    def cli(self, *args, stdin=None):
        p = subprocess.run([sys.executable, "-B", "-I", str(ROOT / "scripts/ledger.py"), "--workspace", str(self.f.ws), *args],
                           input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=30)
        if p.returncode != 2:
            self.assertFalse(p.stderr, p.stderr)
        return p.returncode, json.loads(p.stdout) if p.stdout else {"error": p.stderr}

    def snapshot(self):
        return {str(p.relative_to(self.f.ws)): p.read_bytes() for p in self.f.ws.rglob("*")
                if p.is_file() and not p.name.endswith(".mutex")}

    def screen(self, *args, company="合成公司", platform="boss"):
        before = self.snapshot()
        rc, out = self.cli("screen-check", "--platform", platform, "--company", company, *args)
        self.assertEqual(rc, 0, out)
        self.assertIs(out["screeningOnly"], True)
        self.assertFalse({"allowed", "authorizedBy", "attemptId", "eventId"} & out.keys())
        self.assertEqual((out["platform"], out["company"]), (platform, company))
        self.assertTrue({r["code"] for r in out["reasons"]} <= SIGNALS)
        self.assertTrue(all(set(r) == {"code", "message"} for r in out["reasons"]))
        self.assertEqual(self.snapshot(), before)
        return out

    def skill_command(self, skill):
        doc = (ROOT / f"skills/{skill}/SKILL.md").read_text(encoding="utf-8")
        section = doc.split("## 4.", 1)[1].split("## 5.", 1)[0]
        command = re.search(r"`ledger (?:screen-check|check)(?: [^`]*)?`", section)[0][1:-1]
        for key, value in {"公司": "合成公司", "职位": "开发", "runId": "run-prepare"}.items():
            command = command.replace(f"<{key}>", value)
        return shlex.split(command)[1:], section

    def test_skill_commands_run_verbatim_without_lock_or_sending_authority(self):
        self.x.profile["authorization"] = {"apply": {"send": False}, "followup": {"reply": "L1"}}
        self.x.save_profile()
        self.assertEqual(self.f.append(kind="observe", observed="risk")[0], 0)
        self.assertEqual(self.cli("lock", "release", "--run", "run-prepare")[0], 0)
        for no_send in (False, True):
            if no_send:
                self.assertEqual(self.x.acquire(no_send=True)[0], 0)
            for skill in ("apply", "followup"):
                with self.subTest(skill=skill, no_send=no_send):
                    args, _ = self.skill_command(skill)
                    before = self.snapshot()
                    rc, out = self.cli(*args)
                    self.assertEqual(rc, 0, out)
                    self.assertIs(out["screeningOnly"], True)
                    self.assertNotIn("allowed", out)
                    self.assertIn("company_risk", {r["code"] for r in out["reasons"]})
                    self.assertEqual(self.snapshot(), before)

    def test_all_six_signal_codes_are_read_only_and_exit_zero(self):
        for code in sorted(SIGNALS):
            with self.subTest(code=code):
                self.reset()
                args = []
                if code.startswith("stoplist"):
                    name = "合成公司" if code == "stoplist" else "合成"
                    self.assertEqual(self.cli("stoplist", "add", "--name", name, "--reason", "合成", "--source", "user")[0], 0)
                elif code.startswith("company_"):
                    self.assertEqual(self.f.append(kind="observe", observed=code.removeprefix("company_"))[0], 0)
                else:
                    rc, pending = self.x.prepare(kind="greet", targetRole="开发", jobUrl="https://www.zhipin.com/job_detail/old-id.html")
                    self.assertEqual(rc, 0, pending)
                    self.assertEqual(self.f.append(kind="greet", status="sent", runId="run-prepare", attemptId=pending["attemptId"])[0], 0)
                    if code == "reposted_suspect":
                        args = ["--title", "开发", "--job-url", "https://www.zhipin.com/job_detail/new-id.html"]
                out = self.screen(*args)
                self.assertIn(code, {r["code"] for r in out["reasons"]})
                self.assertEqual(out["account"], "boss-main")

    def test_inbound_consumes_company_signals_not_first_contact_dedup(self):
        rc, pending = self.x.prepare()
        self.assertEqual(rc, 0, pending)
        self.assertEqual(self.f.append(kind="reply", status="sent", runId="run-prepare", attemptId=pending["attemptId"])[0], 0)
        args, section = self.skill_command("followup")
        rc, out = self.cli(*args)
        self.assertEqual(rc, 0, out)
        documented = set(re.findall(r"`([a-z_]+)`", section.split("### 4.3", 1)[0])) & SIGNALS
        self.assertEqual(documented, COMPANY_SIGNALS)
        self.assertIn("already_contacted", {r["code"] for r in out["reasons"]})
        self.assertEqual([r for r in out["reasons"] if r["code"] in documented], [])

    def test_raw_accounts_platforms_and_other_companies_are_isolated(self):
        self.x.current("a|b")
        rc, pending = self.x.prepare()
        self.assertEqual(rc, 0, pending)
        self.assertEqual(self.f.append(kind="reply", status="sent", runId="run-prepare", attemptId=pending["attemptId"])[0], 0)
        self.x.current("a｜b")
        self.assertEqual(self.screen()["reasons"], [])
        old = self.screen("--account", "a|b")
        self.assertEqual(old["account"], "a|b")
        self.assertIn("already_contacted", {r["code"] for r in old["reasons"]})
        self.assertEqual(self.screen(company="另一公司")["reasons"], [])
        self.assertEqual(self.screen(platform="testplat")["reasons"], [])

    def test_run_only_changes_same_run_failed_no_send_dedup(self):
        rc, pending = self.x.prepare(kind="greet", targetRole="开发")
        self.assertEqual(rc, 0, pending)
        self.assertEqual(self.f.append(kind="greet", status="failed_no_send", runId="run-prepare", attemptId=pending["attemptId"])[0], 0)
        self.assertEqual(self.cli("lock", "release", "--run", "run-prepare")[0], 0)
        self.assertEqual(self.screen()["reasons"], [])
        self.assertEqual(self.screen("--run", "other-run")["reasons"], [])
        self.assertIn("already_contacted", {r["code"] for r in self.screen("--run", "run-prepare")["reasons"]})

    def test_input_errors_exit_two_without_mutation(self):
        for args in (("--company", " "), ("--account", ""), ("--account", "unknown"), ("--platform", "unknown"),
                     ("--job-id", ""), ("--job-id", "other", "--job-url", "https://www.zhipin.com/job_detail/id.html")):
            with self.subTest(args=args):
                before = self.snapshot()
                rc, out = self.cli("screen-check", "--platform", "boss", "--company", "合成公司", *args)
                self.assertEqual(rc, 2, out)
                self.assertEqual(self.snapshot(), before)
        derived = self.screen("--job-url", "https://www.zhipin.com/job_detail/visible-id.html?query=1")
        self.assertEqual(derived["jobId"], "visible-id")
        self.assertEqual(self.screen("--job-id", "raw｜id")["jobId"], "raw｜id")
        with ledger.events_path(self.f.ws).open("ab") as handle:
            handle.write(b"corrupt\n")
        before = self.snapshot()
        self.assertEqual(self.cli("screen-check", "--platform", "boss", "--company", "合成公司")[0], 2)
        self.assertEqual(self.snapshot(), before)

    def test_nullable_job_url_not_nullable_or_loosely_typed_payload(self):
        for kind in ("reply", "resume", "greet"):
            with self.subTest(kind=kind):
                self.reset()
                fields = {"resume": "合成简历"} if kind == "resume" else {"text": "合成文字"}
                rc, out = self.x.prepare(kind=kind, jobUrl=None, targetRole="开发", **fields)
                self.assertEqual(rc, 0, out)
                self.assertIsNone(ledger.read_events(self.f.ws)[0][-1]["jobUrl"])
                self.assertEqual(self.cli("guard", "--run", "run-prepare", "--attempt", out["attemptId"])[0], 0)
        for field in ("jobUrl", "text", "resume"):
            values = (1, False, [], {}) if field == "jobUrl" else (None, 1, False, [], {})
            for value in values:
                with self.subTest(field=field, value=value):
                    before = self.snapshot()
                    rc, out = self.x.prepare(kind="resume" if field == "resume" else "reply", **{field: value})
                    self.assertEqual(rc, 2, out)
                    self.assertEqual(self.snapshot(), before)

    def test_screening_never_relaxes_the_full_send_check(self):
        self.assertEqual(self.screen()["reasons"], [])
        self.assertEqual(self.cli("check", "--platform", "boss", "--company", "合成公司")[0], 1)
        self.assertEqual(self.x.acquire(no_send=True)[0], 0)
        intent = dict(self.f.identity, kind="reply", text="合成", reason="合成")
        rc, out = self.cli("check", "--run", "run-prepare", "--json", json.dumps(intent))
        self.assertEqual((rc, out["reasons"][0]["code"]), (1, "no_send"))

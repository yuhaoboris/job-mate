"""IM 分项计划与真实 setup 配置事务；假 Agent 只提供合成语义映射。"""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_notify
import test_workspace

N, L, W = test_notify.notify, test_notify.ledger, test_workspace.workspace
ROOT = Path(__file__).resolve().parents[1]


class ImPreferencesTest(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, JOB_MATE_TESTING="1", JOB_MATE_NOW="2026-11-10T10:00:00+08:00")
        env.start(); self.addCleanup(env.stop)
        self.f = test_notify.NotifyTest(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.f.im.clock = N.now_ms() - 1000
        self.profile = copy.deepcopy(test_workspace.PROFILE)
        self.profile["im"] = {"channel": "lark", "ownerId": test_notify.USER, "larkProfile": "synthetic"}
        (self.f.ws / "profile.json").write_bytes(N.profile_bytes(self.profile))
        self.f.inbox((test_notify.D1, "待决定"), (test_notify.D2, "待处理"))
        self.f.establish()
        self.text = "职位改为后端开发；地点改为上海；薪资按新范围；D-20261030-01 已处理；其他看着办"
        self.mid = self.f.im.user_says(self.text)
        self.assertEqual(self.f.incoming(self.f.im.messages)[0], 0)
        self.assertEqual(self.f.ledger_run("lock", "acquire", "--skill", "followup", "--run", "followup")[0], 0)
        self.stage = self.f.ws.parent / (self.f.ws.name + "-staging")
        self.stage.mkdir()
        self.addCleanup(lambda: shutil.rmtree(self.stage, ignore_errors=True))

    def preference(self, fields, pid="preferences", members=None):
        return {"partId": pid, "kind": "preference", "quote": self.text, "state": "executable",
                "members": members or [pid], "changes": [{"field": field, "before": N.value_at(self.profile, field), "after": value}
                                                          for field, value in fields.items()]}

    def plan(self, parts, revision=1):
        return self.f.run_cmd("reply-state", "--message", self.mid, "--run", "followup",
                              "--plan", json.dumps({"revision": revision, "parts": parts}, ensure_ascii=False))

    def replies(self):
        return self.f.run_cmd("replies")[1]["replies"]

    def prepare(self, fields=None, parts=None, revision=1):
        fields = fields or {"targetRoles": ["后端开发"], "locations": ["上海"]}
        code, out = self.plan(parts or [self.preference(fields)], revision)
        self.assertEqual(code, 0, out)
        self.target = N.profile_bytes(out["targets"]["preferences"])
        (self.stage / "profile.json").write_bytes(self.target)
        self.meta = out["plan"]["parts"][0]
        return out

    def setup_phase(self):
        self.assertEqual(self.f.ledger_run("lock", "release", "--run", "followup")[0], 0)
        self.assertEqual(self.f.ledger_run("lock", "acquire", "--skill", "setup", "--run", "setup")[0], 0)

    def commit(self, run="setup", part="preferences", extra=(), crash=None):
        env = dict(os.environ)
        if crash:
            env["JOB_MATE_TEST_CRASH_AT"] = crash
        args = [sys.executable, "-B", "-I", str(ROOT / "scripts/workspace.py"), "--workspace", str(self.f.ws),
                "commit", "--im-reply", self.mid, "--im-part", part, "--staging", str(self.stage), "--run", run, *extra]
        result = subprocess.run(args, capture_output=True, encoding="utf-8", env=env, timeout=10)
        self.assertFalse(result.stderr, result.stderr)
        return result.returncode, json.loads(result.stdout) if result.stdout else {}

    def recover(self, crash=None, abandon=False):
        env = dict(os.environ)
        if crash:
            env["JOB_MATE_TEST_CRASH_AT"] = crash
        args = [sys.executable, "-B", "-I", str(ROOT / "scripts/workspace.py"), "--workspace", str(self.f.ws),
                "commit", "--recover", "--run", "setup"]
        if abandon:
            args += ["--abandon", "--confirm-mixed-config"]
        result = subprocess.run(args, capture_output=True, encoding="utf-8", env=env, timeout=10)
        self.assertFalse(result.stderr, result.stderr)
        return result.returncode, json.loads(result.stdout) if result.stdout else {}

    def own_reply(self):
        return next(r for r in self.replies() if r["messageId"] == self.mid)

    def test_f1_consumed_decision_stays_final_across_plan_revisions(self):
        import history_fixture
        decision = {"partId": "answer", "kind": "decision", "state": "executable",
                    "decision": test_notify.D1, "quote": test_notify.D1 + " 已处理"}
        unclear = {"partId": "unclear", "kind": "clarification", "state": "needs_clarification",
                   "quote": "其他看着办", "questionFields": ["hardRequirements"]}
        self.assertEqual(self.plan([decision, unclear])[0], 0)
        history_fixture.append_action(self.f.ws, {"platform": "boss", "account": "synthetic", "runId": "old-apply",
            "skill": "apply", "kind": "greet", "status": "uncertain", "attemptId": "f1-original",
            "company": "合成公司", "recruiter": "合成HR", "decision": test_notify.D1,
            "text": "original exact", "reason": "synthetic history"})
        code, out = self.f.ledger_run("append", "--json", json.dumps({"platform": "boss", "kind": "greet",
            "attemptId": "f1-original", "status": "sent", "skill": "user", "runId": "followup",
            "text": "original exact", "reason": "owner confirmation", "evidence": self.mid}))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.f.run_cmd("reply-state", "--message", self.mid, "--run", "followup",
                                        "--part", "answer", "--state", "consumed")[0], 0)
        extra = {"partId": "salary", "kind": "clarification", "state": "needs_clarification",
                 "quote": "薪资按新范围", "questionFields": ["salary.min", "salary.max"]}
        self.assertEqual(self.plan([decision, unclear, extra], revision=2)[0], 0)
        self.assertEqual(self.own_reply()["parts"][0]["state"], "consumed")
        before = (self.f.ws / "ledger/im.jsonl").read_bytes()
        code, out = self.f.run_cmd("reply-state", "--message", self.mid, "--run", "followup",
                                  "--part", "answer", "--state", "ready")
        self.assertEqual((code, out.get("duplicate")), (0, True), out)
        self.assertEqual((self.f.ws / "ledger/im.jsonl").read_bytes(), before)
        (self.stage / "profile.json").write_bytes(N.profile_bytes(self.profile))
        self.setup_phase()
        code, out = self.commit(part="answer")
        self.assertEqual((code, out.get("code")), (2, "im_change_source_invalid"), out)
        attempts = list(L.attempts_of(L.read_events(self.f.ws)[0]).values())
        self.assertEqual([(a["attemptId"], a["status"]) for a in attempts], [("f1-original", "sent")])

    def test_f2_preference_commit_requires_successful_dependencies(self):
        for state in ("needs_clarification", "rejected"):
            with self.subTest(dependency=state):
                case = ImPreferencesTest(); case.setUp()
                try:
                    parent = {"partId": "parent", "kind": "clarification" if state == "needs_clarification" else "rejected",
                              "state": state, "quote": "其他看着办", "questionFields": ["hardRequirements"]}
                    child = case.preference({"locations": ["上海"]}); child["dependsOn"] = ["parent"]
                    case.prepare(parts=[child, parent]); case.setup_phase()
                    before = (case.f.ws / "profile.json").read_bytes()
                    code, result = case.commit()
                    case.assertEqual((code, result.get("code")), (2, "im_change_baseline_conflict"), result)
                    case.assertEqual((case.f.ws / "profile.json").read_bytes(), before)
                    case.assertFalse((case.f.ws / "backups").exists())
                    case.assertFalse((case.f.ws / ".job-mate-txn.json").exists())
                finally:
                    case.doCleanups()

    def test_f2_decision_requires_successful_not_rejected_dependencies(self):
        for state in ("needs_clarification", "rejected"):
            with self.subTest(dependency=state):
                case = ImPreferencesTest(); case.setUp()
                try:
                    parent = {"partId": "parent", "kind": "clarification" if state == "needs_clarification" else "rejected",
                              "state": state, "quote": "其他看着办"}
                    child = {"partId": "answer", "kind": "decision", "state": "executable", "decision": test_notify.D1,
                             "quote": test_notify.D1 + " 已处理", "dependsOn": ["parent"]}
                    case.assertEqual(case.plan([parent, child])[0], 0)
                    before = (case.f.ws / "ledger/im.jsonl").read_bytes()
                    code, result = case.f.run_cmd("reply-state", "--message", case.mid, "--run", "followup",
                                                 "--part", "answer", "--state", "ready")
                    case.assertEqual((code, result.get("code")), (2, "im_change_baseline_conflict"), result)
                    case.assertEqual((case.f.ws / "ledger/im.jsonl").read_bytes(), before)
                    case.assertEqual(case.own_reply()["parts"][1]["state"], "executable")
                finally:
                    case.doCleanups()

    def test_f2_applied_and_consumed_dependencies_allow_progress(self):
        for state in ("applied", "consumed"):
            with self.subTest(dependency=state):
                case = ImPreferencesTest(); case.setUp()
                try:
                    parent = (case.preference({"targetRoles": ["后端开发"]}, pid="parent") if state == "applied" else
                              {"partId": "parent", "kind": "decision", "state": "executable", "decision": test_notify.D1,
                               "quote": test_notify.D1 + " 已处理"})
                    child = case.preference({"locations": ["上海"]}); child["dependsOn"] = ["parent"]
                    answer = {"partId": "answer", "kind": "decision", "state": "executable", "decision": test_notify.D2,
                              "quote": "其他看着办", "dependsOn": ["parent"]}
                    parts = [child, parent, answer]
                    out = case.prepare(parts=parts)
                    if state == "consumed":
                        case.assertEqual(case.f.run_cmd("reply-state", "--message", case.mid, "--run", "followup",
                                                       "--part", "parent", "--state", "consumed")[0], 0)
                    else:
                        (case.stage / "profile.json").write_bytes(N.profile_bytes(out["targets"]["parent"]))
                        case.setup_phase(); case.assertEqual(case.commit(part="parent")[0], 0)
                        case.assertEqual(case.f.ledger_run("lock", "release", "--run", "setup")[0], 0)
                        case.assertEqual(case.f.ledger_run("lock", "acquire", "--skill", "followup", "--run", "followup")[0], 0)
                        case.prepare(parts=parts, revision=2)
                    case.setup_phase(); case.assertEqual(case.commit()[0], 0)
                    case.assertEqual(case.f.ledger_run("lock", "release", "--run", "setup")[0], 0)
                    case.assertEqual(case.f.ledger_run("lock", "acquire", "--skill", "followup", "--run", "followup")[0], 0)
                    code, result = case.f.run_cmd("reply-state", "--message", case.mid, "--run", "followup",
                                                 "--part", "answer", "--state", "ready")
                    case.assertEqual(code, 0, result)
                    case.assertEqual(case.own_reply()["parts"][2]["state"], "ready")
                finally:
                    case.doCleanups()

    def test_f3_salary_change_cannot_bypass_unresolved_salary_question(self):
        before = (self.f.ws / "profile.json").read_bytes()
        part = self.preference({"salary.min": 20000})
        question = {"partId": "salary-question", "kind": "clarification", "state": "needs_clarification",
                    "quote": "薪资按新范围", "questionFields": ["salary.max"]}
        code, result = self.plan([part, question])
        self.assertEqual((code, result.get("code")), (2, "im_change_fields_forbidden"), result)
        self.assertEqual((self.f.ws / "profile.json").read_bytes(), before)
        self.assertFalse(N.load_state(self.f.ws)["plans"])
        self.assertFalse((self.f.ws / "backups").exists())
        self.assertFalse((self.f.ws / ".job-mate-txn.json").exists())

    def test_c01_c03_single_message_commits_with_backup_and_audit(self):
        before = (self.f.ws / "profile.json").read_bytes()
        self.prepare({"targetRoles": ["后端开发"], "locations": ["上海"], "hardRequirements": ["合成行业"],
                      "salary.currency": "CNY", "salary.period": "month", "salary.taxBasis": "pre_tax",
                      "salary.min": 20000, "salary.max": 30000, "salary.matchRule": "overlap"})
        self.setup_phase()
        code, result = self.commit()
        self.assertEqual(code, 0, result)
        self.assertEqual((self.f.ws / "profile.json").read_bytes(), self.target)
        self.assertEqual((self.f.ws / "backups" / result["backup"] / "profile.json").read_bytes(), before)
        audit = self.own_reply()["preferenceChanges"][0]
        self.assertEqual((audit["status"], audit["messageId"], audit["txnId"], audit["backup"]),
                         ("applied", self.mid, result["txnId"], result["backup"]))
        self.assertEqual(audit["fields"], self.meta["changes"])
        self.assertTrue(audit["effectiveAt"])
        self.assertEqual(len([r for r in N.read_records(self.f.ws) if r["type"] == "in"]), 1)
        self.assertFalse(any(r.get("kind") == "clarify" for r in N.load_state(self.f.ws)["out"].values()))
        self.assertEqual(self.own_reply()["state"], "consumed")

    def test_c05_stale_baseline_never_overwrites_current_profile(self):
        self.prepare()
        self.setup_phase()
        current = json.loads((self.f.ws / "profile.json").read_bytes())
        current.setdefault("style", {})["tone"] = "concise"
        raw = N.profile_bytes(current); (self.f.ws / "profile.json").write_bytes(raw)
        code, result = self.commit()
        self.assertEqual((code, result["code"]), (2, "im_change_baseline_conflict"))
        self.assertEqual((self.f.ws / "profile.json").read_bytes(), raw)
        self.assertFalse((self.f.ws / "backups").exists())
        self.assertFalse((self.f.ws / ".job-mate-txn.json").exists())
        self.assertEqual(self.f.ledger_run("lock", "release", "--run", "setup")[0], 0)
        self.assertEqual(self.f.ledger_run("lock", "acquire", "--skill", "followup", "--run", "followup")[0], 0)
        # 无关字段变动，可从同一条原消息重派生技术计划，无第二条确认。
        self.prepare(revision=2)
        self.setup_phase()
        self.assertEqual(self.commit()[0], 0)
        self.assertEqual(json.loads((self.f.ws / "profile.json").read_bytes())["style"]["tone"], "concise")

    def test_c05_target_field_conflict_requires_clarification(self):
        self.prepare()
        current = copy.deepcopy(self.profile); current["locations"] = ["合成另一城市"]
        (self.f.ws / "profile.json").write_bytes(N.profile_bytes(current))
        code, result = self.plan([self.preference({"locations": ["上海"]})], revision=2)
        self.assertEqual((code, result["code"]), (2, "im_change_baseline_conflict"))
        self.assertEqual(json.loads((self.f.ws / "profile.json").read_bytes())["locations"], ["合成另一城市"])

    def test_c06_transaction_and_audit_crash_windows(self):
        case = ImPreferencesTest(); case.setUp()
        try:
            before = (case.f.ws / "profile.json").read_bytes()
            requested = {"revision": 1, "parts": [case.preference({"locations": ["上海"]})]}
            child = subprocess.run([sys.executable, "-B", "-I", str(ROOT / "scripts/notify.py"), "--workspace", str(case.f.ws),
                "reply-state", "--message", case.mid, "--run", "followup", "--plan", json.dumps(requested)],
                capture_output=True, encoding="utf-8", timeout=10, env=dict(os.environ, JOB_MATE_TEST_CRASH_AT="im_after_plan"))
            case.assertEqual(child.returncode, 70)
            case.assertEqual((case.f.ws / "profile.json").read_bytes(), before)
            case.assertFalse((case.f.ws / ".job-mate-txn.json").exists())
            case.prepare({"locations": ["上海"]}, revision=2); case.setup_phase()
            case.assertEqual(case.commit()[0], 0)
            case.assertEqual(len([r for r in N.read_records(case.f.ws) if r["type"] == "in"]), 1)
        finally:
            case.doCleanups()
        for point in ("after_journal", "after_file_1", "im_after_applied_audit", "im_after_journal_delete"):
            with self.subTest(point=point):
                case = ImPreferencesTest(); case.setUp()
                try:
                    before = (case.f.ws / "profile.json").read_bytes()
                    case.prepare(); case.setup_phase()
                    case.assertEqual(case.commit(crash=point)[0], 70)
                    if point == "im_after_journal_delete":
                        case.assertFalse((case.f.ws / ".job-mate-txn.json").exists())
                        case.assertEqual(case.own_reply()["preferenceChanges"][0]["status"], "applied")
                        before_backups = sorted((case.f.ws / "backups").iterdir())
                        code, out = case.commit()
                        case.assertEqual((code, out["duplicate"]), (0, True))
                        case.assertEqual(sorted((case.f.ws / "backups").iterdir()), before_backups)
                        case.assertEqual(case.own_reply()["parts"][0]["state"], "applied")
                    else:
                        case.assertTrue((case.f.ws / ".job-mate-txn.json").exists())
                        if point == "im_after_applied_audit":
                            case.assertEqual(case.own_reply()["preferenceChanges"][0]["status"], "pending")
                        case.assertEqual(case.recover(crash="im_after_rollback_audit")[0], 70)
                        case.assertEqual(case.recover()[0], 0)
                        case.assertEqual((case.f.ws / "profile.json").read_bytes(), before)
                        case.assertEqual(case.own_reply()["preferenceChanges"][0]["status"], "rolled_back")
                        records = [r for r in N.read_records(case.f.ws) if r["type"] == "preference_change" and r["status"] == "rolled_back"]
                        case.assertEqual(len(records), 1)
                finally:
                    case.doCleanups()

    def test_c08_only_external_frozen_profile_is_accepted(self):
        self.prepare(); self.setup_phase()
        before = (self.f.ws / "profile.json").read_bytes()
        for name in ("stoplist.yaml", "candidate.md", "qa.yaml", "rules.md", "job-mate.json", "unknown.json"):
            with self.subTest(name=name):
                (self.stage / name).write_text("synthetic", encoding="utf-8")
                code, result = self.commit()
                self.assertEqual((code, result["code"]), (2, "im_change_fields_forbidden"))
                self.assertEqual((self.f.ws / "profile.json").read_bytes(), before)
                self.assertFalse((self.f.ws / ".job-mate-txn.json").exists())
                self.assertFalse((self.f.ws / "backups").exists())
                (self.stage / name).unlink()
        forged = json.loads(self.target); forged["authorization"]["apply"]["send"] = not forged["authorization"]["apply"]["send"]
        (self.stage / "profile.json").write_bytes(N.profile_bytes(forged))
        code, result = self.commit()
        self.assertEqual((code, result["code"]), (2, "im_change_fields_forbidden"))
        self.assertEqual((self.f.ws / "profile.json").read_bytes(), before)

    def test_c08_internal_stage_and_symlink_are_rejected_before_backup(self):
        self.prepare(); self.setup_phase()
        original_stage = self.stage
        inside = self.f.ws / "inside-stage"; inside.mkdir()
        (inside / "profile.json").write_bytes(self.target)
        self.stage = inside
        code, result = self.commit()
        self.assertEqual((code, result["code"]), (2, "im_change_fields_forbidden"))
        self.stage = original_stage
        from test_build import require_symlinks
        require_symlinks(self)
        saved = self.f.ws.parent / (self.f.ws.name + "-untrusted.json")
        saved.write_bytes(self.target); self.addCleanup(lambda: saved.unlink(missing_ok=True))
        (self.stage / "profile.json").unlink()
        (self.stage / "profile.json").symlink_to(saved)
        code, result = self.commit()
        self.assertEqual((code, result["code"]), (2, "im_change_fields_forbidden"))
        self.assertFalse((self.f.ws / "backups").exists())

    def test_c16_stage_source_replacement_does_not_change_frozen_commit(self):
        self.prepare(); self.setup_phase()
        original = W.config_txn.validate
        replaced = []
        def replace_after_read(staged):
            if not replaced:
                forged = json.loads(self.target); forged["locations"] = ["unapproved"]
                (self.stage / "profile.json").write_bytes(N.profile_bytes(forged))
                replaced.append(True)
            return original(staged)
        import contextlib, io
        output = io.StringIO()
        with patch.object(W.config_txn, "validate", side_effect=replace_after_read), contextlib.redirect_stdout(output):
            code = W.main(["--workspace", str(self.f.ws), "commit", "--staging", str(self.stage), "--run", "setup",
                           "--im-reply", self.mid, "--im-part", "preferences"])
        self.assertEqual(code, 0, output.getvalue())
        self.assertEqual((self.f.ws / "profile.json").read_bytes(), self.target)
        self.assertNotEqual((self.stage / "profile.json").read_bytes(), self.target)

    def test_c10_next_round_prepare_uses_new_profile_revision(self):
        before_rev = L.config_rev(self.profile)
        self.prepare(); self.setup_phase(); self.assertEqual(self.commit()[0], 0)
        profile = json.loads((self.f.ws / "profile.json").read_bytes())
        self.assertNotEqual(L.config_rev(profile), before_rev)
        self.assertEqual(self.f.ledger_run("lock", "release", "--run", "setup")[0], 0)
        self.assertEqual(self.f.ledger_run("lock", "acquire", "--skill", "followup", "--run", "next")[0], 0)
        message = self.f.im.user_says("D-20261030-01 原话发：approved exact")
        self.assertEqual(self.f.incoming(self.f.im.messages)[0], 0)
        event = {"platform": "boss", "company": "合成公司", "recruiter": "合成HR", "runId": "next", "skill": "followup",
                 "kind": "observe", "observed": "decision", "decision": test_notify.D1, "version": 1,
                 "steps": [{"step": "approved", "kind": "reply", "text": "approved exact"}], "reason": message}
        self.assertEqual(self.f.ledger_run("append", "--json", json.dumps(event))[0], 0)
        intent = {"platform": "boss", "company": "合成公司", "recruiter": "合成HR", "kind": "reply", "decision": test_notify.D1,
                  "step": "approved", "text": "approved exact", "reason": message}
        code, result = self.f.ledger_run("prepare", "--run", "next", "--json", json.dumps(intent))
        self.assertEqual(code, 0, result)
        pending = next(e for e in L.read_events(self.f.ws)[0] if e.get("attemptId") == result["attemptId"])
        self.assertEqual(pending["profileRev"], L.config_rev(profile))
        code, guard = self.f.ledger_run("guard", "--run", "next", "--attempt", result["attemptId"])
        self.assertEqual(code, 0, guard); self.assertTrue(guard["allowed"])

    def test_c11_duplicate_commit_is_audited_idempotence_not_value_guessing(self):
        self.prepare(); self.setup_phase()
        code, first = self.commit(); self.assertEqual(code, 0, first)
        before = N.read_records(self.f.ws)
        code, duplicate = self.commit()
        self.assertEqual((code, duplicate["duplicate"], duplicate["txnId"]), (0, True, first["txnId"]))
        self.assertEqual(N.read_records(self.f.ws), before)
        self.assertEqual(len(list((self.f.ws / "backups").iterdir())), 1)

    def test_c13_setup_phase_and_run_are_required(self):
        self.prepare()
        original = (self.f.ws / "profile.json").read_bytes()
        for skill in ("followup", "apply", "setup"):
            if skill != "followup":
                self.assertEqual(self.f.ledger_run("lock", "acquire", "--skill", skill, "--run", skill)[0], 0)
            code, result = self.commit(run=skill if skill != "setup" else "forged")
            self.assertEqual((code, result["code"]), (2, "setup_lock_required"))
            self.assertEqual(self.f.ledger_run("lock", "release", "--run", skill)[0], 0)
        import fsutil
        with fsutil.try_locked(self.f.ws / "ledger/.im-watch.lock") as held:
            self.assertTrue(held)
            code, result = self.commit(run="watch")
            self.assertEqual((code, result["code"]), (2, "setup_lock_required"))
        self.assertEqual(self.f.ledger_run("lock", "acquire", "--skill", "setup", "--run", "recovery", "--recovery")[0], 0)
        code, result = self.commit(run="recovery")
        self.assertEqual((code, result["code"]), (2, "setup_lock_required"))
        self.assertEqual((self.f.ws / "profile.json").read_bytes(), original)
        self.assertFalse((self.f.ws / ".job-mate-txn.json").exists())
        self.assertFalse(any(r.get("status") == "applied" for r in N.read_records(self.f.ws)))

    def test_c13_expired_and_releasing_setup_lock_are_rejected(self):
        self.prepare(); self.setup_phase()
        path = self.f.ws / "ledger/lock.json"
        saved = path.read_bytes()
        for change in ({"expiresAt": "2026-11-09T00:00:00+08:00"}, {"releasing": True}):
            lock = json.loads(saved); lock.update(change); path.write_text(json.dumps(lock), encoding="utf-8")
            code, result = self.commit()
            self.assertEqual((code, result["code"]), (2, "setup_lock_required"))
            self.assertFalse((self.f.ws / ".job-mate-txn.json").exists())
        path.write_bytes(saved)
        for extra in (("--im-reply", "", "--im-part", ""), ("--stoplist-ops", "forbidden")):
            self.assertEqual(self.commit(extra=extra)[0], 2)
        self.assertFalse((self.f.ws / "backups").exists())

    def test_c06_optional_metadata_corruption_and_abandon_fail_closed(self):
        self.prepare(); self.setup_phase()
        self.assertEqual(self.commit(crash="after_journal")[0], 70)
        path = self.f.ws / ".job-mate-txn.json"
        saved = path.read_bytes(); obj = json.loads(saved)
        obj["imChange"]["unexpected"] = True; path.write_text(json.dumps(obj), encoding="utf-8")
        self.assertEqual(self.recover()[0], 2)
        self.assertTrue(path.exists())
        path.write_bytes(saved)
        self.assertEqual(self.recover(abandon=True)[0], 0)
        self.assertEqual(self.own_reply()["preferenceChanges"][0]["status"], "needs_review")

    def test_c14_audit_survives_consumption_and_reporting(self):
        case = ImPreferencesTest(); case.setUp()
        try:
            before = (case.f.ws / "profile.json").read_bytes()
            case.prepare(); case.setup_phase()
            original = N.append_record
            def failed_audit(ws, record, tz):
                if record["type"] == "preference_change":
                    raise OSError("synthetic audit failure")
                return original(ws, record, tz)
            import contextlib, io
            output = io.StringIO()
            with patch.object(N, "append_record", side_effect=failed_audit), contextlib.redirect_stdout(output):
                code = W.main(["--workspace", str(case.f.ws), "commit", "--staging", str(case.stage), "--run", "setup",
                               "--im-reply", case.mid, "--im-part", "preferences"])
            case.assertEqual(code, 2, output.getvalue())
            case.assertTrue((case.f.ws / ".job-mate-txn.json").exists())
            case.assertEqual((case.f.ws / "profile.json").read_bytes(), before)
            case.assertFalse(any(r.get("status") == "applied" for r in N.read_records(case.f.ws)))
            case.assertEqual(case.recover()[0], 0)
            case.assertEqual(case.own_reply()["preferenceChanges"][0]["status"], "rolled_back")
        finally:
            case.doCleanups()
        self.prepare(); self.setup_phase(); self.assertEqual(self.commit()[0], 0)
        before = self.own_reply()["preferenceChanges"]
        self.assertEqual(self.own_reply()["state"], "consumed")
        self.assertFalse(before[0]["reported"])
        self.assertEqual(self.f.ledger_run("lock", "release", "--run", "setup")[0], 0)
        self.assertEqual(self.f.ledger_run("lock", "acquire", "--skill", "followup", "--run", "followup")[0], 0)
        self.assertEqual(self.f.run_cmd("reply-state", "--message", self.mid, "--run", "followup", "--reported", before[0]["operationId"])[0], 0)
        self.assertTrue(self.own_reply()["preferenceChanges"][0]["reported"])
        for key in ("messageId", "fields", "effectiveAt", "txnId", "backup", "beforeHash", "afterHash"):
            self.assertEqual(self.own_reply()["preferenceChanges"][0][key], before[0][key])

    def test_c15_explicit_reverse_is_an_independent_single_message(self):
        old = self.profile["locations"]
        self.prepare({"locations": ["上海"]}); self.setup_phase(); self.assertEqual(self.commit()[0], 0)
        self.assertEqual(self.f.ledger_run("lock", "release", "--run", "setup")[0], 0)
        self.profile = json.loads((self.f.ws / "profile.json").read_bytes())
        self.text = "地点明确改回原值"
        self.mid = self.f.im.user_says(self.text)
        self.assertEqual(self.f.incoming(self.f.im.messages)[0], 0)
        self.assertEqual(self.f.ledger_run("lock", "acquire", "--skill", "followup", "--run", "followup")[0], 0)
        self.prepare({"locations": old}); self.setup_phase(); self.assertEqual(self.commit()[0], 0)
        self.assertEqual(json.loads((self.f.ws / "profile.json").read_bytes())["locations"], old)
        records = [r for r in N.read_records(self.f.ws) if r["type"] == "preference_change"]
        self.assertEqual(len({r["operationId"] for r in records}), 2)

    def test_c16_independent_groups_commit_but_ambiguous_salary_stays(self):
        parts = [self.preference({"locations": ["上海"], "targetRoles": ["后端开发"]}, members=["city", "roles"]),
                 {"partId": "salary", "kind": "clarification", "quote": "薪资按新范围", "state": "needs_clarification",
                  "questionFields": ["salary.min", "salary.max"]}]
        salary = self.profile["salary"]
        self.prepare(parts=parts); self.setup_phase(); self.assertEqual(self.commit()[0], 0)
        profile = json.loads((self.f.ws / "profile.json").read_bytes())
        self.assertEqual(profile["salary"], salary)
        self.assertEqual((profile["locations"], profile["targetRoles"]), (["上海"], ["后端开发"]))
        self.assertEqual(self.own_reply()["state"], "received")
        self.assertEqual([p["state"] for p in self.own_reply()["parts"]], ["applied", "needs_clarification"])

    def test_w28_c12_apply_confirmation_and_followup_decision_remain_separate(self):
        import history_fixture
        for status, answer in (("sent", "已发出"), ("failed_no_send", "未发出")):
            with self.subTest(status=status):
                case = ImPreferencesTest(); case.setUp()
                try:
                    old = {"platform": "boss", "account": "synthetic", "runId": "old-apply", "skill": "apply",
                           "kind": "greet", "status": "uncertain", "attemptId": "original-apply", "company": "原公司",
                           "recruiter": "原HR", "decision": test_notify.D1, "text": "original exact text", "reason": "synthetic history"}
                    history_fixture.append_action(case.f.ws, old)
                    case.text = f"{test_notify.D1} {answer}；{test_notify.D2} 原话发：approved exact"
                    case.mid = case.f.im.user_says(case.text)
                    case.assertEqual(case.f.incoming(case.f.im.messages)[0], 0)
                    parts = [{"partId": "apply-result", "kind": "decision", "state": "executable", "decision": test_notify.D1,
                              "quote": f"{test_notify.D1} {answer}"},
                             {"partId": "reply", "kind": "decision", "state": "executable", "decision": test_notify.D2,
                              "quote": f"{test_notify.D2} 原话发：approved exact"}]
                    case.assertEqual(case.plan(parts)[0], 0)
                    terminal = {"platform": "boss", "account": "synthetic", "kind": "greet", "attemptId": "original-apply",
                                "status": status, "skill": "user", "runId": "followup", "reason": "owner confirmation",
                                "evidence": case.mid, "text": old["text"]}
                    code, result = case.f.ledger_run("append", "--json", json.dumps(terminal))
                    case.assertEqual(code, 0, result)
                    case.assertEqual(case.f.run_cmd("reply-state", "--message", case.mid, "--run", "followup", "--part", "apply-result", "--state", "consumed")[0], 0)
                    case.assertNotEqual(case.own_reply()["state"], "consumed")
                    # 跨过真实最小发送间隔，按既有本人决定走 prepare/guard，而不是制造新 apply。
                    os.environ["JOB_MATE_NOW"] = "2026-11-10T10:01:00+08:00"
                    declaration = {"platform": "boss", "company": "另一公司", "recruiter": "另一HR", "runId": "followup", "skill": "followup",
                        "kind": "observe", "observed": "decision", "decision": test_notify.D2, "version": 1,
                        "steps": [{"step": "approved", "kind": "reply", "text": "approved exact"}], "reason": "synthetic owner words"}
                    code, result = case.f.ledger_run("append", "--json", json.dumps(declaration)); case.assertEqual(code, 0, result)
                    intent = {"platform": "boss", "company": "另一公司", "recruiter": "另一HR", "kind": "reply", "decision": test_notify.D2,
                              "step": "approved", "text": "approved exact", "reason": "synthetic owner words"}
                    code, result = case.f.ledger_run("prepare", "--run", "followup", "--json", json.dumps(intent))
                    case.assertEqual(code, 0, result)
                    attempt = result["attemptId"]
                    code, guard = case.f.ledger_run("guard", "--run", "followup", "--attempt", attempt)
                    case.assertEqual(code, 0, guard); case.assertTrue(guard["allowed"])
                    fake_platform = [{"kind": "reply", "text": intent["text"], "attemptId": attempt}]
                    case.assertEqual(fake_platform[0]["text"], "approved exact")
                    code, result = case.f.ledger_run("append", "--json", json.dumps({"platform": "boss", "kind": "reply", "attemptId": attempt,
                        "status": "sent", "runId": "followup", "skill": "followup", "reason": "synthetic visible send", "evidence": "fake platform"}))
                    case.assertEqual(code, 0, result)
                    case.assertEqual(case.f.run_cmd("reply-state", "--message", case.mid, "--run", "followup", "--part", "reply", "--state", "consumed")[0], 0)
                    case.assertEqual(case.own_reply()["state"], "consumed")
                    attempts = list(L.attempts_of(L.read_events(case.f.ws)[0]).values())
                    case.assertEqual(len([a for a in attempts if a["kind"] == "greet"]), 1)
                    application = next(a for a in attempts if a["kind"] == "greet")
                    case.assertEqual((application["attemptId"], application["status"]), ("original-apply", status))
                    case.assertEqual(application["events"][-1]["skill"], "user")
                    case.assertEqual(application["events"][-1]["text"], old["text"])
                    case.assertEqual(len([a for a in attempts if a["kind"] == "reply"]), 1)
                finally:
                    case.doCleanups()

    def test_w29_preference_commit_never_adds_apply_attempts_or_grants(self):
        for barrier in ("apply_disabled", "no_send", "restriction", "stoplist", "pause"):
            with self.subTest(barrier=barrier):
                case = ImPreferencesTest(); case.setUp()
                try:
                    case.profile["authorization"]["apply"]["send"] = False
                    (case.f.ws / "profile.json").write_bytes(N.profile_bytes(case.profile))
                    if barrier in ("no_send", "restriction"):
                        case.assertEqual(case.f.ledger_run("lock", "release", "--run", "followup")[0], 0)
                        flags = ["--no-send"] if barrier == "no_send" else ["--restrict", json.dumps(
                            {"kinds": ["reply"], "scope": {"all": True}, "max": 1, "quote": "synthetic restriction"})]
                        case.assertEqual(case.f.ledger_run("lock", "acquire", "--skill", "followup", "--run", "followup", *flags)[0], 0)
                    if barrier == "stoplist":
                        case.assertEqual(case.f.ledger_run("stoplist", "add", "--name", "合成停止公司", "--reason", "synthetic", "--source", "user")[0], 0)
                    if barrier == "pause":
                        case.assertEqual(case.f.ledger_run("pause", "on", "--by", "user", "--reason", "synthetic")[0], 0)
                    controls = {name: (case.f.ws / name).read_bytes() if (case.f.ws / name).exists() else None
                                for name in ("ledger/pause.json", "stoplist.yaml")}
                    case.prepare(); case.setup_phase(); case.assertEqual(case.commit()[0], 0)
                    case.assertFalse(json.loads((case.f.ws / "profile.json").read_bytes())["authorization"]["apply"]["send"])
                    case.assertEqual(L.attempts_of(L.read_events(case.f.ws)[0]), {})
                    for name, raw in controls.items():
                        case.assertEqual((case.f.ws / name).read_bytes() if (case.f.ws / name).exists() else None, raw)
                    case.assertEqual(case.f.ledger_run("lock", "release", "--run", "setup")[0], 0)
                    case.assertEqual(case.f.ledger_run("lock", "acquire", "--skill", "followup", "--run", "next", "--no-send")[0], 0)
                    lock = L.read_lock(case.f.ws)
                    case.assertTrue(lock["noSend"]); case.assertEqual(lock["grants"], [])
                finally:
                    case.doCleanups()

    def test_f9_prepare_checks_each_barrier_after_im_commit(self):
        reasons = {"apply_disabled": "not_authorized", "no_send": "no_send", "restriction": "run_kind_restricted",
                   "stoplist": "stoplist", "pause": "paused"}
        for barrier, expected in reasons.items():
            with self.subTest(barrier=barrier):
                case = ImPreferencesTest(); case.setUp()
                try:
                    flags = (["--no-send"] if barrier == "no_send" else ["--restrict", json.dumps(
                        {"kinds": ["reply"], "scope": {"all": True}, "max": 1, "quote": "synthetic restriction"})]
                        if barrier == "restriction" else [])
                    if flags:
                        case.assertEqual(case.f.ledger_run("lock", "release", "--run", "followup")[0], 0)
                        case.assertEqual(case.f.ledger_run("lock", "acquire", "--skill", "followup", "--run", "followup", *flags)[0], 0)
                    if barrier == "apply_disabled":
                        case.profile["authorization"]["apply"]["send"] = False
                        (case.f.ws / "profile.json").write_bytes(N.profile_bytes(case.profile))
                    elif barrier == "stoplist":
                        case.assertEqual(case.f.ledger_run("stoplist", "add", "--name", "合成停止公司", "--reason", "synthetic", "--source", "user")[0], 0)
                    elif barrier == "pause":
                        case.assertEqual(case.f.ledger_run("pause", "on", "--by", "user", "--reason", "synthetic")[0], 0)
                    case.prepare(); case.setup_phase(); case.assertEqual(case.commit()[0], 0)
                    case.assertEqual(case.f.ledger_run("lock", "release", "--run", "setup")[0], 0)
                    case.assertEqual(case.f.ledger_run("lock", "acquire", "--skill", "apply", "--run", "probe", *flags)[0], 0)
                    intent = {"platform": "boss", "kind": "greet", "company": "合成停止公司", "recruiter": "合成HR",
                              "title": "后端开发", "targetRole": "后端开发", "text": "Hello, interested in this role.", "reason": "synthetic probe"}
                    code, result = case.f.ledger_run("prepare", "--run", "probe", "--json", json.dumps(intent))
                    case.assertEqual(code, 1, result)
                    case.assertFalse(result["allowed"])
                    case.assertEqual(result["reasons"][0]["code"], expected)
                    case.assertEqual(L.attempts_of(L.read_events(case.f.ws)[0]), {})
                finally:
                    case.doCleanups()

    def test_f9_guard_rechecks_live_barriers_after_im_commit(self):
        reasons = {"apply_disabled": "not_authorized", "no_send": "lock_invalid", "restriction": "lock_invalid",
                   "stoplist": "stoplist", "pause": "paused"}
        for barrier, expected in reasons.items():
            with self.subTest(barrier=barrier):
                case = ImPreferencesTest(); case.setUp()
                try:
                    case.prepare(); case.setup_phase(); case.assertEqual(case.commit()[0], 0)
                    case.assertEqual(case.f.ledger_run("lock", "release", "--run", "setup")[0], 0)
                    case.assertEqual(case.f.ledger_run("lock", "acquire", "--skill", "apply", "--run", "probe")[0], 0)
                    intent = {"platform": "boss", "kind": "greet", "company": "合成停止公司", "recruiter": "合成HR",
                              "title": "后端开发", "targetRole": "后端开发", "text": "Hello, interested in this role.", "reason": "synthetic probe"}
                    code, result = case.f.ledger_run("prepare", "--run", "probe", "--json", json.dumps(intent))
                    case.assertEqual(code, 0, result)
                    attempt = result["attemptId"]
                    code, result = case.f.ledger_run("guard", "--run", "probe", "--attempt", attempt)
                    case.assertEqual(code, 0, result); case.assertTrue(result["allowed"])
                    if barrier == "apply_disabled":
                        profile = L.load_profile(case.f.ws); profile["authorization"]["apply"]["send"] = False
                        (case.f.ws / "profile.json").write_bytes(N.profile_bytes(profile))
                    elif barrier in ("no_send", "restriction"):
                        # 故障刺激：既有 pending 后篡改冻结策略；不伪造 pending 或冒称 CLI 允许改策略。
                        with L.lock_mutex(case.f.ws):
                            lock = L.read_lock(case.f.ws)
                            if barrier == "no_send":
                                lock["noSend"] = True
                            else:
                                lock["restriction"] = {"kinds": ["reply"], "scope": {"all": True}, "max": 1,
                                                       "quote": "synthetic changed policy"}
                            L.write_lock(case.f.ws, lock)
                    elif barrier == "stoplist":
                        case.assertEqual(case.f.ledger_run("stoplist", "add", "--name", "合成停止公司", "--reason", "synthetic", "--source", "user")[0], 0)
                    else:
                        case.assertEqual(case.f.ledger_run("pause", "on", "--by", "user", "--reason", "synthetic")[0], 0)
                    events = L.read_events(case.f.ws)[0]
                    code, result = case.f.ledger_run("guard", "--run", "probe", "--attempt", attempt)
                    case.assertEqual(code, 1, result); case.assertFalse(result["allowed"])
                    case.assertEqual(result["reasons"][0]["code"], expected)
                    case.assertEqual(L.read_events(case.f.ws)[0], events)
                    case.assertEqual(len(L.attempts_of(events)), 1)
                finally:
                    case.doCleanups()

    def test_c02_field_whitelist_and_frozen_target(self):
        fields = {"targetRoles": ["后端开发"], "locations": ["上海"], "hardRequirements": ["行业：合成行业"],
                  "recruiterActivity.maxAgeDays": 7, "salary.currency": "CNY", "salary.period": "month",
                  "salary.taxBasis": "pre_tax", "salary.min": 20000, "salary.max": 30000,
                  "salary.matchRule": "overlap", "exclusions.roleTypes": [], "exclusions.employmentTypes": [],
                  "exclusions.workSchedules": [], "exclusions.keywords": ["合成排除行业"]}
        before = (self.f.ws / "profile.json").read_bytes()
        code, result = self.plan([self.preference(fields)])
        self.assertEqual(code, 0, result)
        self.assertEqual((self.f.ws / "profile.json").read_bytes(), before)
        self.assertEqual(set(fields), N.PREFERENCE_FIELDS)
        part = result["plan"]["parts"][0]
        self.assertEqual(part["beforeHash"], hashlib.sha256(before).hexdigest())
        target = N.profile_bytes(result["targets"]["preferences"])
        self.assertEqual(part["afterHash"], hashlib.sha256(target).hexdigest())
        self.assertEqual(len([r for r in N.read_records(self.f.ws) if r["type"] == "in"]), 1)
        self.assertFalse(any(r.get("kind") == "clarify" for r in N.load_state(self.f.ws)["out"].values()))

    def test_c04_ambiguous_group_has_no_implicit_values(self):
        before = (self.f.ws / "profile.json").read_bytes()
        part = {"partId": "unclear", "kind": "clarification", "quote": "其他看着办",
                "state": "needs_clarification", "questionFields": ["hardRequirements"]}
        self.assertEqual(self.plan([part])[0], 0)
        self.assertEqual((self.f.ws / "profile.json").read_bytes(), before)
        reply = next(r for r in self.replies() if r["messageId"] == self.mid)
        self.assertEqual(reply["parts"][0]["state"], "needs_clarification")

    def test_c07_forbidden_fields_cannot_enter_plan(self):
        for field in ("authorization.apply.send", "authorization.followup.reply", "authorization.followup.nudge",
                      "limits.dailyGreetings", "limits.platforms.boss.dailyGreetings", "nudge.maxPerThread",
                      "schedule.timezone", "browser.headedInput", "im.waitMinutes", "restrict", "grant",
                      "style.tone", "greeting.text", "inquiry.priority", "industry"):
            with self.subTest(field=field):
                before = (self.f.ws / "profile.json").read_bytes()
                code, result = self.plan([self.preference({field: True})])
                self.assertEqual((code, result["code"]), (2, "im_change_fields_forbidden"))
                self.assertEqual((self.f.ws / "profile.json").read_bytes(), before)
        self.assertFalse(N.load_state(self.f.ws)["plans"])

    def test_c09_parts_and_dependencies_prevent_whole_message_consumption(self):
        parts = [self.preference({"targetRoles": ["后端开发"], "locations": ["上海"]}, members=["roles", "city"]),
                 {"partId": "answer", "kind": "decision", "quote": "D-20261030-01 已处理", "decision": test_notify.D1,
                  "state": "executable", "dependsOn": ["preferences"]},
                 {"partId": "unclear", "kind": "clarification", "quote": "其他看着办", "state": "needs_clarification"}]
        self.assertEqual(self.plan(parts)[0], 0)
        code, result = self.f.run_cmd("reply-state", "--message", self.mid, "--run", "followup", "--part", "answer", "--state", "ready")
        self.assertEqual((code, result["code"]), (2, "im_change_baseline_conflict"))
        reply = next(r for r in self.replies() if r["messageId"] == self.mid)
        self.assertNotEqual(reply["state"], "consumed")
        self.assertEqual(reply["parts"][0]["members"], ["roles", "city"])

    def test_c11_message_replay_and_c13_plan_stage_guard(self):
        self.assertEqual(self.f.incoming(self.f.im.messages)[0], 0)
        self.assertEqual(len([r for r in N.read_records(self.f.ws) if r["type"] == "in"]), 1)
        self.assertEqual(self.f.ledger_run("lock", "release", "--run", "followup")[0], 0)
        for skill in ("apply", "setup"):
            self.assertEqual(self.f.ledger_run("lock", "acquire", "--skill", skill, "--run", skill)[0], 0)
            code, result = self.plan([self.preference({"locations": ["上海"]})])
            self.assertEqual((code, result["code"]), (2, "followup_lock_required"))
            self.assertEqual(self.f.ledger_run("lock", "release", "--run", skill)[0], 0)

    def test_w24_question_identity_does_not_change_with_wording_or_custom_key(self):
        part = {"partId": "unclear", "kind": "clarification", "quote": "其他看着办", "state": "needs_clarification"}
        self.assertEqual(self.plan([part])[0], 0)
        first = self.f.queue("different-key-1", "question one", "clarify", "--reply-to", self.mid)
        second = self.f.queue("different-key-2", "question two", "clarify", "--reply-to", self.mid)
        self.assertEqual(first[1]["key"], second[1]["key"])
        self.assertEqual(len([r for r in N.load_state(self.f.ws)["out"].values() if r.get("kind") == "clarify"]), 1)


if __name__ == "__main__":
    unittest.main()

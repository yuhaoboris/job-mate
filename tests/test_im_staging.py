"""IM 计划直接提交：真实 CLI、临时工作区，不由调用方复制或序列化目标配置。"""
import copy
import hashlib
import json
import os
import subprocess
import sys
import unittest

import test_im_preferences as preferences

N = preferences.N
ROOT = preferences.ROOT


class ImStagingTest(unittest.TestCase):
    def setUp(self):
        self.case = preferences.ImPreferencesTest()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.ws = self.case.f.ws

    def plan(self, parts=None):
        code, result = self.case.plan(parts or [self.case.preference({"locations": ["上海"]})])
        self.assertEqual(code, 0, result)
        self.assertEqual(list(self.case.stage.iterdir()), [])
        return result

    def cli(self, *args, crash=None):
        env = dict(os.environ)
        if crash:
            env["JOB_MATE_TEST_CRASH_AT"] = crash
        return subprocess.run(
            [sys.executable, "-B", "-I", str(ROOT / "scripts/workspace.py"),
             "--workspace", str(self.ws), "commit", *args],
            capture_output=True, encoding="utf-8", env=env, timeout=10)

    def commit(self, run="setup", message=None, part="preferences", extra=(), crash=None):
        result = self.cli("--run", run, "--im-reply", message or self.case.mid,
                          "--im-part", part, *extra, crash=crash)
        self.assertFalse(result.stderr, result.stderr)
        return result.returncode, json.loads(result.stdout) if result.stdout else {}

    def assert_uncommitted(self, before):
        self.assertEqual((self.ws / "profile.json").read_bytes(), before)
        self.assertFalse((self.ws / "backups").exists())
        self.assertFalse((self.ws / ".job-mate-txn.json").exists())
        self.assertFalse(N.load_state(self.ws)["changes"])

    def test_saved_plan_commits_without_a_staging_directory(self):
        before = (self.ws / "profile.json").read_bytes()
        plan = self.plan()
        self.case.stage.rmdir()
        self.case.setup_phase()
        code, result = self.commit()
        self.assertEqual(code, 0, result)
        self.assertTrue(result["committed"])
        actual = (self.ws / "profile.json").read_bytes()
        self.assertEqual(hashlib.sha256(actual).hexdigest(), plan["plan"]["parts"][0]["afterHash"])
        expected = json.loads(before)
        expected.update(locations=["上海"], updatedAt=plan["plan"]["parts"][0]["updatedAt"])
        self.assertEqual(json.loads(actual), expected)
        self.assertEqual((self.ws / "backups" / result["backup"] / "profile.json").read_bytes(), before)
        audit = self.case.own_reply()["preferenceChanges"][0]
        self.assertEqual((audit["status"], audit["messageId"], audit["txnId"]),
                         ("applied", self.case.mid, result["txnId"]))
        self.assertEqual(self.case.own_reply()["state"], "consumed")
        self.assertFalse(self.case.stage.exists())

    def test_legacy_staging_remains_compatible(self):
        self.case.prepare({"locations": ["上海"]})
        self.case.setup_phase()
        code, result = self.case.commit()
        self.assertEqual(code, 0, result)
        self.assertEqual((self.ws / "profile.json").read_bytes(), self.case.target)

    def test_duplicate_uses_audit_and_preserves_later_configuration(self):
        self.plan()
        self.case.setup_phase()
        code, first = self.commit()
        self.assertEqual(code, 0, first)
        current = json.loads((self.ws / "profile.json").read_bytes())
        current["locations"] = ["深圳"]
        (self.ws / "profile.json").write_bytes(N.profile_bytes(current))
        before = (self.ws / "profile.json").read_bytes()
        records = (self.ws / "ledger/im.jsonl").read_bytes()
        code, duplicate = self.commit()
        self.assertEqual((code, duplicate.get("duplicate")), (0, True), duplicate)
        self.assertEqual((duplicate["txnId"], duplicate["backup"]), (first["txnId"], first["backup"]))
        self.assertEqual((self.ws / "profile.json").read_bytes(), before)
        self.assertEqual((self.ws / "ledger/im.jsonl").read_bytes(), records)
        self.assertEqual(len(list((self.ws / "backups").iterdir())), 1)

    def test_changed_baseline_is_rejected_before_backup(self):
        self.plan()
        self.case.setup_phase()
        changed = copy.deepcopy(self.case.profile)
        changed["locations"] = ["广州"]
        before = N.profile_bytes(changed)
        (self.ws / "profile.json").write_bytes(before)
        code, result = self.commit()
        self.assertEqual((code, result.get("code")), (2, "im_change_baseline_conflict"), result)
        self.assert_uncommitted(before)

    def test_non_object_profile_returns_the_existing_structured_error(self):
        self.case.prepare({"salary.min": 20000})
        self.case.setup_phase()
        before = b"[]\n"
        (self.ws / "profile.json").write_bytes(before)
        records = (self.ws / "ledger/im.jsonl").read_bytes()
        legacy_code, legacy_result = self.case.commit()
        self.assertEqual(legacy_code, 2, legacy_result)
        self.assertIn("profile", legacy_result["error"])
        self.assert_uncommitted(before)
        code, result = self.commit()
        self.assertEqual((code, result), (legacy_code, legacy_result))
        self.assert_uncommitted(before)
        self.assertEqual((self.ws / "ledger/im.jsonl").read_bytes(), records)

    def test_unknown_source_or_part_does_not_commit(self):
        self.plan()
        self.case.setup_phase()
        before = (self.ws / "profile.json").read_bytes()
        for kwargs in ({"message": "unknown-message"}, {"part": "unknown-part"}):
            with self.subTest(**kwargs):
                code, result = self.commit(**kwargs)
                self.assertEqual((code, result.get("code")), (2, "im_change_source_invalid"), result)
                self.assert_uncommitted(before)

    def test_changed_owner_is_not_authorized_by_the_saved_plan(self):
        self.plan()
        self.case.setup_phase()
        changed = copy.deepcopy(self.case.profile)
        changed["im"]["ownerId"] = "ou_other_synthetic"
        before = N.profile_bytes(changed)
        (self.ws / "profile.json").write_bytes(before)
        code, result = self.commit()
        self.assertEqual((code, result.get("code")), (2, "im_change_source_invalid"), result)
        self.assert_uncommitted(before)

    def test_only_matching_normal_setup_lock_can_commit(self):
        self.plan()
        before = (self.ws / "profile.json").read_bytes()
        for skill in ("followup", "apply", "setup"):
            with self.subTest(skill=skill):
                if skill != "followup":
                    self.assertEqual(self.case.f.ledger_run("lock", "acquire", "--skill", skill, "--run", skill)[0], 0)
                code, result = self.commit(run=skill if skill != "setup" else "wrong-run")
                self.assertEqual((code, result.get("code")), (2, "setup_lock_required"), result)
                self.assertEqual(self.case.f.ledger_run("lock", "release", "--run", skill)[0], 0)
                self.assert_uncommitted(before)
        self.assertEqual(self.case.f.ledger_run("lock", "acquire", "--skill", "setup",
                                              "--run", "recovery", "--recovery")[0], 0)
        code, result = self.commit(run="recovery")
        self.assertEqual((code, result.get("code")), (2, "setup_lock_required"), result)
        self.assert_uncommitted(before)

    def test_expired_or_releasing_setup_lock_cannot_commit(self):
        self.plan()
        self.case.setup_phase()
        before = (self.ws / "profile.json").read_bytes()
        path = self.ws / "ledger/lock.json"
        saved = path.read_bytes()
        for changes in ({"expiresAt": "2026-11-09T00:00:00+08:00"}, {"releasing": True}):
            with self.subTest(changes=changes):
                lock = json.loads(saved)
                lock.update(changes)
                path.write_text(json.dumps(lock), encoding="utf-8")
                code, result = self.commit()
                self.assertEqual((code, result.get("code")), (2, "setup_lock_required"), result)
                self.assert_uncommitted(before)

    def test_unsatisfied_dependency_cannot_be_bypassed_by_auto_staging(self):
        pending = {"partId": "clarify", "kind": "clarification", "state": "needs_clarification",
                   "quote": "其他看着办", "questionFields": ["hardRequirements"]}
        part = self.case.preference({"locations": ["上海"]})
        part["dependsOn"] = ["clarify"]
        self.plan([pending, part])
        self.case.setup_phase()
        before = (self.ws / "profile.json").read_bytes()
        code, result = self.commit()
        self.assertEqual((code, result.get("code")), (2, "im_change_baseline_conflict"), result)
        self.assert_uncommitted(before)

    def test_saved_source_digest_and_field_whitelist_are_rechecked(self):
        self.plan()
        self.case.setup_phase()
        path = self.ws / "ledger/im.jsonl"
        original = path.read_bytes()
        before = (self.ws / "profile.json").read_bytes()
        for corruption in ("source_digest", "forbidden_field"):
            with self.subTest(corruption=corruption):
                records = [json.loads(line) for line in original.splitlines()]
                plan = next(r for r in reversed(records) if r["type"] == "reply_plan")
                if corruption == "source_digest":
                    plan["sourceDigest"] = "0" * 64
                else:
                    part = plan["parts"][0]
                    part["changes"].append({"field": "authorization.apply.send", "before": False, "after": True})
                    part["afterHash"] = hashlib.sha256(N.preference_target(json.loads(before), part)).hexdigest()
                path.write_text("".join(N.canonical(r) + "\n" for r in records), encoding="utf-8")
                code, result = self.commit()
                expected = "im_change_source_invalid" if corruption == "source_digest" else "journal_corrupt"
                self.assertEqual((code, result.get("code")), (2, expected), result)
                self.assert_uncommitted(before)

    def test_restricted_options_and_missing_source_still_fail(self):
        self.plan()
        self.case.setup_phase()
        before = (self.ws / "profile.json").read_bytes()
        for extra in (("--recover",), ("--stoplist-ops", "forbidden"),
                      ("--abandon",), ("--confirm-mixed-config",)):
            with self.subTest(extra=extra):
                code, result = self.commit(extra=extra)
                self.assertEqual((code, result.get("code")), (2, "im_change_fields_forbidden"), result)
                self.assert_uncommitted(before)
        result = self.cli("--run", "setup")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--staging", result.stderr)
        self.assert_uncommitted(before)

    def test_auto_commit_uses_existing_crash_recovery(self):
        self.plan()
        self.case.setup_phase()
        before = (self.ws / "profile.json").read_bytes()
        self.assertEqual(self.commit(crash="after_file_1")[0], 70)
        self.assertTrue((self.ws / ".job-mate-txn.json").exists())
        self.assertNotEqual((self.ws / "profile.json").read_bytes(), before)
        code, result = self.case.recover()
        self.assertEqual(code, 0, result)
        self.assertTrue(result["recovered"])
        self.assertEqual((self.ws / "profile.json").read_bytes(), before)
        self.assertFalse((self.ws / ".job-mate-txn.json").exists())
        self.assertEqual(self.case.own_reply()["preferenceChanges"][0]["status"], "rolled_back")


if __name__ == "__main__":
    unittest.main()

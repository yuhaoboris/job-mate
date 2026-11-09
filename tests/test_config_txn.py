"""T5.5：真实CLI、临时配置、进程崩溃与恢复；不访问任何平台。"""
import copy
import json
import os
import hashlib
import queue
import threading
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import test_workspace

W = test_workspace.workspace
ROOT = Path(__file__).resolve().parents[1]


class ConfigTransactionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ws = self.root / "workspace"
        self.stage = self.root / "staging"
        self.stage.mkdir()
        self.env = dict(os.environ, JOB_MATE_TESTING="1", JOB_MATE_NOW="2026-11-04T12:00:00+08:00", PYTHONDONTWRITEBYTECODE="1")
        self.ok("workspace", "init", str(self.ws), "--account", "boss:synthetic:合成账号", bare=True)
        self.ok("ledger", "lock", "acquire", "--skill", "setup", "--run", "setup-txn")

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, module, *args, bare=False, crash=None, version=None):
        command = [sys.executable, "-B", "-I", str(ROOT / f"scripts/{module}.py")]
        if version:
            program = ("import sys,importlib;sys.path.insert(0,sys.argv[1]);import ledger;ledger.SUITE_VERSION=sys.argv[3];"
                       "module=importlib.import_module(sys.argv[2]);raise SystemExit(module.main(sys.argv[4:]))")
            command = [sys.executable, "-B", "-I", "-c", program, str(ROOT / "scripts"), module, version]
        if not bare:
            command += ["--workspace", str(self.ws)]
        env = dict(self.env)
        if crash:
            env["JOB_MATE_TEST_CRASH_AT"] = crash
        p = subprocess.run([*command, *args], capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
        self.assertFalse(p.stderr, p.stderr)
        return p.returncode, json.loads(p.stdout) if p.stdout else {}

    def ok(self, module, *args, **kwargs):
        rc, out = self.cli(module, *args, **kwargs)
        self.assertEqual(rc, 0, out)
        return out

    def stage_file(self, name, data):
        path = self.stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))

    def commit(self, **kwargs):
        return self.cli("workspace", "commit", "--staging", str(self.stage), "--run", "setup-txn", **kwargs)

    def recover(self, **kwargs):
        return self.cli("workspace", "commit", "--recover", "--run", "setup-txn", **kwargs)

    def config(self):
        return {f: (self.ws / f).read_bytes() if (self.ws / f).exists() else None for f in W.CONFIG_FILES}

    def journal(self):
        return json.loads((self.ws / ".job-mate-txn.json").read_text(encoding="utf-8"))

    def test_wait_window_commit_and_old_backup_rejection(self):
        for wait in (0, 60):
            profile = copy.deepcopy(test_workspace.PROFILE)
            profile["im"] = {"channel": "lark", "ownerId": "synthetic", "larkProfile": "default", "waitMinutes": wait}
            self.stage_file("profile.json", json.dumps(profile))
            self.assertEqual(self.commit()[0], 0)
        for wait in (61, 240):
            profile["im"]["waitMinutes"] = wait
            self.stage_file("profile.json", json.dumps(profile))
            before = self.config()
            backups = sorted(p.name for p in (self.ws / "backups").iterdir())
            self.assertEqual(self.commit()[0], 2)
            self.assertEqual(self.config(), before)
            self.assertEqual(sorted(p.name for p in (self.ws / "backups").iterdir()), backups)
            self.assertFalse((self.ws / ".job-mate-txn.json").exists())
        valid = (self.ws / "profile.json").read_bytes()
        (self.ws / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
        old = self.ok("workspace", "backup")["backup"]
        (self.ws / "profile.json").write_bytes(valid)
        self.assertEqual(self.cli("workspace", "restore", old, "--run", "setup-txn")[0], 2)
        self.assertEqual((self.ws / "profile.json").read_bytes(), valid)

    def test_crash_points_rollback_new_files_and_are_idempotent(self):
        for point in ("after_backup", "after_journal", "after_file_1"):
            with self.subTest(point=point):
                before = self.config()
                self.stage_file("rules.md", "新文件\n")
                self.assertEqual(self.commit(crash=point)[0], 70)
                if point == "after_backup":
                    self.assertEqual(self.config(), before)
                    self.assertFalse((self.ws / ".job-mate-txn.json").exists())
                else:
                    if point == "after_file_1":
                        self.assertTrue((self.ws / "rules.md").exists())
                    self.assertEqual(self.recover(crash="during_rollback")[0], 70)
                    rc, out = self.recover()
                    self.assertEqual(rc, 0, out)
                    self.assertTrue(all(item["ok"] for item in out["verified"]))
                    self.assertEqual(self.config(), before)
                    self.assertFalse((self.ws / ".job-mate-txn.json").exists())
                    self.assertFalse(self.recover()[1]["recovered"])
        self.assertEqual(self.commit()[0], 0)

    def test_pending_blocks_new_commit_restore_and_both_runtime_skills(self):
        backup = self.ok("workspace", "backup")["backup"]
        self.stage_file("rules.md", "新规则")
        self.assertEqual(self.commit(crash="after_file_1")[0], 70)
        evidence = {str(p.relative_to(self.ws)): p.read_bytes() for p in self.ws.rglob("*") if p.is_file()}
        txn_id = self.journal()["txnId"]
        for command in (("commit", "--staging", str(self.stage), "--run", "setup-txn"), ("restore", backup)):
            rc, out = self.cli("workspace", *command)
            self.assertEqual((rc, out["code"], out["txnId"]), (2, "txn_pending", txn_id), out)
            self.assertEqual({str(p.relative_to(self.ws)): p.read_bytes() for p in self.ws.rglob("*") if p.is_file()}, evidence)
        self.ok("ledger", "lock", "release", "--run", "setup-txn")
        for skill in ("apply", "followup"):
            rc, out = self.cli("ledger", "lock", "acquire", "--skill", skill, "--run", "same-run")
            self.assertEqual((rc, out["code"]), (1, "config_txn_incomplete"))
        self.ok("ledger", "lock", "acquire", "--skill", "setup", "--run", "setup-txn")
        self.assertEqual(self.recover()[0], 0)
        self.ok("workspace", "restore", backup, "--run", "setup-txn")

    def test_backup_tampering_never_partially_restores_and_abandon_saves_state(self):
        self.stage_file("rules.md", "新规则")
        self.assertEqual(self.commit(crash="after_file_1")[0], 70)
        journal = self.journal()
        backup = self.ws / journal["backupDir"]
        (backup / "job-mate.json").write_bytes(b"corrupt")
        current, log = self.config(), (self.ws / ".job-mate-txn.json").read_bytes()
        rc, out = self.recover()
        self.assertEqual((rc, out["code"]), (2, "backup_corrupt"))
        self.assertEqual(self.config(), current)
        self.assertEqual((self.ws / ".job-mate-txn.json").read_bytes(), log)
        self.assertEqual(self.cli("workspace", "commit", "--recover", "--run", "setup-txn", "--abandon")[0], 2)
        out = self.ok("workspace", "commit", "--recover", "--run", "setup-txn", "--abandon", "--confirm-mixed-config")
        archive = self.ws / "backups" / out["savedAs"]
        self.assertEqual((archive / "JOURNAL.json").read_bytes(), log)
        self.assertEqual((archive / "rules.md").read_bytes(), current["rules.md"])
        self.assertEqual(len(json.loads((archive / "STATE.json").read_text())["files"]), len(W.CONFIG_FILES))
        self.assertIn("当前配置可能新旧混合", out["warning"])
        self.assertEqual(self.config(), current)
        self.assertFalse((self.ws / ".job-mate-txn.json").exists())

    def test_invalid_staging_has_no_config_or_journal_effects(self):
        bad_profiles = []
        for key, value in (("futureField", True), ("authorization", {"apply": {"send": "true"}}),
                           ("style", {"emoji": "false"}), ("schedule", {"activeHours": {"weekdays": [True], "start": "00:00", "end": "23:59"}}),
                           ("limits", {"unknownLimit": 1}), ("im", {"channel": "none", "futureField": 1})):
            p = copy.deepcopy(test_workspace.PROFILE)
            p[key] = value
            bad_profiles.append(json.dumps(p))
        for data in ("not-json", "[]", "{}", *bad_profiles):
            with self.subTest(data=data):
                self.stage_file("profile.json", data)
                self.stage_file("rules.md", "不能提前安装")
                before = self.config()
                self.assertEqual(self.commit()[0], 2)
                self.assertEqual(self.config(), before)
                self.assertFalse((self.ws / ".job-mate-txn.json").exists())
        (self.stage / "profile.json").unlink()
        (self.ws / "profile.json").write_bytes(b"\xff")
        self.assertEqual(self.commit()[0], 0)  # 不把当前坏profile当作暂存校验输入。

    def seal(self, root):
        files = [{"path": name, "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()}
                 for name in W.CONFIG_FILES if (root / name).is_file()]
        (root / "MANIFEST.json").write_text(json.dumps({"schemaVersion": 1, "files": files}), encoding="utf-8")

    def test_corrupt_journal_and_path_sets_never_delete_guessed_targets(self):
        self.stage_file("rules.md", "新规则")
        self.assertEqual(self.commit(crash="after_file_1")[0], 70)
        path = self.ws / ".job-mate-txn.json"
        valid = path.read_bytes()
        initial = self.config()
        outside = self.root / "outside.txt"
        outside.write_bytes(b"outside sentinel")
        for bad_path in ("../outside.txt", str(outside), "C:/outside.txt", "C:\\outside.txt", "resumes/../rules.md", "resumes//index.yaml", "./rules.md"):
            obj = json.loads(valid)
            obj["files"][0]["path"] = bad_path
            path.write_text(json.dumps(obj), encoding="utf-8")
            log = path.read_bytes()
            self.assertEqual(self.recover()[0], 2)
            self.assertEqual(self.config(), initial)
            self.assertEqual(path.read_bytes(), log)
            self.assertEqual(outside.read_bytes(), b"outside sentinel")
        for bad in (b"not-json", b"[]", b"{}", valid[:-10]):
            path.write_bytes(bad)
            self.assertEqual(self.recover()[0], 2)
            self.assertEqual(path.read_bytes(), bad)
            self.assertEqual(self.config(), initial)
        for mutate in (lambda obj: obj["files"].append(copy.deepcopy(obj["files"][0])),
                       lambda obj: obj["files"].pop(),
                       lambda obj: obj.update(backupDir="../outside"),
                       lambda obj: obj.update(futureField=True),
                       lambda obj: obj.update(startedAt=None)):
            obj = json.loads(valid)
            mutate(obj)
            path.write_text(json.dumps(obj), encoding="utf-8")
            self.assertEqual(self.recover()[0], 2)
            self.assertEqual(self.config(), initial)
        path.write_bytes(valid)
        self.assertEqual(self.recover()[0], 0)

    def test_manifest_set_and_digest_validation_happens_before_any_target_change(self):
        self.stage_file("rules.md", "新规则")
        self.assertEqual(self.commit(crash="after_file_1")[0], 70)
        log = self.ws / ".job-mate-txn.json"
        valid_log = log.read_bytes()
        root = self.ws / self.journal()["backupDir"]
        manifest = root / "MANIFEST.json"
        valid_manifest = manifest.read_bytes()
        initial = self.config()
        for alteration in ("missing", "bad_sha", "wrong_set", "duplicate", "future"):
            with self.subTest(alteration=alteration):
                obj = json.loads(valid_manifest)
                if alteration == "missing":
                    manifest.unlink()
                elif alteration == "bad_sha":
                    obj["files"][0]["sha256"] = "0" * 64
                elif alteration == "wrong_set":
                    obj["files"] = [{"path": "rules.md", "sha256": hashlib.sha256(b"new").hexdigest()}]
                elif alteration == "duplicate":
                    obj["files"].append(copy.deepcopy(obj["files"][0]))
                else:
                    obj["future"] = True
                if alteration != "missing":
                    raw = json.dumps(obj).encode("utf-8")
                    manifest.write_bytes(raw)
                    journal = json.loads(valid_log)
                    journal["manifestSha"] = hashlib.sha256(raw).hexdigest()
                    log.write_text(json.dumps(journal), encoding="utf-8")
                rc, out = self.recover()
                self.assertEqual((rc, out["code"]), (2, "backup_corrupt"), out)
                self.assertEqual(self.config(), initial)
                manifest.write_bytes(valid_manifest)
                log.write_bytes(valid_log)
        self.assertEqual(self.recover()[0], 0)

    def test_missing_or_corrupt_marker_cannot_authorize_recovery_deletion(self):
        self.stage_file("rules.md", "新规则")
        self.assertEqual(self.commit(crash="after_file_1")[0], 70)
        marker = self.ws / "job-mate.json"
        valid = marker.read_bytes()
        for data in (None, b"broken", b"[]"):
            if data is None:
                marker.unlink()
            else:
                marker.write_bytes(data)
            before = self.config()
            log = (self.ws / ".job-mate-txn.json").read_bytes()
            self.assertEqual(self.recover()[0], 2)
            self.assertEqual(self.config(), before)
            self.assertEqual((self.ws / ".job-mate-txn.json").read_bytes(), log)
            marker.write_bytes(valid)
        self.assertEqual(self.recover()[0], 0)

    def test_restore_validates_manifest_and_unknown_fields_without_touching_current_config(self):
        backup = self.ok("workspace", "backup")["backup"]
        root = self.ws / "backups" / backup
        initial = self.config()
        for invalid in ("missing_manifest", "bad_file", "future_profile", "future_marker", "future_yaml"):
            with self.subTest(invalid=invalid):
                for path in root.iterdir():
                    if path.is_file():
                        path.unlink()
                (root / "job-mate.json").write_bytes(initial["job-mate.json"])
                if invalid == "future_profile":
                    p = copy.deepcopy(test_workspace.PROFILE)
                    p["future"] = True
                    (root / "profile.json").write_text(json.dumps(p), encoding="utf-8")
                elif invalid == "future_marker":
                    p = json.loads(initial["job-mate.json"])
                    p["future"] = True
                    (root / "job-mate.json").write_text(json.dumps(p), encoding="utf-8")
                elif invalid == "future_yaml":
                    (root / "qa.yaml").write_text("- id: test\n  ask: [问题]\n  answer: 合成\n  basis: 合成\n  future: 未知\n", encoding="utf-8")
                self.seal(root)
                if invalid == "missing_manifest":
                    (root / "MANIFEST.json").unlink()
                elif invalid == "bad_file":
                    (root / "job-mate.json").write_bytes(b"changed")
                rc, out = self.cli("workspace", "restore", backup, "--run", "setup-txn")
                self.assertEqual(rc, 2, out)
                self.assertEqual(self.config(), initial)
                self.assertFalse((self.ws / ".job-mate-txn.json").exists())

    def test_broken_profile_and_stoplist_controlled_restore_preserves_damaged_bytes(self):
        self.stage_file("profile.json", json.dumps(test_workspace.PROFILE))
        self.stage_file("stoplist.yaml", "# 空停止名单\n")
        self.stage_file("rules.md", "旧规则")
        self.assertEqual(self.commit()[0], 0)
        backup = self.ok("workspace", "backup")["backup"]
        (self.ws / "profile.json").write_bytes(b"\xff")
        (self.ws / "stoplist.yaml").write_bytes(b"\xff\xfe damaged")
        self.ok("ledger", "lock", "release", "--run", "setup-txn")
        self.ok("ledger", "lock", "acquire", "--skill", "setup", "--run", "setup-txn")
        before = self.config()
        rc, out = self.cli("workspace", "restore", backup, "--run", "setup-txn")
        self.assertEqual((rc, out["code"]), (1, "stoplist_unreadable"), out)
        self.assertEqual(self.config(), before)
        out = self.ok("workspace", "restore", backup, "--run", "setup-txn", "--stoplist-from-backup")
        saved = self.ws / "backups" / out["corruptStoplistSavedAs"] / "stoplist.yaml"
        self.assertEqual(saved.read_bytes(), before["stoplist.yaml"])
        self.assertEqual(json.loads((self.ws / "profile.json").read_text(encoding="utf-8")), test_workspace.PROFILE)
        self.assertEqual((self.ws / "stoplist.yaml").read_bytes(), "# 空停止名单\n".encode("utf-8"))

    def test_stoplist_operations_are_atomic_and_do_not_silently_conflict(self):
        ops = self.root / "operations.json"
        add = {"op": "add", "name": "合成公司", "aliases": ["合成别名"], "reason": "本人确认", "source": "user"}
        ops.write_text(json.dumps([add]), encoding="utf-8")
        out = self.ok("workspace", "commit", "--staging", str(self.stage), "--run", "setup-txn", "--stoplist-ops", str(ops))
        self.assertTrue(out["committed"])
        self.assertIn("合成公司", (self.ws / "stoplist.yaml").read_text(encoding="utf-8"))
        self.stage_file("stoplist.yaml", "[]")
        before = self.config()
        self.assertEqual(self.cli("workspace", "commit", "--staging", str(self.stage), "--run", "setup-txn", "--stoplist-ops", str(ops))[0], 2)
        self.assertEqual(self.config(), before)
        (self.stage / "stoplist.yaml").unlink()
        ops.write_text(json.dumps([{"op": "remove", "name": "合成公司"}, dict(add, name="另一公司")]), encoding="utf-8")
        self.ok("workspace", "commit", "--staging", str(self.stage), "--run", "setup-txn", "--stoplist-ops", str(ops))
        names = [e["name"] for e in W.ledger.parse_stoplist((self.ws / "stoplist.yaml").read_text(encoding="utf-8"))]
        self.assertEqual(names, ["另一公司"])
        for bad in ([dict(add, future=True)], [{"op": "remove", "name": "合成别名"}], [{"op": "execute", "name": "cmd"}],
                    [dict(add, name="不能提前落盘"), {"op": "remove", "name": "未登记名称"}]):
            ops.write_text(json.dumps(bad), encoding="utf-8")
            before = self.config()
            self.assertEqual(self.cli("workspace", "commit", "--staging", str(self.stage), "--run", "setup-txn", "--stoplist-ops", str(ops))[0], 2)
            self.assertEqual(self.config(), before)

    def test_recovery_minor_version_chain_and_automatic_restore_never_downgrade(self):
        backup = self.ok("workspace", "backup")["backup"]
        self.stage_file("rules.md", "新规则")
        marker = self.ws / "job-mate.json"
        meta = json.loads(marker.read_bytes())
        meta["suiteVersion"] = "1.1.0"
        marker.write_text(json.dumps(meta), encoding="utf-8")
        self.assertEqual(self.commit(crash="after_file_1", version="1.1.0")[0], 70)
        self.ok("ledger", "lock", "release", "--run", "setup-txn")
        self.ok("ledger", "lock", "acquire", "--skill", "setup", "--recovery", "--run", "setup-txn")
        self.assertEqual(self.commit()[1]["code"], "recovery_lock_only")
        self.assertEqual(self.recover()[0], 0)
        self.ok("workspace", "restore", backup, "--run", "setup-txn")
        self.assertEqual(json.loads(marker.read_bytes())["suiteVersion"], "1.1.0")
        self.ok("ledger", "lock", "release", "--run", "setup-txn")
        self.ok("workspace", "restore", backup)
        self.assertEqual(json.loads(marker.read_bytes())["suiteVersion"], "1.1.0")

    def test_commit_upgrade_is_not_written_before_backup_or_on_rollback(self):
        marker = self.ws / "job-mate.json"
        meta = json.loads(marker.read_bytes())
        meta["suiteVersion"] = "1.0.0"
        original = json.dumps(meta, separators=(",", ":")).encode("utf-8")
        marker.write_bytes(original)
        self.stage_file("rules.md", "新规则")
        self.assertEqual(self.commit(crash="after_backup", version="1.1.0")[0], 70)
        self.assertEqual(marker.read_bytes(), original)
        self.assertEqual(self.commit(crash="after_file_1", version="1.1.0")[0], 70)
        self.assertEqual(self.recover()[0], 0)
        self.assertEqual(marker.read_bytes(), original)
        self.assertEqual(self.commit(version="1.1.0")[0], 0)
        self.assertEqual(json.loads(marker.read_bytes())["suiteVersion"], "1.1.0")

    def test_actual_write_failure_rolls_back_exact_original_bytes_and_version(self):
        original = self.config()
        self.stage_file("profile.json", json.dumps(test_workspace.PROFILE))
        self.stage_file("rules.md", "新规则")
        program = '''import sys
sys.path.insert(0,sys.argv[1])
import workspace as W,fsutil,ledger
from pathlib import Path
ledger.SUITE_VERSION="1.1.0"
ws=Path(sys.argv[2]).resolve(); original=fsutil.replace_atomic; failed=False
def fail_once(src,dst):
    global failed
    if Path(dst)==ws/"rules.md" and not failed:
        failed=True
        raise OSError("合成目标写入失败")
    return original(src,dst)
fsutil.replace_atomic=fail_once
raise SystemExit(W.main(["--workspace",str(ws),"commit","--staging",sys.argv[3],"--run","setup-txn"]))
'''
        p = subprocess.run([sys.executable, "-B", "-I", "-c", program, str(ROOT / "scripts"), str(self.ws), str(self.stage)],
                           capture_output=True, text=True, encoding="utf-8", env=self.env, timeout=30)
        self.assertFalse(p.stderr, p.stderr)
        self.assertEqual((p.returncode, json.loads(p.stdout)["code"]), (2, "commit_rolled_back"), p.stdout)
        self.assertEqual(self.config(), original)
        self.assertFalse((self.ws / ".job-mate-txn.json").exists())

    def test_last_backup_is_verified_before_deleting_first_new_file(self):
        (self.ws / "candidate.md").write_bytes("旧事实".encode("utf-8"))
        self.stage_file("rules.md", "新规则")
        self.assertEqual(self.commit(crash="after_file_1")[0], 70)
        root = self.ws / self.journal()["backupDir"]
        (root / "candidate.md").write_bytes(b"tampered last file")
        initial = self.config()
        self.assertEqual(self.recover()[1]["code"], "backup_corrupt")
        self.assertEqual(self.config(), initial)
        self.assertTrue((self.ws / "rules.md").exists())

    def test_setup_lock_validation_and_same_run_runtime_reacquisition(self):
        self.stage_file("rules.md", "新规则")
        self.ok("ledger", "lock", "release", "--run", "setup-txn")
        before = self.config()
        self.assertEqual(self.commit()[1]["code"], "setup_lock_required")
        self.assertEqual(self.config(), before)
        self.ok("ledger", "lock", "acquire", "--skill", "setup", "--run", "setup-txn")
        self.assertEqual(self.commit(crash="after_journal")[0], 70)
        log = (self.ws / ".job-mate-txn.json").read_bytes()
        self.assertEqual(self.recover()[0], 0)
        self.ok("ledger", "lock", "release", "--run", "setup-txn")
        for skill in ("apply", "followup"):
            self.ok("ledger", "lock", "acquire", "--skill", skill, "--run", "runtime")
            # 历史故障状态：已有运行锁上出现未完成事务，续取也不能走reused捷径。
            (self.ws / ".job-mate-txn.json").write_bytes(log)
            rc, out = self.cli("ledger", "lock", "acquire", "--skill", skill, "--run", "runtime")
            self.assertEqual((rc, out["code"]), (1, "config_txn_incomplete"))
            (self.ws / ".job-mate-txn.json").unlink()
            self.ok("ledger", "lock", "release", "--run", "runtime")

    def test_symlink_and_self_overwrite_paths_are_rejected(self):
        target = self.root / "outside"
        target.mkdir()
        outside = target / "sentinel"
        outside.write_bytes(b"outside")
        link = self.stage / "rules.md"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as error:
            self.skipTest("需要符号链接权限：" + str(error))
        before = self.config()
        self.assertEqual(self.commit()[0], 2)
        self.assertEqual(self.config(), before)
        self.assertEqual(outside.read_bytes(), b"outside")
        link.unlink()
        self.stage_file("rules.md", "新规则")
        for stage in (self.ws, self.root, self.ws / "backups"):
            self.assertEqual(self.cli("workspace", "commit", "--staging", str(stage), "--run", "setup-txn")[0], 2)
            self.assertEqual(self.config(), before)
        (self.ws / "rules.md").symlink_to(outside)
        self.assertEqual(self.commit()[1]["code"], "unsafe_path")
        self.assertEqual(outside.read_bytes(), b"outside")
        (self.ws / "rules.md").unlink()
        backup = self.ok("workspace", "backup")["backup"]
        root = self.ws / "backups" / backup
        (root / "job-mate.json").unlink()
        (root / "job-mate.json").symlink_to(outside)
        self.assertEqual(self.cli("workspace", "restore", backup, "--run", "setup-txn")[0], 2)
        self.assertEqual(self.config(), before)
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_recover_preserves_a_newer_pre_recovery_marker_version(self):
        self.stage_file("rules.md", "新规则")
        self.assertEqual(self.commit(crash="after_file_1")[0], 70)
        marker = self.ws / "job-mate.json"
        meta = json.loads(marker.read_bytes())
        meta["suiteVersion"] = "1.1.0"
        marker.write_text(json.dumps(meta), encoding="utf-8")
        self.assertEqual(self.recover()[0], 0)
        self.assertEqual(json.loads(marker.read_bytes())["suiteVersion"], "1.1.0")
        self.assertFalse((self.ws / "rules.md").exists())

    def test_restore_crash_reuses_transaction_rollback(self):
        (self.ws / "rules.md").write_bytes(b"backup version")
        backup = self.ok("workspace", "backup")["backup"]
        (self.ws / "rules.md").unlink()
        before = self.config()
        self.assertEqual(self.cli("workspace", "restore", backup, "--run", "setup-txn", crash="after_file_1")[0], 70)
        self.assertEqual((self.ws / "rules.md").read_bytes(), b"backup version")
        self.assertEqual(self.recover()[0], 0)
        self.assertEqual(self.config(), before)

    def test_two_real_commits_serialize_and_freeze_validated_staging(self):
        self.stage_file("profile.json", json.dumps(test_workspace.PROFILE))
        self.stage_file("rules.md", "第一提交")
        second_stage = self.root / "second-stage"
        second_stage.mkdir()
        (second_stage / "rules.md").write_text("第二提交", encoding="utf-8")
        program = '''import sys,contextlib
sys.path.insert(0,sys.argv[1])
import workspace as W,fsutil,ledger
role=sys.argv[4]
original_lock=fsutil.locked
@contextlib.contextmanager
def traced(path):
    if role=="second" and path.name==".mutation.mutex":
        print("WAITING",file=sys.stderr,flush=True)
    with original_lock(path):
        yield
fsutil.locked=traced
ledger.file_mutex=traced
original=W.do_backup
def pause(ws,label=None):
    result=original(ws,label)
    print("READY",file=sys.stderr,flush=True)
    sys.stdin.readline()
    return result
if role=="first": W.do_backup=pause
raise SystemExit(W.main(["--workspace",sys.argv[2],"commit","--staging",sys.argv[3],"--run","setup-txn"]))
'''
        children = []
        readers = []
        try:
            def start(stage, role):
                p = subprocess.Popen([sys.executable, "-B", "-I", "-c", program, str(ROOT / "scripts"), str(self.ws), str(stage), role],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8", env=self.env)
                children.append(p)
                messages = queue.Queue()
                def read():
                    for line in p.stderr:
                        messages.put(line.strip())
                thread = threading.Thread(target=read, daemon=True)
                thread.start()
                readers.append(thread)
                return p, messages
            first, a = start(self.stage, "first")
            self.assertEqual(a.get(timeout=10), "READY")
            self.stage_file("profile.json", "not-json after validation")
            second, b = start(second_stage, "second")
            self.assertEqual(b.get(timeout=10), "WAITING")
            with self.assertRaises(subprocess.TimeoutExpired):
                second.wait(timeout=0.2)
            first.stdin.write("continue\n")
            first.stdin.flush()
            for p in children:
                self.assertEqual(p.wait(timeout=15), 0, p.stdout.read() if p.poll() is not None and p.returncode else "")
            self.assertEqual(json.loads((self.ws / "profile.json").read_bytes()), test_workspace.PROFILE)
            self.assertEqual((self.ws / "rules.md").read_text(encoding="utf-8"), "第二提交")
        finally:
            for p in children:
                if p.poll() is None:
                    p.kill()
                p.wait(timeout=5)
                for stream in (p.stdin, p.stdout, p.stderr):
                    stream.close()
            for thread in readers:
                thread.join(timeout=5)

    def test_missing_journal_is_not_permission_to_guess_rollback_paths(self):
        self.stage_file("rules.md", "新规则")
        self.assertEqual(self.commit(crash="after_file_1")[0], 70)
        log = self.ws / ".job-mate-txn.json"
        raw = log.read_bytes()
        log.unlink()
        before = self.config()
        self.assertFalse(self.recover()[1]["recovered"])
        self.assertEqual(self.config(), before)
        log.write_bytes(raw)
        self.assertEqual(self.recover()[0], 0)

    def test_all_configuration_schemas_and_unicode_bytes_are_supported(self):
        self.stage_file("job-mate.json", (self.ws / "job-mate.json").read_bytes())
        self.stage_file("profile.json", json.dumps(test_workspace.PROFILE, ensure_ascii=False))
        self.stage_file("stoplist.yaml", "# 空停止名单\r\n")
        self.stage_file("rules.md", "  本人确认原文\r\n全角Ａ与组合e\u0301不规范化\n")
        self.stage_file("candidate.md", "# 合成个人事实\n" + "五年合成项目经验，不是真实求职数据。" * 4)
        self.stage_file("qa.yaml", "- id: sample\n  ask: [工作几年]\n  answer: 合成回答\n  basis: candidate.md\n")
        self.stage_file("resumes/index.yaml", 'default: sample\nversions:\n  - id: sample\n    file: sample.pdf\n    use: 合成\n    uploaded: {boss: "合成,附件.pdf"}\n')
        rc, out = self.commit()
        self.assertEqual(rc, 0, out)
        for name in W.CONFIG_FILES:
            if name != "job-mate.json":
                self.assertEqual((self.ws / name).read_bytes(), (self.stage / name).read_bytes(), name)

    def test_crash_override_requires_testing_flag(self):
        self.stage_file("rules.md", "新规则")
        self.env["JOB_MATE_TESTING"] = "0"
        self.ok("ledger", "lock", "release", "--run", "setup-txn")
        self.ok("ledger", "lock", "acquire", "--skill", "setup", "--run", "setup-txn")
        self.assertEqual(self.commit(crash="after_file_1")[0], 0)
        self.assertFalse((self.ws / ".job-mate-txn.json").exists())

    def test_config_mutex_wait_rechecks_version_before_real_mutation(self):
        self.stage_file("rules.md", "新规则")
        program = '''import sys,contextlib
sys.path.insert(0,sys.argv[1])
import workspace as W,fsutil
from pathlib import Path
ws=Path(sys.argv[2]); role=sys.argv[4]
if role=="holder":
    with fsutil.locked(ws/"ledger/config.lock"):
        print("READY",flush=True)
        sys.stdin.readline()
else:
    original=fsutil.locked
    @contextlib.contextmanager
    def traced(path):
        if path.name=="config.lock": print("WAITING",flush=True)
        with original(path): yield
    fsutil.locked=traced
    raise SystemExit(W.main(["--workspace",str(ws),"commit","--staging",sys.argv[3],"--run","setup-txn"]))
'''
        children, readers = [], []
        try:
            def start(role):
                p = subprocess.Popen([sys.executable, "-B", "-I", "-c", program, str(ROOT / "scripts"), str(self.ws), str(self.stage), role],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=self.env)
                children.append(p)
                result = queue.Queue()
                thread = threading.Thread(target=lambda: result.put(p.stdout.readline().strip()), daemon=True)
                thread.start()
                readers.append(thread)
                return p, result
            holder, a = start("holder")
            self.assertEqual(a.get(timeout=10), "READY")
            writer, b = start("writer")
            self.assertEqual(b.get(timeout=10), "WAITING")
            meta = json.loads((self.ws / "job-mate.json").read_bytes())
            meta["suiteVersion"] = "1.1.0"
            (self.ws / "job-mate.json").write_text(json.dumps(meta), encoding="utf-8")
            before = self.config()
            holder.stdin.write("continue\n")
            holder.stdin.flush()
            self.assertEqual(holder.wait(timeout=10), 0)
            self.assertEqual(writer.wait(timeout=10), 2)
            self.assertEqual(json.loads(writer.stdout.read())["code"], "suite_too_old")
            self.assertEqual(self.config(), before)
            self.assertFalse((self.ws / ".job-mate-txn.json").exists())
        finally:
            for p in children:
                if p.poll() is None:
                    p.kill()
                p.wait(timeout=5)
                for stream in (p.stdin, p.stdout, p.stderr):
                    stream.close()
            for thread in readers:
                thread.join(timeout=5)

    def test_commit_validates_and_installs_staged_files_together(self):
        self.stage_file("profile.json", json.dumps(test_workspace.PROFILE))
        self.stage_file("rules.md", "# 合成规则\n")
        rc, out = self.commit()
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.ws / "rules.md").read_text(encoding="utf-8"), "# 合成规则\n")
        self.assertEqual(json.loads((self.ws / "profile.json").read_text(encoding="utf-8")), test_workspace.PROFILE)
        self.assertFalse((self.ws / ".job-mate-txn.json").exists())
        backup = self.ws / "backups" / out["backup"]
        self.assertTrue((backup / "MANIFEST.json").is_file())

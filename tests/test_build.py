import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build  # noqa: E402


def run_build(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = build.main(list(argv))
    return code, json.loads(out.getvalue())


# 已完整改写的文档进入体量预算；其余在对应任务重写时加入。
TEXT_BUDGETS = {"shared/notify.md": 16000, "shared/ledger.md": 16000,
                "shared/workspace.md": 16000, "skills/setup/SKILL.md": 20000}


class BuildTest(unittest.TestCase):
    def test_rewritten_document_size_budgets(self):
        for name, maximum in TEXT_BUDGETS.items():
            with self.subTest(path=name):
                self.assertLessEqual((ROOT / name).stat().st_size, maximum)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "dist"

    def tearDown(self):
        self.tmp.cleanup()

    def test_builds_self_contained_skills(self):
        code, res = run_build("--out", str(self.out))
        self.assertEqual(code, 0, res)
        names = sorted(Path(p).name for p in res["built"])
        self.assertEqual(names, ["job-mate-apply", "job-mate-followup", "job-mate-setup"])
        for name in names:
            d = self.out / name
            skill = (d / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn(f"name: {name}\n", skill)
            self.assertNotIn("上两级目录", skill)
            self.assertNotIn(".../scripts/", skill)
            for shared in ("ledger.md", "notify.md"):
                self.assertNotIn("上两级目录", (d / "shared" / shared).read_text(encoding="utf-8"))
            self.assertTrue((d / "scripts" / "ledger.py").exists())
            self.assertTrue((d / "scripts" / "workspace.py").exists())
            self.assertTrue((d / "scripts" / "notify.py").exists())
            self.assertFalse((d / "scripts" / "build.py").exists())
            self.assertTrue((d / "shared" / "ledger.md").exists())
            self.assertEqual(json.loads((d / "BUILD.json").read_text())["skill"], name)
            for md in d.rglob("*.md"):
                text = md.read_text(encoding="utf-8")
                self.assertNotIn("](../../", text, md)
                for link in re.findall(r"\]\(([^)\s#]+)", text):
                    if not re.match(r"^[a-z]+:", link):
                        self.assertTrue((md.parent / link).resolve().exists(), f"{md}: {link}")

    def test_description_mentions_prefixed_skill_names(self):
        run_build("--out", str(self.out))
        apply_md = (self.out / "job-mate-apply" / "SKILL.md").read_text(encoding="utf-8")
        desc = next(line for line in apply_md.splitlines() if line.startswith("description:"))
        self.assertIn("用 job-mate-followup", desc)
        self.assertIn("用 job-mate-setup", desc)
        # 正文中的命令参数不能被改写
        self.assertIn("--skill apply", apply_md)

    def test_cross_skill_reference_copied(self):
        run_build("--out", str(self.out))
        d = self.out / "job-mate-followup"
        self.assertTrue((d / "ref" / "apply" / "platforms" / "boss.md").exists())

    def test_built_scripts_run(self):
        run_build("--out", str(self.out))
        ws = Path(self.tmp.name) / "ws"
        script = self.out / "job-mate-setup" / "scripts" / "workspace.py"
        p = subprocess.run([sys.executable, "-I", str(script), "init", str(ws),
                            "--boss-account", "zp", "--boss-name", "演示"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        ledger = self.out / "job-mate-apply" / "scripts" / "ledger.py"
        p = subprocess.run([sys.executable, "-I", str(ledger), "--workspace", str(ws), "validate"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_quiet_close_sends_no_done_and_no_summary(self):
        """IM 实测 P8.5/P8.6 回归：只做收尾的轮次不得发 done / summary。

        真实 pi 在「结论：无需动作」的 ready 收尾和「补发已 done 的结果通知」时
        多发了一条 done 和一条 summary，方案 2.6 的 F1/F2 因此不成立。
        这里锁住已修正的措辞，避免改文档或改构建时静默丢掉这条规则。
        """
        run_build("--out", str(self.out))
        d = self.out / "job-mate-followup"
        skill = (d / "SKILL.md").read_text(encoding="utf-8")
        notify_md = (d / "shared" / "notify.md").read_text(encoding="utf-8")
        # 结论「无需动作」的 ready 收尾：不发 done、不发 summary
        self.assertIn("**这种情况不发 `done`**", skill)
        self.assertIn("也**不发 `summary`**", skill)
        # 补发已 done 的结果通知：只补 done，不进入 summary
        self.assertIn("**不发 `summary`**（第 9 节）", skill)
        # 第 9 节 summary 的前提不再是「发过 IM 消息」，而是「实际处理过会话或执行过决定」
        self.assertIn("**只做第 2.3 节收尾的轮次不发 `summary`**", skill)
        self.assertIn("本轮**实际处理过会话或执行过已决定事项**（含本地动作）时", skill)
        # shared/notify.md 与技能说的一致
        self.assertIn("**不发 `done`、不发 `summary`**", notify_md)
        self.assertIn("已 done 只补通知与消费，不重发 HR、不发 summary", notify_md)

    def test_install_replaces_only_managed_dirs(self):
        target = Path(self.tmp.name) / "skills"
        (target / "other-skill").mkdir(parents=True)
        (target / "other-skill" / "SKILL.md").write_text("keep")
        code, res = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 0, res)
        self.assertEqual(len(res["installed"]), 3)
        self.assertEqual((target / "other-skill" / "SKILL.md").read_text(), "keep")
        # 再装一次：替换自己装的
        code, _ = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 0)
        # 同名但不是本工具装的：拒绝覆盖
        (target / "job-mate-apply" / "BUILD.json").unlink()
        code, res = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 2)
        self.assertIn("不覆盖", res["error"])


class BuildSafetyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.source = self.base / "source"
        for skill in ("apply", "followup", "setup"):
            d = self.source / "skills" / skill
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"---\nname: {skill}\ndescription: 示例\n---\n正文\n")
        (self.source / "shared").mkdir()
        (self.source / "shared" / "rules.md").write_text("规则\n")
        (self.source / "scripts").mkdir()
        (self.source / "scripts" / "demo.py").write_text("print('demo')\n")
        self.root_patch = patch.object(build, "ROOT", self.source)
        self.root_patch.start()
        self.out = self.base / "out"

    def tearDown(self):
        self.root_patch.stop()
        self.tmp.cleanup()

    def test_all_skill_arguments_validated_before_any_side_effect(self):
        victim = self.base / "victim"
        victim.mkdir()
        canary = victim / "CANARY"
        canary.write_text("keep")
        for invalid in ("apply/../../victim", "unknown", "../apply", "/apply", "apply/", ""):
            with self.subTest(invalid=invalid):
                code, result = run_build("--out", str(self.out), "--skill", "apply", "--skill", invalid)
                self.assertEqual(code, 2, result)
                self.assertEqual(canary.read_text(), "keep")
                self.assertFalse(self.out.exists())

    def test_invalid_skill_keeps_existing_outputs_and_skips_git(self):
        code, result = run_build("--out", str(self.out))
        self.assertEqual(code, 0, result)
        before = {p.relative_to(self.out): p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
        with patch.object(build, "git_info") as git:
            code, result = run_build("--out", str(self.out), "--skill", "apply", "--skill", "missing")
        self.assertEqual(code, 2, result)
        git.assert_not_called()
        self.assertEqual({p.relative_to(self.out): p.read_bytes() for p in self.out.rglob("*") if p.is_file()}, before)

    def test_source_and_output_overlap_rejected_without_changes(self):
        before = (self.source / "skills/apply/SKILL.md").read_bytes()
        for dest in (self.source, self.base, self.source / "skills", self.source / "shared/nested",
                     self.source / "scripts", self.source / ".git/nested"):
            with self.subTest(dest=dest):
                code, result = run_build("--out", str(dest))
                self.assertEqual(code, 2, result)
                self.assertEqual((self.source / "skills/apply/SKILL.md").read_bytes(), before)
                self.assertFalse((dest / "job-mate-apply").exists())

    def test_root_aliases_allowed_but_package_and_source_symlinks_rejected(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "CANARY").write_text("keep")
        alias = self.base / "alias"
        alias.symlink_to(outside, target_is_directory=True)
        for dest in (alias, alias / "child"):
            code, result = run_build("--out", str(dest))
            self.assertEqual(code, 0, result)
        self.assertEqual((outside / "CANARY").read_text(), "keep")
        self.assertTrue((outside / "job-mate-apply/SKILL.md").is_file())
        self.out.mkdir()
        (self.out / "job-mate-followup").symlink_to(outside, target_is_directory=True)
        code, result = run_build("--out", str(self.out))
        self.assertEqual(code, 2, result)
        self.assertFalse((self.out / "job-mate-apply").exists())
        (self.out / "job-mate-followup").unlink()
        (self.source / "scripts/demo.py").unlink()
        (self.source / "scripts/demo.py").symlink_to(outside / "CANARY")
        code, result = run_build("--out", str(self.out))
        self.assertEqual(code, 2, result)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_dangling_and_nested_target_symlinks_are_not_followed(self):
        self.out.mkdir()
        dest = self.out / "job-mate-followup"
        dest.symlink_to(self.base / "missing", target_is_directory=True)
        code, result = run_build("--out", str(self.out))
        self.assertEqual(code, 2, result)
        self.assertTrue(dest.is_symlink())
        self.assertEqual(list(self.out.iterdir()), [dest])
        dest.unlink()
        code, result = run_build("--out", str(self.out))
        self.assertEqual(code, 0, result)
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "CANARY").write_text("keep")
        (dest / "shared/rules.md").unlink()
        (dest / "shared/rules.md").symlink_to(outside / "CANARY")
        code, result = run_build("--out", str(self.out))
        self.assertEqual(code, 2, result)
        self.assertEqual((outside / "CANARY").read_text(), "keep")
        self.assertTrue((dest / "shared/rules.md").is_symlink())

    def test_source_directory_alias_and_external_link_fail_without_publication(self):
        shared = self.source / "shared"
        shared.rename(self.base / "shared-original")
        shared.symlink_to(self.base / "shared-original", target_is_directory=True)
        code, result = run_build("--out", str(self.out))
        self.assertEqual(code, 2, result)
        self.assertFalse(self.out.exists())
        shared.unlink()
        (self.base / "shared-original").rename(shared)
        private = self.base / "private.md"
        private.write_text("private canary")
        skill = self.source / "skills/followup/SKILL.md"
        skill.write_text(skill.read_text() + f"[外部]({private})\n")
        code, result = run_build("--out", str(self.out))
        self.assertEqual(code, 2, result)
        self.assertFalse(self.out.exists())
        self.assertEqual(private.read_text(), "private canary")

    def test_later_unmanaged_install_target_prevents_all_writes(self):
        target = self.base / "installed"
        dest = target / "job-mate-followup"
        dest.mkdir(parents=True)
        (dest / "BUILD.json").write_text('{}')
        (dest / "CANARY").write_text("keep")
        code, result = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 2, result)
        self.assertFalse(self.out.exists())
        self.assertFalse((target / "job-mate-apply").exists())
        self.assertEqual((dest / "CANARY").read_text(), "keep")

    def test_install_aliases_resolve_before_overlap_checks(self):
        for target in (self.out, self.out / "nested", self.base, self.source / "skills"):
            with self.subTest(target=target):
                code, result = run_build("--out", str(self.out), "--install", str(target))
                self.assertEqual(code, 2, result)
                self.assertFalse(self.out.exists())
        alias = self.base / "alias"
        alias.symlink_to(self.base, target_is_directory=True)
        code, result = run_build("--out", str(self.out), "--install", str(alias / "installed"))
        self.assertEqual(code, 0, result)
        source_alias = self.base / "source-alias"
        source_alias.symlink_to(self.source / "skills", target_is_directory=True)
        before = (self.out / "job-mate-apply/BUILD.json").read_bytes()
        code, result = run_build("--out", str(self.out), "--install", str(source_alias / "nested"))
        self.assertEqual(code, 2, result)
        code, result = run_build("--out", str(source_alias / "nested"))
        self.assertEqual(code, 2, result)
        self.assertFalse((self.source / "skills/nested").exists())
        self.assertEqual((self.out / "job-mate-apply/BUILD.json").read_bytes(), before)

    def test_managed_install_rejects_extra_modified_and_wrong_identity(self):
        target = self.base / "installed"
        code, result = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 0, result)
        dest = target / "job-mate-followup"
        manifest = dest / "BUILD.json"
        original = manifest.read_bytes()
        extra = dest / "CANARY"
        extra.write_text("keep")
        before = (self.out / "job-mate-apply/BUILD.json").read_bytes()
        code, result = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 2, result)
        self.assertEqual(extra.read_text(), "keep")
        self.assertEqual((self.out / "job-mate-apply/BUILD.json").read_bytes(), before)
        extra.unlink()
        for bad in ("not-json", "[]", '{}', json.dumps(dict(json.loads(original), skill="job-mate-apply"))):
            with self.subTest(bad=bad):
                manifest.write_text(bad)
                code, result = run_build("--out", str(self.out), "--install", str(target))
                self.assertEqual(code, 2, result)
                self.assertEqual(manifest.read_text(), bad)
        manifest.write_bytes(original)
        rules = dest / "shared/rules.md"
        rules.write_text("user changes")
        code, result = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 2, result)
        self.assertEqual(rules.read_text(), "user changes")

    def test_only_manifest_or_wrong_frontmatter_cannot_claim_ownership(self):
        dest = self.out / "job-mate-followup"
        dest.mkdir(parents=True)
        (dest / "BUILD.json").write_text(json.dumps({"source": "job-mate", "format": 1,
                                                    "skill": "job-mate-followup", "files": {}, "directories": []}))
        code, result = run_build("--out", str(self.out))
        self.assertEqual(code, 2, result)
        self.assertFalse((self.out / "job-mate-apply").exists())
        (dest / "BUILD.json").unlink()
        dest.rmdir()
        code, result = run_build("--out", str(self.out))
        self.assertEqual(code, 0, result)
        # Even a matching inventory cannot turn another skill into a replaceable target.
        skill = dest / "SKILL.md"
        skill.write_text("---\nname: unrelated\n---\n")
        manifest = dest / "BUILD.json"
        info = json.loads(manifest.read_text())
        info["files"]["SKILL.md"] = hashlib.sha256(skill.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(info))
        code, result = run_build("--out", str(self.out))
        self.assertEqual(code, 2, result)
        self.assertIn("unrelated", skill.read_text())

    def test_verified_old_manifest_allows_removed_and_renamed_files(self):
        # 信任边界经复审调整：完整 format1 清单、摘要、技能身份和允许顶层证明旧包归属。
        # 同权限用户伪造整个合法清单不在威胁范围；不能用新版本子集约束阻断正常升级。
        retired = self.source / "shared/retired.md"
        retired.write_text("old version")
        target = self.base / "installed"
        code, result = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 0, result)
        retired.rename(self.source / "shared/renamed.md")
        code, result = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 0, result)
        for root in (self.out, target):
            self.assertFalse((root / "job-mate-followup/shared/retired.md").exists())
            self.assertEqual((root / "job-mate-followup/shared/renamed.md").read_text(), "old version")

    def test_used_install_with_python_cache_and_ds_store_can_upgrade(self):
        target = self.base / "installed"
        code, result = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 0, result)
        scripts = target / "job-mate-apply/scripts"
        proc = subprocess.run([sys.executable, "-I", "-c",
                               f"import sys; sys.path.insert(0, {str(scripts)!r}); import demo"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(list((scripts / "__pycache__").glob("*.pyc")))
        for folder in (target / "job-mate-apply", scripts):
            (folder / ".DS_Store").write_bytes(b"finder metadata")
        (self.source / "shared/rules.md").write_text("new rules")
        code, result = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 0, result)
        self.assertEqual((target / "job-mate-apply/shared/rules.md").read_text(), "new rules")
        self.assertFalse((scripts / "__pycache__").exists())
        self.assertFalse(list(target.rglob(".DS_Store")))

    def test_legacy_manifest_error_explains_move_out_and_retry(self):
        dest = self.out / "job-mate-apply"
        dest.mkdir(parents=True)
        legacy = json.dumps({"source": "/legacy/source", "skill": "job-mate-apply"})
        (dest / "BUILD.json").write_text(legacy)
        code, result = run_build("--out", str(self.out))
        self.assertEqual(code, 2, result)
        self.assertIn("整体迁出 runtime 扫描的技能目录", result["error"])
        self.assertIn("不要仅在技能目录内改名", result["error"])
        self.assertIn("重试", result["error"])
        self.assertIn(str(dest.resolve()), result["error"])
        self.assertEqual((dest / "BUILD.json").read_text(), legacy)

    def test_no_git_builds_with_null_provenance(self):
        with patch.object(build.subprocess, "run", side_effect=FileNotFoundError("git")):
            code, result = run_build("--out", str(self.out))
        self.assertEqual(code, 0, result)
        self.assertIsNone(result["commit"])
        self.assertIsNone(result["dirty"])

    def test_vendored_source_does_not_claim_enclosing_repo_commit(self):
        def git(*args):
            return subprocess.run(["git", "-C", str(self.base), *args],
                                  capture_output=True, text=True, check=True)
        git("init", "-q")
        git("-c", "user.name=Synthetic", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "outer")
        code, result = run_build("--out", str(self.out))
        self.assertEqual(code, 0, result)
        self.assertIsNone(result["commit"])
        self.assertIsNone(result["dirty"])

    def test_failed_batch_publish_restores_both_roots_and_can_retry(self):
        target = self.base / "installed"
        code, result = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 0, result)
        before = {p.relative_to(self.base): p.read_bytes() for root in (self.out, target)
                  for p in root.rglob("*") if p.is_file()}
        (self.source / "shared/rules.md").write_text("new version")
        rename = build.fsutil.replace_atomic
        def fail_once(path, dest):
            if Path(dest) == target.resolve() / "job-mate-followup" and "new" in path.parts:
                raise OSError("injected publish failure")
            return rename(path, dest)
        with patch.object(build.fsutil, "replace_atomic", fail_once):
            code, result = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 2, result)
        after = {p.relative_to(self.base): p.read_bytes() for root in (self.out, target)
                 for p in root.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        self.assertFalse(list(self.base.rglob(".job-mate-transaction-*")))
        code, result = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 0, result)
        self.assertEqual((target / "job-mate-followup/shared/rules.md").read_text(), "new version")

    def test_partial_copy_failure_preserves_old_packages_and_cleans_stage(self):
        target = self.base / "installed"
        code, result = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 0, result)
        before = {p.relative_to(self.base): p.read_bytes() for root in (self.out, target)
                  for p in root.rglob("*") if p.is_file()}
        (self.source / "shared/rules.md").write_text("new version")
        copytree = build.shutil.copytree
        def fail_copy(source, dest, *args, **kwargs):
            dest = Path(dest)
            if dest.name == "job-mate-followup" and target.resolve() in dest.parents:
                dest.mkdir()
                (dest / "SKILL.md").write_bytes(b"partial copy")
                raise OSError("injected copy failure")
            return copytree(source, dest, *args, **kwargs)
        with patch.object(build.shutil, "copytree", fail_copy):
            code, result = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 2, result)
        self.assertIn("injected copy failure", result["error"])
        self.assertEqual({p.relative_to(self.base): p.read_bytes() for root in (self.out, target)
                          for p in root.rglob("*") if p.is_file()}, before)
        self.assertFalse(list(self.base.rglob(".job-mate-transaction-*")))
        code, result = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 0, result)

    def test_rollback_failure_preserves_complete_backup_and_reports_recovery_path(self):
        target = self.base / "installed"
        code, result = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 0, result)
        dest = target / "job-mate-followup"
        old = {p.relative_to(dest): p.read_bytes() for p in dest.rglob("*") if p.is_file()}
        (self.source / "shared/rules.md").write_text("new version")
        rename = build.fsutil.replace_atomic
        def fail_publish_and_rollback(path, destination):
            if Path(destination) == dest.resolve() and ("new" in path.parts or "old" in path.parts):
                raise OSError("injected rename failure")
            return rename(path, destination)
        with patch.object(build.fsutil, "replace_atomic", fail_publish_and_rollback):
            code, result = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 2, result)
        self.assertIn("回滚未完成", result["error"])
        backups = list(target.glob(".job-mate-transaction-*/old/job-mate-followup"))
        self.assertEqual(len(backups), 1)
        self.assertIn(str(backups[0].resolve()), result["error"])
        self.assertEqual({p.relative_to(backups[0]): p.read_bytes() for p in backups[0].rglob("*") if p.is_file()}, old)
        self.assertFalse(dest.exists())
        # Reported backup is directly recoverable, without reconstructing files from a manifest.
        backups[0].rename(dest)
        self.assertEqual({p.relative_to(dest): p.read_bytes() for p in dest.rglob("*") if p.is_file()}, old)

    def test_abnormal_cleanup_keeps_unknown_data_in_owned_staging_area(self):
        target = self.base / "installed"
        code, result = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 0, result)
        copytree = build.shutil.copytree
        def fail_with_unknown_data(source, dest, *args, **kwargs):
            dest = Path(dest)
            if dest.name == "job-mate-followup" and target.resolve() in dest.parents:
                dest.mkdir()
                (dest / "USER-CANARY").write_text("keep")
                raise OSError("injected copy failure")
            return copytree(source, dest, *args, **kwargs)
        with patch.object(build.shutil, "copytree", fail_with_unknown_data):
            code, result = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 2, result)
        canaries = list(target.glob(".job-mate-transaction-*/new/job-mate-followup/USER-CANARY"))
        self.assertEqual(len(canaries), 1)
        self.assertEqual(canaries[0].read_text(), "keep")
        self.assertIn(str(canaries[0].parent.resolve()), result["error"])
        self.assertEqual((target / "job-mate-followup/shared/rules.md").read_text(), "规则\n")

    def test_postcommit_cleanup_keeps_unknown_backup_data_and_reports_warning(self):
        target = self.base / "installed"
        code, result = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 0, result)
        (self.source / "shared/rules.md").write_text("new version")
        rename = build.fsutil.replace_atomic
        def add_unknown_backup_data(path, destination):
            moved = rename(path, destination)
            if Path(destination) == target.resolve() / "job-mate-setup" and path.parent.name == "new":
                backup = path.parent.parent / "old/job-mate-followup"
                (backup / "USER-CANARY").write_text("keep")
            return moved
        with patch.object(build.fsutil, "replace_atomic", add_unknown_backup_data):
            code, result = run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 0, result)
        self.assertTrue(result.get("warnings"), result)
        canaries = list(target.glob(".job-mate-transaction-*/old/job-mate-followup/USER-CANARY"))
        self.assertEqual(len(canaries), 1)
        self.assertEqual(canaries[0].read_text(), "keep")
        self.assertIn(str(canaries[0].parent.resolve()), " ".join(result["warnings"]))
        self.assertEqual((target / "job-mate-followup/shared/rules.md").read_text(), "new version")

    def test_cache_exclusion_does_not_follow_links_or_ignore_user_files(self):
        code, result = run_build("--out", str(self.out))
        self.assertEqual(code, 0, result)
        package = self.out / "job-mate-apply"
        canary = self.base / "CANARY"
        canary.write_text("keep")
        for path in (package / ".DS_Store", package / "scripts/__pycache__"):
            with self.subTest(path=path):
                path.symlink_to(canary)
                code, result = run_build("--out", str(self.out))
                self.assertEqual(code, 2, result)
                self.assertEqual(canary.read_text(), "keep")
                self.assertTrue(path.is_symlink())
                path.unlink()
        cache = package / "scripts/__pycache__"
        cache.mkdir()
        pyc = cache / "demo.pyc"
        pyc.symlink_to(canary)
        code, result = run_build("--out", str(self.out))
        self.assertEqual(code, 2, result)
        self.assertTrue(pyc.is_symlink())
        pyc.unlink()
        if hasattr(os, "mkfifo"):
            for path in (pyc, package / ".DS_Store"):
                os.mkfifo(path)
                code, result = run_build("--out", str(self.out))
                self.assertEqual(code, 2, result)
                self.assertTrue(path.is_fifo())
                path.unlink()
        note = cache / "notes.txt"
        note.write_text("user notes")
        code, result = run_build("--out", str(self.out))
        self.assertEqual(code, 2, result)
        self.assertEqual(note.read_text(), "user notes")

    def test_manifest_paths_cannot_escape_package(self):
        code, result = run_build("--out", str(self.out))
        self.assertEqual(code, 0, result)
        manifest = self.out / "job-mate-apply/BUILD.json"
        original = manifest.read_bytes()
        canary = self.base / "CANARY"
        canary.write_text("outside data")
        for path in ("../../CANARY", str(canary.resolve())):
            with self.subTest(path=path):
                info = json.loads(original)
                info["files"][path] = hashlib.sha256(canary.read_bytes()).hexdigest()
                manifest.write_text(json.dumps(info))
                code, result = run_build("--out", str(self.out))
                self.assertEqual(code, 2, result)
                self.assertEqual(canary.read_text(), "outside data")
        manifest.write_bytes(original)

    def test_nonzero_git_command_gives_null_provenance(self):
        results = [subprocess.CompletedProcess([], 0, str(self.source.resolve()) + "\n"),
                   subprocess.CompletedProcess([], 1, "invalid commit"),
                   subprocess.CompletedProcess([], 0, "")]
        with patch.object(build.subprocess, "run", side_effect=results):
            code, result = run_build("--out", str(self.out))
        self.assertEqual(code, 0, result)
        self.assertIsNone(result["commit"])
        self.assertIsNone(result["dirty"])

    def test_regular_repo_local_build_install_and_repeat_still_work(self):
        out = self.source / "dist"
        target = self.source / ".agents/skills"
        for _ in range(2):
            code, result = run_build("--out", str(out), "--install", str(target),
                                     "--skill", "apply", "--skill", "apply")
            self.assertEqual(code, 0, result)
            self.assertEqual(len(result["built"]), 1)
            self.assertEqual(len(result["installed"]), 1)
            self.assertEqual((out / "job-mate-apply/SKILL.md").read_bytes(),
                             (target / "job-mate-apply/SKILL.md").read_bytes())

    def test_public_manifest_has_stable_source_and_git_provenance(self):
        with patch.object(build, "git_info", return_value=("a" * 40, True)):
            code, result = run_build("--out", str(self.out))
        self.assertEqual(code, 0, result)
        for built in result["built"]:
            raw = (Path(built) / "BUILD.json").read_text()
            info = json.loads(raw)
            self.assertEqual(info["source"], "job-mate")
            self.assertEqual(info["commit"], "a" * 40)
            self.assertIs(info["dirty"], True)
            self.assertNotIn(str(self.source), raw)
            self.assertNotIn(str(self.base), raw)


class TakeoverRulesBuildTest(unittest.TestCase):
    def test_takeover_rule_contract_covers_synthetic_time_scenarios(self):
        # 方案 13.2(10)：这里只锁住技能规则，页面区间判读无脚本实现，
        # 不以静态检查冒充真实 Agent 行为抽查（仍属 13.3 待验）。
        skill = (ROOT / "skills/followup/SKILL.md").read_text()
        boss = (ROOT / "skills/followup/platforms/boss.md").read_text()
        cases = {
            "分钟 [10:00,10:01) 跨过 10:00:30": ("[10:00, 10:01)", "跨分界", "暂停询问"),
            "日期全天跨分界": ("只显示日期为当天全天", "同一天日期标签"),
            "跨午夜：前日全天早于次日分界": ("区间整个早于分界", "历史"),
            "读后、登记前本人发出": ("`readAt`", "登记不用写入时间", "整个晚于分界"),
            "构成分界的 sent 当天日期仍可匹配": ("包括构成分界的那条", "前后 2 分钟"),
            "本人发简历或同意卡片": ("同意交换联系方式", "接受面试", "简历卡片"),
            "上午交还，下午分钟新活动接管": ("下午新发「14:00」是接管",),
            "上午交还，下午同日期第2条仍模糊": ("第 2 条、再次暂停", "不能算历史"),
        }
        for scenario, terms in cases.items():
            with self.subTest(scenario=scenario):
                for term in terms:
                    self.assertIn(term, skill)
        for term in ("置顶", "往上滚动", "23:59:59", "时间或执行者看不清"):
            self.assertIn(term, boss)


if __name__ == "__main__":
    unittest.main()

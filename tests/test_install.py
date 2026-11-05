"""安装探测与确认：所有路径来自临时 HOME，不访问已安装技能。"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_build

B, ROOT = test_build.build, test_build.ROOT


class InstallDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.out = self.root / "built"
        self.environment = {**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home),
                            "PYTHONDONTWRITEBYTECODE": "1"}
        self.env = patch.dict(os.environ, self.environment)
        self.env.start()
        self.addCleanup(self.env.stop)

    def invoke(self, *answers):
        with patch("builtins.input", side_effect=answers), contextlib.redirect_stderr(io.StringIO()):
            return test_build.run_build("--out", str(self.out), "--install")

    def test_discovery_is_bounded_to_known_configuration_roots(self):
        self.assertEqual(B.discover_skill_dirs(), [])
        for name in (".agents", ".claude", ".cursor", ".dsh", ".pi/agent", "unrelated/skills"):
            (self.home / name).mkdir(parents=True)
        found = B.discover_skill_dirs()
        self.assertEqual([p for _, p in found], [self.home / p for p in
                         (".agents/skills", ".claude/skills", ".cursor/skills", ".dsh/skills", ".pi/agent/skills")])
        self.assertTrue(all(not p.exists() for _, p in found))  # 探测不创建 skills。

    def test_dsh_and_shared_directory_install_only_to_selected_location(self):
        for name in (".agents", ".dsh"):
            (self.home / name).mkdir()
        found = B.discover_skill_dirs()
        self.assertEqual([p for _, p in found], [self.home / ".agents/skills", self.home / ".dsh/skills"])
        self.assertTrue(all("DeepSeek Harness" in name for name, _ in found))
        code, out = self.invoke("2", "y")
        self.assertEqual(code, 0, out)
        self.assertEqual({Path(p).parent for p in out["installed"]}, {self.home / ".dsh/skills"})
        self.assertEqual(len(out["installed"]), 3)
        self.assertFalse((self.home / ".agents/skills").exists())

    def test_missing_candidates_does_not_build_or_prompt(self):
        code, out = self.invoke()
        self.assertEqual(code, 2, out)
        self.assertIn("没有找到", out["error"])
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_single_candidate_requires_explicit_confirmation(self):
        config = self.home / ".agents"
        config.mkdir()
        for answer in ("", "n", "1"):
            with self.subTest(answer=answer):
                code, out = self.invoke(answer)
                self.assertEqual(code, 2, out)
                self.assertFalse((config / "skills").exists())
                self.assertFalse(self.out.exists())

    def test_eof_is_not_confirmation(self):
        (self.home / ".claude").mkdir()
        code, out = self.invoke(EOFError())
        self.assertEqual(code, 2, out)
        self.assertIn("尚未确认", out["error"])
        self.assertFalse(self.out.exists())
        self.assertFalse((self.home / ".claude/skills").exists())

    def test_multiple_candidates_require_choice_and_confirmation(self):
        for name in (".agents", ".claude"):
            (self.home / name).mkdir()
        for answers in (("",), ("y",), ("0",), ("3",), ("2", "n")):
            with self.subTest(answers=answers):
                code, out = self.invoke(*answers)
                self.assertEqual(code, 2, out)
                self.assertFalse(self.out.exists())
                self.assertTrue(all(not p.exists() for _, p in B.discover_skill_dirs()))
        code, out = self.invoke("2", "y")
        self.assertEqual(code, 0, out)
        self.assertEqual({Path(p).parent for p in out["installed"]}, {self.home / ".claude/skills"})
        self.assertFalse((self.home / ".agents/skills").exists())
        self.assertEqual(len(out["installed"]), 3)

    def test_auto_install_cli_with_temporary_home_builds_complete_suite(self):
        (self.home / ".cursor").mkdir()
        result = subprocess.run([sys.executable, str(ROOT / "scripts/build.py"),
                                 "--out", str(self.out), "--install"],
                                input="y\n", text=True, encoding="utf-8", capture_output=True,
                                env=self.environment)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = json.loads(result.stdout)
        self.assertIn("确认", result.stderr)
        self.assertEqual({Path(p).name for p in output["installed"]},
                         {"job-mate-setup", "job-mate-apply", "job-mate-followup"})
        versions = []
        for package in map(Path, output["installed"]):
            B.managed(package, package.name)
            manifest = json.loads((package / "BUILD.json").read_text(encoding="utf-8"))
            versions.append((manifest["suiteVersion"], manifest["commit"]))
            self.assertTrue((package / "shared/ask.md").is_file())
            self.assertTrue((package / "scripts/workspace.py").is_file())
        self.assertEqual(len(set(versions)), 1)

    def test_explicit_install_does_not_discover_or_prompt(self):
        target = self.root / "explicit skills"
        with patch.object(B, "discover_skill_dirs", side_effect=AssertionError("不应探测")), \
                patch("builtins.input", side_effect=AssertionError("不应交互")):
            code, out = test_build.run_build("--out", str(self.out), "--install", str(target))
        self.assertEqual(code, 0, out)
        self.assertEqual({Path(p).parent for p in out["installed"]}, {target})

    def test_build_only_never_installs_or_discovers(self):
        with patch.object(B, "discover_skill_dirs", side_effect=AssertionError("不应探测")):
            code, out = test_build.run_build("--out", str(self.out))
        self.assertEqual(code, 0, out)
        self.assertNotIn("installed", out)
        self.assertEqual(list(self.home.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

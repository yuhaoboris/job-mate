"""开源文件与仓库隐私边界检查。"""
import hashlib
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class RepoHygieneTest(unittest.TestCase):
    def test_desktop_scenarios_are_synthetic_and_have_no_runtime_history(self):
        fake = ROOT / "tests/fakes"
        scenarios = sorted((fake / "scenarios").glob("*.json"))
        self.assertTrue(scenarios)
        for path in scenarios:
            with self.subTest(path=path.name):
                scene = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(scene["schemaVersion"], 1)
                self.assertIs(scene["synthetic"], True)
                self.assertEqual(scene["state"]["calls"], [])
                self.assertEqual(scene["state"]["snapshots"], {})
                self.assertEqual(scene["state"]["inputEvents"], [])
                self.assertEqual(scene["state"]["drafts"], {})
        self.assertFalse(list(fake.rglob("*.lock")))
        self.assertFalse(list(fake.rglob("*.cmd")))

    def test_license_is_apache_2(self):
        # https://www.apache.org/licenses/LICENSE-2.0.txt，下载于 2026-11-04。
        self.assertEqual(hashlib.sha256((ROOT / "LICENSE").read_bytes()).hexdigest(),
                         "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30")

    @unittest.skipUnless(shutil.which("git"), "需要 Git")
    def test_private_paths_are_ignored_at_every_depth(self):
        for path in ("profile.json", "x/profile.json", "workspace1/ledger/events.jsonl",
                     "a/b/resumes/r.pdf", ".private/plans/p.md", "dist/x",
                     "x/setup-progress.json", "x/.job-mate-txn.json", "lark-im-resources/a"):
            with self.subTest(path=path):
                result = subprocess.run(["git", "check-ignore", "--no-index", path],
                                        cwd=ROOT, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
        for path in ("scripts/ledger.py", "shared/ledger.md", "platforms/boss/platform.json"):
            with self.subTest(path=path):
                result = subprocess.run(["git", "check-ignore", "--no-index", path],
                                        cwd=ROOT, capture_output=True)
                self.assertEqual(result.returncode, 1, result.stderr)

    @unittest.skipUnless(shutil.which("git"), "需要 Git")
    def test_tracked_files_do_not_match_private_ignore_rules(self):
        result = subprocess.run(["git", "ls-files", "-ci", "--exclude-standard"],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()

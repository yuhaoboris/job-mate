"""固定 CI 矩阵与安全构建命令；不把静态检查当作异系统运行结果。"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class CiTest(unittest.TestCase):
    def test_workflow_matrix_and_commands(self):
        text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^  push:$")
        self.assertRegex(text, r"(?m)^  pull_request:$")
        self.assertIn("os: [ubuntu-24.04, macos-latest, windows-latest]", text)
        self.assertIn("python: ['3.9', '3.12', '3.13']", text)
        self.assertIn("runs-on: ${{ matrix.os }}", text)
        self.assertIn("python-version: ${{ matrix.python }}", text)
        for name in ("PYTHONDONTWRITEBYTECODE", "PYTHONUTF8"):
            self.assertIn(f"{name}: '1'", text)
        commands = re.findall(r"(?m)^        run: (.+)$", text)
        self.assertEqual(commands, ["git config --global core.autocrlf false",
                                    "python -m unittest discover -s tests",
                                    'python scripts/build.py --out "${{ runner.temp }}/dist"'])
        self.assertLess(text.index("core.autocrlf false"), text.index("actions/checkout@"))
        checkout = text.split("uses: actions/checkout@", 1)[1].split("- uses:", 1)[0]
        self.assertRegex(checkout, r"(?m)^          fetch-depth: 0$")
        self.assertIn("actions/setup-node@v4", text)
        self.assertIn("node-version: lts/*", text)
        self.assertLess(text.index("actions/setup-node@"), text.index("name: 全量单测"))
        self.assertIn("contents: read", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("--install", text)

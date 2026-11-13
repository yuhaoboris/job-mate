"""检查 CI 的覆盖与执行契约，不固定 action 版本或代替异系统运行。"""

import itertools
import json
import re
import shlex
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def block(text, name):
    """只取本工作流的缩进块，不实现通用 YAML 解析器。"""
    match = re.search(rf"(?m)^([ \t]*){re.escape(name)}:[ \t]*$", text)
    if match is None:
        raise AssertionError(f"missing block: {name}")
    lines = text[match.end():].splitlines()
    end = next((i for i, line in enumerate(lines)
                if line.strip() and len(line) - len(line.lstrip()) <= len(match[1])), len(lines))
    return "\n".join(lines[:end])


def field(text, name):
    match = re.search(rf"(?m)^[ \t]*(?:-[ \t]+)?{re.escape(name)}:[ \t]*(.+)$", text)
    if match is None:
        raise AssertionError(f"missing field: {name}")
    return match[1].strip().strip("\"'")


class CiTest(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    def assert_workflow(self, text):
        events = block(text, "on")
        keys = re.findall(r"(?m)^([ \t]+)(\w+):", events)
        indent = min(len(space) for space, _ in keys)
        self.assertEqual({name for space, name in keys if len(space) == indent},
                         {"push", "pull_request", "workflow_dispatch"})
        self.assertEqual(field(block(events, "push"), "branches").strip("[] \"'"), "master")
        group = field(block(text, "concurrency"), "group")
        for context in ("github.workflow", "github.event_name", "github.event.pull_request.number", "github.ref"):
            self.assertIn(context, group)
        self.assertIn("||", group)
        self.assertEqual(field(block(text, "concurrency"), "cancel-in-progress"), "true")

        matrix = block(text, "matrix")
        systems = {value.strip(" \"'") for value in field(matrix, "os").strip("[]").split(",")}
        expression = re.fullmatch(
            r"\$\{\{\s*fromJSON\(\s*github.event_name\s*==\s*'workflow_dispatch'\s*&&\s*"
            r"'([^']+)'\s*\|\|\s*'([^']+)'\s*\)\s*\}\}", field(matrix, "python"))
        self.assertIsNotNone(expression, "manual and daily Python axes must be selected by the event")
        extra = block(matrix, "include")
        included = {(field(extra, "os"), field(extra, "python"))}
        minimum = {(system, "3.9") for system in ("ubuntu-24.04", "macos-latest", "windows-latest")}
        daily = set(itertools.product(systems, json.loads(expression[2]))) | included
        manual = set(itertools.product(systems, json.loads(expression[1]))) | included
        self.assertEqual(daily, minimum | {("ubuntu-24.04", "3.13")})
        self.assertEqual(manual, set(itertools.product({system for system, _ in minimum},
                                                     ("3.9", "3.12", "3.13"))))
        for name, expected in (("runs-on", "${{ matrix.os }}"), ("python-version", "${{ matrix.python }}"),
                               ("PYTHONDONTWRITEBYTECODE", "1"), ("PYTHONUTF8", "1")):
            self.assertEqual(field(text, name), expected)
        self.assertEqual(field(block(text, "permissions"), "contents"), "read")
        self.assertNotRegex(text, r"(?m)^\s*[\w-]+:\s*(?:write|write-all)\s*$")
        self.assertNotRegex(text, r"secrets\.|continue-on-error:\s*true|\|\|\s*true")

        steps = block(text, "steps")
        commands = [(match.start(), shlex.split(match[1]))
                    for match in re.finditer(r"(?m)^\s*run:\s*(.+)$", steps)]
        test_at, test = next((pos, args) for pos, args in commands if "unittest" in args)
        self.assertRegex(" ".join(test), r"^python(?:3)? (?:-B )?-m unittest discover(?: |$)")
        self.assertEqual(test[test.index("-s") + 1], "tests")
        self.assertFalse({"-p", "-k", "--help"} & set(test), "run the whole test suite")
        build_at, build = next((pos, args) for pos, args in commands if "scripts/build.py" in args)
        self.assertRegex(" ".join(build), r"^python(?:3)? (?:-B )?scripts/build\.py(?: |$)")
        self.assertTrue(build[build.index("--out") + 1].startswith("${{ runner.temp }}/"))
        self.assertNotIn("--install", build)
        self.assertLess(test_at, build_at)
        for action in ("checkout", "setup-python", "setup-node"):
            self.assertLess(steps.index(f"actions/{action}@"), test_at)
        self.assertLess(steps.index("core.autocrlf false"), steps.index("actions/checkout@"))

    def test_workflow_contract(self):
        self.assert_workflow(self.text)

    def test_harmless_format_and_action_updates(self):
        text = re.sub(r"actions/([\w-]+)@v\d+", r"actions/\1@new-version", self.text)
        text = text.replace("os: [ubuntu-24.04, macos-latest, windows-latest]",
                            "os: [ windows-latest, ubuntu-24.04, macos-latest ]")
        text = text.replace("- os: ubuntu-24.04\n            python: '3.13'",
                            "- python: \"3.13\"\n            os: ubuntu-24.04")
        text = text.replace("name: 全量单测", "name: Run tests")
        text = re.sub(r"(?m)^( +)", r"\1\1", text)
        self.assert_workflow(text)

    def test_missing_support_or_filtered_tests_are_rejected(self):
        for text in (self.text.replace("macos-latest", "ubuntu-22.04"),
                     self.text.replace("discover -s tests", "discover -s tests -p test_ci.py")):
            with self.subTest(text=text), self.assertRaises(AssertionError):
                self.assert_workflow(text)

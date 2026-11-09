"""核对文档引用与真实解析器 / 合成 threads 输出，不锁定中文措辞。"""

import argparse
import contextlib
import io
import json
import hashlib
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import im_lark
import ledger
import notify
import workspace

COMMAND_RE = re.compile(
    r"(?<![\w/.-])(ledger|workspace|notify|im_lark\.py) +"
    r"((?:--[\w-]+(?: +(?!-)\S+)? +)*)([a-z][\w-]*)"
)
ALLOW = set()
FOLLOWUP_THREAD_FIELDS = (
    "pendingTails", "inventoryPending", "canNudge", "nudgeReason",
    "nudgeNeedsPageState", "nudgeSeq", "nudgeIntent", "suggestSilent",
    "boundaryAt", "outboundTexts", "inventoryReply", "interviewIntentStages",
    "lastHandled", "info.pending", "info.gaveUp",
)
BASELINE_FOLLOWUP_SHA256 = "454d1c7714e0c6e7c29938e2d20f508b7f80bf81ae9522edd10cf097f7f3fc4b"
BASELINE_ABSENT = {"nudgeReason": "T2.1", "outboundTexts": "T2.2",
                   "info.pending": "T2.3", "info.gaveUp": "T2.3"}


def followup_text():
    return (ROOT / "skills/followup/SKILL.md").read_text(encoding="utf-8").replace("\r\n", "\n")


def mentions(field, text=None):
    if text is None:
        text = followup_text().split("---", 2)[2]
    return re.search(r"(?<![A-Za-z0-9_.])" + re.escape(field) + r"(?![A-Za-z0-9_])", text) is not None


def is_baseline(text=None):
    text = followup_text() if text is None else text.replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest() == BASELINE_FOLLOWUP_SHA256


def active_exemptions(text=None):
    return BASELINE_ABSENT if is_baseline(text) else {}


def missing_fields(text=None):
    return [field for field in FOLLOWUP_THREAD_FIELDS
            if field not in active_exemptions(text) and not mentions(field, text)]


def subcommands(parser):
    return next(action.choices for action in parser._actions
                if isinstance(action, argparse._SubParsersAction))


class SkillStructureTest(unittest.TestCase):
    def test_document_commands_match_real_parsers(self):
        parsers = {name: subcommands(module.build_parser()) for name, module in (
            ("ledger", ledger), ("workspace", workspace),
            ("notify", notify), ("im_lark.py", im_lark))}
        progress = subcommands(parsers["workspace"]["progress"])
        files = sorted((ROOT / "skills").glob("*/SKILL.md")) + sorted((ROOT / "shared").rglob("*.md"))
        count = 0
        for path in files:
            for match in COMMAND_RE.finditer(path.read_text(encoding="utf-8")):
                command, _, action = match.groups()
                reference = (path.relative_to(ROOT).as_posix(), command, action)
                if reference in ALLOW:
                    continue
                count += 1
                with self.subTest(reference=reference):
                    self.assertIn(action, parsers[command])
                    if command == "workspace" and action == "progress":
                        child = re.match(r" +([a-z][\w-]*)", match.string[match.end():])
                        if child:
                            self.assertIn(child.group(1), progress)
        self.assertGreater(count, 0)

    def test_followup_fields_match_real_threads(self):
        import history_fixture
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "job-mate.json").write_text(json.dumps({
                "schemaVersion": 1, "suiteVersion": "1.0.0",
                "accounts": {"boss": {"accountKey": "synthetic"}},
            }), encoding="utf-8")
            (root / "profile.json").write_text(json.dumps({"schemaVersion": 1}), encoding="utf-8")
            code, result = history_fixture.append_action(root, {
                "platform": "boss", "runId": "synthetic", "skill": "apply",
                "kind": "greet", "status": "sent", "company": "合成公司",
                "recruiter": "合成招聘者", "reason": "测试，未操作平台",
            })
            self.assertEqual(code, 0, result)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = ledger.main(["--workspace", str(root), "threads"])
            self.assertEqual(code, 0, output.getvalue())
            result = json.loads(output.getvalue())
            thread = result["threads"][0]
            for field in FOLLOWUP_THREAD_FIELDS:
                with self.subTest(field=field):
                    value = result if field in result else thread
                    for key in field.split("."):
                        self.assertIn(key, value)
                        value = value[key]
        # (b) 正文引用；基线以外不能再使用字段豁免。
        self.assertEqual(missing_fields(), [])
        # (c) 基线豁免不得多报。
        if is_baseline():
            for field in BASELINE_ABSENT:
                with self.subTest(exempt_field=field):
                    self.assertFalse(mentions(field))
        # (d) 豁免字段和任务限定在本轮清单内。
        self.assertLessEqual(set(BASELINE_ABSENT), set(FOLLOWUP_THREAD_FIELDS))
        self.assertLessEqual(set(BASELINE_ABSENT.values()), {"T2.1", "T2.2", "T2.3"})
        # (e) 文本偏离基线后必须清空常量，不能靠扩大豁免绕过。
        if not is_baseline():
            self.assertEqual(BASELINE_ABSENT, {})


if __name__ == "__main__":
    unittest.main()

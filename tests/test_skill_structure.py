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
    r"(?<![A-Za-z0-9_/.-])(ledger|workspace|notify|im_lark\.py)(?=[ \t]+[-a-z])"
)
ALLOW = set()
FOLLOWUP_THREAD_FIELDS = (
    "pendingTails", "inventoryPending", "canNudge", "nudgeReason",
    "nudgeNeedsPageState", "nudgeSeq", "nudgeIntent", "suggestSilent",
    "boundaryAt", "outboundTexts", "inventoryReply", "interviewIntentStages",
    "lastHandled", "info.pending", "info.gaveUp",
)
BASELINE_FOLLOWUP_SHA256 = "454d1c7714e0c6e7c29938e2d20f508b7f80bf81ae9522edd10cf097f7f3fc4b"
BASELINE_ABSENT = {}


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


def command_tokens(source):
    """保留引号/占位符内的空白与中文；自然文边界不吞掉未闭合结构。"""
    tokens = []
    index = 0
    while index < len(source):
        char = source[index]
        if char in " \t[]":
            index += 1
            continue
        if char in "`\n，。；：、（）→":
            break
        start = index
        protected = char in "\"'<"
        if protected:
            end = ">" if char == "<" else char
            index += 1
            value = ""
            while index < len(source) and source[index] != end:
                if source[index] in "\r\n`":
                    raise ValueError("unterminated_token")
                if source[index] == "\\" and char == '"':
                    index += 1
                    if index >= len(source):
                        raise ValueError("unterminated_token")
                value += source[index]
                index += 1
            if index == len(source):
                raise ValueError("unterminated_token")
            index += 1
            if char == "<":
                value = "<" + value + ">"
        else:
            while index < len(source) and source[index] not in " \t[]`\n，。；：、（）→":
                if source[index] in "\"'<":
                    # --option=<value> / --option="value with spaces"
                    prefix = source[start:index]
                    tail = command_tokens(source[index:])
                    if not tail:
                        raise ValueError("missing_option_value")
                    tokens.append((prefix + tail[0][0], True))
                    return tokens + tail[1:]
                index += 1
            value = source[start:index]
        tokens.append((value, protected))
    return tokens


def validate_command(parser, tokens):
    position = 0
    while True:
        options = {flag: action for action in parser._actions for flag in action.option_strings}
        positional = [action for action in parser._actions if not action.option_strings]
        pos_index = 0
        nested = False
        while position < len(tokens):
            token, protected = tokens[position]
            # 选项（含 = 后的 Unicode 值）先识别，不能当成自然文截断。
            if token.startswith("-"):
                flag, equal, inline = token.partition("=")
                action = options.get(flag)
                if action is None:
                    return "invalid"
                nargs = action.nargs
                count = 1 if nargs is None else nargs
                if equal:
                    if count == 0 or not inline:
                        return "invalid"
                    count = 0 if isinstance(count, int) else count
                position += 1
                if isinstance(count, int):
                    for _ in range(count):
                        if position >= len(tokens) or tokens[position][0].startswith("--"):
                            return "invalid"
                        position += 1
                elif count in ("?", "*", "+"):
                    start = position
                    while position < len(tokens) and not tokens[position][0].startswith("-"):
                        if count == "?" and position > start:
                            break
                        position += 1
                    if count == "+" and position == start and not equal:
                        return "invalid"
                else:
                    return "unparsed"
                continue
            # 仅非选项的未保护中文可作为自然文边界；选项值已按 nargs 消费。
            if not protected and re.search(r"[^\x00-\x7f]", token):
                break
            if pos_index >= len(positional):
                return "invalid"
            action = positional[pos_index]
            if isinstance(action, argparse._SubParsersAction):
                if token not in action.choices:
                    return "invalid"
                parser = action.choices[token]
                position += 1
                nested = True
                break
            # ledger lock/inventory/stoplist 的 action 是带 choices 的位置参数，
            # 不是 SubParsersAction。占位符只表示值，不声称运行期参数齐全。
            if action.choices and not (protected and token.startswith("<")) and token not in action.choices:
                return "invalid"
            position += 1
            pos_index += 1
        if nested:
            continue
        for action in positional[pos_index:]:
            if isinstance(action, argparse._SubParsersAction) and action.required:
                return "invalid"
            if action.choices and action.nargs not in ("?", "*"):
                return "invalid"
        return "valid"


def scan_commands(text):
    parsers = {name: module.build_parser() for name, module in (
        ("ledger", ledger), ("workspace", workspace), ("notify", notify), ("im_lark.py", im_lark))}
    results = {name: [] for name in ("valid", "invalid", "unparsed")}
    for match in COMMAND_RE.finditer(text):
        source = text[match.end():].split("\n", 1)[0]
        try:
            tokens = command_tokens(source)
            outcome = validate_command(parsers[match.group(1)], tokens) if tokens else "unparsed"
        except ValueError:
            outcome = "unparsed"
        results[outcome].append((match.group(1), source))
    return results["valid"], results["invalid"], results["unparsed"]


class SkillStructureTest(unittest.TestCase):
    def test_document_commands_match_real_parsers(self):
        files = sorted((ROOT / "skills").glob("*/SKILL.md")) + sorted((ROOT / "shared").rglob("*.md"))
        count = 0
        for path in files:
            valid, invalid, unparsed = scan_commands(path.read_text(encoding="utf-8"))
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertEqual(invalid, [])
                self.assertEqual(unparsed, [])
            count += len(valid)
        self.assertGreater(count, 0)
        self.assertEqual(ALLOW, set())

    def test_command_scanner_negative_and_positive_cases(self):
        negatives = (
            "用ledger definitely_invalid", "workspace progress --workspace X definitely_invalid",
            "workspace progress definitely_invalid", "ledger --workspace <目录> definitely_invalid",
            "`notify definitely_invalid`", "im_lark.py definitely_invalid", "ledger lock definitely_invalid",
            "ledger lock", "workspace progress", "ledger threads --workspace X",
            "notify replies --state", "ledger threads unexpected_word",
        )
        positives = (
            "workspace --workspace <目录> progress start --mode new", "ledger lock acquire --skill followup",
            "用 ledger threads", "`im_lark.py sync`", "notify replies --state test",
            'ledger --workspace "带 空格的目录" threads', 'notify send --text="中文 空白" --kind todo',
            "ledger lock acquire --no-send --skill=followup", "用ledger threads，说明", "ledger threads 中文说明",
        )
        for source in negatives:
            with self.subTest(negative=source):
                valid, invalid, unparsed = scan_commands(source)
                self.assertTrue(invalid)
                self.assertEqual((valid, unparsed), ([], []))
        for source in positives:
            with self.subTest(positive=source):
                valid, invalid, unparsed = scan_commands(source)
                self.assertTrue(valid)
                self.assertEqual((invalid, unparsed), ([], []))
        for source in ('ledger --workspace "unterminated', "ledger --workspace <broken threads"):
            with self.subTest(unparsed=source):
                self.assertTrue(scan_commands(source)[2])

    def test_f5_non_ascii_unknown_options_are_invalid(self):
        # 都是未知选项，而非自然文边界；应归 invalid，不是 unparsed。
        for source in ("ledger threads --bogus=中文", "ledger threads --bogus=abc中文",
                       "`ledger threads --bogus=中文`", "ledger threads --opt 中文",
                       'ledger threads --bogus="中文"'):
            with self.subTest(source=source):
                valid, invalid, unparsed = scan_commands(source)
                self.assertTrue(invalid, (valid, invalid, unparsed))
                self.assertEqual((valid, unparsed), ([], []))
        self.assertEqual(scan_commands("notify send --text=中文 --kind todo")[1:], ([], []))

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

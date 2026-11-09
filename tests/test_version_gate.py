"""版本准入入口登记：新增子命令或改变副作用参数时同步扩充此表。"""

import argparse
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ledger
import notify
import workspace
import im_lark

MODULES = {"ledger": ledger, "notify": notify, "workspace": workspace, "im_lark": im_lark}
# mode 仍表示写入准入，不能与“是否整段持中央锁”混为一谈。
RELEASES_CENTRAL_LOCK = {("im_lark.py", "sync"), ("im_lark.py", "watch")}
# 脚本、完整命令及影响副作用的参数、分类。参数值均为合成数据。
ENTRY_CASES = [
    ("im_lark", "doctor", "read"),
    ("im_lark", "sync", "write"),
    ("im_lark", "sync --send-only", "write"),
    ("im_lark", "sync --fetch-only", "write"),
    # make_ws 为 channel:none、无待回复项，普通 watch 预检后立即退出，不进入循环。
    ("im_lark", "watch", "write"),
    ("ledger", "validate", "read"),
    ("ledger", "screen-check --platform boss --company 示例", "read"),
    ("ledger", "screen-check --platform boss --company 示例 --account main --title 开发 --job-id page-id --run screen-run", "read"),
    ("ledger", "screen-check --platform boss --company 示例 --job-url https://www.zhipin.com/job_detail/page-id.html", "read"),
    ("ledger", "check --platform boss --company 示例", "read"),
    ("ledger", "check --platform boss --account main --company 示例", "read"),
    ("ledger", "decision --id D-20260101-01", "read"),
    ("ledger", "guard --run setup-run --attempt att-example", "write"),
    ("ledger", "guard --run setup-run --attempt att-example --platform boss --account main", "write"),
    ("ledger", "prepare --run setup-run --json {}", "write"),
    ("ledger", "prepare --run setup-run", "write"),
    ("ledger", 'check --run setup-run --json {"kind":"reply","platform":"boss","company":"合成","reason":"合成","text":"合成"}', "read"),
    ("ledger", 'lock acquire --skill followup --restrict {"kinds":["reply"],"scope":{"all":true},"max":0,"quote":"合成"}', "write"),
    ("ledger", 'lock acquire --skill followup --grant {"item":"reply","scope":{"all":true},"max":1,"quote":"合成"}', "write"),
    ("ledger", "append --json {}", "write"),
    ("ledger", "append", "write"),  # stdin 不能绕过
    ("ledger", "tail --run setup-run --thread boss|main|company|recruiter --read-at 2026-01-01T12:00:00+08:00 --json {}", "write"),
    ("ledger", "tail --run setup-run --thread boss|main|company|recruiter --read-at 2026-01-01T12:00:00+08:00", "write"),
    ("ledger", "quota --platform boss --run setup-run", "read"),
    ("ledger", "quota --platform boss --account main --run setup-run", "read"),
    ("ledger", "inventory start --platform boss --run setup-run", "write"),
    ("ledger", "inventory record --platform boss --run setup-run --json {}", "write"),
    ("ledger", "inventory progress --platform boss --run setup-run --reason synthetic", "write"),
    ("ledger", "inventory finish --platform boss --run setup-run --reason synthetic", "write"),
    ("ledger", "threads", "read"),
    ("ledger", "threads --platform boss --account main", "read"),
    ("ledger", "stats", "read"),
    ("ledger", "stats --platform boss --account main", "read"),
    ("ledger", "export --table events --format csv", "read"),
    ("ledger", "export --table events --format jsonl --out {ws}.events.jsonl", "read"),
    ("ledger", "export --table threads --format csv --out {ws}.threads.csv", "read"),
    ("ledger", "export --table threads --format jsonl", "read"),
    ("ledger", "export --table jobs --format csv", "read"),
    ("ledger", "export --table jobs --format jsonl --platform boss --account main", "read"),
    ("ledger", "lock status", "read"),
    ("ledger", "lock acquire --skill setup", "write"),
    ("ledger", "lock acquire --skill apply --no-send", "write"),
    ("ledger", "lock acquire --skill followup", "write"),
    ("ledger", "lock acquire --skill setup --recovery", "recover"),
    ("ledger", "lock acquire --skill setup --recovery --no-send", "recover"),
    ("ledger", "lock renew --run setup-run", "write"),
    ("ledger", "lock release --run setup-run", "recover"),
    ("ledger", "pause status", "read"),
    ("ledger", "pause on --reason 示例", "write"),
    ("ledger", "pause off", "recover"),
    ("ledger", "stoplist list", "read"),
    ("ledger", "stoplist add --name 示例 --reason 示例 --source user", "write"),
    ("ledger", "stoplist remove --name 示例", "write"),
    ("workspace", "init {ws}", "write"),
    ("workspace", "init {ws} --account boss:main:合成", "write"),
    ("workspace", "check", "read"),
    ("workspace", "backup", "write"),
    ("workspace", "commit --staging {ws}.staging --run setup-run", "write"),
    ("workspace", "commit --staging {ws}.staging --run setup-run --stoplist-ops {ws}.ops.json", "write"),
    ("workspace", "commit --recover --run setup-run", "recover"),
    ("workspace", "commit --recover --abandon --confirm-mixed-config --run setup-run", "recover"),
    ("workspace", "restore sample --stoplist-from-backup", "recover"),
    ("workspace", "backups", "read"),
    ("workspace", "restore sample", "recover"),
    ("workspace", "restore sample --run setup-run", "recover"),
    ("workspace", "changed --since sample", "read"),
    ("workspace", "progress start --mode new", "write"),
    ("workspace", "progress start --mode resetup --base-backup sample", "write"),
    ("workspace", "progress start --mode partial --select browser --base-backup sample", "write"),
    ("workspace", "progress show", "read"),
    ("workspace", "progress record browser --status kept", "write"),
    ("workspace", "progress goto browser", "write"),
    ("workspace", "progress finish", "write"),
    ("workspace", "progress clear", "write"),
    ("notify", "send --kind alert --key example --text 示例", "write"),
    ("notify", "poll", "read"),
    ("notify", "incoming --json {}", "write"),
    ("notify", "incoming", "write"),
    ("notify", "outbox", "read"),
    ("notify", "outbox --stale --limit 1", "read"),
    ("notify", "outbox --claim", "write"),
    ("notify", "outbox --claim --limit 1", "write"),
    ("notify", "outbox --claim --stale", "write"),  # 非法组合也不能绕过写入门禁
    ("notify", "receipt --key example --token example --result sent --message-id m --chat-id c --create-time 1", "write"),
    ("notify", "receipt --key example --token example --result sent --message-id m --chat-id c", "write"),
    ("notify", "receipt --key example --token example --result failed --error 示例", "write"),
    ("notify", "receipt --key example --token example --result unknown", "write"),
    ("notify", "replies", "read"),
    ("notify", "reply-state --message example --state ready --decision D-20260101-01", "write"),
    ("notify", "test", "write"),
    ("notify", "status", "read"),
]
CREATION_CASES = ["init {ws}", "progress start --mode new"]


def argv(command, ws):
    # 先分词再展开路径，兼容临时目录路径中的空格（不走 shell）。
    return [arg.replace("{ws}", str(ws)) for arg in command.split()]


def invoke(module, ws, command):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), patch.object(sys, "stdin", io.StringIO("{}")):
        code = module.main(["--workspace", str(ws), *argv(command, ws)])
    return code, json.loads(out.getvalue())


def leaves(parser, prefix=()):
    sub = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
    if sub:
        return set().union(*(leaves(child, (*prefix, name)) for name, child in sub.choices.items()))
    # ledger 现有 lock / pause / stoplist 的 action 是带 choices 的位置参数。
    action = next((a for a in parser._actions if not a.option_strings and a.choices), None)
    return {(*prefix, value) for value in action.choices} if action else {prefix}


class VersionGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.count = 0
        self.testing = patch.dict(os.environ, {"JOB_MATE_TESTING": "1", "JOB_MATE_NOW": "2026-01-01T12:00:00+08:00"})
        self.testing.start()
        self.version = patch.object(ledger, "SUITE_VERSION", "1.0.0")
        self.version.start()

    def tearDown(self):
        self.version.stop()
        self.testing.stop()
        self.tmp.cleanup()

    def make_ws(self, version="1.1.0", recovery=False):
        self.count += 1
        ws = self.base / f"合成 工作区 {self.count}"
        ws.mkdir()
        workspace.init_workspace(ws, {"boss": {"accountKey": "main", "displayName": "合成"}})
        meta = ledger.read_json(ws / ledger.MARKER)
        meta["suiteVersion"] = version
        ledger.write_atomic(ws / ledger.MARKER, json.dumps(meta))
        ledger.write_atomic(ws / "profile.json", json.dumps({"schemaVersion": 1, "im": {"channel": "none"}}))
        ledger.write_atomic(ws / "rules.md", "当前规则")
        ledger.write_atomic(ws / "setup-progress.json", json.dumps({
            "schemaVersion": 1, "mode": "partial", "plan": ["browser"], "answers": {},
            "added": {}, "current": "browser"}))
        sample = ws / "backups/sample"
        sample.mkdir(parents=True)
        ledger.write_atomic(sample / ledger.MARKER, json.dumps(dict(meta, suiteVersion="1.0.0")))
        ledger.write_atomic(sample / "rules.md", "备份规则")
        ledger.write_atomic(sample / "stoplist.yaml", "# 空停止名单\n")
        ledger.write_atomic(sample / "MANIFEST.json", json.dumps({"schemaVersion": 1, "files": [
            {"path": name, "sha256": hashlib.sha256((sample / name).read_bytes()).hexdigest()}
            for name in ("job-mate.json", "rules.md", "stoplist.yaml")]}))
        if recovery:
            ledger.write_lock(ws, {"skill": "setup", "runId": "setup-run", "mode": "recovery",
                                   "expiresAt": "2026-01-01T13:00:00+08:00", "ttlMinutes": 60})
        return ws

    def snapshot(self, ws):
        # 专用互斥文件不是业务状态；只读入口允许创建它们。
        return {str(p.relative_to(ws)): p.read_bytes() for p in ws.rglob("*")
                if p.is_file() and not p.name.endswith(".mutex") and p.relative_to(ws).as_posix() != "ledger/config.lock"}

    def test_entry_table_automatically_covers_all_argparse_command_paths(self):
        self.assertEqual(RELEASES_CENTRAL_LOCK, {("im_lark.py", "sync"), ("im_lark.py", "watch")})
        for command in ("sync", "watch", "watch --status"):
            self.assertEqual(im_lark.entry_mode(im_lark.build_parser().parse_args(command.split())), "write")
        started = time.monotonic()
        code, result = invoke(im_lark, self.make_ws("1.0.0"), "watch")
        self.assertEqual((code, result["reason"]), (0, "inactive"))
        self.assertLess(time.monotonic() - started, 5)
        for name, module in MODULES.items():
            registered = set()
            for script, command, mode in ENTRY_CASES:
                if script != name:
                    continue
                args = module.build_parser().parse_args(argv(command, Path("synthetic")))
                self.assertEqual(module.entry_mode(args), mode, (script, command))
                registered.add((args.cmd, args.action) if hasattr(args, "action") else (args.cmd,))
            self.assertEqual(registered, leaves(module.build_parser()), name)

    def test_every_entry_newer_same_major_workspace(self):
        for script, command, mode in ENTRY_CASES:
            with self.subTest(script=script, command=command, mode=mode):
                ws = self.make_ws()
                if command == "restore sample --run setup-run" or command.startswith("commit --recover"):
                    ledger.write_lock(ws, {"skill": "setup", "runId": "setup-run",
                                           "expiresAt": "2026-01-01T13:00:00+08:00"})
                before = self.snapshot(ws)
                code, out = invoke(MODULES[script], ws, command)
                if mode == "write":
                    self.assertEqual((code, out.get("code")), (2, "suite_too_old"), out)
                    self.assertEqual(self.snapshot(ws), before)
                elif mode == "read":
                    self.assertIn(code, (0, 1), out)
                    self.assertIn("suite_older_than_workspace", out.get("warnings", []), out)
                    self.assertEqual(self.snapshot(ws), before)
                else:
                    self.assertEqual(code, 0, out)
                    self.assertEqual(ledger.read_json(ws / ledger.MARKER)["suiteVersion"], "1.1.0")

    def test_every_entry_different_major_workspace(self):
        for script, command, mode in ENTRY_CASES:
            with self.subTest(script=script, command=command):
                ws = self.make_ws("2.0.0")
                before = self.snapshot(ws)
                code, out = invoke(MODULES[script], ws, command)
                if mode == "read":
                    self.assertIn(code, (0, 1), out)
                    self.assertIn("suite_older_than_workspace", out.get("warnings", []))
                else:
                    self.assertEqual((code, out.get("code")), (2, "suite_major_mismatch"), out)
                self.assertEqual(self.snapshot(ws), before)

    def test_every_write_entry_rejects_recovery_lock_without_mutation(self):
        for script, command, mode in ENTRY_CASES:
            with self.subTest(script=script, command=command):
                ws = self.make_ws("1.0.0", recovery=True)
                before = self.snapshot(ws)
                code, out = invoke(MODULES[script], ws, command)
                if mode == "write":
                    self.assertEqual((code, out.get("code")), (2, "recovery_lock_only"), out)
                    self.assertEqual(self.snapshot(ws), before)
                elif mode == "read":
                    self.assertIn(code, (0, 1), out)
                    self.assertEqual(self.snapshot(ws), before)

    def test_recovery_lock_restore_release_chain_and_auto_recovery(self):
        for auto in (False, True):
            with self.subTest(auto=auto):
                ws = self.make_ws()
                if not auto:
                    code, out = invoke(ledger, ws, "lock acquire --skill setup --recovery --run setup-run")
                    self.assertEqual(code, 0, out)
                    self.assertEqual(out["lock"]["mode"], "recovery")
                    for module, command in ((ledger, "pause on --reason 示例"),
                                            (workspace, "progress record browser --status kept"),
                                            (ledger, "guard --run setup-run --attempt example")):
                        before = self.snapshot(ws)
                        code, out = invoke(module, ws, command)
                        self.assertEqual((code, out.get("code")), (2, "recovery_lock_only"), out)
                        self.assertEqual(self.snapshot(ws), before)
                    code, out = invoke(ledger, ws, "pause off")
                    self.assertEqual(code, 0, out)
                seen = []
                backup = workspace.do_backup
                def observe(ws, label=None):
                    seen.append(ledger.read_lock(ws))
                    return backup(ws, label)
                with patch.object(workspace, "do_backup", side_effect=observe):
                    code, out = invoke(workspace, ws, "restore sample" + ("" if auto else " --run setup-run"))
                self.assertEqual(code, 0, out)
                self.assertEqual(seen[0]["mode"], "recovery")
                self.assertEqual((ws / "rules.md").read_text(encoding="utf-8"), "备份规则")
                self.assertEqual(ledger.read_json(ws / ledger.MARKER)["suiteVersion"], "1.1.0")
                if not auto:
                    self.assertTrue(ledger.read_lock(ws))
                    code, out = invoke(ledger, ws, "lock release --run setup-run")
                    self.assertEqual(code, 0, out)
                self.assertIsNone(ledger.read_lock(ws))

    def test_ordinary_lock_upgrades_but_recovery_lock_does_not(self):
        with patch.object(ledger, "SUITE_VERSION", "1.2.0"):
            for flag, expected in (("", "1.2.0"), (" --recovery", "1.0.0")):
                ws = self.make_ws("1.0.0")
                code, out = invoke(ledger, ws, "lock acquire --skill setup" + flag)
                self.assertEqual(code, 0, out)
                self.assertEqual(ledger.read_json(ws / ledger.MARKER)["suiteVersion"], expected)

    def test_patch_difference_does_not_block_writes_or_downgrade_marker(self):
        ws = self.make_ws("1.0.9")
        code, out = invoke(ledger, ws, "lock acquire --skill setup")
        self.assertEqual(code, 0, out)
        self.assertEqual(ledger.read_json(ws / ledger.MARKER)["suiteVersion"], "1.0.9")

    def test_recovery_flag_restricted_to_setup_acquire(self):
        for command in ("lock acquire --skill apply --recovery", "lock acquire --skill followup --recovery",
                        "lock renew --run setup-run --recovery", "lock release --run setup-run --recovery"):
            with self.subTest(command=command):
                ws = self.make_ws("1.0.0")
                before = self.snapshot(ws)
                code, out = invoke(ledger, ws, command)
                self.assertEqual(code, 2, out)
                self.assertEqual(self.snapshot(ws), before)

    def test_restore_explicit_run_must_be_valid_setup_lock(self):
        for skill, expires, releasing in (("apply", "2026-01-01T13:00:00+08:00", False),
                                          ("setup", "2000-01-01T00:00:00+08:00", False),
                                          ("setup", "2026-01-01T13:00:00+08:00", True)):
            with self.subTest(skill=skill, expires=expires, releasing=releasing):
                ws = self.make_ws()
                ledger.write_lock(ws, {"skill": skill, "runId": "setup-run", "expiresAt": expires,
                                       "releasing": releasing})
                before = self.snapshot(ws)
                code, out = invoke(workspace, ws, "restore sample --run setup-run")
                self.assertEqual(code, 2, out)
                self.assertEqual(self.snapshot(ws), before)
        ws = self.make_ws()
        before = self.snapshot(ws)
        code, out = invoke(workspace, ws, "restore sample --run missing")
        self.assertEqual(code, 2, out)
        self.assertEqual(self.snapshot(ws), before)

    def test_markerless_creation_entries_and_existing_marker_protection(self):
        for command in CREATION_CASES:
            with self.subTest(command=command):
                self.count += 1
                ws = self.base / f"new workspace {self.count}"
                code, out = invoke(workspace, ws, command)
                self.assertEqual(code, 0, out)
                self.assertEqual(ledger.read_json(ws / ledger.MARKER)["suiteVersion"], "1.0.0")
                meta = ledger.read_json(ws / ledger.MARKER)
                meta["suiteVersion"] = "1.1.0"
                ledger.write_atomic(ws / ledger.MARKER, json.dumps(meta))
                before = self.snapshot(ws)
                code, out = invoke(workspace, ws, command)
                self.assertEqual((code, out.get("code")), (2, "suite_too_old"), out)
                self.assertEqual(self.snapshot(ws), before)

    def test_read_error_still_carries_version_warning(self):
        code, out = invoke(workspace, self.make_ws(), "changed --since missing")
        self.assertEqual(code, 2, out)
        self.assertIn("suite_older_than_workspace", out.get("warnings", []))

    def test_restore_can_use_valid_ordinary_setup_lock_without_version_upgrade(self):
        ws = self.make_ws()
        ledger.write_lock(ws, {"skill": "setup", "runId": "setup-run",
                               "expiresAt": "2026-01-01T13:00:00+08:00"})
        code, out = invoke(workspace, ws, "restore sample --run setup-run")
        self.assertEqual(code, 0, out)
        self.assertEqual(ledger.read_json(ws / ledger.MARKER)["suiteVersion"], "1.1.0")
        self.assertEqual(ledger.read_lock(ws)["runId"], "setup-run")

    def test_warning_context_does_not_leak_between_invocations(self):
        code, out = invoke(ledger, self.make_ws(), "stats")
        self.assertIn("suite_older_than_workspace", out["warnings"])
        code, out = invoke(ledger, self.make_ws("1.0.0"), "stats")
        self.assertNotIn("warnings", out)

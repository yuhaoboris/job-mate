import ast
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fsutil


# 真正启动独立 Python 进程；Windows 不依赖 fork 或可执行 shell 脚本。
CHILD = r'''
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import fsutil, ledger
ws, mode = Path(sys.argv[2]), sys.argv[3]
if mode == "append":
    for n in range(200):
        with fsutil.locked(ws / "outer.lock"):
            ledger.append_line(ws, {"pid": os.getpid(), "n": n, "text": "中文"})
elif mode == "race":
    input()
    for n in range(20):
        with fsutil.locked(ws / "new.lock"):
            p = ws / "counter"
            value = int(fsutil.read_text(p)) if p.exists() else 0
            fsutil.write_text(p, str(value + 1))
            time.sleep(0.002)
            assert int(fsutil.read_text(p)) == value + 1, "锁重叠"
elif mode == "crash":
    with fsutil.locked(ws / "crash.lock"):
        print("locked", flush=True)
        os._exit(1)
elif mode == "acquire":
    with fsutil.locked(ws / "crash.lock"):
        print("acquired", flush=True)
'''


class FsutilTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        self.children = []

    def tearDown(self):
        for p in self.children:
            if p.poll() is None:
                p.kill()
            p.communicate()
        self.tmp.cleanup()

    def spawn(self, mode):
        p = subprocess.Popen([sys.executable, "-I", "-c", CHILD, str(ROOT / "scripts"), str(self.ws), mode],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8")
        self.children.append(p)
        return p

    def finish(self, p, timeout=15):
        out, err = p.communicate(timeout=timeout)
        self.assertEqual(p.returncode, 0, out + err)
        return out

    def test_concurrent_append_400_valid_json_lines(self):
        children = [self.spawn("append") for _ in range(2)]
        for p in children:
            self.finish(p)
        rows = [json.loads(s) for s in fsutil.read_text(self.ws / "ledger/events.jsonl").splitlines()]
        self.assertEqual(len(rows), 400)
        self.assertEqual(len({(r["pid"], r["n"]) for r in rows}), 400)
        self.assertTrue(all(r["text"] == "中文" for r in rows))

    def test_first_lock_creation_four_processes_are_exclusive(self):
        children = [self.spawn("race") for _ in range(4)]
        for p in children:
            p.stdin.write("go\n")
            p.stdin.flush()
        for p in children:
            self.finish(p)
        self.assertEqual(fsutil.read_text(self.ws / "counter"), "80")
        self.assertEqual((self.ws / "new.lock").stat().st_size, 1)

    def test_crashed_holder_releases_lock_within_two_seconds(self):
        p = self.spawn("crash")
        out, err = p.communicate(timeout=5)
        self.assertEqual((p.returncode, out.strip()), (1, "locked"), err)
        started = time.monotonic()
        self.assertEqual(self.finish(self.spawn("acquire"), timeout=2).strip(), "acquired")
        self.assertLess(time.monotonic() - started, 2)

    def test_timezone_fallback_and_unknown_zone(self):
        with patch.object(fsutil, "ZoneInfo", side_effect=ZoneInfoNotFoundError):
            for name, hours in (("Asia/Shanghai", 8), ("Asia/Hong_Kong", 8), ("Asia/Taipei", 8),
                                ("Asia/Singapore", 8), ("Asia/Tokyo", 9)):
                self.assertEqual(fsutil.tz_from_name(name).utcoffset(None), timedelta(hours=hours))
            with self.assertRaisesRegex(ValueError, "安装时区数据"):
                fsutil.tz_from_name("Unknown/Zone")

    def test_clock_requires_testing_and_aware_time(self):
        tz = fsutil.tz_from_name("Asia/Tokyo")
        with patch.dict(os.environ, {"JOB_MATE_NOW": "2001-01-01T00:00:00+00:00", "JOB_MATE_TESTING": "0"}):
            self.assertNotEqual(fsutil.now(tz).year, 2001)
            os.environ["JOB_MATE_TESTING"] = "1"
            self.assertEqual(fsutil.now(tz).isoformat(), "2001-01-01T09:00:00+09:00")
            os.environ["JOB_MATE_NOW"] = "2001-01-01T00:00:00"
            with self.assertRaisesRegex(ValueError, "必须含时区"):
                fsutil.now(tz)

    def test_atomic_utf8_write_and_cleanup_on_replace_failure(self):
        p = self.ws / "文本"
        fsutil.write_text(p, "旧")
        fsutil.write_text_atomic(p, "新\n中文")
        self.assertEqual(fsutil.read_text(p), "新\n中文")
        with patch.object(fsutil, "replace_atomic", side_effect=OSError("failure")):
            with self.assertRaises(OSError):
                fsutil.write_text_atomic(p, "不能覆盖")
        self.assertEqual(fsutil.read_text(p), "新\n中文")
        self.assertEqual([f.name for f in self.ws.iterdir()], ["文本"])

    def test_replace_permission_retry_is_bounded(self):
        with patch.object(fsutil.os, "replace", side_effect=[PermissionError(), None]) as replace, \
                patch.object(fsutil.time, "sleep") as sleep:
            fsutil.replace_atomic("src", "dst")
            self.assertEqual(replace.call_count, 2)
            sleep.assert_called_once_with(0.05)
        with patch.object(fsutil.os, "replace", side_effect=PermissionError()) as replace, \
                patch.object(fsutil.time, "sleep") as sleep:
            with self.assertRaises(PermissionError):
                fsutil.replace_atomic("src", "dst")
            self.assertEqual((replace.call_count, sleep.call_count), (41, 40))

    def test_scripts_use_portable_lock_replace_and_explicit_text_encoding(self):
        for source in (ROOT / "scripts").glob("*.py"):
            if source.name == "fsutil.py":
                continue
            content = source.read_text(encoding="utf-8")
            self.assertNotIn("fcntl", content, source)
            self.assertNotIn("msvcrt", content, source)
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
                if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
                    if fn.value.id == "os" and name == "replace":
                        self.fail(str(source))
                    if fn.value.id in {"fsutil", "ledger"}:
                        continue
                if name not in {"open", "read_text", "write_text"}:
                    continue
                # os.open 是描述符 API，不是文本读写。
                if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "os":
                    continue
                mode_index = 1 if isinstance(fn, ast.Name) else 0
                mode = next((kw.value for kw in node.keywords if kw.arg == "mode"), None)
                if mode is None and name == "open" and len(node.args) > mode_index:
                    mode = node.args[mode_index]
                if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" in mode.value:
                    continue
                self.assertTrue(any(kw.arg == "encoding" for kw in node.keywords),
                                f"{source}:{node.lineno} 缺少 encoding")

"""假 runtime 合并唤起；驱动真实本地命令，不绑定/启动任何 Agent。"""
import json
import subprocess
import sys
import unittest

import test_im_preferences

ROOT = test_im_preferences.ROOT


class SyntheticRuntime:
    """无桌面能力的完整 followup 调度协议替身；不解释自然语言。"""
    def __init__(self, ws):
        self.ws = ws
        self.pending = False
        self.requests = []
        self.calls = []

    def call(self, module, *args):
        self.calls.append((module, *args))
        result = subprocess.run([sys.executable, "-B", "-I", str(ROOT / f"scripts/{module}.py"),
                    "--workspace", str(self.ws), *args], capture_output=True, encoding="utf-8", timeout=10)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return json.loads(result.stdout)

    def reply_available(self):
        self.pending = True  # 提示可重复，业务状态仍须重读。

    def drain(self):
        if not self.pending or self.call("ledger", "lock", "status").get("lock"):
            return
        self.pending = False
        self.requests.append({"skill": "followup", "mode": "full", "unattended": True})
        self.call("ledger", "lock", "acquire", "--skill", "followup", "--run", "runtime", "--no-send")
        # 执行轮进入完整技能的入口/消息/跟进/收尾检查点；本替身没有桌面，不能宣称巡检完成。
        self.call("notify", "status")
        self.call("ledger", "threads")
        self.call("notify", "replies")
        self.call("ledger", "quota", "--platform", "boss", "--run", "runtime")
        self.call("ledger", "threads")
        self.call("ledger", "lock", "release", "--run", "runtime")
        self.call("notify", "status")


class ImRuntimeTest(unittest.TestCase):
    def test_w27_repeated_events_schedule_full_followup_only_after_active_round(self):
        case = test_im_preferences.ImPreferencesTest(); case.setUp(); self.addCleanup(case.doCleanups)
        self.assertEqual(case.f.ledger_run("lock", "release", "--run", "followup")[0], 0)
        self.assertEqual(case.f.ledger_run("lock", "acquire", "--skill", "apply", "--run", "active-apply")[0], 0)
        runtime = SyntheticRuntime(case.f.ws)
        runtime.reply_available(); runtime.reply_available(); runtime.drain()
        self.assertEqual(runtime.requests, [])
        self.assertEqual(case.f.ledger_run("lock", "release", "--run", "active-apply")[0], 0)
        runtime.drain()
        self.assertEqual(runtime.requests, [{"skill": "followup", "mode": "full", "unattended": True}])
        count = len(runtime.calls); runtime.drain(); self.assertEqual(len(runtime.calls), count)
        acquisitions = [call for call in runtime.calls if call[:3] == ("ledger", "lock", "acquire")]
        self.assertEqual(len(acquisitions), 1)
        self.assertIn("followup", acquisitions[0]); self.assertIn("--no-send", acquisitions[0])
        self.assertFalse(any("apply" in call for call in runtime.calls))
        self.assertIn(("ledger", "threads"), runtime.calls)
        self.assertIn(("ledger", "quota", "--platform", "boss", "--run", "runtime"), runtime.calls)
        self.assertEqual(test_im_preferences.L.attempts_of(test_im_preferences.L.read_events(case.f.ws)[0]), {})


if __name__ == "__main__":
    unittest.main()

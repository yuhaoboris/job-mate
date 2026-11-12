"""普通 IM 入站到受限配置提交；只用合成消息，语义计划由替身给定。"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_notify
import test_workspace

N = test_notify.notify
ROOT = Path(__file__).resolve().parents[1]


class ImIntakeTest(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, JOB_MATE_TESTING="1", JOB_MATE_NOW="2026-11-10T10:00:00+08:00")
        env.start()
        self.addCleanup(env.stop)
        self.f = test_notify.NotifyTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.f.im.clock = N.now_ms() - 1000
        self.profile = copy.deepcopy(test_workspace.PROFILE)
        self.profile["im"] = {"channel": "lark", "ownerId": test_notify.USER, "larkProfile": "synthetic"}
        (self.f.ws / "profile.json").write_bytes(N.profile_bytes(self.profile))
        self.f.establish()
        self.stage = tempfile.TemporaryDirectory()
        self.addCleanup(self.stage.cleanup)

    def check_preference_roundtrip(self, recent_test):
        if recent_test:
            self.f.queue_test()  # 本地假回执，不调用飞书。
        text = "地点改为上海"
        message = self.f.im.user_says(text)
        before = (self.f.ws / "profile.json").read_bytes()
        code, incoming = self.f.incoming(self.f.im.messages)
        self.assertEqual(code, 0, incoming)
        self.assertEqual([(r["messageId"], r["state"]) for r in incoming["new"]], [(message, "received")])
        code, pending = self.f.run_cmd("replies", "--state", "received")
        self.assertEqual(code, 0, pending)
        self.assertEqual([r["messageId"] for r in pending["replies"]], [message])
        self.assertIsNone(pending["replies"][0]["decisionHint"])
        self.assertEqual((self.f.ws / "profile.json").read_bytes(), before)
        self.assertEqual(N.open_items(self.f.ws), [])
        status = self.f.run_cmd("status")[1]
        self.assertEqual([r["messageId"] for r in status["pendingReplies"]], [message])
        self.assertFalse(status["replyWatch"]["active"])
        self.assertIsNone(status["replyWatch"]["deadlineAt"])
        ack = N.load_state(self.f.ws)["out"]["ack:" + message]
        self.assertEqual(ack["text"], "【职伴】已收到，等待处理。")

        # 假 Agent 明确映射白名单字段；入站脚本不承担自然语言解析。
        self.assertEqual(self.f.ledger_run("lock", "acquire", "--skill", "followup", "--run", "followup")[0], 0)
        plan = {"revision": 1, "parts": [{"partId": "location", "kind": "preference", "quote": text,
                 "state": "executable", "changes": [{"field": "locations", "before": self.profile["locations"],
                                                        "after": ["上海"]}]}]}
        code, prepared = self.f.run_cmd("reply-state", "--message", message, "--run", "followup",
                                       "--plan", json.dumps(plan, ensure_ascii=False))
        self.assertEqual(code, 0, prepared)
        target = N.profile_bytes(prepared["targets"]["location"])
        (Path(self.stage.name) / "profile.json").write_bytes(target)
        self.assertEqual(self.f.ledger_run("lock", "release", "--run", "followup")[0], 0)
        self.assertEqual(self.f.ledger_run("lock", "acquire", "--skill", "setup", "--run", "setup")[0], 0)
        result = subprocess.run(
            [sys.executable, "-B", "-I", str(ROOT / "scripts/workspace.py"), "--workspace", str(self.f.ws),
             "commit", "--im-reply", message, "--im-part", "location", "--staging", self.stage.name, "--run", "setup"],
            capture_output=True, encoding="utf-8", env=dict(os.environ), timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(result.stderr)
        self.assertEqual((self.f.ws / "profile.json").read_bytes(), target)
        current = json.loads(target)
        self.assertEqual(current["locations"], ["上海"])
        self.assertEqual(current["authorization"], self.profile["authorization"])
        self.assertEqual(current["limits"], self.profile["limits"])
        reply = next(r for r in self.f.run_cmd("replies")[1]["replies"] if r["messageId"] == message)
        self.assertEqual(reply["state"], "consumed")
        self.assertEqual(reply["preferenceChanges"][0]["status"], "applied")
        self.assertEqual(reply["preferenceChanges"][0]["messageId"], message)
        self.assertEqual(N.open_items(self.f.ws), [])
        self.assertEqual(test_notify.ledger.attempts_of(test_notify.ledger.read_events(self.f.ws)[0]), {})

    def test_unprompted_preference_reaches_setup_without_open_decision(self):
        self.check_preference_roundtrip(recent_test=False)

    def test_unquoted_preference_during_test_window_reaches_setup(self):
        self.check_preference_roundtrip(recent_test=True)


if __name__ == "__main__":
    unittest.main()

"""IM 分项计划与真实 setup 配置事务；假 Agent 只提供合成语义映射。"""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_notify
import test_workspace

N, L, W = test_notify.notify, test_notify.ledger, test_workspace.workspace
ROOT = Path(__file__).resolve().parents[1]


class ImPreferencesTest(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, JOB_MATE_TESTING="1", JOB_MATE_NOW="2026-11-10T10:00:00+08:00")
        env.start(); self.addCleanup(env.stop)
        self.f = test_notify.NotifyTest(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.f.im.clock = N.now_ms() - 1000
        self.profile = copy.deepcopy(test_workspace.PROFILE)
        self.profile["im"] = {"channel": "lark", "ownerId": test_notify.USER, "larkProfile": "synthetic"}
        (self.f.ws / "profile.json").write_bytes(N.profile_bytes(self.profile))
        self.f.inbox((test_notify.D1, "待决定"), (test_notify.D2, "待处理"))
        self.f.establish()
        self.text = "职位改为后端开发；地点改为上海；薪资按新范围；D-20261030-01 已处理；其他看着办"
        self.mid = self.f.im.user_says(self.text)
        self.assertEqual(self.f.incoming(self.f.im.messages)[0], 0)
        self.assertEqual(self.f.ledger_run("lock", "acquire", "--skill", "followup", "--run", "followup")[0], 0)
        self.stage = self.f.ws.parent / (self.f.ws.name + "-staging")
        self.stage.mkdir()
        self.addCleanup(lambda: shutil.rmtree(self.stage, ignore_errors=True))

    def preference(self, fields, pid="preferences", members=None):
        return {"partId": pid, "kind": "preference", "quote": self.text, "state": "executable",
                "members": members or [pid], "changes": [{"field": field, "before": N.value_at(self.profile, field), "after": value}
                                                          for field, value in fields.items()]}

    def plan(self, parts, revision=1):
        return self.f.run_cmd("reply-state", "--message", self.mid, "--run", "followup",
                              "--plan", json.dumps({"revision": revision, "parts": parts}, ensure_ascii=False))

    def replies(self):
        return self.f.run_cmd("replies")[1]["replies"]

    def test_c02_field_whitelist_and_frozen_target(self):
        fields = {"targetRoles": ["后端开发"], "locations": ["上海"], "hardRequirements": ["行业：合成行业"],
                  "recruiterActivity.maxAgeDays": 7, "salary.currency": "CNY", "salary.period": "month",
                  "salary.taxBasis": "pre_tax", "salary.min": 20000, "salary.max": 30000,
                  "salary.matchRule": "overlap", "exclusions.roleTypes": [], "exclusions.employmentTypes": [],
                  "exclusions.workSchedules": [], "exclusions.keywords": ["合成排除行业"]}
        before = (self.f.ws / "profile.json").read_bytes()
        code, result = self.plan([self.preference(fields)])
        self.assertEqual(code, 0, result)
        self.assertEqual((self.f.ws / "profile.json").read_bytes(), before)
        self.assertEqual(set(fields), N.PREFERENCE_FIELDS)
        part = result["plan"]["parts"][0]
        self.assertEqual(part["beforeHash"], hashlib.sha256(before).hexdigest())
        target = N.profile_bytes(result["targets"]["preferences"])
        self.assertEqual(part["afterHash"], hashlib.sha256(target).hexdigest())
        self.assertEqual(len([r for r in N.read_records(self.f.ws) if r["type"] == "in"]), 1)
        self.assertFalse(any(r.get("kind") == "clarify" for r in N.load_state(self.f.ws)["out"].values()))

    def test_c04_ambiguous_group_has_no_implicit_values(self):
        before = (self.f.ws / "profile.json").read_bytes()
        part = {"partId": "unclear", "kind": "clarification", "quote": "其他看着办",
                "state": "needs_clarification", "questionFields": ["hardRequirements"]}
        self.assertEqual(self.plan([part])[0], 0)
        self.assertEqual((self.f.ws / "profile.json").read_bytes(), before)
        reply = next(r for r in self.replies() if r["messageId"] == self.mid)
        self.assertEqual(reply["parts"][0]["state"], "needs_clarification")

    def test_c07_forbidden_fields_cannot_enter_plan(self):
        for field in ("authorization.apply.send", "authorization.followup.reply", "authorization.followup.nudge",
                      "limits.dailyGreetings", "limits.platforms.boss.dailyGreetings", "nudge.maxPerThread",
                      "schedule.timezone", "browser.headedInput", "im.waitMinutes", "restrict", "grant",
                      "style.tone", "greeting.text", "inquiry.priority", "industry"):
            with self.subTest(field=field):
                before = (self.f.ws / "profile.json").read_bytes()
                code, result = self.plan([self.preference({field: True})])
                self.assertEqual((code, result["code"]), (2, "im_change_fields_forbidden"))
                self.assertEqual((self.f.ws / "profile.json").read_bytes(), before)
        self.assertFalse(N.load_state(self.f.ws)["plans"])

    def test_c09_parts_and_dependencies_prevent_whole_message_consumption(self):
        parts = [self.preference({"targetRoles": ["后端开发"], "locations": ["上海"]}, members=["roles", "city"]),
                 {"partId": "answer", "kind": "decision", "quote": "D-20261030-01 已处理", "decision": test_notify.D1,
                  "state": "executable", "dependsOn": ["preferences"]},
                 {"partId": "unclear", "kind": "clarification", "quote": "其他看着办", "state": "needs_clarification"}]
        self.assertEqual(self.plan(parts)[0], 0)
        code, result = self.f.run_cmd("reply-state", "--message", self.mid, "--run", "followup", "--part", "answer", "--state", "ready")
        self.assertEqual((code, result["code"]), (2, "im_change_baseline_conflict"))
        reply = next(r for r in self.replies() if r["messageId"] == self.mid)
        self.assertNotEqual(reply["state"], "consumed")
        self.assertEqual(reply["parts"][0]["members"], ["roles", "city"])

    def test_c11_message_replay_and_c13_plan_stage_guard(self):
        self.assertEqual(self.f.incoming(self.f.im.messages)[0], 0)
        self.assertEqual(len([r for r in N.read_records(self.f.ws) if r["type"] == "in"]), 1)
        self.assertEqual(self.f.ledger_run("lock", "release", "--run", "followup")[0], 0)
        for skill in ("apply", "setup"):
            self.assertEqual(self.f.ledger_run("lock", "acquire", "--skill", skill, "--run", skill)[0], 0)
            code, result = self.plan([self.preference({"locations": ["上海"]})])
            self.assertEqual((code, result["code"]), (2, "followup_lock_required"))
            self.assertEqual(self.f.ledger_run("lock", "release", "--run", skill)[0], 0)

    def test_w24_question_identity_does_not_change_with_wording_or_custom_key(self):
        part = {"partId": "unclear", "kind": "clarification", "quote": "其他看着办", "state": "needs_clarification"}
        self.assertEqual(self.plan([part])[0], 0)
        first = self.f.queue("different-key-1", "question one", "clarify", "--reply-to", self.mid)
        second = self.f.queue("different-key-2", "question two", "clarify", "--reply-to", self.mid)
        self.assertEqual(first[1]["key"], second[1]["key"])
        self.assertEqual(len([r for r in N.load_state(self.f.ws)["out"].values() if r.get("kind") == "clarify"]), 1)


if __name__ == "__main__":
    unittest.main()

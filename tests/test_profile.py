"""T9.1：共用 profile 校验与合并；真实 CLI 只访问临时合成工作区。"""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_workspace
import test_config_txn
import test_prepare

W, L = test_workspace.workspace, test_workspace.ledger
C = W.config_schema
ROOT = Path(__file__).resolve().parents[1]
BASE = {"schemaVersion": 1, "targetRoles": ["合成开发岗位"], "locations": [],
        "authorization": {"apply": {"send": False}}}


def changed(path, value):
    p = copy.deepcopy(BASE)
    if path.startswith("salary."):
        p["salary"] = {"matchRule": ["overlap"]}
    cursor = p
    keys = path.split(".")
    for key in keys[:-1]:
        cursor = cursor.setdefault(key, {})
    cursor[keys[-1]] = value
    return p


class ProfileTest(unittest.TestCase):
    def test_profile_format_1_only_without_conversion_or_boolean_coercion(self):
        """设计4：此前沿用2并把1判错；现以唯一格式1为正例，其他版本不迁移。"""
        for version in (1, 2, 0, 3, True, False, 1.0, "1", None):
            with self.subTest(version=version):
                p = changed("schemaVersion", version)
                before = copy.deepcopy(p)
                if type(version) is int and version == 1:
                    C.profile(p)
                    self.assertEqual(L.effective_profile(p)["schemaVersion"], 1)
                else:
                    with self.assertRaises(L.LedgerError):
                        C.profile(p)
                    with self.assertRaises(L.LedgerError):
                        L.effective_profile(p)
                self.assertEqual(p, before)

    def test_each_typed_rule_accepts_and_rejects(self):
        rules = (
            ("style.tone", ["polite", "friendly", "concise"], ["formal", 1, None, []]),
            ("style.length", ["short", "medium"], ["long", True, None]),
            ("style.address", ["neutral", "surname"], ["name", {}, None]),
            ("style.selfIntro", [["合成事实"], ["一", "二", "三"]], [[], [""], [" "], [1], "事实", ["一"] * 4, None]),
            ("style.bannedWords", [[], ["亲", "精通"]], ["亲", [""], [True], None]),
            ("style.emoji", [True, False], [1, 0, "false", None]),
            ("browser.headedInput", [False, True], [1, "true", None]),
            ("inquiry.priority", [[], ["employment", "salary"], ["salary", "hours", "location", "leave"]],
             [["salary", "salary"], ["salary.range"], ["unknown"], "salary", [1], [{}], None]),
            ("inquiry.maxPerMessage", [1, 2], [0, 3, True, 1.0, "2", None]),
            ("nudge.maxPerSilence", [1, 3], [0, 4, True, 1.0, None]),
            ("nudge.maxPerThread", [3, 9], [0, 2, 10, False, 6.0, None]),
            ("authorization.apply.send", [False, True], [1, "true", None]),
            ("authorization.followup.reply", ["L1", "L2"], ["L3", "l2", True, None]),
            ("authorization.followup.nudge", [False, True], [0, 1, "true", None]),
            ("limits.dedupScope", ["company", "job"], ["all", 0, None]),
            ("recruiterActivity.maxAgeDays", [0, 7], [True, -1, 7.0, None]),
            ("salary.min", [None, 0, 1.5], [True, -1, float("inf"), float("nan")]),
        )
        rules += tuple(("nudge." + key, [1, 24], [0, -1, True, 1.5, 24.0, float("inf"), float("nan"), None])
                       for key in ("afterReadHours", "afterUnreadHours", "intervalHours", "perDay"))
        rules += tuple(("limits." + key, [0, 1], [True, -1, 1.0, float("inf"), None])
                       for key in L.DEFAULT_LIMITS if key != "dedupScope")
        for path, good, bad in rules:
            # 正反分组独立遍历，不能因 bool == 1 而混淆类型。
            for expected, values in ((True, good), (False, bad)):
                for value in values:
                    with self.subTest(path=path, value=value, expected=expected):
                        p = changed(path, value)
                        problems, warnings = [], []
                        W.check_profile(p, problems, warnings)
                        self.assertEqual(not problems, expected, problems)
                        self.assertEqual(not C.profile_issues(p), expected)
                        if expected:
                            C.profile(p)
                            self.assertEqual(W.effective_profile(p), L.effective_profile(p))
                        else:
                            with self.assertRaises(L.LedgerError):
                                C.profile(p)
                            with self.assertRaises(L.LedgerError):
                                L.effective_profile(p)

    def test_unknown_null_and_malformed_sections_are_not_defaults(self):
        for section in ("style", "inquiry", "browser", "authorization", "limits", "nudge", "schedule",
                        "recruiterActivity", "greeting", "exclusions", "im"):
            for value in (None, [], "bad", True, {"unknown": 1}):
                with self.subTest(section=section, value=value):
                    p = changed(section, value)
                    self.assertTrue(C.profile_issues(p))
                    with self.assertRaises(L.LedgerError):
                        L.effective_profile(p)
        for path, value in (("unknown", {}), ("schemaVersion", True), ("schemaVersion", 2),
                            ("authorization.followup.level", "L2"), ("authorization.followup", {"reply": "L3"}),
                            ("targetRoles", []), ("targetRoles", [True]), ("locations", [None])):
            with self.subTest(path=path):
                self.assertTrue(C.profile_issues(changed(path, value)))

    def test_defaults_warn_without_inventing_personal_facts_or_enabling_apply(self):
        before = copy.deepcopy(BASE)
        problems, warnings = [], []
        W.check_profile(BASE, problems, warnings)
        self.assertEqual(problems, [])
        fields = {w["field"] for w in warnings}
        self.assertTrue({"style", "style.selfIntro", "inquiry", "browser", "nudge", "limits",
                         "authorization.followup", "schedule", "im"} <= fields)
        effective = L.effective_profile(BASE)
        self.assertEqual(effective["authorization"], {"apply": {"send": False}, "followup": {"reply": "L2", "nudge": True}})
        self.assertEqual(effective["browser"], {"headedInput": False})
        self.assertNotIn("selfIntro", effective["style"])
        self.assertNotIn("text", effective["greeting"])
        self.assertEqual(BASE, before)
        self.assertTrue(C.profile_issues({}))  # 正式配置不能因默认值而漏掉必填条件。
        self.assertFalse(L.effective_profile({})["authorization"]["apply"]["send"])

    def test_partial_deep_merge_preserves_siblings_and_independent_switches(self):
        p = copy.deepcopy(BASE)
        p.update(style={"tone": "friendly", "selfIntro": ["本人确认的合成事实"], "bannedWords": []},
                 inquiry={"maxPerMessage": 1}, browser={"headedInput": True},
                 authorization={"apply": {"send": False}, "followup": {"reply": "L1"}})
        effective = L.effective_profile(p)
        self.assertEqual(effective["style"], {"tone": "friendly", "length": "short", "address": "neutral",
                                            "selfIntro": ["本人确认的合成事实"], "bannedWords": [], "emoji": False})
        self.assertEqual(effective["inquiry"], {"priority": list(L.DEFAULT_INQUIRY_PRIORITY), "maxPerMessage": 1})
        self.assertEqual(effective["authorization"]["followup"], {"reply": "L1", "nudge": True})
        for reply in ("L1", "L2"):
            for nudge in (False, True):
                p["authorization"]["followup"] = {"reply": reply, "nudge": nudge}
                self.assertEqual(L.effective_profile(p)["authorization"]["followup"], {"reply": reply, "nudge": nudge})

    def test_merge_deep_copies_input_defaults_and_overrides(self):
        p = changed("style.selfIntro", ["合成事实"])
        p["limits"] = {"platforms": {"boss": {"greetsPerRun": 2}}}
        before = copy.deepcopy(p)
        a = L.effective_profile(p)
        a["style"]["selfIntro"].append("不是原始事实")
        a["style"]["bannedWords"].clear()
        a["schedule"]["activeHours"]["weekdays"].clear()
        a["limits"]["platforms"]["boss"]["greetsPerRun"] = 99
        self.assertEqual(p, before)
        b = L.effective_profile(p, "boss")
        self.assertEqual(b["limits"]["greetsPerRun"], 2)
        self.assertEqual(b["style"]["bannedWords"], ["精通"])
        self.assertEqual(b["schedule"]["activeHours"]["weekdays"], [1, 2, 3, 4, 5])
        self.assertEqual(b["style"]["selfIntro"], ["合成事实"])
        self.assertNotIn("platforms", b["limits"])

    def test_platform_precedence_and_every_override_validated(self):
        with patch.dict(os.environ, {"JOB_MATE_TESTING": "1", "JOB_MATE_TEST_PLATFORM_ROOT": str(ROOT / "tests/fixtures/platforms")}):
            p = {"limits": {"detailsPerRun": 8, "platforms": {"testplat": {"greetsPerRun": 9}}},
                 "nudge": {"intervalHours": 60, "platforms": {"boss": {"perDay": 2}}}}
            self.assertEqual(L.effective_profile({}, "testplat")["limits"]["greetsPerRun"], 2)
            merged = L.effective_profile(p, "testplat")
            self.assertEqual((merged["limits"]["greetsPerRun"], merged["limits"]["detailsPerRun"]), (9, 8))
            self.assertEqual(L.effective_limits(p, "testplat"), merged["limits"])
            self.assertEqual(L.effective_nudge(p, "boss")["perDay"], 2)
            self.assertEqual(L.effective_nudge(p, "testplat")["perDay"], 20)
            for field, values in (("limits", {"inboundScanDays": True}), ("nudge", {"intervalHours": 0}),
                                  ("nudge", {"maxPerSilence": 3, "maxPerThread": 2}), ("limits", {"unknown": 2})):
                bad = {field: {"platforms": {"testplat": values}}}
                with self.subTest(bad=bad), self.assertRaises(L.LedgerError):
                    L.effective_profile(bad, "boss")
            # 无效全局值即使被平台覆盖也不能被掩盖。
            with self.assertRaises(L.LedgerError):
                L.effective_profile({"nudge": {"intervalHours": 0, "platforms": {"boss": {"intervalHours": 1}}}}, "boss")

    def test_strict_json_rejects_duplicate_and_nonfinite_numbers(self):
        for raw in ('{"schemaVersion":1,"schemaVersion":1}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}'):
            with self.subTest(raw=raw), self.assertRaises((L.LedgerError, ValueError)):
                C.json_value(raw)


class ProfileCliTest(unittest.TestCase):
    def test_commit_check_and_runtime_use_same_rules_without_bad_writes(self):
        f = test_config_txn.ConfigTransactionTest()
        f.setUp()
        try:
            valid = C.effective_profile(BASE)
            valid["style"]["selfIntro"] = ["本人确认的合成事实"]
            f.stage_file("profile.json", json.dumps(valid))
            f.ok("workspace", "commit", "--staging", str(f.stage), "--run", "setup-txn")
            before = f.config()
            # 真实 check 会报告其他尚未完成材料，但 profile 与 commit 的合法性一致。
            rc, out = f.cli("workspace", "check")
            self.assertEqual([x for x in out["problems"] if x.get("file") == "profile.json"], [])
            for path, value in (("style.selfIntro", []), ("inquiry.maxPerMessage", True), ("browser.headedInput", 1),
                                ("nudge.intervalHours", 0), ("authorization.followup.reply", "L3"),
                                ("limits.inboundScanDays", 1.5), ("nudge.platforms.boss.maxPerThread", 2),
                                ("unknown", {})):
                p = changed(path, value)
                f.stage_file("profile.json", json.dumps(p))
                rc, out = f.commit()
                self.assertEqual(rc, 2, (path, out))
                self.assertEqual(f.config(), before)
                self.assertFalse((f.ws / ".job-mate-txn.json").exists())
                # 同一非法字节作为现状时，check 拒绝且保持原字节。
                raw = json.dumps(p).encode("utf-8")
                (f.ws / "profile.json").write_bytes(raw)
                rc, out = f.cli("workspace", "check")
                self.assertEqual(rc, 1, out)
                self.assertTrue([x for x in out["problems"] if x.get("file") == "profile.json"])
                self.assertEqual((f.ws / "profile.json").read_bytes(), raw)
                (f.ws / "profile.json").write_bytes(before["profile.json"])
            # 部分暂存只替换本人选定的文件，未选字段、事件不改。
            f.stage_file("profile.json", json.dumps(valid))
            f.ok("workspace", "commit", "--staging", str(f.stage), "--run", "setup-txn")
            self.assertEqual(f.config(), before)
        finally:
            f.tearDown()

    def test_real_ledger_defaults_do_not_enable_apply_or_unimplemented_nudge(self):
        f = test_prepare.PrepareTest()
        f.setUp()
        try:
            (f.f.ws / "profile.json").write_text(json.dumps(BASE), encoding="utf-8")
            self.assertEqual(f.acquire()[0], 0)
            before = L.events_path(f.f.ws).read_bytes()
            for kind in ("greet", "nudge"):
                rc, out = f.prepare(kind=kind)
                self.assertEqual((rc, out["reasons"][0]["code"]), (1, "not_authorized"), out)
                self.assertEqual(L.events_path(f.f.ws).read_bytes(), before)
            # L1+nudge=true 仍不允许代答；两个开关互不借权。
            p = changed("authorization.followup.reply", "L1")
            (f.f.ws / "profile.json").write_text(json.dumps(p), encoding="utf-8")
            rc, out = f.prepare()
            self.assertEqual((rc, out["reasons"][0]["code"]), (1, "not_authorized"), out)
            self.assertEqual(L.events_path(f.f.ws).read_bytes(), before)
            # 缺省 L2 支持有依据的答复；独立 nudge=false 不会关闭 reply。
            p = changed("authorization.followup.nudge", False)
            (f.f.ws / "profile.json").write_text(json.dumps(p), encoding="utf-8")
            rc, out = f.prepare()
            self.assertEqual(rc, 0, out)
            self.assertEqual(out["authorizedBy"], "long_term")
        finally:
            f.tearDown()

    def test_invalid_runtime_profile_returns_json_not_traceback_and_does_not_append(self):
        f = test_prepare.PrepareTest()
        f.setUp()
        try:
            self.assertEqual(f.acquire()[0], 0)
            before = L.events_path(f.f.ws).read_bytes()
            for p in (changed("style.selfIntro", []), changed("limits.inboundScanDays", True),
                      changed("authorization.followup.level", "L2"), None, [], 1):
                (f.f.ws / "profile.json").write_text(json.dumps(p), encoding="utf-8")
                rc, out = f.prepare()
                self.assertEqual(rc, 2, out)
                self.assertEqual(L.events_path(f.f.ws).read_bytes(), before)
        finally:
            f.tearDown()


if __name__ == "__main__":
    unittest.main()

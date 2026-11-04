"""平台描述加载与真实数据路径；全用合成工作区，不访问招聘平台。"""

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ledger
import platforms
import workspace

FIXTURES = ROOT / "tests/fixtures/platforms"


def invoke(module, ws, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = module.main(["--workspace", str(ws), *args])
    return code, json.loads(out.getvalue())


class PlatformTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"JOB_MATE_TESTING": "1", "JOB_MATE_TEST_PLATFORM_ROOT": str(FIXTURES)})
        self.env.start()
        self.good = platforms.load_platform("testplat")

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_builtin_boss_description_and_time_vocabulary(self):
        data = platforms.load_platform("boss")
        self.assertEqual(data["automationPolicy"], {"forbidCDP": True, "forbidHeadless": True})
        self.assertEqual(data["conversationKey"], ["company", "recruiter"])
        self.assertEqual(data["activityLabels"], {"在线": 0, "刚刚活跃": 0, "今日活跃": 0, "3日内活跃": 3,
                         "本周活跃": 7, "2周内活跃": 14, "本月活跃": 30, "2月内活跃": 60,
                         "3月内活跃": 90, "半年前活跃": 180})
        labels = data["timeLabels"]
        self.assertEqual(labels["relativeDays"], {"今天": 0, "昨天": 1, "前天": 2})
        self.assertIn("刚刚", labels["recent"])
        self.assertEqual(set(labels["weekdays"]), {"星期" + day for day in "一二三四五六日"})
        self.assertEqual(set(labels["dateFormats"]), {"M月D日", "YYYY-MM-DD", "YYYY年M月D日"})
        self.assertEqual(labels["timeFormat"], "HH:MM")
        self.assertIn("HH:MM", labels["dateTimeFormat"])
        self.assertIs(data["messageId"]["available"], False)
        self.assertIs(self.good["messageId"]["available"], True)

    def test_extra_platform_root_only_works_when_testing_is_one(self):
        for value in ("", "0", "true"):
            with self.subTest(value=value), patch.dict(os.environ, {"JOB_MATE_TESTING": value}):
                with self.assertRaisesRegex(platforms.PlatformError, "未知平台"):
                    platforms.load_platform("testplat")
        self.assertEqual(platforms.load_platform("testplat")["id"], "testplat")

    def test_additional_root_cannot_override_builtin_description(self):
        dest = self.base / "boss"
        dest.mkdir()
        (dest / "platform.json").write_text("invalid", encoding="utf-8")
        with patch.dict(os.environ, {"JOB_MATE_TEST_PLATFORM_ROOT": str(self.base)}):
            self.assertEqual(platforms.load_platform("boss")["name"], "BOSS直聘")

    def test_unknown_and_path_like_ids_are_rejected(self):
        for name in ("missing", "../boss", "/boss", "boss/../boss", "boss\\other", "BOSS", "", None):
            with self.subTest(name=name), self.assertRaises(platforms.PlatformError):
                platforms.load_platform(name)

    def test_each_required_field_is_checked(self):
        for field in platforms.REQUIRED:
            with self.subTest(field=field):
                data = copy.deepcopy(self.good)
                del data[field]
                with self.assertRaisesRegex(platforms.PlatformError, field):
                    platforms.validate_platform(data, "testplat")

    def test_nested_required_fields_and_invalid_values(self):
        cases = [("id", "other"), ("name", ""), ("domains", []), ("domains", ["https://example.invalid"]),
                 ("domains", ["example.invalid", "example.invalid"]), ("conversationKey", [""]),
                 ("recruiterFormat", None), ("jobUrl", []), ("jobUrl.idPattern", "["),
                 ("jobUrl.idPattern", "/jobs/no-group"), ("jobUrl.idPattern", "(one)(two)"),
                 ("jobUrl.stripQuery", 1), ("activityLabels", []), ("activityLabels.recent", True),
                 ("activityLabels.recent", -1), ("automationPolicy", []), ("automationPolicy.forbidCDP", "true"),
                 ("automationPolicy.forbidHeadless", 0), ("defaultLimits", []), ("defaultLimits.greetsPerRun", -1),
                 ("defaultLimits.greetsPerRun", True), ("defaultLimits.dedupScope", 1),
                 ("defaultLimits.dedupScope", "unknown"), ("timeLabels", []), ("timeLabels.relativeDays", []),
                 ("timeLabels.relativeDays.today", True), ("timeLabels.weekdays.Monday", 0),
                 ("timeLabels.weekdays.Monday", 8), ("timeLabels.recent", []),
                 ("timeLabels.dateFormats", ["x", "x"]), ("timeLabels.timeFormat", ""),
                 ("timeLabels.dateTimeFormat", ""), ("messageId", []), ("messageId.available", 0)]
        for path, value in cases:
            with self.subTest(path=path, value=value):
                data = copy.deepcopy(self.good)
                parts = path.split(".")
                obj = data
                for key in parts[:-1]:
                    obj = obj[key]
                obj[parts[-1]] = value
                with self.assertRaises(platforms.PlatformError):
                    platforms.validate_platform(data, "testplat")
        for parent, fields in (("jobUrl", ("idPattern", "stripQuery")),
                               ("automationPolicy", ("forbidCDP", "forbidHeadless")),
                               ("timeLabels", ("relativeDays", "weekdays", "recent", "dateFormats", "timeFormat", "dateTimeFormat")),
                               ("messageId", ("available",))):
            for field in fields:
                with self.subTest(parent=parent, field=field):
                    data = copy.deepcopy(self.good)
                    del data[parent][field]
                    with self.assertRaisesRegex(platforms.PlatformError, field):
                        platforms.validate_platform(data, "testplat")
        for value in ([], None, "bad"):
            with self.subTest(value=value), self.assertRaises(platforms.PlatformError):
                platforms.validate_platform(value, "testplat")

    def test_malformed_json_encoding_and_directory_are_reported(self):
        path = self.base / "testplat/platform.json"
        path.parent.mkdir()
        with patch.dict(os.environ, {"JOB_MATE_TEST_PLATFORM_ROOT": str(self.base)}):
            for raw in (b"{broken", b"\xff"):
                path.write_bytes(raw)
                with self.assertRaisesRegex(platforms.PlatformError, "无法读取平台"):
                    platforms.load_platform("testplat")
            path.unlink()
            path.mkdir()
            with self.assertRaisesRegex(platforms.PlatformError, "无法读取平台"):
                platforms.load_platform("testplat")

    def test_loaded_objects_are_not_mutable_cached_state(self):
        self.good["jobUrl"]["stripQuery"] = True
        self.assertIs(platforms.load_platform("testplat")["jobUrl"]["stripQuery"], False)

    def test_url_helpers_use_selected_pattern_and_strip_query_policy(self):
        boss = "https://www.zhipin.com/job_detail/synthetic.html?lid=synthetic#section"
        fixture = "https://jobs.example.invalid/jobs/AB-12?keep=yes#section"
        self.assertEqual(ledger.job_id_from_url("boss", boss), "synthetic")
        self.assertEqual(ledger.job_id_from_url("testplat", fixture), "AB-12")
        self.assertIsNone(ledger.job_id_from_url("boss", fixture))
        self.assertIsNone(ledger.job_id_from_url("testplat", boss))
        self.assertEqual(ledger.normalize_job_url("boss", boss), boss.split("?")[0])
        self.assertEqual(ledger.normalize_job_url("testplat", fixture), fixture)
        for platform in ("boss", "testplat"):
            self.assertIsNone(ledger.job_id_from_url(platform, None))
            self.assertIsNone(ledger.normalize_job_url(platform, None))

    def test_malformed_event_platform_and_url_return_json_without_writing(self):
        ws = self.base / "invalid-workspace"
        workspace.init_workspace(ws)
        for event in ({"jobUrl": "https://jobs.example.invalid/jobs/AB-12"},
                      {"platform": ["testplat"], "jobUrl": "x"},
                      {"platform": {"id": "testplat"}},
                      {"platform": "testplat", "jobUrl": 123},
                      {"platform": "testplat", "jobUrl": []}):
            with self.subTest(event=event):
                code, out = invoke(ledger, ws, "append", "--json", json.dumps(event))
                self.assertEqual(code, 2, out)
                self.assertIn("error", out)
                self.assertFalse(ledger.events_path(ws).exists())

    def test_append_and_check_pass_platform_through_the_actual_data_path(self):
        ws = self.base / "workspace"
        workspace.init_workspace(ws)
        marker = ledger.read_json(ws / ledger.MARKER)
        marker["accounts"]["testplat"] = {"accountKey": "test-main", "displayName": "合成账号"}
        ledger.write_atomic(ws / ledger.MARKER, json.dumps(marker))
        self.assertEqual(invoke(ledger, ws, "lock", "acquire", "--skill", "apply", "--run", "synthetic")[0], 0)
        url = "https://jobs.example.invalid/jobs/AB-12?keep=yes#section"
        ev = {"platform": "testplat", "runId": "synthetic", "skill": "apply", "kind": "greet",
              "status": "pending", "company": "合成公司", "title": "合成岗位", "reason": "离线合成测试", "jobUrl": url}
        code, out = invoke(ledger, ws, "append", "--json", json.dumps(ev))
        self.assertEqual(code, 0, out)
        self.assertEqual((out["event"]["jobId"], out["event"]["jobUrl"]), ("AB-12", url))
        code, out = invoke(ledger, ws, "check", "--platform", "testplat", "--company", "另一公司", "--scope", "job", "--job-url", url)
        self.assertEqual(code, 1, out)
        self.assertEqual(out["jobId"], "AB-12")
        self.assertIn("same_job", [r["type"] for r in out["reasons"]])
        before = ledger.events_path(ws).read_bytes()
        code, out = invoke(ledger, ws, "append", "--json", json.dumps(dict(ev, platform="missing")))
        self.assertEqual(code, 2, out)
        self.assertIn("未知平台", out["error"])
        self.assertEqual(ledger.events_path(ws).read_bytes(), before)
        code, out = invoke(ledger, ws, "check", "--platform", "missing", "--company", "合成", "--kind", "reply")
        self.assertEqual(code, 2, out)
        self.assertIn("未知平台", out["error"])

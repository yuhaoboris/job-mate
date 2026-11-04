"""T4.5 表格导出与只读安全出口；无真实数据或表格应用执行。"""
import csv
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_derivation

ledger = test_derivation.ledger


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.x = test_derivation.DerivationTest()
        self.x.setUp()
        self.f = self.x.f
        self.tmp = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()
        self.x.tearDown()

    def export(self, table="events", format="jsonl", *args):
        return self.f.run_cli("export", "--table", table, "--format", format, *args)

    def rows(self, table="events", *args):
        code, out = self.export(table, "jsonl", *args)
        self.assertEqual(code, 0, out)
        return [json.loads(line) for line in out["content"].split("\n") if line]

    def test_empty_tables_have_stable_csv_headers_bom_crlf_and_empty_jsonl(self):
        ledger.write_atomic(ledger.events_path(self.f.ws), "")
        for table in ("events", "threads", "jobs"):
            code, out = self.export(table, "csv")
            self.assertEqual(code, 0, out)
            self.assertEqual(out["count"], 0)
            self.assertTrue(out["content"].startswith("\ufeff"))
            self.assertTrue(out["content"].endswith("\r\n"))
            self.assertEqual(list(csv.reader(io.StringIO(out["content"][1:]))), [out["columns"]])
            code, empty = self.export(table)
            self.assertEqual(code, 0, empty)
            self.assertEqual(empty["content"], "")

    def test_events_preserve_unicode_approved_text_and_nested_json(self):
        text = " \n批准Ａ\u2028逐字\u2029保留\u0085\r\n "
        self.x.decision(steps=[{"step": "s1", "kind": "reply", "text": text}])
        rows = self.rows()
        self.assertEqual(rows[-1]["steps"][0]["text"], text)
        self.assertNotIn("_line", rows[-1])
        code, out = self.export("events", "csv")
        self.assertEqual(code, 0, out)
        parsed = list(csv.DictReader(io.StringIO(out["content"][1:], newline="")))
        self.assertEqual(json.loads(parsed[-1]["steps"])[0]["text"], text)
        self.assertEqual(parsed[-1]["account"], "boss-main")

    def test_jobs_latest_screen_and_contact_attempt_are_not_confused_with_sent(self):
        self.x.event(kind="screen", jobId="job-X", result="qualified", reasons=["旧结论"], jdFacts={"hours.schedule": "双休"})
        self.x.action(kind="greet", jobId="job-X", final="failed_no_send")
        self.x.event(kind="screen", jobId="job-X", result="excluded", reasons=["新结论"])
        job = next(row for row in self.rows("jobs") if row["jobKey"] == "boss|job-X")
        self.assertEqual(job["result"], "excluded")
        self.assertEqual(job["reasons"], ["新结论"])
        self.assertTrue(job["hasContactAttempt"])
        self.assertFalse(job["contacted"])
        self.x.action(kind="reply", jobId="job-X")
        self.assertTrue(next(row for row in self.rows("jobs") if row["jobKey"] == "boss|job-X")["contacted"])

    def test_jobs_and_threads_are_isolated_by_account(self):
        self.x.event(kind="screen", jobId="same-job", result="excluded", reasons=["旧账号"])
        meta = ledger.read_json(self.f.ws / ledger.MARKER)
        meta["accounts"]["boss"]["accountKey"] = "second"
        ledger.write_atomic(self.f.ws / ledger.MARKER, json.dumps(meta))
        self.x.event(kind="screen", jobId="same-job", result="qualified", reasons=["新账号"])
        jobs = {row["account"]: row for row in self.rows("jobs") if row["jobKey"] == "boss|same-job"}
        self.assertEqual(jobs["boss-main"]["result"], "excluded")
        self.assertEqual(jobs["second"]["result"], "qualified")
        self.assertEqual({row["account"] for row in self.rows("threads")}, {"boss-main", "second"})
        self.assertEqual({row["account"] for row in self.rows("events", "--account", "boss-main")}, {"boss-main"})

    def test_date_filters_are_inclusive_workspace_dates_for_all_tables(self):
        os.environ["JOB_MATE_NOW"] = "2026-11-05T00:30:00+08:00"
        self.x.event(kind="observe", observed="resume_viewed", company="次日公司", at="2026-11-04T16:30:00+00:00")
        for table in ("events", "threads", "jobs"):
            rows = self.rows(table, "--since", "2026-11-05", "--until", "2026-11-05")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["company"], "次日公司")
            self.assertEqual(len(self.rows(table, "--until", "2026-11-04")), 1)
        for args in (("--since", "bad"), ("--since", "2026-11-05", "--until", "2026-11-04")):
            self.assertEqual(self.export("events", "csv", *args)[0], 2)

    def test_filtering_threads_does_not_discard_old_state(self):
        self.x.event(kind="observe", observed="info", facts={"hours.schedule": "双休"})
        first = self.f.ok_tail()
        verified = self.f.ok_tail(base=first["tailId"])
        self.assertEqual(self.f.handled(verified)[0], 0)
        os.environ["JOB_MATE_NOW"] = "2026-11-05T12:00:00+08:00"
        self.x.event(kind="observe", observed="resume_viewed", title=None)
        row = self.rows("threads", "--since", "2026-11-05")[0]
        self.assertEqual(row["info"]["known"], {"hours.schedule": "双休"})
        self.assertEqual(row["lastHandled"]["tailId"], verified["tailId"])
        self.assertIsNone(row["pendingTail"])

    def test_csv_formula_prefix_is_display_only_jsonl_and_ledger_stay_exact(self):
        for value in ("=1+2", "+SUM(1)", "-1+2", "@SUM(1)", "\t=1+2", " \r=1", "＝1+2", "\u2028=1", "\u200b@SUM(1)"):
            self.x.event(kind="observe", observed="resume_viewed", reason=value)
        before = ledger.events_path(self.f.ws).read_bytes()
        values = [row["reason"] for row in self.rows()]
        code, out = self.export("events", "csv")
        self.assertEqual(code, 0, out)
        rows = list(csv.DictReader(io.StringIO(out["content"][1:], newline="")))
        self.assertEqual([row["reason"] for row in rows[1:]], ["'" + value for value in values[1:]])
        self.assertEqual(ledger.events_path(self.f.ws).read_bytes(), before)

    def test_out_is_atomic_new_file_with_bom_and_no_content_on_stdout(self):
        target = self.outdir / "export.csv"
        code, out = self.export("events", "csv", "--out", str(target))
        self.assertEqual(code, 0, out)
        self.assertEqual(out["out"], str(target))
        self.assertNotIn("content", out)
        self.assertTrue(target.read_bytes().startswith(b"\xef\xbb\xbf"))
        before = target.read_bytes()
        self.assertEqual(self.export("events", "csv", "--out", str(target))[0], 2)
        self.assertEqual(target.read_bytes(), before)

    def test_workspaces_and_suite_are_protected_even_under_recovery_lock(self):
        self.assertEqual(self.f.run_cli("lock", "release", "--run", "run-tail")[0], 0)
        self.assertEqual(self.f.run_cli("lock", "acquire", "--skill", "setup", "--recovery", "--run", "recover")[0], 0)
        before = ledger.events_path(self.f.ws).read_bytes()
        for target in (ledger.events_path(self.f.ws), self.f.ws / "profile.json", self.f.ws / "job-mate.json",
                       self.f.ws / "new-export.csv", test_derivation.test_tail.ROOT / "new-export.csv"):
            code, out = self.export("events", "csv", "--out", str(target))
            self.assertEqual(code, 2, out)
        other = self.outdir / "other-workspace"
        other.mkdir()
        (other / ledger.MARKER).write_text("{}", encoding="utf-8")
        self.assertEqual(self.export("events", "csv", "--out", str(other / "new.csv"))[0], 2)
        target = self.outdir / "permitted.jsonl"
        self.assertEqual(self.export("events", "jsonl", "--out", str(target))[0], 0)
        self.assertEqual(ledger.events_path(self.f.ws).read_bytes(), before)
        self.assertEqual(ledger.read_lock(self.f.ws)["mode"], "recovery")

    def test_symlink_and_hardlink_aliases_do_not_overwrite_source(self):
        source = ledger.events_path(self.f.ws)
        before = source.read_bytes()
        hard = self.outdir / "hard.csv"
        os.link(source, hard)
        self.assertEqual(self.export("events", "csv", "--out", str(hard))[0], 2)
        symbolic = self.outdir / "symbolic.csv"
        try:
            symbolic.symlink_to(source)
        except (OSError, NotImplementedError):
            self.skipTest("当前环境没有符号链接权限；硬链接分支已执行")
        self.assertEqual(self.export("events", "csv", "--out", str(symbolic))[0], 2)
        directory = self.outdir / "directory"
        directory.symlink_to(self.f.ws, target_is_directory=True)
        self.assertEqual(self.export("events", "csv", "--out", str(directory / "new.csv"))[0], 2)
        self.assertEqual(source.read_bytes(), before)

    def test_publish_failure_or_racing_destination_never_leaves_half_output(self):
        target = self.outdir / "result.jsonl"
        with patch.object(ledger.fsutil.os, "link", side_effect=OSError("synthetic publish failure")):
            self.assertEqual(self.export("events", "jsonl", "--out", str(target))[0], 2)
        self.assertEqual(list(self.outdir.iterdir()), [])
        original = os.link
        def race(source, destination):
            Path(destination).write_text("racing writer", encoding="utf-8")
            return original(source, destination)
        with patch.object(ledger.fsutil.os, "link", side_effect=race):
            self.assertEqual(self.export("events", "jsonl", "--out", str(target))[0], 2)
        self.assertEqual(target.read_text(encoding="utf-8"), "racing writer")
        self.assertEqual(list(self.outdir.iterdir()), [target])

    def test_unknown_event_fields_are_preserved_without_external_csv_headers(self):
        self.x.event(kind="observe", observed="replied", extra={"original": "=payload"}, **{"=bad-header": "raw"})
        code, out = self.export("events", "csv")
        self.assertEqual(code, 0, out)
        parsed = list(csv.DictReader(io.StringIO(out["content"][1:], newline="")))[-1]
        self.assertNotIn("=bad-header", parsed)
        self.assertEqual(json.loads(parsed["extra"]), {"extra": {"original": "=payload"}, "=bad-header": "raw"})
        self.assertEqual(self.rows()[-1]["extra"], {"original": "=payload"})

    def test_fsync_failure_and_corrupt_input_leave_no_output(self):
        target = self.outdir / "export.jsonl"
        with patch.object(ledger.fsutil.os, "fsync", side_effect=OSError("synthetic fsync failure")):
            self.assertEqual(self.export("events", "jsonl", "--out", str(target))[0], 2)
        self.assertEqual(list(self.outdir.iterdir()), [])
        ledger.append_line(self.f.ws, {"schemaVersion": 1})
        before = ledger.events_path(self.f.ws).read_bytes()
        self.assertEqual(self.export("events", "jsonl", "--out", str(target))[0], 2)
        self.assertFalse(target.exists())
        self.assertEqual(ledger.events_path(self.f.ws).read_bytes(), before)

    def test_error_keeps_version_warning_and_missing_parent_is_not_created(self):
        with patch.object(ledger, "SUITE_VERSION", "0.9.0"):
            code, out = self.export("events", "csv", "--out", str(self.f.ws / "denied.csv"))
            self.assertEqual(code, 2, out)
            self.assertIn("suite_older_than_workspace", out["warnings"])
        target = self.outdir / "absent" / "file.csv"
        self.assertEqual(self.export("events", "csv", "--out", str(target))[0], 2)
        self.assertFalse(target.parent.exists())

    def test_output_basename_cannot_target_a_windows_alternate_stream(self):
        target = self.outdir / "export.csv:stream"
        self.assertEqual(self.export("events", "csv", "--out", str(target))[0], 2)
        self.assertEqual(list(self.outdir.iterdir()), [])

    def test_recent_job_activity_keeps_the_older_latest_screen(self):
        self.x.event(kind="screen", result="qualified", reasons=["已评估"], jobId="old-screen", jdFacts={"hours.schedule": "双休"})
        os.environ["JOB_MATE_NOW"] = "2026-11-05T12:00:00+08:00"
        self.x.event(kind="observe", observed="replied", jobId="old-screen")
        row = self.rows("jobs", "--since", "2026-11-05")[0]
        self.assertEqual(row["result"], "qualified")
        self.assertEqual(row["jdFacts"], {"hours.schedule": "双休"})
        self.assertTrue(row["screenAt"].startswith("2026-11-04"))

    def test_jsonl_file_has_no_bom_and_retains_original_text(self):
        text = "=合成原文\u2028不改写"
        self.x.event(kind="observe", observed="replied", reason=text)
        target = self.outdir / "events.jsonl"
        code, out = self.export("events", "jsonl", "--out", str(target))
        self.assertEqual(code, 0, out)
        self.assertFalse(target.read_bytes().startswith(b"\xef\xbb\xbf"))
        rows = [json.loads(line) for line in target.read_text(encoding="utf-8").split("\n") if line]
        self.assertEqual(rows[-1]["reason"], text)

    def test_stdout_json_warning_contract_and_read_only_classification(self):
        before = ledger.events_path(self.f.ws).read_bytes()
        with patch.object(ledger, "SUITE_VERSION", "0.9.0"):
            code, out = self.export("events", "csv")
            self.assertEqual(code, 0, out)
            self.assertIn("suite_older_than_workspace", out["warnings"])
            self.assertTrue(out["content"].startswith("\ufeff"))
        args = ledger.build_parser().parse_args(["export", "--table", "events", "--format", "csv", "--out", "x.csv"])
        self.assertEqual(ledger.entry_mode(args), "read")
        self.assertEqual(ledger.events_path(self.f.ws).read_bytes(), before)

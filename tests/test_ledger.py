import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import ledger  # noqa: E402

TZ = ledger.fsutil.tz_from_name("Asia/Shanghai")


class Workspace:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        (self.path / "job-mate.json").write_text(json.dumps({
            "schemaVersion": 1, "suiteVersion": "1.0.0", "accounts": {"boss": {"accountKey": "zhipin-test"}},
        }))
        (self.path / "profile.json").write_text(json.dumps({"schemaVersion": 2}))

    def run(self, *argv, stdin=None):
        out = io.StringIO()
        old_stdin = sys.stdin
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out):
                code = ledger.main(["--workspace", str(self.path), *argv])
        finally:
            sys.stdin = old_stdin
        return code, json.loads(out.getvalue())

    def ensure_lock(self, run):
        """测试便利：让 run 成为当前持锁者（必要时释放其他测试 run 的锁）。"""
        _, st = self.run("lock", "status")
        lock = st.get("lock")
        if lock and lock["runId"] == run and st["held"]:
            return
        if lock:
            self.run("lock", "release", "--run", lock["runId"])
        code, out = self.run("lock", "acquire", "--skill", "apply", "--run", run, "--ttl", "600")
        assert code == 0, out

    def expire_lock(self):
        p = self.path / "ledger" / "lock.json"
        lock = json.loads(p.read_text())
        lock["expiresAt"] = (datetime.now(TZ) - timedelta(minutes=1)).isoformat(timespec="seconds")
        p.write_text(json.dumps(lock))

    def append(self, lock=True, **ev):
        base = {"platform": "boss", "runId": "run-1", "skill": "apply", "kind": "greet",
                "company": "示例科技", "title": "前端开发", "location": "东莞",
                "reason": "测试", "evidence": None}
        base.update(ev)
        if lock and base.get("status") == "pending":
            self.ensure_lock(base["runId"])
        return self.run("append", "--json", json.dumps(base, ensure_ascii=False))

    def greet(self, final="sent", **ev):
        code, out = self.append(status="pending", **ev)
        assert code == 0, out
        aid = out["event"]["attemptId"]
        if final:
            code, out = self.append(status=final, attemptId=aid, **ev)
            assert code == 0, out
        return aid

    def close(self):
        self.tmp.cleanup()


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()

    def tearDown(self):
        self.ws.close()

    def test_append_fills_defaults_and_normalizes_url(self):
        code, out = self.ws.append(
            status="pending",
            jobUrl="https://www.zhipin.com/job_detail/ebcc1666b6c4938d03Zy0928FldR.html?lid=x&securityId=y")
        self.assertEqual(code, 0)
        ev = out["event"]
        self.assertEqual(ev["account"], "zhipin-test")
        self.assertEqual(ev["jobId"], "ebcc1666b6c4938d03Zy0928FldR")
        self.assertEqual(ev["jobUrl"], "https://www.zhipin.com/job_detail/ebcc1666b6c4938d03Zy0928FldR.html")
        self.assertTrue(ev["attemptId"].startswith("att-"))

    def test_terminal_without_pending_rejected(self):
        code, out = self.ws.append(status="sent", attemptId="att-nope")
        self.assertEqual(code, 2)
        self.assertIn("pending", out["error"])

    def test_check_blocks_same_company_by_default(self):
        self.ws.greet(jobId="A1")
        code, out = self.ws.run("check", "--platform", "boss", "--company", "示例科技", "--job-id", "B2")
        self.assertEqual(code, 1)
        self.assertEqual(out["reasons"][0]["type"], "same_company")

    def test_check_job_scope_allows_other_job_but_flags_repost(self):
        self.ws.greet(jobId="A1")
        code, _ = self.ws.run("check", "--platform", "boss", "--company", "示例科技",
                              "--title", "后端开发", "--job-id", "B2", "--scope", "job")
        self.assertEqual(code, 0)
        code, out = self.ws.run("check", "--platform", "boss", "--company", "示例科技",
                                "--title", "前端开发", "--job-id", "B2", "--scope", "job")
        self.assertEqual(code, 1)
        self.assertEqual(out["reasons"][0]["type"], "reposted_suspect")

    def test_pending_and_uncertain_block_skipped_does_not(self):
        self.ws.greet(final=None, company="甲公司")
        self.ws.greet(final="uncertain", company="乙公司")
        self.assertEqual(self.ws.append(status="skipped", company="丙公司")[0], 0)
        self.assertEqual(self.ws.run("check", "--platform", "boss", "--company", "甲公司")[0], 1)
        self.assertEqual(self.ws.run("check", "--platform", "boss", "--company", "乙公司")[0], 1)
        self.assertEqual(self.ws.run("check", "--platform", "boss", "--company", "丙公司")[0], 0)

    def test_manual_review_can_clear_uncertain_but_not_sent(self):
        aid = self.ws.greet(final="uncertain", company="乙公司")
        code, out = self.ws.append(status="failed_no_send", attemptId=aid, company="乙公司")
        self.assertEqual(code, 2)
        self.assertIn("本人核对", out["error"])
        self.assertEqual(self.ws.append(status="failed_no_send", attemptId=aid, company="乙公司", skill="user")[0], 0)
        self.assertEqual(self.ws.run("check", "--platform", "boss", "--company", "乙公司", "--run", "run-2")[0], 0)
        aid = self.ws.greet(final="sent", company="丁公司")
        self.assertEqual(self.ws.append(status="failed_no_send", attemptId=aid, company="丁公司", skill="user")[0], 2)
        self.assertEqual(self.ws.run("check", "--platform", "boss", "--company", "丁公司")[0], 1)

    def test_invalid_transitions_rejected(self):
        aid = self.ws.greet(final=None, company="状态样例")
        for status in ("skipped", "already_contacted", "pending"):
            code, out = self.ws.append(status=status, attemptId=aid, company="状态样例", lock=False)
            self.assertEqual(code, 2, status)
        code, out = self.ws.append(status="sent", attemptId=aid, company="别的公司")
        self.assertEqual(code, 2)
        self.assertIn("company", out["error"])
        self.assertEqual(self.ws.run("check", "--platform", "boss", "--company", "状态样例")[0], 1)
        self.assertEqual(self.ws.run("quota", "--platform", "boss")[1]["greetsToday"], 1)

    def test_failed_no_send_blocks_only_same_run(self):
        self.ws.greet(final="failed_no_send", company="戊公司")
        self.assertEqual(self.ws.run("check", "--platform", "boss", "--company", "戊公司", "--run", "run-1")[0], 1)
        self.assertEqual(self.ws.run("check", "--platform", "boss", "--company", "戊公司", "--run", "run-2")[0], 0)

    def test_stoplist_exact_alias_and_suspect(self):
        code, _ = self.ws.run("stoplist", "add", "--name", "软通动力信息技术(集团)股份有限公司",
                              "--alias", "软通动力", "--alias", "iSoftStone",
                              "--reason", "人员外包", "--source", "本人")
        self.assertEqual(code, 0)
        code, out = self.ws.run("check", "--platform", "boss", "--company", "ISOFTSTONE")
        self.assertEqual(out["reasons"][0]["type"], "stoplist")
        code, out = self.ws.run("check", "--platform", "boss", "--company", "软通动力东莞分公司")
        self.assertEqual(code, 1)
        self.assertEqual(out["reasons"][0]["type"], "stoplist_suspect")
        code, out = self.ws.run("stoplist", "add", "--name", "软通动力", "--reason", "重复", "--source", "本人")
        self.assertFalse(out["added"])

    def test_bad_stoplist_line_reported(self):
        (self.ws.path / "stoplist.yaml").write_text("- name: 甲\n  aliases: 乙\n", encoding="utf-8")
        code, out = self.ws.run("validate")
        self.assertEqual(code, 2)
        self.assertIn("aliases", out["stoplist"]["error"])

    def test_corrupt_line_blocks_check(self):
        self.ws.greet()
        with (self.ws.path / "ledger" / "events.jsonl").open("a") as f:
            f.write("{broken\n")
        code, out = self.ws.run("validate")
        self.assertEqual(code, 2)
        self.assertEqual(out["errors"][0]["line"], 3)
        code, out = self.ws.run("check", "--platform", "boss", "--company", "别的公司")
        self.assertEqual(code, 2)

    def test_quota_counts_attempts_not_successes(self):
        (self.ws.path / "profile.json").write_text(json.dumps(
            {"limits": {"greetsPerRun": 2, "greetsPerDay": 3, "minSendIntervalSeconds": 0}}))
        self.ws.greet(company="甲")
        self.ws.greet(final="uncertain", company="乙")
        code, out = self.ws.run("quota", "--platform", "boss", "--run", "run-1")
        self.assertEqual(code, 1)
        self.assertEqual(out["greetsToday"], 2)
        self.assertEqual(out["remainingThisRun"], 0)
        code, out = self.ws.run("quota", "--platform", "boss", "--run", "run-2")
        self.assertEqual(code, 0)
        self.assertEqual(out["remainingToday"], 1)

    def test_quota_ignores_yesterday(self):
        yesterday = (datetime.now(TZ) - timedelta(days=1)).isoformat(timespec="seconds")
        self.ws.greet(final=None, at=yesterday)
        _, out = self.ws.run("quota", "--platform", "boss")
        self.assertEqual(out["greetsToday"], 0)

    def test_lock_exclusive_release_and_expired_takeover(self):
        code, out = self.ws.run("lock", "acquire", "--skill", "apply")
        self.assertEqual(code, 0)
        run1 = out["lock"]["runId"]
        self.assertEqual(self.ws.run("lock", "acquire", "--skill", "followup")[0], 1)
        self.assertEqual(self.ws.run("lock", "release", "--run", "other")[0], 1)
        self.assertEqual(self.ws.run("lock", "release", "--run", run1)[0], 0)

        code, out = self.ws.run("lock", "acquire", "--skill", "apply")
        run2 = out["lock"]["runId"]
        self.ws.greet(final=None, runId=run2, company="中断公司")
        self.ws.expire_lock()
        code, out = self.ws.run("lock", "acquire", "--skill", "followup")
        self.assertEqual(code, 0)
        self.assertEqual(out["tookOverFrom"]["runId"], run2)
        self.assertEqual(out["reconciled"][0]["company"], "中断公司")
        _, chk = self.ws.run("check", "--platform", "boss", "--company", "中断公司")
        self.assertIn("uncertain", chk["reasons"][0]["detail"])

    def test_release_reconciles_pending_before_next_acquire(self):
        aid = self.ws.greet(final=None, company="释放样例")
        code, out = self.ws.run("lock", "release", "--run", "run-1")
        self.assertEqual(code, 0, out)
        self.assertEqual([a["attemptId"] for a in out.get("reconciled", [])], [aid])
        code, out = self.ws.run("lock", "acquire", "--skill", "followup", "--run", "next")
        self.assertEqual(code, 0, out)
        attempts = ledger.attempts_of(ledger.read_events(self.ws.path)[0])
        self.assertEqual(attempts[aid]["status"], "uncertain")
        self.assertEqual(self.ws.run("guard", "--run", "next", "--attempt", aid)[0], 1)

    def test_release_keeps_terminal_states_and_rejects_other_owner(self):
        for state in ("sent", "uncertain", "failed_no_send", "skipped", "already_contacted"):
            if state in ("skipped", "already_contacted"):
                self.ws.append(status=state, company=state)
            else:
                self.ws.greet(final=state, company=state)
        aid = self.ws.greet(final=None, company="仍待定")
        before = ledger.events_path(self.ws.path).read_bytes()
        lock_before = ledger.lock_path(self.ws.path).read_bytes()
        code, out = self.ws.run("lock", "release", "--run", "other")
        self.assertEqual(code, 1, out)
        self.assertEqual(ledger.events_path(self.ws.path).read_bytes(), before)
        self.assertEqual(ledger.lock_path(self.ws.path).read_bytes(), lock_before)
        code, out = self.ws.run("lock", "release", "--run", "run-1")
        self.assertEqual(code, 0, out)
        self.assertEqual([a["attemptId"] for a in out["reconciled"]], [aid])
        attempts = ledger.attempts_of(ledger.read_events(self.ws.path)[0])
        for a in attempts.values():
            if a["attemptId"] != aid:
                self.assertEqual(a["status"], a["company"])
        self.assertTrue(ledger.events_path(self.ws.path).read_bytes().startswith(before))

    def test_release_all_terminal_returns_empty_reconciled(self):
        self.ws.greet()
        before = ledger.events_path(self.ws.path).read_bytes()
        code, out = self.ws.run("lock", "release", "--run", "run-1")
        self.assertEqual((code, out["released"], out["reconciled"]), (0, True, []))
        self.assertEqual(ledger.events_path(self.ws.path).read_bytes(), before)
        self.assertFalse(self.ws.run("lock", "release", "--run", "run-1")[1]["released"])

    def test_release_does_not_reconcile_another_runs_pending(self):
        aid = self.ws.greet(final=None, company="其他轮次")
        # 合成旧版遗留：当前锁属于另一轮，但旧 pending 没有被核销。
        with ledger.lock_mutex(self.ws.path):
            lock = ledger.read_lock(self.ws.path)
            lock["runId"] = "current"
            ledger.write_lock(self.ws.path, lock)
        code, out = self.ws.run("lock", "release", "--run", "current")
        self.assertEqual((code, out["reconciled"]), (0, []))
        self.assertEqual(ledger.attempts_of(ledger.read_events(self.ws.path)[0])[aid]["status"], "pending")

    def fail_partial_release(self):
        ids = [self.ws.greet(final=None, company=c) for c in ("收尾甲", "收尾乙")]
        original = ledger.append_line
        calls = []

        def fail_second(ws, ev):
            calls.append(ev)
            if len(calls) == 2:
                raise OSError("合成写入失败")
            original(ws, ev)

        with patch.object(ledger, "append_line", side_effect=fail_second):
            code, out = self.ws.run("lock", "release", "--run", "run-1")
        self.assertEqual(code, 2, out)
        self.assertIn("收尾失败", out["error"])
        self.assertTrue(ledger.read_lock(self.ws.path)["releasing"])
        self.assertEqual(self.ws.run("guard", "--run", "run-1", "--attempt", ids[1])[0], 2)
        self.assertEqual(self.ws.append(status="pending", company="禁止继续", lock=False)[0], 2)
        self.assertEqual(self.ws.run("lock", "renew", "--run", "run-1")[0], 1)
        self.assertEqual(self.ws.run("lock", "acquire", "--skill", "apply", "--run", "run-1")[0], 1)
        self.assertEqual(self.ws.run("lock", "acquire", "--skill", "followup", "--run", "next")[0], 1)
        return ids

    def test_release_partial_failure_is_closed_and_retry_reports_all(self):
        ids = self.fail_partial_release()
        code, out = self.ws.run("lock", "release", "--run", "run-1")
        self.assertEqual((code, out["released"]), (0, True))
        self.assertEqual([a["attemptId"] for a in out["reconciled"]], ids)
        attempts = ledger.attempts_of(ledger.read_events(self.ws.path)[0])
        for aid in ids:
            self.assertEqual(attempts[aid]["status"], "uncertain")
            self.assertEqual(len(attempts[aid]["events"]), 2)

    def test_expired_takeover_recovers_partial_release(self):
        ids = self.fail_partial_release()
        self.ws.expire_lock()
        code, out = self.ws.run("lock", "acquire", "--skill", "followup", "--run", "next")
        self.assertEqual(code, 0, out)
        self.assertEqual([a["attemptId"] for a in out["reconciled"]], ids)
        self.assertEqual(out["tookOverFrom"]["runId"], "run-1")
        self.assertEqual(self.ws.run("lock", "release", "--run", "run-1")[0], 1)
        self.assertEqual(ledger.read_lock(self.ws.path)["runId"], "next")

    def test_release_retry_keeps_user_verified_terminal_and_reports_only_uncertain(self):
        ids = self.fail_partial_release()
        self.assertEqual(self.ws.append(status="sent", attemptId=ids[0], company="收尾甲",
                                        skill="user", lock=False)[0], 0)
        code, out = self.ws.run("lock", "release", "--run", "run-1")
        self.assertEqual(code, 0, out)
        self.assertEqual([a["attemptId"] for a in out["reconciled"]], [ids[1]])
        self.assertEqual(ledger.attempts_of(ledger.read_events(self.ws.path)[0])[ids[0]]["status"], "sent")

    def test_release_unlink_failure_keeps_recoverable_report(self):
        aid = self.ws.greet(final=None)
        with patch.object(Path, "unlink", side_effect=OSError("合成删除锁失败")):
            code, out = self.ws.run("lock", "release", "--run", "run-1")
        self.assertEqual(code, 2, out)
        self.assertTrue(ledger.read_lock(self.ws.path)["releasing"])
        code, out = self.ws.run("lock", "release", "--run", "run-1")
        self.assertEqual(code, 0, out)
        self.assertEqual([a["attemptId"] for a in out["reconciled"]], [aid])
        self.assertEqual(len(ledger.attempts_of(ledger.read_events(self.ws.path)[0])[aid]["events"]), 2)

    def test_release_corrupt_ledger_keeps_lock(self):
        self.ws.greet(final=None)
        with ledger.events_path(self.ws.path).open("a") as f:
            f.write("{broken\n")
        code, out = self.ws.run("lock", "release", "--run", "run-1")
        self.assertEqual(code, 2, out)
        self.assertIn("损坏", out["error"])
        self.assertEqual(ledger.read_lock(self.ws.path)["runId"], "run-1")

    def test_concurrent_release_reconciles_once(self):
        aid = self.ws.greet(final=None)
        cmd = [sys.executable, "-I", str(SCRIPTS / "ledger.py"), "--workspace", str(self.ws.path),
               "lock", "release", "--run", "run-1"]
        procs = [subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        results = [json.loads(p.communicate(timeout=10)[0]) for p in procs]
        self.assertEqual(sum(r["released"] for r in results), 1, results)
        winner = next(r for r in results if r["released"])
        self.assertEqual([a["attemptId"] for a in winner["reconciled"]], [aid])
        self.assertEqual(len(ledger.attempts_of(ledger.read_events(self.ws.path)[0])[aid]["events"]), 2)

    def test_release_and_acquire_race_never_leave_pending_or_delete_new_lock(self):
        aid = self.ws.greet(final=None)
        self.ws.expire_lock()
        cmd = [sys.executable, "-I", str(SCRIPTS / "ledger.py"), "--workspace", str(self.ws.path), "lock"]
        procs = [subprocess.Popen(cmd + argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for argv in (["release", "--run", "run-1"],
                              ["acquire", "--skill", "followup", "--run", "next"])]
        results = [json.loads(p.communicate(timeout=10)[0]) for p in procs]
        self.assertTrue(results[1]["acquired"], results)
        self.assertEqual(ledger.read_lock(self.ws.path)["runId"], "next")
        attempt = ledger.attempts_of(ledger.read_events(self.ws.path)[0])[aid]
        self.assertEqual(attempt["status"], "uncertain")
        self.assertEqual(len(attempt["events"]), 2)

    def test_setup_new_draft_blocks_apply_followup_but_not_setup(self):
        """判据 7：新建草稿阻止获取/沿用锁；清除后恢复正常。"""
        path = self.ws.path / "setup-progress.json"
        self.ws.run("lock", "acquire", "--skill", "apply", "--run", "old")
        path.write_text(json.dumps({"mode": "new"}))
        for skill in ("apply", "followup"):
            code, out = self.ws.run("lock", "acquire", "--skill", skill, "--run", "old")
            self.assertEqual(code, 1, out)
            self.assertFalse(out["acquired"])
            self.assertIn("先完成 setup", out["reason"])
        self.ws.run("lock", "release", "--run", "old")
        code, out = self.ws.run("lock", "acquire", "--skill", "setup", "--run", "setup-test")
        self.assertEqual(code, 0, out)
        self.ws.run("lock", "release", "--run", "setup-test")
        path.unlink()
        for skill in ("apply", "followup"):
            code, out = self.ws.run("lock", "acquire", "--skill", skill, "--run", "restored")
            self.assertEqual(code, 0, out)
            self.ws.run("lock", "release", "--run", "restored")

    def test_setup_unreadable_draft_fails_closed(self):
        """判据 7：只读取 mode；读不出时按新建阻断，不影响 setup。"""
        path = self.ws.path / "setup-progress.json"
        for broken in ('{broken', '[]', '{}', '{"mode": []}', '{"mode": "unknown"}'):
            path.write_text(broken)
            before = path.read_bytes()
            for skill in ("apply", "followup"):
                self.assertEqual(self.ws.run("lock", "acquire", "--skill", skill)[0], 1)
            self.assertFalse((self.ws.path / "ledger/lock.json").exists())
            code, out = self.ws.run("lock", "acquire", "--skill", "setup", "--run", "repair")
            self.assertEqual(code, 0, out)
            self.ws.run("lock", "release", "--run", "repair")
            self.assertEqual(path.read_bytes(), before)

    def test_setup_reset_drafts_do_not_block_runtime_lock(self):
        """判据 7：重新设置期间旧配置仍可用，ledger 只读取 mode。"""
        path = self.ws.path / "setup-progress.json"
        for mode in ("resetup", "partial"):
            path.write_text(json.dumps({"mode": mode}))
            for skill in ("apply", "followup"):
                code, out = self.ws.run("lock", "acquire", "--skill", skill, "--run", "live")
                self.assertEqual(code, 0, out)
                self.ws.run("lock", "release", "--run", "live")

    def test_pending_requires_live_lock(self):
        self.assertEqual(self.ws.append(status="pending", lock=False)[0], 2)
        self.ws.ensure_lock("old")
        self.ws.expire_lock()
        code, out = self.ws.append(status="pending", runId="old", lock=False)
        self.assertEqual(code, 2)
        self.assertIn("过期", out["error"])
        self.assertEqual(self.ws.run("lock", "renew", "--run", "old")[0], 0)
        self.assertEqual(self.ws.append(status="pending", runId="old", lock=False)[0], 0)
        self.ws.expire_lock()
        self.assertEqual(self.ws.run("lock", "acquire", "--skill", "followup", "--run", "new")[0], 0)
        code, out = self.ws.append(status="pending", runId="old", company="失锁样例", lock=False)
        self.assertEqual(code, 2)
        self.assertIn("未持有", out["error"])
        self.assertEqual(self.ws.run("lock", "renew", "--run", "old")[0], 1)

    def test_pending_renews_lock(self):
        self.ws.ensure_lock("run-1")
        p = self.ws.path / "ledger" / "lock.json"
        lock = json.loads(p.read_text())
        lock["expiresAt"] = (datetime.now(TZ) + timedelta(seconds=30)).isoformat(timespec="seconds")
        p.write_text(json.dumps(lock))
        self.ws.append(status="pending", lock=False)
        after = ledger.parse_at(json.loads(p.read_text())["expiresAt"])
        self.assertGreater(after, datetime.now(TZ) + timedelta(minutes=5))

    def test_no_send_lock_rejects_pending(self):
        self.assertEqual(self.ws.run("lock", "acquire", "--skill", "followup", "--run", "ro", "--no-send")[0], 0)
        code, out = self.ws.append(status="pending", runId="ro", lock=False)
        self.assertEqual(code, 2)
        self.assertIn("禁发", out["error"])
        self.assertEqual(self.ws.append(kind="observe", observed="replied", runId="ro", lock=False)[0], 0)

    def test_global_pause_blocks_pending_and_guard_until_resumed(self):
        self.ws.ensure_lock("run-p")
        _, out = self.ws.append(status="pending", runId="run-p", company="暂停样例", lock=False)
        aid = out["event"]["attemptId"]
        self.assertEqual(self.ws.run("pause", "on")[0], 2)  # 缺少原因
        code, out = self.ws.run("pause", "on", "--reason", "本人 IM 指令「暂停」", "--by", "im")
        self.assertEqual(code, 0)
        code, out = self.ws.run("guard", "--run", "run-p", "--attempt", aid)
        self.assertEqual(code, 2)
        self.assertIn("暂停", out["error"])
        code, out = self.ws.append(status="pending", runId="run-p", company="暂停样例二", lock=False)
        self.assertEqual(code, 2)
        self.assertIn("暂停", out["error"])
        # 终态和观察记录已经发生的事实，暂停时照常可写
        self.assertEqual(self.ws.append(status="uncertain", runId="run-p", attemptId=aid,
                                        company="暂停样例", lock=False)[0], 0)
        self.ws.run("lock", "release", "--run", "run-p")
        _, acq = self.ws.run("lock", "acquire", "--skill", "followup", "--run", "run-q")
        self.assertEqual(acq["paused"]["by"], "im")
        self.assertEqual(self.ws.run("pause", "off", "--by", "im")[0], 0)
        self.assertIsNone(self.ws.run("pause", "status")[1]["paused"])
        self.assertEqual(self.ws.append(status="pending", runId="run-q", company="暂停样例三", lock=False)[0], 0)

    def test_concurrent_takeover_only_one_wins(self):
        for round_ in range(5):
            self.ws.ensure_lock(f"stale-{round_}")
            self.ws.expire_lock()
            cmd = [sys.executable, "-I", str(SCRIPTS / "ledger.py"), "--workspace", str(self.ws.path),
                   "lock", "acquire", "--skill"]
            procs = [subprocess.Popen(cmd + [skill], stdout=subprocess.PIPE, text=True)
                     for skill in ("apply", "followup")]
            results = [json.loads(p.communicate()[0]) for p in procs]
            winners = [r for r in results if r.get("acquired")]
            self.assertEqual(len(winners), 1, results)
            _, st = self.ws.run("lock", "status")
            self.assertEqual(st["lock"]["runId"], winners[0]["lock"]["runId"])

    def test_first_contact_counts_other_kinds(self):
        self.ws.greet(final=None, kind="reply", skill="followup", company="回复样例", recruiter="王女士(HR)")
        code, out = self.ws.run("check", "--platform", "boss", "--company", "回复样例")
        self.assertEqual(code, 1)
        self.assertEqual(out["reasons"][0]["type"], "same_company")

    def test_conversation_check_blocks_unresolved_manual_paused(self):
        common = dict(company="会话样例", recruiter="李女士(HR)")
        chk = lambda *extra: self.ws.run("check", "--platform", "boss", "--company", "会话样例",
                                         "--recruiter", "李女士(HR)", "--kind", "reply", *extra)
        self.ws.greet(**common)
        self.assertEqual(chk()[0], 0)
        self.ws.append(kind="observe", observed="paused", decision="D-20261028-01", skill="followup", **common)
        code, out = chk()
        self.assertEqual(code, 1)
        self.assertEqual(out["reasons"][0]["type"], "paused")
        self.assertEqual(chk("--decision", "D-20261028-01")[0], 0)
        self.ws.append(kind="observe", observed="manual", skill="followup", **common)
        self.assertEqual(chk()[1]["reasons"][0]["type"], "manual")
        self.ws.append(kind="observe", observed="unpaused", decision="D-20261028-01", skill="followup", **common)
        self.ws.append(kind="observe", observed="released", skill="user", **common)
        self.assertEqual(chk()[0], 0)
        self.ws.greet(final=None, kind="reply", skill="followup", **common)
        self.assertEqual(chk()[1]["reasons"][0]["type"], "unresolved")

    def test_decision_state(self):
        common = dict(company="决策样例", recruiter="周先生(HR)", kind="reply", skill="followup",
                      decision="D-20261028-02", step="reply1")
        state = lambda: self.ws.run("decision", "--id", "D-20261028-02")[1]["state"]
        self.assertEqual(state(), "not_started")
        aid = self.ws.greet(final=None, **common)
        self.assertEqual(state(), "in_progress")
        self.ws.append(status="sent", attemptId=aid, **common)
        self.assertEqual(state(), "partial")
        self.ws.append(kind="observe", observed="unpaused", company="决策样例", recruiter="周先生(HR)",
                       skill="followup", decision="D-20261028-02")
        self.assertEqual(state(), "done")

    def test_resume_direct_delivery_closes_attempt(self):
        common = dict(company="简历样例", recruiter="陈女士(HR)", kind="resume", skill="followup")
        self.ws.greet(final="sent", **common)
        self.ws.append(kind="observe", observed="resume_delivered", company="简历样例",
                       recruiter="陈女士(HR)", skill="followup")
        t = self.ws.run("threads", "--company", "简历样例")[1]["threads"][0]
        self.assertEqual(t["unresolved"], [])

    def test_stoplist_roundtrip_special_values(self):
        name = 'ACME "Labs" \\ 东莞'
        aliases = ["Alpha,Beta", "it's", "[x]", "- dash"]
        args = ["stoplist", "add", "--name", name, "--reason", "原因: 含冒号", "--source", "测试"]
        for a in aliases:
            args += ["--alias", a]
        code, out = self.ws.run(*args)
        self.assertEqual(code, 0, out)
        entry = self.ws.run("stoplist", "list")[1]["entries"][0]
        self.assertEqual(entry["name"], name)
        self.assertEqual(entry["aliases"], aliases)
        self.assertEqual(entry["reason"], "原因: 含冒号")
        for company in [name, *aliases]:
            self.assertEqual(self.ws.run("check", "--platform", "boss", "--company", company)[0], 1, company)

    def test_stoplist_hand_written_quotes(self):
        (self.ws.path / "stoplist.yaml").write_text(
            "# 注释\n- name: '单''引号'\n  aliases: [\"甲,乙\", 丙 , '丁']\n  reason: 测试\n", encoding="utf-8")
        entry = self.ws.run("stoplist", "list")[1]["entries"][0]
        self.assertEqual(entry["name"], "单'引号")
        self.assertEqual(entry["aliases"], ["甲,乙", "丙", "丁"])

    def test_stoplist_remove_keeps_others(self):
        for n in ("甲公司", "乙公司", "丙公司"):
            self.ws.run("stoplist", "add", "--name", n, "--reason", "测试", "--source", "测试")
        code, out = self.ws.run("stoplist", "remove", "--name", "乙公司")
        self.assertEqual(code, 0)
        names = [e["name"] for e in self.ws.run("stoplist", "list")[1]["entries"]]
        self.assertEqual(names, ["甲公司", "丙公司"])
        self.assertEqual(self.ws.run("stoplist", "remove", "--name", "乙公司")[0], 1)

    def test_quota_interval_uses_latest_send(self):
        (self.ws.path / "profile.json").write_text(json.dumps({"limits": {"minSendIntervalSeconds": 5}}))
        earlier = (datetime.now(TZ) - timedelta(seconds=20)).isoformat(timespec="seconds")
        aid = self.ws.greet(final=None, at=earlier)
        self.ws.append(status="sent", attemptId=aid)
        out = self.ws.run("quota", "--platform", "boss")[1]
        self.assertGreater(out["waitSeconds"], 0)

    def test_same_job_id_blocks_even_with_different_company_name(self):
        self.ws.greet(final="uncertain", company="品牌名称", jobId="same-id")
        code, out = self.ws.run("check", "--platform", "boss", "--company", "登记公司", "--job-id", "same-id")
        self.assertEqual(code, 1)
        self.assertEqual(out["reasons"][0]["type"], "same_job")

    def test_declined_inbound_stays_closed_until_reopened(self):
        common = dict(company="入站样例", recruiter="王女士(HR)", skill="followup")
        chk = lambda kind: self.ws.run("check", "--platform", "boss", "--kind", kind,
                                       "--company", "入站样例", "--recruiter", "王女士(HR)")
        self.ws.append(kind="observe", observed="declined", reason="薪资不符", **common)
        for kind in ("reply", "resume", "followup"):
            code, out = chk(kind)
            self.assertEqual(code, 1, kind)
            self.assertEqual(out["reasons"][0]["type"], "closed")
        self.ws.append(kind="observe", observed="reopened", reason="HR 推荐了新职位，评估符合", **common)
        self.assertEqual(chk("reply")[0], 0)
        self.ws.append(kind="observe", observed="rejected", reason="对方婉拒", **common)
        self.ws.append(kind="observe", observed="reopened", reason="误用", **common)
        self.assertEqual(chk("reply")[1]["reasons"][0]["type"] in ("closed", "rejected"), True)

    def test_manual_blocks_even_with_decision(self):
        common = dict(company="接管样例", recruiter="赵先生(HR)", skill="followup")
        self.ws.greet(**dict(common, skill="apply"))
        self.ws.append(kind="observe", observed="paused", decision="D-20261028-01", **common)
        self.ws.append(kind="observe", observed="manual", **common)
        code, out = self.ws.run("check", "--platform", "boss", "--kind", "reply", "--company", "接管样例",
                                "--recruiter", "赵先生(HR)", "--decision", "D-20261028-01")
        self.assertEqual(code, 1)
        self.assertIn("manual", [r["type"] for r in out["reasons"]])

    def test_non_send_decision_done_and_still_pausing(self):
        common = dict(company="决定样例", recruiter="钱女士(HR)", skill="followup")
        self.ws.append(kind="observe", observed="paused", decision="D-20261028-03", **common)
        out = self.ws.run("decision", "--id", "D-20261028-03")[1]
        self.assertEqual(out["state"], "not_started")
        self.assertNotIn("发送流程执行", out["advice"].replace("外发类走发送流程", ""))
        self.assertEqual(out["stillPausing"][0]["company"], "决定样例")
        self.ws.append(kind="observe", observed="unpaused", decision="D-20261028-03", skill="user",
                       company="决定样例", recruiter="钱女士(HR)")
        out = self.ws.run("decision", "--id", "D-20261028-03")[1]
        self.assertEqual(out["state"], "done")
        self.assertEqual(out["stillPausing"], [])

    def test_decision_sent_but_not_completed_resumes(self):
        """中断点：外发已 sent、inbox 已标已执行，但 unpaused 未写入。状态应为 partial，步骤已完成，会话仍暂停。"""
        common = dict(company="对账样例", recruiter="孙先生(HR)", skill="followup", decision="D-20261028-04")
        self.ws.greet(**dict(common, skill="apply", decision=None))
        self.ws.append(kind="observe", observed="paused", **common)
        self.ws.greet(final="sent", kind="reply", step="reply1", **common)
        out = self.ws.run("decision", "--id", "D-20261028-04", "--step", "reply1")[1]
        self.assertEqual(out["state"], "partial")
        self.assertEqual(out["stepState"], "done")
        self.assertEqual(len(out["stillPausing"]), 1)

    def test_multi_action_decision_not_done_until_all_steps(self):
        """G01：交还会话 + 回复 + 发简历，无中断与中断两种路径都不能提前完成，也不能漏掉后续动作。"""
        d = "D-20261029-05"
        common = dict(company="组合样例", recruiter="何女士(HR)", skill="followup")
        st = lambda *a: self.ws.run("decision", "--id", d, *a)[1]
        self.ws.append(kind="observe", observed="manual", **common)
        self.ws.append(kind="observe", observed="paused", decision=d, **common)
        self.assertEqual(st()["state"], "not_started")
        self.ws.append(kind="observe", observed="released", decision=d, **common)
        out = st()
        self.assertEqual(out["state"], "partial")
        self.assertEqual(out["localActions"], ["released"])
        # 交还后中断再恢复：回复步骤仍未开始
        self.assertEqual(st("--step", "reply1")["stepState"], "not_started")
        self.ws.greet(final="sent", kind="reply", step="reply1", decision=d, **common)
        self.assertEqual(st()["state"], "partial")
        self.assertEqual(st("--step", "resume1")["stepState"], "not_started")
        self.ws.greet(final="sent", kind="resume", step="resume1", decision=d, **common)
        out = st()
        self.assertEqual(out["state"], "partial")
        self.assertEqual(out["steps"], {"reply1": "done", "resume1": "done"})
        self.ws.append(kind="observe", observed="unpaused", decision=d, **common)
        self.assertEqual(st()["state"], "done")

    def unstepped_attempt(self, statuses, **fields):
        """模拟旧版写下的、没有 step 的决定外发记录（绕过 append 直接写文件）。"""
        base = {"schemaVersion": 2, "platform": "boss", "account": "zhipin-test", "runId": "old-run",
                "attemptId": "att-unstepped-" + statuses[-1], "skill": "followup", "kind": "reply",
                "title": None, "location": None, "reason": "旧版执行决定", "evidence": None}
        base.update(fields)
        for st in statuses:
            ledger.append_line(self.ws.path, dict(base, status=st, at=datetime.now(TZ).isoformat(timespec="seconds")))

    def test_unstepped_sent_needs_review(self):
        """H01：旧版无 step 的已发送决定，在完成标记前恢复，不得被当成新步骤再次发送。"""
        d = "D-20261029-07"
        c = dict(company="旧步骤样例", recruiter="林女士(HR)")
        self.ws.append(kind="observe", observed="paused", decision=d, skill="followup", **c)
        self.unstepped_attempt(["pending", "sent"], decision=d, text="批准的回复", **c)
        out = self.ws.run("decision", "--id", d, "--step", "reply1")[1]
        self.assertEqual(out["state"], "needs_review")
        self.assertEqual(out["stepState"], "needs_review")
        code, chk = self.ws.run("check", "--platform", "boss", "--kind", "reply", "--company", c["company"],
                                "--recruiter", c["recruiter"], "--decision", d, "--run", "run-1")
        self.assertEqual(code, 1)
        self.assertIn("decision_needs_review", [r["type"] for r in chk["reasons"]])
        code, err = self.ws.append(kind="reply", status="pending", step="reply1", decision=d,
                                   skill="followup", **c)
        self.assertEqual(code, 2)
        self.assertIn("核对", err["error"])
        # 本人核对确认已全部完成 → 记完成标记
        self.ws.append(kind="observe", observed="unpaused", decision=d, skill="user", **c)
        self.assertEqual(self.ws.run("decision", "--id", d)[1]["state"], "done")

    def test_unstepped_pending_is_in_progress(self):
        d = "D-20261029-08"
        c = dict(company="旧待定样例", recruiter="林女士(HR)")
        self.unstepped_attempt(["pending"], decision=d, **c)
        self.assertEqual(self.ws.run("decision", "--id", d, "--step", "reply1")[1]["state"], "in_progress")

    def test_partial_combo_needs_review(self):
        d = "D-20261029-09"
        c = dict(company="旧组合样例", recruiter="林女士(HR)")
        self.ws.append(kind="observe", observed="released", decision=d, skill="followup", **c)
        self.unstepped_attempt(["pending", "sent"], decision=d, **c)
        out = self.ws.run("decision", "--id", d, "--step", "resume1")[1]
        self.assertEqual(out["state"], "needs_review")
        self.assertEqual(out["stepState"], "needs_review")

    def test_decision_send_requires_step(self):
        code, out = self.ws.append(status="pending", kind="reply", decision="D-20261029-10")
        self.assertEqual(code, 2)
        self.assertIn("step", out["error"])

    def test_step_requires_decision(self):
        code, out = self.ws.append(status="pending", kind="reply", step="reply1")
        self.assertEqual(code, 2)
        self.assertIn("step", out["error"])

    def test_closed_reasons_not_cleared_by_declined_then_reopened(self):
        """G02：job_closed → declined → reopened 不应放行；declined → reopened 应放行。"""
        chk = lambda c: self.ws.run("check", "--platform", "boss", "--kind", "reply",
                                    "--company", c, "--recruiter", "王女士(HR)")
        for obs in ("job_closed", "declined", "reopened"):
            self.ws.append(kind="observe", observed=obs, company="关闭样例", recruiter="王女士(HR)", skill="followup")
        code, out = chk("关闭样例")
        self.assertEqual(code, 1)
        self.assertIn("job_closed", out["reasons"][0]["detail"])
        self.ws.append(kind="observe", observed="job_closed", company="直接样例", recruiter="王女士(HR)", skill="followup")
        self.ws.append(kind="observe", observed="reopened", company="直接样例", recruiter="王女士(HR)", skill="followup")
        self.assertEqual(chk("直接样例")[0], 1)
        for obs in ("declined", "reopened"):
            self.ws.append(kind="observe", observed=obs, company="可重开样例", recruiter="王女士(HR)", skill="followup")
        self.assertEqual(chk("可重开样例")[0], 0)

    def test_decision_returns_paused_reason(self):
        """G03：只写了 paused、没写 inbox，凭公开输出即可重建事项摘要。"""
        d = "D-20261029-06"
        self.ws.append(kind="observe", observed="paused", decision=d, reason="面试邀约：周五 10:00 线下",
                       company="暂停样例", recruiter="冯先生(HR)", skill="followup")
        rec = self.ws.run("decision", "--id", d)[1]["pausedRecords"][0]
        self.assertEqual(rec["reason"], "面试邀约：周五 10:00 线下")
        self.assertEqual((rec["company"], rec["recruiter"]), ("暂停样例", "冯先生(HR)"))

    def test_first_contact_reasons_deduplicated(self):
        self.ws.greet(company="多次样例", recruiter="甲(HR)")
        self.ws.greet(kind="reply", skill="followup", company="多次样例", recruiter="甲(HR)")
        code, out = self.ws.run("check", "--platform", "boss", "--company", "多次样例")
        self.assertEqual(code, 1)
        self.assertEqual([r["type"] for r in out["reasons"]], ["same_company"])
        self.assertIn("reply", out["reasons"][0]["detail"])

    def test_guard_requires_lock_and_pending(self):
        self.ws.ensure_lock("run-a")
        _, out = self.ws.append(status="pending", runId="run-a", company="守卫样例", lock=False)
        aid = out["event"]["attemptId"]
        self.assertEqual(self.ws.run("guard", "--run", "run-a", "--attempt", aid)[0], 0)
        # 中断点：pending 之后暂停，锁过期被接管
        self.ws.expire_lock()
        _, acq = self.ws.run("lock", "acquire", "--skill", "followup", "--run", "run-b")
        self.assertEqual(acq["reconciled"][0]["attemptId"], aid)
        code, out = self.ws.run("guard", "--run", "run-a", "--attempt", aid)
        self.assertEqual(code, 2)
        self.assertIn("未持有", out["error"])
        # 新持锁者也不能拿别人的、已改记 uncertain 的尝试继续操作
        code, out = self.ws.run("guard", "--run", "run-b", "--attempt", aid)
        self.assertEqual(code, 1)

    def test_guard_rejects_finished_attempt(self):
        self.ws.ensure_lock("run-1")
        aid = self.ws.greet(final="sent", company="已完成样例")
        code, out = self.ws.run("guard", "--run", "run-1", "--attempt", aid)
        self.assertEqual(code, 1)
        self.assertIn("sent", out["reason"])

    def test_threads_merges_pending_without_recruiter(self):
        aid = self.ws.greet(final=None, company="甲")
        self.ws.append(status="sent", attemptId=aid, company="甲", recruiter="张先生", text="您好")
        _, out = self.ws.run("threads", "--platform", "boss")
        self.assertEqual(len(out["threads"]), 1)
        t = out["threads"][0]
        self.assertEqual(t["recruiter"], "张先生")
        self.assertEqual(t["outboundTexts"][0]["text"], "您好")
        self.assertTrue(t["canFollowup"])

    def test_threads_manual_and_release(self):
        common = dict(company="甲", recruiter="张先生")
        self.ws.greet(**common)
        self.ws.append(kind="observe", observed="manual", skill="followup", **common)
        _, out = self.ws.run("threads")
        self.assertTrue(out["threads"][0]["manual"])
        self.assertFalse(out["threads"][0]["canFollowup"])
        self.ws.append(kind="observe", observed="released", skill="user", **common)
        _, out = self.ws.run("threads")
        self.assertFalse(out["threads"][0]["manual"])

    def test_followup_eligibility_rules(self):
        common = dict(company="甲", recruiter="张先生")
        days_ago = lambda n: (datetime.now(TZ) - timedelta(days=n)).isoformat(timespec="seconds")
        self.ws.greet(at=days_ago(10), **common)
        self.ws.greet(kind="followup", skill="followup", at=days_ago(3), **common)
        _, out = self.ws.run("threads")
        self.assertTrue(out["threads"][0]["canFollowup"])
        self.ws.greet(kind="followup", skill="followup", at=days_ago(2), **common)
        _, out = self.ws.run("threads")
        t = out["threads"][0]
        self.assertEqual(t["followupsSinceReply"], 2)
        self.assertFalse(t["canFollowup"])
        self.assertIn("连续", t["followupReason"])
        self.ws.append(kind="observe", observed="replied", skill="followup", **common)
        _, out = self.ws.run("threads")
        self.assertTrue(out["threads"][0]["canFollowup"])
        self.ws.greet(kind="followup", skill="followup", **common)
        _, out = self.ws.run("threads")
        t = out["threads"][0]
        self.assertFalse(t["canFollowup"])
        self.assertIn("3", t["followupReason"])

    def test_followup_once_per_day(self):
        common = dict(company="乙", recruiter="李女士")
        self.ws.greet(**common)
        self.ws.greet(kind="followup", skill="followup", **common)
        _, out = self.ws.run("threads", "--company", "乙")
        self.assertIn("今天", out["threads"][0]["followupReason"])

    def test_observe_rejected_blocks_company(self):
        self.ws.append(kind="observe", observed="rejected", company="己公司", skill="followup")
        code, out = self.ws.run("check", "--platform", "boss", "--company", "己公司", "--scope", "job")
        self.assertEqual(code, 1)
        self.assertEqual(out["reasons"][0]["type"], "rejected")

class CurrentTakeoverTest(unittest.TestCase):
    """当前会话交还、读取分界与模糊活动确认，不涉及旧数据。"""

    def setUp(self):
        self.ws = Workspace()
        self.ws.ensure_lock("run-1")
        self.now = datetime(2026, 11, 2, 12, tzinfo=TZ)

    def tearDown(self):
        self.ws.close()

    def observe(self, observed, **extra):
        return self.ws.append(kind="observe", observed=observed, skill=extra.pop("skill", "user"),
                              recruiter="示例招聘者(HR)", **extra)

    def adopt(self, **extra):
        return self.observe("adopted", readAt=self.now.isoformat(),
                            at=(self.now + timedelta(minutes=5)).isoformat(), **extra)

    def thread(self, company="示例科技"):
        return self.ws.run("threads", "--company", company)[1]["threads"][0]

    def test_adoption_requires_explicit_user_valid_read_time_and_live_lock(self):
        self.assertEqual(self.adopt(skill="followup")[0], 2)
        for read_at in (None, "", "2026-11-02T12:00:00", "2026-11-03T12:00:00+08:00"):
            self.assertEqual(self.observe("adopted", readAt=read_at, at=self.now.isoformat())[0], 2)
        self.ws.run("lock", "release", "--run", "run-1")
        self.assertEqual(self.adopt()[0], 2)
        self.ws.ensure_lock("run-1")
        self.assertEqual(self.adopt()[0], 0)
        self.assertEqual(self.adopt()[1]["duplicate"], True)

    def test_adoption_cannot_bypass_no_send_or_global_pause(self):
        self.ws.run("lock", "release", "--run", "run-1")
        self.ws.run("lock", "acquire", "--skill", "followup", "--run", "run-1", "--no-send")
        self.assertEqual(self.adopt()[0], 2)
        self.ws.run("lock", "release", "--run", "run-1")
        self.ws.ensure_lock("run-1")
        self.ws.run("pause", "on", "--reason", "合成安全暂停")
        self.assertEqual(self.adopt()[0], 2)
        self.ws.run("pause", "off")
        self.assertEqual(self.adopt()[0], 0)

    def test_second_today_activity_still_pauses_after_one_acknowledged(self):
        self.adopt()
        item = {"shown": "今天", "precision": "day", "kind": "text", "count": 1}
        self.observe("manual")
        self.observe("released", acknowledged=[item])
        self.assertFalse(self.thread()["manual"])
        self.assertEqual(self.thread()["acknowledged"], [item])
        # 页面第2条同标签活动超出已确认条数，技能须另行暂停，而非扩大确认范围。
        self.observe("paused", decision="D-20261102-02", reason="今天第2条我方活动无法确定执行者")
        self.assertEqual(self.thread()["pausedBy"], ["D-20261102-02"])
        self.assertEqual(self.thread()["acknowledged"][0]["count"], 1)

    def test_boundary_uses_read_time_sent_and_release_max(self):
        self.adopt()
        self.assertEqual(self.thread()["boundaryAt"], self.now.isoformat())
        sent = self.now + timedelta(minutes=10)
        self.ws.greet(kind="reply", recruiter="示例招聘者(HR)", at=sent.isoformat())
        self.assertEqual(self.thread()["boundaryAt"], sent.isoformat())
        released = self.now + timedelta(minutes=20)
        self.observe("released", at=released.isoformat())
        self.assertEqual(self.thread()["boundaryAt"], released.isoformat())
        # 事后补记更早的 sent 不能后退分界。
        self.ws.greet(kind="resume", recruiter="示例招聘者(HR)", at=sent.isoformat())
        self.assertEqual(self.thread()["boundaryAt"], released.isoformat())
        # 普通会话也按 sent / released 取最晚值。
        self.ws.greet(company="普通会话", recruiter="示例招聘者(HR)", at=sent.isoformat())
        self.observe("released", company="普通会话", at=self.now.isoformat())
        self.assertEqual(self.thread("普通会话")["boundaryAt"], sent.isoformat())

    def test_acknowledged_validation_and_latest_release_output(self):
        item = {"shown": "2026-11-02", "precision": "day", "kind": "text", "count": 1}
        for value in (None, {}, [None], [{**item, "text": "原文"}]):
            self.assertEqual(self.observe("released", acknowledged=value)[0], 2)
        for field, values in {"shown": [None, "", "  ", 1], "precision": [None, "hour"],
                              "kind": [None, "unknown"], "count": [0, -1, True, 1.0, "1"]}.items():
            for value in values:
                self.assertEqual(self.observe("released", acknowledged=[{**item, field: value}])[0], 2)
        self.assertEqual(self.observe("manual", acknowledged=[item])[0], 2)
        self.adopt()
        for kind in ("text", "resume", "contact", "interview"):
            self.assertEqual(self.observe("released", acknowledged=[{**item, "kind": kind}])[0], 0)
            self.assertEqual(self.thread()["acknowledged"], [{**item, "kind": kind}])
        minute = {**item, "shown": "2026-11-02 10:00", "precision": "minute"}
        self.observe("released", acknowledged=[minute])
        self.assertEqual(self.thread()["acknowledged"], [minute])
        self.observe("released")
        self.assertEqual(self.thread()["acknowledged"], [])

    def test_release_clears_manual_without_resetting_counts(self):
        self.adopt()
        self.ws.greet(kind="followup", recruiter="示例招聘者(HR)")
        before = self.thread()
        self.observe("manual")
        self.observe("released", skill="user")
        after = self.thread()
        self.assertFalse(after["manual"])
        for key in ("followupsSent", "followupsSinceReply", "readAt"):
            self.assertEqual(after[key], before[key])

    def test_all_local_sent_available_including_boundary_and_resume(self):
        for i in range(8):
            self.ws.greet(kind="reply", recruiter="示例招聘者(HR)", text=f"本地合成文字{i}")
        self.ws.greet(kind="resume", recruiter="示例招聘者(HR)")
        t = self.thread()
        self.assertEqual(len(t["outboundTexts"]), 9)
        self.assertEqual(t["outboundTexts"][-1]["kind"], "resume")
        self.assertEqual(t["outboundTexts"][-1]["at"], t["boundaryAt"])


if __name__ == "__main__":
    unittest.main()

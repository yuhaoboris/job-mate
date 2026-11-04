"""原生扫描跨调用续页：真实 adapter CLI + 离线替身，不访问真实通道。"""
import copy
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_im_lark as support
import im_lark
import im_scan
import ledger
import notify

ANCHOR, ROOT, fake = support.ANCHOR, support.ROOT, support.fake


class LarkScanTest(unittest.TestCase):
    def setUp(self):
        self.case = support.ImLarkTest()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.establish()

    def reset(self):
        self.case.doCleanups()
        self.setUp()

    def cli_fetch(self, crash=None, expected=0):
        case = self.case
        env = dict(os.environ)
        env.pop("JOB_MATE_TEST_CRASH_AT", None)
        if crash:
            env["JOB_MATE_TEST_CRASH_AT"] = crash
        process = subprocess.run([sys.executable, "-B", "-I", str(ROOT / "scripts/im_lark.py"),
                                  "--workspace", str(case.ws), "sync", "--fetch-only"],
                                 cwd=case.base, env=env, capture_output=True, encoding="utf-8", timeout=120)
        self.assertEqual(process.returncode, expected, process.stdout + process.stderr)
        self.assertFalse(process.stderr)
        return json.loads(process.stdout) if expected == 0 else None

    def populate_prefix(self, owner):
        case = self.case
        bot = fake.sample("native-get")["data"]["items"][0]["sender"]
        messages = []
        for number in range(2, 1003):
            message = case.message(number, created=ANCHOR + 10 if owner else ANCHOR + number)
            if not owner:
                message["sender"] = copy.deepcopy(bot)
            messages.append(message)
        pause = case.message(2000, "暂停", created=ANCHOR + 10 if owner else ANCHOR + 2000)
        case.add_messages(*messages, pause)
        return pause

    def prefix_then_pause(self, owner):
        case = self.case
        pause = self.populate_prefix(owner)
        results = [self.cli_fetch()]
        self.assertNotIn(pause["message_id"], case.rows()["in"])
        self.assertEqual(case.poll()["businessCursorMs"], ANCHOR + 10 if owner else 0)
        os.environ["JOB_MATE_NOW"] = datetime.fromtimestamp((ANCHOR + 600000) / 1000, timezone.utc).isoformat()
        results.append(self.cli_fetch())
        self.assertEqual([r["incomplete"] for r in results], [True, False])
        self.assertEqual([len(r["incoming"]["new"]) for r in results], [999, 3] if owner else [0, 1])
        self.assertIn(pause["message_id"], case.rows()["in"])
        self.assertEqual(case.rows()["commandResults"][pause["message_id"]]["status"], "applied")
        calls = [c for c in case.state_data()["calls"] if c["command"] == "native-list"]
        queries = [json.loads(c["argv"][c["argv"].index("--params") + 1]) for c in calls]
        self.assertEqual(len(calls), 21)
        self.assertIn("page_token", queries[20])
        self.assertEqual({q["start_time"] for q in queries}, {queries[0]["start_time"]})
        self.assertEqual({q["end_time"] for q in queries}, {queries[0]["end_time"]})
        return results

    def test_real_cli_1001_bot_then_owner_pause_eventually_arrives(self):
        self.prefix_then_pause(owner=False)

    def test_real_cli_over_1000_same_ms_owner_then_pause_eventually_arrives(self):
        self.prefix_then_pause(owner=True)

    def checkpoint(self):
        case = self.case
        item = case.message(2, created=ANCHOR + 20)
        item["sender"] = copy.deepcopy(fake.sample("native-get")["data"]["items"][0]["sender"])
        item["body"]["content"] = '{"text":"synthetic-bot-body-must-not-persist"}'
        with patch.object(im_lark, "MAX_PAGES", 1):
            out, _ = case.fetch_pages([case.page([item], True, "synthetic-next-token")])
        self.assertTrue(out["incomplete"])
        self.assertTrue(case.poll()["lastIncomplete"])
        self.assertEqual(case.poll()["businessCursorMs"], 0)
        return item

    def records(self):
        return notify.read_records(self.case.ws)

    def projection(self):
        return im_scan.project(self.records(), notify.now_ms())

    def assert_pause_once(self, pause):
        case = self.case
        records = self.records()
        mid = pause["message_id"]
        self.assertEqual(sum(r.get("type") == "in" and r.get("messageId") == mid for r in records), 1)
        self.assertEqual(sum(r.get("type") == "command" and r.get("messageId") == mid and r.get("status") == "applied" for r in records), 1)
        self.assertEqual(sum(r.get("type") == "out" and r.get("key") == "cmd:" + mid and r.get("status") == "queued" for r in records), 1)
        self.assertTrue(ledger.read_pause(case.ws)["paused"])
        self.assertEqual(ledger.read_pause(case.ws)["imCommands"].count(mid), 1)

    def test_complete_bot_window_advances_only_scan_watermark_then_queries_overlap(self):
        case = self.case
        bot = fake.sample("native-get")["data"]["items"][0]["sender"]
        items = [case.message(n, created=ANCHOR + n) for n in range(2, 1003)]
        for item in items:
            item["sender"] = copy.deepcopy(bot)
        case.add_messages(*items)
        first, second = self.cli_fetch(), self.cli_fetch()
        self.assertEqual([first["incomplete"], second["incomplete"]], [True, False])
        self.assertEqual(case.poll()["businessCursorMs"], 0)
        through = case.poll()["scanThroughMs"]
        self.assertEqual(case.poll()["sinceMs"], through)
        self.assertFalse(case.poll()["lastIncomplete"])
        before = len([c for c in case.state_data()["calls"] if c["command"] == "native-list"])
        self.cli_fetch()
        calls = [c for c in case.state_data()["calls"] if c["command"] == "native-list"]
        self.assertEqual(len(calls) - before, 1)
        query = json.loads(calls[-1]["argv"][calls[-1]["argv"].index("--params") + 1])
        self.assertEqual(query["start_time"], str(through // 1000 - 1))
        self.assertEqual(case.rows()["cursor"], 0)

    def test_intent_is_persisted_before_network_and_bad_page_adds_no_progress(self):
        before = (self.case.ws / "ledger/im.jsonl").read_bytes()
        def transport(*_):
            self.assertEqual(self.records()[-1]["phase"], "intent")
            raise im_lark.LarkError("synthetic_query_failed")
        with patch.object(im_lark, "run_lark", side_effect=transport):
            self.assertTrue(self.case.sync("--fetch-only")["incomplete"])
        self.case.assert_no_scan_progress(before)
        self.assertTrue(self.case.poll()["lastIncomplete"])
        self.assertEqual(self.case.poll()["scanThroughMs"], 0)

    def test_resume_rejects_cross_call_tokens_order_versions_time_and_chat_without_progress(self):
        item = self.checkpoint()
        case = self.case
        before = (case.ws / "ledger/im.jsonl").read_bytes()
        earlier = case.message(3, created=ANCHOR + 19)
        changed = copy.deepcopy(item)
        changed["body"]["content"] = '{"text":"different-bot-body"}'
        bad_time = case.message(4)
        bad_time["create_time"] = None
        foreign = case.message(5, created=ANCHOR + 21)
        foreign["chat_id"] = "oc_" + "f" * 32
        for page in (case.page([case.message(3, created=ANCHOR + 21)], True, "synthetic-next-token"),
                     case.page([earlier]), case.page([changed]), case.page([bad_time]), case.page([foreign])):
            with self.subTest(page=page):
                out, calls = case.fetch_pages([page])
                self.assertTrue(out["incomplete"])
                self.assertEqual((case.ws / "ledger/im.jsonl").read_bytes(), before)
                query = json.loads(calls[0][calls[0].index("--params") + 1])
                self.assertEqual(query["page_token"], "synthetic-next-token")
        self.assertNotIn("synthetic-bot-body-must-not-persist", before.decode("utf-8"))
        active = next(iter(self.projection()["active"].values()))
        self.assertTrue(all(len(h) == 64 for h in active["versions"].values()))

    def test_opaque_token_unicode_separators_quotes_and_backslashes_roundtrip(self):
        case = self.case
        token = "opaque-" + "".join(chr(n) for n in (34, 92, 0x2028, 0x85, 10, 92))
        with patch.object(im_lark, "MAX_PAGES", 1):
            case.fetch_pages([case.page([case.message()], True, token)])
        self.assertEqual(next(iter(self.projection()["active"].values()))["nextToken"], token)
        out, calls = case.fetch_pages([case.page([])])
        self.assertFalse(out["incomplete"])
        self.assertEqual(json.loads(calls[0][calls[0].index("--params") + 1])["page_token"], token)

    def test_unknown_token_error_keeps_original_checkpoint_and_window(self):
        self.checkpoint()
        case = self.case
        before = (case.ws / "ledger/im.jsonl").read_bytes()
        rejection = subprocess.CompletedProcess([], 1, "", json.dumps({"ok": False, "identity": "bot", "error": {"code": "UNSAMPLED_EXPIRED", "message": "secret-diagnostic"}}))
        out, calls = case.fetch_pages([rejection])
        self.assertTrue(out["incomplete"])
        self.assertNotIn("secret", json.dumps(out))
        self.assertEqual((case.ws / "ledger/im.jsonl").read_bytes(), before)
        original = json.loads(calls[0][calls[0].index("--params") + 1])
        out, calls = case.fetch_pages([case.page([case.message(3, "暂停", created=ANCHOR + 21)])])
        self.assertFalse(out["incomplete"])
        self.assertEqual(json.loads(calls[0][calls[0].index("--params") + 1]), original)

    def test_same_millisecond_resume_does_not_skip_smaller_message_id(self):
        case = self.case
        high = case.message(100, created=ANCHOR + 10)
        low = case.message(2, "暂停", created=ANCHOR + 10)
        with patch.object(im_lark, "MAX_PAGES", 1):
            case.fetch_pages([case.page([high], True, "synthetic-tie-token")])
        out, _ = case.fetch_pages([case.page([low])])
        self.assertFalse(out["incomplete"])
        self.assertIn(low["message_id"], case.rows()["in"])
        self.assert_pause_once(low)

    def test_future_since_without_complete_scan_evidence_is_still_rejected(self):
        self.checkpoint()
        case = self.case
        before = (case.ws / "ledger/im.jsonl").read_bytes()
        batch = {"since": case.poll()["sinceMs"] + 1, "complete": True, "messages": []}
        case.invoke(notify, "incoming", "--json", json.dumps(batch), expected=2)
        self.assertEqual((case.ws / "ledger/im.jsonl").read_bytes(), before)
        case.fetch_pages([case.page([])])
        watermark = case.poll()["scanThroughMs"]
        self.assertGreater(watermark, case.poll()["businessCursorMs"])
        batch["since"] = watermark - 1000
        case.invoke(notify, "incoming", "--json", json.dumps(batch))
        before = (case.ws / "ledger/im.jsonl").read_bytes()
        batch["since"] = watermark + 1
        case.invoke(notify, "incoming", "--json", json.dumps(batch), expected=2)
        self.assertEqual((case.ws / "ledger/im.jsonl").read_bytes(), before)

    def test_bad_checkpoint_versions_transitions_routes_bounds_and_unproven_watermark_fail_closed(self):
        self.checkpoint()
        original = self.records()
        mutations = [(0, "type", "scna"), (-1, "type", "cursor"), (0, "scanVersion", 2), (0, "scanVersion", True), (0, "revision", 1),
                     (0, "route", "f" * 64), (0, "chatId", "oc_" + "f" * 32),
                     (0, "startMs", original[-1]["startMs"] + 1000), (0, "endMs", original[-1]["endMs"] + 1000),
                     (0, "startedMs", False), (-1, "revision", 3), (-1, "base", "f" * 64),
                     (-1, "seenTokens", ["other-token"]), (-1, "seenTokens", ["a", "a"]),
                     (-1, "nextToken", None), (-1, "lastMs", original[-1]["endMs"]),
                     (-1, "versions", {}), (-1, "hasMore", False), (-1, "scanThroughMs", original[-1]["endMs"])]
        scan_indices = [i for i, r in enumerate(original) if r.get("type") == "scan"]
        for which, key, value in mutations:
            with self.subTest(key=key, value=value):
                records = copy.deepcopy(original)
                records[scan_indices[which]][key] = value
                raw = "".join(notify.canonical(r) + "\n" for r in records)
                (self.case.ws / "ledger/im.jsonl").write_text(raw, encoding="utf-8")
                with patch.object(im_lark, "run_lark", side_effect=AssertionError("坏进度不得查询")):
                    out = self.case.sync("--fetch-only")
                self.assertTrue(out["incomplete"])
                self.assertEqual(out["errors"], ["invalid_scan_state"])
                self.assertEqual((self.case.ws / "ledger/im.jsonl").read_text(encoding="utf-8"), raw)
        proof_index = next(i for i, r in enumerate(original) if "scanBatch" in r)
        for field, value in (("versions", {}), ("versionsDigest", "f" * 64)):
            records = copy.deepcopy(original)
            records[proof_index]["scanBatch"][field] = value
            raw = "".join(notify.canonical(r) + "\n" for r in records)
            (self.case.ws / "ledger/im.jsonl").write_text(raw, encoding="utf-8")
            with patch.object(im_lark, "run_lark", side_effect=AssertionError("坏接收凭据不可借用")):
                self.assertTrue(self.case.sync("--fetch-only")["incomplete"])
            self.assertEqual((self.case.ws / "ledger/im.jsonl").read_text(encoding="utf-8"), raw)
        records = copy.deepcopy(original)
        forged = copy.deepcopy(records[-1])
        forged.update(phase="complete", hasMore=False, nextToken=None, scanThroughMs=forged["endMs"])
        # 即使标成终页，也没有匹配的 incoming 凭据，不能从 checkpoint 猜完成。
        records[-1] = forged
        (self.case.ws / "ledger/im.jsonl").write_text("".join(notify.canonical(r) + "\n" for r in records), encoding="utf-8")
        self.assertTrue(self.case.sync("--fetch-only")["incomplete"])

    def test_route_switch_does_not_borrow_checkpoint_and_return_restores_same_window(self):
        self.checkpoint()
        case = self.case
        original_scan = next(iter(self.projection()["active"].values()))
        before = (case.ws / "ledger/im.jsonl").read_bytes()
        case.configure(larkProfile="different-profile")
        calls_before = len(case.state_data()["calls"])
        self.assertEqual(self.cli_fetch()["incoming"], {"skipped": "not_readable"})
        self.assertEqual(len(case.state_data()["calls"]), calls_before)
        self.assertEqual(case.poll()["scanThroughMs"], 0)
        self.assertFalse(case.poll()["lastIncomplete"])
        self.assertEqual((case.ws / "ledger/im.jsonl").read_bytes(), before)
        case.configure()
        out, calls = case.fetch_pages([case.page([])])
        self.assertFalse(out["incomplete"])
        query = json.loads(calls[0][calls[0].index("--params") + 1])
        self.assertEqual(query["page_token"], original_scan["nextToken"])
        self.assertEqual(query["start_time"], str(original_scan["startMs"] // 1000))
        self.assertEqual(query["end_time"], str(original_scan["endMs"] // 1000))

    def test_second_route_with_own_chat_starts_fresh_without_consuming_old_checkpoint(self):
        self.checkpoint()
        case = self.case
        old = next(iter(self.projection()["active"].values()))
        profile = "synthetic-second-profile"
        second = fake.initial_state(profile, seeded=False)["profiles"][profile]
        second.update(ownerId="ou_" + "3" * 32, chatId="oc_" + "2" * 32)
        case.update_state(lambda state: state["profiles"].update({profile: second}))
        case.configure(larkProfile=profile, ownerId=second["ownerId"])
        case.queue("second-route")
        case.sync("--send-only")
        self.assertFalse(self.cli_fetch()["incomplete"])
        call = [c for c in case.state_data()["calls"] if c["command"] == "native-list"][-1]
        query = json.loads(call["argv"][call["argv"].index("--params") + 1])
        self.assertNotIn("page_token", query)
        self.assertEqual(query["container_id"], second["chatId"])
        self.assertEqual(call["argv"][:2], ["--profile", profile])
        self.assertIn(old["scanId"], {s["scanId"] for s in self.projection()["active"].values()})
        case.configure()
        self.assertEqual(case.poll()["scanThroughMs"], 0)
        self.assertTrue(case.poll()["lastIncomplete"])
        out, calls = case.fetch_pages([case.page([])])
        self.assertFalse(out["incomplete"])
        query = json.loads(calls[0][calls[0].index("--params") + 1])
        self.assertEqual(query["page_token"], old["nextToken"])

    def test_recovery_lock_refuses_resume_before_query_or_progress(self):
        self.checkpoint()
        case = self.case
        case.invoke(ledger, "lock", "acquire", "--skill", "setup", "--recovery", "--run", "scan-recovery")
        before = (case.ws / "ledger/im.jsonl").read_bytes()
        calls_before = len(case.state_data()["calls"])
        self.cli_fetch(expected=2)
        with patch.object(im_lark, "run_lark", side_effect=AssertionError("恢复锁下不可查询")):
            for flag in ((), ("--fetch-only",), ("--send-only",)):
                case.sync(*flag, expected=2)
        self.assertEqual(len(case.state_data()["calls"]), calls_before)
        self.assertEqual((case.ws / "ledger/im.jsonl").read_bytes(), before)

    def test_real_process_crashes_before_during_after_incoming_and_checkpoint_replay_once(self):
        points = ("scan_after_intent", "scan_before_incoming", "scan_incoming_after_message", "scan_incoming_after_cursor",
                  "scan_command_after_effect", "scan_after_incoming", "scan_before_checkpoint", "scan_after_checkpoint")
        for point in points:
            with self.subTest(point=point):
                self.reset()
                pause = self.case.message(2, "暂停")
                self.case.add_messages(pause)
                self.cli_fetch(crash=point, expected=70)
                plan = self.case.poll()
                self.assertEqual(bool(plan["scanThroughMs"]), point == "scan_after_checkpoint")
                self.assertEqual(plan["lastIncomplete"], point != "scan_after_checkpoint")
                if point == "scan_after_intent":
                    self.assertFalse(any(c["command"] == "native-list" for c in self.case.state_data()["calls"]))
                self.cli_fetch()
                self.assert_pause_once(pause)
                self.assertFalse(self.case.poll()["lastIncomplete"])

    def test_real_process_checkpoint_crash_replays_old_pages_or_resumes_saved_token(self):
        for point in ("scan_before_checkpoint", "scan_after_checkpoint"):
            with self.subTest(point=point):
                self.reset()
                pause = self.populate_prefix(owner=False)
                self.cli_fetch(crash=point, expected=70)
                projection = self.projection()
                active = next(iter(projection["active"].values()))
                self.assertEqual(active["revision"], int(point == "scan_after_checkpoint"))
                self.assertEqual(self.case.poll()["scanThroughMs"], 0)
                self.assertEqual(self.case.poll()["businessCursorMs"], 0)
                outcomes = [self.cli_fetch()]
                if outcomes[-1]["incomplete"]:
                    outcomes.append(self.cli_fetch())
                self.assertFalse(outcomes[-1]["incomplete"])
                self.assert_pause_once(pause)
                calls = [c for c in self.case.state_data()["calls"] if c["command"] == "native-list"]
                self.assertEqual(len(calls), 41 if point == "scan_before_checkpoint" else 21)

    def test_crash_replay_checks_native_versions_even_before_checkpoint(self):
        for point in ("scan_incoming_after_message", "scan_after_incoming"):
            with self.subTest(point=point):
                self.reset()
                pause = self.case.message(2, "暂停")
                self.case.add_messages(pause)
                self.cli_fetch(crash=point, expected=70)
                before = (self.case.ws / "ledger/im.jsonl").read_bytes()
                def change(state):
                    state["profiles"][support.PROFILE]["messages"][-1]["body"]["content"] = json.dumps({"text": "恢复"})
                self.case.update_state(change)
                out = self.cli_fetch()
                self.assertTrue(out["incomplete"])
                self.assertIn("conflicting_message_version", out["errors"])
                self.assertEqual((self.case.ws / "ledger/im.jsonl").read_bytes(), before)
                self.assertEqual(self.case.poll()["scanThroughMs"], 0)

    def test_incoming_io_failure_retains_checkpoint_and_repairs_business_effects(self):
        pause = self.case.message(2, "暂停")
        self.case.add_messages(pause)
        original = notify.append_record
        def fail_applied(ws, record, tz):
            if record.get("type") == "command" and record.get("status") == "applied":
                raise OSError("synthetic-fsync-failure")
            return original(ws, record, tz)
        with patch.object(notify, "append_record", side_effect=fail_applied):
            self.case.sync("--fetch-only", expected=2)
        self.assertEqual(self.case.poll()["scanThroughMs"], 0)
        self.assertEqual(next(iter(self.projection()["active"].values()))["revision"], 0)
        self.cli_fetch()
        self.assert_pause_once(pause)


if __name__ == "__main__":
    unittest.main()

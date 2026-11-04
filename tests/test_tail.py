"""合成会话的 v1 事件与尾部核对；消息原文只用于输入，不保存。"""
import contextlib
import io
import json
import os
import queue
import threading
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ledger
import workspace
import history_fixture


class TailTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"JOB_MATE_TESTING": "1", "JOB_MATE_NOW": "2026-11-04T12:00:00+08:00",
                                          "JOB_MATE_TEST_PLATFORM_ROOT": str(ROOT / "tests/fixtures/platforms")})
        self.env.start()
        workspace.init_workspace(self.ws, {"boss": {"accountKey": "boss-main", "displayName": "合成账号"}})
        meta = ledger.read_json(self.ws / ledger.MARKER)
        meta["accounts"]["testplat"] = {"accountKey": "test-main", "displayName": "测试账号"}
        ledger.write_atomic(self.ws / ledger.MARKER, json.dumps(meta))
        self.assertEqual(self.run_cli("lock", "acquire", "--skill", "followup", "--run", "run-tail")[0], 0)
        self.identity = {"platform": "boss", "company": "合成公司", "title": "开发", "recruiter": "招聘者(HR)"}
        code, out = self.append(kind="observe", observed="resume_requested")
        self.assertEqual(code, 0, out)
        self.thread = "boss|boss-main|合成公司|招聘者(hr)"

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *args, stdin=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch.object(sys, "stdin", io.StringIO(stdin or "")):
            try:
                code = ledger.main(["--workspace", str(self.ws), *args])
            except SystemExit as error:
                self.fail(f"CLI 参数未实现：{error}")
        return code, json.loads(out.getvalue())

    def append(self, **fields):
        event = dict(self.identity, runId="run-tail", skill="followup", reason="合成测试")
        event.update(fields)
        event = history_fixture.reply_input(self.ws, event)
        return self.run_cli("append", "--json", json.dumps(event, ensure_ascii=False))

    def history(self, **fields):
        """显式构造旧动作记录，供尾部审计使用；不验证本轮发送授权。"""
        if fields.get("kind") not in ledger.ACTION_KINDS:
            return self.append(**fields)
        event = dict(self.identity, runId="run-tail", skill="followup", reason="合成历史夹具")
        event.update(fields)
        return history_fixture.append_action(self.ws, event)

    def message(self, text="私密HR原文A", label="今天 10:32", **extra):
        return dict(type="text", text=text, label=label, **extra)

    def snapshot(self, messages=None, anchor=None, **extra):
        body = dict(anchor=self.message("私密我方锚点", "今天 10:00", speaker="us") if anchor is None else anchor,
                    messages=messages if messages is not None else [self.message()], complete=True, anchorUnique=True)
        body.update(extra)
        return body

    def tail(self, body=None, base=None, thread=None, stdin=False, run="run-tail"):
        args = ["tail", "--run", run, "--thread", thread or self.thread,
                "--read-at", os.environ["JOB_MATE_NOW"]]
        if base:
            args += ["--base", base]
        raw = json.dumps(self.snapshot() if body is None else body, ensure_ascii=False)
        if not stdin:
            args += ["--json", raw]
        return self.run_cli(*args, stdin=raw if stdin else None)

    def ok_tail(self, *args, **kwargs):
        code, out = self.tail(*args, **kwargs)
        self.assertEqual(code, 0, out)
        return out

    def handled(self, tail, user=False, **extra):
        return self.append(kind="observe", observed="handled", tailId=tail["tailId"], baseTailId=tail["baseTailId"],
                           **dict({"skill": "user", "evidence": "本人核对完整页面"} if user else {}, **extra))

    def test_schema_fields_and_screen_no_send_lock(self):
        code, out = self.append(kind="screen", result="qualified", reasons=["符合"], jdFacts={"salary.months": "13"},
                                checks=["hours.time"], targetRole="开发")
        self.assertEqual(code, 0, out)
        ev = out["event"]
        self.assertEqual(ev["schemaVersion"], 1)
        self.assertRegex(ev["eventId"], r"^evt-[0-9a-f]{12}$")
        self.assertEqual(ev["threadKey"], self.thread)
        self.assertEqual(ev["jobKey"], "boss|合成公司|开发")
        self.assertEqual(self.run_cli("lock", "release", "--run", "run-tail")[0], 0)
        self.assertEqual(self.run_cli("lock", "acquire", "--skill", "followup", "--run", "run-tail", "--no-send")[0], 0)
        self.assertEqual(self.append(kind="screen", result="pending", reasons=[])[0], 0)
        self.assertEqual(self.run_cli("lock", "release", "--run", "run-tail")[0], 0)
        self.assertEqual(self.append(kind="screen", result="pending", reasons=[])[0], 2)

    def test_time_labels_and_no_context_does_not_guess_today(self):
        tz = ledger.tz_of({})
        at = ledger.parse_at(os.environ["JOB_MATE_NOW"])
        for label, day in (("今天 10:32", "2026-11-04"), ("昨天", "2026-11-03"), ("前天", "2026-11-02"),
                           ("星期三", "2026-10-28"), ("12月31日", "2025-12-31"), ("2026年11月2日", "2026-11-02")):
            with self.subTest(label=label):
                self.assertEqual(ledger.resolve_time_label(label, at, tz, "boss")["date"], day)
        self.assertIsNone(ledger.resolve_time_label("10:32", at, tz, "boss")["date"])
        self.assertIsNone(ledger.resolve_time_label("未知", at, tz, "boss")["date"])

    def test_unique_reads_explicit_base_cross_day_and_no_raw_text(self):
        first = self.ok_tail()
        self.assertFalse(first["settled"])
        self.assertEqual(self.handled(first)[0], 2)
        verify = self.ok_tail(base=first["tailId"], stdin=True)
        self.assertNotEqual(first["tailId"], verify["tailId"])
        self.assertEqual((verify["result"], verify["settled"]), ("same", True))
        self.assertEqual(self.handled(verify)[0], 0)
        os.environ["JOB_MATE_NOW"] = "2026-11-05T12:00:00+08:00"
        self.run_cli("lock", "renew", "--run", "run-tail")
        same = self.ok_tail(self.snapshot([self.message(label="昨天 10:32")],
                                         anchor=self.message("私密我方锚点", "昨天 10:00", speaker="us")))
        self.assertEqual(same["result"], "same")
        self.assertFalse(same["settled"])
        self.assertEqual(self.handled(verify)[0], 2)
        raw = ledger.events_path(self.ws).read_text(encoding="utf-8")
        self.assertNotIn("私密HR原文A", raw)
        self.assertNotIn("私密我方锚点", raw)

    def baseline(self, body=None):
        out = self.ok_tail(body)
        self.assertEqual(self.handled(out, user=True)[0], 0)
        return out

    def send(self, text="本人批准的 回复", **extra):
        code, out = self.history(kind="reply", status="pending", text=text, **extra)
        self.assertEqual(code, 0, out)
        aid = out["event"]["attemptId"]
        code, out = self.history(kind="reply", status="sent", attemptId=aid, text=text, **extra)
        self.assertEqual(code, 0, out)
        return aid

    def bridge_body(self, aid, messages=None, **changes):
        sent = self.message("本人批准的 回复", "今天 12:00", speaker="us", attemptId=aid)
        body = self.snapshot(messages=[] if messages is None else messages, anchor=sent,
                             bridge=[self.snapshot()["anchor"], dict(self.message(), speaker="hr"), sent], bridgeComplete=True)
        body.update(changes)
        return body

    def test_same_anchor_extension_repetition_and_changed_or_withdrawn_tail(self):
        for original, current, expected, count in (([self.message()], [self.message(), self.message()], "extended", 1),
                     ([self.message(), self.message()], [self.message()], "mismatch", None),
                     ([self.message()], [self.message("编辑后的私密文字")], "mismatch", None),
                     ([self.message("A"), self.message("B")], [self.message("B"), self.message("A")], "mismatch", None)):
            with self.subTest(expected=expected, original=original):
                base = self.baseline(self.snapshot(original))
                result = self.ok_tail(self.snapshot(current), base=base["tailId"])
                self.assertEqual((result["result"], result["newCount"]), (expected, count))
                self.assertFalse(result["settled"])

    def test_extension_processed_then_explicit_base_not_old_handled(self):
        self.baseline()
        current = self.snapshot([self.message(), self.message("新私密内容", "今天 11:00")])
        extended = self.ok_tail(current)
        verified = self.ok_tail(current, base=extended["tailId"])
        self.assertEqual((verified["result"], verified["settled"]), ("same", True))
        self.assertEqual(self.handled(verified)[0], 0)

    def test_time_missing_or_downgraded_duplicate_anchor_and_incomplete_read(self):
        for body in (self.snapshot([self.message(label="10:32")]), self.snapshot([self.message(label="今天")]),
                     self.snapshot(anchorUnique=False), self.snapshot(complete=False)):
            with self.subTest(body=body):
                base = self.baseline()
                result = self.ok_tail(body, base=base["tailId"])
                self.assertEqual(result["result"], "mismatch")
                self.assertFalse(result["settled"])
                self.assertEqual(self.handled(result)[0], 2)
                if not body["complete"] or not body["anchorUnique"]:
                    self.assertEqual(self.handled(result, user=True)[0], 2)

    def test_mismatch_review_cannot_be_eliminated_by_switching_base(self):
        base = self.baseline()
        changed = self.snapshot([self.message("新的私密信息")])
        mismatch = self.ok_tail(changed, base=base["tailId"])
        same = self.ok_tail(changed, base=mismatch["tailId"])
        self.assertEqual(same["result"], "same")
        self.assertFalse(same["settled"])
        self.assertEqual(self.handled(same)[0], 2)
        self.assertEqual(self.handled(same, user=True, evidence="")[0], 2)
        self.assertEqual(self.handled(same, user=True)[0], 0)
        again = self.ok_tail(changed, base=same["tailId"])
        self.assertTrue(again["settled"])

    def test_malformed_input_and_foreign_base_never_append(self):
        good = self.snapshot()
        malformed = [{}, dict(good, complete=1), dict(good, anchorUnique="yes"), dict(good, messages="x"),
                     dict(good, anchor={}), dict(good, messages=[{"type": "unknown", "text": "隐私"}]),
                     dict(good, bridge=[self.message(speaker="stranger")]), dict(good, bridgeComplete=True),
                     dict(good, unexpected="不允许落盘"), dict(good, messages=[self.message(id="fake-id")])]
        before = ledger.events_path(self.ws).read_bytes()
        for body in malformed:
            with self.subTest(body=body):
                code, out = self.tail(body)
                self.assertEqual(code, 2, out)
                self.assertEqual(ledger.events_path(self.ws).read_bytes(), before)
        self.assertEqual(self.tail(base="tail-000000000000")[0], 2)
        self.assertEqual(ledger.events_path(self.ws).read_bytes(), before)
        self.assertEqual(self.append(kind="observe", observed="resume_requested", company="另一公司")[0], 0)
        foreign = self.ok_tail(thread="boss|boss-main|另一公司|招聘者(hr)")
        before = ledger.events_path(self.ws).read_bytes()
        self.assertEqual(self.tail(base=foreign["tailId"])[0], 2)
        self.assertEqual(ledger.events_path(self.ws).read_bytes(), before)

    def test_absent_evidence_is_false_and_start_does_not_hide_recorded_outbound(self):
        body = self.snapshot()
        del body["complete"]
        del body["anchorUnique"]
        result = self.ok_tail(body)
        self.assertEqual(result["result"], "mismatch")
        self.assertEqual(self.handled(result, user=True)[0], 2)
        self.send()
        result = self.ok_tail(self.snapshot(anchor="start"), base=result["tailId"])
        self.assertEqual(result["result"], "mismatch")
        self.assertEqual(result["reason"], "start_conflicts_with_sent")

    def test_fully_read_conversation_start_can_be_verified(self):
        first = self.ok_tail(self.snapshot(anchor="start"))
        again = self.ok_tail(self.snapshot(anchor="start"), base=first["tailId"])
        self.assertTrue(again["settled"])
        self.assertEqual(self.handled(again)[0], 0)

    def test_handled_latest_snapshot_same_thread_and_base_only(self):
        first = self.ok_tail()
        again = self.ok_tail(base=first["tailId"])
        newer = self.ok_tail(base=again["tailId"])
        self.assertEqual(self.handled(again)[0], 2)
        self.assertEqual(self.append(kind="observe", observed="handled", tailId=newer["tailId"], baseTailId=None)[0], 2)
        self.assertEqual(self.append(kind="observe", observed="handled", company="其他公司", tailId=newer["tailId"], baseTailId=newer["baseTailId"])[0], 2)
        self.assertEqual(self.handled(newer)[0], 0)

    def test_pending_tail_without_explicit_base_cannot_restart_cleanly(self):
        first = self.ok_tail()
        restarted = self.ok_tail()
        self.assertFalse(restarted["settled"])
        self.assertEqual(restarted["reason"], "pending_base_required")
        same = self.ok_tail(base=restarted["tailId"])
        self.assertFalse(same["settled"])
        self.assertEqual(self.handled(same, user=True)[0], 0)

    def test_changed_anchor_complete_bridge_settles_without_persisting_raw(self):
        base = self.ok_tail()
        aid = self.send()
        result = self.ok_tail(self.bridge_body(aid), base=base["tailId"])
        self.assertEqual((result["result"], result["settled"]), ("new_anchor", True))
        self.assertEqual(self.handled(result)[0], 0)
        raw = ledger.events_path(self.ws).read_text(encoding="utf-8")
        self.assertNotIn("私密HR原文A", raw)
        self.assertNotIn("私密我方锚点", raw)
        events, errors = ledger.read_events(self.ws)
        self.assertFalse(errors)
        self.assertTrue(events[-2]["bridgeComplete"])
        self.assertNotIn("text", events[-2]["bridge"][0])

    def test_identical_hr_after_reply_is_new_anchor_not_same_message(self):
        base = self.ok_tail()
        aid = self.send()
        body = self.bridge_body(aid, [self.message()])
        body["bridge"].append(dict(self.message(), speaker="hr"))
        result = self.ok_tail(body, base=base["tailId"])
        self.assertEqual((result["result"], result["newCount"], result["settled"]), ("new_anchor", 1, False))
        verified = self.ok_tail(self.snapshot([self.message()], anchor=body["anchor"]), base=result["tailId"])
        self.assertTrue(verified["settled"])

    def test_new_hr_before_send_remains_review_even_when_new_anchor_tail_empty(self):
        base = self.ok_tail()
        aid = self.send()
        body = self.bridge_body(aid)
        body["bridge"].insert(2, self.message("发送之前新到的秘密", "今天 11:59", speaker="hr"))
        result = self.ok_tail(body, base=base["tailId"])
        self.assertFalse(result["settled"])
        again = self.ok_tail(self.snapshot([], anchor=body["anchor"]), base=result["tailId"])
        self.assertFalse(again["settled"])
        self.assertEqual(self.handled(again)[0], 2)
        self.assertEqual(self.handled(again, user=True)[0], 0)
        self.assertNotIn("发送之前新到的秘密", ledger.events_path(self.ws).read_text(encoding="utf-8"))

    def test_incomplete_or_forged_bridge_never_settles_and_is_sticky(self):
        base = self.ok_tail()
        aid = self.send()
        correct = self.bridge_body(aid)
        bodies = [self.snapshot([], anchor=correct["anchor"]), dict(correct, bridgeComplete=False)]
        for field, value in (("attemptId", "stranger"), ("text", "伪造正文"), ("label", "今天 09:00"), ("type", "card")):
            bad = json.loads(json.dumps(correct))
            bad["bridge"][-1][field] = value
            bodies.append(bad)
        repeated = json.loads(json.dumps(correct))
        repeated["bridge"].insert(1, repeated["bridge"][0])
        bodies.append(repeated)
        for body in bodies:
            with self.subTest(body=body):
                result = self.ok_tail(body, base=base["tailId"])
                self.assertFalse(result["settled"])
                self.assertEqual(self.handled(result)[0], 2)
        again = self.ok_tail(self.snapshot([], anchor=correct["anchor"]), base=result["tailId"])
        self.assertFalse(again["settled"])

    def test_other_target_and_uncertain_attempt_cannot_prove_a_bridge(self):
        base = self.ok_tail()
        aid = self.send(company="其他公司")
        result = self.ok_tail(self.bridge_body(aid), base=base["tailId"])
        self.assertFalse(result["settled"])
        code, pending = self.history(kind="reply", status="pending", text="本人批准的 回复")
        self.assertEqual(code, 0, pending)
        aid = pending["event"]["attemptId"]
        self.assertEqual(self.append(kind="reply", status="uncertain", attemptId=aid)[0], 0)
        result = self.ok_tail(self.bridge_body(aid), base=base["tailId"])
        self.assertFalse(result["settled"])
        self.assertEqual(result["reason"], "unresolved_send")
        self.assertEqual(self.handled(result)[0], 2)

    def test_stable_platform_id_is_identity_even_if_display_text_and_time_change(self):
        self.identity["platform"] = "testplat"
        self.assertEqual(self.append(kind="observe", observed="resume_requested")[0], 0)
        thread = "testplat|test-main|合成公司|招聘者(hr)"
        body = self.snapshot([self.message("仅用于输入的秘密", None, id="hr-1")],
                             anchor=self.message("另一个秘密", None, speaker="us", id="us-1"))
        first = self.ok_tail(body, thread=thread)
        self.assertEqual(self.handled(first, user=True)[0], 0)
        body["messages"][0]["text"] = "变更后的显示"
        body["messages"][0]["label"] = "无法确定"
        same = self.ok_tail(body, base=first["tailId"], thread=thread)
        self.assertEqual((same["result"], same["settled"]), ("same", True))
        event = ledger.read_events(self.ws)[0][-1]
        self.assertEqual(event["fingerprints"], [{"id": "hr-1"}])
        self.assertNotIn("仅用于输入的秘密", ledger.events_path(self.ws).read_text(encoding="utf-8"))

    def test_v1_fields_errors_decision_versions_and_no_self_event_id(self):
        cases = [dict(kind="screen", result="bad", reasons=[]), dict(kind="screen", result="qualified"),
                 dict(kind="greet", status="skipped", asks=["salary"]),
                 dict(kind="reply", status="skipped", checks=["hours.time"]),
                 dict(kind="observe", observed="info", facts={"unknown": "值"}),
                 dict(kind="observe", observed="interview_invited"),
                 dict(kind="observe", observed="tail"),
                 dict(kind="observe", observed="replied", eventId="evt-000000000000"),
                 dict(kind="observe", observed="replied", threadKey="wrong"),
                 dict(kind="observe", observed="replied", schemaVersion=2)]
        for fields in cases:
            with self.subTest(fields=fields):
                before = ledger.events_path(self.ws).read_bytes()
                self.assertEqual(self.append(**fields)[0], 2)
                self.assertEqual(ledger.events_path(self.ws).read_bytes(), before)
        d = "D-20261104-01"
        step = {"step": "reply1", "kind": "reply", "text": " \n原样 全角Ａ\n "}
        code, out = self.append(kind="observe", observed="decision", decision=d, version=1, steps=[step])
        self.assertEqual(code, 0, out)
        self.assertEqual(out["event"]["steps"][0]["text"], step["text"])
        self.assertEqual(self.append(kind="observe", observed="decision", decision=d, version=3, steps=[])[0], 2)
        self.assertEqual(self.append(kind="observe", observed="decision", decision=d, version=2, steps=[step])[0], 2)
        self.assertEqual(self.append(kind="observe", observed="decision", decision=d, version=2,
                                    steps=[{"step": "resume1", "kind": "resume", "resume": "v1"}])[0], 0)
        self.assertEqual(self.run_cli("decision", "--id", d)[0], 0)
        self.assertEqual(self.run_cli("threads")[0], 0)

    def test_generated_keys_escaping_nulls_and_revision_canonicalization(self):
        code, out = self.append(kind="screen", result="excluded", reasons=[], company=" Ａ | 公司 ",
                                title=" 开发 | 岗位 ", recruiter=" 王 | HR ")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["event"]["threadKey"], "boss|boss-main|a｜公司|王｜hr")
        self.assertEqual(out["event"]["jobKey"], "boss|a｜公司|开发｜岗位")
        code, out = self.append(kind="observe", observed="resume_requested", company=None, title=None, recruiter=None)
        self.assertEqual(code, 0, out)
        self.assertIsNone(out["event"]["threadKey"])
        self.assertIsNone(out["event"]["jobKey"])
        self.assertEqual(ledger.config_rev({"b": 2, "a": "中"}), ledger.config_rev({"a": "中", "b": 2}))
        self.assertRegex(ledger.config_rev({}), r"^[0-9a-f]{12}$")

    def test_repeated_new_anchor_with_two_confirmed_sends_is_still_ambiguous(self):
        base = self.ok_tail()
        first, second = self.send(), self.send()
        body = self.bridge_body(first)
        repeated = dict(body["bridge"][-1], attemptId=second)
        body["bridge"].append(repeated)
        body["anchor"] = repeated
        result = self.ok_tail(body, base=base["tailId"])
        self.assertFalse(result["settled"])
        self.assertEqual(self.handled(result)[0], 2)

    def test_all_action_and_observation_kinds_info_and_invitation_fields(self):
        for kind in sorted(ledger.ACTION_KINDS):
            code, out = self.append(kind=kind, status="skipped", asks=["salary.range"], targetRole="开发")
            self.assertEqual(code, 0, out)
            self.assertEqual(out["event"]["kind"], kind)
        for observed in ("resume_requested", "interview_decided", "silent"):
            self.assertEqual(self.append(kind="observe", observed=observed)[0], 0)
        self.assertEqual(self.append(kind="observe", observed="info", facts={"hours.schedule": "双休"})[0], 0)
        code, out = self.append(kind="observe", observed="interview_invited", decision="D-20261104-03")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["event"]["threadKey"], self.thread)
        self.assertEqual(self.append(kind="followup", status="skipped")[0], 2)
        self.assertEqual(self.append(kind="observe", observed="info", company=None, title=None, facts={})[0], 2)

    def test_custom_conversation_key_and_job_id_are_derived_from_platform(self):
        data = json.loads((ROOT / "tests/fixtures/platforms/testplat/platform.json").read_text(encoding="utf-8"))
        data["conversationKey"] = ["company", "title"]
        directory = self.ws / "test-platforms/testplat"
        directory.mkdir(parents=True)
        (directory / "platform.json").write_text(json.dumps(data), encoding="utf-8")
        with patch.dict(os.environ, {"JOB_MATE_TEST_PLATFORM_ROOT": str(directory.parent)}):
            code, out = self.append(kind="screen", platform="testplat", recruiter=None, result="pending", reasons=[],
                                    jobUrl="https://jobs.example.invalid/jobs/Job-12?keep=yes")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["event"]["jobKey"], "testplat|Job-12")
        self.assertEqual(out["event"]["threadKey"], "testplat|test-main|合成公司|开发")

    def test_time_and_base_order_and_handled_payload_cannot_be_forged(self):
        first = self.ok_tail()
        os.environ["JOB_MATE_NOW"] = "2026-11-04T12:01:00+08:00"
        last = self.ok_tail(base=first["tailId"])
        before = ledger.events_path(self.ws).read_bytes()
        os.environ["JOB_MATE_NOW"] = "2026-11-04T12:00:00+08:00"
        self.assertEqual(self.tail(base=first["tailId"])[0], 2)
        self.assertEqual(ledger.events_path(self.ws).read_bytes(), before)
        os.environ["JOB_MATE_NOW"] = "2026-11-04T12:01:00+08:00"
        for payload in ({"text": "不应保存的HR原文"}, {"fingerprints": []}, {"listMark": {"summary": "私密摘要"}}):
            self.assertEqual(self.handled(last, **payload)[0], 2)
        mark = {"label": "今天", "digest": "a" * 16, "unread": 0}
        code, out = self.handled(last, listMark=mark)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["event"]["listMark"], mark)
        self.assertEqual(out["event"]["fingerprints"], ledger.tail_state(ledger.read_events(self.ws)[0], ("boss", "boss-main", self.thread))["latest"]["fingerprints"])

    def test_card_send_can_prove_a_bridge_but_old_or_mismatched_time_cannot(self):
        base = self.ok_tail()
        code, out = self.history(kind="resume", status="pending", resume="版本A")
        self.assertEqual(code, 0, out)
        aid = out["event"]["attemptId"]
        self.assertEqual(self.append(kind="resume", status="sent", attemptId=aid, resume="版本A")[0], 0)
        card = dict(type="card", text="版本A", label="今天 12:00", speaker="us", attemptId=aid)
        body = self.snapshot([], anchor=card, bridge=[self.snapshot()["anchor"], dict(self.message(), speaker="hr"), card], bridgeComplete=True)
        result = self.ok_tail(body, base=base["tailId"])
        self.assertTrue(result["settled"])
        self.assertEqual(self.handled(result)[0], 0)
        self.assertNotIn("私密HR原文A", json.dumps(result, ensure_ascii=False))

    def test_invalid_scalar_and_step_types_return_errors_not_tracebacks(self):
        values = [dict(kind={}), dict(kind="observe", observed=[]), dict(kind="reply", status={}),
                  dict(kind="observe", observed="decision", decision="D-20261104-03", version=True, steps=[]),
                  dict(kind="observe", observed="decision", decision="D-20261104-03", version=1,
                       steps=[{"step": "s", "kind": {}, "text": "原话"}]),
                  dict(kind="observe", observed="replied", hrAt="no-zone")]
        for fields in values:
            with self.subTest(fields=fields):
                self.assertEqual(self.append(**fields)[0], 2)

    def test_acknowledged_groups_are_absolute_and_cross_day_stable(self):
        code, out = self.append(kind="observe", observed="released", skill="user",
            acknowledged=[{"shown": "今天 10:32", "precision": "minute", "kind": "text", "count": 1}])
        self.assertEqual(code, 0, out)
        self.assertEqual(out["event"]["acknowledged"][0]["shown"], "2026-11-04 10:32")
        os.environ["JOB_MATE_NOW"] = "2026-11-05T12:00:00+08:00"
        code, out = self.append(kind="observe", observed="released", skill="user",
            acknowledged=[{"shown": "昨天 10:32", "precision": "minute", "kind": "text", "count": 1}])
        self.assertEqual(code, 0, out)
        self.assertEqual(out["event"]["acknowledged"][0]["shown"], "2026-11-04 10:32")
        self.assertEqual(self.append(kind="observe", observed="released", skill="user",
            acknowledged=[{"shown": "10:32", "precision": "minute", "kind": "text", "count": 1}])[0], 2)
        code, out = self.append(kind="observe", observed="released", skill="user", readAt="2026-11-04T12:00:00+08:00",
            acknowledged=[{"shown": "今天", "precision": "day", "kind": "text", "count": 1}])
        self.assertEqual(code, 0, out)
        self.assertEqual(out["event"]["acknowledged"][0]["shown"], "2026-11-04")

    def test_fractional_read_time_is_valid_and_future_or_wrong_run_never_append(self):
        os.environ["JOB_MATE_NOW"] = "2026-11-04T12:00:00.500000+08:00"
        self.assertEqual(self.append(kind="observe", observed="adopted", skill="user",
                                     readAt=os.environ["JOB_MATE_NOW"])[0], 0)
        result = self.ok_tail()
        before = ledger.events_path(self.ws).read_bytes()
        body = json.dumps(self.snapshot(), ensure_ascii=False)
        for run, when in (("wrong-run", os.environ["JOB_MATE_NOW"]),
                          ("run-tail", "2026-11-04T12:00:01+08:00")):
            code, out = self.run_cli("tail", "--run", run, "--thread", self.thread, "--read-at", when,
                                     "--base", result["tailId"], "--json", body)
            self.assertEqual(code, 2, out)
            self.assertEqual(ledger.events_path(self.ws).read_bytes(), before)

    def test_real_concurrent_cli_reads_have_distinct_ids_and_only_latest_can_be_handled(self):
        base = self.baseline()
        argv = [sys.executable, "-I", str(ROOT / "scripts/ledger.py"), "--workspace", str(self.ws), "tail",
                "--run", "run-tail", "--thread", self.thread, "--read-at", os.environ["JOB_MATE_NOW"],
                "--base", base["tailId"], "--json", json.dumps(self.snapshot(), ensure_ascii=False)]
        children = [subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8") for _ in range(2)]
        try:
            results = []
            for child in children:
                out, error = child.communicate(timeout=10)
                self.assertEqual(child.returncode, 0, error + out)
                self.assertNotIn("私密HR原文A", out)
                results.append(json.loads(out))
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.communicate()
        self.assertNotEqual(results[0]["tailId"], results[1]["tailId"])
        latest = ledger.tail_state(ledger.read_events(self.ws)[0], ("boss", "boss-main", self.thread))["latest"]["tailId"]
        for result in results:
            code, out = self.handled(result)
            self.assertEqual(code, 0 if result["tailId"] == latest else 2, out)

    def test_nfkc_and_whitespace_only_affect_fingerprint_not_approved_text(self):
        first = self.ok_tail(self.snapshot([self.message("Ａ  \n B")]))
        same = self.ok_tail(self.snapshot([self.message("A B")]), base=first["tailId"])
        self.assertTrue(same["settled"])
        self.assertEqual(ledger.content_digest("Ａ  \n B"), ledger.content_digest("A B"))
        code, out = self.append(kind="observe", observed="resume_requested", company="   ")
        self.assertEqual(code, 0, out)
        self.assertIsNone(out["event"]["threadKey"])
        self.assertIsNone(out["event"]["jobKey"])

    def test_bridge_cannot_use_a_retargeted_terminal_as_identity_proof(self):
        base = self.ok_tail()
        code, out = self.history(kind="reply", status="pending", recruiter="另一个招聘者(HR)", text="本人批准的 回复")
        self.assertEqual(code, 0, out)
        aid = out["event"]["attemptId"]
        # append 的完整身份冻结留给 T5.3；此处只检验 bridge 不信冲突记录。
        code, out = self.history(kind="reply", status="sent", attemptId=aid, text="本人批准的 回复")
        self.assertEqual(code, 0, out)
        result = self.ok_tail(self.bridge_body(aid), base=base["tailId"])
        self.assertFalse(result["settled"])
        self.assertEqual(result["reason"], "bridge_target_mismatch")

    def test_job_id_cannot_contradict_or_suppress_a_readable_url(self):
        url = "https://jobs.example.invalid/jobs/Real-12"
        code, out = self.append(kind="screen", platform="testplat", result="pending", reasons=[], jobUrl=url, jobId=None)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["event"]["jobKey"], "testplat|Real-12")
        before = ledger.events_path(self.ws).read_bytes()
        self.assertEqual(self.append(kind="screen", platform="testplat", result="pending", reasons=[], jobUrl=url, jobId="wrong")[0], 2)
        self.assertEqual(ledger.events_path(self.ws).read_bytes(), before)
        code, out = self.run_cli("check", "--platform", "testplat", "--company", "合成公司", "--job-url", url, "--job-id", "wrong")
        self.assertEqual(code, 2, out)

    def test_old_run_sent_cannot_prove_current_run_bridge(self):
        base = self.ok_tail()
        aid = self.send()
        self.assertEqual(self.run_cli("lock", "release", "--run", "run-tail")[0], 0)
        self.assertEqual(self.run_cli("lock", "acquire", "--skill", "followup", "--run", "run-next")[0], 0)
        code, out = self.run_cli("tail", "--run", "run-next", "--thread", self.thread,
            "--read-at", os.environ["JOB_MATE_NOW"], "--base", base["tailId"],
            "--json", json.dumps(self.bridge_body(aid)))
        self.assertEqual(code, 0, out)
        self.assertFalse(out["settled"])
        self.assertEqual(out["reason"], "bridge_unconfirmed_send")
        self.assertEqual(self.handled(out)[0], 2)

    def test_recovery_lock_cannot_record_screen_or_tail_and_read_remains_available(self):
        self.run_cli("lock", "release", "--run", "run-tail")
        self.assertEqual(self.run_cli("lock", "acquire", "--skill", "setup", "--recovery", "--run", "run-tail")[0], 0)
        before = ledger.events_path(self.ws).read_bytes()
        for code, out in (self.append(kind="screen", result="pending", reasons=[]), self.tail()):
            self.assertEqual((code, out.get("code")), (2, "recovery_lock_only"), out)
        self.assertEqual(ledger.events_path(self.ws).read_bytes(), before)
        self.assertEqual(self.run_cli("threads")[0], 0)

    def test_reader_never_treats_an_inflight_locked_append_as_corruption(self):
        event = ledger.prepare_event(dict(self.identity, schemaVersion=1, account="boss-main", runId="run-tail",
                    skill="followup", kind="observe", observed="resume_requested", at=os.environ["JOB_MATE_NOW"],
                    attemptId="att-reader-test", reason="合成追加", evidence=None))
        writer_code = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import fsutil
ws, payload = Path(sys.argv[2]), sys.argv[3]
with fsutil.locked(ws / "ledger/.events.mutex"):
    with (ws / "ledger/events.jsonl").open("a", encoding="utf-8") as out:
        half = len(payload) // 2
        out.write(payload[:half]); out.flush()
        print("ready", flush=True)
        assert input() == "continue"
        out.write(payload[half:] + "\\n"); out.flush()
'''
        reader_code = '''import sys, json, contextlib
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import ledger
real = ledger.file_mutex
@contextlib.contextmanager
def watched(path):
    print("waiting", flush=True)
    with real(path):
        yield
ledger.file_mutex = watched
events, errors = ledger.read_events(Path(sys.argv[2]), strict=False)
print(json.dumps({"count": len(events), "errors": errors}), flush=True)
'''
        writer = subprocess.Popen([sys.executable, "-I", "-c", writer_code, str(ROOT / "scripts"), str(self.ws), json.dumps(event)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        children, readers = [writer], []
        def checkpoint(child):
            channel = queue.Queue()
            reader = threading.Thread(target=lambda: channel.put(child.stdout.readline()), daemon=True)
            readers.append(reader)
            reader.start()
            result = channel.get(timeout=10)
            reader.join(timeout=5)
            return result.strip()
        try:
            self.assertEqual(checkpoint(writer), "ready")
            reader = subprocess.Popen([sys.executable, "-I", "-c", reader_code, str(ROOT / "scripts"), str(self.ws)],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
            children.append(reader)
            self.assertEqual(checkpoint(reader), "waiting")
            writer.stdin.write("continue\n"); writer.stdin.flush()
            out, error = writer.communicate(timeout=10)
            self.assertEqual(writer.returncode, 0, error)
            out, error = reader.communicate(timeout=10)
            self.assertEqual(reader.returncode, 0, error)
            self.assertEqual(json.loads(out), {"count": 2, "errors": []})
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.communicate()
            for reader in readers:
                reader.join(timeout=5)

    def patch_tail(self, tail, **changes):
        path = ledger.events_path(self.ws)
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line]
        for row in rows:
            if row.get("tailId") == tail["tailId"] and row.get("observed") == "tail":
                row.update(changes)
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    def assert_old_base_does_not_drop_withdrawn_pending(self, cross_run=False):
        old = self.baseline()
        extended = self.ok_tail(self.snapshot([self.message(), self.message("待处理B", "今天 11:00")]))
        self.assertEqual(extended["result"], "extended")
        run = "run-tail"
        if cross_run:
            self.assertEqual(self.run_cli("lock", "release", "--run", run)[0], 0)
            run = "run-next"
            self.assertEqual(self.run_cli("lock", "acquire", "--skill", "followup", "--run", run)[0], 0)
        current = self.ok_tail(base=old["tailId"], run=run)
        self.assertFalse(current["settled"])
        self.assertEqual(self.handled(current, runId=run)[0], 2)
        again = self.ok_tail(base=current["tailId"], run=run)
        self.assertFalse(again["settled"])
        self.assertTrue(ledger.tail_state(ledger.read_events(self.ws)[0], ("boss", "boss-main", self.thread))["pending"])
        self.assertEqual(self.handled(again, user=True, runId=run)[0], 0)

    def test_old_handled_base_cannot_hide_withdrawn_pending_extension(self):
        self.assert_old_base_does_not_drop_withdrawn_pending()

    def test_old_handled_base_cannot_hide_pending_extension_across_runs(self):
        self.assert_old_base_does_not_drop_withdrawn_pending(cross_run=True)

    def test_old_base_is_safe_when_intermediate_pending_read_is_same(self):
        base = self.baseline()
        same = self.ok_tail()
        self.assertEqual(same["result"], "same")
        verified = self.ok_tail(base=base["tailId"])
        self.assertTrue(verified["settled"])
        self.assertEqual(self.handled(verified)[0], 0)
        self.assertEqual(self.run_cli("validate")[0], 0)

    def test_processed_extension_can_settle_with_complete_bridge(self):
        self.baseline()
        extra = self.message("实际处理B", "今天 11:00")
        extended = self.ok_tail(self.snapshot([self.message(), extra]))
        aid = self.send()
        body = self.bridge_body(aid)
        body["bridge"].insert(2, dict(extra, speaker="hr"))
        verified = self.ok_tail(body, base=extended["tailId"])
        self.assertTrue(verified["settled"])
        self.assertEqual(self.handled(verified)[0], 0)
        same = self.ok_tail(self.snapshot([], anchor=body["anchor"]), base=verified["tailId"])
        self.assertTrue(same["settled"])
        self.assertEqual(self.handled(same)[0], 0)
        self.assertEqual(self.run_cli("validate")[0], 0)

    def test_approved_text_roundtrips_all_unicode_line_separators(self):
        text = " \t全角Ａ" + "|".join("前" + char + "后" for char in
                    ("\u2028", "\u2029", "\u0085", "\v", "\f", "\x1c", "\x1d", "\x1e", "\r", "\n", "\r\n")) + "\n "
        decision = "D-20261104-08"
        code, out = self.append(kind="observe", observed="decision", decision=decision, version=1,
                               steps=[{"step": "verbatim", "kind": "reply", "text": text}])
        self.assertEqual(code, 0, out)
        self.assertEqual(out["event"]["steps"][0]["text"], text)
        code, out = self.run_cli("validate")
        self.assertEqual(code, 0, out)
        rows, errors = ledger.read_events(self.ws)
        self.assertFalse(errors)
        self.assertEqual(rows[-1]["steps"][0]["text"], text)
        self.assertEqual(self.run_cli("decision", "--id", decision)[0], 0)
        self.assertEqual(ledger.read_events(self.ws)[0][-1]["steps"][0]["text"], text)

    def test_history_rejects_same_and_extended_inconsistent_with_fingerprints(self):
        base = self.baseline()
        wrong = self.ok_tail(self.snapshot([self.message("不同B")]), base=base["tailId"])
        original = ledger.events_path(self.ws).read_bytes()
        for result, settled, count in (("same", True, 0), ("extended", False, 1)):
            with self.subTest(result=result):
                self.patch_tail(wrong, result=result, settled=settled, reviewRequired=False, reason=result, newCount=count)
                code, out = self.run_cli("validate")
                self.assertEqual(code, 2, out)
                self.assertEqual(out["errors"][0]["line"], 4)
                before = ledger.events_path(self.ws).read_bytes()
                self.assertEqual(self.handled(wrong)[0], 2)
                self.assertEqual(ledger.events_path(self.ws).read_bytes(), before)
                ledger.events_path(self.ws).write_bytes(original)

    def test_history_rejects_cleared_sticky_review_even_without_settled(self):
        base = self.baseline()
        body = self.snapshot([self.message("编辑后B")])
        mismatch = self.ok_tail(body, base=base["tailId"])
        same = self.ok_tail(body, base=mismatch["tailId"])
        original = ledger.events_path(self.ws).read_bytes()
        for settled in (False, True):
            with self.subTest(settled=settled):
                self.patch_tail(same, settled=settled, reviewRequired=False, newCount=0)
                self.assertEqual(self.run_cli("validate")[0], 2)
                self.assertEqual(self.handled(same)[0], 2)
                ledger.events_path(self.ws).write_bytes(original)

    def test_history_rejects_handled_referencing_polluted_snapshot(self):
        base = self.ok_tail()
        verified = self.ok_tail(base=base["tailId"])
        self.assertEqual(self.handled(verified)[0], 0)
        fp = ledger.read_events(self.ws)[0][-2]["fingerprints"][0].copy()
        fp["digest"] = "0" * 16
        self.patch_tail(verified, fingerprints=[fp])
        code, out = self.run_cli("validate")
        self.assertEqual(code, 2, out)
        self.assertEqual([error["line"] for error in out["errors"]], [3, 4])
        valid, _ = ledger.read_events(self.ws, strict=False)
        self.assertFalse(any(ev.get("observed") == "handled" for ev in valid))

    def test_history_rechecks_valid_bridge_and_rejects_changed_hr_fingerprint(self):
        base = self.ok_tail()
        verified = self.ok_tail(self.bridge_body(self.send()), base=base["tailId"])
        self.assertTrue(verified["settled"])
        self.assertEqual(self.run_cli("validate")[0], 0)
        bridge = ledger.read_events(self.ws)[0][-1]["bridge"]
        bridge[1] = dict(bridge[1], digest="0" * 16)
        self.patch_tail(verified, bridge=bridge)
        self.assertEqual(self.run_cli("validate")[0], 2)
        self.assertEqual(self.handled(verified)[0], 2)

    def test_history_rechecks_bridge_with_stable_ids_without_hr_raw_text(self):
        self.identity["platform"] = "testplat"
        self.assertEqual(self.append(kind="observe", observed="resume_requested")[0], 0)
        thread = "testplat|test-main|合成公司|招聘者(hr)"
        anchor = self.message("仅输入的旧我方内容", "today 10:00", speaker="us", id="us-old")
        hr = self.message("仅输入的HR内容", "today 10:32", id="hr-A")
        base = self.ok_tail(self.snapshot([hr], anchor=anchor), thread=thread)
        aid = self.send()
        sent = self.message("本人批准的 回复", "today 12:00", speaker="us", id="us-new", attemptId=aid)
        body = self.snapshot([], anchor=sent, bridge=[anchor, dict(hr, speaker="hr"), sent], bridgeComplete=True)
        verified = self.ok_tail(body, base=base["tailId"], thread=thread)
        self.assertTrue(verified["settled"])
        self.assertEqual(self.run_cli("validate")[0], 0)
        raw = ledger.events_path(self.ws).read_text(encoding="utf-8")
        self.assertNotIn(hr["text"], raw)
        saved = ledger.read_events(self.ws)[0][-1]
        original = ledger.events_path(self.ws).read_bytes()
        proof = saved["bridgeEvidence"]
        proof[-1]["digest"] = "0" * 16
        self.patch_tail(verified, bridgeEvidence=proof)
        self.assertEqual(self.run_cli("validate")[0], 2)
        ledger.events_path(self.ws).write_bytes(original)
        bridge = saved["bridge"]
        bridge[1] = {"id": "hr-unhandled"}
        self.patch_tail(verified, bridge=bridge)
        self.assertEqual(self.run_cli("validate")[0], 2)
        ledger.events_path(self.ws).write_bytes(original)
        self.assertEqual(self.handled(verified)[0], 0)

    def test_history_rejects_corrupt_bridge_sender_proof(self):
        base = self.ok_tail()
        verified = self.ok_tail(self.bridge_body(self.send()), base=base["tailId"])
        saved = ledger.read_events(self.ws)[0][-1]
        original = ledger.events_path(self.ws).read_bytes()
        for field, value in (("attemptId", "unknown"), ("digest", "0" * 16), ("type", "card"),
                             ("pageAt", "2026-11-04T09:00:00+08:00"), ("speaker", "hr")):
            with self.subTest(field=field):
                proof = json.loads(json.dumps(saved["bridgeEvidence"]))
                proof[-1][field] = value
                self.patch_tail(verified, bridgeEvidence=proof)
                self.assertEqual(self.run_cli("validate")[0], 2)
                self.assertEqual(self.handled(verified)[0], 2)
                ledger.events_path(self.ws).write_bytes(original)
        self.patch_tail(verified, bridgeEvidence=None)
        self.assertEqual(self.run_cli("validate")[0], 2)
        ledger.events_path(self.ws).write_bytes(original)
        # 历史用冻结的页面绝对时间，不随当前时区重新解释旧标签。
        with patch.object(ledger, "tz_of", return_value=ledger.parse_at("2026-11-04T00:00:00+00:00").tzinfo):
            self.assertEqual(self.run_cli("validate")[0], 0)
        self.assertEqual(self.handled(verified)[0], 0)

    def test_history_cannot_clear_review_while_a_send_is_unresolved(self):
        base = self.ok_tail()
        self.assertEqual(self.history(kind="reply", status="pending", text="合成待核对发送")[0], 0)
        current = self.ok_tail(base=base["tailId"])
        self.assertFalse(current["settled"])
        self.patch_tail(current, reviewRequired=False, settled=True, reason="same", newCount=0)
        self.assertEqual(self.run_cli("validate")[0], 2)
        self.assertEqual(self.handled(current)[0], 2)

    def test_unknown_bridge_attempt_input_is_not_copied_into_evidence(self):
        base = self.ok_tail()
        body = self.bridge_body(self.send())
        secret = "误填在attemptId中的HR私人原文"
        body["bridge"][-1]["attemptId"] = secret
        current = self.ok_tail(body, base=base["tailId"])
        self.assertFalse(current["settled"])
        self.assertEqual(current["reason"], "bridge_unconfirmed_send")
        self.assertNotIn(secret, ledger.events_path(self.ws).read_text(encoding="utf-8"))
        self.assertEqual(self.run_cli("validate")[0], 0)

    def test_history_checks_explicit_base_marker_as_well_as_results(self):
        self.baseline()
        current = self.ok_tail()
        self.assertFalse(current["settled"])
        original = ledger.events_path(self.ws).read_bytes()
        for value in (True, None, "yes"):
            with self.subTest(value=value):
                self.patch_tail(current, baseExplicit=value)
                self.assertEqual(self.run_cli("validate")[0], 2)
                ledger.events_path(self.ws).write_bytes(original)

    def test_validate_reports_new_field_line_number_and_duplicate_event_id(self):
        ev = dict(ledger.read_events(self.ws)[0][0])
        ev["eventId"] = "not-an-id"
        ledger.append_line(self.ws, ev)
        code, out = self.run_cli("validate")
        self.assertEqual(code, 2, out)
        self.assertEqual(out["errors"][0]["line"], 2)
        self.assertIn("eventId", out["errors"][0]["error"])
        ev["eventId"] = ledger.read_events(self.ws, strict=False)[0][0]["eventId"]
        ledger.append_line(self.ws, ev)
        code, out = self.run_cli("validate")
        self.assertEqual(out["errors"][-1]["line"], 3)
        self.assertIn("重复", out["errors"][-1]["error"])
        ev["schemaVersion"] = 2
        ev["eventId"] = "evt-111111111111"
        ledger.append_line(self.ws, ev)
        code, out = self.run_cli("validate")
        self.assertEqual(code, 2, out)
        self.assertEqual(out["errors"][-1]["line"], 4)
        self.assertIn("schemaVersion", out["errors"][-1]["error"])

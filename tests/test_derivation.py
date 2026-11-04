"""T4.3 只读派生：合成会话、测试时钟、真实 CLI 入口；不接触平台。"""
import json
import os
import unittest
from unittest.mock import patch

import test_tail

ledger = test_tail.ledger


class DerivationTest(unittest.TestCase):
    def setUp(self):
        # 组合复用 CLI 夹具，不继承/重复执行其安全测试。
        self.f = test_tail.TailTest()
        self.f.setUp()

    def tearDown(self):
        self.f.tearDown()

    def event(self, **fields):
        code, out = self.f.append(**fields)
        self.assertEqual(code, 0, out)
        return out["event"]

    def threads(self, *args):
        code, out = self.f.run_cli("threads", *args)
        self.assertEqual(code, 0, out)
        return out

    def thread(self):
        return next(t for t in self.threads()["threads"] if t["recruiter"] == self.f.identity["recruiter"])

    def profile(self, priority):
        ledger.write_atomic(self.f.ws / "profile.json", json.dumps({"inquiry": {"priority": priority}}))

    def action(self, final="sent", **fields):
        fields = {"kind": "reply", "text": "合成问题", **fields}
        pending = self.event(status="pending", **fields)
        if final:
            self.event(status=final, attemptId=pending["attemptId"], **fields)
        return pending["attemptId"]

    def decision(self, version=1, steps=None, decision="D-20261104-11", **fields):
        return self.event(kind="observe", observed="decision", decision=decision, version=version,
                          steps=steps if steps is not None else [{"step": "reply1", "kind": "reply", "text": "合成问题"}], **fields)

    def status(self, decision="D-20261104-11", *extra):
        code, out = self.f.run_cli("decision", "--id", decision, *extra)
        self.assertEqual(code, 0, out)
        return out

    def test_jd_subitems_checks_priority_and_hr_override(self):
        self.profile(["salary", "hours"])
        self.event(kind="screen", result="qualified", reasons=[], checks=["employment.type", "hours.time"],
                   jdFacts={"hours.schedule": "双休", "salary.range": "JD范围"})
        info = self.thread()["info"]
        self.assertEqual(info["known"]["hours.schedule"], "双休")
        self.assertEqual(info["pending"], ["employment.type", "hours.time", "salary.months"])
        self.event(kind="observe", observed="info", facts={"salary.range": "HR范围"})
        self.event(kind="screen", result="qualified", reasons=[], jdFacts={"salary.range": "后来的JD"})
        self.assertEqual(self.thread()["info"]["known"]["salary.range"], "HR范围")

    def test_new_job_recomputes_information_and_does_not_share_question_counts(self):
        self.profile(["hours"])
        self.event(kind="observe", observed="info", jobId="job-A", facts={"hours.schedule": "双休"})
        self.action(jobId="job-A", asks=["hours.time"])
        self.event(kind="observe", observed="replied", jobId="job-A")
        self.assertEqual(self.thread()["info"]["askedUnanswered"], {"hours.time": 1})
        self.event(kind="screen", jobId="job-B", result="pending", reasons=[], checks=["employment.type"])
        thread = self.thread()
        self.assertEqual(thread["currentJobKey"], "boss|job-B")
        self.assertEqual(thread["info"]["known"], {})
        self.assertEqual(thread["info"]["askedUnanswered"], {})
        self.assertEqual(thread["info"]["pending"], ["employment.type", "hours.schedule", "hours.time"])

    def test_screen_without_recruiter_still_supplies_same_job_jd(self):
        self.profile([])
        self.event(kind="screen", recruiter=None, jobId="shared-job", result="pending", reasons=[],
                   jdFacts={"hours.schedule": "双休"}, checks=["hours.time"])
        self.event(kind="observe", observed="replied", jobId="shared-job")
        self.assertEqual(self.thread()["info"]["pending"], ["hours.time"])
        self.assertEqual(self.thread()["info"]["known"], {"hours.schedule": "双休"})

    def test_two_questions_with_replies_give_up_not_multiple_replies_to_one_question(self):
        self.profile(["hours"])
        self.action(asks=["hours.time"])
        self.assertEqual(self.thread()["info"]["askedUnanswered"], {})
        for _ in range(3):
            self.event(kind="observe", observed="replied")
        self.assertEqual(self.thread()["info"]["askedUnanswered"], {"hours.time": 1})
        self.action(asks=["hours.time"])
        self.event(kind="observe", observed="replied")
        info = self.thread()["info"]
        self.assertEqual(info["askedUnanswered"], {"hours.time": 2})
        self.assertEqual(info["gaveUp"], ["hours.time"])
        self.assertNotIn("hours.time", info["pending"])
        self.event(kind="observe", observed="info", facts={"hours.time": "9至18点"})
        self.assertEqual(self.thread()["info"]["gaveUp"], [])
        self.assertEqual(self.thread()["info"]["askedUnanswered"], {})

    def test_only_confirmed_sends_count_and_asks_can_be_on_pending_record(self):
        self.profile(["hours"])
        self.action(final="failed_no_send", asks=["hours.time"])
        uncertain = self.action(final="uncertain", asks=["hours.time"])
        self.event(kind="observe", observed="replied")
        self.assertEqual(self.thread()["info"]["askedUnanswered"], {})
        self.event(kind="reply", status="sent", attemptId=uncertain, skill="user", text="合成问题")
        # 核对后才确认发送；此前的 HR 回复不猜成对此次问询的回应。
        self.assertEqual(self.thread()["info"]["askedUnanswered"], {})
        self.event(kind="observe", observed="replied")
        self.assertEqual(self.thread()["info"]["askedUnanswered"], {"hours.time": 1})

    def test_other_job_hr_reply_does_not_consume_question_round(self):
        self.profile(["hours"])
        self.action(jobId="job-A", asks=["hours.time"])
        self.event(kind="observe", observed="replied", jobId="job-B")
        self.event(kind="observe", observed="replied", jobId="job-A", recruiter="另一位(HR)")
        self.event(kind="screen", result="pending", reasons=[], jobId="job-A")
        self.assertEqual(self.thread()["info"]["askedUnanswered"], {})
        self.event(kind="observe", observed="replied", jobId="job-A")
        self.assertEqual(self.thread()["info"]["askedUnanswered"], {"hours.time": 1})

    def test_stage_is_monotonic_and_interview_intent_records_actual_send_stage(self):
        self.assertEqual(self.thread()["stage"], "replied")
        self.action(tags=["intent:interview"])
        self.event(kind="observe", observed="info", facts={"hours.schedule": "双休"})
        self.action(tags=["intent:interview"])
        self.event(kind="observe", observed="resume_viewed")
        self.action(tags=["intent:interview"])
        self.decision(steps=[])
        self.event(kind="observe", observed="interview_invited", decision="D-20261104-12")
        self.event(kind="screen", result="pending", reasons=[], jobId="different-job")
        self.assertEqual(self.thread()["stage"], "interview_invited")
        self.assertEqual(self.thread()["interviewIntentStages"], ["replied", "info", "resume"])

    def test_pending_tail_is_not_cleared_by_same_or_settled_without_handled(self):
        first = self.f.ok_tail()
        thread = self.thread()
        self.assertEqual(thread["threadKey"], self.f.thread)
        self.assertEqual(thread["account"], "boss-main")
        self.assertEqual(thread["pendingTail"]["tailId"], first["tailId"])
        verified = self.f.ok_tail(base=first["tailId"])
        self.assertTrue(verified["settled"])
        self.assertEqual(self.thread()["pendingTail"]["tailId"], verified["tailId"])
        self.assertEqual(self.f.handled(verified)[0], 0)
        self.assertIsNone(self.thread()["pendingTail"])
        self.assertEqual(self.threads()["pendingTails"], [])
        changed = self.f.ok_tail(self.f.snapshot([self.f.message(), self.f.message("新增B", "今天 11:00")]))
        pending = self.threads()["pendingTails"]
        self.assertEqual([item["pendingTail"]["tailId"] for item in pending], [changed["tailId"]])

    def test_last_handled_read_time_is_not_extended_by_unhandled_open(self):
        first = self.f.ok_tail()
        verified = self.f.ok_tail(base=first["tailId"])
        mark = {"label": "合成时间标签", "digest": "a" * 16, "unread": 0}
        self.assertEqual(self.f.handled(verified, listMark=mark)[0], 0)
        before = self.thread()["lastHandled"]
        os.environ["JOB_MATE_NOW"] = "2026-11-04T12:05:00+08:00"
        current = self.f.ok_tail()
        thread = self.thread()
        self.assertEqual(thread["lastHandled"], before)
        self.assertEqual(before["readAt"], "2026-11-04T12:00:00+08:00")
        self.assertEqual(before["listMark"], mark)
        self.assertTrue(before["complete"])
        self.assertEqual(thread["lastOpenedAt"], os.environ["JOB_MATE_NOW"])
        self.assertEqual(thread["pendingTail"]["tailId"], current["tailId"])

    def test_pending_projection_retains_sticky_reason_until_owner_closes(self):
        base = self.f.baseline()
        changed = self.f.snapshot([self.f.message("编辑B")])
        wrong = self.f.ok_tail(changed, base=base["tailId"])
        current = self.f.ok_tail(changed, base=wrong["tailId"])
        pending = self.thread()["pendingTail"]
        self.assertEqual(pending["result"], "same")
        self.assertFalse(pending["settled"])
        self.assertIn("content_or_order_changed", pending["reviewReasons"])
        self.assertEqual(self.f.handled(current, user=True)[0], 0)
        self.assertIsNone(self.thread()["pendingTail"])

    def test_versions_supersede_only_unsent_steps_and_match_threads(self):
        self.decision(steps=[{"step": "reply1", "kind": "reply", "text": "合成问题"},
                             {"step": "reply0", "kind": "reply", "text": "未开始"}])
        self.action(final="failed_no_send", decision="D-20261104-11", step="reply1")
        self.decision(2, [{"step": "reply2", "kind": "reply", "text": "新的原话"}])
        status = self.status()
        self.assertEqual(status["effectiveVersion"], 2)
        self.assertEqual(status["steps"], {"reply1": "superseded", "reply0": "superseded", "reply2": "not_started"})
        self.assertFalse(status["versions"][0]["active"])
        self.assertEqual(self.thread()["decisions"][0]["versions"], status["versions"])

    def test_old_unresolved_remains_invalidated_and_completion_cannot_hide_it(self):
        self.decision()
        aid = self.action(final="uncertain", decision="D-20261104-11", step="reply1")
        self.decision(2, [{"step": "reply2", "kind": "reply", "text": "新版"}])
        self.event(kind="observe", observed="unpaused", skill="user", decision="D-20261104-11")
        status = self.status()
        self.assertEqual(status["state"], "needs_review")
        step = status["versions"][0]["steps"][0]
        self.assertEqual(step["status"], "uncertain")
        self.assertEqual(step["attempts"][0]["attemptId"], aid)
        self.assertTrue(status["attempts"][0]["versionInvalidated"])
        self.event(kind="reply", status="sent", skill="user", attemptId=aid, decision="D-20261104-11", step="reply1")
        self.assertEqual(self.status()["steps"]["reply1"], "sent")
        self.assertNotEqual(self.status()["state"], "done")

    def test_versionless_action_binds_unique_registered_step_not_latest_version(self):
        self.decision()
        aid = self.action(final=None, decision="D-20261104-11", step="reply1")
        self.decision(2, [{"step": "reply2", "kind": "reply", "text": "新版"}])
        status = self.status()
        self.assertEqual(status["state"], "needs_review")
        self.assertEqual(status["attempts"][0]["decisionVersion"], 1)
        self.event(kind="reply", status="sent", attemptId=aid, decision="D-20261104-11", step="reply1")
        self.assertEqual(self.status()["steps"]["reply1"], "sent")
        self.assertEqual(self.status()["steps"]["reply2"], "not_started")

    def test_sent_does_not_hide_another_unresolved_attempt_for_the_same_step(self):
        self.decision()
        self.action(decision="D-20261104-11", step="reply1")
        self.action(final="uncertain", decision="D-20261104-11", step="reply1")
        status = self.status()
        self.assertEqual(status["steps"]["reply1"], "sent")
        self.assertEqual(status["state"], "needs_review")
        self.assertEqual([a["status"] for a in status["attempts"]], ["sent", "uncertain"])

    def test_old_completion_marker_does_not_finish_a_new_version(self):
        self.decision()
        self.action(decision="D-20261104-11", step="reply1")
        self.event(kind="observe", observed="unpaused", decision="D-20261104-11")
        self.assertEqual(self.status()["state"], "done")
        self.decision(2, [{"step": "reply2", "kind": "reply", "text": "新版"}])
        self.assertEqual(self.status()["state"], "not_started")

    def test_item_and_version_are_not_inferred_from_unregistered_or_conflicting_metadata(self):
        unknown = self.action(final=None, decision="D-20261104-11", step="unknown")
        self.assertEqual(self.status()["state"], "needs_review")
        self.assertIsNone(self.status()["attempts"][0]["decisionVersion"])
        self.event(kind="reply", status="failed_no_send", attemptId=unknown, decision="D-20261104-11", step="unknown")
        self.decision()
        self.action(decision="D-20261104-11", step="reply1", decisionVersion=2)
        self.assertEqual(self.status()["state"], "needs_review")
        self.assertTrue(self.status()["unmappedAttempts"])

    def test_same_step_name_in_different_decisions_is_independent(self):
        self.decision()
        self.decision(decision="D-20261104-12")
        self.action(decision="D-20261104-11", step="reply1")
        self.assertEqual(self.status()["steps"]["reply1"], "sent")
        self.assertEqual(self.status("D-20261104-12")["steps"]["reply1"], "not_started")
        self.assertEqual(len(self.thread()["decisions"]), 2)

    def test_registered_invitation_ack_is_not_a_versioned_user_step(self):
        self.event(kind="observe", observed="interview_invited", decision="D-20261104-11")
        self.action(decision="D-20261104-11", step="ack")
        status = self.status()
        self.assertIsNone(status["effectiveVersion"])
        self.assertEqual(status["versions"], [])
        self.assertEqual(status["systemAttempts"][0]["status"], "sent")
        self.assertEqual(status["state"], "not_started")

    def test_partial_info_reply_consumes_question_once_without_separate_replied(self):
        self.action(asks=["salary.range", "hours.time"])
        self.event(kind="observe", observed="info", facts={"salary.range": "已知范围"})
        self.assertEqual(self.thread()["info"]["askedUnanswered"], {"hours.time": 1})
        self.event(kind="observe", observed="replied")
        self.assertEqual(self.thread()["info"]["askedUnanswered"], {"hours.time": 1})

    def test_platform_filter_does_not_hide_other_target_pause_for_same_decision(self):
        self.decision()
        self.event(platform="testplat", kind="observe", observed="paused", decision="D-20261104-11")
        self.action(decision="D-20261104-11", step="reply1")
        self.event(kind="observe", observed="unpaused", decision="D-20261104-11")
        expected = self.status()
        self.assertNotEqual(expected["state"], "done")
        visible = self.threads("--platform", "boss")["threads"][0]["decisions"][0]
        self.assertEqual(visible["state"], expected["state"])
        self.assertEqual(visible["stillPausing"], expected["stillPausing"])

    def test_platform_conversation_dimensions_are_used_for_thread_projection(self):
        data = json.loads((test_tail.ROOT / "tests/fixtures/platforms/testplat/platform.json").read_text(encoding="utf-8"))
        data["conversationKey"] = ["company", "title"]
        directory = self.f.ws / "test-platforms/testplat"
        directory.mkdir(parents=True)
        (directory / "platform.json").write_text(json.dumps(data), encoding="utf-8")
        with patch.dict(os.environ, {"JOB_MATE_TEST_PLATFORM_ROOT": str(directory.parent)}):
            for title in ("岗位A", "岗位B"):
                self.event(platform="testplat", title=title, kind="observe", observed="replied")
            threads = self.threads("--platform", "testplat")["threads"]
            self.assertEqual(len(threads), 2)
            self.assertEqual({t["threadKey"] for t in threads},
                             {"testplat|test-main|合成公司|岗位a", "testplat|test-main|合成公司|岗位b"})

    def test_conflicting_target_is_not_claimed_as_executing_the_registered_step(self):
        self.decision()
        self.action(company="不同目标公司", decision="D-20261104-11", step="reply1")
        status = self.status()
        self.assertEqual(status["state"], "needs_review")
        self.assertEqual(status["unmappedAttempts"][0]["status"], "sent")

    def test_rebound_decision_link_is_visible_in_both_items(self):
        self.decision()
        self.decision(decision="D-20261104-12")
        aid = self.action(final=None, decision="D-20261104-11", step="reply1")
        self.event(kind="reply", status="sent", attemptId=aid, decision="D-20261104-12", step="reply1")
        for decision in ("D-20261104-11", "D-20261104-12"):
            status = self.status(decision)
            self.assertEqual(status["state"], "needs_review")
            self.assertEqual(status["unmappedAttempts"][0]["attemptId"], aid)
            self.assertEqual(status["unmappedAttempts"][0]["status"], "sent")

    def test_explicit_empty_sent_metadata_does_not_inherit_unasked_intent(self):
        pending = self.event(kind="reply", status="pending", asks=["hours.time"], tags=["intent:interview"])
        self.event(kind="reply", status="sent", attemptId=pending["attemptId"], asks=[], tags=[])
        self.event(kind="observe", observed="replied")
        self.assertEqual(self.thread()["info"]["askedUnanswered"], {})
        self.assertEqual(self.thread()["interviewIntentStages"], [])

    def test_greet_milestone_requires_sent_and_joins_late_recruiter(self):
        pending = self.event(kind="greet", company="独立公司", recruiter=None, status="pending")
        visible = self.threads("--company", "独立公司")["threads"]
        self.assertIsNone(visible[0]["stage"])
        self.event(kind="greet", company="独立公司", status="sent", attemptId=pending["attemptId"])
        visible = self.threads("--company", "独立公司")["threads"]
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]["stage"], "greeted")
        self.assertEqual(visible[0]["threadKey"], "boss|boss-main|独立公司|招聘者(hr)")

    def test_invalid_inquiry_is_a_structured_error_not_silently_changed_priority(self):
        for inquiry in (None, [], {"priority": "hours"}, {"priority": ["unknown"]}):
            ledger.write_atomic(self.f.ws / "profile.json", json.dumps({"inquiry": inquiry}))
            code, out = self.f.run_cli("threads")
            self.assertEqual(code, 2, out)
            self.assertIn("inquiry", out["error"])

    def test_non_reply_ack_is_not_treated_as_a_valid_system_receipt(self):
        self.event(kind="observe", observed="interview_invited", decision="D-20261104-11")
        self.action(kind="resume", decision="D-20261104-11", step="ack")
        self.assertEqual(self.status()["state"], "needs_review")

    def test_partial_reply_metadata_uses_current_job_without_importing_new_job(self):
        self.event(kind="observe", observed="replied", jobId="job-A")
        self.action(title=None, asks=["hours.time"])
        self.event(kind="observe", observed="replied", title=None)
        self.assertEqual(self.thread()["currentJobKey"], "boss|job-A")
        self.assertEqual(self.thread()["info"]["askedUnanswered"], {"hours.time": 1})
        self.event(kind="observe", observed="replied", jobId="job-B")
        self.assertEqual(self.thread()["info"]["askedUnanswered"], {})

    def test_late_sent_confirmation_does_not_move_unkeyed_question_to_new_job(self):
        self.event(kind="observe", observed="replied", jobId="job-A")
        pending = self.event(kind="reply", status="pending", title=None, asks=["hours.time"])
        self.event(kind="screen", result="pending", reasons=[], jobId="job-B")
        self.event(kind="reply", status="sent", title=None, attemptId=pending["attemptId"])
        self.event(kind="observe", observed="replied", jobId="job-B")
        self.assertEqual(self.thread()["info"]["askedUnanswered"], {})
        self.event(kind="observe", observed="replied", jobId="job-A")
        self.assertEqual(self.thread()["info"]["askedUnanswered"], {"hours.time": 1})

    def test_owner_reviewed_unmapped_sent_does_not_block_a_new_registered_version(self):
        self.action(decision="D-20261104-11", step="old_unregistered")
        self.assertEqual(self.status()["state"], "needs_review")
        closed = self.event(kind="observe", observed="unpaused", decision="D-20261104-11", skill="user")
        self.assertEqual(self.status()["state"], "done")
        self.decision()
        status = self.status()
        self.assertEqual(status["state"], "not_started")
        self.assertEqual(status["unmappedAttempts"][0]["reviewResolvedBy"], closed["eventId"])
        self.assertEqual(status["unmappedAttempts"][0]["status"], "sent")
        self.assertEqual(status["steps"]["reply1"], "not_started")

    def test_owner_completion_for_other_target_does_not_hide_unmapped_sent(self):
        self.action(company="另一个目标", decision="D-20261104-11", step="unregistered")
        self.event(kind="observe", observed="unpaused", decision="D-20261104-11", skill="user")
        self.assertEqual(self.status()["state"], "needs_review")

    def test_backfilled_older_hr_reply_does_not_consume_a_new_question(self):
        self.action(asks=["hours.time"])
        self.event(kind="observe", observed="replied", hrAt="2026-11-04T11:00:00+08:00")
        self.assertEqual(self.thread()["info"]["askedUnanswered"], {})
        os.environ["JOB_MATE_NOW"] = "2026-11-04T12:02:00+08:00"
        self.event(kind="observe", observed="replied", hrAt="2026-11-04T12:01:00+08:00")
        self.assertEqual(self.thread()["info"]["askedUnanswered"], {"hours.time": 1})

    def test_invitation_ack_then_approved_unicode_step_completes_only_user_version(self):
        decision = "D-20261104-11"
        self.event(kind="observe", observed="interview_invited", decision=decision)
        self.event(kind="observe", observed="paused", decision=decision)
        self.action(decision=decision, step="ack")
        self.assertEqual(self.status()["state"], "not_started")
        text = " \n本人批准的全角Ａ\u2028原样文字\n "
        self.decision(steps=[{"step": "reply1", "kind": "reply", "text": text}])
        self.action(decision=decision, step="reply1", text=text)
        self.assertEqual(self.status()["state"], "partial")
        self.event(kind="observe", observed="interview_decided", decision=decision)
        self.event(kind="observe", observed="unpaused", decision=decision)
        status = self.status()
        self.assertEqual(status["state"], "done")
        self.assertEqual(status["steps"], {"reply1": "sent"})
        self.assertEqual(status["versions"][0]["steps"][0]["text"], text)
        self.assertEqual(len(status["systemAttempts"]), 1)

    def test_owner_reviewed_unstepped_terminal_does_not_reblock_new_version_write(self):
        # 当前格式故障注入，沿用已有 H01 的本人核对收口路径。
        for status in ("pending", "sent"):
            raw = dict(self.f.identity, schemaVersion=1, account="boss-main", runId="run-tail", skill="followup",
                       kind="reply", status=status, attemptId="att-fault-unstepped", decision="D-20261104-11",
                       at=os.environ["JOB_MATE_NOW"], reason="合成缺步骤故障", evidence=None)
            ledger.append_line(self.f.ws, ledger.prepare_event(raw))
        self.assertEqual(self.status()["state"], "needs_review")
        self.event(kind="observe", observed="unpaused", skill="user", decision="D-20261104-11")
        self.assertEqual(self.status()["state"], "done")
        self.decision()
        self.action(decision="D-20261104-11", step="reply1")
        self.assertEqual(self.status()["steps"]["reply1"], "sent")

    def test_delayed_handled_write_does_not_refresh_the_complete_read_time(self):
        first = self.f.ok_tail()
        verified = self.f.ok_tail(base=first["tailId"])
        read_at = os.environ["JOB_MATE_NOW"]
        os.environ["JOB_MATE_NOW"] = "2026-11-05T14:00:00+08:00"
        self.assertEqual(self.f.handled(verified, user=True)[0], 0)
        handled = self.thread()["lastHandled"]
        self.assertEqual(handled["readAt"], read_at)
        self.assertEqual(handled["at"], os.environ["JOB_MATE_NOW"])
        self.assertNotEqual(handled["readAt"], handled["at"])

    def test_ack_retargeted_from_other_recruiter_keeps_sent_but_requires_review(self):
        self.event(kind="observe", observed="interview_invited", decision="D-20261104-11")
        pending = self.event(kind="reply", status="pending", decision="D-20261104-11", step="ack", recruiter="招聘者乙(HR)")
        self.event(kind="reply", status="sent", decision="D-20261104-11", step="ack", attemptId=pending["attemptId"])
        status = self.status()
        self.assertEqual(status["state"], "needs_review")
        self.assertEqual(status["attempts"][0]["status"], "sent")
        self.assertEqual(status["attempts"][0]["mappingIssue"], "ack_target_conflict")
        self.assertTrue(status["unmappedAttempts"])

    def test_ack_allows_unknown_recruiter_to_be_completed_without_retargeting(self):
        self.event(kind="observe", observed="interview_invited", decision="D-20261104-11")
        pending = self.event(kind="reply", status="pending", decision="D-20261104-11", step="ack", recruiter=None)
        self.event(kind="reply", status="sent", decision="D-20261104-11", step="ack", attemptId=pending["attemptId"])
        status = self.status()
        self.assertEqual(status["state"], "not_started")
        self.assertIsNone(status["systemAttempts"][0]["mappingIssue"])
        self.assertEqual(status["systemAttempts"][0]["status"], "sent")

    def test_projection_is_read_only_and_does_not_reinterpret_tail_safety(self):
        first = self.f.ok_tail()
        verified = self.f.ok_tail(base=first["tailId"])
        before = ledger.events_path(self.f.ws).read_bytes()
        with patch.object(ledger, "compare_tail", wraps=ledger.compare_tail) as compare:
            self.assertEqual(self.thread()["pendingTail"]["tailId"], verified["tailId"])
            self.assertGreater(compare.call_count, 0)  # read_events 的同一安全判定路径。
        self.status()
        self.assertEqual(ledger.events_path(self.f.ws).read_bytes(), before)

"""临时工作区、合成列表/尾部与真实台账接口；不访问招聘平台。"""
import copy
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_workspace

L, W = test_workspace.ledger, test_workspace.workspace
ROOT = Path(__file__).resolve().parents[1]


class InventoryTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime.fromisoformat("2026-11-05T12:00:00+08:00")
        self.env = patch.dict(os.environ, JOB_MATE_TESTING="1", JOB_MATE_NOW=self.now.isoformat())
        self.env.start()
        self.addCleanup(self.env.stop)
        self.f = test_workspace.WorkspaceTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.f.init()
        self.f.fill()
        self.ws = self.f.ws
        self.profile = copy.deepcopy(test_workspace.PROFILE)
        self.profile["authorization"] = {"apply": {"send": False}, "followup": {"reply": "L2", "nudge": True}}
        self.profile["limits"] = {"minSendIntervalSeconds": 0}
        self.profile["schedule"] = {"timezone": "Asia/Shanghai", "activeHours": {
            "weekdays": list(range(1, 8)), "start": "00:00", "end": "23:59"}}
        self.save()
        self.run_id = "inventory-run"
        self.ok("lock", "acquire", "--skill", "followup", "--run", self.run_id)

    def cli(self, *args):
        return test_workspace.invoke_data(L.main, "--workspace", str(self.ws), *args)

    def ok(self, *args):
        rc, out = self.cli(*args)
        self.assertEqual(rc, 0, out)
        return out

    def save(self):
        (self.ws / "profile.json").write_text(json.dumps(self.profile), encoding="utf-8")

    def clock(self, minutes):
        self.now += timedelta(minutes=minutes)
        os.environ["JOB_MATE_NOW"] = self.now.isoformat()

    def inventory(self, action, body=None, *extra):
        args = ["inventory", action, "--platform", "boss", "--run", self.run_id, *extra]
        if body is not None:
            args += ["--json", json.dumps(body, ensure_ascii=False)]
        return self.cli(*args)

    def inv_ok(self, action, body=None, *extra):
        rc, out = self.inventory(action, body, *extra)
        self.assertEqual(rc, 0, out)
        return out

    def row(self, company="合成会话公司", **extra):
        row = {"company": company, "recruiter": "示例招聘者(HR)", "title": "本人已在聊的岗位",
               "readAt": self.now.isoformat(), "lastActivityLabel": "2026-11-04 10:00", "listState": "active"}
        return dict(row, **extra)

    def thread(self, company="合成会话公司"):
        return next(t for t in self.ok("threads")["threads"] if t["company"] == company)

    def append(self, observed, **extra):
        body = {"kind": "observe", "observed": observed, "platform": "boss", "runId": self.run_id,
                "skill": "followup", "company": "合成会话公司", "recruiter": "示例招聘者(HR)", "reason": "合成观察"}
        body.update(extra)
        return self.cli("append", "--json", json.dumps(body, ensure_ascii=False))

    def intent(self, last_at="2026-11-04T10:00:00+08:00"):
        return {"kind": "nudge", "platform": "boss", "company": "合成会话公司", "recruiter": "示例招聘者(HR)",
                "title": "本人已在聊的岗位", "text": "您好，想确认岗位是否仍开放，我有真实项目经验，希望有机会交流。",
                "reason": "合成职位依据", "tags": ["nudge:1", "intent:confirm_open"],
                "pageState": {"lastSpeaker": "us", "lastOurStatus": "read", "lastOurAt": last_at,
                              "precision": "second", "readAt": self.now.isoformat()}}

    def check(self, body):
        return self.cli("check", "--run", self.run_id, "--json", json.dumps(body, ensure_ascii=False))

    def reply_intent(self, kind="reply", **extra):
        body = self.intent()
        body.update(kind=kind, text="您好，这是有依据的合成答复。")
        body.pop("pageState"); body.pop("tags")
        if kind == "resume":
            body.pop("text")
            body["resume"] = "a"
        return dict(body, **extra)

    def read_tail(self, messages, base=None, anchor_label="2026-09-20 10:00"):
        body = {"anchor": {"type": "text", "text": "历史我方消息", "label": anchor_label},
                "messages": [{"type": "text", "text": text, "label": label} for text, label in messages],
                "complete": True, "anchorUnique": True}
        args = ["tail", "--run", self.run_id, "--thread", self.thread()["threadKey"],
                "--read-at", self.now.isoformat(), "--json", json.dumps(body)]
        if base:
            args += ["--base", base]
        return self.ok(*args)

    def test_start_record_duplicate_and_only_end_completes(self):
        status = self.ok("threads")
        self.assertEqual(status["inventories"][0]["status"], "not_started")
        self.assertEqual(status["threads"], [])
        inv_id = self.inv_ok("start")["inventory"]["inventoryId"]
        self.assertEqual(self.inv_ok("start")["inventory"]["inventoryId"], inv_id)
        first = self.inv_ok("record", self.row())
        ev = first["event"]
        self.assertEqual((ev["skill"], ev["adoptionSource"], ev["inventoryId"]), ("followup", "inventory", inv_id))
        self.assertEqual(ev["readAt"], self.now.isoformat())
        self.assertTrue(self.inv_ok("record", self.row())["duplicate"])
        self.assertEqual(self.thread()["boundaryAt"], ev["readAt"])
        self.assertEqual(self.thread()["adoptionSource"], "inventory")
        self.assertTrue(self.thread()["inventoryPendingOpen"])
        self.assertEqual(self.ok("threads")["inventoryPending"][0]["inventoryId"], inv_id)
        mid = self.inv_ok("progress", None, "--reason", "列表很长，本轮预算用尽")
        self.assertEqual((mid["inventory"]["status"], mid["inventory"]["registeredCount"]), ("in_progress", 1))
        end = self.inv_ok("finish", None, "--reason", "已滚到全部列表底部")
        self.assertTrue(end["inventory"]["reachedEnd"])
        self.assertEqual(end["inventory"]["status"], "completed")
        self.assertEqual(self.inv_ok("start")["inventory"]["inventoryId"], inv_id)
        self.assertEqual(self.inventory("record", self.row("新出现的公司"))[0], 2)
        self.assertTrue(self.ok("validate")["ok"])

    def test_resume_across_runs_restart_only_adds_new_threads(self):
        old = self.inv_ok("start")["inventory"]["inventoryId"]
        self.inv_ok("record", self.row())
        self.inv_ok("progress", None, "--reason", "锁时限将到")
        self.ok("lock", "release", "--run", self.run_id)
        self.run_id = "next-inventory-run"
        self.clock(1)
        self.ok("lock", "acquire", "--skill", "followup", "--run", self.run_id)
        self.assertEqual(self.inv_ok("start")["inventory"]["inventoryId"], old)
        self.assertTrue(self.inv_ok("record", self.row())["duplicate"])
        self.inv_ok("record", self.row("第二家合成公司"))
        self.assertEqual(self.inv_ok("finish", None, "--reason", "到底")["inventory"]["registeredCount"], 2)
        new = self.inv_ok("start", None, "--restart")["inventory"]["inventoryId"]
        self.assertNotEqual(new, old)
        self.assertTrue(self.inv_ok("record", self.row())["duplicate"])
        self.inv_ok("record", self.row("第三家合成公司"))
        self.assertEqual(self.inv_ok("finish", None, "--reason", "重新到底")["inventory"]["registeredCount"], 1)
        self.assertEqual(len(self.ok("threads")["threads"]), 3)

    def test_no_send_allowed_global_pause_and_missing_or_wrong_lock_refused(self):
        self.ok("lock", "release", "--run", self.run_id)
        self.assertEqual(self.inventory("start")[0], 2)
        self.ok("lock", "acquire", "--skill", "apply", "--run", self.run_id)
        self.assertEqual(self.inventory("start")[0], 2)
        self.ok("lock", "release", "--run", self.run_id)
        self.ok("lock", "acquire", "--skill", "followup", "--run", self.run_id, "--no-send")
        self.inv_ok("start")
        self.inv_ok("record", self.row())
        before = L.events_path(self.ws).read_bytes()
        self.ok("pause", "on", "--reason", "合成安全暂停")
        self.assertEqual(self.inventory("record", self.row("不得接手"))[0], 2)
        self.assertEqual(self.inventory("finish", None, "--reason", "未浏览不能到底")[0], 2)
        self.assertEqual(L.events_path(self.ws).read_bytes(), before)
        self.assertEqual(self.inv_ok("progress", None, "--reason", "遇风控已停，保存原有进度")["inventory"]["status"], "in_progress")
        self.ok("pause", "off")
        self.assertEqual(self.check(self.intent())[1]["reasons"][0]["code"], "no_send")

    def test_exclusions_and_preexisting_manual_closed_or_pending_are_not_overwritten(self):
        self.inv_ok("start")
        self.assertEqual(self.inv_ok("record", self.row("甲公司"))["skipped"], "stoplist")
        for flag in ("draft", "rejected", "job_closed", "risk"):
            self.assertEqual(self.inv_ok("record", self.row("不接手" + flag, listState=flag))["skipped"], flag)
        self.assertEqual(self.append("manual")[0], 0)
        self.assertEqual(self.inv_ok("record", self.row())["skipped"], "already_registered")
        self.assertTrue(self.thread()["manual"])
        self.assertIsNone(self.thread()["adoptionSource"])
        for observed in ("paused", "job_closed"):
            company = "拒绝接手" + observed
            self.assertEqual(self.append(observed, company=company)[0], 0)
            self.assertEqual(self.inventory("record", self.row(company))[0], 2)
        self.assertEqual(self.inv_ok("start")["inventory"]["registeredCount"], 0)

    def test_existing_sent_and_user_adoption_keep_their_source_and_state(self):
        self.inv_ok("start")
        self.assertEqual(self.append("adopted", skill="user", readAt=self.now.isoformat())[0], 0)
        self.assertTrue(self.inv_ok("record", self.row())["duplicate"])
        self.assertEqual(self.thread()["adoptionSource"], "user")
        self.assertEqual(self.inv_ok("start")["inventory"]["registeredCount"], 0)

    def test_pending_is_refused_then_existing_agent_sent_is_not_adopted_again(self):
        self.inv_ok("start")
        intent = self.intent()
        intent.update(kind="reply", company="已有Agent会话", text="合成数据回复")
        intent.pop("tags"); intent.pop("pageState")
        pending = self.ok("prepare", "--run", self.run_id, "--json", json.dumps(intent))
        self.assertEqual(self.inventory("record", self.row("已有Agent会话"))[0], 2)
        terminal = {"kind": "reply", "status": "sent", "platform": "boss", "runId": self.run_id,
                    "attemptId": pending["attemptId"], "skill": "followup", "reason": "合成送达证据"}
        self.ok("append", "--json", json.dumps(terminal))
        self.assertEqual(self.inv_ok("record", self.row("已有Agent会话"))["skipped"], "already_registered")
        self.assertIsNone(self.thread("已有Agent会话")["adoptionSource"])
        self.assertEqual(self.inv_ok("start")["inventory"]["registeredCount"], 0)

    def test_inventory_history_is_account_scoped_and_not_a_conversation(self):
        self.inv_ok("start")
        self.inv_ok("record", self.row())
        self.inv_ok("finish", None, "--reason", "第一账号到底")
        marker = L.read_json(self.ws / L.MARKER)
        marker["accounts"]["boss"]["accountKey"] = "second-synthetic"
        (self.ws / L.MARKER).write_text(json.dumps(marker), encoding="utf-8")
        self.assertEqual(self.inv_ok("start")["inventory"]["status"], "in_progress")
        self.inv_ok("record", self.row())
        threads = self.ok("threads")
        self.assertEqual(len(threads["threads"]), 2)
        self.assertEqual({t["account"] for t in threads["threads"]}, {"zp-1", "second-synthetic"})
        self.assertEqual({s["status"] for s in threads["inventories"]}, {"completed", "in_progress"})
        self.assertTrue(all(t["company"] for t in threads["threads"]))
        self.assertEqual(len(self.ok("threads", "--account", "second-synthetic")["inventories"]), 1)

    def test_adoption_read_boundary_certifies_history_not_new_user_activity(self):
        self.inv_ok("start")
        self.inv_ok("record", self.row())
        code, out = self.check(self.intent())
        self.assertEqual(code, 0, out)
        self.assertIsNone(self.thread()["lastOutbound"])
        self.clock(3)
        code, out = self.check(self.intent(last_at=(self.now - timedelta(minutes=1)).isoformat()))
        self.assertEqual(code, 1, out)
        self.assertEqual(out["reasons"][0]["code"], "page_state_mismatch")
        self.assertEqual(self.append("manual", reason="分界后我方活动无对应 sent")[0], 0)
        self.assertTrue(self.thread()["manual"])
        self.assertEqual(self.check(self.intent())[1]["reasons"][0]["code"], "thread_manual")

    def test_stale_inventory_not_candidate_or_sendable_even_with_fresh_owner_release(self):
        self.inv_ok("start")
        self.inv_ok("record", self.row(lastActivityLabel="2026-09-20 10:00"))
        self.assertEqual(self.thread()["nudgeReason"], "inventory_stale")
        self.assertFalse(self.thread()["canNudge"])
        code, out = self.check(self.intent(last_at="2026-09-20T10:00:00+08:00"))
        self.assertEqual(code, 1, out)
        self.assertEqual(out["reasons"][0]["code"], "inventory_stale")
        self.assertEqual(self.append("released", skill="user")[0], 0)
        self.assertEqual(self.thread()["nudgeReason"], "inventory_stale")
        # 按盘点读取时刻计算年龄，不因过了几轮而把所有会话变成超龄。
        self.assertEqual(self.thread()["nudgesTotal"], 0)

    def test_configurable_age_and_existing_config_defaults(self):
        self.assertEqual(L.effective_nudge({}, "boss")["existingMaxAgeDays"], 30)
        self.inv_ok("start")
        self.inv_ok("record", self.row(lastActivityLabel="2026-10-11 10:00"))
        self.assertTrue(self.thread()["canNudge"])
        self.profile["nudge"] = {"existingMaxAgeDays": 20}
        self.save()
        self.assertEqual(self.thread()["nudgeReason"], "inventory_stale")
        for value in (True, 0, -1, "30"):
            self.profile["nudge"]["existingMaxAgeDays"] = value
            self.save()
            self.assertEqual(self.cli("threads")[0], 2)

    def test_new_hr_reply_restores_normal_policy_but_old_hr_history_does_not(self):
        self.inv_ok("start")
        self.inv_ok("record", self.row(lastActivityLabel="2026-09-20 10:00"))
        key = self.thread()["threadKey"]

        def read_tail(label, text, base=None):
            body = {"anchor": {"type": "text", "text": "历史我方消息", "label": "2026-09-20 10:00"},
                    "messages": [{"type": "text", "text": text, "label": label}], "complete": True, "anchorUnique": True}
            args = ["tail", "--run", self.run_id, "--thread", key, "--read-at", self.now.isoformat(), "--json", json.dumps(body)]
            if base:
                args += ["--base", base]
            return self.ok(*args)

        old = read_tail("2026-09-21 10:00", "历史 HR 回复")
        self.assertEqual(self.append("replied", tailId=old["tailId"])[0], 0)
        self.assertEqual(self.thread()["nudgeReason"], "inventory_stale")
        self.clock(2)
        body = {"anchor": {"type": "text", "text": "历史我方消息", "label": "2026-09-20 10:00"},
                "messages": [{"type": "text", "text": "历史 HR 回复", "label": "2026-09-21 10:00"},
                             {"type": "text", "text": "接手后 HR 新回复", "label": "2026-11-05 12:01"}],
                "complete": True, "anchorUnique": True}
        new = self.ok("tail", "--run", self.run_id, "--thread", key, "--read-at", self.now.isoformat(),
                      "--base", old["tailId"], "--json", json.dumps(body))
        self.assertEqual(self.append("replied", tailId=new["tailId"])[0], 0)
        self.assertNotEqual(self.thread()["nudgeReason"], "inventory_stale")
        self.assertFalse(self.thread()["inventoryPendingOpen"])
        self.assertTrue(self.ok("validate")["ok"])

    def test_overage_old_unanswered_hr_is_history_not_auto_reply_or_resume(self):
        self.inv_ok("start")
        self.inv_ok("record", self.row(lastActivityLabel="2026-09-21 10:00"))
        tail = self.read_tail([("历史未答问题", "2026-09-21 10:00")])
        scope = tail["inventoryReply"]
        self.assertEqual(scope["historicalIndices"], [0])
        self.assertEqual(scope["newIndices"], [])
        self.assertFalse(scope["ready"])
        # 即使已有事实观察登记，旧 replyProof 也不能批准历史代答。
        self.assertEqual(self.append("replied", tailId=tail["tailId"])[0], 0)
        for kind in ("reply", "resume"):
            body = self.reply_intent(kind)
            before = L.events_path(self.ws).read_bytes()
            code, out = self.check(body)
            self.assertEqual(code, 1, out)
            self.assertEqual(out["reasons"][0]["code"], "inventory_history")
            code, out = self.cli("prepare", "--run", self.run_id, "--json", json.dumps(body))
            self.assertEqual(code, 1, out)
            self.assertEqual(out["reasons"][0]["code"], "inventory_history")
            self.assertEqual(L.events_path(self.ws).read_bytes(), before)
        self.assertEqual(self.thread()["inventoryReply"]["historicalIndices"], [0])

    def test_recent_inventory_keeps_pre_handoff_unanswered_hr_actionable(self):
        self.inv_ok("start")
        self.inv_ok("record", self.row(lastActivityLabel="2026-11-04 11:00"))
        tail = self.read_tail([("接手前但未超龄的未答问题", "2026-11-04 11:00")], anchor_label="2026-11-04 10:00")
        self.assertNotIn("inventoryReply", tail)
        self.assertIsNone(self.thread()["inventoryReply"])
        self.assertEqual(self.append("replied", tailId=tail["tailId"])[0], 0)
        for kind in ("reply", "resume"):
            code, out = self.check(self.reply_intent(kind))
            self.assertEqual(code, 0, out)
            self.assertTrue(out["allowed"])

    def test_mixed_overage_tail_filters_history_and_requires_new_hr_proof(self):
        self.inv_ok("start")
        self.inv_ok("record", self.row(lastActivityLabel="2026-09-21 10:00"))
        self.clock(2)
        tail = self.read_tail([("历史未答问题", "2026-09-21 10:00"), ("接手后新问题", "2026-11-05 12:01")])
        self.assertEqual(tail["inventoryReply"]["historicalIndices"], [0])
        self.assertEqual(tail["inventoryReply"]["newIndices"], [1])
        self.assertFalse(tail["inventoryReply"]["ready"])
        self.assertEqual(self.check(self.reply_intent())[1]["reasons"][0]["code"], "inventory_history")
        self.assertEqual(self.append("replied", tailId=tail["tailId"])[0], 0)
        scope = self.thread()["inventoryReply"]
        self.assertTrue(scope["ready"])
        self.assertEqual(scope["historicalIndices"], [0])  # 新消息不让旧问题重新进入分诊。
        self.assertEqual(scope["newIndices"], [1])
        code, out = self.check(self.reply_intent())
        self.assertEqual(code, 0, out)
        self.assertNotEqual(self.thread()["nudgeReason"], "inventory_stale")

    def test_guard_rechecks_inventory_reply_scope_instead_of_freezing_old_permission(self):
        self.inv_ok("start")
        self.inv_ok("record", self.row(lastActivityLabel="2026-09-21 10:00"))
        self.clock(2)
        tail = self.read_tail([("历史未答问题", "2026-09-21 10:00"), ("接手后新问题", "2026-11-05 12:01")])
        self.assertEqual(self.append("replied", tailId=tail["tailId"])[0], 0)
        pending = self.ok("prepare", "--run", self.run_id, "--json", json.dumps(self.reply_intent()))
        self.clock(1)
        self.read_tail([("历史未答问题", "2026-09-21 10:00")], base=tail["tailId"])
        code, out = self.cli("guard", "--run", self.run_id, "--attempt", pending["attemptId"])
        self.assertEqual(code, 1, out)
        self.assertEqual(out["reasons"][0]["code"], "inventory_history")

    def test_ambiguous_handoff_day_does_not_become_new_by_rereading(self):
        self.inv_ok("start")
        self.inv_ok("record", self.row(lastActivityLabel="2026-09-21 10:00"))
        tail = self.read_tail([("只有日期的未答消息", "2026-11-05")])
        self.assertEqual(tail["inventoryReply"]["uncertainIndices"], [0])
        self.clock(2)
        self.read_tail([("只有日期的未答消息", "2026-11-05")], base=tail["tailId"])
        self.assertFalse(self.thread()["inventoryReply"]["ready"])
        self.assertEqual(self.check(self.reply_intent())[1]["reasons"][0]["code"], "inventory_history")

    def test_future_label_cannot_claim_post_handoff_hr_evidence(self):
        self.inv_ok("start")
        self.inv_ok("record", self.row(lastActivityLabel="2026-09-21 10:00"))
        self.clock(2)
        tail = self.read_tail([("时间晚于读取时刻", "2026-11-05 12:03")])
        self.assertEqual(tail["inventoryReply"]["uncertainIndices"], [0])
        self.assertEqual(self.append("replied", tailId=tail["tailId"])[0], 0)
        self.assertFalse(self.thread()["inventoryReply"]["ready"])
        self.assertEqual(self.thread()["nudgeReason"], "inventory_stale")
        self.assertEqual(self.check(self.reply_intent())[1]["reasons"][0]["code"], "inventory_history")

    def test_explicit_approved_reply_keeps_existing_decision_gate(self):
        self.inv_ok("start")
        self.inv_ok("record", self.row(lastActivityLabel="2026-09-21 10:00"))
        self.read_tail([("历史未答问题", "2026-09-21 10:00")])
        text = "本人明确批准的原文。"
        self.assertEqual(self.append("decision", skill="user", decision="D-20261105-07", version=1,
                                     steps=[{"step": "reply1", "kind": "reply", "text": text}])[0], 0)
        code, out = self.check(self.reply_intent(text=text, decision="D-20261105-07", step="reply1"))
        self.assertEqual(code, 0, out)
        self.assertTrue(out["allowed"])

    def test_read_time_offset_does_not_change_list_activity_age(self):
        self.inv_ok("start")
        self.inv_ok("record", self.row(readAt="2026-11-05T04:00:00+00:00", lastActivityLabel="2026-10-06 10:00"))
        self.assertEqual(self.thread()["readAt"], self.now.isoformat())
        self.assertEqual(self.thread()["nudgeReason"], "inventory_stale")

    def test_unknown_list_time_never_guesses_today_and_page_still_checks_age(self):
        self.inv_ok("start")
        self.inv_ok("record", self.row(lastActivityLabel="10:00"))
        self.assertEqual(self.thread()["listActivity"], {"date": None, "minute": None, "precision": "none"})
        code, out = self.check(self.intent(last_at="2026-09-20T10:00:00+08:00"))
        self.assertEqual(code, 1, out)
        self.assertEqual(out["reasons"][0]["code"], "inventory_stale")
        self.profile["nudge"] = {"existingMaxAgeDays": 10 ** 100}
        self.save()
        self.assertEqual(self.cli("threads")[0], 0)

    def test_fake_list_inventory_never_opens_threads_inputs_or_sends(self):
        seed = ROOT / "tests/fakes/scenarios/boss.json"
        original = seed.read_bytes()
        scene = json.loads(original)
        scene["windows"][0]["page"] = "inventory-first"
        scene["pages"]["inventory-first"] = {"elements": [
            {"id": "root", "role": "group", "text": "全部会话"},
            {"id": "list-recent", "parent": "root", "role": "text",
             "text": "合成会话公司 / 示例招聘者(HR) / 本人已在聊的岗位 / 2026-11-04 10:00 / [已读]合成摘要"},
            {"id": "list-draft", "parent": "root", "role": "text",
             "text": "合成草稿会话 / 草稿招聘者(HR) / 任意岗位 / 2026-11-04 10:00 / 草稿"},
            {"id": "more", "parent": "root", "role": "button", "text": "加载更多列表",
             "action": {"kind": "navigate", "page": "inventory-bottom"}}]}
        scene["pages"]["inventory-bottom"] = {"elements": [
            {"id": "root", "role": "group", "text": "全部会话列表底部"},
            {"id": "list-old", "parent": "root", "role": "text",
             "text": "合成旧会话 / 历史招聘者(HR) / 早前岗位 / 2026-09-20 10:00 / [送达]合成摘要"}]}
        scenario = self.f.root / "synthetic-inventory.json"
        scenario.write_text(json.dumps(scene), encoding="utf-8")
        env = dict(os.environ, AGENT_DESKTOP_FAKE_SCENARIO=str(scenario))

        def desktop(*args):
            result = subprocess.run([sys.executable, "-B", "-I", str(ROOT / "tests/fakes/agent_desktop.py"), *args],
                                    capture_output=True, text=True, encoding="utf-8", env=env, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)["data"]

        def read_list():
            snapshot = desktop("snapshot", "--window", "window-1")
            for node in snapshot["elements"]:
                if node["id"].startswith("list-"):
                    company, recruiter, title, label, summary = node["text"].split(" / ")
                    state = "draft" if summary == "草稿" else "active"
                    self.inv_ok("record", self.row(company, recruiter=recruiter, title=title,
                                                  lastActivityLabel=label, listState=state))
            return snapshot

        self.ok("lock", "release", "--run", self.run_id)
        self.ok("lock", "acquire", "--skill", "followup", "--run", self.run_id, "--no-send")
        self.inv_ok("start")
        read_list()
        self.inv_ok("progress", None, "--reason", "本轮读取预算到，未到列表底部")
        self.assertEqual(self.inv_ok("start")["inventory"]["status"], "in_progress")
        snapshot = read_list()  # 下一轮从头读，已登记不重复。
        desktop("click", "--window", "window-1", "--snapshot", snapshot["snapshotId"], "--id", "more")
        read_list()
        self.assertEqual(self.inv_ok("finish", None, "--reason", "可见列表底部")["inventory"]["registeredCount"], 2)
        after = json.loads(scenario.read_text(encoding="utf-8"))
        self.assertEqual([call["command"] for call in after["state"]["calls"]], ["snapshot", "snapshot", "click", "snapshot"])
        self.assertEqual(after["state"]["inputEvents"], [])
        self.assertEqual(after["state"]["messages"], scene["state"]["messages"])
        self.assertEqual(after["state"]["drafts"], scene["state"]["drafts"])
        self.assertEqual(seed.read_bytes(), original)
        self.assertEqual(len(self.ok("threads")["inventoryPending"]), 2)
        # 暂停分支先被台账拒绝，不调用任何替身页面工具。
        self.ok("pause", "on", "--reason", "合成全局暂停")
        self.assertEqual(self.inventory("start", None, "--restart")[0], 2)
        self.assertEqual(json.loads(scenario.read_text(encoding="utf-8"))["state"]["calls"], after["state"]["calls"])

    def test_skill_contract_requires_inventory_without_setup_switch(self):
        skill = (ROOT / "skills/followup/SKILL.md").read_text(encoding="utf-8")
        recipe = (ROOT / "shared/inventory.md").read_text(encoding="utf-8")
        for term in ("每账号首次", "../../shared/inventory.md", "不设开关", "盘点状态", "尚未打开数"):
            self.assertIn(term, skill)
        for term in ("不逐个打开会话", "不受 inboundScanDays", "真实滚到底后", "不按当前求职条件淘汰",
                     "全局暂停不浏览平台", "最久未处理优先", "不因当前条件不符就 declined", "已有 adopted 返回 duplicate",
                     "不代答、不进入回复分诊", "未超龄的会话", "historicalIndices", "newIndices", "inventory_history"):
            self.assertIn(term, recipe)
        self.assertNotIn("adoptExisting", json.dumps(L.effective_profile({})))
        setup = (ROOT / "skills/setup/SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("adoptExisting", setup)
        self.assertNotIn("inventory", setup)

    def test_input_and_progress_cannot_forge_protocol_or_read_times(self):
        self.inv_ok("start")
        for extra in ({"readAt": "2026-11-06T00:00:00+08:00"}, {"readAt": "2026-11-04T00:00:00+08:00"},
                      {"company": ""}, {"recruiter": ""}, {"lastActivityLabel": 123}, {"readAt": None}, {"rawSummary": "不得存原文"}):
            self.assertEqual(self.inventory("record", self.row(**extra))[0], 2)
        self.assertEqual(self.inventory("finish")[0], 2)
        ev = self.inv_ok("record", self.row())["event"]
        forged = {k: v for k, v in ev.items() if k not in {"eventId", "threadKey", "jobKey"}}
        self.assertEqual(self.cli("append", "--json", json.dumps(forged))[0], 2)
        # 改写合成日志以测试历史重读校验，不涉及真实工作区。
        lines = L.events_path(self.ws).read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0]); first["registeredCount"] = 9
        lines[0] = json.dumps(first)
        L.events_path(self.ws).write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertEqual(self.cli("validate")[0], 2)


if __name__ == "__main__":
    unittest.main()

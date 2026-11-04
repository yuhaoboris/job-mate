"""T10.1：真实CLI、固定时钟、合成事件/页面；不调用浏览器或招聘平台。"""
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE = datetime(2026, 11, 4, 10, tzinfo=timezone(timedelta(hours=8)))
TEXTS = ["您好，想确认前端岗位是否仍开放，我参与过订单系统研发。",
         "我负责的客户端性能优化与贵公司业务很契合，方便安排一次面试交流吗？",
         "如果暂时不考虑我的申请，也麻烦告知一声，便于我安排其他求职事项，谢谢。",
         "贵公司的产品工程方向很吸引我，岗位现在还在招聘吗？"]


class NudgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name) / "workspace"
        self.now = BASE
        self.run = "nudge-run"
        self.identity = {"platform": "boss", "company": "合成公司", "title": "前端研发", "recruiter": "合成HR"}
        self.profile = {"schemaVersion": 1, "targetRoles": ["前端"], "locations": [],
                        "authorization": {"apply": {"send": True}, "followup": {"reply": "L1", "nudge": True}},
                        "limits": {"minSendIntervalSeconds": 0},
                        "schedule": {"timezone": "Asia/Shanghai", "activeHours": {
                            "weekdays": [1, 2, 3, 4, 5, 6, 7], "start": "00:00", "end": "23:59"}}}
        self.ok("workspace", "init", str(self.ws), "--account", "boss:synthetic:合成账号", bare=True)
        self.save()
        self.acquire()
        self.last = None
        self.send("greet", "您好，我有订单研发经验，希望进一步了解岗位。")

    def cli(self, module, *arguments, bare=False):
        prefix = [sys.executable, "-B", "-I", str(ROOT / "scripts" / (module + ".py"))]
        if not bare:
            prefix += ["--workspace", str(self.ws)]
        env = dict(os.environ, JOB_MATE_TESTING="1", JOB_MATE_NOW=self.now.isoformat(), PYTHONDONTWRITEBYTECODE="1",
                   JOB_MATE_TEST_PLATFORM_ROOT=str(ROOT / "tests/fixtures/platforms"))
        out = subprocess.run([*prefix, *arguments], capture_output=True, encoding="utf-8", env=env, timeout=30)
        self.assertFalse(out.stderr, out.stderr)
        return out.returncode, json.loads(out.stdout)

    def ok(self, module, *arguments, **kwargs):
        code, out = self.cli(module, *arguments, **kwargs)
        self.assertEqual(code, 0, out)
        return out

    def save(self):
        (self.ws / "profile.json").write_text(json.dumps(self.profile), encoding="utf-8")

    def acquire(self, *args):
        self.lock_args = args
        return self.ok("ledger", "lock", "acquire", "--skill", "followup", "--run", self.run, *args)

    def clock(self, hours):
        self.ok("ledger", "lock", "release", "--run", self.run)
        self.now = BASE + timedelta(hours=hours)
        self.acquire(*self.lock_args)

    def page(self, **overrides):
        value = {"lastSpeaker": "us", "lastOurStatus": "read", "lastOurAt": self.last["at"],
                 "precision": "second", "readAt": self.now.isoformat()}
        value.update(overrides)
        return value

    def intent(self, text=TEXTS[0], **overrides):
        body = dict(self.identity, kind="nudge", text=text, reason="合成JD与已确认事实", pageState=self.page(),
                    tags=["nudge:1", "intent:confirm_open"])
        body.update(overrides)
        return body

    def prepare(self, body):
        return self.cli("ledger", "prepare", "--run", self.run, "--json", json.dumps(body, ensure_ascii=False))

    def check(self, code, **overrides):
        before = self.events()
        rc, out = self.cli("ledger", "check", "--run", self.run, "--json", json.dumps(self.intent(**overrides), ensure_ascii=False))
        self.assertEqual(rc, 1, out)
        self.assertEqual(out["reasons"][0]["code"], code, out)
        self.assertEqual(self.events(), before)
        return out

    def events(self):
        return (self.ws / "ledger/events.jsonl").read_bytes()

    def rows(self):
        return [json.loads(line) for line in self.events().decode().split("\n") if line]

    def thread(self):
        return next(t for t in self.ok("ledger", "threads")["threads"] if t["recruiter"] == self.identity["recruiter"])

    def observe(self, kind, **extra):
        body = dict(self.identity, kind="observe", observed=kind, runId=self.run, skill="followup", reason="合成观察")
        body.update(extra)
        return self.cli("ledger", "append", "--json", json.dumps(body, ensure_ascii=False))

    def tail(self, texts, base=None, **extra):
        anchor = {"type": "text", "text": self.last["text"], "label": datetime.fromisoformat(self.last["at"]).strftime("%Y-%m-%d %H:%M")}
        body = {"anchor": anchor, "messages": [{"type": "text", "text": m, "label": self.now.strftime("%Y-%m-%d %H:%M")} for m in texts],
                "complete": True, "anchorUnique": True}
        body.update(extra)
        args = ["tail", "--run", self.run, "--thread", self.last["threadKey"], "--read-at", self.now.isoformat(),
                "--json", json.dumps(body, ensure_ascii=False)]
        if base:
            args += ["--base", base]
        out = self.ok("ledger", *args)
        stored = next(r for r in self.rows() if r.get("tailId") == out["tailId"])
        return dict(stored, **out)

    def finish(self, attempt, status="sent", skill="followup"):
        first = next(r for r in self.rows() if r.get("attemptId") == attempt and r.get("status") == "pending")
        body = dict(self.identity, kind=first["kind"], status=status, attemptId=attempt, runId=self.run, skill=skill,
                    text=first["text"], reason="合成结果证据")
        out = self.ok("ledger", "append", "--json", json.dumps(body, ensure_ascii=False))
        if status == "sent":
            self.last = out["event"]
        return out

    def guard(self, attempt, page=None):
        args = ["guard", "--run", self.run, "--attempt", attempt]
        if page is not None:
            args += ["--page-state", json.dumps(page, ensure_ascii=False)]
        return self.cli("ledger", *args)

    def send(self, kind, text, **fields):
        body = dict(self.identity, kind=kind, text=text, targetRole="前端", reason="合成匹配")
        if kind == "nudge":
            body = self.intent(text=text)
        body.update(fields)
        code, out = self.prepare(body)
        self.assertEqual(code, 0, out)
        attempt = out["attemptId"]
        code, guarded = self.guard(attempt, self.page() if kind == "nudge" else None)
        self.assertEqual(code, 0, guarded)
        self.finish(attempt)
        return attempt

    def settle_reply(self, source, old, attempt, texts):
        def message(text, at, speaker, aid=None):
            value = {"type": "text", "text": text, "label": datetime.fromisoformat(at).strftime("%Y-%m-%d %H:%M"), "speaker": speaker}
            if aid: value["attemptId"] = aid
            return value
        bridge = [message(old["text"], old["at"], "us")]
        bridge.extend(message(text, source["readAt"], "hr") for text in texts)
        bridge.append(message(self.last["text"], self.last["at"], "us", attempt))
        verified = self.tail([], base=source["tailId"], bridge=bridge, bridgeComplete=True)
        self.assertTrue(verified["settled"], verified)
        body = dict(self.identity, kind="observe", observed="handled", runId=self.run, skill="followup", reason="完整桥接复核",
                    tailId=verified["tailId"], baseTailId=source["tailId"])
        self.ok("ledger", "append", "--json", json.dumps(body))

    def test_complete_25_47_49_hour_sequence_and_true_reply_reset(self):
        self.clock(25)
        t = self.thread()
        self.assertTrue(t["canNudge"])
        self.assertTrue(t["nudgeNeedsPageState"])
        self.send("nudge", TEXTS[0])
        self.clock(72)  # 距 nudge1 47h，不是距 greet 47h。
        self.check("nudge_interval", text=TEXTS[1], tags=["nudge:2", "intent:propose_interview"])
        self.clock(74)  # 距 nudge1 49h。
        self.send("nudge", TEXTS[1], tags=["nudge:2", "intent:propose_interview"])
        self.clock(123)
        self.send("nudge", TEXTS[2], tags=["nudge:3", "intent:ask_answer"])
        self.clock(172)
        self.assertTrue(self.thread()["suggestSilent"])
        self.assertEqual(self.thread()["nudgesTotal"], 3)
        self.check("nudge_silence_limit", text=TEXTS[3], tags=["nudge:4", "intent:ask_answer"])
        self.assertEqual(self.observe("silent")[0], 0)
        self.check("thread_silent")
        tail = self.tail(["请介绍一下你做过的项目"])
        code, out = self.observe("replied", tailId=tail["tailId"])
        self.assertEqual(code, 0, out)
        t = self.thread()
        self.assertEqual((t["nudgeSeq"], t["nudgesSinceReply"], t["nudgesTotal"], t["silent"]), (1, 0, 3, False))
        # 新 HR 需先答复，nudge 权限不能借给 L1 的普通 reply。
        self.check("not_our_turn")
        self.profile["authorization"]["followup"]["reply"] = "L2"
        self.save()
        old = copy.deepcopy(self.last)
        reply = self.send("reply", "我主要负责订单接口和客户端的性能优化。")
        self.settle_reply(tail, old, reply, ["请介绍一下你做过的项目"])
        self.clock(197)
        self.send("nudge", TEXTS[3])
        self.assertEqual((self.thread()["nudgesSinceReply"], self.thread()["nudgesTotal"]), (1, 4))

    def test_fresh_guard_required_and_new_hr_after_prepare_blocks(self):
        self.clock(25)
        code, out = self.prepare(self.intent())
        self.assertEqual(code, 0, out)
        attempt = out["attemptId"]
        frozen = copy.deepcopy(next(r for r in self.rows() if r.get("attemptId") == attempt))
        for page in (None, self.page(readAt=(self.now - timedelta(seconds=1)).isoformat()),
                     self.page(readAt=(self.now + timedelta(seconds=1)).isoformat()), self.page(lastSpeaker="hr")):
            rc, result = self.guard(attempt, page)
            self.assertEqual(rc, 1, result)
        self.assertEqual(self.guard(attempt, self.page())[0], 0)
        self.finish(attempt, "failed_no_send")
        self.clock(74)
        code, out = self.prepare(self.intent(text=TEXTS[1], tags=["nudge:2", "intent:propose_interview"]))
        self.assertEqual(code, 0, out)
        tail = self.tail(["刚看到，请发简历"])
        # pending 时 tail 会保持未决，HR 来信仍能阻止 guard（无需伪称已处理）。
        rc, result = self.guard(out["attemptId"], self.page())
        self.assertEqual(rc, 1, result)
        self.assertIn(result["reasons"][0]["code"], ("not_our_turn", "unresolved"))
        self.assertEqual(next(r for r in self.rows() if r["eventId"] == frozen["eventId"]), frozen)

    def test_page_time_precision_unknown_new_activity_and_status_thresholds(self):
        self.clock(24)
        self.check("nudge_too_early", pageState=self.page(precision="minute"))
        self.now += timedelta(minutes=1)
        self.assertEqual(self.prepare(self.intent(pageState=self.page(precision="minute")))[0], 0)

    def test_bad_page_evidence_and_early_and_outside_hours(self):
        self.clock(23)
        self.check("nudge_too_early")
        self.clock(25)
        for page in (None, {}, self.page(precision="day"), self.page(lastOurAt="2026-11-04"),
                     self.page(lastOurStatus="unknown"), self.page(lastSpeaker="unknown"),
                     self.page(lastOurAt=(BASE - timedelta(minutes=2, seconds=1)).isoformat()),
                     self.page(lastOurAt=(BASE + timedelta(minutes=2, seconds=1)).isoformat()),
                     self.page(extra="not allowed")):
            self.check("page_state_mismatch", pageState=page)
        self.profile["nudge"] = {"afterReadHours": 30, "afterUnreadHours": 26}
        self.save()
        self.check("nudge_too_early")
        self.check("nudge_too_early", pageState=self.page(lastOurStatus="delivered"))
        self.profile["schedule"]["activeHours"].update(start="15:00", end="18:00")
        self.save()
        self.check("outside_hours")

    def test_independent_authorization_four_combinations_and_grant_scope(self):
        self.clock(25)
        for reply in ("L1", "L2"):
            for enabled in (False, True):
                self.profile["authorization"]["followup"] = {"reply": reply, "nudge": enabled}
                self.save()
                rc, out = self.cli("ledger", "check", "--run", self.run, "--json", json.dumps(self.intent()))
                self.assertEqual(rc, 0 if enabled else 1, out)
                if not enabled:
                    self.assertEqual(out["reasons"][0]["code"], "not_authorized")
        self.profile["authorization"]["followup"] = {"reply": "L1", "nudge": False}
        self.save()
        self.ok("ledger", "lock", "release", "--run", self.run)
        self.run = "exception-run"
        grant = {"item": "nudge", "scope": {"companies": [self.identity["company"]]}, "max": 1, "quote": "合成本轮只准跟该公司一次"}
        self.acquire("--grant", json.dumps(grant))
        self.profile["authorization"]["apply"]["send"] = False
        self.save()
        self.check("not_authorized", kind="reply")
        self.check("not_authorized", kind="greet")
        body = self.intent(company="另一公司")
        rc, out = self.prepare(body)
        self.assertEqual(out["reasons"][0]["code"], "grant_out_of_scope")
        self.send("nudge", TEXTS[0])
        self.clock(74)
        self.check("grant_exhausted", text=TEXTS[1], tags=["nudge:2", "intent:propose_interview"])

    def test_tags_nudge_only_banned_words_and_similarity_all_history(self):
        self.clock(25)
        for tags in ([], ["nudge:2", "intent:confirm_open"], ["nudge:1", "intent:ask_answer"],
                     ["nudge:1", "nudge:8", "intent:confirm_open"], ["nudge:1", "intent:confirm_open", "intent:interview"]):
            self.check("nudge_intent_mismatch", tags=tags)
        for word in ("进度", "跟进", "催", "精通"):
            self.check("banned_word", text="您好，想了解" + word)
        self.check("repeated_text", text=self.last["text"])
        self.check("repeated_text", text=self.last["text"] + "！")
        self.profile["authorization"]["followup"]["reply"] = "L2"
        self.save()
        self.send("reply", "可以向您介绍项目进度。")  # 三个新增禁词仅用于 nudge。
        self.clock(50)
        self.check("repeated_text", text="您好，我有订单研发经验，希望进一步了解岗位。")

    def test_same_snapshot_new_tail_and_late_replied_do_not_refund(self):
        self.clock(25)
        first = self.tail(["合成HR新消息"])
        # 初次真实新 HR 可确认；同一 tail、相同内容的新 tail 都不能再次消费。
        code, out = self.observe("replied", tailId=first["tailId"])
        self.assertEqual(code, 0, out)
        self.assertEqual(self.observe("replied", tailId=first["tailId"])[0], 2)
        same = self.tail(["合成HR新消息"], base=first["tailId"])
        self.assertEqual(self.observe("replied", tailId=same["tailId"])[0], 2)
        self.assertEqual(self.observe("replied", tailId=same["tailId"], skill="user", evidence="合成已确认",
                                      newHr={"part": "tail", "start": 0, "count": 1})[0], 2)
        self.assertEqual(self.observe("replied")[0], 2)

    def test_manual_proof_requires_user_and_real_positions_and_replay_validation(self):
        self.clock(25)
        tail = self.tail(["仅日期的合成HR消息"], messages=[{"type": "text", "text": "仅日期的合成HR消息", "label": "今天"}])
        self.assertEqual(self.observe("replied", tailId=tail["tailId"])[0], 2)
        manual = {"part": "tail", "start": 0, "count": 1}
        self.assertEqual(self.observe("replied", tailId=tail["tailId"], evidence="人工依据", newHr=manual)[0], 2)
        for proof in ({**manual, "start": 1}, {**manual, "count": 2}, {**manual, "confirmationId": "arbitrary"}):
            self.assertEqual(self.observe("replied", tailId=tail["tailId"], skill="user", evidence="人工依据", newHr=proof)[0], 2)
        code, out = self.observe("replied", tailId=tail["tailId"], skill="user", evidence="本人确认第0项为新回复", newHr=manual)
        self.assertEqual(code, 0, out)
        self.assertIn("replyProof", out["event"])
        self.assertEqual(self.observe("replied", tailId=tail["tailId"], skill="user", evidence="另一确认ID也无效", newHr=manual)[0], 2)
        self.assertTrue(self.ok("ledger", "validate")["ok"])
        rows = self.rows()
        for corruption in ("index", "bool_index", "bool_version"):
            changed = copy.deepcopy(rows)
            if corruption == "bool_version":
                changed[-1]["replyProof"]["version"] = True
            else:
                changed[-1]["replyProof"]["items"][0]["index"] = False if corruption == "bool_index" else 99
            (self.ws / "ledger/events.jsonl").write_text("\n".join(json.dumps(r) for r in changed) + "\n", encoding="utf-8")
            rc, out = self.cli("ledger", "validate")
            self.assertEqual(rc, 2, out)

    def test_late_reply_source_boundary_and_release_do_not_refund_attempt(self):
        self.clock(25)
        source = self.tail(["合成HR询问经历"])
        self.profile["authorization"]["followup"]["reply"] = "L2"
        self.save()
        old = copy.deepcopy(self.last)
        reply = self.send("reply", "我负责过供应链系统中的订单模块。")
        self.settle_reply(source, old, reply, ["合成HR询问经历"])
        self.clock(50)
        self.send("nudge", TEXTS[0])
        code, out = self.observe("replied", tailId=source["tailId"], hrAt="2099-01-01T00:00:00+08:00")
        self.assertEqual(code, 0, out)
        self.assertEqual((self.thread()["nudgesSinceReply"], self.thread()["nudgeSeq"]), (1, 2))
        self.assertEqual(self.observe("manual")[0], 0)
        self.assertEqual(self.observe("released", skill="user")[0], 0)
        self.assertEqual(self.thread()["nudgesSinceReply"], 1)
        self.assertEqual(self.observe("replied", tailId=source["tailId"])[0], 2)

    def test_stale_base_extended_and_same_minute_occurrences_cannot_refund_twice(self):
        self.clock(25)
        first = self.tail(["相同HR消息"])
        self.assertEqual(self.observe("replied", tailId=first["tailId"])[0], 0)
        second = self.tail(["相同HR消息", "相同HR消息"], base=first["tailId"])
        code, out = self.observe("replied", tailId=second["tailId"])
        self.assertEqual(code, 0, out)
        self.assertEqual(out["event"]["replyProof"]["items"][0]["index"], 1)
        handled = dict(self.identity, kind="observe", observed="handled", tailId=second["tailId"],
                       baseTailId=first["tailId"], runId=self.run, skill="user", evidence="合成本人确认已处理两条", reason="人工收口")
        self.ok("ledger", "append", "--json", json.dumps(handled))
        stale = self.tail(["相同HR消息", "相同HR消息"], base=first["tailId"])
        self.assertEqual(stale["result"], "extended")
        self.assertEqual(self.observe("replied", tailId=stale["tailId"])[0], 2)
        self.assertEqual(self.observe("replied", tailId=stale["tailId"], skill="user", evidence="换一个确认也不能重用",
                                      newHr={"part": "tail", "start": 1, "count": 1})[0], 2)

    def test_cross_anchor_bridge_keeps_old_identity_and_counts_only_new_occurrence(self):
        self.clock(25)
        old = copy.deepcopy(self.last)
        first = self.tail(["相同HR消息"])
        self.assertEqual(self.observe("replied", tailId=first["tailId"])[0], 0)
        self.profile["authorization"]["followup"]["reply"] = "L2"
        self.save()
        attempt = self.send("reply", "可以，我主要负责客户端应用研发。")
        def message(text, at, speaker, aid=None):
            item = {"type": "text", "text": text, "label": datetime.fromisoformat(at).strftime("%Y-%m-%d %H:%M"), "speaker": speaker}
            if aid: item["attemptId"] = aid
            return item
        bridge = [message(old["text"], old["at"], "us"), message("相同HR消息", self.now.isoformat(), "hr"),
                  message(self.last["text"], self.last["at"], "us", attempt), message("相同HR消息", self.now.isoformat(), "hr")]
        result = self.tail(["相同HR消息"], base=first["tailId"], bridge=bridge, bridgeComplete=True)
        self.assertFalse(result["reviewRequired"], result)
        code, out = self.observe("replied", tailId=result["tailId"])
        self.assertEqual(code, 0, out)
        self.assertEqual(out["event"]["replyProof"]["items"], [{"eventId": result["eventId"], "part": "bridge", "index": 3}])
        self.assertEqual(self.observe("replied", tailId=result["tailId"])[0], 2)
        self.assertNotIn("相同HR消息", self.events().decode())

    def test_max_per_silence_intents_1_2_3_and_thread_limit(self):
        self.clock(25)
        for maximum, intent in ((1, "ask_answer"), (2, "confirm_open"), (3, "confirm_open")):
            self.profile["nudge"] = {"maxPerSilence": maximum}
            self.save()
            rc, out = self.cli("ledger", "check", "--run", self.run, "--json",
                               json.dumps(self.intent(tags=["nudge:1", "intent:" + intent])))
            self.assertEqual(rc, 0, out)
        self.profile["nudge"] = {"maxPerSilence": 1, "maxPerThread": 1}
        self.save()
        self.send("nudge", TEXTS[2], tags=["nudge:1", "intent:ask_answer"])
        self.clock(74)
        source = self.tail(["请说明项目职责"])
        self.assertEqual(self.observe("replied", tailId=source["tailId"])[0], 0)
        self.profile["authorization"]["followup"]["reply"] = "L2"
        self.save()
        old = copy.deepcopy(self.last)
        reply = self.send("reply", "主要负责客户端订单和物流功能。")
        self.settle_reply(source, old, reply, ["请说明项目职责"])
        self.clock(99)
        self.check("nudge_thread_limit", tags=["nudge:1", "intent:ask_answer"])

    def test_daily_quota_raw_account_collision_isolation_and_platform_override(self):
        self.profile["nudge"] = {"perDay": 1}
        self.save()
        old = copy.deepcopy(self.last)
        original_identity = copy.deepcopy(self.identity)
        self.identity = dict(self.identity, company="独立样例企业")
        self.send("greet", "您好，我的项目经验和该职位方向一致。")
        other = (copy.deepcopy(self.identity), copy.deepcopy(self.last))
        self.identity, self.last = original_identity, old
        def account(value):
            marker = self.ws / "job-mate.json"
            data = json.loads(marker.read_text(encoding="utf-8"))
            data["accounts"]["boss"] = {"accountKey": value, "displayName": "合成账号"}
            marker.write_text(json.dumps(data), encoding="utf-8")
        account("x|y")
        self.send("greet", "您好，岗位技术方向和我的经历一致。")
        one = copy.deepcopy(self.last)
        account("x｜y")
        self.send("greet", "您好，岗位技术方向和我的经历一致。")
        two = copy.deepcopy(self.last)
        self.assertEqual(one["threadKey"], two["threadKey"])
        self.clock(25)
        account("x|y")
        self.last = one
        self.send("nudge", TEXTS[0])
        account("x｜y")
        self.last = two
        self.send("nudge", TEXTS[0])  # 不串配额、连续序号或相似度。
        by_account = {t["account"]: t for t in self.ok("ledger", "threads")["threads"] if t["company"] == original_identity["company"]}
        self.assertEqual(by_account["x|y"]["nudgesTotal"], 1)
        self.assertEqual(by_account["x｜y"]["nudgesTotal"], 1)
        account("synthetic")
        self.last = old
        self.send("nudge", TEXTS[0])
        last = copy.deepcopy(self.last)
        self.identity, self.last = other
        self.check("nudge_day_limit")
        self.identity, self.last = original_identity, last
        self.profile["nudge"] = {"intervalHours": 48, "platforms": {"boss": {"intervalHours": 72}}}
        self.save()
        self.clock(74)
        self.check("nudge_interval", text=TEXTS[1], tags=["nudge:2", "intent:propose_interview"])

    def test_platform_quota_and_page_threshold_overrides_are_independent(self):
        boss = copy.deepcopy(self.last)
        marker = self.ws / "job-mate.json"
        data = json.loads(marker.read_text(encoding="utf-8"))
        data["accounts"]["testplat"] = {"accountKey": "synthetic", "displayName": "合成账号"}
        marker.write_text(json.dumps(data), encoding="utf-8")
        self.identity["platform"] = "testplat"
        self.send("greet", "您好，我做过和岗位相关的客户端研发。")
        other = copy.deepcopy(self.last)
        self.profile["nudge"] = {"perDay": 1, "platforms": {"testplat": {"afterReadHours": 26}}}
        self.save()
        self.identity["platform"], self.last = "boss", boss
        self.clock(25)
        self.send("nudge", TEXTS[0])
        self.identity["platform"], self.last = "testplat", other
        self.check("nudge_too_early")
        self.clock(27)
        self.send("nudge", TEXTS[0])

    def test_no_send_restrict_and_approved_source_cannot_bypass_nudge_gates(self):
        self.clock(25)
        self.check("not_authorized", decision="D-20261104-01", step="approved-text")
        self.ok("ledger", "lock", "release", "--run", self.run)
        self.run = "readonly-nudge"
        self.acquire("--no-send")
        self.check("no_send")
        self.ok("ledger", "lock", "release", "--run", self.run)
        self.run = "restricted-nudge"
        restriction = {"kinds": ["reply"], "scope": {"all": True}, "max": 1, "quote": "本轮只答复"}
        self.acquire("--restrict", json.dumps(restriction))
        self.check("run_kind_restricted")
        for kind, expected in (("manual", "thread_manual"), ("paused", "thread_paused"), ("job_closed", "thread_closed")):
            self.ok("ledger", "lock", "release", "--run", self.run)
            self.run = "local-stop-" + kind
            self.acquire()
            code, out = self.observe(kind)
            self.assertEqual(code, 0, out)
            self.check(expected)
            if kind == "manual": self.assertEqual(self.observe("released", skill="user")[0], 0)
            if kind == "paused": self.assertEqual(self.observe("unpaused")[0], 0)

    def test_failed_uncertain_attempts_count_and_default_two_final_intent(self):
        self.clock(25)
        self.profile["nudge"] = {"maxPerSilence": 2}
        self.save()
        code, out = self.prepare(self.intent())
        self.assertEqual(code, 0, out)
        self.finish(out["attemptId"], "failed_no_send")
        self.assertEqual((self.thread()["nudgesSinceReply"], self.thread()["nudgesTotal"]), (1, 1))
        self.clock(74)
        self.check("nudge_intent_mismatch", text=TEXTS[1], tags=["nudge:2", "intent:propose_interview"])
        code, out = self.prepare(self.intent(text=TEXTS[2], tags=["nudge:2", "intent:ask_answer"]))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.guard(out["attemptId"], self.page())[0], 0)
        self.finish(out["attemptId"], "uncertain")
        self.assertEqual((self.thread()["nudgesSinceReply"], self.thread()["nudgesTotal"]), (2, 2))
        self.assertTrue(self.thread()["suggestSilent"])
        self.check("run_uncertain_limit", text=TEXTS[3], tags=["nudge:3", "intent:ask_answer"])

    def test_guard_rechecks_status_local_blocks_and_keeps_own_pending_only_once(self):
        self.clock(25)
        self.profile["nudge"] = {"afterUnreadHours": 30}
        self.save()
        code, out = self.prepare(self.intent())
        self.assertEqual(code, 0, out)
        attempt = out["attemptId"]
        rc, bad = self.guard(attempt, self.page(lastOurStatus="delivered"))
        self.assertEqual((rc, bad["reasons"][0]["code"]), (1, "nudge_too_early"))
        self.assertEqual(self.observe("manual")[0], 0)
        rc, bad = self.guard(attempt, self.page())
        self.assertEqual((rc, bad["reasons"][0]["code"]), (1, "thread_manual"))
        self.assertEqual(self.observe("released", skill="user")[0], 0)
        self.assertEqual(self.guard(attempt, self.page())[0], 0)
        # 自己 pending 时的空尾读取不应制造第二个未决；其余真实歧义仍阻断。
        empty = self.tail([])
        self.assertEqual(self.guard(attempt, self.page())[0], 0)
        self.assertEqual(self.thread()["nudgesTotal"], 1)
        (self.ws / "stoplist.yaml").write_text("- name: 合成公司\n  aliases: []\n  reason: 合成风险\n", encoding="utf-8")
        rc, bad = self.guard(attempt, self.page())
        self.assertEqual((rc, bad["reasons"][0]["code"]), (1, "stoplist"))

    def test_manual_precision_refinement_cannot_create_another_reply_identity(self):
        self.clock(25)
        unknown = self.tail([], messages=[{"type": "text", "text": "同一条合成回复", "label": "今天"}])
        manual = {"part": "tail", "start": 0, "count": 1}
        self.assertEqual(self.observe("replied", tailId=unknown["tailId"], skill="user", evidence="本人确认第0条", newHr=manual)[0], 0)
        precise = self.tail(["同一条合成回复"], base=unknown["tailId"])
        self.assertEqual(self.observe("replied", tailId=precise["tailId"])[0], 2)
        self.assertEqual(self.observe("replied", tailId=precise["tailId"], skill="user", evidence="只补充分钟而非另一条", newHr=manual)[0], 2)
        two = self.tail(["同一条合成回复", "同一条合成回复"], base=precise["tailId"])
        self.assertEqual(self.observe("replied", tailId=two["tailId"], skill="user", evidence="本人明确确认可见的第二条",
                                      newHr={"part": "tail", "start": 1, "count": 1})[0], 0)
        self.assertTrue(self.ok("ledger", "validate")["ok"])

    def test_large_valid_hour_values_reject_without_overflow_or_guessing(self):
        self.clock(25)
        self.profile["nudge"] = {"afterReadHours": 10 ** 100, "afterUnreadHours": 10 ** 100}
        self.save()
        self.check("nudge_too_early")
        self.assertFalse(self.thread()["canNudge"])

    def test_owner_handoff_accepts_only_certified_history_without_fabricating_sent(self):
        self.clock(50)
        self.assertEqual(self.observe("manual")[0], 0)
        self.assertEqual(self.observe("released", skill="user")[0], 0)
        historical = (BASE + timedelta(hours=25)).isoformat()
        page = self.page(lastOurAt=historical)
        code, out = self.prepare(self.intent(pageState=page))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.guard(out["attemptId"], self.page())[1]["reasons"][0]["code"], "page_state_mismatch")
        self.assertEqual(self.guard(out["attemptId"], page)[0], 0)
        self.finish(out["attemptId"], "sent")
        self.identity.update(company="独立样例企业", recruiter="独立招聘者", jobUrl="https://www.zhipin.com/job_detail/owned-synthetic.html")
        self.assertEqual(self.observe("adopted", skill="user", readAt=self.now.isoformat())[0], 0)
        self.assertIsNone(self.thread()["lastOutbound"])
        self.assertTrue(self.thread()["canNudge"])
        self.clock(75)
        self.check("page_state_mismatch", pageState=self.page(lastOurAt=(BASE + timedelta(hours=51)).isoformat()))
        page = self.page(lastOurAt=historical)
        code, out = self.prepare(self.intent(pageState=page))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.guard(out["attemptId"], page)[0], 0)
        self.assertFalse(any(e["kind"] in ("greet", "reply", "nudge") and e["status"] == "sent"
                             and e["company"] == self.identity["company"] for e in self.rows()))
        self.assertEqual(self.thread()["nudgesTotal"], 1)

    def test_hr_first_seen_inside_bridge_cannot_refund_later_outbound_or_clear_silent(self):
        base = self.tail([])
        old = copy.deepcopy(self.last)
        self.clock(25)
        attempt = self.send("nudge", TEXTS[0], tags=["nudge:1", "intent:confirm_open"])
        self.assertEqual(self.observe("silent")[0], 0)
        self.clock(26)
        def message(text, at, speaker, aid=None):
            result = {"type": "text", "text": text, "label": datetime.fromisoformat(at).strftime("%Y-%m-%d %H:%M"), "speaker": speaker}
            if aid: result["attemptId"] = aid
            return result
        bridge = [message(old["text"], old["at"], "us"),
                  message("事后完整桥中才确认的较早HR", (BASE + timedelta(hours=24.5)).isoformat(), "hr"),
                  message(self.last["text"], self.last["at"], "us", attempt)]
        source = self.tail([], base=base["tailId"], bridge=bridge, bridgeComplete=True)
        self.assertEqual(self.observe("replied", tailId=source["tailId"])[0], 2)
        self.assertEqual(self.observe("replied", tailId=source["tailId"], skill="user", evidence="明确确认桥中第1条且其后已发送跟进",
                                      newHr={"part": "bridge", "start": 1, "count": 1})[0], 0)
        row = self.thread()
        self.assertEqual((row["nudgesSinceReply"], row["nudgesTotal"], row["nudgeSeq"]), (1, 1, 2))
        self.assertTrue(row["silent"])
        self.assertTrue(self.ok("ledger", "validate")["ok"])

    def test_concurrent_prepare_is_atomic_and_guard_counts_own_quota_once(self):
        self.clock(25)
        self.profile["nudge"] = {"perDay": 1}
        self.save()
        command = [sys.executable, "-B", "-I", str(ROOT / "scripts/ledger.py"), "--workspace", str(self.ws),
                   "prepare", "--run", self.run, "--json", json.dumps(self.intent())]
        env = dict(os.environ, JOB_MATE_TESTING="1", JOB_MATE_NOW=self.now.isoformat(), PYTHONDONTWRITEBYTECODE="1")
        children = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", env=env) for _ in range(2)]
        results = []
        try:
            for child in children:
                out, err = child.communicate(timeout=30)
                self.assertEqual(err, "")
                results.append((child.returncode, json.loads(out)))
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill(); child.communicate()
        self.assertEqual(sorted(rc for rc, _ in results), [0, 1])
        success = next(out for rc, out in results if rc == 0)
        self.assertEqual(len([r for r in self.rows() if r.get("kind") == "nudge" and r.get("status") == "pending"]), 1)
        self.assertEqual(self.guard(success["attemptId"], self.page())[0], 0)
        self.profile["authorization"]["followup"]["nudge"] = False
        self.save()
        rc, result = self.guard(success["attemptId"], self.page())
        self.assertEqual((rc, result["reasons"][0]["code"]), (1, "not_authorized"))


if __name__ == "__main__":
    unittest.main()

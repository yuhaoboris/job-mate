"""T10.1 P2：真实 CLI 补桥不能退还已发生的跟进；仅临时合成工作区。"""
import copy
from datetime import datetime, timedelta
import unittest

import test_nudge as drivers


class NudgeBoundaryTest(unittest.TestCase):
    def fixture(self, maximum=1):
        f = drivers.NudgeTest()
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.profile["nudge"] = {"maxPerSilence": maximum}
        f.save()
        old = copy.deepcopy(f.last)
        base = f.tail([])
        f.clock(25)
        intent = "ask_answer" if maximum == 1 else "confirm_open"
        aid = f.send("nudge", drivers.TEXTS[2] if maximum == 1 else drivers.TEXTS[0],
                     tags=["nudge:1", "intent:" + intent])
        sent = copy.deepcopy(f.last)
        self.assertEqual(f.observe("silent")[0], 0)
        f.clock(26)
        return f, base, old, aid, sent

    @staticmethod
    def message(text, at, speaker, aid=None):
        value = {"type": "text", "text": text, "speaker": speaker,
                 "label": datetime.fromisoformat(at).strftime("%Y-%m-%d %H:%M")}
        if aid:
            value["attemptId"] = aid
        return value

    def partial(self, f, base, old, sent, texts=("事后发现的较早HR",)):
        f.last = old
        snap = f.tail([], base=base["tailId"], complete=False,
                      messages=[self.message(text, (drivers.BASE + timedelta(hours=24)).isoformat(), "hr") for text in texts])
        self.assertTrue(snap["reviewRequired"])
        f.last = sent
        return snap

    def bridge(self, f, base, old, aid, sent, texts=("事后发现的较早HR",), **extra):
        items = [self.message(old["text"], old["at"], "us")]
        items.extend(self.message(text, (drivers.BASE + timedelta(hours=24)).isoformat(), "hr") for text in texts)
        items.append(self.message(sent["text"], sent["at"], "us", aid))
        return f.tail([], base=base["tailId"], bridge=items, bridgeComplete=True, **extra)

    def confirm(self, f, snap, part="bridge", start=1, count=1):
        rc, out = f.observe("replied", tailId=snap["tailId"], skill="user",
                            evidence="本人逐项确认较早HR在已发送nudge之前，之后没有新HR",
                            newHr={"part": part, "start": start, "count": count})
        self.assertEqual(rc, 0, out)
        return out["event"]["replyProof"]

    def close(self, f, base, snap):
        self.assertEqual(f.observe("handled", tailId=snap["tailId"], baseTailId=base["tailId"],
                                   skill="user", evidence="本人核对完整桥和当前空尾部")[0], 0)
        self.assertEqual(f.observe("manual")[0], 0)
        self.assertEqual(f.observe("released", skill="user")[0], 0)

    def test_original_cli_probe_both_observation_orders_keep_budget_and_silent(self):
        for partial_first in (False, True):
            with self.subTest(partial_first=partial_first):
                f, base, old, aid, sent = self.fixture()
                if partial_first:
                    self.partial(f, base, old, sent)
                snap = self.bridge(f, base, old, aid, sent)
                proof = self.confirm(f, snap)
                self.assertEqual(proof["items"][0]["part"], "tail" if partial_first else "bridge")
                row = f.thread()
                self.close(f, base, snap)
                f.clock(74)
                rc, prepared = f.prepare(f.intent(text=drivers.TEXTS[3], tags=["nudge:1", "intent:ask_answer"]))
                guard = f.guard(prepared["attemptId"], f.page()) if rc == 0 else None
                signal = {"partial_first": partial_first, "counts": (row["nudgesSinceReply"], row["nudgesTotal"]),
                          "silent": row["silent"], "prepare": (rc, prepared), "guard": guard}
                self.assertEqual(signal["counts"], (1, 1), signal)
                self.assertTrue(row["silent"], signal)
                self.assertEqual(rc, 1, signal)
                self.assertEqual(prepared["reasons"][0]["code"], "thread_silent", signal)
                self.assertTrue(f.ok("ledger", "validate")["ok"])

    def test_late_supplement_rechecks_pending_and_real_later_hr_reopens(self):
        f, base, old, aid, sent = self.fixture(maximum=2)
        partial = self.partial(f, base, old, sent)
        original = self.confirm(f, partial, part="tail", start=0)
        f.last = old
        full = f.tail([], base=partial["tailId"], messages=[self.message(
            "事后发现的较早HR", (drivers.BASE + timedelta(hours=24)).isoformat(), "hr")])
        f.last = sent
        self.close(f, partial, full)
        f.clock(74)
        rc, pending = f.prepare(f.intent(text=drivers.TEXTS[3]))
        self.assertEqual(rc, 0, pending)
        snap = self.bridge(f, base, old, aid, sent)
        self.close(f, base, snap)
        row = f.thread()
        self.assertEqual((row["nudgesSinceReply"], row["nudgesTotal"]), (2, 2))
        self.assertTrue(row["silent"])
        rc, guarded = f.guard(pending["attemptId"], f.page())
        self.assertEqual((rc, guarded["reasons"][0]["code"]), (1, "thread_silent"))
        duplicate = self.bridge(f, base, old, aid, sent)
        self.assertEqual(f.observe("replied", tailId=duplicate["tailId"], skill="user", evidence="重复同一完整桥",
                                   newHr={"part": "bridge", "start": 1, "count": 1})[0], 2)
        self.close(f, base, duplicate)
        f.finish(pending["attemptId"], "failed_no_send")
        f.clock(100)
        fresh = f.tail(["这是一条跟进之后真正的新HR回复"], base=duplicate["tailId"])
        self.assertEqual(f.observe("replied", tailId=fresh["tailId"])[0], 0)
        row = f.thread()
        self.assertEqual((row["nudgesSinceReply"], row["nudgesTotal"]), (0, 2))
        self.assertFalse(row["silent"])
        persisted = next(e["replyProof"] for e in f.rows() if e.get("replyProof") == original)
        self.assertEqual(persisted, original)
        self.assertTrue(f.ok("ledger", "validate")["ok"])


if __name__ == "__main__":
    unittest.main()

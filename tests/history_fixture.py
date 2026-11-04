"""只读派生/恢复测试的旧中间态夹具，不是发送入口；不随技能分发。

这些数据用于审计过去的不完整/错绑记录，不应强求其通过当前 prepare。
新发送权限、并发与门禁测试必须通过真实 CLI，不使用此构造器。
"""
import uuid

import ledger


def reply_input(ws, raw):
    """旧派生用例的合成人工核对：真实尾部指纹和明确位置，仍由 append 生成/校验凭据。"""
    raw = dict(raw)
    if raw.get("kind") != "observe" or raw.get("observed") != "replied" or raw.get("tailId"):
        return raw
    if any(k in raw for k in ("eventId", "threadKey", "jobKey", "replyProof")):
        return raw
    candidate = dict(raw)
    candidate.setdefault("schemaVersion", 1)
    candidate.setdefault("at", ledger.now_iso(ledger.tz_of(ledger.load_profile(ws))))
    candidate.setdefault("account", ledger.default_account(ws, candidate["platform"]))
    candidate.setdefault("attemptId", "att-" + uuid.uuid4().hex[:12])
    event = ledger.prepare_event(candidate)
    if ledger.validate_event(event) or not event["threadKey"]:
        return raw
    events, _ = ledger.read_events(ws)
    key = ledger.thread_key(event)
    own = [e for e in events if e["kind"] in ledger.ACTION_KINDS and e.get("status") == "sent" and ledger.thread_key(e) == key]
    when = ledger.parse_at(event["at"])
    anchor = "start"
    if own:
        sent = own[-1]
        at = ledger.parse_at(sent["at"])
        anchor = dict(speaker="us", type="card" if sent["kind"] == "resume" else "text",
                      digest=ledger.content_digest(sent.get("text") or sent.get("resume") or "合成我方内容"),
                      date=at.date().isoformat(), minute=at.strftime("%H:%M"), precision="minute")
    fp = dict(speaker="hr", type="text", digest=ledger.content_digest("合成新回复" + uuid.uuid4().hex),
              date=when.date().isoformat(), minute=when.strftime("%H:%M"), precision="minute")
    identity = {k: event.get(k) for k in ("platform", "account", "company", "title", "location", "recruiter", "jobId", "targetRole")}
    tail = ledger.prepare_event(dict(identity, schemaVersion=1, kind="observe", observed="tail", skill="followup", runId=event["runId"],
        attemptId="att-" + uuid.uuid4().hex[:12], at=event["at"], readAt=event["at"], reason="合成核对快照",
        tailId="tail-" + uuid.uuid4().hex[:12], baseTailId=None, baseExplicit=False, complete=True, anchorUnique=True,
        anchor=anchor, fingerprints=[fp]))
    state = ledger.tail_state(events, ledger.thread_scope(tail))
    if state["latest"]:
        tail.update(baseTailId=state["latest"]["tailId"], baseExplicit=True)
    tail.update(ledger.tail_outcome(events, tail, state))
    assert not ledger.validate_event(tail)
    ledger.validate_history_event(events, tail)
    ledger.append_line(ws, tail)
    return dict(raw, skill="user", tailId=tail["tailId"], newHr={"part": "tail", "start": 0, "count": 1},
                evidence="合成本人逐条确认本快照的新 HR 位置")


def append_action(ws, raw):
    raw = dict(raw)
    if raw.get("status") == "uncertain":
        # 本夹具明确构造工具正常返回的业务不明；环境与锁收尾由真实接口专项覆盖。
        raw.setdefault("uncertaintyClass", "business")
    raw.setdefault("schemaVersion", 1)
    raw.setdefault("at", ledger.now_iso(ledger.tz_of(ledger.load_profile(ws))))
    raw.setdefault("account", ledger.default_account(ws, raw["platform"]))
    raw.setdefault("attemptId", "att-" + uuid.uuid4().hex[:12])
    if raw.get("jobUrl"):
        raw["jobUrl"] = ledger.normalize_job_url(raw["platform"], raw["jobUrl"])
        actual = ledger.job_id_from_url(raw["platform"], raw["jobUrl"])
        if actual and raw.get("jobId") not in (None, "", actual):
            raise AssertionError("合成历史的 jobId 与 URL 不符")
        if actual:
            raw["jobId"] = actual
    event = ledger.prepare_event(raw)
    errors = ledger.validate_event(event)
    assert not errors, errors
    ledger.append_line(ws, event)
    return 0, {"ok": True, "event": event}

"""只读派生/恢复测试的旧中间态夹具，不是发送入口；不随技能分发。

这些数据用于审计过去的不完整/错绑记录，不应强求其通过当前 prepare。
新发送权限、并发与门禁测试必须通过真实 CLI，不使用此构造器。
"""
import uuid

import ledger


def append_action(ws, raw):
    raw = dict(raw)
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

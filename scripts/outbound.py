"""发送门禁的单一判定路径；CLI 传入其 ledger 实例，避免重复加载 __main__ 状态。"""
import argparse
from difflib import SequenceMatcher
import re
import unicodedata


def deny(code, message, retry=None):
    out = {"allowed": False, "reasons": [{"code": code, "message": message}]}
    if retry is not None:
        out["retryAfterSeconds"] = retry
    return out


def declaration_check(L, ev, events, current=None):
    decision, step = ev.get("decision"), ev.get("step")
    declarations = [e for e in events if e["kind"] == "observe" and e.get("observed") == "decision"
                    and e.get("decision") == decision]
    if not declarations:
        return deny("decision_not_registered", "本人决定尚未登记"), None
    latest = declarations[-1]
    owner = next((e for e in declarations if any(s["step"] == step for s in e["steps"])), None)
    if owner is None:
        return deny("decision_not_registered", "该步骤未登记"), None
    if owner is not latest or (current is not None and current.get("decisionVersion") != latest["version"]):
        return deny("decision_superseded", "该步骤不属于冻结的有效决定版本"), None
    if current is not None and latest.get("_line", 0) >= current.get("_line", 0):
        return deny("decision_not_registered", "该决定并未在原 pending 前登记"), None
    attempts = [a for a in L.attempts_of(events).values() if any(e.get("decision") == decision for e in a["events"])]
    if any(a["status"] in ("pending", "uncertain") for a in attempts):
        return deny("decision_unresolved", "该事项仍有未决尝试，先本人核对"), None
    if any(a["status"] == "sent" and any(e.get("step") == step for e in a["events"]) for a in attempts):
        return deny("decision_step_done", "该决定步骤已经发送"), None
    summary = L.decisions_of(events, only=decision)[decision]
    if any(not a["reviewResolvedBy"] for a in summary["unmappedAttempts"]):
        return deny("decision_needs_review", "该事项存在未核对的无法归属尝试"), None
    definition = next(s for s in latest["steps"] if s["step"] == step)
    field = "resume" if definition["kind"] == "resume" else "text"
    if (ev["kind"] != definition["kind"] or ev.get(field) != definition[field]
            or not L.known_target_matches(ev, latest)):
        return deny("decision_text_mismatch", "动作、目标或原文与本人登记步骤不一致"), None
    return None, latest["version"]


def uncertainty_stop(L, events, run, ev):
    business = []
    for attempt in L.attempts_of(events).values():
        if attempt["runId"] != run or not attempt["everPending"]:
            continue
        unclear = [e for e in attempt["events"] if e["status"] == "uncertain"]
        if any(e.get("uncertaintyClass", "tool") == "tool" for e in unclear):
            return deny("run_uncertain_limit", "本轮工具/环境结果不明，收尾后结束整轮")
        if any(e.get("uncertaintyClass") == "business" for e in unclear):
            business.append(attempt)
    if len(business) >= 2:
        return deny("run_uncertain_limit", "本轮第二次业务结果不明，收口不退回已发生次数")
    for attempt in business:
        if attempt["platform"] != ev["platform"] or attempt["account"] != ev["account"]:
            continue
        if attempt["kind"] in ("greet", "intro"):
            if attempt.get("jobId") and ev.get("jobId"):
                same = attempt["jobId"] == ev["jobId"]
            else:
                same = all(not attempt.get(field) or not ev.get(field) or L.norm_text(attempt[field]) == L.norm_text(ev[field])
                           for field in ("company", "title"))
        else:
            same = all(not attempt.get(field) or not ev.get(field) or L.norm_text(attempt[field]) == L.norm_text(ev[field])
                       for field in L.load_platform(ev["platform"])["conversationKey"])
        if same:
            return deny("unresolved", "本轮该目标发生过业务结果不明，先收口并停止本轮自动处理该目标")
    return None


def nudge_page(L, page, now, pending=None):
    fields = {"lastSpeaker", "lastOurStatus", "lastOurAt", "precision", "readAt"}
    if not isinstance(page, dict) or set(page) != fields:
        return None
    try:
        at, read = L.parse_at(page["lastOurAt"]), L.parse_at(page["readAt"])
    except (ValueError, TypeError):
        return None
    if (page["precision"] not in ("minute", "second") or page["lastOurStatus"] not in ("read", "delivered")
            or page["lastSpeaker"] not in ("us", "hr") or at > read or read > now
            or pending is not None and read < L.parse_at(pending["at"])):
        return None
    if page["precision"] == "minute":
        if at.second or at.microsecond:
            return None
        try:
            upper = at + L.timedelta(minutes=1)
        except OverflowError:
            return None
    else:
        if not re.search(r"T\d{2}:\d{2}:\d{2}", page["lastOurAt"]):
            return None
        upper = at
    return at, upper, read


def nudge_budget(L, ev, events, profile, now):
    key = L.thread_key(ev)
    state, policy = L.nudge_state(events, key, profile), profile["nudge"]
    if state["silent"]:
        return deny("thread_silent", "连续沉默已登记，须确认新 HR 回复才重开")
    if state["nudgesSinceReply"] >= policy["maxPerSilence"]:
        return deny("nudge_silence_limit", "连续跟进次数已满")
    if state["nudgesTotal"] >= policy["maxPerThread"]:
        return deny("nudge_thread_limit", "会话累计跟进次数已满")
    attempts = L.attempts_of(events)
    firsts = [(a, next(e for e in a["events"] if e["status"] == "pending")) for a in attempts.values()
              if a["kind"] == "nudge" and a["everPending"]]
    today = sum(a["platform"] == ev["platform"] and a["account"] == ev["account"]
                and L.parse_at(p["at"]).astimezone(now.tzinfo).date() == now.date() for a, p in firsts)
    if today >= policy["perDay"]:
        return deny("nudge_day_limit", "当前平台原始账号当日跟进次数已满")
    times = [max([L.parse_at(p["at"])] + [L.parse_at(e["at"]) for e in a["events"] if e["status"] == "sent"])
             for a, p in firsts if L.compatible_thread_keys(L.thread_key(L.thread_identity(p, attempts)), key)]
    if times and (now - max(times)).total_seconds() < policy["intervalHours"] * 3600:
        return deny("nudge_interval", "距本会话上次跟进未满间隔")
    hours = profile["schedule"]["activeHours"]
    if not (now.isoweekday() in hours["weekdays"] and hours["start"] <= now.strftime("%H:%M") < hours["end"]):
        return deny("outside_hours", "不在本人设置的主动跟进时段")
    return None


def nudge_policy(L, ev, events, profile, now, page, pending=None):
    key = L.thread_key(ev)
    parsed = nudge_page(L, page, now, pending)
    if parsed is None:
        return deny("page_state_mismatch", "须明确重新读取的我方状态、绝对时间、精度与读取时间")
    if page["lastSpeaker"] != "us":
        return deny("not_our_turn", "最后发言方为 HR，先处理来信")
    at, upper, read = parsed
    attempts = L.attempts_of(events)
    last, blocked = L.nudge_turn(events, key)
    if blocked:
        return deny(blocked, "仍有 HR 来信、尾部歧义或缺少可归属的我方活动/本人交接依据")
    last_at = L.parse_at(last["at"]) if last is not None else None
    known_at = last_at
    if pending is not None:
        prepared = nudge_page(L, pending.get("pageState"), now)
        if prepared is None:
            return deny("page_state_mismatch", "原准备缺少可信页面快照，先核对收尾")
        known_at = max([prepared[0]] + ([last_at] if last_at is not None else []))
    if known_at is not None and (known_at - at).total_seconds() > 120:
        return deny("page_state_mismatch", "页面早于本地最后发送，先核对旧页面")
    if last_at is None or (at - last_at).total_seconds() > 120:
        owner = L.nudge_owner_boundary(events, key)
        if owner is None or upper > owner[1]:
            return deny("page_state_mismatch", "陌生我方活动不在本人交接的已知历史内，先核对接管")
    problem = nudge_budget(L, ev, events, profile, now)
    if problem:
        return problem
    policy, state = profile["nudge"], L.nudge_state(events, key, profile)
    delay = policy["afterReadHours"] if page["lastOurStatus"] == "read" else policy["afterUnreadHours"]
    if (now - upper).total_seconds() < delay * 3600:
        return deny("nudge_too_early", "页面时间区间上界起算尚未满足等待小时数")
    tags = ev.get("tags", [])
    if ([t for t in tags if t.startswith("nudge:")] != ["nudge:" + str(state["nudgeSeq"])]
            or [t for t in tags if t.startswith("intent:")] != ["intent:" + state["nudgeIntent"]]):
        return deny("nudge_intent_mismatch", "连续序号与意图标签须精确一致且各只有一个")
    normalize = lambda s: re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).lower()
    text = normalize(ev["text"])
    for a in attempts.values():
        if a["kind"] not in ("greet", "intro", "nudge") or not L.compatible_thread_keys(L.thread_key(L.thread_identity(a["events"][0], attempts)), key):
            continue
        for e in a["events"]:
            old = normalize(e.get("text") or "")
            if old and (old == text or SequenceMatcher(None, old, text).ratio() >= .85):
                return deny("repeated_text", "与本会话之前的招呼或跟进文字相同或过于相似")
    return None


def evaluate(L, ws, ev, run, lock, events, profile, current=None, page_state=None):
    """只计算结果，不写事件、不续锁；prepare 在同一协调区内使用此结果。"""
    now, tz = L.fsutil.now(L.tz_of(profile)), L.tz_of(profile)
    checking = current is not None
    other_events = [e for e in events if not (checking and e["kind"] in L.ACTION_KINDS
                    and L.attempt_key(e) == L.attempt_key(current))]
    def exhausted(used, maximum):
        return maximum != "unlimited" and (used > maximum if checking else used >= maximum)
    if not lock or lock.get("runId") != run or lock.get("releasing") or L.lock_expired(lock, now) or lock.get("skill") == "setup":
        if lock and lock.get("mode") == "recovery":
            return deny("recovery_lock_only", "恢复锁不能外发")
        return deny("lock_invalid", "没有本轮有效发送锁")
    if lock.get("noSend"):
        return deny("no_send", "本轮禁发优先于全部授权")
    if lock.get("policyRev") not in (None, L.run_policy_rev(lock)):
        return deny("lock_invalid", "本轮锁的冻结策略被改写，须先停止并核对")
    rule = lock.get("restriction")
    bindings = lock.get("scopeBindings", {})
    if rule:
        if ev["kind"] not in rule["kinds"]:
            return deny("run_kind_restricted", "动作不在本轮允许集合")
        if not L.scope_covers(rule, bindings.get("restriction", []), ev):
            return deny("run_scope_restricted", "目标不在本轮冻结范围")
        if exhausted(L.pending_uses(events, run, rule["kinds"]), rule["max"]):
            return deny("run_limit_exhausted", "本轮全部授权来源合计次数已满")
    profile = L.effective_profile(profile, ev["platform"])
    kind, decision, step = ev["kind"], ev.get("decision"), ev.get("step")
    dedup_grant = None
    ack = step == "ack"
    approved = bool(decision and not ack)
    authorization = profile["authorization"]
    apply, followup = authorization["apply"], authorization["followup"]
    source, version = None, None
    if ack:
        invitation = next((e for e in events if e["kind"] == "observe" and e.get("observed") == "interview_invited"
                           and e.get("decision") == decision and L.same_complete_thread_keys(L.thread_key(e), L.thread_key(ev))
                           and (not checking or e.get("_line", 0) < current.get("_line", 0))), None)
        if kind != "reply" or invitation is None or not L.known_target_matches(ev, invitation):
            return deny("ack_invalid_invitation", "ack 必须关联同一原始目标的已登记邀约")
        if any(a["step"] == "ack" and a["decision"] == decision for a in L.attempts_of(other_events).values()):
            return deny("ack_duplicate", "该事项已有 ack 尝试，不重复发送")
        text = unicodedata.normalize("NFKC", ev.get("text") or "")
        if re.search(r"\d", text) or any(word in text for word in ("点", "号", "周", "星期", "今天", "明天", "后天", "上午", "下午", "晚上")):
            return deny("ack_contains_time", "系统回执不得含数字或时间词")
    elif approved:
        if kind not in ("reply", "resume"):
            return deny("not_authorized", "此动作不能通过本人事项决定授权")
        problem, version = declaration_check(L, ev, other_events, current)
        if problem:
            return problem
        source = f"decision:{decision}@v{version}"
    if source is None:
        long_term = (apply["send"] if kind in ("greet", "intro") else followup["nudge"] if kind == "nudge" else followup["reply"] == "L2")
        frozen_source = current.get("authorizedBy") if checking else None
        if checking and frozen_source == "long_term" and not long_term:
            return deny("not_authorized", "原长期授权已失效，不更换授权来源")
        if long_term and (not checking or frozen_source == "long_term"):
            source = "long_term"
        else:
            item = "apply_send" if kind in ("greet", "intro") else "nudge" if kind == "nudge" else "reply"
            grants = [g for g in lock.get("grants", []) if g["item"] == item
                      and (not checking or frozen_source == "grant:" + g["id"])]
            covered = [g for g in grants if L.scope_covers(g, bindings.get(g["id"], []), ev)]
            usable = [g for g in covered if not exhausted(L.pending_uses(events, run, authorized_by="grant:" + g["id"]), g["max"])]
            if not usable:
                code = "grant_exhausted" if covered else "grant_out_of_scope" if grants else "not_authorized"
                return deny(code, "缺少覆盖目标且仍有额度的发送授权")
            source = "grant:" + usable[0]["id"]
    if checking and source != current.get("authorizedBy"):
        return deny("not_authorized", "pending 的冻结授权来源无效，不能重新选源")
    if checking and current.get("dedupGrant"):
        dedup_grant = current["dedupGrant"]
        grant = next((g for g in lock.get("grants", []) if g["id"] == dedup_grant and g["item"] == "dedup_once"), None)
        if (not grant or not L.scope_covers(grant, bindings.get(dedup_grant, []), ev)
                or L.pending_uses(events, run, dedup_grant=dedup_grant) != 1):
            return deny("already_contacted", "pending 的去重豁免已失效或目标/已占额度不符")
    if L.read_pause(ws):
        return deny("paused", "工作区已全局暂停")
    stopped = uncertainty_stop(L, events, run, ev)
    if stopped:
        return stopped
    limits = profile["limits"]
    for reason in L.stoplist_reasons(ws, ev["company"]):
        return deny(reason["type"], reason["detail"])
    if kind != "resume":
        text = ev.get("text")
        if not isinstance(text, str) or not text.strip():
            return deny("empty_text", "拟发送文字不能为空")
        if not approved:
            style = profile["style"]
            banned = style["bannedWords"]
            normalize = lambda s: re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).lower()
            system = ["精通", *(["进度", "跟进", "催"] if kind == "nudge" else [])]
            if any(normalize(word) in normalize(text) for word in [*system, *banned]):
                return deny("banned_word", "自拟文字含系统或本人禁用词")
            maximum = 150 if kind == "reply" else 100 if style["length"] == "medium" else 60
            if len(text) > maximum:
                return deny("too_long", f"文字超过 {maximum} 个 Unicode 字符")
    same_account = [e for e in other_events if e["platform"] == ev["platform"] and e["account"] == ev["account"]]
    latest = [L.parse_at(e["at"]) for e in same_account if e["kind"] in L.ACTION_KINDS and e["status"] in ("pending", "sent")]
    if latest:
        wait = limits["minSendIntervalSeconds"] - (now - max(latest)).total_seconds()
        if wait > 0:
            return deny("interval", "同一平台账号的全部外发共用最小间隔", int(wait) + (wait != int(wait)))
    attempts = list(L.attempts_of(events).values())
    if kind in ("greet", "nudge"):
        for reason in L.company_observation_reasons(events, ev["platform"], ev["account"], ev["company"]):
            return deny("company_risk" if reason["type"] == "risk" else "company_rejected", reason["detail"])
    threads = L.build_threads(other_events, ev["platform"], profile, ev["account"])
    requested_key = L.thread_key(ev)
    candidates = [(key, thread) for key, thread in threads.items() if L.compatible_thread_keys(key, requested_key)]
    for key, thread in candidates:
        if thread["manual"]:
            return deny("thread_manual", "本人已接管")
        if thread["closed"]:
            return deny("thread_closed", "目标会话已结束")
        if thread["unresolved"]:
            return deny("unresolved", "目标有未确认外发，先核对")
        if any(d != decision or not (approved or ack) or not L.same_complete_thread_keys(key, requested_key)
               for d in thread["pausedBy"]):
            return deny("thread_paused", "存在不属于当前有效决定/邀约的暂停")
    if kind == "nudge":
        problem = nudge_policy(L, ev, other_events, profile, now,
                               page_state if checking else ev.get("pageState"), current)
        if problem:
            return problem
    if kind == "intro":
        if not any(a["kind"] == "greet" and a["status"] == "sent" and a["runId"] == run and not a["identityConflicts"]
                   and L.same_complete_thread_keys(L.thread_key(L.thread_identity(L.sent_metadata(a)[0], {L.attempt_key(a): a})),
                                                  requested_key) for a in attempts):
            return deny("not_authorized", "本轮同一会话尚无平台首次招呼已发送事实")
    if kind == "greet":
        args = argparse.Namespace(platform=ev["platform"], account=ev["account"], company=ev["company"],
                                  title=ev.get("title"), job_id=ev.get("jobId"), job_url=ev.get("jobUrl"), run=run)
        reasons, _ = L.first_contact_reasons(args, other_events, limits["dedupScope"])
        page = ev.get("pageState", {})
        if not isinstance(page, dict) or ("contacted" in page and type(page["contacted"]) is not bool):
            raise L.LedgerError("pageState 须为对象，contacted 为页面已联系状态的布尔值")
        if page.get("contacted"):
            return deny("already_contacted", "平台页面已联系，不能再次点击首次沟通")
        if reasons:
            unresolved_keys = {L.attempt_key(a) for a in L.attempts_of(other_events).values() if a["status"] in ("pending", "uncertain")}
            unresolved_events = [e for e in events if e["kind"] in L.ACTION_KINDS and L.attempt_key(e) in unresolved_keys]
            if L.first_contact_reasons(args, unresolved_events, limits["dedupScope"])[0]:
                return deny("unresolved", "去重相关目标仍有未确认外发，不能使用豁免")
            waiver = next((g for g in lock.get("grants", []) if g["item"] == "dedup_once"
                           and L.scope_covers(g, bindings.get(g["id"], []), ev)
                           and ((checking and current.get("dedupGrant") == g["id"]
                                 and L.pending_uses(events, run, dedup_grant=g["id"]) == 1)
                                or (not checking and L.pending_uses(events, run, dedup_grant=g["id"]) < 1))), None)
            if waiver:
                dedup_grant = waiver["id"]
            else:
                reason = reasons[0]
                return deny("reposted_suspect" if reason["type"] == "reposted_suspect" else "already_contacted", reason["detail"])
        greets = [a for a in attempts if a["platform"] == ev["platform"] and a["account"] == ev["account"]
                  and a["kind"] == "greet" and a["everPending"]]
        if exhausted(sum(a["runId"] == run for a in greets), limits["greetsPerRun"]):
            return deny("quota_run", "本轮招呼配额已满")
        if exhausted(sum(L.parse_at(a["firstAt"]).astimezone(tz).date() == now.date() for a in greets), limits["greetsPerDay"]):
            return deny("quota_day", "当日招呼配额已满")
        hours = profile["schedule"]["activeHours"]
        active = now.isoweekday() in hours["weekdays"] and hours["start"] <= now.strftime("%H:%M") < hours["end"]
        if not active and not any(g["item"] == "hours" and L.scope_covers(g, bindings.get(g["id"], []), ev)
                                  for g in lock.get("grants", [])):
            return deny("outside_hours", "不在本人设置的首次招呼时段")
        if not ev.get("targetRole"):
            raise L.LedgerError("首次招呼须带 targetRole 和匹配 reason")
    result = {"allowed": True, "authorizedBy": source}
    if version is not None:
        result["decisionVersion"] = version
    if dedup_grant is not None:
        result["dedupGrant"] = dedup_grant
    return result

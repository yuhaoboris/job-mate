"""首次 followup 盘点：账号级进度、列表接手及历史沉默保护。仅通过 ledger 调用。"""
import json
import re
import uuid
from datetime import datetime, timedelta


def is_progress(ev):
    return ev.get("kind") == "observe" and ev.get("observed") == "inventory"


def is_adoption(ev):
    return (ev.get("kind") == "observe" and ev.get("observed") == "adopted"
            and ev.get("adoptionSource") == "inventory")


def scope(ev):
    return ev["platform"], ev["account"]


def progress(events, platform, account):
    rows = [e for e in events if is_progress(e) and scope(e) == (platform, account)]
    if not rows:
        return {"platform": platform, "account": account, "status": "not_started", "inventoryId": None,
                "registeredCount": 0, "reachedEnd": False, "stopReason": None}
    last = rows[-1]
    registered = sum(is_adoption(e) and scope(e) == (platform, account)
                     and e["inventoryId"] == last["inventoryId"] for e in events)
    return {"platform": platform, "account": account,
            "status": "completed" if last["reachedEnd"] else "in_progress",
            "inventoryId": last["inventoryId"], "registeredCount": registered,
            "reachedEnd": last["reachedEnd"], "stopReason": last["reason"], "runId": last["runId"]}


def validate(L, ev):
    fields = {"inventoryId", "inventoryPhase", "registeredCount", "reachedEnd", "listActivity", "adoptionSource"}
    if not is_progress(ev) and not is_adoption(ev):
        return ["盘点字段只用于 inventory 或盘点 adopted"] if fields & set(ev) else []
    errors = []
    if ev["skill"] != "followup":
        errors.append("盘点记录须由 followup 写入")
    if not isinstance(ev.get("inventoryId"), str) or not re.fullmatch(r"inv-[0-9a-f]{12}", ev["inventoryId"]):
        errors.append("inventoryId 无效")
    if is_progress(ev):
        if any(ev.get(k) is not None for k in ("company", "title", "location", "recruiter", "targetRole", "jobId", "threadKey", "jobKey")):
            errors.append("inventory 是账号级记录，不关联会话或职位")
        if ev.get("inventoryPhase") not in ("started", "progress", "completed"):
            errors.append("inventoryPhase 无效")
        if type(ev.get("registeredCount")) is not int or ev["registeredCount"] < 0:
            errors.append("registeredCount 须为非负整数")
        if type(ev.get("reachedEnd")) is not bool or ev["reachedEnd"] != (ev.get("inventoryPhase") == "completed"):
            errors.append("只有滚到底的 completed 才能 reachedEnd:true")
        if set(ev) & {"adoptionSource", "listActivity", "readAt"}:
            errors.append("账号进度不能冒充接手或列表读取")
    else:
        if not ev.get("threadKey"):
            errors.append("盘点接手须有完整会话身份")
        if set(ev) & {"inventoryPhase", "registeredCount", "reachedEnd"}:
            errors.append("盘点接手不能指定进度")
        activity = ev.get("listActivity")
        if not isinstance(activity, dict) or set(activity) != {"date", "minute", "precision"}:
            errors.append("listActivity 须为解析后的日期、分钟及精度，不保存摘要")
        else:
            try:
                value = activity["date"]
                if activity["precision"] == "none":
                    if value is not None or activity["minute"] is not None:
                        raise ValueError()
                elif activity["precision"] in ("day", "minute"):
                    stamp = value + (" " + activity["minute"] if activity["precision"] == "minute" else "")
                    fmt = "%Y-%m-%d %H:%M" if activity["precision"] == "minute" else "%Y-%m-%d"
                    if datetime.strptime(stamp, fmt).strftime(fmt) != stamp:
                        raise ValueError()
                    if activity["precision"] == "day" and activity["minute"] is not None:
                        raise ValueError()
                    if bounds(L, activity, ev["readAt"])[0] > L.parse_at(ev["readAt"]):
                        raise ValueError()
                else:
                    raise ValueError()
            except (TypeError, ValueError, KeyError, OverflowError):
                errors.append("listActivity 日期/精度无效或晚于读取时间")
    return errors


def validate_history(L, events, ev):
    if not is_progress(ev) and not is_adoption(ev):
        return
    state = progress(events, *scope(ev))
    if is_progress(ev) and ev["inventoryPhase"] == "started":
        if state["status"] == "in_progress" or any(e.get("inventoryId") == ev["inventoryId"] for e in events):
            raise L.LedgerError("盘点仍进行中或 inventoryId 重复")
        count = 0
    else:
        if state["status"] != "in_progress" or state["inventoryId"] != ev["inventoryId"]:
            raise L.LedgerError("盘点未开始、已完成或平台账号不符")
        start = next(e for e in events if is_progress(e) and scope(e) == scope(ev)
                     and e["inventoryId"] == ev["inventoryId"] and e["inventoryPhase"] == "started")
        if L.parse_at(ev.get("readAt", ev["at"])) < L.parse_at(start["at"]):
            raise L.LedgerError("盘点读取/进度早于该次盘点开始")
        count = state["registeredCount"]
    if is_progress(ev) and ev["registeredCount"] != count:
        raise L.LedgerError("盘点已登记数与事实记录不符")


def bounds(L, activity, read_at):
    if activity["precision"] == "none":
        return None
    tz = L.parse_at(read_at).tzinfo
    value = activity["date"] + ("T" + activity["minute"] if activity["precision"] == "minute" else "T00:00")
    lower = datetime.fromisoformat(value).replace(tzinfo=tz)
    return lower, lower + (timedelta(minutes=1) if activity["precision"] == "minute" else timedelta(days=1))


def replied_after(L, events, adopted):
    sources = {e["eventId"]: e for e in events}
    cut = L.parse_at(adopted["readAt"])
    for event in events:
        if (event["kind"] != "observe" or event["observed"] != "replied"
                or not L.same_complete_thread_keys(L.thread_key(event), L.thread_key(adopted))):
            continue
        for item in event["replyProof"]["items"]:
            source = sources[item["eventId"]]
            values = source["fingerprints"] if item["part"] == "tail" else source["bridge"]
            fp = values[item["index"]]
            if "date" in fp:
                interval = bounds(L, fp, adopted["readAt"])
                if interval and interval[0] > L.parse_at(source["readAt"]):
                    continue
                if interval and interval[0] > cut and L.parse_at(source["readAt"]) > cut:
                    return True
                if interval and interval[1] <= cut:
                    continue  # 接手前的历史 HR，即使后来才读到也不解除沉默保护。
            base = next((e for e in events if e.get("tailId") == source.get("baseTailId")
                         and e["kind"] == "observe" and e["observed"] == "tail"
                         and L.same_complete_thread_keys(L.thread_key(e), L.thread_key(adopted))), None)
            if base and L.parse_at(base["readAt"]) >= cut:
                return True  # 已校验的新 HR 身份，出现在接手后的读取基准之后。
    return False


def over_age(L, adopted, policy, page_upper=None):
    interval = bounds(L, adopted["listActivity"], adopted["readAt"])
    upper = interval[1] if interval else page_upper
    return upper is not None and (L.parse_at(adopted["readAt"]) - upper).total_seconds() >= policy["existingMaxAgeDays"] * 86400


def stale(L, events, key, policy, page_upper=None):
    adopted = next((e for e in events if is_adoption(e) and L.same_complete_thread_keys(L.thread_key(e), key)), None)
    return (adopted is not None and over_age(L, adopted, policy, page_upper)
            and not replied_after(L, events, adopted))


def reply_scope(L, events, key, policy):
    """超龄接手的回复分诊范围；按消息 origin 区分，不因后来有新 HR 而放活全部旧尾部。"""
    adopted = next((e for e in events if is_adoption(e) and L.same_complete_thread_keys(L.thread_key(e), key)), None)
    if adopted is None or not over_age(L, adopted, policy):
        return None
    result = {"tailId": None, "historicalIndices": [], "newIndices": [], "uncertainIndices": [], "ready": False}
    tails = [e for e in events if e["kind"] == "observe" and e["observed"] == "tail"
             and L.same_complete_thread_keys(L.thread_key(e), key)]
    if not tails:
        return result
    latest, cut = tails[-1], L.parse_at(adopted["readAt"])
    result["tailId"] = latest["tailId"]
    catalog = L.reply_catalog(events, key)
    confirmed = catalog["consumed"] - catalog["unresolved"]
    sources = {e["eventId"]: e for e in events}
    entries = catalog["locations"].get((latest["tailId"], "tail"), [])
    eligible = []
    for index, fp in enumerate(latest["fingerprints"]):
        entry = entries[index] if index < len(entries) else None
        origin = entry[0] if entry else None
        identity = (origin["eventId"], origin["part"], origin["index"]) if origin else None
        source = sources[origin["eventId"]] if origin else latest
        interval = bounds(L, fp, adopted["readAt"]) if "date" in fp else None
        category = "uncertainIndices"
        if interval and interval[0] > L.parse_at(source["readAt"]):
            category = "uncertainIndices"
        elif interval and interval[1] <= cut:
            category = "historicalIndices"
        elif interval and interval[0] > cut and L.parse_at(source["readAt"]) > cut:
            category = "newIndices"
        else:
            base = next((e for e in tails if e["tailId"] == source.get("baseTailId")), None)
            new_extension = (origin and origin["part"] == "tail" and source["result"] == "extended"
                             and source["complete"] and source["anchorUnique"] and not source["reviewRequired"]
                             and origin["index"] >= len(source["fingerprints"]) - source["newCount"])
            if base and L.parse_at(base["readAt"]) >= cut and (identity in confirmed or new_extension):
                category = "newIndices"
        result[category].append(index)
        if category == "newIndices":
            eligible.append(identity)
    result["ready"] = (bool(eligible) and all(identity in confirmed for identity in eligible)
                       and not result["uncertainIndices"] and latest["complete"] and latest["anchorUnique"]
                       and not latest["reviewRequired"])
    return result


def command(L, args, ws):
    if args.restart and args.action != "start":
        raise L.LedgerError("--restart 仅用于开始重新盘点")
    L.load_platform(args.platform)
    profile = L.load_profile(ws)
    tz = L.tz_of(profile)
    with L.lock_mutex(ws):
        lock = L.require_live_lock(ws, args.run, tz, allow_record=True)
        if lock["skill"] != "followup":
            raise L.LedgerError("盘点须持 followup 运行锁")
        if L.read_pause(ws) and args.action != "progress":
            raise L.LedgerError("全局暂停时不浏览平台、不进行盘点；只可本地保存已有进度")
        events, _ = L.read_events(ws)
        account = L.resolve_account(ws, args.platform, args.account, events)
        state = progress(events, args.platform, account)
        now = L.fsutil.now(tz).isoformat()
        base = {"schemaVersion": L.SCHEMA_VERSION, "platform": args.platform, "account": account,
                "runId": args.run, "at": now, "attemptId": "att-" + uuid.uuid4().hex[:12],
                "skill": "followup", "kind": "observe", "reason": args.reason or "首次会话盘点", "evidence": None}
        if args.action == "start":
            if state["status"] == "in_progress" or state["status"] == "completed" and not args.restart:
                return L.emit({"inventory": state, "resumed": state["status"] == "in_progress"})
            base.update(observed="inventory", inventoryId="inv-" + uuid.uuid4().hex[:12],
                        inventoryPhase="started", registeredCount=0, reachedEnd=False)
        else:
            if state["status"] != "in_progress":
                raise L.LedgerError("先开始盘点；完成后的重新盘点须 start --restart")
            base["inventoryId"] = state["inventoryId"]
            if args.action == "record":
                row = json.loads(args.json if args.json is not None else L.sys.stdin.read())
                allowed = {"company", "recruiter", "title", "readAt", "lastActivityLabel", "listState"}
                if (not isinstance(row, dict) or set(row) - allowed
                        or not {"company", "recruiter", "readAt", "lastActivityLabel", "listState"} <= set(row)):
                    raise L.LedgerError("列表输入须含公司、招聘者、readAt、lastActivityLabel、listState，不保存摘要")
                if not isinstance(row["readAt"], str):
                    raise L.LedgerError("readAt 须为实际读取时间字符串")
                if row["lastActivityLabel"] is not None and not isinstance(row["lastActivityLabel"], str):
                    raise L.LedgerError("lastActivityLabel 须为可见时间标签或 null")
                if row["listState"] not in ("active", "draft", "rejected", "job_closed", "risk"):
                    raise L.LedgerError("listState 无效")
                if row["listState"] != "active":
                    return L.emit({"skipped": row["listState"], "inventory": state})
                base.update({k: row[k] for k in ("company", "recruiter")})
                base["readAt"] = L.parse_at(row["readAt"]).astimezone(tz).isoformat()
                activity = L.resolve_time_label(row["lastActivityLabel"], row["readAt"], tz, args.platform)
                if activity["precision"] == "none":
                    activity = {"date": None, "minute": None, "precision": "none"}
                base.update(title=row.get("title"), observed="adopted", adoptionSource="inventory", listActivity=activity)
            else:
                if not args.reason:
                    raise L.LedgerError("盘点进度/完成须注明停止原因或到底依据")
                base.update(observed="inventory", inventoryPhase="completed" if args.action == "finish" else "progress",
                            registeredCount=state["registeredCount"], reachedEnd=args.action == "finish")
        ev = L.prepare_event(base, tz)
        errors = L.validate_event(ev)
        if errors:
            raise L.LedgerError("盘点事件不合格：" + "；".join(errors))
        L.validate_history_event(events, ev)
        if is_adoption(ev):
            duplicate = L.adoption_result(events, ev)
            if duplicate is not None:
                return L.emit(duplicate[0], duplicate[1])
            blocked = L.stoplist_reasons(ws, ev["company"]) or L.company_observation_reasons(events, args.platform, account, ev["company"])
            if blocked:
                return L.emit({"skipped": blocked[0]["type"], "inventory": state})
            if any(e["kind"] != "screen" and L.same_complete_thread_keys(L.thread_key(e), L.thread_key(ev)) for e in events):
                return L.emit({"skipped": "already_registered", "inventory": state})
        L.append_line(ws, ev)
        return L.emit({"ok": True, "event": ev, "inventory": progress([*events, ev], args.platform, account)})

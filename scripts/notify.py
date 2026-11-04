#!/usr/bin/env python3
"""职伴本地通知状态：排队、认领、回执、入站与唤起记录；不执行任何外部动作。

所有命令输出 JSON。退出码：0 已记录；1 被阻断；2 参数、配置或记录错误。
"""

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402

CHANNELS = {"runtime", "lark", "none"}
DEFAULT_WAIT_MINUTES = 60
MAX_SEND_ATTEMPTS = 3
IDEMPOTENCY_MARGIN_MS = 300000
MAX_LAUNCHES_PER_REPLY = 2
TEST_REPLY_WINDOW_SECONDS = 1800
HEADER = "【职伴】"
REPLY_STATES = {"ready", "consumed", "clarifying", "ignored"}
PENDING_REPLY_STATES = {"received", "ready"}
STATE_VERSION = 2
COMMAND_VERSION = 2
PROTOCOL_VERSION = 3
COMMANDS = {"暂停": "pause", "恢复": "resume", "重试": "retry"}
SEND_KINDS = {"decision", "todo", "clarify", "done", "summary", "alert"}
INBOX_HEAD_RE = re.compile(r"^##\s+(D-\d{8}-\d+)\s+(待决定|待处理|已决定|已处理)", re.M)
OPEN_INBOX_STATES = {"待决定", "待处理"}
MAX_MS = 253402300799999  # datetime 的可表示范围；拒绝溢出而非截断
CHECK_HINT = "核对结果请引用本消息回复『已发出 / 未发出』，或回复『{seq} 已发出』"


class NotifyError(Exception):
    pass


def emit(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    return code


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def valid_ms(value):
    return type(value) is int and 0 <= value <= MAX_MS


def now_ms():
    return int(datetime.now().timestamp() * 1000)


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as e:
        raise NotifyError(f"非 JSON 数据：{e}")


def im_config(profile):
    im = profile.get("im", {})
    if not isinstance(im, dict):
        raise NotifyError("im 应为对象；请显式修复，停用通知请设 channel: none")
    channel = im.get("channel", "none")
    if not isinstance(channel, str) or channel not in CHANNELS:
        raise NotifyError("im.channel 无法识别")
    owner = im.get("userId") if channel == "lark" else im.get("ownerId")
    if channel != "none" and not nonempty(owner):
        raise NotifyError("IM 需要非空 ownerId（旧 lark 配置为 userId）")
    transport = im.get("transport", {}) if channel == "runtime" else {}
    if channel == "lark":
        if not owner.startswith("ou_") or not owner[3:].strip():
            raise NotifyError("im.userId：lark 需要本人的 open_id（ou_ 开头且后缀非空）；请显式修复")
        profile_name = im.get("larkProfile", "")
        if not isinstance(profile_name, str) or (profile_name and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", profile_name)):
            raise NotifyError("im.larkProfile 非法")
        transport = {"kind": "lark", "profile": profile_name}
    if not isinstance(transport, dict):
        raise NotifyError("im.transport 应为对象")
    window = transport.get("idempotencyWindowSeconds", 0)
    if type(window) is not int or not 0 <= window <= 86400:
        raise NotifyError("idempotencyWindowSeconds 应为 0–86400 的整数")
    wait = im.get("waitMinutes", DEFAULT_WAIT_MINUTES)
    if type(wait) is not int or not 0 <= wait <= 240:
        raise NotifyError("im.waitMinutes 应为 0–240 的整数；请显式修复配置")
    cfg = {"channel": channel, "ownerId": owner, "transport": transport,
           "waitMinutes": wait, "window": window}
    cfg["route"] = hashlib.sha256(canonical({k: cfg[k] for k in ("channel", "ownerId", "transport")}).encode()).hexdigest()
    return cfg


def context(ws):
    profile = ledger.load_profile(ws)
    return {"cfg": im_config(profile), "tz": ledger.tz_of(profile)}


def im_path(ws):
    return ws / "ledger" / "im.jsonl"


def im_mutex(ws):
    return ledger.file_mutex(ws / "ledger" / ".im.mutex")


def poll_mutex(ws):
    return ledger.file_mutex(ws / "ledger" / ".poll.mutex")


def read_records(ws):
    path = im_path(ws)
    if not path.exists():
        return []
    rows = []
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError as e:
            raise NotifyError(f"ledger/im.jsonl 第 {no} 行损坏：{e}")
        if not isinstance(rec, dict):
            raise NotifyError(f"ledger/im.jsonl 第 {no} 行不是对象")
        rows.append(rec)
    return rows


def append_record(ws, rec, tz):
    """调用者持有 im_mutex。原子替换保留整个追加前缀，失败重放不会读到半行。"""
    rec = dict(rec)
    rec.setdefault("at", ledger.now_iso(tz))
    path = im_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = path.read_text(encoding="utf-8") if path.exists() else ""
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    ledger.write_atomic(path, prefix + canonical(rec) + "\n")
    return rec


def fold(records, cfg=None):
    st = {"out": {}, "outByMessage": {}, "in": {}, "chatId": None, "cursor": 0,
          "launches": [], "retries": [], "retryAt": {}, "retryIndex": {}, "retrySources": set(),
          "commandResults": {}, "lastIncomplete": False, "chatConflicts": [], "incomingRejected": {}}
    # 旧格式没有身份：只有显式核对过的一次性绑定可授予当前路由。
    binding = next((r for r in records if r.get("type") == "binding"), None)
    legacy_route = binding["route"] if binding is not None else None
    def current(r):
        return cfg is None or r.get("route", legacy_route) == cfg["route"]
    for index, r in enumerate(records):
        t = r.get("type")
        if t == "out":
            o = st["out"].setdefault(r["key"], {"key": r["key"], "attempts": 0, "firstAt": r["at"]})
            if r["status"] in ("pending", "handoff"):
                o["attempts"] += 1
            o.update({k: v for k, v in r.items() if k not in ("type", "key")})
            o.setdefault("route", legacy_route)
            o["legacy"] = o.get("protocolVersion") != PROTOCOL_VERSION
            if o["legacy"] and o["status"] in ("pending", "failed"):
                o["status"] = "unknown"
            if o["status"] == "unknown":
                o["deliveryUncertain"] = True  # 后一次明确未投递不能抹去前一次未知发送。
            if r.get("messageId"):
                st["outByMessage"][r["messageId"]] = o
        elif t == "in" and current(r):
            st["in"][r["messageId"]] = dict(r, state=r.get("state") or "received", legacyInbound="route" not in r,
                                               requiresReconcile="route" not in r and r.get("state") == "ready")
        elif t == "state" and r["messageId"] in st["in"]:
            reply = st["in"][r["messageId"]]
            reply.update(state=r["state"], decision=r.get("decision") or reply.get("decision"),
                         note=r.get("note"), stateAt=r["at"], stateVersion=r.get("stateVersion") or reply.get("stateVersion"))
            if reply["legacyInbound"] and r["state"] == "ready":
                reply["requiresReconcile"] = True
            if r.get("confirmedReconciled") is True:
                reply["confirmedReconciled"] = True
        elif t == "command":
            st["commandResults"][r["messageId"]] = r
        elif t == "chat" and current(r):
            if st["chatId"] and st["chatId"] != r["chatId"]:
                st["chatConflicts"].append(r["chatId"])
            else:
                st["chatId"] = r["chatId"]
        elif t == "cursor" and current(r):
            st["cursor"] = max(st["cursor"], r["createTime"])
            st["lastIncomplete"] = r.get("lastIncomplete", False)
        elif t == "rejected" and current(r):
            st["incomingRejected"].setdefault(r["reason"], set()).add(r["messageId"])
        elif t == "launch":
            st["launches"].append(dict(r, index=index))
        elif t == "retry":
            st["retries"].append(r)
            source = r.get("sourceId") or f"legacy:{r['at']}"
            if source not in st["retrySources"]:
                st["retrySources"].add(source)
                for mid in r.get("messageIds") or []:
                    st["retryAt"][mid] = r["at"]
                    st["retryIndex"][mid] = index
    return st


def load_state(ws, cfg=None):
    return fold(read_records(ws), cfg)


def legacy_unbound(ws):
    records = read_records(ws)
    if any(r.get("type") == "binding" for r in records):
        return False
    # handoff / receipt 是同一出站键的增量，没有版本和路由不代表旧记录。
    return (any(r.get("type") in ("in", "chat", "cursor") and "route" not in r for r in records)
            or any(o["legacy"] and o.get("route") is None for o in fold(records)["out"].values()))


def legacy_chats(records):
    chats = {r["chatId"] for r in records if r.get("type") == "chat" and "route" not in r and nonempty(r.get("chatId"))}
    chats.update(o["chatId"] for o in fold(records)["out"].values()
                 if o["legacy"] and o.get("route") is None and o["status"] == "sent" and nonempty(o.get("chatId")))
    return chats


def legacy_unbound_summary(ws, cfg):
    result = {"replies": {"received": 0, "ready": 0, "clarifying": 0},
              "commands": {"pending": 0, "applied": 0}, "unknownOutbox": 0, "bindable": False}
    records = read_records(ws)
    if any(r.get("type") == "binding" for r in records):
        return result
    st = fold(records)
    for r in st["in"].values():
        if not r.get("legacyInbound"):
            continue
        if r["state"] in result["replies"]:
            result["replies"][r["state"]] += 1
        elif r["state"] == "command":
            status = (st["commandResults"].get(r["messageId"]) or {}).get("status")
            result["commands"]["applied" if status == "applied" else "pending"] += 1
    result["unknownOutbox"] = sum(o["legacy"] and o.get("route") is None and o["status"] == "unknown" for o in st["out"].values())
    chats = legacy_chats(records)
    if cfg["channel"] == "lark" and len(chats) == 1:
        established = sync_view(ws, cfg)["chatId"]
        result["bindable"] = established is None or established in chats
    return result


def other_routes_summary(ws, cfg):
    result = {"received": 0, "ready": 0, "clarifying": 0}
    records = read_records(ws)
    binding = next((r for r in records if r.get("type") == "binding"), None)
    for reply in fold(records)["in"].values():
        route = reply.get("route", binding["route"] if binding else None)
        if route is not None and route != cfg["route"] and reply["state"] in result:
            result[reply["state"]] += 1
    return result


OTHER_ROUTES_WARNING = "route_changed_pending：其他路由仍有未处理答复；请恢复原配置，按 inbox/ledger 对账后继续或明确消费，不自动接管"


def cmd_migrate_legacy(args, ws):
    ctx = context(ws)
    cfg = ctx["cfg"]
    if not args.confirmed_owner:
        raise NotifyError("迁移需要 --confirmed-owner：本人/可信 runtime 核对原私聊对方及应用身份")
    if cfg["channel"] != "lark" or args.owner_id != cfg["ownerId"] or not nonempty(args.chat_id):
        raise NotifyError("只允许与当前旧 lark 配置 ownerId 精确一致的显式绑定")
    with poll_mutex(ws), im_mutex(ws):
        records = read_records(ws)
        binding = next((r for r in records if r.get("type") == "binding"), None)
        if binding:
            if binding["route"] != cfg["route"] or binding["chatId"] != args.chat_id:
                raise NotifyError("旧历史已绑定，禁止换 owner/channel/transport 重绑")
            return emit({"status": "duplicate", "route": cfg["route"], "chatId": args.chat_id})
        chats = legacy_chats(records)
        if chats != {args.chat_id}:
            raise NotifyError("无法唯一核对旧私聊，或 chatId 不匹配；不猜测迁移")
        established = sync_view(ws, cfg)["chatId"]
        if established and established != args.chat_id:
            raise NotifyError("当前路由已建立其他私聊，禁止用迁移改写")
        append_record(ws, {"type": "binding", "route": cfg["route"], "channel": cfg["channel"],
                           "ownerId": cfg["ownerId"], "transport": cfg["transport"], "chatId": args.chat_id,
                           "confirmedOwner": True}, ctx["tz"])
    return emit({"status": "bound", "route": cfg["route"], "chatId": args.chat_id})


def first_sent_ms(st, cfg=None):
    times = [o["createTime"] for o in st["out"].values()
             if o["status"] == "sent" and valid_ms(o.get("createTime"))
             and (cfg is None or o.get("route") == cfg["route"])
             and (not o.get("chatId") or o["chatId"] == st["chatId"])]
    return min(times) if times else None


def pending_replies(st):
    return [r for r in st["in"].values() if r["state"] in PENDING_REPLY_STATES]


def needs_reconciliation(reply):
    return bool(reply.get("requiresReconcile") and not reply.get("confirmedReconciled"))


def runnable_replies(st):
    return [r for r in pending_replies(st) if not needs_reconciliation(r)]


def command_records(st):
    return sorted([r for r in st["in"].values() if r.get("state") == "command"], key=command_order)


def command_order(rec):
    # 同毫秒没有可靠的远端先后，安全优先：暂停排在恢复之后。
    pause = normalize_cmd(rec.get("text", "")) == "暂停"
    return (rec.get("createTime", 0), int(pause), rec["messageId"])


def pending_commands(st):
    return [r for r in command_records(st) if not r.get("legacyInbound") and (r.get("commandVersion") or 0) >= COMMAND_VERSION
            and (st["commandResults"].get(r["messageId"]) or {}).get("status") != "applied"]


def legacy_commands(st):
    return [r for r in command_records(st) if r.get("legacyInbound") or (r.get("commandVersion") or 0) < COMMAND_VERSION]


def legacy_command_rows(st):
    return [dict(public_reply(r), commandStatus=(st["commandResults"].get(r["messageId"]) or {}).get("status", "unknown"))
            for r in legacy_commands(st)]


def legacy_consumed(st):
    return [r for r in st["in"].values() if r["state"] == "consumed" and (r.get("stateVersion") or 0) < STATE_VERSION]


def launches_for(st, message_id):
    return [r for r in st["launches"] if r.get("status") == "started"
            and message_id in (r.get("messageIds") or []) and r["index"] > st["retryIndex"].get(message_id, -1)]


def public_reply(r):
    result = {k: r.get(k) for k in ("messageId", "at", "text", "decisionHint", "decisionCandidates", "state", "decision", "note")}
    if needs_reconciliation(r):
        result["needsReconcile"] = True
    if r["state"] in PENDING_REPLY_STATES | {"clarifying"} and (
        not all(nonempty(r.get(k)) for k in ("route", "chatId", "senderId"))
        or r.get("senderType") != "user" or r.get("deleted") is not False
    ):
        result.update(replyToBlockedReason="missing_message_metadata",
                      replyRecovery="request_new_reply_with_decision_id_or_terminal")
    return result


def inbox_items(ws):
    path = ws / "inbox" / "decisions.md"
    return {m.group(1): m.group(2) for m in INBOX_HEAD_RE.finditer(path.read_text(encoding="utf-8"))} if path.exists() else {}


def open_items(ws):
    return sorted(i for i, s in inbox_items(ws).items() if s in OPEN_INBOX_STATES)


def awaiting_check(ws):
    try:
        events, _ = ledger.read_events(ws)
        return [{"decision": a["decision"], "attemptId": a["attemptId"]}
                for a in ledger.attempts_of(events).values() if a["status"] == "uncertain" and a.get("decision")], None
    except (ledger.LedgerError, OSError, UnicodeError, ValueError, TypeError, KeyError) as e:
        return [], f"ledger_unreadable: {e}"


def candidates(ws):
    awaiting, error = awaiting_check(ws)
    items = inbox_items(ws)
    if error:
        return [], error
    uncertain = {a["decision"] for a in awaiting}
    return sorted(i for i, state in items.items() if state in OPEN_INBOX_STATES or (state == "已决定" and i in uncertain)), None


def short_seq(decision):
    return decision.rsplit("-", 1)[1]


def idem_key(ws, key):
    return "jw-" + hashlib.sha1(f"{ws}|{key}".encode()).hexdigest()[:32]


def compose(kind, body, decision=None):
    body = body.strip()
    if kind == "decision":
        return (f"{HEADER}需要你决定 · {decision}\n{body}\n\n"
                f"直接回复本消息，或回复「{short_seq(decision)} 你的决定」。")
    if kind == "todo":
        return f"{HEADER}需要你处理 · {decision}\n{body}\n\n处理好后回复本消息「已处理」。"
    if kind == "done":
        return f"{HEADER}{decision} {body}"
    return f"{HEADER}{body}"


def text_digest(text):
    s = unicodedata.normalize("NFKC", text or "")
    return hashlib.sha1(re.sub(r"\s+", " ", s).strip().encode()).hexdigest()[:12]


def without_hint(text, decision):
    hint = "\n\n" + CHECK_HINT.format(seq=short_seq(decision))
    return text[:-len(hint)] if text.endswith(hint) else text


def default_key(kind, decision, reply_to, run, key, text=None):
    if key:
        return key
    if kind == "done":
        if not decision:
            raise NotifyError("done 需要 --decision")
        return f"done:{decision}:{text_digest(text)}"
    if kind in ("decision", "todo"):
        if not decision:
            raise NotifyError(f"{kind} 需要 --decision")
        return f"{kind}:{decision}"
    if kind == "clarify":
        if not reply_to:
            raise NotifyError("clarify 需要 --reply-to")
        return f"clarify:{reply_to}"
    if kind == "summary":
        if not run:
            raise NotifyError("summary 需要 --run")
        return f"summary:{run}"
    raise NotifyError(f"{kind} 需要 --key")


def retry_deadline(o):
    start, window = o.get("firstHandoffMs"), o.get("window", 0)
    if not valid_ms(start) or type(window) is not int or window * 1000 <= IDEMPOTENCY_MARGIN_MS:
        return None
    deadline = start + window * 1000 - IDEMPOTENCY_MARGIN_MS
    if o.get("idempotencyDeadlineMs", deadline) != deadline:
        return None
    return deadline


def blocked_reason(o, cfg):
    if cfg["channel"] == "none":
        return "im_disabled"
    if o.get("legacy") and o["status"] == "sent":
        return None  # 去重是全历史事实，不把绑定身份当作新发送授权。
    if o.get("route") != cfg["route"]:
        return "route_changed"
    if o.get("legacy") and o["status"] != "sent":
        return "legacy_unknown"
    if o["status"] == "blocked":
        return o.get("error") or "blocked"
    if o["status"] in ("failed", "unknown"):
        if o["attempts"] >= MAX_SEND_ATTEMPTS:
            return "attempt_limit"
        if o.get("deliveryUncertain") or o["status"] == "unknown":
            if not nonempty(o.get("idempotencyKey")):
                return "idempotency_key_unfrozen"
            deadline = retry_deadline(o)
            if deadline is None or not o["firstHandoffMs"] <= now_ms() < deadline:
                return "unknown_delivery"
    return None


def trusted_reply_to(st, cfg, message_id):
    if message_id is None:
        return True
    chat = st["chatId"]
    if not chat:
        return False
    incoming = st["in"].get(message_id)
    if incoming and incoming.get("route") == cfg["route"]:
        return (incoming.get("chatId") == chat and incoming.get("senderId") == cfg["ownerId"]
                and incoming.get("senderType") == "user" and incoming.get("deleted") is False)
    outgoing = st["outByMessage"].get(message_id)
    return bool(outgoing and outgoing.get("route") == cfg["route"] and outgoing["status"] == "sent"
                and outgoing.get("chatId") == chat)


def deliver(ws, ctx, key, text, kind, decision=None, reply_to=None):
    """只排队。保留名称供本地回执/提醒共用；没有执行器或自动重试。"""
    cfg, tz = ctx["cfg"], ctx["tz"]
    if cfg["channel"] == "none":
        return 0, {"status": "skipped", "sent": False, "skipped": "im_disabled", "key": key}
    with im_mutex(ws):
        st = sync_view(ws, cfg)
        if kind == "done" and decision and key.startswith(f"done:{decision}:"):
            base = text_digest(without_hint(text, decision))
            matches = [o for o in st["out"].values() if o.get("kind") == "done" and o.get("decision") == decision
                       and text_digest(without_hint(o.get("text", ""), decision)) == base]
            if matches:
                key = matches[0]["key"]
        o = st["out"].get(key)
        if o:
            reason = blocked_reason(o, cfg)
            if o["status"] != "sent" and not trusted_reply_to(st, cfg, o.get("replyTo")):
                reason = reason or "reply_to_untrusted"
            if reason:
                return 1, {"status": "blocked", "key": key, "reason": reason, "sent": False}
            status = "already_sent" if o["status"] == "sent" else "already_queued"
            return 0, {"status": status, "skipped": status, "key": key, "sent": o["status"] == "sent",
                       "messageId": o.get("messageId"), "reusedStored": o.get("text") != text or o.get("replyTo") != reply_to}
        if not trusted_reply_to(st, cfg, reply_to):
            return 1, {"status": "blocked", "key": key, "reason": "reply_to_untrusted", "sent": False}
        append_record(ws, {"type": "out", "protocolVersion": PROTOCOL_VERSION, "key": key, "kind": kind,
                           "decision": decision, "status": "queued", "text": text, "replyTo": reply_to,
                           "route": cfg["route"], "channel": cfg["channel"], "ownerId": cfg["ownerId"],
                           "transport": cfg["transport"], "window": cfg["window"]}, tz)
    return 0, {"status": "queued", "key": key, "sent": False}


def outbox_rows(ws, ctx):
    rows = []
    st = sync_view(ws, ctx["cfg"])
    for o in st["out"].values():
        if ctx["cfg"]["channel"] == "none":
            continue
        row = dict(o)
        reason = blocked_reason(o, ctx["cfg"])
        if o["status"] != "sent" and not trusted_reply_to(st, ctx["cfg"], o.get("replyTo")):
            reason = reason or "reply_to_untrusted"
        if reason and o["status"] != "sent":
            row.update(status="blocked", reason=reason, deliveryStatus=o["status"])
        if o["status"] == "handoff":
            row["stale"] = now_ms() - o["handoffMs"] > 600000
        rows.append(row)
    return rows


def cmd_outbox(args, ws):
    ctx = context(ws)
    if args.limit <= 0:
        raise NotifyError("limit 必须为正数")
    with im_mutex(ws):
        rows = outbox_rows(ws, ctx)
        if args.stale:
            rows = [o for o in rows if o.get("stale")]
        if not args.claim:
            return emit({"entries": rows[:args.limit]})
        if args.stale:
            raise NotifyError("stale 只能查看，不能认领")
        claimed = []
        for o in rows:
            if o["status"] not in ("queued", "failed", "unknown"):
                continue
            token = uuid.uuid4().hex
            handoff_ms = now_ms()
            start = o.get("firstHandoffMs", handoff_ms)
            deadline = retry_deadline({**o, "firstHandoffMs": start})
            requires_guarantee = bool(o.get("deliveryUncertain") or o["status"] == "unknown")
            if requires_guarantee and (deadline is None or not start <= handoff_ms < deadline):
                continue
            rec = {"type": "out", "key": o["key"], "status": "handoff", "token": token,
                   "attempt": o["attempts"] + 1, "handoffMs": handoff_ms, "firstHandoffMs": start,
                   "idempotencyKey": o.get("idempotencyKey") or idem_key(ws, o["key"]),
                   "idempotencyDeadlineMs": deadline, "deliveryDeadlineMs": deadline if requires_guarantee else None,
                   "requiresIdempotencyGuarantee": requires_guarantee}
            append_record(ws, rec, ctx["tz"])
            claimed.append({**{k: o.get(k) for k in ("key", "text", "replyTo", "ownerId", "channel", "transport", "route")},
                            **{k: rec[k] for k in ("attempt", "token", "idempotencyKey", "firstHandoffMs", "idempotencyDeadlineMs",
                                                   "deliveryDeadlineMs", "requiresIdempotencyGuarantee")},
                            "idempotencyWindowSeconds": o.get("window", 0)})
            if len(claimed) >= args.limit:
                break
        return emit({"entries": claimed})


def cmd_receipt(args, ws):
    ctx = context(ws)
    if not nonempty(args.key) or not nonempty(args.token):
        raise NotifyError("key/token 不能为空")
    if args.create_time is not None and (not valid_ms(args.create_time) or args.create_time > now_ms() + 300000):
        raise NotifyError("createTime 必须为有效毫秒整数")
    if args.result == "sent":
        if not nonempty(args.message_id) or not nonempty(args.chat_id) or args.error is not None:
            raise NotifyError("sent 需要 messageId/chatId，不接受 error")
    elif any(v is not None for v in (args.message_id, args.chat_id, args.create_time)):
        raise NotifyError("未确认送达的回执不能带消息元数据")
    elif args.result == "failed" and not nonempty(args.error):
        raise NotifyError("failed 需要 error 明确说明未投递证据；超时应使用 unknown")
    receipt = {"result": args.result, "messageId": args.message_id, "chatId": args.chat_id,
               "createTime": args.create_time, "error": args.error}
    with im_mutex(ws):
        st = load_state(ws, ctx["cfg"])
        o = st["out"].get(args.key)
        if not o or o.get("token") != args.token:
            raise NotifyError("未知或陈旧的 key/token")
        if o["status"] != "handoff":
            if o.get("receipt") == receipt:
                return emit({"status": "duplicate", "key": args.key})
            raise NotifyError("冲突回执，不能覆盖已有结果")
        if args.result == "sent" and any(other["key"] != args.key and other.get("messageId") == args.message_id
                                         for other in st["out"].values()):
            raise NotifyError("messageId 已绑定其他出站键")
        rec = {"type": "out", "key": args.key, "status": args.result, "receipt": receipt}
        if args.result == "sent":
            deadline = o.get("deliveryDeadlineMs")
            late = deadline is not None and (now_ms() >= deadline or (args.create_time is not None and args.create_time >= deadline))
            rec.update(messageId=args.message_id, chatId=args.chat_id, createTime=args.create_time, deadlineExceeded=late)
        elif args.error is not None:
            rec["error"] = args.error
        append_record(ws, rec, ctx["tz"])
        # chat 可从已保存 sent 推导；重复回执也不依赖第二次写入是否成功。
    return emit({"status": args.result, "key": args.key})


def sync_view(ws, cfg):
    st = load_state(ws, cfg)
    # 按回执落盘顺序，而非排队顺序确定第一次私聊；迟到回执不能改写它。
    for rec in read_records(ws):
        if rec.get("type") != "out" or rec.get("status") != "sent" or not rec.get("chatId"):
            continue
        o = st["out"][rec["key"]]
        if o.get("route") != cfg["route"]:
            continue
        if not st["chatId"] and not o.get("replyTo"):
            st["chatId"] = rec["chatId"]
        elif st["chatId"] and rec["chatId"] != st["chatId"]:
            st["chatConflicts"].append(rec["chatId"])
    st["outByMessage"] = {mid: o for mid, o in st["outByMessage"].items() if o.get("route") == cfg["route"]}
    return st


def poll(ws, ctx):
    cfg = ctx["cfg"]
    st = sync_view(ws, cfg)
    first = first_sent_ms(st, cfg)
    other = other_routes_summary(ws, cfg)
    return {**{k: cfg[k] for k in ("channel", "ownerId", "transport")}, "chatId": st["chatId"],
            "sinceMs": max(st["cursor"], first or 0), "readable": cfg["channel"] != "none" and bool(st["chatId"]) and first is not None,
            "outboxPending": sum(o["status"] != "sent" for o in outbox_rows(ws, ctx)),
            "replies": [public_reply(r) for r in pending_replies(st)], "paused": ledger.read_pause(ws),
            "lastIncomplete": st["lastIncomplete"],
            "legacyRouteUnbound": legacy_unbound(ws), "legacyUnbound": legacy_unbound_summary(ws, cfg), "otherRoutes": other,
            "warning": OTHER_ROUTES_WARNING if any(other.values()) else (
                "legacy_route_unbound：请本人核对后显式 migrate-legacy；旧答复未授予当前路由" if legacy_unbound(ws) else None)}


def normalize_cmd(text):
    return re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", text or ""))


def resolve_decision(ws, st, text, parent_id):
    opened, error = candidates(ws)
    if error:
        return None, []
    if parent_id and parent_id in st["outByMessage"]:
        d = st["outByMessage"][parent_id].get("decision")
        if d:
            return d, [d]
    m = re.search(r"D-\d{8}-\d+", text or "")
    if m:
        return m.group(0), [m.group(0)]
    m = re.match(r"^\s*(\d{1,3})(?=\s|$)", unicodedata.normalize("NFKC", text or ""))
    if m:
        cands = [d for d in opened if int(short_seq(d)) == int(m.group(1))]
        return (cands[0] if len(cands) == 1 else None), cands
    return (opened[0] if len(opened) == 1 else None), opened


def active_test(st, tz, route=None):
    tests = [o for o in st["out"].values() if o.get("kind") == "test" and o["status"] == "sent"
             and (route is None or o.get("route") == route)]
    if not tests:
        return None
    t = max(tests, key=lambda o: o.get("createTime") or int(ledger.parse_at(o["at"]).timestamp() * 1000))
    age = (datetime.now(tz) - ledger.parse_at(t["at"])).total_seconds()
    answered = any(r["state"] == "test" and (r.get("testKey") == t["key"] if r.get("testKey") else r["at"] >= t["at"])
                   for r in st["in"].values())
    return t if age <= TEST_REPLY_WINDOW_SECONDS and not answered else None


def run_command(ws, ctx, rec):
    mid, tz = rec["messageId"], ctx["tz"]
    action = COMMANDS[rec["command"]]
    if action in ("pause", "resume"):
        with ledger.lock_mutex(ws):
            previous = ledger.read_json(ledger.pause_path(ws)) or {}
            ids = list(previous.get("imCommands") or [])
            all_st = load_state(ws)
            applied = set(ids) | {m for m, r in all_st["commandResults"].items() if r.get("status") == "applied"}
            latest = max((command_order(r) for r in command_records(all_st) if r["messageId"] in applied), default=(-1, -1, ""))
            stale = command_order(rec) < latest
            if action == "resume" and previous.get("paused") and previous.get("by") != "im":
                # 安全暂停发生后才收到的旧 resume 也不能解除它；秒级时间采取保守上界。
                stale |= rec.get("createTime", 0) < int(ledger.parse_at(previous["at"]).timestamp() * 1000) + 1000
            if mid not in ids and not stale:
                ids.append(mid)
                pause = {"paused": action == "pause", "at": ledger.now_iso(tz), "by": "im",
                         "reason": f"本人 IM 指令「{rec['command']}」", "imCommands": ids[-50:]}
                ledger.write_atomic(ledger.pause_path(ws), canonical(pause) + "\n")
        if stale:
            return "旧指令未改变当前暂停状态，请核对后重新发送。"
        if action == "pause":
            return "已暂停：不会再发出新消息，正在运行的一轮会在下一次外发前停下。回「恢复」继续。"
        n = len(runnable_replies(load_state(ws, ctx["cfg"])))
        return "已恢复。" + (f"有 {n} 条答复待执行，马上处理。" if n else "")
    with im_mutex(ws):
        st = load_state(ws, ctx["cfg"])
        previous = [r for r in st["retries"] if r.get("sourceId") == mid]
        all_st = load_state(ws)
        applied = {m for m, r in all_st["commandResults"].items() if r.get("status") == "applied"} | all_st["retrySources"]
        latest = max((command_order(r) for r in command_records(all_st) if r["messageId"] in applied), default=(-1, -1, ""))
        if not previous and command_order(rec) < latest:
            return "旧重试指令未重置唤起次数；请核对后重新发送。"
        ids = previous[-1].get("messageIds", []) if previous else [r["messageId"] for r in runnable_replies(st)]
        if not previous:
            append_record(ws, {"type": "retry", "sourceId": mid, "messageIds": ids}, tz)
    return f"好的，重新执行 {len(ids)} 条未完成的答复。" if ids else "当前没有未完成的答复。"


def dispatch_commands(ws, ctx):
    applied = []
    for rec in pending_commands(load_state(ws, ctx["cfg"])):
        with im_mutex(ws):
            previous = load_state(ws)["commandResults"].get(rec["messageId"])
            if not previous or previous.get("status") != "pending":
                append_record(ws, {"type": "command", "messageId": rec["messageId"], "status": "pending"}, ctx["tz"])
        text = run_command(ws, ctx, rec)
        with im_mutex(ws):
            append_record(ws, {"type": "command", "messageId": rec["messageId"], "status": "applied", "text": text}, ctx["tz"])
        applied.append(rec["command"])
    return applied


def validate_batch(batch):
    if not isinstance(batch, dict) or set(batch) != {"since", "complete", "messages"}:
        raise NotifyError("批次需要 since/complete/messages")
    if not valid_ms(batch["since"]) or type(batch["complete"]) is not bool or not isinstance(batch["messages"], list):
        raise NotifyError("批次字段类型错误")
    fields = {"messageId", "chatId", "createTime", "senderId", "senderType", "text", "parentId", "deleted"}
    for m in batch["messages"]:
        if not isinstance(m, dict) or set(m) != fields:
            raise NotifyError("消息字段错误")
        if any(not nonempty(m[k]) for k in ("messageId", "chatId", "senderId", "senderType")):
            raise NotifyError("消息 ID/身份不能为空")
        if not isinstance(m["text"], str) or not valid_ms(m["createTime"]) or type(m["deleted"]) is not bool:
            raise NotifyError("消息正文/时间/删除状态类型错误")
        if m["parentId"] is not None and not nonempty(m["parentId"]):
            raise NotifyError("parentId 应为非空 ID 或 null")


def incoming(ws, ctx, batch):
    validate_batch(batch)
    cfg, tz = ctx["cfg"], ctx["tz"]
    with poll_mutex(ws):
        with im_mutex(ws):
            plan = poll(ws, ctx)
            if not plan["readable"] or batch["since"] > plan["sinceMs"]:
                raise NotifyError("私聊不可读或 since 超过当前游标")
            st = sync_view(ws, cfg)
            first = first_sent_ms(st, cfg)
            rejects, accepted = Counter(), []
            history = load_state(ws)
            all_in = history["in"]
            versions = {}
            for m in batch["messages"]:
                versions.setdefault(m["messageId"], set()).add(canonical(m))
            conflicts = {mid for mid, values in versions.items() if len(values) > 1 and mid not in all_in}
            rejected_ids = set()
            # schema 已整批验证；内容冲突只拒收该 ID，不让边界编辑卡住独立的新暂停。
            for m in sorted(batch["messages"], key=command_order):
                mid = m["messageId"]
                previous = all_in.get(mid)
                payload = (previous.get("payload") or {k: previous[k] for k in m if k in previous}) if previous else {}
                reason = None
                if mid in all_in and mid not in st["in"]:
                    reason = "foreign_route"
                elif mid in conflicts or any(m[k] != v for k, v in payload.items()):
                    reason = "payload_changed"
                elif m["deleted"]:
                    reason = "deleted"
                elif m["senderType"] != "user" or m["senderId"] != cfg["ownerId"]:
                    reason = "sender"
                elif m["chatId"] != plan["chatId"]:
                    reason = "chat"
                elif m["createTime"] < first:
                    reason = "before_boundary"
                elif m["createTime"] > now_ms() + 300000:
                    reason = "future"
                elif m["messageId"] in history["outByMessage"]:
                    reason = "outgoing"
                if reason:
                    rejects[reason] += 1
                    if reason in ("payload_changed", "foreign_route"):
                        rejected_ids.add((reason, mid))
                    continue
                accepted.append(m)
            for reason, mid in sorted(rejected_ids):
                if mid not in st["incomingRejected"].get(reason, set()):
                    append_record(ws, {"type": "rejected", "route": cfg["route"], "reason": reason, "messageId": mid}, tz)
            opened, ledger_error = candidates(ws)
            test = active_test(st, tz, cfg["route"])
            new = []
            for m in accepted:
                mid = m["messageId"]
                if mid in st["in"]:
                    continue
                rec = {"type": "in", "route": cfg["route"], "payload": m, **m,
                       "at": datetime.fromtimestamp(m["createTime"] / 1000, tz).isoformat(timespec="milliseconds")}
                cmd = normalize_cmd(m["text"])
                if cmd in COMMANDS:
                    rec.update(command=cmd, commandVersion=COMMAND_VERSION, state="command")
                else:
                    hint, cands = resolve_decision(ws, st, m["text"], m["parentId"])
                    rec.update(decisionHint=hint, decisionCandidates=cands)
                    explicit = re.search(r"D-\d{8}-\d+", m["text"]) or re.match(r"^\s*\d{1,3}(?=\s|$)", unicodedata.normalize("NFKC", m["text"]))
                    if ledger_error:
                        rec.update(state="received", note="ledger_unreadable")
                    elif test and not explicit and not (m["parentId"] in st["outByMessage"] and st["outByMessage"][m["parentId"]].get("decision")) and (
                        m["parentId"] == test.get("messageId") or (m["parentId"] is None and not opened and not cands)
                    ):
                        rec.update(state="test", testKey=test["key"])
                        test = None
                    elif not opened and not cands:
                        rec.update(state="ignored", note="没有待答复的事项")
                    else:
                        rec["state"] = "received"
                append_record(ws, rec, tz)
                st["in"][mid] = rec
                new.append(rec)
            cursor = max([st["cursor"], *[m["createTime"] for m in accepted]])
            # complete 只确认从本次请求起的连续前缀；陈旧批次不能清除后来的 incomplete。
            covered_until = max([batch["since"], *[m["createTime"] for m in accepted]])
            incomplete = not batch["complete"] or (st["lastIncomplete"] and covered_until < plan["sinceMs"])
            append_record(ws, {"type": "cursor", "route": cfg["route"], "createTime": cursor, "lastIncomplete": incomplete}, tz)
        commands = dispatch_commands(ws, ctx)
        # 重放补齐已入账消息的缺失回执；唯一键确保写入中断也不丢、不重复。
        st = load_state(ws, cfg)
        for rec in st["in"].values():
            mid = rec["messageId"]
            if rec["state"] == "command":
                result = st["commandResults"].get(mid)
                if rec.get("legacyInbound") or (rec.get("commandVersion") or 0) < COMMAND_VERSION:
                    continue  # 历史指令只在 status 汇总，不逐条刷屏或误报已 applied。
                if result and result.get("status") == "applied":
                    deliver(ws, ctx, f"cmd:{mid}", compose("ack", result["text"]), "ack", reply_to=mid)
            else:
                text = "测试成功：消息收发都正常。" if rec["state"] == "test" else (
                    "目前没有等你答复的事项。支持的指令：暂停、恢复、重试。" if rec["state"] == "ignored" else (
                        "已收到。当前处于暂停状态，回「恢复」后执行。" if ledger.read_pause(ws) else "已收到，正在处理。"))
                deliver(ws, ctx, f"ack:{mid}", compose("ack", text), "ack", reply_to=mid)
        return {"new": [public_reply(r) for r in new], "commands": commands, "rejected": dict(rejects),
                "legacyCommands": legacy_command_rows(st), **poll(ws, ctx)}


def unfinished_launches(st):
    finished = {r["launchId"] for r in st["launches"] if r.get("status") != "started"}
    return [r for r in st["launches"] if r.get("status") == "started" and r["launchId"] not in finished]


def launch_prompt(ws):
    return (f"使用 job-mate 的 followup 技能，工作区：{ws}。本轮由 IM 监听器唤起："
            "先处理本人在 IM 中的答复（notify.py replies --state received 与 --state ready）；"
            "ready 只执行已经写入 inbox/ledger 的结论，不重新解释原始答复；"
            "执行已决定的事项，处理相关会话后结束；不进入等待，不在终端提问。")


def claim_trigger(ws, ctx):
    with poll_mutex(ws), ledger.lock_mutex(ws), im_mutex(ws):
        st = load_state(ws, ctx["cfg"])
        reason = "paused" if ledger.read_pause(ws) else ("running_lock" if ledger.lock_active(ws, ctx["tz"]) else None)
        if unfinished_launches(st):
            reason = "unfinished_launch"
        if not reason and any(other_routes_summary(ws, ctx["cfg"]).values()):
            reason = "route_changed_pending"
        unbound = legacy_unbound_summary(ws, ctx["cfg"])
        if not reason and (unbound["replies"]["received"] or unbound["replies"]["ready"]):
            reason = "legacy_route_unbound"
        replies = runnable_replies(st)
        ids = [r["messageId"] for r in replies if len(launches_for(st, r["messageId"])) < MAX_LAUNCHES_PER_REPLY]
        if not reason and not ids:
            reason = "attempt_limit" if replies else ("needs_reconcile" if any(needs_reconciliation(r) for r in pending_replies(st)) else "no_replies")
        if not reason:
            launch_id = "launch-" + uuid.uuid4().hex
            append_record(ws, {"type": "launch", "launchId": launch_id, "status": "started", "messageIds": ids}, ctx["tz"])
            return emit({"status": "started", "launchId": launch_id, "messageIds": ids, "prompt": launch_prompt(ws)})
    if reason == "attempt_limit":
        for reply in replies:
            mid = reply["messageId"]
            finished = [r for r in st["launches"] if r.get("status") != "started" and mid in r.get("messageIds", [])]
            if finished and finished[-1]["status"] in ("failed", "timeout"):
                continue  # finish 已排队失败提醒，不再追加另一条。
            deliver(ws, ctx, f"giveup:{mid}:{st['retryAt'].get(mid, '')}", compose(
                "alert", f"你的答复已执行 {MAX_LAUNCHES_PER_REPLY} 次仍未处理完，不再自动执行。"
                         "回「重试」再执行一次，或回到电脑前处理。"), "alert", reply_to=mid)
    return emit({"status": "blocked", "reason": reason}, 1)


def cmd_trigger(args, ws):
    ctx = context(ws)
    if args.action == "claim":
        return claim_trigger(ws, ctx)
    with poll_mutex(ws), ledger.lock_mutex(ws), im_mutex(ws):
        st = load_state(ws, ctx["cfg"])
        if not args.confirmed_stopped:
            raise NotifyError("finish 需要 --confirmed-stopped：确认实例及全部后代已停止")
        launches = [r for r in st["launches"] if r["launchId"] == args.launch_id]
        if not launches:
            raise NotifyError("未知 launchId")
        if launches[-1]["status"] != "started":
            if launches[-1]["status"] != args.status:
                raise NotifyError("冲突的 launch finish")
            result = "duplicate"
        else:
            append_record(ws, {"type": "launch", "launchId": args.launch_id, "status": args.status,
                               "confirmedStopped": True, "messageIds": launches[0].get("messageIds", [])}, ctx["tz"])
            result = args.status
    if args.status in ("failed", "timeout"):
        again = any(len(launches_for(st, mid)) < MAX_LAUNCHES_PER_REPLY for mid in launches[0].get("messageIds", []))
        nxt = "会自动再试一次" if again else "不再自动执行，回「重试」再执行一次，或回到电脑前处理"
        what = "超时" if args.status == "timeout" else "失败"
        deliver(ws, ctx, f"alert:{args.launch_id}", compose("alert", f"执行你的答复时 Agent {what}。答复已保存，{nxt}。"), "alert")
    return emit({"status": result, "launchId": args.launch_id})


def read_text_arg(args):
    text = args.text if args.text is not None else sys.stdin.read()
    if not text.strip():
        raise NotifyError("消息正文为空")
    return text


def cmd_send(args, ws):
    ctx = context(ws)
    if args.kind in ("decision", "todo") and args.decision not in inbox_items(ws):
        raise NotifyError("inbox 中没有该事项：先写入 inbox，再排队")
    if args.decision is not None and not re.fullmatch(r"D-\d{8}-\d+", args.decision):
        raise NotifyError("decision ID 非法")
    text = compose(args.kind, read_text_arg(args), args.decision)
    if args.kind == "done" and args.decision:
        opened, _ = candidates(ws)
        if args.decision in opened:
            text += "\n\n" + CHECK_HINT.format(seq=short_seq(args.decision))
    key = default_key(args.kind, args.decision, args.reply_to, args.run, args.key, text)
    if not nonempty(key) or (args.reply_to is not None and not nonempty(args.reply_to)):
        raise NotifyError("key/replyTo 不能为空")
    code, out = deliver(ws, ctx, key, text, args.kind, args.decision, args.reply_to)
    return emit(out, code)


def cmd_poll(args, ws):
    return emit(poll(ws, context(ws)))


def cmd_incoming(args, ws):
    try:
        batch = json.loads(args.json if args.json is not None else sys.stdin.read())
    except ValueError as e:
        raise NotifyError(f"incoming JSON 非法：{e}")
    return emit(incoming(ws, context(ws), batch))


def cmd_replies(args, ws):
    cfg = context(ws)["cfg"]
    st = load_state(ws, cfg)
    other = other_routes_summary(ws, cfg)
    return emit({"replies": [public_reply(r) for r in st["in"].values() if not args.state or r["state"] == args.state],
                 "openItems": open_items(ws), "legacyUnbound": legacy_unbound_summary(ws, cfg), "otherRoutes": other,
                 "warning": OTHER_ROUTES_WARNING if any(other.values()) else None})


def cmd_reply_state(args, ws):
    ctx = context(ws)
    with im_mutex(ws):
        st = sync_view(ws, ctx["cfg"])
        r = st["in"].get(args.message)
        if not r or r["state"] in ("command", "test"):
            raise NotifyError("找不到可处理的普通回复")
        if args.state in ("consumed", "ready") and not args.decision:
            raise NotifyError(f"{args.state} 需要 --decision")
        if needs_reconciliation(r) and not args.confirmed_reconciled:
            raise NotifyError("旧 ready 尚未完成 F1/F2 对账，不能执行或改变状态；需要 --confirmed-reconciled")
        if args.confirmed_reconciled:
            if not nonempty(args.note) or not nonempty(args.decision) or (r.get("decision") and r["decision"] != args.decision):
                raise NotifyError("确认对账须保留原决定 ID，并用 --note 说明 F1/F2 核对结果")
        if args.state == "clarifying" and (not trusted_reply_to(st, ctx["cfg"], args.message) or not any(
            o.get("kind") == "clarify" and o.get("replyTo") == args.message and o["status"] == "sent"
            and o.get("route") == ctx["cfg"]["route"] and o.get("chatId") == st["chatId"] and nonempty(o.get("messageId"))
            for o in st["out"].values()
        )):
            raise NotifyError("追问尚未确认 sent 或引用缺少可信元数据；保留原状态，不能把 queued/blocked/failed 当成已追问")
        append_record(ws, {"type": "state", "messageId": args.message, "state": args.state,
                           "stateVersion": STATE_VERSION, "decision": args.decision, "note": args.note,
                           "confirmedReconciled": args.confirmed_reconciled}, ctx["tz"])
    return emit({"ok": True, "messageId": args.message, "state": args.state, "decision": args.decision})


def cmd_test(args, ws):
    code, out = deliver(ws, context(ws), f"test:{uuid.uuid4().hex}", compose("test", "测试消息：请引用本消息回复「收到」，确认回复也能被读取。"), "test")
    return emit(out, code)


def cmd_status(args, ws):
    ctx = context(ws)
    st = sync_view(ws, ctx["cfg"])
    rows = outbox_rows(ws, ctx)
    awaiting, error = awaiting_check(ws)
    warnings = [error] if error else []
    if any(other_routes_summary(ws, ctx["cfg"]).values()):
        warnings.append(OTHER_ROUTES_WARNING)
    if legacy_unbound(ws):
        warnings.append("legacy_route_unbound：旧私聊/游标/答复须显式核对绑定；不自动继承")
    if (ws / "ledger" / "listener.json").exists():
        warnings.append("旧监听器已不再支持，请本人卸载")
    return emit({**poll(ws, ctx), "chatEstablished": bool(st["chatId"]), "chatConflicts": sorted(set(st["chatConflicts"])),
                 "openItems": open_items(ws), "pendingReplies": [public_reply(r) for r in pending_replies(st)],
                 "readyReplies": [public_reply(r) for r in st["in"].values() if r["state"] == "ready"],
                 "needsReconcile": [dict(public_reply(r), reason="旧版答复需核对 inbox 与 ledger decision；确认路由不代表确认业务完成")
                                    for r in st["in"].values() if r in legacy_consumed(st) or needs_reconciliation(r)],
                 "legacyCommands": legacy_command_rows(st),
                 "incomingRejected": {reason: sorted(ids) for reason, ids in st["incomingRejected"].items()},
                 "outbox": dict(Counter(o["status"] for o in rows)),
                 "unconfirmedSends": [{k: o.get(k) for k in ("key", "kind", "decision", "status", "attempts", "reason", "deliveryStatus", "legacy")}
                                      for o in rows if o["status"] != "sent"],
                 "stale": [{k: o.get(k) for k in ("key", "token", "attempt")} for o in rows if o.get("stale")],
                 "lateSends": [{k: o.get(k) for k in ("key", "token", "deliveryDeadlineMs")} for o in rows if o.get("deadlineExceeded")],
                 "routeConflicts": [o["key"] for o in rows if o.get("route") != ctx["cfg"]["route"]],
                 "awaitingCheck": awaiting, "unfinishedLaunches": unfinished_launches(st), "warnings": warnings})


def build_parser():
    p = argparse.ArgumentParser(prog="notify.py", description=__doc__.splitlines()[0])
    p.add_argument("--workspace")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send", help="只排队，不表示已发送")
    s.add_argument("--kind", required=True, choices=sorted(SEND_KINDS))
    for name in ("decision", "reply-to", "run", "key", "text"):
        s.add_argument("--" + name)
    sub.add_parser("poll", help="只读同步计划")
    inc = sub.add_parser("incoming")
    inc.add_argument("--json")
    o = sub.add_parser("outbox")
    o.add_argument("--claim", action="store_true")
    o.add_argument("--stale", action="store_true")
    o.add_argument("--limit", type=int, default=100)
    r = sub.add_parser("receipt")
    r.add_argument("--key", required=True)
    r.add_argument("--token", required=True)
    r.add_argument("--result", required=True, choices=["sent", "failed", "unknown"])
    for name in ("message-id", "chat-id", "error"):
        r.add_argument("--" + name)
    r.add_argument("--create-time", type=int)
    replies = sub.add_parser("replies")
    replies.add_argument("--state", choices=sorted(REPLY_STATES | {"received", "command", "test"}))
    state = sub.add_parser("reply-state")
    state.add_argument("--message", required=True)
    state.add_argument("--state", required=True, choices=sorted(REPLY_STATES))
    state.add_argument("--decision")
    state.add_argument("--note")
    state.add_argument("--confirmed-reconciled", action="store_true")
    sub.add_parser("test", help="只排队测试通知")
    sub.add_parser("status")
    migration = sub.add_parser("migrate-legacy")
    migration.add_argument("--owner-id", required=True)
    migration.add_argument("--chat-id", required=True)
    migration.add_argument("--confirmed-owner", action="store_true")
    trigger = sub.add_parser("trigger").add_subparsers(dest="action", required=True)
    trigger.add_parser("claim")
    finish = trigger.add_parser("finish")
    finish.add_argument("--launch-id", required=True)
    finish.add_argument("--status", required=True, choices=["ok", "failed", "timeout", "stopped"])
    finish.add_argument("--confirmed-stopped", action="store_true")
    return p


HANDLERS = {"send": cmd_send, "poll": cmd_poll, "incoming": cmd_incoming, "outbox": cmd_outbox,
            "receipt": cmd_receipt, "replies": cmd_replies, "reply-state": cmd_reply_state,
            "test": cmd_test, "status": cmd_status, "trigger": cmd_trigger, "migrate-legacy": cmd_migrate_legacy}


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return HANDLERS[args.cmd](args, ledger.find_workspace(args.workspace))
    except (NotifyError, ledger.LedgerError, OSError, UnicodeError, ValueError, TypeError, KeyError, OverflowError) as e:
        return emit({"error": str(e)}, 2)


if __name__ == "__main__":
    sys.exit(main())

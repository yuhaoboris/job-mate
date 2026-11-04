#!/usr/bin/env python3
"""职伴本地通知状态：排队、认领、回执与入站；不执行任何外部动作。

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
import fsutil  # noqa: E402
import ledger  # noqa: E402

CHANNELS = {"lark", "none"}
DEFAULT_WAIT_MINUTES = 60
MAX_SEND_ATTEMPTS = 3
TEST_REPLY_WINDOW_SECONDS = 1800
HEADER = "【职伴】"
REPLY_STATES = {"ready", "consumed", "clarifying", "ignored"}
PENDING_REPLY_STATES = {"received", "ready"}
STATE_VERSION = 2
COMMAND_VERSION = 2
PROTOCOL_VERSION = 3
COMMANDS = {"暂停": "pause", "恢复": "resume"}
SEND_KINDS = {"decision", "todo", "clarify", "done", "summary", "alert"}
INBOX_HEAD_RE = re.compile(r"^##\s+(D-\d{8}-\d+)\s+(待决定|待处理|已决定|已处理)", re.M)
OPEN_INBOX_STATES = {"待决定", "待处理"}
MAX_MS = 253402300799999  # datetime 的可表示范围；拒绝溢出而非截断
CHECK_HINT = "核对结果请引用本消息回复『已发出 / 未发出』，或回复『{seq} 已发出』"


class NotifyError(Exception):
    pass


def emit(obj, code=0):
    return ledger.emit(obj, code)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def valid_ms(value):
    return type(value) is int and 0 <= value <= MAX_MS


def now_ms():
    return int(fsutil.now().timestamp() * 1000)


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as e:
        raise NotifyError(f"非 JSON 数据：{e}")


def im_config(profile):
    """供通知与工作区体检共用；配置不合法时显式报错。"""
    im = profile.get("im", {"channel": "none"})
    if not isinstance(im, dict):
        raise NotifyError("im 应为对象")
    channel = im.get("channel")
    if not isinstance(channel, str) or channel not in CHANNELS:
        raise NotifyError("im.channel 应为 lark 或 none")
    allowed = {"channel"} if channel == "none" else {"channel", "ownerId", "larkProfile", "waitMinutes", "larkCommand"}
    if set(im) - allowed:
        raise NotifyError("im 含未知字段")
    if channel == "lark" and (not nonempty(im.get("ownerId")) or not nonempty(im.get("larkProfile"))):
        raise NotifyError("lark 需要非空 ownerId 与 larkProfile")
    wait = im.get("waitMinutes", DEFAULT_WAIT_MINUTES)
    if type(wait) is not int or not 0 <= wait <= 240:
        raise NotifyError("im.waitMinutes 应为 0–240 的整数")
    command = im.get("larkCommand")
    if "larkCommand" in im and (not isinstance(command, list) or not command or not all(nonempty(v) for v in command)):
        raise NotifyError("im.larkCommand 应为非空字符串数组")
    cfg = {"channel": channel, "ownerId": im.get("ownerId"), "larkProfile": im.get("larkProfile"),
           "waitMinutes": wait}
    if command is not None:
        cfg["larkCommand"] = command
    cfg["route"] = hashlib.sha256(canonical({k: cfg[k] for k in ("channel", "ownerId", "larkProfile")}).encode()).hexdigest()
    return cfg


def context(ws):
    profile = ledger.load_profile(ws)
    return {"cfg": im_config(profile), "tz": ledger.tz_of(profile)}


def im_path(ws):
    return ws / "ledger" / "im.jsonl"


def im_mutex(ws, write=True):
    return ledger.operation_mutex(ws, ".im.mutex", write=write)


def poll_mutex(ws):
    return ledger.operation_mutex(ws, ".poll.mutex")


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
          "commandResults": {}, "lastIncomplete": False, "chatConflicts": [], "incomingRejected": {}}
    for r in records:
        t = r.get("type")
        current = cfg is None or r.get("route") == cfg["route"]
        if t == "out":
            o = st["out"].setdefault(r["key"], {"key": r["key"], "attempts": 0, "firstAt": r["at"]})
            if r["status"] == "handoff":
                o["attempts"] += 1
            o.update({k: v for k, v in r.items() if k not in ("type", "key")})
            if r.get("messageId"):
                st["outByMessage"][r["messageId"]] = o
        elif t == "in" and current:
            st["in"][r["messageId"]] = dict(r, state=r.get("state") or "received")
        elif t == "state" and r["messageId"] in st["in"]:
            reply = st["in"][r["messageId"]]
            reply.update(state=r["state"], decision=r.get("decision") or reply.get("decision"),
                         note=r.get("note"), stateAt=r["at"])
        elif t == "command":
            st["commandResults"][r["messageId"]] = r
        elif t == "cursor" and current:
            st["cursor"] = max(st["cursor"], r["createTime"])
            st["lastIncomplete"] = r.get("lastIncomplete", False)
        elif t == "rejected" and current:
            st["incomingRejected"].setdefault(r["reason"], set()).add(r["messageId"])
    return st


def load_state(ws, cfg=None):
    return fold(read_records(ws), cfg)


def first_sent_ms(st, cfg=None):
    return st.get("firstSentMs")


def pending_replies(st):
    return [r for r in st["in"].values() if r["state"] in PENDING_REPLY_STATES]


def command_records(st):
    return sorted([r for r in st["in"].values() if r.get("state") == "command"], key=command_order)


def command_order(rec):
    # 同毫秒没有可靠的远端先后，安全优先：暂停排在恢复之后。
    pause = normalize_cmd(rec.get("text", "")) == "暂停"
    return (rec.get("createTime", 0), int(pause), rec["messageId"])


def pending_commands(st):
    return [r for r in command_records(st)
            if (st["commandResults"].get(r["messageId"]) or {}).get("status") != "applied"]


def public_reply(r):
    return {k: r.get(k) for k in ("messageId", "at", "text", "decisionHint", "decisionCandidates", "state", "decision", "note")}


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


def result_body(text, decision):
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


def blocked_reason(o, cfg):
    if cfg["channel"] == "none":
        return "im_disabled"
    if o.get("route") != cfg["route"]:
        return "route_changed"
    if o["status"] == "blocked":
        return o.get("error") or "blocked"
    if o["status"] == "unknown":
        return "unknown_delivery"
    if o["status"] == "failed" and o["attempts"] >= MAX_SEND_ATTEMPTS:
        return "attempt_limit"
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
            digest = text_digest(result_body(text, decision))
            match = next((entry for entry in st["out"].values()
                          if entry["key"].startswith(f"done:{decision}:") and entry.get("kind") == "done"
                          and text_digest(result_body(entry["text"], decision)) == digest), None)
            if match:
                key = match["key"]
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
                           "larkProfile": cfg["larkProfile"]}, tz)
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
    with im_mutex(ws, write=args.claim):
        rows = outbox_rows(ws, ctx)
        if args.stale:
            rows = [o for o in rows if o.get("stale")]
        if not args.claim:
            return emit({"entries": rows[:args.limit]})
        if args.stale:
            raise NotifyError("stale 只能查看，不能认领")
        claimed = []
        for o in rows:
            if o["status"] not in ("queued", "failed"):
                continue
            rec = {"type": "out", "key": o["key"], "status": "handoff", "token": uuid.uuid4().hex,
                   "attempt": o["attempts"] + 1, "handoffMs": now_ms(),
                   "idempotencyKey": o.get("idempotencyKey") or idem_key(ws, o["key"])}
            append_record(ws, rec, ctx["tz"])
            claimed.append({**{k: o.get(k) for k in ("key", "text", "replyTo", "ownerId", "channel", "larkProfile", "route")},
                            **{k: rec[k] for k in ("attempt", "token", "idempotencyKey")}})
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
            rec.update(messageId=args.message_id, chatId=args.chat_id, createTime=args.create_time)
        elif args.error is not None:
            rec["error"] = args.error
        append_record(ws, rec, ctx["tz"])
        # chat 可从已保存 sent 推导；重复回执也不依赖第二次写入是否成功。
    return emit({"status": args.result, "key": args.key})


def sync_view(ws, cfg):
    st = load_state(ws, cfg)
    # 按回执落盘顺序，而非排队顺序确定第一次私聊；迟到回执不能改写它。
    for rec in read_records(ws):
        if (rec.get("type") != "out" or rec.get("status") != "sent"
                or not nonempty(rec.get("chatId")) or not valid_ms(rec.get("createTime"))):
            continue
        o = st["out"][rec["key"]]
        if o.get("route") != cfg["route"]:
            continue
        if not st["chatId"] and not o.get("replyTo"):
            st["chatId"] = rec["chatId"]
            st["firstSentMs"] = rec["createTime"]
        elif st["chatId"] and rec["chatId"] != st["chatId"]:
            st["chatConflicts"].append(rec["chatId"])
    st["outByMessage"] = {mid: o for mid, o in st["outByMessage"].items() if o.get("route") == cfg["route"]}
    return st


def poll(ws, ctx):
    cfg = ctx["cfg"]
    st = sync_view(ws, cfg)
    first = first_sent_ms(st, cfg)
    return {**{k: cfg[k] for k in ("channel", "ownerId", "larkProfile")}, "chatId": st["chatId"],
            "sinceMs": max(st["cursor"], first or 0), "readable": cfg["channel"] != "none" and bool(st["chatId"]) and first is not None,
            "outboxPending": sum(o["status"] != "sent" for o in outbox_rows(ws, ctx)),
            "replies": [public_reply(r) for r in pending_replies(st)], "paused": ledger.read_pause(ws),
            "lastIncomplete": st["lastIncomplete"]}


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
    age = (fsutil.now(tz) - ledger.parse_at(t["at"])).total_seconds()
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
        n = len(pending_replies(load_state(ws, ctx["cfg"])))
        return "已恢复。" + (f"有 {n} 条答复待执行，马上处理。" if n else "")


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
            if any(m["chatId"] != plan["chatId"] for m in batch["messages"]):
                raise NotifyError("批次 chatId 与当前私聊不一致")
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
                if result and result.get("status") == "applied":
                    deliver(ws, ctx, f"cmd:{mid}", compose("ack", result["text"]), "ack", reply_to=mid)
            else:
                text = "测试成功：消息收发都正常。" if rec["state"] == "test" else (
                    "目前没有等你答复的事项。支持的指令：暂停、恢复。" if rec["state"] == "ignored" else (
                        "已收到。当前处于暂停状态，回「恢复」后执行。" if ledger.read_pause(ws) else "已收到，正在处理。"))
                deliver(ws, ctx, f"ack:{mid}", compose("ack", text), "ack", reply_to=mid)
        return {"new": [public_reply(r) for r in new], "commands": commands, "rejected": dict(rejects), **poll(ws, ctx)}


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
    st = load_state(ws, context(ws)["cfg"])
    return emit({"replies": [public_reply(r) for r in st["in"].values() if not args.state or r["state"] == args.state],
                 "openItems": open_items(ws)})


def cmd_reply_state(args, ws):
    ctx = context(ws)
    with im_mutex(ws):
        st = sync_view(ws, ctx["cfg"])
        r = st["in"].get(args.message)
        if not r or r["state"] in ("command", "test"):
            raise NotifyError("找不到可处理的普通回复")
        if args.state in ("consumed", "ready") and not args.decision:
            raise NotifyError(f"{args.state} 需要 --decision")
        if args.state == "clarifying" and (not trusted_reply_to(st, ctx["cfg"], args.message) or not any(
            o.get("kind") == "clarify" and o.get("replyTo") == args.message and o["status"] == "sent"
            and o.get("route") == ctx["cfg"]["route"] and o.get("chatId") == st["chatId"] and nonempty(o.get("messageId"))
            for o in st["out"].values()
        )):
            raise NotifyError("追问尚未确认 sent 或引用缺少可信元数据；保留原状态，不能把 queued/blocked/failed 当成已追问")
        append_record(ws, {"type": "state", "messageId": args.message, "state": args.state,
                           "stateVersion": STATE_VERSION, "decision": args.decision, "note": args.note}, ctx["tz"])
    return emit({"ok": True, "messageId": args.message, "state": args.state, "decision": args.decision})


def cmd_test(args, ws):
    code, out = deliver(ws, context(ws), f"test:{uuid.uuid4().hex}", compose("test", "测试消息：请引用本消息回复「收到」，确认回复也能被读取。"), "test")
    return emit(out, code)


def cmd_status(args, ws):
    ctx = context(ws)
    st = sync_view(ws, ctx["cfg"])
    rows = outbox_rows(ws, ctx)
    awaiting, error = awaiting_check(ws)
    return emit({**poll(ws, ctx), "chatEstablished": bool(st["chatId"]), "chatConflicts": sorted(set(st["chatConflicts"])),
                 "openItems": open_items(ws), "pendingReplies": [public_reply(r) for r in pending_replies(st)],
                 "readyReplies": [public_reply(r) for r in st["in"].values() if r["state"] == "ready"],
                 "incomingRejected": {reason: sorted(ids) for reason, ids in st["incomingRejected"].items()},
                 "outbox": dict(Counter(o["status"] for o in rows)),
                 "unconfirmedSends": [{k: o.get(k) for k in ("key", "kind", "decision", "status", "attempts", "reason", "deliveryStatus")}
                                      for o in rows if o["status"] != "sent"],
                 "stale": [{k: o.get(k) for k in ("key", "token", "attempt")} for o in rows if o.get("stale")],
                 "routeConflicts": [o["key"] for o in rows if o.get("route") != ctx["cfg"]["route"] and o["status"] != "sent"],
                 "awaitingCheck": awaiting, "warnings": [error] if error else []})


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
    sub.add_parser("test", help="只排队测试通知")
    sub.add_parser("status")
    return p


HANDLERS = {"send": cmd_send, "poll": cmd_poll, "incoming": cmd_incoming, "outbox": cmd_outbox,
            "receipt": cmd_receipt, "replies": cmd_replies, "reply-state": cmd_reply_state,
            "test": cmd_test, "status": cmd_status}


def entry_mode(args):
    if args.cmd == "outbox":
        return "write" if args.claim else "read"
    return "read" if args.cmd in {"poll", "replies", "status"} else "write"


def main(argv=None):
    fsutil.utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        ws = ledger.find_workspace(args.workspace)
        with ledger.gated(ws, entry_mode(args)):
            return HANDLERS[args.cmd](args, ws)
    except (NotifyError, ledger.LedgerError, OSError, UnicodeError, ValueError, TypeError, KeyError, OverflowError) as e:
        return emit(ledger.error_result(e), 2)


if __name__ == "__main__":
    sys.exit(main())

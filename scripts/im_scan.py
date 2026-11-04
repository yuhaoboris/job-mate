"""飞书扫描记录的纯校验与投影；无 I/O、无业务指令状态机。"""
import hashlib
import json
import re

VERSION = 1
MAX_MS = 253402300799999
HEADER = {"type", "scanVersion", "phase", "scanId", "route", "chatId", "startMs", "endMs", "startedMs", "revision"}
PROGRESS = {"base", "nextToken", "seenTokens", "lastMs", "versions", "pageCount", "hasMore"}


class ScanError(ValueError):
    def __init__(self):
        super().__init__("invalid_scan_state")


def need(value):
    if not value:
        raise ScanError()


def digest(value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hex_string(value, length):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value) is not None


def ms(value):
    return type(value) is int and 0 <= value <= MAX_MS


def native_id(value, prefix):
    return isinstance(value, str) and value.startswith(prefix + "_") and hex_string(value[len(prefix) + 1:], 32)


def initial(record):
    return {**record, "nextToken": None, "seenTokens": [], "lastMs": -1, "versions": {},
            "pageCount": 0, "recordDigest": digest(record)}


def transition(previous, record):
    """只校验进度结构；成功 incoming 的持久凭据由 project 单独校验。"""
    complete = record.get("phase") == "complete"
    need(set(record) == HEADER | PROGRESS | ({"scanThroughMs"} if complete else set()))
    need(record["phase"] in ("checkpoint", "complete"))
    need(all(type(record[k]) is type(previous[k]) and record[k] == previous[k] for k in HEADER - {"phase", "revision"}))
    need(type(record["revision"]) is int and record["revision"] == previous["revision"] + 1)
    need(record["base"] == previous["recordDigest"])
    need(type(record["hasMore"]) is bool and record["hasMore"] is (not complete))
    pages = record["pageCount"]
    need(type(pages) is int and 1 <= pages - previous["pageCount"] <= 20)
    tokens = record["seenTokens"]
    need(isinstance(tokens, list) and all(isinstance(t, str) and bool(t) for t in tokens))
    need(len(tokens) == len(set(tokens)) and len(tokens) == pages - int(complete))
    need(tokens[:len(previous["seenTokens"])] == previous["seenTokens"])
    need(record["nextToken"] is None if complete else record["nextToken"] == tokens[-1])
    last = record["lastMs"]
    need(type(last) is int and (last == -1 or previous["startMs"] <= last < previous["endMs"]))
    need(last >= previous["lastMs"])
    versions = record["versions"]
    need(isinstance(versions, dict) and all(native_id(mid, "om") and hex_string(h, 64) for mid, h in versions.items()))
    need(all(versions.get(mid) == h for mid, h in previous["versions"].items()))
    need(len(versions) - len(previous["versions"]) <= (pages - previous["pageCount"]) * 50)
    need(bool(versions) == (last != -1))
    if not complete:
        need(bool(versions))
    if complete:
        need(ms(record["scanThroughMs"]) and record["scanThroughMs"] == previous["endMs"])


def progress(previous, next_token, tokens, last_ms, versions, pages, complete):
    record = {k: previous[k] for k in HEADER}
    record.update(phase="complete" if complete else "checkpoint", revision=previous["revision"] + 1,
                  base=previous["recordDigest"], nextToken=next_token, seenTokens=tokens,
                  lastMs=last_ms, versions=versions, pageCount=pages, hasMore=not complete)
    if complete:
        record["scanThroughMs"] = previous["endMs"]
    transition(previous, record)
    return record


def proof(record):
    return {"version": VERSION, "scanId": record["scanId"], "revision": record["revision"],
            "digest": digest(record), "versions": record["versions"], "versionsDigest": digest(record["versions"])}


def remember(observed, scan_id, versions):
    need(isinstance(versions, dict))
    memo = observed.setdefault(scan_id, {})
    for mid, value in versions.items():
        need(native_id(mid, "om") and hex_string(value, 64) and memo.get(mid, value) == value)
        memo[mid] = value


def project(records, now_ms):
    """按落盘顺序验证意图、incoming 凭据、checkpoint、终页水位，不能从中间页猜完整。"""
    active, through, used, evidence, completed_at, observed = {}, {}, set(), {}, {}, {}
    routes, anchors, cursors = {}, {}, {}
    for index, original in enumerate(records):
        kind = original.get("type")
        if any(k in original for k in ("scanVersion", "scanId", "scanThroughMs")):
            need(kind == "scan")
        if "scanBatch" in original:
            need(kind == "cursor")
        if "scanMessage" in original:
            need(kind == "in")
            m = original["scanMessage"]
            need(isinstance(m, dict) and set(m) == {"scanId", "revision", "digest"})
            key = next((k for k, v in active.items() if v["scanId"] == m["scanId"]), None)
            need(key is not None and key[0] == original.get("route") and key[1] == original.get("chatId"))
            need(type(m["revision"]) is int and m["revision"] == active[key]["revision"] + 1)
            remember(observed, m["scanId"], {original.get("messageId"): m["digest"]})
        if kind == "out":
            key = original.get("key")
            old = routes.setdefault(key, {})
            old.update(original)
            if original.get("status") == "sent" and ms(original.get("createTime")) and not old.get("replyTo"):
                route, chat = old.get("route"), original.get("chatId")
                if route and chat:
                    anchors.setdefault(route, (chat, original["createTime"]))
        elif kind == "cursor":
            route = original.get("route")
            value = original.get("createTime")
            if ms(value):
                cursors[route] = max(cursors.get(route, 0), value)
            if "scanBatch" in original:
                need(ms(value))
                p = original["scanBatch"]
                need(isinstance(p, dict) and set(p) == {"version", "scanId", "revision", "digest", "versions", "versionsDigest"})
                need(type(p["version"]) is int and p["version"] == VERSION and hex_string(p["scanId"], 32))
                need(type(p["revision"]) is int and p["revision"] > 0 and hex_string(p["digest"], 64))
                key = next((k for k, v in active.items() if v["scanId"] == p["scanId"]), None)
                need(key is not None and key[0] == route and p["revision"] == active[key]["revision"] + 1)
                remember(observed, p["scanId"], p["versions"])
                need(hex_string(p["versionsDigest"], 64) and p["versionsDigest"] == digest(p["versions"]))
                evidence_key = (p["scanId"], p["revision"])
                # 崩溃重读可能取得不同的不透明续页 token；凭据不是 checkpoint，不能据此跳页。
                proofs = evidence.setdefault(evidence_key, {})
                need(proofs.get(p["digest"], p["versionsDigest"]) == p["versionsDigest"])
                proofs[p["digest"]] = p["versionsDigest"]
        elif kind == "scan":
            record = {k: v for k, v in original.items() if k != "at"}
            need(HEADER <= record.keys())
            need(record["type"] == "scan" and type(record["scanVersion"]) is int and record["scanVersion"] == VERSION)
            need(hex_string(record["scanId"], 32) and hex_string(record["route"], 64) and native_id(record["chatId"], "oc"))
            need(type(record["revision"]) is int and record["revision"] >= 0)
            start, end, started = record["startMs"], record["endMs"], record["startedMs"]
            need(ms(start) and ms(end) and ms(started) and start < end and start % 1000 == end % 1000 == 0)
            need(end == (started + 999) // 1000 * 1000 and end <= (now_ms + 999) // 1000 * 1000)
            route, chat = record["route"], record["chatId"]
            key = (route, chat)
            need(route in anchors and anchors[route][0] == chat)
            if record["phase"] == "intent":
                need(set(record) == HEADER and record["revision"] == 0 and key not in active and record["scanId"] not in used)
                basis = max(anchors[route][1], cursors.get(route, 0), through.get(key, 0))
                need(start == max(0, basis // 1000 - 1) * 1000)
                active[key] = initial(record)
                used.add(record["scanId"])
            else:
                need(key in active and record["scanId"] == active[key]["scanId"])
                transition(active[key], record)
                need(evidence.get((record["scanId"], record["revision"]), {}).get(digest(record)) == digest(record["versions"]))
                if record["phase"] == "complete":
                    need(record["scanThroughMs"] >= through.get(key, 0))
                    through[key] = record["scanThroughMs"]
                    completed_at[key] = index
                    del active[key]
                else:
                    active[key] = {**record, "recordDigest": digest(record)}
    return {"active": active, "through": through, "evidence": evidence, "completedAt": completed_at, "observed": observed}

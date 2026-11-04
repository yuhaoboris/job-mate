#!/usr/bin/env python3
"""job-mate 事件记录工具：校验、外发检查、追加、配额、会话汇总、决策执行状态、统计、运行锁、全局暂停、停止名单。

仅用 Python 标准库。所有命令向 stdout 输出一个 JSON 对象。
退出码：0 成功 / 允许；1 不允许（检查不通过、额度用尽、锁被占用）；2 错误（参数、文件损坏、工作区缺失、未持锁）。
"""

import argparse
import json
import os
import re
import socket
import sys
import unicodedata
import uuid
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fsutil  # noqa: E402

SUITE_VERSION = "1.0.0"
SCHEMA_VERSION = 2
MARKER = "job-mate.json"

ACTION_KINDS = {"greet", "intro", "reply", "followup", "resume"}
CONVERSATION_KINDS = ACTION_KINDS - {"greet"}
ACTION_STATUSES = {"pending", "sent", "uncertain", "failed_no_send", "skipped", "already_contacted"}
STANDALONE_STATUSES = {"pending", "skipped", "already_contacted"}
OBSERVATIONS = {
    "replied", "resume_delivered", "resume_viewed", "interview_invited",
    "rejected", "job_closed", "declined", "reopened",
    "manual", "released", "adopted", "paused", "unpaused", "risk", "limit_hit",
}
SKILLS = {"setup", "apply", "followup", "user"}
REQUIRED = [
    "schemaVersion", "at", "platform", "account", "runId", "attemptId", "skill", "kind",
    "company", "title", "location", "recruiter", "reason", "evidence",
]
# 这些观察结果表示该公司不再联系
COMPANY_BLOCKING_OBSERVATIONS = {"rejected", "risk"}
# 这些观察结果表示会话已结束；declined（我方判定职位不符）可由 reopened 重新打开
THREAD_CLOSING_OBSERVATIONS = {"rejected", "job_closed", "risk", "declined"}
# 决定的完成标记：全部动作做完后最后写入的、带 decision 的 unpaused
DECISION_DONE_OBSERVATION = "unpaused"
# 已建立联系的尝试状态（用于首次联系去重）
CONTACT_STATUSES = {"sent", "already_contacted", "pending", "uncertain"}

DEFAULT_LIMITS = {
    "greetsPerRun": 10,
    "greetsPerDay": 30,
    "detailsPerRun": 50,
    "minSendIntervalSeconds": 5,
    "dedupScope": "company",
}
DEFAULT_TZ = "Asia/Shanghai"
DEFAULT_LOCK_TTL_MINUTES = 60

JOB_ID_RE = re.compile(r"/job_detail/([^/?#]+?)\.html")
DECISION_RE = re.compile(r"^D-\d{8}-\d+$")
STEP_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class LedgerError(Exception):
    pass


# ---------- 输出 ----------

def emit(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    return code


# ---------- 工作区 ----------

def find_workspace(explicit):
    if explicit:
        ws = Path(explicit).expanduser().resolve()
        if not (ws / MARKER).is_file():
            raise LedgerError(f"指定目录不是工作区（缺少 {MARKER}）：{ws}")
        return ws
    cur = Path.cwd().resolve()
    for d in [cur, *cur.parents]:
        if (d / MARKER).is_file():
            return d
    raise LedgerError(f"从当前目录向上未找到 {MARKER}；请用 --workspace 指定，或先运行 setup")


def read_json(path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise LedgerError(f"{path.name} 不是有效 JSON：{e}")


def load_profile(ws):
    return read_json(ws / "profile.json", {}) or {}


def limits_of(profile):
    merged = dict(DEFAULT_LIMITS)
    merged.update((profile.get("limits") or {}))
    return merged


def tz_of(profile):
    name = ((profile.get("schedule") or {}).get("timezone")) or DEFAULT_TZ
    return fsutil.tz_from_name(name)


def default_account(ws, platform):
    meta = read_json(ws / MARKER, {}) or {}
    acc = (meta.get("accounts") or {}).get(platform)
    if isinstance(acc, dict):
        return acc.get("accountKey")
    return acc


def events_path(ws):
    return ws / "ledger" / "events.jsonl"


def now_iso(tz):
    return fsutil.now(tz).isoformat(timespec="seconds")


def parse_at(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("时间缺少时区")
    return dt


file_mutex = fsutil.locked
write_atomic = fsutil.write_text_atomic


# ---------- 规范化 ----------

def norm_company(name):
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", name).lower()
    return re.sub(r"\s+", "", s)


def norm_text(s):
    if not s:
        return ""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).lower()


def normalize_job_url(url):
    """去掉查询参数和片段（lid、securityId 等随会话变化）。"""
    if not url:
        return url
    return re.split(r"[?#]", url, maxsplit=1)[0]


def job_id_from_url(url):
    if not url:
        return None
    m = JOB_ID_RE.search(url)
    return m.group(1) if m else None


# ---------- 事件读写 ----------

def validate_acknowledged(items):
    if not isinstance(items, list):
        return ["acknowledged 须为数组"]
    errs = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"shown", "precision", "kind", "count"}:
            errs.append("acknowledged 每项须包含 shown、precision、kind、count，且不含原文等其他字段")
            continue
        if not isinstance(item["shown"], str) or not item["shown"].strip():
            errs.append("acknowledged.shown 须为非空页面时间标签")
        if item["precision"] not in ("day", "minute"):
            errs.append("acknowledged.precision 须为 day 或 minute")
        if item["kind"] not in ("text", "resume", "contact", "interview"):
            errs.append("acknowledged.kind 须为 text、resume、contact 或 interview")
        if type(item["count"]) is not int or item["count"] <= 0:
            errs.append("acknowledged.count 须为正整数")
    return errs


def validate_event(ev):
    errs = []
    for k in REQUIRED:
        if k not in ev:
            errs.append(f"缺少字段 {k}")
    if errs:
        return errs
    if ev["schemaVersion"] != SCHEMA_VERSION:
        errs.append(f"schemaVersion 应为 {SCHEMA_VERSION}")
    try:
        parse_at(ev["at"])
    except (TypeError, ValueError) as e:
        errs.append(f"at 无效：{e}")
    if ev["skill"] not in SKILLS:
        errs.append(f"skill 无效：{ev['skill']}")
    kind = ev["kind"]
    if kind in ACTION_KINDS:
        if ev.get("status") not in ACTION_STATUSES:
            errs.append(f"动作事件 status 无效：{ev.get('status')}")
        if "observed" in ev:
            errs.append("动作事件不应有 observed")
    elif kind == "observe":
        if ev.get("observed") not in OBSERVATIONS:
            errs.append(f"观察事件 observed 无效：{ev.get('observed')}")
        if "status" in ev:
            errs.append("观察事件不应有 status")
    else:
        errs.append(f"kind 无效：{kind}")
    for k in ("platform", "account", "runId", "attemptId", "company"):
        if not ev.get(k):
            errs.append(f"{k} 不能为空")
    if not ev.get("reason"):
        errs.append("reason 不能为空")
    if ev.get("decision") is not None and not DECISION_RE.match(str(ev["decision"])):
        errs.append("decision 应形如 D-20261028-01")
    if ev.get("step") is not None:
        if not STEP_RE.match(str(ev["step"])):
            errs.append("step 只能用字母、数字、- 和 _，最长 32 个字符")
        if not ev.get("decision"):
            errs.append("step 须与 decision 一起使用")
    if "tags" in ev and (not isinstance(ev["tags"], list)
                         or any(not isinstance(tag, str) or not tag.strip() for tag in ev["tags"])):
        errs.append("tags 须为非空字符串组成的数组")
    if kind == "observe" and ev.get("observed") == "adopted":
        if ev["skill"] != "user":
            errs.append("adopted 只允许 skill: user，由本人点名交给 Agent")
        try:
            if parse_at(ev.get("readAt")) > parse_at(ev["at"]):
                errs.append("readAt 不能晚于事件时间")
        except (TypeError, ValueError):
            errs.append("adopted 须带有效的带时区 readAt")
    if "acknowledged" in ev:
        if kind != "observe" or ev.get("observed") != "released":
            errs.append("acknowledged 只用于 released")
        errs.extend(validate_acknowledged(ev["acknowledged"]))
    if "boundaryAt" in ev:
        errs.append("boundaryAt 由 threads 计算，不能在事件中指定")
    return errs


def read_events(ws, strict=True):
    path = events_path(ws)
    events, errors = [], []
    if not path.exists():
        return events, errors
    with path.open(encoding="utf-8") as f:
        for no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError as e:
                errors.append({"line": no, "error": f"JSON 损坏：{e.msg}"})
                continue
            if not isinstance(ev, dict):
                errors.append({"line": no, "error": "不是 JSON 对象"})
                continue
            errs = validate_event(ev)
            if errs:
                errors.append({"line": no, "error": "；".join(errs)})
                continue
            ev["_line"] = no
            events.append(ev)
    if strict and errors:
        raise LedgerError(
            "事件记录有损坏行，禁止外发，请先人工处理："
            + "；".join(f"第 {e['line']} 行 {e['error']}" for e in errors[:5])
        )
    return events, errors


def append_line(ws, ev):
    path = events_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({k: v for k, v in ev.items() if not k.startswith("_")}, ensure_ascii=False) + "\n"
    # 与运行锁、停止名单锁分离，允许持有它们时追加，不嵌套同一路径。
    with file_mutex(path.with_name(".events.mutex")):
        with path.open("a", encoding="utf-8", newline="") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())


# ---------- 停止名单 ----------
# 只支持以下 YAML 子集（setup 生成、本人手工编辑都按此格式）：
#
#   - name: 软通动力
#     aliases: [iSoftStone, "软通,集团"]
#     reason: 人员外包
#     source: 本人
#     added: 2026-10-27
#
# 值可以不加引号；含冒号、逗号、方括号、引号或首尾空格时用双引号（JSON 转义规则）。
# 不支持多行字符串、嵌套和锚点；遇到无法识别的行直接报错，而不是猜。

STOPLIST_KEYS = ["name", "aliases", "reason", "source", "added"]


def _yaml_scalar(raw):
    raw = raw.strip()
    if raw.startswith('"'):
        try:
            val, end = json.JSONDecoder().raw_decode(raw)
        except json.JSONDecodeError:
            raise ValueError(f"双引号字符串无效：{raw}")
        if raw[end:].strip() or not isinstance(val, str):
            raise ValueError(f"双引号字符串后有多余内容：{raw}")
        return val
    if raw.startswith("'"):
        if len(raw) < 2 or not raw.endswith("'"):
            raise ValueError(f"单引号字符串未闭合：{raw}")
        return raw[1:-1].replace("''", "'")
    return raw


def _yaml_list(raw):
    raw = raw.strip()
    if not (raw.startswith("[") and raw.endswith("]")):
        raise ValueError("aliases 须写成 [a, b] 形式")
    s, i, items = raw[1:-1], 0, []
    decoder = json.JSONDecoder()
    while i < len(s):
        while i < len(s) and s[i] in " \t":
            i += 1
        if i >= len(s):
            break
        if s[i] == '"':
            try:
                val, end = decoder.raw_decode(s, i)
            except json.JSONDecodeError:
                raise ValueError(f"aliases 中的双引号字符串无效：{raw}")
            items.append(val)
            i = end
        elif s[i] == "'":
            j = i + 1
            buf = []
            while True:
                if j >= len(s):
                    raise ValueError(f"aliases 中的单引号字符串未闭合：{raw}")
                if s[j] == "'":
                    if j + 1 < len(s) and s[j + 1] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    break
                buf.append(s[j])
                j += 1
            items.append("".join(buf))
            i = j + 1
        else:
            j = s.find(",", i)
            j = len(s) if j < 0 else j
            token = s[i:j].strip()
            if any(c in token for c in "\"'[]"):
                raise ValueError(f"aliases 中含引号或方括号的值须整体加双引号：{token}")
            if token:
                items.append(token)
            i = j
        while i < len(s) and s[i] in " \t":
            i += 1
        if i < len(s):
            if s[i] != ",":
                raise ValueError(f"aliases 格式错误：{raw}")
            i += 1
    return items


def parse_stoplist(text):
    """解析停止名单文本，返回条目列表；每条带 _start/_end 行号（0 起，左闭右开）。"""
    entries, cur = [], None
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        no = idx + 1
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            if cur is not None:
                cur["_end"] = idx
            cur = {"_start": idx}
            entries.append(cur)
            stripped = stripped[2:].strip()
        elif cur is None or not line.startswith((" ", "\t")):
            raise LedgerError(f"stoplist.yaml 第 {no} 行无法识别：{line}")
        if ":" not in stripped:
            raise LedgerError(f"stoplist.yaml 第 {no} 行缺少「键: 值」：{line}")
        key, _, val = stripped.partition(":")
        key = key.strip()
        if key not in STOPLIST_KEYS:
            raise LedgerError(f"stoplist.yaml 第 {no} 行未知字段：{key}")
        try:
            cur[key] = _yaml_list(val) if key == "aliases" else _yaml_scalar(val)
        except ValueError as e:
            raise LedgerError(f"stoplist.yaml 第 {no} 行：{e}")
    if cur is not None:
        cur["_end"] = len(lines)
    for i, e in enumerate(entries, 1):
        if not e.get("name"):
            raise LedgerError(f"stoplist.yaml 第 {i} 条缺少 name")
        e.setdefault("aliases", [])
    return entries


def stoplist_path(ws):
    return ws / "stoplist.yaml"


def stoplist_mutex(ws):
    return file_mutex(ws / "ledger" / ".stoplist.mutex")


def load_stoplist(ws):
    path = stoplist_path(ws)
    if not path.exists():
        return []
    return parse_stoplist(path.read_text(encoding="utf-8"))


def public_entry(e):
    return {k: v for k, v in e.items() if not k.startswith("_")}


def _yaml_quote(s):
    s = str(s)
    if s == "" or re.search(r"[:#\[\],\"'{}\\]|^[\s\-]|\s$", s):
        return json.dumps(s, ensure_ascii=False)
    return s


def format_stop_entry(entry):
    lines = [f"- name: {_yaml_quote(entry['name'])}"]
    aliases = ", ".join(_yaml_quote(a) for a in entry.get("aliases") or [])
    lines.append(f"  aliases: [{aliases}]")
    for k in ("reason", "source", "added"):
        if entry.get(k):
            lines.append(f"  {k}: {_yaml_quote(entry[k])}")
    return "\n".join(lines) + "\n"


def stoplist_text_with(text, entries):
    if text and not text.endswith("\n"):
        text += "\n"
    return text + "".join(format_stop_entry(e) for e in entries)


def find_stop_entry(entries, name):
    for e in entries:
        if match_company(name, [e["name"], *e["aliases"]])[0] == "exact":
            return e
    return None


def match_company(company, names):
    """返回 ("exact"|"suspect"|None, 命中的名称)。包含关系只算疑似。"""
    c = norm_company(company)
    if not c:
        return None, None
    for n in names:
        if norm_company(n) == c:
            return "exact", n
    for n in names:
        nn = norm_company(n)
        if len(nn) >= 2 and len(c) >= 2 and (nn in c or c in nn):
            return "suspect", n
    return None, None


# ---------- 尝试状态 ----------

def resolve_status(statuses):
    # 任何一条 sent 都按已发送处理：发送事实不能被后来的记录抹掉
    return "sent" if "sent" in statuses else statuses[-1]


def attempts_of(events):
    """按 attemptId 归并动作事件，返回 {attemptId: {...}}。"""
    out = {}
    for ev in events:
        if ev["kind"] not in ACTION_KINDS:
            continue
        a = out.setdefault(ev["attemptId"], {"events": []})
        a["events"].append(ev)
    for aid, a in out.items():
        evs = a["events"]
        first = evs[0]
        statuses = [e["status"] for e in evs]
        a.update({
            "attemptId": aid,
            "kind": first["kind"],
            "platform": first["platform"],
            "account": first["account"],
            "runId": first["runId"],
            "company": first["company"],
            "title": first["title"],
            "location": first["location"],
            "recruiter": next((e["recruiter"] for e in reversed(evs) if e.get("recruiter")), None),
            "jobId": next((e.get("jobId") for e in evs if e.get("jobId")), None),
            "decision": next((e.get("decision") for e in evs if e.get("decision")), None),
            "step": next((e.get("step") for e in evs if e.get("step")), None),
            "status": resolve_status(statuses),
            "everPending": "pending" in statuses,
            "firstAt": first["at"],
            "lastAt": evs[-1]["at"],
        })
    return out


def attempt_blocks(a, run_id):
    s = a["status"]
    if s in ("sent", "already_contacted", "uncertain", "pending"):
        return True
    if s == "failed_no_send":
        return a["runId"] == run_id
    return False


def check_transition(prior, ev):
    """校验同一 attempt 的状态转换；不合法时抛出 LedgerError。"""
    status = ev["status"]
    if not prior:
        if status not in STANDALONE_STATUSES:
            raise LedgerError(f"attempt {ev['attemptId']} 没有先写 pending，不能直接记 {status}")
        return
    first = prior[0]
    if first["kind"] not in ACTION_KINDS:
        raise LedgerError(f"attemptId {ev['attemptId']} 已被观察事件使用")
    for k in ("kind", "platform", "company"):
        if norm_text(first[k]) != norm_text(ev[k]):
            raise LedgerError(f"attempt {ev['attemptId']} 的 {k} 与首条记录不一致（{first[k]} → {ev[k]}）")
    current = resolve_status([e["status"] for e in prior])
    allowed = {
        "pending": {"sent", "uncertain", "failed_no_send"},
        "uncertain": {"sent", "failed_no_send"},
        "failed_no_send": {"sent"},
    }.get(current, set())
    if status not in allowed:
        raise LedgerError(f"attempt {ev['attemptId']} 不能从 {current} 转为 {status}")
    if current in ("uncertain", "failed_no_send") and ev["skill"] != "user":
        raise LedgerError(f"attempt {ev['attemptId']} 为 {current}，只能由本人核对后记录（skill 须为 user）")


# ---------- 会话 ----------
# 会话以「平台 + 公司 + 招聘者」为键（BOSS 的会话按招聘者划分）。
# 动作事件的招聘者取同一 attempt 中最后看到的名字，pending 时尚未看到也能归到同一会话。

FOLLOWUP_MAX_TOTAL = 3
FOLLOWUP_MAX_UNANSWERED = 2
FOLLOWUP_COOLDOWN_DAYS = 7


def thread_key(ev):
    return ev["platform"], norm_company(ev["company"]), norm_text(ev.get("recruiter"))


def build_threads(events, platform=None):
    atts = attempts_of(events)
    threads = {}

    def recruiter_of(ev):
        if ev["kind"] in ACTION_KINDS:
            return atts[ev["attemptId"]]["recruiter"]
        return ev.get("recruiter")

    for ev in events:
        if platform and ev["platform"] != platform:
            continue
        rec = recruiter_of(ev)
        key = (ev["platform"], norm_company(ev["company"]), norm_text(rec))
        t = threads.setdefault(key, {
            "platform": ev["platform"], "company": ev["company"], "recruiter": rec,
            "title": None, "jobId": None, "lastAt": None,
            "lastOutbound": None, "lastObserved": None, "lastReplyAt": None,
            "followupsSent": 0, "followupsSinceReply": 0, "lastFollowupAt": None,
            "manual": False, "pausedBy": [], "closed": None, "closedBy": [], "unresolved": [], "outboundTexts": [],
            "readAt": None, "boundaryAt": None, "acknowledged": [],
        })
        t["title"] = ev.get("title") or t["title"]
        t["jobId"] = ev.get("jobId") or t["jobId"]
        t["lastAt"] = ev["at"]
        if ev["kind"] == "observe":
            obs = ev["observed"]
            t["lastObserved"] = {"observed": obs, "at": ev["at"]}
            if obs == "replied":
                t["followupsSinceReply"] = 0
                t["lastReplyAt"] = ev["at"]
            elif obs == "manual":
                t["manual"] = True
            elif obs == "released":
                t["manual"] = False
                t["acknowledged"] = ev.get("acknowledged", [])
            elif obs == "adopted":
                t["readAt"] = ev["readAt"]
                t["manual"] = False
            elif obs == "paused":
                d = ev.get("decision") or "未注明"
                if d not in t["pausedBy"]:
                    t["pausedBy"].append(d)
            elif obs == "unpaused":
                d = ev.get("decision")
                t["pausedBy"] = [x for x in t["pausedBy"] if d and x != d] if d else []
            elif obs in THREAD_CLOSING_OBSERVATIONS:
                if obs not in t["closedBy"]:
                    t["closedBy"].append(obs)
            elif obs == "reopened":
                # 只解除「职位不符」；职位关闭、对方拒绝、风险不能被重新打开
                t["closedBy"] = [c for c in t["closedBy"] if c != "declined"]
            hard = [c for c in t["closedBy"] if c != "declined"]
            t["closed"] = hard[0] if hard else (t["closedBy"][0] if t["closedBy"] else None)
            boundary = ev["readAt"] if obs == "adopted" else ev["at"] if obs == "released" else None
            if boundary and (t["boundaryAt"] is None or parse_at(boundary) > parse_at(t["boundaryAt"])):
                t["boundaryAt"] = boundary
        elif ev["status"] == "sent":
            if t["boundaryAt"] is None or parse_at(ev["at"]) > parse_at(t["boundaryAt"]):
                t["boundaryAt"] = ev["at"]
            t["lastOutbound"] = {"kind": ev["kind"], "at": ev["at"]}
            if ev.get("text") or ev["kind"] == "resume":
                t["outboundTexts"].append({"kind": ev["kind"], "at": ev["at"], "text": ev.get("text")})
            if ev["kind"] == "followup":
                t["followupsSent"] += 1
                t["followupsSinceReply"] += 1
                t["lastFollowupAt"] = ev["at"]
    for key, t in threads.items():
        for a in atts.values():
            if a["status"] in ("pending", "uncertain") and \
                    (a["platform"], norm_company(a["company"]), norm_text(a["recruiter"])) == key:
                t["unresolved"].append({"attemptId": a["attemptId"], "kind": a["kind"], "status": a["status"]})
    return threads


def followup_eligibility(t, now, tz):
    if t["closed"]:
        return False, f"会话已结束（{t['closed']}）"
    if t["manual"]:
        return False, "本人已接管"
    if t["pausedBy"]:
        return False, f"等待本人决定（{'、'.join(t['pausedBy'])}）"
    if t["unresolved"]:
        return False, "有未确认的外发"
    if not t["lastOutbound"]:
        return False, "我方尚未发出过消息"
    if t["followupsSent"] >= FOLLOWUP_MAX_TOTAL:
        return False, f"主动跟进已达 {FOLLOWUP_MAX_TOTAL} 次"
    last = parse_at(t["lastFollowupAt"]) if t["lastFollowupAt"] else None
    if last and last.astimezone(tz).date() == now.date():
        return False, "今天已主动跟进过"
    if last and t["followupsSinceReply"] >= FOLLOWUP_MAX_UNANSWERED \
            and now - last < timedelta(days=FOLLOWUP_COOLDOWN_DAYS):
        return False, f"连续 {FOLLOWUP_MAX_UNANSWERED} 次跟进未回，{FOLLOWUP_COOLDOWN_DAYS} 天内不再跟进"
    return True, "可按页面状态（已读不回或送达未读满 1 小时）决定是否跟进"


def find_threads(threads, company, recruiter=None):
    out = []
    for t in threads.values():
        if match_company(company, [t["company"]])[0] != "exact":
            continue
        if recruiter and norm_text(t["recruiter"]) != norm_text(recruiter):
            continue
        out.append(t)
    return out


# ---------- 命令：validate ----------

def cmd_validate(args, ws):
    events, errors = read_events(ws, strict=False)
    stop_error = None
    try:
        stop = load_stoplist(ws)
    except LedgerError as e:
        stop, stop_error = [], str(e)
    result = {
        "ok": not errors and not stop_error,
        "events": len(events),
        "errors": errors,
        "stoplist": {"entries": len(stop), "error": stop_error},
    }
    return emit(result, 0 if result["ok"] else 2)


# ---------- 命令：check ----------

def stoplist_reasons(ws, company):
    for e in load_stoplist(ws):
        kind, hit = match_company(company, [e["name"], *e["aliases"]])
        if kind:
            return [{
                "type": "stoplist" if kind == "exact" else "stoplist_suspect",
                "detail": f"停止名单：{e['name']}（命中 {hit}；原因：{e.get('reason') or '未注明'}）",
            }]
    return []


def company_observation_reasons(events, platform, company):
    out = []
    for ev in events:
        if ev["kind"] == "observe" and ev["platform"] == platform \
                and ev["observed"] in COMPANY_BLOCKING_OBSERVATIONS \
                and match_company(company, [ev["company"]])[0] == "exact":
            out.append({"type": ev["observed"],
                        "detail": f"该公司已记 {ev['observed']}（{ev['at']}）：{ev['reason']}"})
    return out


def first_contact_reasons(args, events, scope):
    """首次联系去重：所有动作建立的联系都算，不只 greet。"""
    reasons = []
    job_id = args.job_id or job_id_from_url(args.job_url)
    contacts = [a for a in attempts_of(events).values() if attempt_blocks(a, args.run)]
    contacts += [{**ev, "lastAt": ev["at"], "jobId": ev.get("jobId"), "status": "adopted"}
                 for ev in events if ev["kind"] == "observe" and ev["observed"] == "adopted"]
    for a in contacts:
        if a["platform"] != args.platform:
            continue
        same_job = bool(job_id and a["jobId"] == job_id)
        same_company, _ = match_company(args.company, [a["company"]])
        if not same_company and not same_job:
            continue
        same_title = bool(args.title) and norm_text(a["title"]) == norm_text(args.title)
        tag = f"{a['company']} / {a['title']}（{a['kind']} {a['status']}，{a['lastAt']}，attempt {a['attemptId']}）"
        if same_job:
            reasons.append({"type": "same_job", "detail": f"同一职位已有记录：{tag}"})
        elif same_company == "exact" and same_title and job_id and a["jobId"] and a["jobId"] != job_id:
            reasons.append({"type": "reposted_suspect",
                            "detail": f"同公司同职位名但 ID 不同，疑似重新发布或 ID 不稳定：{tag}"})
        elif same_company == "exact" and scope == "company":
            reasons.append({"type": "same_company", "detail": f"同公司已联系：{tag}"})
        elif same_company == "exact" and same_title and not (job_id and a["jobId"]):
            reasons.append({"type": "same_title", "detail": f"同公司同职位名，缺少 ID 无法区分：{tag}"})
        elif same_company == "suspect" and scope == "company":
            reasons.append({"type": "company_suspect", "detail": f"公司名疑似相同：{tag}", "_at": a["lastAt"]})
        if reasons and "_at" not in reasons[-1]:
            reasons[-1]["_at"] = a["lastAt"]
    # 同一类原因只保留最近的一条
    latest = {}
    for r in reasons:
        if r["type"] not in latest or r["_at"] >= latest[r["type"]]["_at"]:
            latest[r["type"]] = r
    return [{k: v for k, v in r.items() if k != "_at"} for r in latest.values()], job_id


def conversation_reasons(args, events, now, tz):
    """会话内外发（回复、跟进、发简历、补发招呼语）的统一检查。"""
    reasons = []
    threads = find_threads(build_threads(events, args.platform), args.company, args.recruiter)
    for t in threads:
        who = f"{t['company']} / {t['recruiter'] or '招聘者未知'}"
        if t["closed"]:
            reasons.append({"type": "closed", "detail": f"会话已结束（{t['closed']}）：{who}"})
        if t["unresolved"]:
            ids = "、".join(u["attemptId"] for u in t["unresolved"])
            reasons.append({"type": "unresolved", "detail": f"有未确认的外发（{ids}），先核对：{who}"})
        if t["manual"]:
            reasons.append({"type": "manual", "detail": f"本人已接管，须本人明确交还（observe/released）后才能发送：{who}"})
        if args.decision and unstepped_decision_attempts(events, args.decision):
            reasons.append({"type": "decision_needs_review",
                            "detail": f"决定 {args.decision} 有不带步骤编号的外发记录，须本人核对后再执行：{who}"})
        other_pauses = [d for d in t["pausedBy"] if d != args.decision]
        if other_pauses:
            reasons.append({"type": "paused", "detail": f"等待本人决定（{'、'.join(other_pauses)}）：{who}"})
        if args.kind == "followup":
            ok, why = followup_eligibility(t, now, tz)
            if not ok:
                reasons.append({"type": "followup_not_eligible", "detail": f"{why}：{who}"})
    if args.kind == "followup" and not threads:
        reasons.append({"type": "no_thread", "detail": "本地没有该会话的记录；只有本人明确点名交给 Agent 才能登记（observe/adopted）"})
    return reasons, threads


def cmd_check(args, ws):
    profile = load_profile(ws)
    tz = tz_of(profile)
    limits = limits_of(profile)
    scope = args.scope or limits["dedupScope"]
    events, _ = read_events(ws)
    reasons = stoplist_reasons(ws, args.company) + company_observation_reasons(events, args.platform, args.company)
    out = {"kind": args.kind, "company": args.company}
    if args.kind == "greet":
        more, job_id = first_contact_reasons(args, events, scope)
        out.update({"jobId": job_id, "dedupScope": scope})
    else:
        more, threads = conversation_reasons(args, events, fsutil.now(tz), tz)
        out["threads"] = len(threads)
    reasons += more
    out.update({"allowed": not reasons, "reasons": reasons})
    return emit(out, 0 if not reasons else 1)


# ---------- 命令：decision ----------

def decision_completed(events, decision):
    """存在完成标记，且没有会话仍被这个决定暂停，才算全部完成。"""
    marked = any(ev["kind"] == "observe" and ev.get("decision") == decision
                 and ev["observed"] == DECISION_DONE_OBSERVATION for ev in events)
    return marked and not any(decision in t["pausedBy"] for t in build_threads(events).values())


def unstepped_decision_attempts(events, decision):
    """同一决定中漏写 step 的外发尝试。决定未完成时，它们与新步骤的对应关系须本人核对。"""
    if decision_completed(events, decision):
        return []
    return [a for a in attempts_of(events).values() if a["decision"] == decision and not a["step"]]


def step_state(statuses):
    if "sent" in statuses:
        return "done"
    if any(x in ("pending", "uncertain") for x in statuses):
        return "in_progress"
    return "failed" if statuses else "not_started"


def cmd_decision(args, ws):
    """外发按 step 跟踪；有带 decision 的 unpaused 且没有会话仍被它暂停，才算全部完成。"""
    events, _ = read_events(ws)
    linked = [a for a in attempts_of(events).values() if a["decision"] == args.id]
    obs = [ev for ev in events if ev["kind"] == "observe" and ev.get("decision") == args.id]
    steps = {}
    for a in linked:
        steps.setdefault(a["step"] or "default", []).append(a["status"])
    step_states = {k: step_state(v) for k, v in steps.items()}
    completed = decision_completed(events, args.id)
    local_done = [ev["observed"] for ev in obs if ev["observed"] not in ("paused", DECISION_DONE_OBSERVATION)]
    unstepped = unstepped_decision_attempts(events, args.id)
    if completed:
        state, advice = "done", "决定已全部执行；只需确认 inbox 已标「已执行」，不要再发"
    elif any(a["status"] in ("pending", "uncertain") for a in unstepped):
        state, advice = "in_progress", "有未确认的外发（无步骤编号）；只核对页面，不新建尝试，结果不明请本人核对"
    elif unstepped:
        state, advice = ("needs_review", "有不带步骤编号的外发记录，无法确定它对应决定中的哪个动作；"
                         "不要新建外发，请本人核对：若决定已全部完成，记带 decision 的 unpaused")
    elif "in_progress" in step_states.values():
        state, advice = "in_progress", "有未确认的外发；只核对页面，不新建尝试，结果不明请本人核对"
    elif "failed" in step_states.values():
        state, advice = "failed", "有动作明确未发出；本轮不重试，请在汇报中说明"
    elif step_states or local_done or any(ev["observed"] == DECISION_DONE_OBSERVATION for ev in obs):
        state, advice = "partial", "部分动作已完成（见 steps、localActions）；跳过已完成的，继续其余动作，全部完成后记 unpaused"
    else:
        state, advice = "not_started", "尚未执行：先列出决定要求的动作，本地动作在前、外发在后，逐个执行，全部完成后记 unpaused"
    out = {
        "decision": args.id, "state": state, "advice": advice,
        "steps": step_states,
        "localActions": local_done,
        "attempts": [{k: a[k] for k in ("attemptId", "kind", "step", "status", "company", "recruiter", "lastAt")}
                     for a in linked],
        "pausedRecords": [{k: ev.get(k) for k in ("at", "platform", "company", "recruiter", "title", "reason")}
                          for ev in obs if ev["observed"] == "paused"],
        "stillPausing": [{"company": t["company"], "recruiter": t["recruiter"]}
                         for t in build_threads(events).values() if args.id in t["pausedBy"]],
    }
    if args.step:
        out["stepState"] = "needs_review" if unstepped else step_states.get(args.step, "not_started")
    return emit(out)


# ---------- 命令：guard ----------

def cmd_guard(args, ws):
    """可能外发的每次 UI 操作（点击沟通、发送、同意）之前调用：确认仍持锁、尝试仍待定，并续期。"""
    tz = tz_of(load_profile(ws))
    with lock_mutex(ws):
        require_live_lock(ws, args.run, tz, renew=True)
        events, _ = read_events(ws)
        a = attempts_of(events).get(args.attempt)
        if not a:
            raise LedgerError(f"找不到 attempt {args.attempt}")
        if a["runId"] != args.run:
            return emit({"allowed": False, "reason": f"attempt 属于 {a['runId']}，不是本轮"}, 1)
        if a["status"] != "pending":
            return emit({"allowed": False,
                         "reason": f"attempt 当前为 {a['status']}，不能再操作；重新核对页面，不新建尝试"}, 1)
    return emit({"allowed": True, "attempt": args.attempt})


# ---------- 命令：append ----------

def adoption_result(events, ev):
    key = thread_key(ev)
    existing = next((e for e in events if e["kind"] == "observe" and e["observed"] == "adopted"
                     and thread_key(e) == key), None)
    if existing:
        return {"ok": True, "duplicate": True, "event": public_entry(existing)}, 0
    t = build_threads(events, ev["platform"]).get(key)
    if t and (t["unresolved"] or t["closed"] or t["pausedBy"]):
        raise LedgerError("会话有未确认外发、已关闭或仍暂停，不能登记")
    return None


def cmd_append(args, ws):
    raw = args.json if args.json is not None else sys.stdin.read()
    try:
        ev = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LedgerError(f"输入不是有效 JSON：{e}")
    if not isinstance(ev, dict):
        raise LedgerError("输入须为 JSON 对象")
    profile = load_profile(ws)
    tz = tz_of(profile)
    ev.setdefault("schemaVersion", SCHEMA_VERSION)
    ev.setdefault("at", now_iso(tz))
    if not ev.get("account") and ev.get("platform"):
        ev["account"] = default_account(ws, ev["platform"])
    for k in ("title", "location", "recruiter", "evidence"):
        ev.setdefault(k, None)
    if ev.get("jobUrl"):
        ev["jobUrl"] = normalize_job_url(ev["jobUrl"])
        ev.setdefault("jobId", job_id_from_url(ev["jobUrl"]))
    if not ev.get("attemptId"):
        if ev.get("kind") in ACTION_KINDS and ev.get("status") not in STANDALONE_STATUSES:
            raise LedgerError("终态事件须带上对应 pending 的 attemptId")
        ev["attemptId"] = "att-" + uuid.uuid4().hex[:12]
    errs = validate_event(ev)
    if errs:
        raise LedgerError("事件不合格：" + "；".join(errs))
    if ev["kind"] in ACTION_KINDS and ev.get("decision") and not ev.get("step") and ev["status"] in STANDALONE_STATUSES:
        raise LedgerError("执行已决定事项的外发须带 step（如 reply1、resume1）")

    adopting = ev["kind"] == "observe" and ev["observed"] == "adopted"

    def write():
        events, _ = read_events(ws)
        if adopting:
            result = adoption_result(events, ev)
            if result is not None:
                return result
        if ev["kind"] in ACTION_KINDS:
            check_transition([e for e in events if e["attemptId"] == ev["attemptId"]], ev)
            if ev["status"] == "pending" and ev.get("decision") and unstepped_decision_attempts(events, ev["decision"]):
                raise LedgerError(f"决定 {ev['decision']} 有不带步骤编号的外发记录，须本人核对后再执行")
        elif any(e["attemptId"] == ev["attemptId"] for e in events):
            raise LedgerError(f"attemptId {ev['attemptId']} 已被使用")
        append_line(ws, ev)
        return {"ok": True, "event": ev}, 0

    # 本人交出会话与 pending 共用有效锁检查。
    with lock_mutex(ws):
        if adopting or (ev["kind"] in ACTION_KINDS and ev["status"] == "pending"):
            require_live_lock(ws, ev["runId"], tz, renew=True)
        out, code = write()
    return emit(out, code)


# ---------- 命令：quota ----------

def cmd_quota(args, ws):
    profile = load_profile(ws)
    limits = limits_of(profile)
    tz = tz_of(profile)
    events, _ = read_events(ws)
    today = fsutil.now(tz).date()
    # 额度按「是否写过 greet 的 pending」计：失败或结果不明也不退回
    greets = [a for a in attempts_of(events).values()
              if a["kind"] == "greet" and a["platform"] == args.platform and a["everPending"]]
    today_greets = [a for a in greets if parse_at(a["firstAt"]).astimezone(tz).date() == today]
    run_greets = [a for a in greets if args.run and a["runId"] == args.run]
    # 间隔按最近一次外发活动（写 pending 或确认 sent）算，取较晚者，保守
    activity = [parse_at(e["at"]) for e in events
                if e["kind"] in ACTION_KINDS and e["platform"] == args.platform
                and e["status"] in ("pending", "sent")]
    last = max(activity, default=None)
    since = (fsutil.now(tz) - last).total_seconds() if last else None
    remaining_day = max(0, limits["greetsPerDay"] - len(today_greets))
    remaining_run = max(0, limits["greetsPerRun"] - len(run_greets)) if args.run else None
    can_greet = remaining_day > 0 and (remaining_run is None or remaining_run > 0)
    wait = 0
    if since is not None and since < limits["minSendIntervalSeconds"]:
        wait = round(limits["minSendIntervalSeconds"] - since, 1)
    return emit({
        "canGreet": can_greet,
        "date": today.isoformat(),
        "greetsToday": len(today_greets),
        "remainingToday": remaining_day,
        "greetsThisRun": len(run_greets) if args.run else None,
        "remainingThisRun": remaining_run,
        "detailsPerRun": limits["detailsPerRun"],
        "secondsSinceLastSend": round(since, 1) if since is not None else None,
        "waitSeconds": wait,
        "limits": limits,
    }, 0 if can_greet else 1)


# ---------- 命令：threads ----------

def cmd_threads(args, ws):
    profile = load_profile(ws)
    tz = tz_of(profile)
    now = fsutil.now(tz)
    events, _ = read_events(ws)
    threads = build_threads(events, args.platform)
    for t in threads.values():
        t["canFollowup"], t["followupReason"] = followup_eligibility(t, now, tz)
    items = sorted(threads.values(), key=lambda t: t["lastAt"] or "", reverse=True)
    if args.company:
        items = [t for t in items if match_company(args.company, [t["company"]])[0]]
    if args.recruiter:
        items = [t for t in items if norm_text(t["recruiter"]) == norm_text(args.recruiter)]
    return emit({"threads": items})


# ---------- 命令：stats ----------

def cmd_stats(args, ws):
    profile = load_profile(ws)
    tz = tz_of(profile)
    events, _ = read_events(ws)

    def in_range(at):
        d = parse_at(at).astimezone(tz).date()
        if args.since and d < datetime.fromisoformat(args.since).date():
            return False
        if args.until and d > datetime.fromisoformat(args.until).date():
            return False
        return True

    actions, observed = {}, {}
    for a in attempts_of(events).values():
        if not in_range(a["firstAt"]) or (args.platform and a["platform"] != args.platform):
            continue
        key = f"{a['platform']}/{a['kind']}"
        actions.setdefault(key, {}).setdefault(a["status"], 0)
        actions[key][a["status"]] += 1
    for ev in events:
        if ev["kind"] != "observe" or not in_range(ev["at"]):
            continue
        if args.platform and ev["platform"] != args.platform:
            continue
        key = f"{ev['platform']}/{ev['observed']}"
        observed[key] = observed.get(key, 0) + 1
    return emit({"since": args.since, "until": args.until, "actions": actions, "observed": observed})


# ---------- 运行锁 ----------
# lock.json 记录持锁者；所有读取、接管、续期、释放都在 .lock.mutex 的独占锁内完成。

def lock_path(ws):
    return ws / "ledger" / "lock.json"


def lock_mutex(ws):
    return file_mutex(ws / "ledger" / ".lock.mutex")


def read_lock(ws):
    return read_json(lock_path(ws))


def write_lock(ws, lock):
    write_atomic(lock_path(ws), json.dumps(lock, ensure_ascii=False, indent=2) + "\n")


def lock_expired(lock, now):
    return parse_at(lock["expiresAt"]) <= now


def require_live_lock(ws, run_id, tz, renew=False):
    """调用方须已持有 lock_mutex。run_id 必须是当前未过期的持锁者，且本轮未禁发。"""
    now = fsutil.now(tz)
    lock = read_lock(ws)
    if not lock or lock.get("runId") != run_id:
        holder = lock.get("runId") if lock else "无"
        raise LedgerError(f"runId {run_id} 未持有运行锁（当前持锁：{holder}），禁止新的外发")
    if lock.get("releasing"):
        raise LedgerError(f"runId {run_id} 正在释放运行锁，禁止新的外发；请完成 lock release 收尾")
    if lock_expired(lock, now):
        raise LedgerError(f"runId {run_id} 的运行锁已过期，禁止新的外发；请 lock renew 或重新获取")
    if lock.get("noSend"):
        raise LedgerError(f"runId {run_id} 本轮为禁发模式（--no-send），不能写入新的外发")
    pause = read_pause(ws)
    if pause:
        raise LedgerError(f"已全局暂停（{pause.get('at')}，{pause.get('reason')}），禁止新的外发；"
                          "本人回「恢复」或 ledger pause off 之后才能继续")
    if renew:
        lock["expiresAt"] = (now + timedelta(minutes=lock.get("ttlMinutes", DEFAULT_LOCK_TTL_MINUTES))) \
            .isoformat(timespec="seconds")
        write_lock(ws, lock)
    return lock


def acquire_lock(ws, tz, skill, run=None, ttl=DEFAULT_LOCK_TTL_MINUTES, no_send=False):
    """调用方须已持有 lock_mutex。返回 (退出码, 结果)。同一 runId 重复获取视为沿用。"""
    # 只检查 mode，不依赖 workspace.py 的步骤格式，避免两个脚本循环导入。
    progress = ws / "setup-progress.json"
    if skill in {"apply", "followup"} and progress.exists():
        try:
            mode = json.loads(progress.read_text(encoding="utf-8"))["mode"]
        except (OSError, ValueError, KeyError, TypeError):
            mode = "new"
        if mode not in ("resetup", "partial"):
            return 1, {"acquired": False, "reason": "初始化未完成或 setup 草稿读不出，请先完成 setup"}
    now = fsutil.now(tz)
    current = read_lock(ws)
    took_over, reconciled = None, []
    if current:
        if run and current.get("runId") == run and not current.get("releasing") and not lock_expired(current, now):
            return 0, {"acquired": True, "reused": True, "lock": current, "tookOverFrom": None, "reconciled": []}
        if not lock_expired(current, now):
            return 1, {"acquired": False, "reason": "已有运行中的任务", "lock": current}
        took_over = current
        reconciled = reconcile_run(ws, current, tz)
    run_id = run or f"run-{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    lock = {
        "skill": skill,
        "runId": run_id,
        "host": socket.gethostname(),
        "acquiredAt": now.isoformat(timespec="seconds"),
        "expiresAt": (now + timedelta(minutes=ttl)).isoformat(timespec="seconds"),
        "ttlMinutes": ttl,
        "noSend": bool(no_send),
    }
    write_lock(ws, lock)
    return 0, {"acquired": True, "reused": False, "lock": lock, "tookOverFrom": took_over, "reconciled": reconciled}


def release_lock(ws, run, tz=None):
    """调用方须已持有 lock_mutex。先持久化收尾意图，核销 pending 后才删除锁。"""
    current = read_lock(ws)
    if not current:
        return 0, {"released": False, "reason": "没有锁", "reconciled": []}
    if current.get("runId") != run:
        return 1, {"released": False, "reason": "锁不属于该 runId", "lock": current}
    tz = tz or tz_of(load_profile(ws))
    events, _ = read_events(ws)
    # 收尾中断时保留候选 ID：重试 / 过期接管仍能报告已追加的 uncertain。
    ids = list(current.get("releaseAttempts") or [])
    for a in attempts_of(events).values():
        if a["runId"] == run and a["status"] == "pending" and a["attemptId"] not in ids:
            ids.append(a["attemptId"])
    current.update(releasing=True, releaseAttempts=ids)
    try:
        write_lock(ws, current)
        reconciled = reconcile_run(ws, current, tz)
        lock_path(ws).unlink()
    except OSError as e:
        raise LedgerError(f"释放运行锁收尾失败，停止外发并重试 lock release；锁未确认释放：{e}") from e
    return 0, {"released": True, "reconciled": reconciled}


def cmd_lock(args, ws):
    profile = load_profile(ws)
    tz = tz_of(profile)
    with lock_mutex(ws):
        now = fsutil.now(tz)
        current = read_lock(ws)

        if args.action == "status":
            if not current:
                return emit({"held": False})
            expired = lock_expired(current, now)
            return emit({"held": not expired, "expired": expired, "lock": current})

        if args.action == "release":
            code, out = release_lock(ws, args.run, tz)
            return emit(out, code)

        if args.action == "renew":
            if not current or current.get("runId") != args.run:
                return emit({"renewed": False, "reason": "已失去运行锁，立即停止外发", "lock": current}, 1)
            if current.get("releasing"):
                return emit({"renewed": False, "reason": "运行锁正在释放，请重试 lock release 完成收尾", "lock": current}, 1)
            current["expiresAt"] = (now + timedelta(minutes=current.get("ttlMinutes", args.ttl))) \
                .isoformat(timespec="seconds")
            write_lock(ws, current)
            return emit({"renewed": True, "lock": current})

        code, out = acquire_lock(ws, tz, args.skill, args.run, args.ttl, args.no_send)
        out["paused"] = read_pause(ws)
        return emit(out, code)


def lock_active(ws, tz):
    """当前是否有未过期的运行锁（只读判断，不加互斥）。"""
    lock = read_lock(ws)
    return bool(lock) and not lock_expired(lock, fsutil.now(tz))


# ---------- 全局暂停 ----------
# pause.json 存在且 paused 为 true 时，任何一轮都不能写入新的 pending，guard 失败。
# 本人通过 IM 回「暂停」「恢复」，或技能遇风控时设置。

def pause_path(ws):
    return ws / "ledger" / "pause.json"


def read_pause(ws):
    """暂停中返回暂停记录，否则返回 None。"""
    p = read_json(pause_path(ws))
    return p if p and p.get("paused") else None


def set_pause(ws, paused, by, reason, tz, im_command=None):
    """写入全局暂停状态。im_command 为已执行的 IM 指令消息 ID，与暂停状态在同一次原子写内落盘；
    之后技能设置安全暂停时保留这些标识，避免旧「恢复」在重放时解除新的安全暂停。"""
    with lock_mutex(ws):
        previous = read_json(pause_path(ws))
        ids = list((previous or {}).get("imCommands") or [])
        if im_command and im_command not in ids:
            ids.append(im_command)
        rec = {"paused": bool(paused), "at": now_iso(tz), "by": by, "reason": reason}
        if ids:
            rec["imCommands"] = ids[-50:]
        write_atomic(pause_path(ws), json.dumps(rec, ensure_ascii=False, indent=2) + "\n")
    return rec, (previous if previous and previous.get("paused") else None)


def cmd_pause(args, ws):
    tz = tz_of(load_profile(ws))
    if args.action == "status":
        return emit({"paused": read_pause(ws)})
    if args.action == "on":
        if not args.reason:
            raise LedgerError("pause on 需要 --reason")
        rec, previous = set_pause(ws, True, args.by, args.reason, tz)
    else:
        rec, previous = set_pause(ws, False, args.by, args.reason or "恢复", tz)
    return emit({"pause": rec, "previous": previous})


def reconcile_run(ws, old_lock, tz):
    """释放 / 过期接管：该轮没有终态的 pending 追加 uncertain。调用方须已持有 lock_mutex。"""
    events, _ = read_events(ws)
    out = []
    release_ids = set(old_lock.get("releaseAttempts") or [])
    cause = "正在释放" if old_lock.get("releasing") else "已过期"
    for a in attempts_of(events).values():
        if a["runId"] != old_lock.get("runId"):
            continue
        if a["status"] == "pending":
            last = a["events"][-1]
            ev = {k: v for k, v in last.items() if not k.startswith("_")}
            ev.update({
                "at": now_iso(tz),
                "status": "uncertain",
                "reason": f"本轮（{old_lock.get('runId')}）锁{cause}，结果未确认，需人工核对",
                "evidence": None,
            })
            append_line(ws, ev)
        elif a["status"] != "uncertain" or a["attemptId"] not in release_ids:
            continue
        out.append({"attemptId": a["attemptId"], "company": a["company"], "title": a["title"], "kind": a["kind"]})
    return out


# ---------- 命令：stoplist ----------

def cmd_stoplist(args, ws):
    if args.action == "list":
        return emit({"entries": [public_entry(e) for e in load_stoplist(ws)]})
    path = stoplist_path(ws)
    with stoplist_mutex(ws):
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        entries = parse_stoplist(text)
        if args.action == "remove":
            hit = next((e for e in entries if norm_company(e["name"]) == norm_company(args.name)), None)
            if not hit:
                return emit({"removed": False, "reason": f"停止名单中没有名为「{args.name}」的条目（按 name 精确匹配）"}, 1)
            lines = text.splitlines(keepends=True)
            new_text = "".join(lines[:hit["_start"]] + lines[hit["_end"]:])
            parse_stoplist(new_text)
            write_atomic(path, new_text)
            return emit({"removed": True, "entry": public_entry(hit)})
        existing = find_stop_entry(entries, args.name)
        if existing:
            return emit({"added": False, "reason": f"已在停止名单：{existing['name']}", "entry": public_entry(existing)})
        profile = load_profile(ws)
        entry = {
            "name": args.name,
            "aliases": args.alias or [],
            "reason": args.reason,
            "source": args.source,
            "added": fsutil.now(tz_of(profile)).date().isoformat(),
        }
        new_text = stoplist_text_with(text, [entry])
        back = parse_stoplist(new_text)[-1]
        if public_entry(back) != entry:
            raise LedgerError(f"停止名单写入后读回不一致，未写入：{public_entry(back)}")
        write_atomic(path, new_text)
        return emit({"added": True, "entry": entry})


# ---------- 入口 ----------

def build_parser():
    p = argparse.ArgumentParser(prog="ledger.py", description=__doc__.splitlines()[0])
    p.add_argument("--workspace", help="工作区路径；默认从当前目录向上查找 job-mate.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("validate", help="校验事件记录与停止名单")

    c = sub.add_parser("check", help="外发前检查")
    c.add_argument("--platform", required=True)
    c.add_argument("--company", required=True)
    c.add_argument("--kind", choices=sorted(ACTION_KINDS), default="greet",
                   help="greet：首次联系去重；其余：会话内外发检查")
    c.add_argument("--recruiter", help="会话内外发时的招聘者「姓名(职务)」")
    c.add_argument("--decision", help="执行本人已决定事项时的决策 ID")
    c.add_argument("--title")
    c.add_argument("--job-id")
    c.add_argument("--job-url")
    c.add_argument("--run", help="本轮 runId；同轮 failed_no_send 不重试")
    c.add_argument("--scope", choices=["company", "job"], help="覆盖 profile 的 dedupScope")

    d = sub.add_parser("decision", help="查询某个已决定事项的执行状态")
    d.add_argument("--id", required=True)
    d.add_argument("--step", help="只看某个外发步骤的状态")

    g = sub.add_parser("guard", help="可能外发的 UI 操作前：确认仍持锁、尝试仍待定，并续期")
    g.add_argument("--run", required=True)
    g.add_argument("--attempt", required=True)

    a = sub.add_parser("append", help="原子追加一条事件（JSON 从 --json 或 stdin 读入）")
    a.add_argument("--json")

    q = sub.add_parser("quota", help="剩余招呼额度")
    q.add_argument("--platform", required=True)
    q.add_argument("--run")

    t = sub.add_parser("threads", help="按会话汇总最新状态")
    t.add_argument("--platform")
    t.add_argument("--company")
    t.add_argument("--recruiter")

    s = sub.add_parser("stats", help="统计")
    s.add_argument("--platform")
    s.add_argument("--since", help="YYYY-MM-DD")
    s.add_argument("--until", help="YYYY-MM-DD")

    lk = sub.add_parser("lock", help="运行锁")
    lk.add_argument("action", choices=["acquire", "renew", "release", "status"])
    lk.add_argument("--skill", choices=sorted(SKILLS - {"user"}))
    lk.add_argument("--run")
    lk.add_argument("--ttl", type=int, default=DEFAULT_LOCK_TTL_MINUTES, help="分钟")
    lk.add_argument("--no-send", action="store_true", help="本轮禁发：持锁期间拒绝写入新的外发")

    pz = sub.add_parser("pause", help="全局暂停：暂停期间任何一轮都不能写入新的外发")
    pz.add_argument("action", choices=["on", "off", "status"])
    pz.add_argument("--reason", help="暂停原因（on 时必填）")
    pz.add_argument("--by", default="user", help="谁设置的：user、im、apply、followup")

    sl = sub.add_parser("stoplist", help="停止名单")
    sl.add_argument("action", choices=["list", "add", "remove"])
    sl.add_argument("--name")
    sl.add_argument("--alias", action="append")
    sl.add_argument("--reason")
    sl.add_argument("--source")

    return p


COMMANDS = {
    "validate": cmd_validate, "check": cmd_check, "decision": cmd_decision, "guard": cmd_guard,
    "append": cmd_append,
    "quota": cmd_quota, "threads": cmd_threads, "stats": cmd_stats, "lock": cmd_lock,
    "pause": cmd_pause, "stoplist": cmd_stoplist,
}


def main(argv=None):
    fsutil.utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "lock":
            if args.action == "acquire" and not args.skill:
                raise LedgerError("lock acquire 需要 --skill")
            if args.action in ("release", "renew") and not args.run:
                raise LedgerError(f"lock {args.action} 需要 --run")
        if args.cmd == "stoplist":
            if args.action == "add" and not (args.name and args.reason and args.source):
                raise LedgerError("stoplist add 需要 --name、--reason、--source")
            if args.action == "remove" and not args.name:
                raise LedgerError("stoplist remove 需要 --name")
        if args.cmd == "decision" and not DECISION_RE.match(args.id):
            raise LedgerError("决策 ID 应形如 D-20261028-01")
        ws = find_workspace(args.workspace)
        return COMMANDS[args.cmd](args, ws)
    except LedgerError as e:
        return emit({"error": str(e)}, 2)


if __name__ == "__main__":
    sys.exit(main())

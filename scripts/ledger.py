#!/usr/bin/env python3
"""job-mate 事件记录工具：校验、外发检查、追加、配额、会话汇总、决策执行状态、统计、运行锁、全局暂停、停止名单。

仅用 Python 标准库。所有命令向 stdout 输出一个 JSON 对象。
退出码：0 成功 / 允许；1 不允许（检查不通过、额度用尽、锁被占用）；2 错误（参数、文件损坏、工作区缺失、未持锁）。
"""

import argparse
import contextlib
import csv
import io
import hashlib
from contextvars import ContextVar
import json
import os
import re
import socket
import sys
import threading
import unicodedata
import uuid
from datetime import datetime, timedelta, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fsutil  # noqa: E402
import outbound  # noqa: E402
from platforms import load_platform  # noqa: E402

SUITE_VERSION = "1.0.0"
SCHEMA_VERSION = 1
MARKER = "job-mate.json"

ACTION_KINDS = {"greet", "intro", "reply", "nudge", "resume"}
INFO_GROUPS = {"salary": ("salary.range", "salary.months"),
               "hours": ("hours.schedule", "hours.time"), "location": ("location.address",),
               "leave": ("leave.annual",), "employment": ("employment.type",)}
INFO_KEYS = frozenset(key for values in INFO_GROUPS.values() for key in values)
DEFAULT_INQUIRY_PRIORITY = ("salary", "hours", "location", "leave")
THREAD_STAGES = ("greeted", "replied", "info", "resume", "interview_invited")
CONVERSATION_KINDS = ACTION_KINDS - {"greet"}
ACTION_STATUSES = {"pending", "sent", "uncertain", "failed_no_send", "skipped", "already_contacted"}
STANDALONE_STATUSES = {"pending", "skipped", "already_contacted"}
OBSERVATIONS = {
    "replied", "resume_delivered", "resume_viewed", "interview_invited",
    "rejected", "job_closed", "declined", "reopened",
    "manual", "released", "adopted", "paused", "unpaused", "risk", "limit_hit",
    "tail", "handled", "info", "decision", "resume_requested", "interview_decided", "silent",
}
SKILLS = {"setup", "apply", "followup", "user"}
REQUIRED = [
    "schemaVersion", "at", "platform", "account", "runId", "attemptId", "skill", "kind",
    "company", "title", "location", "recruiter", "reason", "evidence",
    "eventId", "threadKey", "jobKey", "targetRole",
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
    "inboundScanDays": 14, "inboundScanPerRun": 30, "inboundRecheckHours": 24,
}
DEFAULT_NUDGE = {"afterReadHours": 24, "afterUnreadHours": 24, "intervalHours": 48,
                 "maxPerSilence": 3, "maxPerThread": 6, "perDay": 20}
DEFAULT_TZ = "Asia/Shanghai"
DEFAULT_LOCK_TTL_MINUTES = 60

DECISION_RE = re.compile(r"^D-\d{8}-\d+$")
STEP_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class LedgerError(Exception):
    pass


class VersionGateError(LedgerError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


_GATE_WARNINGS = ContextVar("suite_warnings", default=())
_MUTATION_OWNER = threading.local()


def mutation_held(ws):
    return (os.getpid(), Path(ws).resolve()) in getattr(_MUTATION_OWNER, "held", set())


@contextlib.contextmanager
def mutation_mutex(ws):
    """中央写入协调锁；只在同进程同线程同工作区内重入。

    顺序：mutation → poll → im / lock / stoplist → events。
    lock 与 stoplist 不互相嵌套；IM 指令在释放 im 后才取 lock。
    所有写入/恢复/创建入口先取中央锁，业务锁不可反向调用新入口。
    """
    ws = Path(ws).resolve()
    key = (os.getpid(), ws)
    held = getattr(_MUTATION_OWNER, "held", set())
    if key in held:
        yield
        return
    with file_mutex(ws / "ledger" / ".mutation.mutex"):
        _MUTATION_OWNER.held = held | {key}
        try:
            yield
        finally:
            _MUTATION_OWNER.held = held


@contextlib.contextmanager
def operation_mutex(ws, name, write=True):
    """业务锁的统一顺序；只读不取中央锁，也不做写入准入。"""
    with mutation_mutex(ws) if write else contextlib.nullcontext():
        with file_mutex(ws / "ledger" / name):
            yield


def error_result(error):
    out = {"error": str(error)}
    if isinstance(error, VersionGateError):
        out["code"] = error.code
    if getattr(error, "suite_warnings", None):
        out["warnings"] = error.suite_warnings
    return out


def suite_version(value):
    if not isinstance(value, str) or not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value):
        raise LedgerError("suiteVersion 须为主.次.修订版本号，请核对标记或更新 job-mate")
    return tuple(map(int, value.split(".")))


def version_gate(ws, mode):
    """仅判断，不取互斥锁；持锁调用者也可复查，避免嵌套同锁。"""
    if mode not in {"read", "write", "recover"}:
        raise ValueError(f"未知准入模式：{mode}")
    try:
        meta = read_json(ws / MARKER)
        stored = suite_version(meta.get("suiteVersion") if isinstance(meta, dict) else None)
        running = suite_version(SUITE_VERSION)
    except (LedgerError, ValueError, OSError):
        if mode == "read":
            return []  # 仍允许体检损坏的工作区。
        raise
    if mode == "read":
        return ["suite_older_than_workspace"] if stored > running else []
    if stored[0] != running[0]:
        raise VersionGateError("suite_major_mismatch", "套件与工作区主版本不同，请更新 job-mate")
    if mode == "write":
        lock = read_lock(ws)
        if lock and lock.get("mode") == "recovery" and not lock_expired(lock, fsutil.now().astimezone()):
            raise VersionGateError("recovery_lock_only", "恢复锁只允许恢复类写入，请先完成恢复并释放锁")
        if stored[:2] > running[:2]:
            raise VersionGateError("suite_too_old", "工作区版本较新，请更新 job-mate 后再写入")
    return []


@contextlib.contextmanager
def gated(ws, mode, allow_create=False):
    if allow_create and mode != "write":
        raise ValueError("创建豁免只用于写入入口")
    # 准入和全部副作用共用临界区，包括等待业务锁和跨脚本调用。
    with mutation_mutex(ws) if mode != "read" else contextlib.nullcontext():
        # 创建也先取锁再看标记；互斥文件不是工作区标记。
        warnings = [] if allow_create and not (ws / MARKER).exists() else version_gate(ws, mode)
        token = _GATE_WARNINGS.set(tuple(warnings))
        try:
            yield
        except Exception as error:
            if _GATE_WARNINGS.get():
                error.suite_warnings = list(_GATE_WARNINGS.get())
            raise
        finally:
            _GATE_WARNINGS.reset(token)


def upgrade_suite_version(ws):
    meta = read_json(ws / MARKER)
    if suite_version(meta["suiteVersion"]) < suite_version(SUITE_VERSION):
        meta["suiteVersion"] = SUITE_VERSION
        write_atomic(ws / MARKER, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")


# ---------- 输出 ----------

def emit(obj, code=0):
    if _GATE_WARNINGS.get():
        obj = dict(obj, warnings=list(obj.get("warnings", [])) + list(_GATE_WARNINGS.get()))
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


def platform_settings(profile, field, platform, defaults):
    if not isinstance(profile, dict):
        raise LedgerError("profile 须为对象")
    base = profile.get(field, {})
    if not isinstance(base, dict) or not isinstance(base.get("platforms", {}), dict):
        raise LedgerError(f"{field} 及其 platforms 须为对象")
    override = base.get("platforms", {}).get(platform, {})
    if not isinstance(override, dict):
        raise LedgerError(f"{field}.platforms.{platform} 须为对象")
    return {**defaults, **{k: v for k, v in base.items() if k != "platforms"}, **override}


def effective_limits(profile, platform_id):
    defaults = dict(DEFAULT_LIMITS, **load_platform(platform_id)["defaultLimits"])
    result = platform_settings(profile, "limits", platform_id, defaults)
    for key, value in result.items():
        if key not in DEFAULT_LIMITS or (key == "dedupScope" and value not in ("company", "job")) or (
                key != "dedupScope" and (type(value) is not int or value < 0)):
            raise LedgerError(f"limits.{key} 无效")
    return result


def effective_nudge(profile, platform_id):
    load_platform(platform_id)
    result = platform_settings(profile, "nudge", platform_id, DEFAULT_NUDGE)
    for key, value in result.items():
        if key not in DEFAULT_NUDGE or isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value < float("inf"):
            raise LedgerError(f"nudge.{key} 无效")
    if (type(result["maxPerSilence"]) is not int or not 1 <= result["maxPerSilence"] <= 3
            or type(result["maxPerThread"]) is not int or not result["maxPerSilence"] <= result["maxPerThread"] <= 9
            or type(result["perDay"]) is not int):
        raise LedgerError("nudge 次数范围无效")
    return result


def recovery_tz(ws):
    try:
        return tz_of(load_profile(ws))
    except (LedgerError, OSError, ValueError, TypeError, AttributeError):
        return fsutil.tz_from_name(DEFAULT_TZ)


def tz_of(profile):
    name = ((profile.get("schedule") or {}).get("timezone")) or DEFAULT_TZ
    return fsutil.tz_from_name(name)


def default_account(ws, platform):
    load_platform(platform)
    meta = read_json(ws / MARKER, {}) or {}
    accounts = meta.get("accounts", {})
    if not isinstance(accounts, dict):
        raise LedgerError("accounts 须为每平台单账号对象")
    account = accounts.get(platform)
    if not isinstance(account, dict) or not isinstance(account.get("accountKey"), str) or not account["accountKey"].strip():
        raise LedgerError(f"{platform} 未配置有效账号，请先 setup")
    return account["accountKey"]


def resolve_account(ws, platform, account=None, history=()):
    if account is not None and (not isinstance(account, str) or not account.strip()):
        raise LedgerError("account 须为非空字符串")
    if account is not None and any(e["platform"] == platform and e["account"] == account for e in history):
        return account
    current = default_account(ws, platform)
    if account is not None and account != current:
        raise LedgerError("account 不匹配当前平台配置；不得用未知别名绕过配额")
    return current


def account_filter(ws, args, events):
    if args.platform is not None:
        load_platform(args.platform)
    account = args.account
    if account is None:
        return None
    if not isinstance(account, str) or not account.strip():
        raise LedgerError("account 须为非空字符串")
    if any(e["account"] == account and (args.platform is None or e["platform"] == args.platform) for e in events):
        return account
    platforms = [args.platform] if args.platform else list((read_json(ws / MARKER).get("accounts") or {}))
    if not any(default_account(ws, p) == account for p in platforms):
        raise LedgerError("未知 account；既无历史也不在当前配置")
    return account


def events_path(ws):
    return ws / "ledger" / "events.jsonl"


def now_iso(tz):
    return fsutil.now(tz).isoformat(timespec="seconds")


def parse_at(value):
    if isinstance(value, str) and value.endswith("Z"):
        value = value[:-1] + "+00:00"
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


def normalize_job_url(platform, url):
    """按平台描述决定是否去掉随会话变化的查询参数与片段。"""
    config = load_platform(platform)["jobUrl"]
    if not url or not config["stripQuery"]:
        return url
    return re.split(r"[?#]", url, maxsplit=1)[0]


def job_id_from_url(platform, url):
    pattern = load_platform(platform)["jobUrl"]["idPattern"]
    if not url:
        return None
    match = re.search(pattern, url)
    return (match.group(1) or None) if match else None


# ---------- v1 公共字段与时间 ----------

def config_rev(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def content_digest(text):
    value = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def key_part(value, normalize=None):
    return (normalize(value) if normalize else value).replace("|", "｜")


def event_keys(ev):
    platform = ev.get("platform")
    config = load_platform(platform)
    company, title = ev.get("company"), ev.get("title")
    job = ev.get("jobId")
    job_key = None
    if job:
        job_key = "|".join((platform, key_part(job)))
    elif company and title and norm_company(company) and norm_text(title):
        job_key = "|".join((platform, key_part(company, norm_company), key_part(title, norm_text)))
    fields = config["conversationKey"]
    thread = None
    if ev.get("account") and all(ev.get(field) for field in fields):
        components = [key_part(ev[field], norm_company if field == "company" else norm_text) for field in fields]
        if all(components):
            thread = "|".join([platform, key_part(ev["account"]), *components])
    return thread, job_key


def thread_scope(ev):
    """外部键的转义不保证可逆；内部关联须另带原始平台、账号。"""
    thread = ev["threadKey"] if "threadKey" in ev else event_keys(ev)[0]
    return ev["platform"], ev["account"], thread


def prepare_event(ev, tz=None):
    """内部生成事件身份；调用方的批准原文不规范化或改写。"""
    ev = dict(ev)
    for field in ("company", "title", "location", "recruiter", "evidence", "targetRole"):
        ev.setdefault(field, None)
    for field in ("platform", "account", "company", "title", "location", "recruiter", "jobId", "targetRole"):
        if ev.get(field) is not None and not isinstance(ev[field], str):
            raise LedgerError(f"{field} 须为字符串或 null")
    thread, job = event_keys(ev)
    for field, computed in (("threadKey", thread), ("jobKey", job)):
        if field in ev and ev[field] != computed:
            raise LedgerError(f"{field} 与页面字段派生结果不符")
        ev[field] = computed
    ev["eventId"] = "evt-" + uuid.uuid4().hex[:12]
    if ev.get("kind") == "observe" and ev.get("observed") == "released" and isinstance(ev.get("acknowledged"), list):
        reading = ev.get("readAt", ev["at"])
        try:
            if parse_at(reading) > parse_at(ev["at"]):
                raise ValueError("晚于事件时间")
        except (TypeError, ValueError) as error:
            raise LedgerError("released.readAt 须有效且不晚于事件时间") from error
        normalized = []
        for item in ev["acknowledged"]:
            item = dict(item) if isinstance(item, dict) else item
            if isinstance(item, dict) and isinstance(item.get("shown"), str):
                absolute = resolve_time_label(item["shown"], reading, tz or parse_at(reading).tzinfo, ev["platform"])
                if absolute["date"] and absolute["precision"] == item.get("precision"):
                    item["shown"] = absolute["date"] + (" " + absolute["minute"] if absolute["minute"] else "")
            normalized.append(item)
        ev["acknowledged"] = normalized
    return ev


def resolve_time_label(label, read_at, tz, platform):
    """只换算平台描述中的明确日期；孤立时分不借用读取日。"""
    labels = load_platform(platform)["timeLabels"]
    read_at = parse_at(read_at) if isinstance(read_at, str) else read_at
    if read_at.tzinfo is None:
        raise ValueError("读取时间须带时区")
    today = read_at.astimezone(tz).date()
    empty = {"date": None, "minute": None, "precision": "none"}
    if not isinstance(label, str):
        return empty
    label = unicodedata.normalize("NFKC", label).strip()
    minute = None
    clock = re.search(r"(?:^|\s)(\d{2}):(\d{2})$", label)
    if clock and labels["timeFormat"] == "HH:MM":
        if int(clock[1]) > 23 or int(clock[2]) > 59:
            return empty
        minute = clock[1] + ":" + clock[2]
        label = label[:clock.start()].strip()
    resolved = None
    try:
        if label in labels["relativeDays"]:
            resolved = today - timedelta(days=labels["relativeDays"][label])
        elif label in labels["weekdays"]:
            delta = (today.isoweekday() - labels["weekdays"][label]) % 7 or 7
            resolved = today - timedelta(days=delta)
        else:
            formats = {"YYYY-MM-DD": r"(\d{4})-(\d{2})-(\d{2})",
                       "YYYY年M月D日": r"(\d{4})年(\d{1,2})月(\d{1,2})日",
                       "M月D日": r"(\d{1,2})月(\d{1,2})日"}
            for fmt in labels["dateFormats"]:
                pattern = formats.get(fmt)
                match = re.fullmatch(pattern, label) if pattern else None
                if not match:
                    continue
                parts = tuple(map(int, match.groups()))
                if len(parts) == 3:
                    resolved = date(*parts)
                else:
                    # 闰日取不晚于读取日的最近一次，不猜未来年份。
                    for year in range(today.year, max(0, today.year - 9), -1):
                        try:
                            candidate = date(year, *parts)
                        except ValueError:
                            continue
                        if candidate <= today:
                            resolved = candidate
                            break
                break
    except (ValueError, OverflowError):
        return empty
    return {"date": resolved.isoformat() if resolved else None, "minute": minute,
            "precision": ("minute" if minute else "day") if resolved else "none"}


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
            errs.append("acknowledged.shown 须为非空绝对时间标签")
        if item["precision"] not in ("day", "minute"):
            errs.append("acknowledged.precision 须为 day 或 minute")
        try:
            parsed = datetime.strptime(item["shown"], "%Y-%m-%d %H:%M") if item["precision"] == "minute" else date.fromisoformat(item["shown"])
            canonical = parsed.strftime("%Y-%m-%d %H:%M") if item["precision"] == "minute" else parsed.isoformat()
            if canonical != item["shown"]:
                raise ValueError("非规范时间")
        except (TypeError, ValueError):
            errs.append("acknowledged.shown 须为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM")
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
    if not isinstance(ev.get("kind"), str) or not isinstance(ev.get("skill"), str):
        return ["kind 与 skill 须为字符串"]
    if type(ev["schemaVersion"]) is not int or ev["schemaVersion"] != SCHEMA_VERSION:
        errs.append(f"schemaVersion 应为 {SCHEMA_VERSION}")
    try:
        parse_at(ev["at"])
    except (TypeError, ValueError) as e:
        errs.append(f"at 无效：{e}")
    if ev["skill"] not in SKILLS:
        errs.append(f"skill 无效：{ev['skill']}")
    kind = ev["kind"]
    if kind in ACTION_KINDS:
        if not isinstance(ev.get("status"), str) or ev["status"] not in ACTION_STATUSES:
            errs.append(f"动作事件 status 无效：{ev.get('status')}")
        if "observed" in ev:
            errs.append("动作事件不应有 observed")
    elif kind == "observe":
        if not isinstance(ev.get("observed"), str) or ev["observed"] not in OBSERVATIONS:
            errs.append(f"观察事件 observed 无效：{ev.get('observed')}")
        if "status" in ev:
            errs.append("观察事件不应有 status")
    elif kind == "screen":
        if "status" in ev or "observed" in ev:
            errs.append("screen 不使用 status 或 observed")
    else:
        errs.append(f"kind 无效：{kind}")
    for k in ("platform", "account", "runId", "attemptId"):
        if not isinstance(ev.get(k), str) or not ev[k].strip():
            errs.append(f"{k} 不能为空")
    if not isinstance(ev.get("reason"), str) or not ev["reason"].strip():
        errs.append("reason 不能为空")
    if ev.get("hrAt") is not None:
        try:
            parse_at(ev["hrAt"])
        except (TypeError, ValueError):
            errs.append("hrAt 须为带时区的 ISO 时间")
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
    errs.extend(validate_v1(ev))
    return errs


def validate_v1(ev):
    errors = []
    if not isinstance(ev.get("eventId"), str) or not re.fullmatch(r"evt-[0-9a-f]{12}", ev["eventId"]):
        errors.append("eventId 无效")
    try:
        if (ev.get("threadKey"), ev.get("jobKey")) != event_keys(ev):
            errors.append("threadKey / jobKey 与页面字段不符")
    except (ValueError, TypeError, AttributeError):
        errors.append("平台或标识字段无效")
    for name in ("company", "title", "location", "recruiter", "targetRole", "jobId"):
        if ev.get(name) is not None and not isinstance(ev[name], str):
            errors.append(f"{name} 须为字符串或 null")
    for name in ("profileRev", "styleRev"):
        if name in ev and (not isinstance(ev[name], str) or not re.fullmatch(r"[0-9a-f]{12}", ev[name])):
            errors.append(f"{name} 无效")
    for name in ("checks", "asks"):
        if name in ev:
            values = ev[name]
            if (not isinstance(values, list) or any(not isinstance(v, str) or v not in INFO_KEYS for v in values)
                    or len(set(values)) != len(values)):
                errors.append(f"{name} 须为不重复的信息子项数组")
    for name in ("jdFacts", "facts"):
        if name in ev and (not isinstance(ev[name], dict) or any(
                key not in INFO_KEYS or not isinstance(value, str) for key, value in ev[name].items())):
            errors.append(f"{name} 须为信息子项到字符串的对象")
    kind, observed = ev["kind"], ev.get("observed")
    if kind == "screen":
        if ev.get("result") not in ("qualified", "pending", "excluded"):
            errors.append("screen.result 无效")
        if not isinstance(ev.get("reasons"), list) or any(not isinstance(x, str) for x in ev.get("reasons", [])):
            errors.append("screen.reasons 须为字符串数组")
        if "salaryText" in ev and not isinstance(ev["salaryText"], str):
            errors.append("salaryText 须为字符串")
    if "jdFacts" in ev or "checks" in ev:
        if kind not in ("screen", "greet"):
            errors.append("jdFacts / checks 只用于 screen 或 greet")
    if "asks" in ev and kind not in ACTION_KINDS:
        errors.append("asks 只用于外发动作")
    if "facts" in ev and not (kind == "observe" and observed == "info"):
        errors.append("facts 只用于 observe/info")
    if kind == "observe" and observed == "info" and ("facts" not in ev or not ev.get("jobKey")):
        errors.append("info 须带 facts 和可派生的 jobKey")
    if kind == "observe" and observed == "interview_invited" and (not ev.get("decision") or not ev.get("threadKey")):
        errors.append("interview_invited 须关联 decision 与目标 threadKey")
    if kind == "observe" and observed == "decision":
        if not ev.get("decision") or type(ev.get("version")) is not int or ev["version"] < 1:
            errors.append("decision 须带事项 ID 和正整数 version")
        steps = ev.get("steps")
        if not isinstance(steps, list):
            errors.append("decision.steps 须为数组")
        else:
            seen = set()
            for step in steps:
                if (not isinstance(step, dict) or set(step) - {"step", "kind", "text", "resume"}
                        or not isinstance(step.get("step"), str) or not STEP_RE.fullmatch(step["step"])
                        or step["step"] in seen or not isinstance(step.get("kind"), str) or step["kind"] not in ACTION_KINDS
                        or ("text" in step) + ("resume" in step) != 1):
                    errors.append("decision.steps 的步骤、动作或内容结构无效")
                    continue
                seen.add(step["step"])
                field = "text" if "text" in step else "resume"
                if not isinstance(step[field], str) or not step[field].strip():
                    errors.append("decision.steps 内容须为非空字符串")
                if (step["kind"] == "resume") != (field == "resume"):
                    errors.append("resume 步骤须使用 resume 版本，其他步骤使用 text")
    if kind == "observe" and observed in ("tail", "handled"):
        for name in ("tailId", "baseTailId"):
            value = ev.get(name)
            if name not in ev or (value is None and name != "baseTailId") or (value is not None and (
                    not isinstance(value, str) or not re.fullmatch(r"tail-[0-9a-f]{12}", value))):
                errors.append(f"{name} 无效或缺失")
        if not ev.get("threadKey"):
            errors.append("尾部记录须有完整 threadKey")
        if observed == "tail":
            errors.extend(validate_tail_record(ev))
        else:
            allowed = set(REQUIRED) | {"observed", "tailId", "baseTailId", "listMark", "fingerprints", "jobId", "jobUrl", "tags"}
            if set(ev) - allowed:
                errors.append("handled 含不允许的字段")
            if "listMark" in ev and (not isinstance(ev["listMark"], dict) or set(ev["listMark"]) - {"label", "digest", "unread"}
                    or not isinstance(ev["listMark"].get("label"), str)
                    or not isinstance(ev["listMark"].get("digest"), str)
                    or not re.fullmatch(r"[0-9a-f]{16}", ev["listMark"]["digest"])
                    or type(ev["listMark"].get("unread")) is not int or ev["listMark"]["unread"] < 0):
                errors.append("listMark 须为 label、16位 digest 与非负 unread，不存摘要原文")
    return errors


def validate_history_event(events, ev):
    if any(e["eventId"] == ev["eventId"] for e in events):
        raise LedgerError("eventId 重复")
    if ev["kind"] != "observe":
        return
    if ev["observed"] == "tail":
        if any(e["kind"] == "observe" and e["observed"] == "tail" and e["tailId"] == ev["tailId"] for e in events):
            raise LedgerError("tailId 重复")
        state = tail_state(events, thread_scope(ev))
        if ev["baseTailId"] and ev["baseTailId"] not in state["tails"]:
            raise LedgerError("baseTailId 不属于该会话")
        if state["latest"] and parse_at(ev["readAt"]) < parse_at(state["latest"]["readAt"]):
            raise LedgerError("readAt 早于该会话最新读取")
        outcome = tail_outcome(events, ev, state)
        if any(ev.get(key) != value for key, value in outcome.items()):
            raise LedgerError("tail 比较结果或未决状态与持久化证据矛盾")
    if ev["observed"] == "decision":
        prior = [e for e in events if e["kind"] == "observe" and e["observed"] == "decision"
                 and e.get("decision") == ev["decision"]]
        if ev["version"] != max((e["version"] for e in prior), default=0) + 1:
            raise LedgerError("decision.version 须为现有最大版本加一")
        used = {step["step"] for e in prior for step in e["steps"]}
        if used & {step["step"] for step in ev["steps"]}:
            raise LedgerError("decision.steps 名称不能跨版本重复")
    if ev["observed"] == "handled":
        tails = [e for e in events if e["kind"] == "observe" and e["observed"] == "tail"
                 and thread_scope(e) == thread_scope(ev)]
        if not tails or tails[-1]["tailId"] != ev["tailId"] or tails[-1]["baseTailId"] != ev["baseTailId"]:
            raise LedgerError("handled 只能引用同一会话最新 tail 与其 baseTailId")
        tail = tails[-1]
        if "fingerprints" in ev and ev["fingerprints"] != tail["fingerprints"]:
            raise LedgerError("handled.fingerprints 与快照不符")
        ev["fingerprints"] = tail["fingerprints"]
        if not tail["complete"] or not tail["anchorUnique"]:
            raise LedgerError("读取不全或锚点不唯一，不能 handled")
        if not tail["settled"] and not (ev["skill"] == "user" and isinstance(ev.get("evidence"), str) and ev["evidence"].strip()):
            raise LedgerError("tail 未 settled，须本人带核对依据收口")


def read_events(ws, strict=True):
    path = events_path(ws)
    events, errors = [], []
    if not path.exists():
        return events, errors
    # 只取最内层事件锁形成完整字节快照，不把只读升级为业务写入。
    with file_mutex(path.with_name(".events.mutex")):
        lines = fsutil.read_text(path).split("\n")
    for no, line in enumerate(lines, 1):
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
        if not errs:
            try:
                validate_history_event(events, ev)
            except LedgerError as error:
                errs.append(str(error))
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
    return operation_mutex(ws, ".stoplist.mutex")


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


def attempt_key(event):
    return event["platform"], event["account"], event["attemptId"]


def attempts_of(events):
    """按 (platform, account, attemptId) 归并动作事件。"""
    out = {}
    for ev in events:
        if ev["kind"] not in ACTION_KINDS:
            continue
        a = out.setdefault(attempt_key(ev), {"events": []})
        a["events"].append(ev)
    for aid, a in out.items():
        evs = a["events"]
        first = evs[0]
        statuses = [e["status"] for e in evs]
        a.update({
            "attemptId": first["attemptId"],
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
# 会话按平台、账号及 conversationKey 隔离。
# 同一 attempt 补齐的页面身份用于归并，事件原值不改写。

FOLLOWUP_MAX_TOTAL = 3
FOLLOWUP_MAX_UNANSWERED = 2
FOLLOWUP_COOLDOWN_DAYS = 7


def thread_key(ev):
    values = [(norm_company if field == "company" else norm_text)(ev.get(field))
              for field in load_platform(ev["platform"])["conversationKey"]]
    return (ev["platform"], ev["account"], *values)


def thread_identity(ev, attempts):
    identity = dict(ev)
    if ev["kind"] in ACTION_KINDS:
        attempt = attempts[attempt_key(ev)]
        identity["recruiter"] = attempt["recruiter"]
        for field in load_platform(ev["platform"])["conversationKey"]:
            if identity.get(field) is None:
                identity[field] = next((e[field] for e in attempt["events"] if e.get(field) is not None), None)
    return identity


def _base_threads(events, platform=None):
    atts = attempts_of(events)
    threads = {}

    for ev in events:
        if platform and ev["platform"] != platform:
            continue
        identity = thread_identity(ev, atts)
        rec = identity.get("recruiter")
        key = thread_key(identity)
        t = threads.setdefault(key, {
            "platform": ev["platform"], "account": ev["account"], "company": ev["company"], "recruiter": rec,
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
        elif ev["kind"] in ACTION_KINDS and ev["status"] == "sent":
            if t["boundaryAt"] is None or parse_at(ev["at"]) > parse_at(t["boundaryAt"]):
                t["boundaryAt"] = ev["at"]
            t["lastOutbound"] = {"kind": ev["kind"], "at": ev["at"]}
            if ev.get("text") or ev["kind"] == "resume":
                t["outboundTexts"].append({"kind": ev["kind"], "at": ev["at"], "text": ev.get("text")})
            if ev["kind"] == "nudge":
                t["followupsSent"] += 1
                t["followupsSinceReply"] += 1
                t["lastFollowupAt"] = ev["at"]
    for a in atts.values():
        if a["status"] in ("pending", "uncertain"):
            t = threads.get(thread_key(thread_identity(a["events"][0], atts)))
            if t is not None:
                t["unresolved"].append({k: a[k] for k in ("platform", "account", "attemptId", "kind", "status")})
    return threads


def sent_metadata(attempt):
    """仅确认 sent 后使用准备时元数据；显式终态值优先，异职位不继承。"""
    sent = next((e for e in attempt["events"] if e["status"] == "sent"), None)
    if sent is None:
        return None, None, {}
    job = sent.get("jobKey") or next((e.get("jobKey") for e in attempt["events"] if e.get("jobKey")), None)
    metadata = {}
    for event in attempt["events"]:
        if event.get("jobKey") in (None, job):
            for field in ("asks", "tags"):
                if field in event:
                    metadata[field] = event[field]
        if event is sent:
            break
    return sent, job, metadata


def build_threads(events, platform=None, profile=None, account=None):
    """在既有接管/暂停投影上补只读派生；尾部安全只消费已校验事件。"""
    all_threads = _base_threads(events)
    threads = {key: t for key, t in all_threads.items() if (platform is None or t["platform"] == platform)
               and (account is None or t["account"] == account)}
    atts = attempts_of(events)
    sent = {aid: sent_metadata(a) for aid, a in atts.items()}
    inquiry = (profile or {}).get("inquiry", {})
    if not isinstance(inquiry, dict):
        raise LedgerError("inquiry 须为对象")
    priority = inquiry.get("priority", DEFAULT_INQUIRY_PRIORITY)
    if not isinstance(priority, (list, tuple)) or any(not isinstance(x, str) or x not in INFO_GROUPS for x in priority):
        raise LedgerError("inquiry.priority 须为信息大类数组")
    jobs, rounds, counts, tails, related, contexts = {}, {}, {}, {}, {}, {}
    for key, thread in threads.items():
        thread.update(threadKey=None, account=None, currentJobKey=None, stage=None, interviewIntentStages=[],
                      pendingTail=None, lastHandled=None, lastOpenedAt=None, decisions=[])
        related[key] = set()
    for ev in events:
        job = ev.get("jobKey")
        if job:
            data = jobs.setdefault((ev["platform"], ev["account"], job), {"jd": {}, "facts": {}, "checks": []})
            data["jd"].update(ev.get("jdFacts") or {})
            if ev["kind"] == "observe" and ev["observed"] == "info":
                data["facts"].update(ev["facts"])
            for check in ev.get("checks", []):
                if check not in data["checks"]:
                    data["checks"].append(check)
        if ev["kind"] == "observe" and ev["observed"] in ("tail", "handled"):
            tails.setdefault(thread_scope(ev), []).append(ev)
        identity = thread_identity(ev, atts)
        key = thread_key(identity)
        if key not in threads:
            continue
        thread = threads[key]
        thread["threadKey"] = event_keys(identity)[0]
        thread["account"] = ev["account"]
        if job:
            thread["currentJobKey"] = job
        if ev["kind"] in ACTION_KINDS:
            contexts.setdefault(attempt_key(ev), thread["currentJobKey"])
        if ev.get("decision"):
            related[key].add(ev["decision"])
        milestone = None
        if ev["kind"] == "observe":
            obs = ev["observed"]
            milestone = {"replied": "replied", "info": "replied", "resume_requested": "replied",
                         "resume_delivered": "resume", "resume_viewed": "resume",
                         "interview_invited": "interview_invited"}.get(obs)
            if obs == "info" and ev["facts"]:
                milestone = "info"
            if obs in ("replied", "info", "resume_requested", "interview_invited") and thread["currentJobKey"]:
                pair = (key, thread["currentJobKey"])
                total = counts.setdefault(pair, {})
                # 一个问询尝试只消费首轮后续回复，多条 HR 发言不会重复加数。
                waiting = []
                for asked in rounds.pop(pair, []):
                    if ev.get("hrAt") and parse_at(ev["hrAt"]) < asked["sentAt"]:
                        waiting.append(asked)
                        continue
                    for item in asked["asks"]:
                        total[item] = total.get(item, 0) + 1
                if waiting:
                    rounds[pair] = waiting
        elif ev["kind"] in ACTION_KINDS and sent[attempt_key(ev)][0] is ev:
            _, actual_job, metadata = sent[attempt_key(ev)]
            # 省略职位的允许输入承接首次尝试时上下文；晚确认不能挪到新职位。
            actual_job = actual_job or contexts[attempt_key(ev)]
            milestone = "resume" if ev["kind"] == "resume" else "greeted"
            if actual_job and metadata.get("asks"):
                rounds.setdefault((key, actual_job), []).append({"asks": metadata["asks"], "sentAt": parse_at(ev["at"])})
        if milestone and (thread["stage"] is None or THREAD_STAGES.index(milestone) > THREAD_STAGES.index(thread["stage"])):
            thread["stage"] = milestone
        if ev["kind"] in ACTION_KINDS and sent[attempt_key(ev)][0] is ev:
            if "intent:interview" in sent[attempt_key(ev)][2].get("tags", []) and thread["stage"] not in thread["interviewIntentStages"]:
                thread["interviewIntentStages"].append(thread["stage"])
    decisions = decisions_of(events, all_threads)
    for key, thread in threads.items():
        data = jobs.get((thread["platform"], thread["account"], thread["currentJobKey"]), {"jd": {}, "facts": {}, "checks": []})
        known = dict(data["jd"], **data["facts"])
        unanswered = {item: count for item, count in counts.get((key, thread["currentJobKey"]), {}).items() if item not in known}
        gave_up = [item for item in INFO_KEYS if unanswered.get(item, 0) >= 2]
        order = list(data["checks"])
        for group in priority:
            order.extend(item for item in INFO_GROUPS[group] if item not in order)
        thread["info"] = {"known": known, "pending": [item for item in order if item not in known and item not in gave_up],
                          "askedUnanswered": unanswered, "gaveUp": sorted(gave_up)}
        scope = thread_scope(thread)
        state = tail_state(tails.get(scope, []), scope)
        latest, handled = state["latest"], state["handled"]
        thread["lastOpenedAt"] = latest["readAt"] if latest else None
        if state["pending"]:
            thread["pendingTail"] = {field: latest[field] for field in
                ("tailId", "baseTailId", "result", "newCount", "readAt", "reason", "settled", "reviewRequired", "complete", "anchorUnique")}
            thread["pendingTail"]["reviewReasons"] = state["reviewReasons"]
        if handled:
            record = state["handledEvent"]
            thread["lastHandled"] = {"tailId": handled["tailId"], "at": record["at"], "readAt": handled["readAt"],
                                     "listMark": record.get("listMark"), "complete": handled["complete"]}
        thread["decisions"] = [decisions[d] for d in sorted(related[key]) if d in decisions]
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


def company_observation_reasons(events, platform, account, company):
    out = []
    for ev in events:
        if ev["kind"] == "observe" and ev["platform"] == platform and ev["account"] == account \
                and ev["observed"] in COMPANY_BLOCKING_OBSERVATIONS \
                and match_company(company, [ev["company"]])[0] == "exact":
            out.append({"type": ev["observed"],
                        "detail": f"该公司已记 {ev['observed']}（{ev['at']}）：{ev['reason']}"})
    return out


def first_contact_reasons(args, events, scope):
    """首次联系去重：所有动作建立的联系都算，不只 greet。"""
    reasons = []
    url_id = job_id_from_url(args.platform, args.job_url)
    if url_id and args.job_id and args.job_id != url_id:
        raise LedgerError("jobId 与链接中可读取的 ID 不符")
    job_id = args.job_id or url_id
    contacts = [a for a in attempts_of(events).values() if attempt_blocks(a, args.run)]
    contacts += [{**ev, "lastAt": ev["at"], "jobId": ev.get("jobId"), "status": "adopted"}
                 for ev in events if ev["kind"] == "observe" and ev["observed"] == "adopted"]
    for a in contacts:
        if a["platform"] != args.platform or a["account"] != args.account:
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


# ---------- 原子发送准备与只读同路径预检 ----------

INTENT_FIELDS = {"kind", "platform", "account", "company", "title", "location", "recruiter", "jobUrl",
                 "text", "resume", "reason", "targetRole", "jdFacts", "checks", "asks", "tags", "decision", "step", "pageState"}


def intent_event(ws, body, run, lock, profile, check_job_id=None):
    if not isinstance(body, dict) or set(body) - INTENT_FIELDS:
        raise LedgerError("发送意图含无效字段；status、ID、版本、授权来源、摘要及绑定只能由脚本生成")
    if not isinstance(body.get("kind"), str) or body["kind"] not in ACTION_KINDS or not body.get("platform"):
        raise LedgerError("发送意图须有合法 kind / platform")
    if not isinstance(body.get("company"), str) or not norm_company(body["company"]):
        raise LedgerError("发送意图须有可辨认的公司")
    if not isinstance(profile, dict) or not isinstance(profile.get("schedule", {}), dict):
        raise LedgerError("profile / schedule 须为对象")
    if (body["kind"] == "resume" and "text" in body) or (body["kind"] != "resume" and "resume" in body):
        raise LedgerError("文字动作与简历动作不能合并成一个尝试")
    ev = dict(body)
    if ev.get("decision") and not ev.get("step"):
        raise LedgerError("执行具体事项须带 step；事项字符串本身不是授权")
    if check_job_id is not None:
        if not isinstance(check_job_id, str) or not check_job_id.strip():
            raise LedgerError("check --job-id 须为非空页面ID")
        ev["jobId"] = check_job_id
    if "account" in ev and (not isinstance(ev["account"], str) or not ev["account"].strip()):
        raise LedgerError("显式 account 须为非空字符串")
    ev["account"] = resolve_account(ws, ev["platform"], ev.get("account"))
    for field in ("text", "resume", "jobUrl"):
        if field in ev and not isinstance(ev[field], str):
            raise LedgerError(f"{field} 须为字符串")
    if ev["kind"] == "resume" and not ev.get("resume", "").strip():
        raise LedgerError("简历动作须有 resume 版本")
    if ev.get("jobUrl"):
        ev["jobUrl"] = normalize_job_url(ev["platform"], ev["jobUrl"])
        actual_id = job_id_from_url(ev["platform"], ev["jobUrl"])
        if actual_id and ev.get("jobId") not in (None, actual_id):
            raise LedgerError("jobId 与链接中可读取的 ID 不符")
        ev["jobId"] = actual_id or ev.get("jobId")
    ev.update(schemaVersion=SCHEMA_VERSION, status="pending", runId=run or "read-only-check",
              skill=(lock or {}).get("skill", "followup"), at=now_iso(tz_of(profile)),
              attemptId="att-" + uuid.uuid4().hex[:12])
    ev = prepare_event(ev)
    errors = validate_event(ev)
    if errors:
        raise LedgerError("发送意图不合格：" + "；".join(errors))
    return ev


def prepare_or_check(args, ws, write):
    with lock_mutex(ws, write=write):
        profile = load_profile(ws)
        lock = read_lock(ws)
        if args.json is not None:
            body = lock_object(args.json)
        elif write:
            body = lock_object(sys.stdin.read())
        else:
            body = {"platform": args.platform, "company": args.company, "kind": args.kind,
                    "reason": args.reason or "只读发送预检"}
            for source, target in (("account", "account"), ("title", "title"), ("recruiter", "recruiter"),
                                   ("job_url", "jobUrl"), ("decision", "decision"), ("step", "step"),
                                   ("text", "text"), ("resume", "resume"), ("target_role", "targetRole")):
                if getattr(args, source, None) is not None:
                    body[target] = getattr(args, source)
        if not write and args.scope is not None and args.scope != effective_limits(profile, body.get("platform"))["dedupScope"]:
            raise LedgerError("check 不能临时覆盖去重配置；逐个放行须登记 dedup_once")
        ev = intent_event(ws, body, args.run, lock, profile, getattr(args, "job_id", None))
        events, _ = read_events(ws)
        result = outbound.evaluate(sys.modules[__name__], ws, ev, args.run, lock, events, profile)
        if write and result["allowed"]:
            ev.update(authorizedBy=result["authorizedBy"], profileRev=config_rev(profile), styleRev=config_rev(profile.get("style", {})),
                      runPolicyRev=run_policy_rev(lock))
            for field in ("decisionVersion", "dedupGrant"):
                if field in result:
                    ev[field] = result[field]
            validate_history_event(events, ev)
            append_line(ws, ev)
            # 续期失败则保留已落盘 pending，由统一收尾处理，绝不重建尝试。
            require_live_lock(ws, args.run, tz_of(profile), renew=True)
            result.update(attemptId=ev["attemptId"], eventId=ev["eventId"])
        return emit(result, 0 if result["allowed"] else 1)


def cmd_prepare(args, ws):
    return prepare_or_check(args, ws, True)


def cmd_check(args, ws):
    return prepare_or_check(args, ws, False)


# ---------- 命令：decision ----------

def attempt_target_conflict(attempt, target):
    fields = {"platform", "account", "company", "title", "recruiter", "jobId",
              *load_platform(attempt["platform"])["conversationKey"]}
    for field in fields:
        if field in ("platform", "account", "jobId"):
            normalize = lambda value: value
        else:
            normalize = norm_company if field == "company" else norm_text
        if target.get(field) is not None and any(
                e.get(field) is not None and normalize(e[field]) != normalize(target[field]) for e in attempt["events"]):
            return True
    return False


def decision_attempt(attempt, decision, owners, effective, invitations, positions):
    """当前尚无 prepare 冻结：只用已登记且唯一的 step 归版，不倒推新授权。"""
    first = attempt["events"][0]
    ids = {e["decision"] for e in attempt["events"] if e.get("decision")}
    steps = {e["step"] for e in attempt["events"] if e.get("step")}
    owner = owners.get(attempt["step"])
    claims = [e["decisionVersion"] for e in attempt["events"] if e.get("decisionVersion") is not None]
    invitation = next((e for e in invitations if thread_scope(e) == thread_scope(attempt)
                       and positions[e["eventId"]] < positions[first["eventId"]]), None)
    ack = attempt["step"] == "ack" and invitation is not None
    issue, version = None, None
    if ids != {decision} or len(steps) != 1:
        issue = "ambiguous_link"
    elif first.get("decision") != decision or first.get("step") != attempt["step"]:
        issue = "late_binding"
    elif ack:
        if attempt_target_conflict(attempt, invitation):
            issue = "ack_target_conflict"
        elif attempt["kind"] != "reply":
            issue = "ack_kind_conflict"
        elif claims:
            issue = "ack_has_version"
    elif not owner or positions[owner[1]["eventId"]] > positions[first["eventId"]]:
        issue = "step_not_registered"
    else:
        number, declaration, definition = owner
        payload_key = "resume" if definition["kind"] == "resume" else "text"
        payload = next((e[payload_key] for e in reversed(attempt["events"]) if e.get(payload_key) is not None), None)
        target_conflict = attempt_target_conflict(attempt, declaration)
        if any(type(value) is not int or value != number for value in claims):
            issue = "version_conflict"
        elif target_conflict:
            issue = "step_target_conflict"
        elif attempt["kind"] != definition["kind"] or (payload is not None and payload != definition[payload_key]):
            issue = "step_content_conflict"
        else:
            version = number
    out = {k: attempt[k] for k in ("platform", "account", "attemptId", "kind", "step", "status", "company", "recruiter", "lastAt")}
    out.update(decisionVersion=version, versionInvalidated=version is not None and version != effective,
               mappingIssue=issue)
    return out, ack


def step_state(statuses):
    # 已发事实不降级；不同 attempt 的未决状态另列并独立阻断整个事项。
    if "sent" in statuses:
        return "sent"
    if "pending" in statuses:
        return "pending"
    if "uncertain" in statuses:
        return "uncertain"
    return "failed_no_send" if statuses else "not_started"


def decisions_of(events, threads=None, only=None):
    """按事项/版本/step/attempt 分层派生，版本变化不抹去任何旧未决。"""
    threads = _base_threads(events) if threads is None else threads
    positions = {event["eventId"]: i for i, event in enumerate(events)}
    observations, actions = {}, {}
    for ev in events:
        if ev["kind"] == "observe" and ev.get("decision") and (only is None or ev["decision"] == only):
            observations.setdefault(ev["decision"], []).append(ev)
    for attempt in attempts_of(events).values():
        for decision in {e["decision"] for e in attempt["events"] if e.get("decision")}:
            if only is None or decision == only:
                actions.setdefault(decision, []).append(attempt)
    result = {}
    ids = set(observations) | set(actions) | ({only} if only is not None else set())
    for decision in sorted(ids):
        obs, linked = observations.get(decision, []), actions.get(decision, [])
        declarations = [e for e in obs if e["observed"] == "decision"]
        latest = declarations[-1] if declarations else None
        effective = latest["version"] if latest else None
        owners = {step["step"]: (e["version"], e, step) for e in declarations for step in e["steps"]}
        invitations = [e for e in obs if e["observed"] == "interview_invited"]
        reviews = {(e["platform"], e["account"], thread_key(e)): e for e in obs
                   if e["observed"] == DECISION_DONE_OBSERVATION and e["skill"] == "user"}
        attempts, system, unmapped = [], [], []
        for attempt in linked:
            projected, ack = decision_attempt(attempt, decision, owners, effective, invitations, positions)
            identity = thread_identity(attempt["events"][0], {attempt_key(attempt): attempt})
            review = reviews.get((attempt["platform"], attempt["account"], thread_key(identity)))
            # 本人已核对的终态不因后来登记新版而再次变成未知；保留原记录和依据。
            projected["reviewResolvedBy"] = (review["eventId"] if review and projected["mappingIssue"]
                and attempt["status"] not in ("pending", "uncertain")
                and positions[review["eventId"]] > positions[attempt["events"][-1]["eventId"]] else None)
            attempts.append(projected)
            if ack:
                system.append(projected)
            if projected["mappingIssue"]:
                unmapped.append(projected)
        by_step = {}
        for projected in attempts:
            if projected["decisionVersion"] is not None:
                by_step.setdefault((projected["decisionVersion"], projected["step"]), []).append(projected)
        versions, flat = [], {}
        for declaration in declarations:
            number = declaration["version"]
            projected_steps = []
            for definition in declaration["steps"]:
                matches = by_step.get((number, definition["step"]), [])
                status = step_state([a["status"] for a in matches])
                if number != effective and status in ("not_started", "failed_no_send"):
                    status = "superseded"
                projected_steps.append(dict(definition, status=status, attempts=matches))
                flat[definition["step"]] = status
            versions.append({"version": number, "active": number == effective, "steps": projected_steps})
        still_pausing = [{"company": t["company"], "recruiter": t["recruiter"]}
                         for t in threads.values() if decision in t["pausedBy"]]
        start = positions[latest["eventId"]] if latest else -1
        end = max((positions[e["eventId"]] for a in linked for e in a["events"]), default=start)
        marks = [e for e in obs if e["observed"] == DECISION_DONE_OBSERVATION
                 and positions[e["eventId"]] > max(start, end)]
        marker = marks[-1] if marks and not still_pausing else None
        local_done = [e["observed"] for e in obs if positions[e["eventId"]] > start
                      and e["observed"] in ("released", "adopted", "interview_decided", "declined", "reopened", "manual")]
        current = versions[-1]["steps"] if versions else []
        unresolved = any(a["status"] in ("pending", "uncertain") for a in attempts)
        user_completion = bool(marker and marker["skill"] == "user")
        if unresolved or any(not a["reviewResolvedBy"] for a in unmapped):
            state, advice = "needs_review", "有未决或无法归属的尝试；先本人核对，不新增外发"
        elif marker and (user_completion or all(step["status"] == "sent" for step in current)):
            state, advice = "done", "已收尾；不重复发送，未能归版的历史记录仍保留供核对"
        elif any(step["status"] == "failed_no_send" for step in current):
            state, advice = "failed", "有明确未发送的步骤，本轮不重试，请本人决定"
        elif local_done or any(step["status"] == "sent" for step in current):
            state, advice = "partial", "跳过已发步骤；仅继续有效版本的未开始步骤，完成后收尾"
        else:
            state, advice = "not_started", "有效版本尚未执行；先确认步骤和本轮授权"
        result[decision] = {
            "decision": decision, "effectiveVersion": effective, "versions": versions, "steps": flat,
            "state": state, "advice": advice, "attempts": attempts, "unmappedAttempts": unmapped,
            "systemAttempts": system, "completionConfirmedByUser": user_completion and not unresolved,
            "localActions": local_done, "stillPausing": still_pausing,
            "pausedRecords": [{k: ev.get(k) for k in ("at", "platform", "company", "recruiter", "title", "reason")}
                              for ev in obs if ev["observed"] == "paused"],
        }
    return result


def decision_completed(events, decision):
    return decisions_of(events, only=decision)[decision]["state"] == "done"


def unstepped_decision_attempts(events, decision):
    """只解除同目标本人已核对终态的既有缺 step 阻断，不隐藏未决。"""
    summary = decisions_of(events, only=decision)[decision]
    blocked = {attempt_key(a) for a in summary["unmappedAttempts"] if not a["step"] and not a["reviewResolvedBy"]}
    return [a for a in attempts_of(events).values() if attempt_key(a) in blocked]


def cmd_decision(args, ws):
    events, _ = read_events(ws)
    out = decisions_of(events, only=args.id)[args.id]
    if args.step:
        state = out["steps"].get(args.step, "not_started")
        if out["state"] == "needs_review" and state not in ("sent", "superseded", "pending", "uncertain", "failed_no_send"):
            state = "needs_review"
        out["stepState"] = state
    return emit(out)


# ---------- 命令：guard ----------

def cmd_guard(args, ws):
    """可能外发的每次 UI 操作（点击沟通、发送、同意）之前调用：确认仍持锁、尝试仍待定，并续期。"""
    tz = tz_of(load_profile(ws))
    with lock_mutex(ws):
        require_live_lock(ws, args.run, tz, renew=True)
        events, _ = read_events(ws)
        candidates = [a for a in attempts_of(events).values() if a["attemptId"] == args.attempt
                      and (args.platform is None or a["platform"] == args.platform)
                      and (args.account is None or a["account"] == args.account)]
        if args.platform and args.account is None:
            account = resolve_account(ws, args.platform)
            candidates = [a for a in candidates if a["account"] == account]
        if not candidates:
            raise LedgerError(f"找不到 attempt {args.attempt}")
        if len(candidates) != 1:
            raise LedgerError("attemptId 存在多个命名空间，请明确 --platform 和 --account")
        a = candidates[0]
        resolve_account(ws, a["platform"], a["account"])
        if a["runId"] != args.run:
            return emit({"allowed": False, "reason": f"attempt 属于 {a['runId']}，不是本轮"}, 1)
        if a["status"] != "pending":
            return emit({"allowed": False,
                         "reason": f"attempt 当前为 {a['status']}，不能再操作；重新核对页面，不新建尝试"}, 1)
    return emit({"allowed": True, "attempt": args.attempt, "platform": a["platform"], "account": a["account"]})


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
    if any(key in ev and not isinstance(ev[key], str) for key in ("kind", "skill", "status", "observed")):
        raise LedgerError("kind、skill、status、observed 须为字符串")
    if ev.get("kind") in ACTION_KINDS and ev.get("status") == "pending":
        raise LedgerError("动作 pending 只能用 ledger prepare 原子准备")
    if any(field in ev for field in ("eventId", "authorizedBy", "decisionVersion", "dedupGrant", "profileRev", "styleRev", "scopeBindings", "runPolicyRev")):
        raise LedgerError("ID、授权、版本、摘要与绑定由脚本冻结，不能指定或覆盖")
    if ev.get("kind") == "observe" and ev.get("observed") == "tail":
        raise LedgerError("observe/tail 只能用 ledger tail 写入")
    profile = load_profile(ws)
    tz = tz_of(profile)
    ev.setdefault("schemaVersion", SCHEMA_VERSION)
    ev.setdefault("at", fsutil.now(tz).isoformat())
    try:
        parse_at(ev["at"])
    except (ValueError, TypeError) as error:
        raise LedgerError("at 须为带时区的 ISO 时间") from error
    if ev.get("platform"):
        load_platform(ev["platform"])
    if "account" in ev and (not isinstance(ev["account"], str) or not ev["account"].strip()):
        raise LedgerError("显式 account 须为非空字符串")
    if "account" not in ev and ev.get("platform"):
        ev["account"] = default_account(ws, ev["platform"])
    for k in ("title", "location", "recruiter", "evidence"):
        ev.setdefault(k, None)
    if ev.get("jobUrl") is not None and not isinstance(ev["jobUrl"], str):
        raise LedgerError("jobUrl 须为字符串或 null")
    if ev.get("jobUrl") and ev.get("platform"):
        ev["jobUrl"] = normalize_job_url(ev["platform"], ev["jobUrl"])
        url_id = job_id_from_url(ev["platform"], ev["jobUrl"])
        if url_id and ev.get("jobId") not in (None, "", url_id):
            raise LedgerError("jobId 与链接中可读取的 ID 不符")
        if url_id is not None or not ev.get("jobId"):
            ev["jobId"] = url_id
    if not ev.get("attemptId"):
        if ev.get("kind") in ACTION_KINDS and ev.get("status") not in STANDALONE_STATUSES:
            raise LedgerError("终态事件须带上对应 pending 的 attemptId")
        ev["attemptId"] = "att-" + uuid.uuid4().hex[:12]
    ev = prepare_event(ev, tz)
    errs = validate_event(ev)
    if errs:
        raise LedgerError("事件不合格：" + "；".join(errs))
    if ev["kind"] in ACTION_KINDS and ev.get("decision") and not ev.get("step") and ev["status"] in STANDALONE_STATUSES:
        raise LedgerError("执行已决定事项的外发须带 step（如 reply1、resume1）")

    adopting = ev["kind"] == "observe" and ev["observed"] == "adopted"

    def write():
        events, _ = read_events(ws)
        history = events if not (ev["kind"] in ACTION_KINDS and ev["status"] in STANDALONE_STATUSES) else ()
        resolve_account(ws, ev["platform"], ev["account"], history)
        validate_history_event(events, ev)
        if adopting:
            result = adoption_result(events, ev)
            if result is not None:
                return result
        if ev["kind"] in ACTION_KINDS:
            prior = [e for e in events if attempt_key(e) == attempt_key(ev)]
            if prior:
                for field in ("authorizedBy", "decisionVersion", "dedupGrant", "profileRev", "styleRev", "runPolicyRev"):
                    if field in prior[0]:
                        ev[field] = prior[0][field]
            check_transition(prior, ev)
            if ev["status"] == "pending" and ev.get("decision") and unstepped_decision_attempts(events, ev["decision"]):
                raise LedgerError(f"决定 {ev['decision']} 有不带步骤编号的外发记录，须本人核对后再执行")
        elif any(attempt_key(e) == attempt_key(ev) for e in events):
            raise LedgerError(f"attemptId {ev['attemptId']} 已被使用")
        append_line(ws, ev)
        return {"ok": True, "event": ev}, 0

    # 本人交出会话与 pending 共用有效锁检查。
    with lock_mutex(ws):
        version_gate(ws, "write")
        if ev["kind"] == "screen":
            require_live_lock(ws, ev["runId"], tz, allow_record=True)
        elif adopting or (ev["kind"] in ACTION_KINDS and ev["status"] == "pending"):
            require_live_lock(ws, ev["runId"], tz, renew=True)
        out, code = write()
    return emit(out, code)


# ---------- 会话尾部：只保存指纹，不保存输入原文 ----------

def fingerprint_valid(fp):
    if fp == "start":
        return True
    if not isinstance(fp, dict):
        return False
    if set(fp) == {"id"}:
        return isinstance(fp["id"], str) and bool(fp["id"].strip())
    if set(fp) != {"speaker", "type", "digest", "date", "minute", "precision"}:
        return False
    if fp["speaker"] not in ("us", "hr") or fp["type"] not in ("text", "card"):
        return False
    if not isinstance(fp["digest"], str) or not re.fullmatch(r"[0-9a-f]{16}", fp["digest"]):
        return False
    try:
        if fp["date"] is not None and date.fromisoformat(fp["date"]).isoformat() != fp["date"]:
            return False
        if fp["minute"] is not None:
            if not isinstance(fp["minute"], str) or not re.fullmatch(r"\d{2}:\d{2}", fp["minute"]):
                return False
            datetime.strptime(fp["minute"], "%H:%M")
    except (ValueError, TypeError):
        return False
    return fp["precision"] == ("none" if fp["date"] is None else "minute" if fp["minute"] else "day")


def validate_bridge_evidence(ev):
    """ID 指纹没有角色/内容/时间；只补本轮发送核验必需的最小事实。"""
    proof, bridge = ev.get("bridgeEvidence"), ev.get("bridge")
    if not isinstance(proof, list) or not isinstance(bridge, list) or len(proof) != len(bridge):
        return ["tail.bridgeEvidence 须与 bridge 逐项对应"]
    for item in proof:
        if not isinstance(item, dict) or item.get("speaker") not in ("us", "hr"):
            return ["tail.bridgeEvidence 发言方无效"]
        if set(item) == {"speaker"}:
            continue
        if (set(item) != {"speaker", "attemptId", "type", "digest", "pageAt"} or item["speaker"] != "us"
                or item["type"] not in ("text", "card") or not isinstance(item["digest"], str)
                or not re.fullmatch(r"[0-9a-f]{16}", item["digest"])
                or (item["attemptId"] is not None and (not isinstance(item["attemptId"], str) or not item["attemptId"].strip()))):
            return ["tail.bridgeEvidence 发送证据无效"]
        if item["pageAt"] is not None:
            try:
                at = parse_at(item["pageAt"])
                if at.second or at.microsecond:
                    raise ValueError("须为分钟时间")
            except (ValueError, TypeError):
                return ["tail.bridgeEvidence.pageAt 须为带时区的分钟时间或 null"]
    return []


def validate_tail_record(ev):
    errors = []
    for key in ("complete", "anchorUnique", "baseExplicit", "settled", "reviewRequired"):
        if type(ev.get(key)) is not bool:
            errors.append(f"tail.{key} 须为布尔值")
    if ev.get("result") not in ("same", "extended", "new_anchor", "mismatch"):
        errors.append("tail.result 无效")
    if not fingerprint_valid(ev.get("anchor")):
        errors.append("tail.anchor 指纹无效")
    for key in ("fingerprints", "bridge"):
        if key == "bridge" and key not in ev:
            continue
        if not isinstance(ev.get(key), list) or any(fp == "start" or not fingerprint_valid(fp) for fp in ev[key]):
            errors.append(f"tail.{key} 指纹序列无效")
    try:
        if parse_at(ev.get("readAt")) > parse_at(ev["at"]):
            errors.append("tail.readAt 晚于事件时间")
    except (ValueError, TypeError):
        errors.append("tail.readAt 无效")
    if ev.get("newCount") is not None and (type(ev["newCount"]) is not int or ev["newCount"] < 0):
        errors.append("tail.newCount 须为非负整数或 null")
    if "bridge" in ev:
        if type(ev.get("bridgeComplete")) is not bool:
            errors.append("tail.bridgeComplete 须为布尔值")
        errors.extend(validate_bridge_evidence(ev))
    elif "bridgeEvidence" in ev or "bridgeComplete" in ev:
        errors.append("tail.bridge 证据缺少指纹序列")
    if ev.get("settled") and ev.get("result") == "new_anchor" and (not ev.get("bridge") or not ev.get("bridgeComplete")):
        errors.append("new_anchor settled 缺少完整 bridge")
    if "text" in ev or "messages" in ev:
        errors.append("tail 不得保存消息原文")
    if ev.get("settled") and (ev.get("reviewRequired") or ev.get("result") not in ("same", "new_anchor")
                               or not ev.get("baseTailId") or not ev.get("complete") or not ev.get("anchorUnique")):
        errors.append("tail.settled 与核对证据矛盾")
    return errors


def message_fingerprint(message, speaker, read_at, tz, platform):
    if (not isinstance(message, dict) or set(message) - {"speaker", "type", "text", "label", "id", "attemptId"}
            or message.get("speaker", speaker) != speaker or message.get("type") not in ("text", "card")
            or not isinstance(message.get("text"), str) or not message["text"].strip()
            or (message.get("label") is not None and not isinstance(message["label"], str))):
        raise LedgerError("尾部消息的发言方、类型、文字或时间标签无效")
    if "attemptId" in message and (not isinstance(message["attemptId"], str) or not message["attemptId"].strip()):
        raise LedgerError("bridge.attemptId 须为非空字符串")
    if "id" in message:
        if (not load_platform(platform)["messageId"]["available"] or not isinstance(message["id"], str)
                or not message["id"].strip()):
            raise LedgerError("平台未启用稳定消息 ID 或 id 无效")
        return {"id": message["id"]}
    return dict(speaker=speaker, type=message["type"], digest=content_digest(message["text"]),
                **resolve_time_label(message.get("label"), read_at, tz, platform))


def definite_fingerprint(fp):
    return fp == "start" or "id" in fp or bool(fp.get("date") and fp.get("minute"))


def fingerprints_equal(left, right):
    return definite_fingerprint(left) and definite_fingerprint(right) and left == right


def sequence_equal(left, right):
    return len(left) == len(right) and all(fingerprints_equal(a, b) for a, b in zip(left, right))


def tail_state(events, scope):
    """按 (原始平台, 原始账号, 外部 threadKey) 聚合尾部安全状态。"""
    latest, handled, handled_event, review = None, None, None, False
    tails, review_reasons = {}, []
    for event in events:
        if event["kind"] != "observe" or thread_scope(event) != scope:
            continue
        if event["observed"] == "tail":
            latest = event
            tails[event["tailId"]] = event
            review |= event["reviewRequired"]
            if (event["reviewRequired"] and event["reason"] not in ("same", "extended", "no_base", "bridge_verified")
                    and event["reason"] not in review_reasons):
                review_reasons.append(event["reason"])
        elif event["observed"] == "handled":
            handled, handled_event = tails[event["tailId"]], event
            if event["skill"] == "user" and isinstance(event.get("evidence"), str) and event["evidence"].strip():
                review, review_reasons = False, []
    return {"scope": scope, "latest": latest, "handled": handled, "handledEvent": handled_event, "review": review,
            "reviewReasons": review_reasons, "tails": tails,
            "pending": latest is not None and (handled is None or handled["tailId"] != latest["tailId"])}


def compare_tail(base, anchor, messages, complete, unique):
    if not complete:
        return "mismatch", None, "incomplete_read"
    if not unique:
        return "mismatch", None, "anchor_ambiguity"
    if not all(definite_fingerprint(fp) for fp in [anchor, *messages]):
        return "mismatch", None, "time_ambiguity"
    ids = [fp["id"] for fp in [anchor, *messages] if isinstance(fp, dict) and "id" in fp]
    if len(set(ids)) != len(ids):
        return "mismatch", None, "duplicate_identity"
    if base is None:
        return "new_anchor", len(messages), "no_base"
    if not all(definite_fingerprint(fp) for fp in [base["anchor"], *base["fingerprints"]]):
        return "mismatch", None, "time_ambiguity"
    if not base["complete"] or not base["anchorUnique"]:
        return "mismatch", None, "incomplete_base"
    if not fingerprints_equal(base["anchor"], anchor):
        if anchor == "start":
            return "mismatch", None, "anchor_ambiguity"
        return "new_anchor", len(messages), "anchor_changed"
    prior = base["fingerprints"]
    if len(messages) < len(prior):
        return "mismatch", None, "count_decreased"
    if not sequence_equal(prior, messages[:len(prior)]):
        return "mismatch", None, "content_or_order_changed"
    if len(messages) == len(prior):
        return "same", 0, "same"
    return "extended", len(messages) - len(prior), "extended"


def bridge_evidence(raw_bridge, base, read_at, tz, platform, known_attempts):
    proof = []
    for i, message in enumerate(raw_bridge):
        item = {"speaker": message["speaker"]}
        old_anchor = i == 0 and base is not None and base["anchor"] != "start"
        if message["speaker"] == "us" and not old_anchor:
            when = resolve_time_label(message.get("label"), read_at, tz, platform)
            page_at = (datetime.fromisoformat(when["date"] + "T" + when["minute"]).replace(tzinfo=tz).isoformat()
                       if when["date"] and when["minute"] else None)
            # 未匹配引用仅记 null，防止把误填的页面原文作为编号写入证据。
            aid = message.get("attemptId")
            item.update(attemptId=aid if aid in known_attempts else None, type=message["type"],
                        digest=content_digest(message["text"]), pageAt=page_at)
        proof.append(item)
    return proof


def bridge_verified(atts, base, anchor, messages, bridge, proof, complete, run, read_at, scope):
    """只用持久化指纹及发送证据验证连续覆盖；写入和重读共用，不依赖原文。"""
    platform, account, _ = scope
    if thread_scope(base) != scope:
        return False, True, "bridge_target_mismatch"
    if not complete or not bridge:
        return False, True, "incomplete_bridge"
    start = 0 if base["anchor"] == "start" else 1
    if start and (proof[0]["speaker"] != "us" or not fingerprints_equal(bridge[0], base["anchor"])):
        return False, True, "bridge_anchor_mismatch"
    if not all(definite_fingerprint(fp) for fp in bridge):
        return False, True, "bridge_time_ambiguity"
    if start and sum(fingerprints_equal(fp, base["anchor"]) for fp in bridge) != 1:
        return False, True, "bridge_anchor_ambiguity"
    for fp, item in zip(bridge, proof):
        if "id" not in fp and fp["speaker"] != item["speaker"]:
            return False, True, "bridge_role_mismatch"
    us = [i for i in range(start, len(bridge)) if proof[i]["speaker"] == "us"]
    if not us or not fingerprints_equal(bridge[us[-1]], anchor):
        return False, True, "bridge_current_anchor_mismatch"
    if sum(fingerprints_equal(fp, anchor) for fp in bridge) != 1:
        return False, True, "bridge_current_anchor_ambiguity"
    ids = [fp["id"] for fp in bridge if "id" in fp]
    if len(ids) != len(set(ids)):
        return False, True, "bridge_identity_ambiguity"
    before = [bridge[i] for i in range(start, us[0]) if proof[i]["speaker"] == "hr"]
    if not sequence_equal(before, base["fingerprints"]):
        return False, True, "bridge_hr_before_send_changed"
    after = [bridge[i] for i in range(us[-1] + 1, len(bridge)) if proof[i]["speaker"] == "hr"]
    if not sequence_equal(after, messages):
        return False, True, "bridge_tail_mismatch"
    all_hr = [bridge[i] for i in range(start, len(bridge)) if proof[i]["speaker"] == "hr"]
    used = set()
    for i in us:
        message = proof[i]
        attempt_id = message.get("attemptId")
        attempt = atts.get((platform, account, attempt_id))
        if not attempt or attempt_id in used or attempt["runId"] != run or attempt["status"] != "sent":
            return False, True, "bridge_unconfirmed_send"
        used.add(attempt_id)
        sent = next((e for e in reversed(attempt["events"]) if e["status"] == "sent"), None)
        if not sent or thread_scope(sent) != scope or sent["runId"] != run:
            return False, True, "bridge_target_mismatch"
        # 不依赖后续身份冻结功能：bridge 自身拒绝用改绑目标的终态证明连续性。
        for field in ("platform", "account", *load_platform(platform)["conversationKey"]):
            normalize = (lambda value: value) if field in ("platform", "account") else norm_text
            if any(e.get(field) is not None and normalize(e[field]) != normalize(sent.get(field) or "")
                   for e in attempt["events"]):
                return False, True, "bridge_target_mismatch"
        payload = sent.get("resume") if sent["kind"] == "resume" else sent.get("text")
        expected_type = "card" if sent["kind"] == "resume" else "text"
        if (message.get("type") != expected_type or not isinstance(payload, str)
                or content_digest(payload) != message.get("digest")):
            return False, True, "bridge_content_mismatch"
        if message.get("pageAt") is None:
            return False, True, "bridge_time_ambiguity"
        page_at = parse_at(message["pageAt"])
        fp = bridge[i]
        if "id" not in fp and (fp["type"] != message["type"] or fp["digest"] != message["digest"]
                or fp["date"] != page_at.date().isoformat() or fp["minute"] != page_at.strftime("%H:%M")):
            return False, True, "bridge_send_evidence_mismatch"
        sent_at = parse_at(sent["at"])
        if (not parse_at(base["readAt"]) <= sent_at <= read_at
                or not page_at - timedelta(minutes=2) <= sent_at < page_at + timedelta(minutes=3)):
            return False, True, "bridge_send_time_mismatch"
    if not sequence_equal(all_hr, base["fingerprints"]):
        # 新锚点之后仍可见的新 HR 留给下一次处理；跨锚点丢失则只能本人核对。
        hidden = [bridge[i] for i in range(start, us[-1]) if proof[i]["speaker"] == "hr"]
        return False, not sequence_equal(hidden, base["fingerprints"]), "bridge_new_hr"
    return True, False, "bridge_verified"


def tail_outcome(events, ev, state=None):
    """线性处理历史前缀；比较与未决语义不信任事件自报的结果布尔。"""
    scope = thread_scope(ev)
    state = state if state is not None else tail_state(events, scope)
    if state["scope"] != scope:
        raise LedgerError("尾部状态不属于该平台账号会话")
    base = state["tails"].get(ev["baseTailId"])
    if base is not None and thread_scope(base) != scope:
        raise LedgerError("baseTailId 不属于该平台账号会话")
    explicit = ev["baseExplicit"]
    if explicit and base is None:
        raise LedgerError("显式 baseTailId 不属于该会话")
    if not explicit and ev["baseTailId"] != (state["handled"]["tailId"] if state["handled"] else None):
        raise LedgerError("默认 baseTailId 须为该会话最近 handled")
    result, count, reason = compare_tail(base, ev["anchor"], ev["fingerprints"], ev["complete"], ev["anchorUnique"])
    review = state["review"] or result == "mismatch"
    settled = explicit and result == "same"
    # 每次判定只聚合一次尝试；bridge 内逐条引用，不逐消息重扫全表。
    atts = attempts_of(events)
    thread_atts = [a for a in atts.values() if thread_scope(a) == scope]
    if ev["anchor"] == "start" and any(a["status"] == "sent" for a in thread_atts):
        result, count, reason, review, settled = "mismatch", None, "start_conflicts_with_sent", True, False
    if base and result == "new_anchor":
        settled, failed, reason = bridge_verified(atts, base, ev["anchor"], ev["fingerprints"],
            ev.get("bridge", []), ev.get("bridgeEvidence", []), ev.get("bridgeComplete", False),
            ev["runId"], parse_at(ev["readAt"]), scope)
        settled = explicit and settled
        review |= failed
    if state["pending"]:
        if not explicit:
            review, settled, reason = True, False, "pending_base_required"
        elif state["latest"]["tailId"] != base["tailId"] and compare_tail(base,
                state["latest"]["anchor"], state["latest"]["fingerprints"],
                state["latest"]["complete"], state["latest"]["anchorUnique"])[0] != "same":
            # 旧 base 不能跳过已观察的待处理内容；中间等同读取则不扩大覆盖范围。
            review, settled, reason = True, False, "pending_base_not_covering_latest"
    if any(a["status"] in ("pending", "uncertain") for a in thread_atts):
        review, settled, reason = True, False, "unresolved_send"
    return {"result": result, "newCount": None if review else count, "reason": reason,
            "settled": bool(settled and not review), "reviewRequired": bool(review)}


def cmd_tail(args, ws):
    try:
        body = json.loads(args.json if args.json is not None else sys.stdin.read())
    except ValueError as error:
        raise LedgerError("尾部输入不是有效 JSON") from error
    if not isinstance(body, dict) or set(body) - {"anchor", "messages", "complete", "anchorUnique", "bridge", "bridgeComplete"}:
        raise LedgerError("尾部输入结构或字段无效")
    for key in ("complete", "anchorUnique", "bridgeComplete"):
        if key in body and type(body[key]) is not bool:
            raise LedgerError(f"{key} 须为布尔值")
    if not isinstance(body.get("messages"), list) or ("bridge" in body and not isinstance(body["bridge"], list)):
        raise LedgerError("messages / bridge 须为数组")
    tz = tz_of(load_profile(ws))
    read_at = parse_at(args.read_at)
    if read_at > fsutil.now(tz):
        raise LedgerError("readAt 不能晚于当前时间")
    with lock_mutex(ws):
        lock = require_live_lock(ws, args.run, tz, allow_record=True)
        events, _ = read_events(ws)
        identities = [e for e in events if e.get("threadKey") == args.thread]
        if not identities:
            raise LedgerError("threadKey 未登记，请先记录该会话的页面身份")
        if len({(e["platform"], e["account"]) for e in identities}) != 1:
            raise LedgerError("threadKey 对应多个平台/账号，存在歧义；须本人核对，不能自动读取")
        source = identities[-1]
        platform = source["platform"]
        anchor_raw = body.get("anchor")
        anchor = "start" if anchor_raw == "start" else message_fingerprint(anchor_raw, "us", read_at, tz, platform)
        messages = [message_fingerprint(message, "hr", read_at, tz, platform) for message in body["messages"]]
        raw_bridge = body.get("bridge", [])
        bridge = []
        for message in raw_bridge:
            if not isinstance(message, dict) or message.get("speaker") not in ("us", "hr"):
                raise LedgerError("bridge 须明确每条消息的 speaker")
            bridge.append(message_fingerprint(message, message["speaker"], read_at, tz, platform))
        if body.get("bridgeComplete") and "bridge" not in body:
            raise LedgerError("bridgeComplete 缺少 bridge")
        state = tail_state(events, thread_scope(source))
        if state["latest"] and read_at < parse_at(state["latest"]["readAt"]):
            raise LedgerError("readAt 不能早于该会话最新读取")
        base = state["tails"].get(args.base) if args.base else state["handled"]
        if args.base and base is None:
            raise LedgerError("baseTailId 不属于该会话")
        if base and read_at < parse_at(base["readAt"]):
            raise LedgerError("readAt 不能早于基准读取时间")
        complete, unique = body.get("complete", False), body.get("anchorUnique", False)
        ev = {key: source.get(key) for key in ("platform", "account", "company", "title", "location", "recruiter", "jobId", "targetRole")}
        ev.update(schemaVersion=SCHEMA_VERSION, runId=args.run, skill=lock["skill"], kind="observe", observed="tail",
                  at=fsutil.now(tz).isoformat(), attemptId="att-" + uuid.uuid4().hex[:12], evidence=None,
                  tailId="tail-" + uuid.uuid4().hex[:12], baseTailId=base["tailId"] if base else None,
                  baseExplicit=bool(args.base), anchor=anchor, fingerprints=messages, readAt=args.read_at,
                  complete=complete, anchorUnique=unique)
        if "bridge" in body:
            ev.update(bridge=bridge, bridgeComplete=body.get("bridgeComplete", False),
                      bridgeEvidence=bridge_evidence(raw_bridge, base, read_at, tz, platform,
                          {e["attemptId"] for e in events if e["kind"] in ACTION_KINDS
                           and e["platform"] == platform and e["account"] == source["account"]}))
        ev = prepare_event(ev)
        ev.update(tail_outcome(events, ev, state))
        errors = validate_event(ev)
        if errors:
            raise LedgerError("尾部事件无效：" + "；".join(errors))
        validate_history_event(events, ev)
        append_line(ws, ev)
        return emit({key: ev[key] for key in ("tailId", "baseTailId", "result", "newCount", "reason", "settled")})


# ---------- 命令：quota ----------

def cmd_quota(args, ws):
    profile = load_profile(ws)
    args.account = resolve_account(ws, args.platform, args.account)
    limits = effective_limits(profile, args.platform)
    tz = tz_of(profile)
    events, _ = read_events(ws)
    today = fsutil.now(tz).date()
    # 额度按「是否写过 greet 的 pending」计：失败或结果不明也不退回
    greets = [a for a in attempts_of(events).values()
              if a["kind"] == "greet" and a["platform"] == args.platform and a["account"] == args.account and a["everPending"]]
    today_greets = [a for a in greets if parse_at(a["firstAt"]).astimezone(tz).date() == today]
    run_greets = [a for a in greets if args.run and a["runId"] == args.run]
    # 间隔按最近一次外发活动（写 pending 或确认 sent）算，取较晚者，保守
    activity = [parse_at(e["at"]) for e in events
                if e["kind"] in ACTION_KINDS and e["platform"] == args.platform and e["account"] == args.account
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
        "canGreet": can_greet, "platform": args.platform, "account": args.account,
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
    account = account_filter(ws, args, events)
    threads = build_threads(events, args.platform, profile, account)
    for t in threads.values():
        t["canFollowup"], t["followupReason"] = followup_eligibility(t, now, tz)
    items = sorted(threads.values(), key=lambda t: t["lastAt"] or "", reverse=True)
    if args.company:
        items = [t for t in items if match_company(args.company, [t["company"]])[0]]
    if args.recruiter:
        items = [t for t in items if norm_text(t["recruiter"]) == norm_text(args.recruiter)]
    return emit({"threads": items, "pendingTails": [t for t in items if t["pendingTail"] is not None]})


# ---------- 命令：stats ----------

def cmd_stats(args, ws):
    profile = load_profile(ws)
    tz = tz_of(profile)
    events, _ = read_events(ws)

    account = account_filter(ws, args, events)

    def in_range(at):
        d = parse_at(at).astimezone(tz).date()
        if args.since and d < datetime.fromisoformat(args.since).date():
            return False
        if args.until and d > datetime.fromisoformat(args.until).date():
            return False
        return True

    actions, observed = {}, {}
    for a in attempts_of(events).values():
        if not in_range(a["firstAt"]) or (args.platform and a["platform"] != args.platform) or (account is not None and a["account"] != account):
            continue
        key = f"{a['platform']}/{a['account']}/{a['kind']}"
        actions.setdefault(key, {}).setdefault(a["status"], 0)
        actions[key][a["status"]] += 1
    for ev in events:
        if ev["kind"] != "observe" or not in_range(ev["at"]):
            continue
        if (args.platform and ev["platform"] != args.platform) or (account is not None and ev["account"] != account):
            continue
        key = f"{ev['platform']}/{ev['account']}/{ev['observed']}"
        observed[key] = observed.get(key, 0) + 1
    return emit({"since": args.since, "until": args.until, "actions": actions, "observed": observed})


# ---------- 只读导出 ----------

EXPORT_COLUMNS = {
    "events": tuple(REQUIRED) + ("status", "observed", "jobId", "jobUrl", "text", "resume", "decision", "step",
        "decisionVersion", "version", "steps", "tags", "jdFacts", "checks", "facts", "asks", "result", "reasons",
        "salaryText", "profileRev", "styleRev", "hrAt", "readAt", "tailId", "baseTailId", "baseExplicit", "anchor",
        "fingerprints", "complete", "anchorUnique", "settled", "reviewRequired", "bridge", "bridgeComplete",
        "bridgeEvidence", "listMark", "acknowledged", "extra"),
    "threads": ("platform", "account", "threadKey", "company", "recruiter", "title", "jobId", "currentJobKey", "stage",
        "lastAt", "lastOutbound", "lastObserved", "lastReplyAt", "info", "interviewIntentStages", "pendingTail",
        "lastHandled", "lastOpenedAt", "unresolved", "manual", "pausedBy", "closed", "closedBy", "followupsSent",
        "followupsSinceReply", "lastFollowupAt", "readAt", "boundaryAt", "acknowledged", "outboundTexts",
        "decisions", "canFollowup", "followupReason", "extra"),
    "jobs": ("platform", "account", "jobKey", "company", "title", "jobId", "lastAt", "screenEventId", "screenAt",
        "result", "reasons", "jdFacts", "checks", "salaryText", "hasContactAttempt", "contacted", "unresolved", "extra"),
}


def export_jobs(events):
    jobs = {}
    for ev in events:
        if not ev.get("jobKey"):
            continue
        key = ev["platform"], ev["account"], ev["jobKey"]
        job = jobs.setdefault(key, {"platform": key[0], "account": key[1], "jobKey": key[2],
            "screenEventId": None, "screenAt": None, "result": None, "reasons": [], "jdFacts": {}, "checks": [],
            "salaryText": None, "hasContactAttempt": False, "contacted": False, "unresolved": []})
        for field in ("company", "title", "jobId"):
            if ev.get(field) is not None:
                job[field] = ev[field]
        job["lastAt"] = ev["at"]
        if ev["kind"] == "screen":
            job.update(screenEventId=ev["eventId"], screenAt=ev["at"], result=ev["result"], reasons=ev["reasons"],
                       jdFacts=ev.get("jdFacts", {}), checks=ev.get("checks", []), salaryText=ev.get("salaryText"))
        if ev["kind"] == "observe" and ev["observed"] == "adopted":
            job["contacted"] = True
    for attempt in attempts_of(events).values():
        first_job = next((e["jobKey"] for e in attempt["events"] if e.get("jobKey")), None)
        original = jobs.get((attempt["platform"], attempt["account"], first_job))
        if original is not None:
            original["hasContactAttempt"] |= attempt["everPending"]
            if attempt["status"] in ("pending", "uncertain"):
                original["unresolved"].append({k: attempt[k] for k in ("platform", "account", "attemptId", "kind", "status")})
        sent, actual_job, _ = sent_metadata(attempt)
        if sent or attempt["status"] == "already_contacted":
            target = jobs.get((attempt["platform"], attempt["account"], actual_job or first_job))
            if target is not None:
                target["contacted"] = True
    return list(jobs.values())


def export_content(rows, columns, format):
    if format == "jsonl":
        return "".join(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n" for row in rows)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        values = []
        for field in columns:
            value = ({key: value for key, value in row.items() if key not in columns or key == "extra"} if field == "extra" else row.get(field))
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            if isinstance(value, str):
                # 只检测规范化探针，保留原值；CSV 展示层防公式，JSONL 不改业务原文。
                probe = unicodedata.normalize("NFKC", value)
                first = next((ch for ch in probe if not (ch.isspace() or unicodedata.category(ch) in ("Cc", "Cf"))), "")
                if first in ("=", "+", "-", "@"):
                    value = "'" + value
            values.append(value)
        writer.writerow(values)
    return "\ufeff" + output.getvalue()


def export_destination(ws, value):
    requested = Path(value).expanduser().absolute()
    if ":" in requested.name:
        raise LedgerError("导出文件名不能含冒号或备用数据流")
    if requested.exists() or requested.is_symlink():
        raise LedgerError("导出只创建外部新文件，不覆盖既有目标或符号链接")
    # 固定已存在父目录的真实路径，系统临时目录的别名也按实际位置判断。
    parent = requested.parent.resolve(strict=True)
    if not parent.is_dir():
        raise LedgerError("导出父目录须已存在")
    target = parent / requested.name
    suite = Path(__file__).resolve().parent.parent
    if target.is_relative_to(ws.resolve()) or target.is_relative_to(suite):
        raise LedgerError("导出目标必须在工作区和套件目录之外")
    for ancestor in (parent, *parent.parents):
        marker = ancestor / MARKER
        if marker.exists() or marker.is_symlink() or ((ancestor / "scripts/ledger.py").is_file()
                and ((ancestor / "skills").is_dir() or (ancestor / "SKILL.md").is_file())):
            raise LedgerError("导出目标位于其他求职工作区或套件目录内")
    return target


def cmd_export(args, ws):
    profile = load_profile(ws)
    if not isinstance(profile, dict):
        raise LedgerError("profile 须为对象")
    tz = tz_of(profile)
    bounds = []
    for value in (args.since, args.until):
        parsed = date.fromisoformat(value) if value is not None else None
        if parsed is not None and parsed.isoformat() != value:
            raise LedgerError("日期须为 YYYY-MM-DD")
        bounds.append(parsed)
    since, until = bounds
    if since and until and since > until:
        raise LedgerError("since 不得晚于 until")
    events, _ = read_events(ws)
    account = account_filter(ws, args, events)
    if args.table == "events":
        rows = [public_entry(e) for e in events]
    elif args.table == "threads":
        rows = list(build_threads(events, profile=profile).values())
        now = fsutil.now(tz)
        for row in rows:
            row["canFollowup"], row["followupReason"] = followup_eligibility(row, now, tz)
    else:
        rows = export_jobs(events)
    selected = []
    for row in rows:
        if (args.platform and row["platform"] != args.platform) or (account is not None and row["account"] != account):
            continue
        at = parse_at(row["at"] if args.table == "events" else row["lastAt"]).astimezone(tz).date()
        if (since is None or at >= since) and (until is None or at <= until):
            selected.append(row)
    columns = EXPORT_COLUMNS[args.table]
    content = export_content(selected, columns, args.format)
    result = {"table": args.table, "format": args.format, "columns": list(columns), "count": len(selected)}
    if args.out is not None:
        target = export_destination(ws, args.out)
        fsutil.publish_text_new(target, content)
        result["out"] = str(target)
    else:
        result["content"] = content
    return emit(result)


# ---------- 运行锁 ----------
# lock.json 记录持锁者；所有读取、接管、续期、释放都在 .lock.mutex 的独占锁内完成。

def lock_path(ws):
    return ws / "ledger" / "lock.json"


def lock_mutex(ws, write=True):
    return operation_mutex(ws, ".lock.mutex", write=write)


def read_lock(ws):
    return read_json(lock_path(ws))


def write_lock(ws, lock):
    write_atomic(lock_path(ws), json.dumps(lock, ensure_ascii=False, indent=2) + "\n")


def lock_expired(lock, now):
    return parse_at(lock["expiresAt"]) <= now


def require_live_lock(ws, run_id, tz, renew=False, allow_record=False):
    """调用方须已持有 lock_mutex。run_id 必须是当前未过期的持锁者，且本轮未禁发。"""
    version_gate(ws, "write")
    now = fsutil.now(tz)
    lock = read_lock(ws)
    if not lock or lock.get("runId") != run_id:
        holder = lock.get("runId") if lock else "无"
        raise LedgerError(f"runId {run_id} 未持有运行锁（当前持锁：{holder}），禁止新的外发")
    if lock.get("releasing"):
        raise LedgerError(f"runId {run_id} 正在释放运行锁，禁止新的外发；请完成 lock release 收尾")
    if lock_expired(lock, now):
        raise LedgerError(f"runId {run_id} 的运行锁已过期，禁止新的外发；请 lock renew 或重新获取")
    if lock.get("noSend") and not allow_record:
        raise LedgerError(f"runId {run_id} 本轮为禁发模式（--no-send），不能写入新的外发")
    pause = read_pause(ws)
    if pause and not allow_record:
        raise LedgerError(f"已全局暂停（{pause.get('at')}，{pause.get('reason')}），禁止新的外发；"
                          "本人回「恢复」或 ledger pause off 之后才能继续")
    if renew:
        lock["expiresAt"] = (now + timedelta(minutes=lock.get("ttlMinutes", DEFAULT_LOCK_TTL_MINUTES))) \
            .isoformat(timespec="seconds")
        write_lock(ws, lock)
    return lock


def lock_object(raw):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise LedgerError(f"授权结构含重复字段：{key}")
            out[key] = value
        return out
    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise LedgerError("限制或例外须为 JSON 对象")
    return value


def validate_scope(scope):
    if not isinstance(scope, dict) or len(scope) != 1:
        raise LedgerError("scope 须且只能指定 all / threads / companies / jobs 之一")
    key, values = next(iter(scope.items()))
    if key == "all" and values is True:
        return
    if key not in ("threads", "companies", "jobs") or not isinstance(values, list) or not values or any(
            not isinstance(v, str) or not v.strip() for v in values):
        raise LedgerError("scope 列表须为非空字符串数组，all 只能为 true")


def validate_run_rule(rule, restriction=False):
    fields = {"kinds", "scope", "max", "quote"} if restriction else {"item", "scope", "quote"}
    if not restriction and rule.get("item") != "hours":
        fields.add("max")
    if set(rule) != fields:
        raise LedgerError("限制或例外字段缺失/多余；编号、绑定与用量只能由脚本生成")
    validate_scope(rule["scope"])
    if not isinstance(rule["quote"], str) or not rule["quote"].strip():
        raise LedgerError("quote 须为本人非空原话")
    if restriction:
        kinds = rule["kinds"]
        if (not isinstance(kinds, list) or not kinds or any(not isinstance(k, str) or k not in ACTION_KINDS for k in kinds)
                or len(set(kinds)) != len(kinds)):
            raise LedgerError("restriction.kinds 须为五种外发动作的非空不重复子集")
    elif rule["item"] not in ("apply_send", "reply", "nudge", "hours", "dedup_once"):
        raise LedgerError("grant.item 无效")
    if not restriction and rule["item"] == "dedup_once" and (
            type(rule["max"]) is not int or rule["max"] != 1 or set(rule["scope"]) != {"jobs"}
            or len(rule["scope"]["jobs"]) != 1):
        raise LedgerError("dedup_once 只能绑定单个职位且 max 必须是整数1")
    if "max" in rule and rule["max"] != "unlimited" and (
            type(rule["max"]) is not int or rule["max"] < (0 if restriction else 1)):
        raise LedgerError("max 须为合法整数或 unlimited，布尔不算整数")


def scope_identity(ev, axis):
    """分量分别比较，账号与平台及职位ID均不做 Unicode 合并。"""
    if axis == "threads":
        identity = {field: (norm_company if field == "company" else norm_text)(ev.get(field))
                    for field in load_platform(ev["platform"])["conversationKey"]}
    elif ev.get("jobId"):
        identity = {"jobId": ev["jobId"]}
    else:
        identity = {"company": norm_company(ev.get("company")), "title": norm_text(ev.get("title"))}
    if not all(identity.values()):
        raise LedgerError("具体目标身份不足，请先核对页面并登记")
    return {"platform": ev["platform"], "account": ev["account"], "identity": identity}


def bind_scope(scope, events):
    axis = next(iter(scope))
    if axis not in ("threads", "jobs"):
        return []
    field = "threadKey" if axis == "threads" else "jobKey"
    bindings = []
    for key in dict.fromkeys(scope[axis]):
        matches = {}
        for event in events:
            if event.get(field) == key:
                identity = scope_identity(event, axis)
                matches[json.dumps(identity, ensure_ascii=False, sort_keys=True)] = identity
        if len(matches) != 1:
            raise LedgerError(f"作用域键未登记或存在多个原始目标/账号：{key}；请先禁发登记身份或本人核对")
        bindings.append(dict(key=key, **next(iter(matches.values()))))
    return bindings


def scope_covers(rule, bindings, ev):
    axis = next(iter(rule["scope"]))
    values = rule["scope"][axis]
    if axis == "all":
        return True
    if axis == "companies":
        return norm_company(ev.get("company")) in {norm_company(v) for v in values}
    field = "threadKey" if axis == "threads" else "jobKey"
    try:
        target = dict(key=ev.get(field), **scope_identity(ev, axis))
    except LedgerError:
        return False
    return target["key"] in values and target in bindings


def pending_uses(events, run, kinds=None, authorized_by=None, dedup_grant=None):
    return sum(1 for a in attempts_of(events).values() if a["runId"] == run and a["everPending"]
               and (kinds is None or a["kind"] in kinds)
               and (authorized_by is None or next(e for e in a["events"] if e["status"] == "pending").get("authorizedBy") == authorized_by)
               and (dedup_grant is None or next(e for e in a["events"] if e["status"] == "pending").get("dedupGrant") == dedup_grant))


def run_policy_rev(lock):
    frozen = {key: lock.get(key) for key in ("skill", "restriction", "grants", "scopeBindings", "noSend")}
    return hashlib.sha256(json.dumps(frozen, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def lock_view(lock, events):
    out = dict(lock)
    restriction = lock.get("restriction")
    out["restriction"] = None
    if restriction is not None:
        used = pending_uses(events, lock["runId"], restriction["kinds"])
        maximum = restriction["max"]
        out["restriction"] = dict(restriction, used=used, remaining=None if maximum == "unlimited" else max(0, maximum - used))
    out["grants"] = []
    for grant in lock.get("grants", []):
        used = pending_uses(events, lock["runId"], **({"dedup_grant": grant["id"]} if grant["item"] == "dedup_once"
                                                   else {"authorized_by": "grant:" + grant["id"]}))
        maximum = grant.get("max", "unlimited")
        out["grants"].append(dict(grant, used=used, remaining=None if maximum == "unlimited" else max(0, maximum - used)))
    return out


def acquire_lock(ws, tz, skill, run=None, ttl=DEFAULT_LOCK_TTL_MINUTES, no_send=False, recovery=False,
                 restriction=None, grants=None):
    """调用方须已持有 lock_mutex。返回 (退出码, 结果)。同一 runId 重复获取视为沿用。"""
    if recovery and skill != "setup":
        raise LedgerError("恢复锁只能由 setup 获取")
    version_gate(ws, "recover" if recovery else "write")
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
            if current.get("skill") != skill or (current.get("mode") == "recovery") != bool(recovery):
                raise LedgerError("不能改变已有运行锁的技能或恢复模式")
            original_grants = [{k: v for k, v in g.items() if k != "id"} for g in current.get("grants", [])]
            if ((restriction is not None and restriction != current.get("restriction"))
                    or (grants is not None and grants != original_grants)
                    or (no_send and not current.get("noSend"))):
                raise LedgerError("本轮限制、例外与禁发模式不可中途改写")
            if not recovery:
                upgrade_suite_version(ws)
            return 0, {"acquired": True, "reused": True, "lock": current, "tookOverFrom": None, "reconciled": []}
        if not lock_expired(current, now):
            return 1, {"acquired": False, "reason": "已有运行中的任务", "lock": current}
        if run and current.get("runId") == run:
            raise LedgerError("原运行锁已过期；请续锁保留本轮限制，不以同轮重新取锁改绑")
    rules = {"restriction": restriction, **{f"g{i}": grant for i, grant in enumerate(grants or [], 1)}}
    concrete = any(rule and next(iter(rule["scope"])) in ("threads", "jobs") for rule in rules.values())
    events = read_events(ws)[0] if concrete or skill in ("apply", "followup") else []
    bindings = {name: bind_scope(rule["scope"], events) for name, rule in rules.items() if rule is not None}
    waived = set()
    for name, rule in rules.items():
        if rule and rule.get("item") == "dedup_once":
            target = json.dumps({k: v for k, v in bindings[name][0].items() if k != "key"}, sort_keys=True)
            if target in waived:
                raise LedgerError("同轮同一原始目标不能重复登记 dedup_once")
            waived.add(target)
    if current:
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
        "restriction": restriction,
        "grants": [dict(grant, id=f"g{i}") for i, grant in enumerate(grants or [], 1)],
        "scopeBindings": bindings,
    }
    lock["policyRev"] = run_policy_rev(lock)
    if skill in ("apply", "followup") and any(e.get("status") == "pending" and e["runId"] == run_id
            and e.get("runPolicyRev") not in (None, lock["policyRev"]) for e in events):
        raise LedgerError("同一 runId 已有发送用量，不能换锁改限制、例外编号或原始绑定")
    if recovery:
        lock["mode"] = "recovery"
    write_lock(ws, lock)
    if not recovery:
        upgrade_suite_version(ws)
    return 0, {"acquired": True, "reused": False, "lock": lock, "tookOverFrom": took_over, "reconciled": reconciled}


def release_lock(ws, run, tz=None):
    """调用方须已持有 lock_mutex。先持久化收尾意图，核销 pending 后才删除锁。"""
    version_gate(ws, "recover")
    current = read_lock(ws)
    if not current:
        return 0, {"released": False, "reason": "没有锁", "reconciled": []}
    if current.get("runId") != run:
        return 1, {"released": False, "reason": "锁不属于该 runId", "lock": current}
    tz = tz or recovery_tz(ws)
    events, _ = read_events(ws)
    # 收尾中断时保留候选 ID：重试 / 过期接管仍能报告已追加的 uncertain。
    ids = release_keys(current)
    for a in attempts_of(events).values():
        if a["runId"] == run and a["status"] == "pending" and attempt_key(a) not in ids:
            ids.append(attempt_key(a))
    current.update(releasing=True, releaseAttempts=ids)
    try:
        write_lock(ws, current)
        reconciled = reconcile_run(ws, current, tz)
        lock_path(ws).unlink()
    except OSError as e:
        raise LedgerError(f"释放运行锁收尾失败，停止外发并重试 lock release；锁未确认释放：{e}") from e
    return 0, {"released": True, "reconciled": reconciled}


def cmd_lock(args, ws):
    tz = recovery_tz(ws)
    with lock_mutex(ws, write=args.action != "status"):
        now = fsutil.now(tz)
        current = read_lock(ws)

        if args.action == "status":
            if not current:
                return emit({"held": False})
            expired = lock_expired(current, now)
            events = read_events(ws)[0] if current.get("restriction") or current.get("grants") else []
            return emit({"held": not expired, "expired": expired, "lock": lock_view(current, events)})

        if args.action == "release":
            code, out = release_lock(ws, args.run, tz)
            return emit(out, code)

        if args.action == "renew":
            version_gate(ws, "write")
            if not current or current.get("runId") != args.run:
                return emit({"renewed": False, "reason": "已失去运行锁，立即停止外发", "lock": current}, 1)
            if current.get("releasing"):
                return emit({"renewed": False, "reason": "运行锁正在释放，请重试 lock release 完成收尾", "lock": current}, 1)
            current["expiresAt"] = (now + timedelta(minutes=current.get("ttlMinutes", args.ttl))) \
                .isoformat(timespec="seconds")
            write_lock(ws, current)
            return emit({"renewed": True, "lock": current})

        if args.restrict and len(args.restrict) != 1:
            raise LedgerError("--restrict 只接受一次，不合并重复规则")
        restriction = lock_object(args.restrict[0]) if args.restrict else None
        grants = [lock_object(raw) for raw in args.grant] if args.grant else None
        if restriction is not None:
            validate_run_rule(restriction, restriction=True)
        for grant in grants or []:
            validate_run_rule(grant)
        if grants and args.no_send:
            raise LedgerError("--grant 不能与 --no-send 共存")
        code, out = acquire_lock(ws, tz, args.skill, args.run, args.ttl, args.no_send, args.recovery, restriction, grants)
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
        version_gate(ws, "write" if paused else "recover")
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
    tz = recovery_tz(ws) if args.action == "off" else tz_of(load_profile(ws))
    if args.action == "status":
        return emit({"paused": read_pause(ws)})
    if args.action == "on":
        if not args.reason:
            raise LedgerError("pause on 需要 --reason")
        rec, previous = set_pause(ws, True, args.by, args.reason, tz)
    else:
        rec, previous = set_pause(ws, False, args.by, args.reason or "恢复", tz)
    return emit({"pause": rec, "previous": previous})


def release_keys(lock):
    values = lock.get("releaseAttempts", [])
    if not isinstance(values, list) or any(not isinstance(x, (list, tuple)) or len(x) != 3
            or any(not isinstance(part, str) or not part for part in x) for x in values):
        raise LedgerError("releaseAttempts 命名空间标识损坏")
    return [tuple(x) for x in values]


def reconcile_run(ws, old_lock, tz):
    """释放 / 过期接管：该轮没有终态的 pending 追加 uncertain。调用方须已持有 lock_mutex。"""
    events, _ = read_events(ws)
    out = []
    release_ids = set(release_keys(old_lock))
    cause = "正在释放" if old_lock.get("releasing") else "已过期"
    for a in attempts_of(events).values():
        if a["runId"] != old_lock.get("runId"):
            continue
        if a["status"] == "pending":
            last = a["events"][-1]
            ev = {k: v for k, v in last.items() if not k.startswith("_")}
            ev.update({
                "at": fsutil.now(tz).isoformat(),
                "status": "uncertain",
                "reason": f"本轮（{old_lock.get('runId')}）锁{cause}，结果未确认，需人工核对",
                "evidence": None,
                "eventId": "evt-" + uuid.uuid4().hex[:12],
            })
            validate_history_event(events, ev)
            append_line(ws, ev)
            events.append(ev)
        elif a["status"] != "uncertain" or attempt_key(a) not in release_ids:
            continue
        out.append({k: a[k] for k in ("platform", "account", "attemptId", "company", "title", "kind")})
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
    prepare = sub.add_parser("prepare", help="原子检查全部门禁并写入唯一 pending")
    prepare.add_argument("--run", required=True)
    prepare.add_argument("--json")
    export = sub.add_parser("export", help="只读导出；文件仅写工作区外新路径")
    export.add_argument("--table", choices=tuple(EXPORT_COLUMNS), required=True)
    export.add_argument("--format", choices=("csv", "jsonl"), required=True)
    export.add_argument("--since")
    export.add_argument("--until")
    export.add_argument("--out")
    export.add_argument("--platform")
    export.add_argument("--account")
    tail = sub.add_parser("tail", help="读取会话尾部并记录指纹，不保存原文")
    tail.add_argument("--run", required=True)
    tail.add_argument("--thread", required=True)
    tail.add_argument("--read-at", required=True)
    tail.add_argument("--base")
    tail.add_argument("--json")

    c = sub.add_parser("check", help="外发前检查")
    c.add_argument("--platform")
    c.add_argument("--company")
    c.add_argument("--json")
    c.add_argument("--text")
    c.add_argument("--resume")
    c.add_argument("--step")
    c.add_argument("--target-role")
    c.add_argument("--reason")
    c.add_argument("--account")
    c.add_argument("--kind", choices=sorted(ACTION_KINDS), default="greet",
                   help="greet：首次联系去重；其余：会话内外发检查")
    c.add_argument("--recruiter", help="会话内外发时的招聘者「姓名(职务)」")
    c.add_argument("--decision", help="执行本人已决定事项时的决策 ID")
    c.add_argument("--title")
    c.add_argument("--job-id")
    c.add_argument("--job-url")
    c.add_argument("--run", help="本轮 runId；同轮 failed_no_send 不重试")
    c.add_argument("--scope", choices=["company", "job"], help="只可与现有 dedupScope 一致，不能借预检覆盖发送门禁")

    d = sub.add_parser("decision", help="查询某个已决定事项的执行状态")
    d.add_argument("--id", required=True)
    d.add_argument("--step", help="只看某个外发步骤的状态")

    g = sub.add_parser("guard", help="可能外发的 UI 操作前：确认仍持锁、尝试仍待定，并续期")
    g.add_argument("--run", required=True)
    g.add_argument("--attempt", required=True)
    g.add_argument("--platform")
    g.add_argument("--account")

    a = sub.add_parser("append", help="原子追加一条事件（JSON 从 --json 或 stdin 读入）")
    a.add_argument("--json")

    q = sub.add_parser("quota", help="剩余招呼额度")
    q.add_argument("--platform", required=True)
    q.add_argument("--account")
    q.add_argument("--run")

    t = sub.add_parser("threads", help="按会话汇总最新状态")
    t.add_argument("--platform")
    t.add_argument("--account")
    t.add_argument("--company")
    t.add_argument("--recruiter")

    s = sub.add_parser("stats", help="统计")
    s.add_argument("--platform")
    s.add_argument("--account")
    s.add_argument("--since", help="YYYY-MM-DD")
    s.add_argument("--until", help="YYYY-MM-DD")

    lk = sub.add_parser("lock", help="运行锁")
    lk.add_argument("action", choices=["acquire", "renew", "release", "status"])
    lk.add_argument("--skill", choices=sorted(SKILLS - {"user"}))
    lk.add_argument("--run")
    lk.add_argument("--ttl", type=int, default=DEFAULT_LOCK_TTL_MINUTES, help="分钟")
    lk.add_argument("--no-send", action="store_true", help="本轮禁发：持锁期间拒绝写入新的外发")
    lk.add_argument("--recovery", action="store_true", help="仅 setup 获取恢复锁，只允许恢复类写入")
    lk.add_argument("--restrict", action="append", help="单一本轮动作、目标和总次数限制 JSON")
    lk.add_argument("--grant", action="append", help="可重复的本轮追加授权 JSON")

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
    "append": cmd_append, "prepare": cmd_prepare, "tail": cmd_tail, "export": cmd_export,
    "quota": cmd_quota, "threads": cmd_threads, "stats": cmd_stats, "lock": cmd_lock,
    "pause": cmd_pause, "stoplist": cmd_stoplist,
}


def entry_mode(args):
    if args.cmd == "lock":
        return {"status": "read", "release": "recover", "renew": "write",
                "acquire": "recover" if args.recovery else "write"}[args.action]
    if args.cmd == "pause":
        return {"status": "read", "on": "write", "off": "recover"}[args.action]
    if args.cmd == "stoplist":
        return "read" if args.action == "list" else "write"
    return "write" if args.cmd in {"append", "prepare", "guard", "tail"} else "read"


def main(argv=None):
    fsutil.utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "lock":
            if args.action != "acquire" and (args.restrict or args.grant):
                raise LedgerError("--restrict / --grant 只能在取锁时登记，不能中途修改")
            if args.recovery and (args.action != "acquire" or args.skill != "setup"):
                raise LedgerError("--recovery 仅用于 lock acquire --skill setup")
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
        with gated(ws, entry_mode(args)):
            return COMMANDS[args.cmd](args, ws)
    except (LedgerError, OSError, ValueError) as e:
        return emit(error_result(e), 2)


if __name__ == "__main__":
    sys.exit(main())

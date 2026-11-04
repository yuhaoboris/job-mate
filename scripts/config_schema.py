"""配置事务的封闭字段校验；只校验传入内容，不读取当前损坏配置。"""
import copy
import json
import math
import re
from pathlib import PurePosixPath, PureWindowsPath

import fsutil
import ledger
import notify

CONFIG_FILES = ("job-mate.json", "profile.json", "stoplist.yaml", "candidate.md", "qa.yaml", "rules.md", "resumes/index.yaml")


def require(ok, message):
    if not ok:
        raise ledger.LedgerError("配置校验失败：" + message)


def object_fields(value, allowed, required=()):
    require(isinstance(value, dict), "须为对象")
    require(not set(value) - set(allowed), "含当前脚本不认识的字段；请核对或更新 job-mate")
    require(set(required) <= set(value), "缺少必填字段：" + ",".join(sorted(set(required) - set(value))))
    return value


def json_value(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "JSON 重复字段 " + key)
            result[key] = value
        return result
    def constant(value):
        raise ValueError("非法 JSON 常量 " + value)
    def finite(value):
        result = float(value)
        require(math.isfinite(result), "JSON 非有限数值")
        return result
    return json.loads(raw, object_pairs_hook=unique, parse_constant=constant, parse_float=finite)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def strings(value):
    return isinstance(value, list) and all(text(v) for v in value)


def number(value):
    return (type(value) is int and value >= 0) or (type(value) is float and math.isfinite(value) and value >= 0)


def relative(value):
    require(text(value) and "\\" not in value and ":" not in value and not PureWindowsPath(value).drive,
            "路径须为无盘符的相对路径")
    parts = value.split("/")
    require(not PurePosixPath(value).is_absolute() and all(p not in ("", ".", "..") for p in parts), "路径含空分量或越界")
    return parts


def marker(p):
    object_fields(p, {"schemaVersion", "suiteVersion", "createdAt", "accounts"}, {"schemaVersion", "suiteVersion", "accounts"})
    require(type(p["schemaVersion"]) is int and p["schemaVersion"] == 1, "不支持的标记格式")
    require(ledger.suite_version(p["suiteVersion"])[0] == ledger.suite_version(ledger.SUITE_VERSION)[0], "备份标记主版本不同")
    require(isinstance(p["accounts"], dict), "accounts 须为对象")
    for platform, account in p["accounts"].items():
        ledger.load_platform(platform)
        object_fields(account, {"accountKey", "displayName"}, {"accountKey", "displayName"})
        require(text(account["accountKey"]) and text(account["displayName"]), "账号分量为空")
    if "createdAt" in p:
        require(text(p["createdAt"]), "createdAt 须为时间字符串")
        ledger.parse_at(p["createdAt"])


def default_profile():
    """只含政策缺省，不含职位、个人事实或招呼语正文；每次返回独立副本。"""
    return {"hardRequirements": [],
            "exclusions": {k: [] for k in ("roleTypes", "employmentTypes", "workSchedules", "keywords")},
            "recruiterActivity": {"maxAgeDays": 7}, "browser": {"headedInput": False},
            "style": {"tone": "polite", "length": "short", "address": "neutral",
                      "bannedWords": ["精通"], "emoji": False},
            "inquiry": {"priority": list(ledger.DEFAULT_INQUIRY_PRIORITY), "maxPerMessage": 2},
            "greeting": {"mode": "dynamic"},
            "authorization": {"apply": {"send": False}, "followup": {"reply": "L2", "nudge": True}},
            "limits": dict(ledger.DEFAULT_LIMITS, platforms={}), "nudge": dict(ledger.DEFAULT_NUDGE, platforms={}),
            "im": {"channel": "none"},
            "schedule": {"timezone": ledger.DEFAULT_TZ,
                         "activeHours": {"weekdays": [1, 2, 3, 4, 5], "start": "09:00", "end": "18:00"}}}


def profile_issues(p, partial=False):
    """体检、事务和运行时共用字段规则；partial 只允许省略必填项，不放宽显式值。"""
    issues = []

    def check(field, fn):
        try:
            return fn()
        except (ledger.LedgerError, notify.NotifyError, ValueError) as error:
            issues.append({"file": "profile.json", "field": field, "problem": str(error)})
            return None

    def need(field, ok, message="取值无效"):
        check(field, lambda: require(ok, field + "：" + message))

    if not isinstance(p, dict):
        need("profile", False, "须为对象")
        return issues
    allowed = {*default_profile(), "schemaVersion", "updatedAt", "targetRoles", "locations", "salary"}
    for key in sorted(set(p) - allowed):
        need(key, False, "未知字段，请核对或更新 job-mate")
    if not partial:
        for key in ("schemaVersion", "targetRoles", "locations", "authorization"):
            need(key, key in p, "缺少必填字段")
    if "schemaVersion" in p:
        need("schemaVersion", type(p["schemaVersion"]) is int and p["schemaVersion"] == 2, "应为 2")
    for key in ("targetRoles", "locations", "hardRequirements"):
        if key in p:
            need(key, strings(p[key]) and (key != "targetRoles" or bool(p[key])), "须为字符串数组，目标职位不可为空")
    if "updatedAt" in p:
        check("updatedAt", lambda: ledger.parse_at(p["updatedAt"]) if text(p["updatedAt"]) else require(False, "须为时间字符串"))
    if "salary" in p and p["salary"] is not None:
        sal = check("salary", lambda: object_fields(p["salary"], {"currency", "period", "taxBasis", "min", "max", "matchRule"}, {"matchRule"}))
        if sal is not None:
            for key in ("min", "max"):
                need("salary." + key, sal.get(key) is None or number(sal[key]), "须为有限非负数或 null")
            if number(sal.get("min")) and number(sal.get("max")):
                need("salary", sal["min"] <= sal["max"], "区间倒置")
            for key, choices in (("period", ("hour", "day", "week", "month", "year")), ("taxBasis", ("pre_tax", "post_tax"))):
                if key in sal:
                    need("salary." + key, sal[key] in choices)
            if "currency" in sal:
                need("salary.currency", text(sal["currency"]))
            rules = sal["matchRule"] if isinstance(sal["matchRule"], list) else [sal["matchRule"]]
            need("salary.matchRule", strings(rules) and bool(rules) and all(v in ("overlap", "lower_bound", "contained") for v in rules))
    for field, keys in (("exclusions", {"roleTypes", "employmentTypes", "workSchedules", "keywords"}),
                        ("browser", {"headedInput"}), ("authorization", {"apply", "followup"}),
                        ("greeting", {"mode", "text"}), ("recruiterActivity", {"maxAgeDays"}),
                        ("schedule", {"timezone", "activeHours"}),
                        ("style", {"tone", "length", "address", "selfIntro", "bannedWords", "emoji"}),
                        ("inquiry", {"priority", "maxPerMessage"})):
        if field not in p:
            continue
        cfg = check(field, lambda: object_fields(p[field], keys))
        if cfg is None:
            continue
        if field == "exclusions":
            for key, value in cfg.items():
                need(field + "." + key, strings(value), "须为字符串数组")
        elif field == "browser":
            need(field, "headedInput" not in cfg or type(cfg["headedInput"]) is bool, "headedInput 须为布尔")
        elif field == "authorization":
            if not partial:
                need("authorization.apply.send", isinstance(cfg.get("apply"), dict) and "send" in cfg["apply"], "缺少明确的布尔授权")
            for key, fields in (("apply", {"send"}), ("followup", {"reply", "nudge"})):
                if key not in cfg:
                    continue
                entry = check(field + "." + key, lambda: object_fields(cfg[key], fields))
                if entry is not None:
                    for name, value in entry.items():
                        need(field + "." + key + "." + name, value in ("L1", "L2") if name == "reply" else type(value) is bool)
        elif field == "greeting":
            if "mode" in cfg:
                need("greeting.mode", cfg["mode"] in ("dynamic", "static"))
            if "text" in cfg or cfg.get("mode") == "static":
                need("greeting.text", text(cfg.get("text")) and len(cfg["text"]) <= 80, "须为 1–80 字原文")
        elif field == "recruiterActivity":
            if "maxAgeDays" in cfg:
                need(field + ".maxAgeDays", type(cfg["maxAgeDays"]) is int and cfg["maxAgeDays"] >= 0)
        elif field == "schedule":
            if "timezone" in cfg:
                check("schedule.timezone", lambda: fsutil.tz_from_name(cfg["timezone"]) if text(cfg["timezone"]) else require(False, "时区为空"))
            if "activeHours" in cfg:
                hours = check("schedule.activeHours", lambda: object_fields(cfg["activeHours"], {"weekdays", "start", "end"}, {"weekdays", "start", "end"}))
                if hours is not None:
                    days = hours["weekdays"]
                    need("schedule.activeHours.weekdays", isinstance(days, list) and bool(days) and all(type(v) is int and 1 <= v <= 7 for v in days) and len(set(days)) == len(days))
                    need("schedule.activeHours", all(isinstance(hours[k], str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", hours[k]) for k in ("start", "end")) and hours["start"] < hours["end"])
        elif field == "style":
            for key, choices in (("tone", ("polite", "friendly", "concise")), ("length", ("short", "medium")), ("address", ("neutral", "surname"))):
                if key in cfg:
                    need(field + "." + key, cfg[key] in choices)
            for key in ("selfIntro", "bannedWords"):
                if key in cfg:
                    need(field + "." + key, strings(cfg[key]) and (key != "selfIntro" or 1 <= len(cfg[key]) <= 3), "须为字符串数组；selfIntro 须为 1–3 条非空短语")
            if "emoji" in cfg:
                need("style.emoji", type(cfg["emoji"]) is bool)
        elif field == "inquiry":
            if "priority" in cfg:
                priority = cfg["priority"]
                need("inquiry.priority", strings(priority) and len(set(priority)) == len(priority) and all(v in ledger.INFO_GROUPS for v in priority))
            if "maxPerMessage" in cfg:
                need("inquiry.maxPerMessage", type(cfg["maxPerMessage"]) is int and cfg["maxPerMessage"] in (1, 2))
    for field, defaults in (("limits", ledger.DEFAULT_LIMITS), ("nudge", ledger.DEFAULT_NUDGE)):
        if field not in p:
            continue
        cfg = check(field, lambda: object_fields(p[field], {*defaults, "platforms"}))
        if cfg is None:
            continue
        overrides = cfg.get("platforms", {})
        if not isinstance(overrides, dict):
            need(field + ".platforms", False, "须为对象")
            continue
        blocks = [(field, {k: v for k, v in cfg.items() if k != "platforms"})]
        for platform, entry in overrides.items():
            if check(field + ".platforms." + platform, lambda: ledger.load_platform(platform)) is not None:
                entry = check(field + ".platforms." + platform, lambda: object_fields(entry, defaults))
                if entry is not None:
                    blocks.append((field + ".platforms." + platform, entry))
        for path, block in blocks:
            for key, value in block.items():
                valid = value in ("company", "job") if key == "dedupScope" else type(value) is int and value >= (1 if field == "nudge" else 0)
                need(path + "." + key, valid, "须为正整数" if field == "nudge" else "限额无效")
            if field == "nudge":
                merged = {**defaults, **blocks[0][1], **block}
                a, b = merged["maxPerSilence"], merged["maxPerThread"]
                need(path, type(a) is int and type(b) is int and 1 <= a <= 3 and a <= b <= 9, "连续次数须为 1–3，总次数须在连续次数至 9 之间")
    check("im", lambda: notify.im_config(p))
    return issues


def profile(p, partial=False):
    issues = profile_issues(p, partial=partial)
    if issues:
        raise ledger.LedgerError(issues[0]["field"] + "：" + issues[0]["problem"])


def profile_warnings(p):
    if not isinstance(p, dict):
        return []
    warnings = []

    def missing(raw, defaults, prefix=""):
        for key, value in defaults.items():
            field = prefix + key
            if key not in raw:
                warnings.append({"file": "profile.json", "field": field,
                                 "warning": "缺少，将使用默认值 " + json.dumps(value, ensure_ascii=False)})
            elif isinstance(value, dict) and isinstance(raw[key], dict):
                missing(raw[key], value, field + ".")
    missing(p, default_profile())
    if isinstance(p.get("style", {}), dict) and "selfIntro" not in p.get("style", {}):
        warnings.append({"file": "profile.json", "field": "style.selfIntro",
                         "warning": "缺少个人介绍素材；由 setup 据 candidate.md 起草并请本人确认，不生成默认个人事实"})
    return warnings


def effective_profile(p, platform_id=None):
    profile(p, partial=True)
    result = default_profile()
    if platform_id is not None:
        result["limits"].update(ledger.load_platform(platform_id)["defaultLimits"])

    def merge(base, raw):
        for key, value in raw.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                merge(base[key], value)
            else:
                base[key] = copy.deepcopy(value)
    merge(result, p)
    if platform_id is not None:
        for field in ("limits", "nudge"):
            overrides = result[field].pop("platforms", {})
            result[field].update(copy.deepcopy(overrides.get(platform_id, {})))
    if result["im"]["channel"] == "lark":
        cfg = notify.im_config(result)
        result["im"]["waitMinutes"] = cfg["waitMinutes"]
    return result


def records(raw, indent, allowed, required):
    rows, current = [], None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or line.strip() == "[]":
            continue
        match = re.fullmatch(" " * indent + r"- id:\s*(.+)", line)
        if match:
            current = {"id": ledger._yaml_scalar(match[1])}
            rows.append(current)
            continue
        match = re.fullmatch(" " * (indent + 2) + r"([A-Za-z_]+):\s*(.*)", line)
        require(current is not None and match is not None, "YAML 仅支持文档中的逐条键值格式")
        key, value = match.groups()
        require(key not in current and key in allowed, "YAML 重复或未知字段 " + key)
        current[key] = value
    for row in rows:
        require(set(required) <= row.keys() and all(text(row[k]) for k in required), "YAML 条目缺少必填值")
    require(len({r["id"] for r in rows}) == len(rows), "YAML id 重复")
    return rows


def uploaded_map(raw):
    require(raw.startswith("{") and raw.endswith("}"), "uploaded 须为内联映射")
    body, offset, seen = raw[1:-1], 0, set()
    token = re.compile(r'''\s*([a-z][a-z0-9_-]*)\s*:\s*("(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^,{}\[\]]+)\s*(,|$)''')
    while body[offset:].strip():
        match = token.match(body, offset)
        require(match is not None, "uploaded 映射格式无效")
        key, value, delimiter = match.groups()
        require(key not in seen, "uploaded 平台重复")
        seen.add(key)
        ledger.load_platform(key)
        value = value.strip()
        require(value == "null" or text(ledger._yaml_scalar(value)), "uploaded 值无效")
        offset = match.end()
        require(not delimiter or bool(body[offset:].strip()), "uploaded 末尾多余逗号")


def validate(name, data):
    raw = data.decode("utf-8")
    if name == "job-mate.json":
        marker(json_value(raw))
    elif name == "profile.json":
        profile(json_value(raw))
    elif name == "stoplist.yaml":
        ledger.parse_stoplist(raw)
    elif name == "qa.yaml":
        rows = records(raw, 0, {"ask", "answer", "basis", "avoid"}, {"id", "ask", "answer", "basis"})
        for row in rows:
            questions = ledger._yaml_list(row["ask"])
            require(strings(questions) and bool(questions), "qa.ask 须为非空文字数组")
            for key in ("answer", "basis", "avoid"):
                if key in row:
                    require(text(ledger._yaml_scalar(row[key])), "qa 值为空")
    elif name == "resumes/index.yaml":
        lines = raw.splitlines()
        meaningful = [v for v in lines if v.strip() and not v.lstrip().startswith("#")]
        require(len(meaningful) >= 3 and meaningful[0].startswith("default:") and meaningful[1] == "versions:", "简历索引须有 default / versions")
        default = ledger._yaml_scalar(meaningful[0].split(":", 1)[1].strip())
        rows = records("\n".join(meaningful[2:]), 2, {"file", "use", "uploaded"}, {"id", "file", "use"})
        require(default in {r["id"] for r in rows}, "简历 default 不存在")
        for row in rows:
            relative(ledger._yaml_scalar(row["file"]))
            if "uploaded" in row:
                uploaded_map(row["uploaded"])
    else:
        require(name in ("candidate.md", "rules.md"), "未知配置文件")
        # 自由文本仅保留字节；不把本人材料、日志或Markdown解释为指令。

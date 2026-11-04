"""配置事务的封闭字段校验；只校验传入内容，不读取当前损坏配置。"""
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
    return json.loads(raw, object_pairs_hook=unique, parse_constant=constant)


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


def profile(p):
    object_fields(p, {"schemaVersion", "updatedAt", "targetRoles", "locations", "salary", "hardRequirements", "exclusions",
                      "browser", "recruiterActivity", "authorization", "greeting", "limits", "im", "schedule", "style", "inquiry", "nudge"},
                  {"schemaVersion", "targetRoles", "locations", "authorization"})
    require(type(p["schemaVersion"]) is int and p["schemaVersion"] == 2, "不支持的 profile 格式，请核对或更新 job-mate")
    for key in ("targetRoles", "locations", "hardRequirements"):
        if key in p:
            require(strings(p[key]) and (key != "targetRoles" or bool(p[key])), key + " 须为字符串数组，目标职位不可为空")
    if "updatedAt" in p:
        require(text(p["updatedAt"]), "updatedAt 须为时间字符串")
        ledger.parse_at(p["updatedAt"])
    if p.get("salary") is not None:
        sal = object_fields(p["salary"], {"currency", "period", "taxBasis", "min", "max", "matchRule"}, {"matchRule"})
        for key in ("min", "max"):
            require(sal.get(key) is None or number(sal[key]), "salary 数值无效")
        require(sal.get("min") is None or sal.get("max") is None or sal["min"] <= sal["max"], "salary 区间倒置")
        require("currency" not in sal or text(sal["currency"]), "currency 为空")
        require("period" not in sal or sal["period"] in ("hour", "day", "week", "month", "year"), "period 无效")
        require("taxBasis" not in sal or sal["taxBasis"] in ("pre_tax", "post_tax"), "taxBasis 无效")
        rules = sal["matchRule"] if isinstance(sal["matchRule"], list) else [sal["matchRule"]]
        require(bool(rules) and all(isinstance(v, str) and v in ("overlap", "lower_bound", "contained") for v in rules), "matchRule 无效")
    exc = object_fields(p.get("exclusions", {}), {"roleTypes", "employmentTypes", "workSchedules", "keywords"})
    require(all(strings(v) for v in exc.values()), "exclusions 须为字符串数组")
    object_fields(p.get("browser", {}), set())
    auth = object_fields(p["authorization"], {"apply", "followup"}, {"apply"})
    apply = object_fields(auth["apply"], {"send"}, {"send"})
    require(type(apply["send"]) is bool, "apply.send 须为布尔")
    follow = object_fields(auth.get("followup", {}), {"reply", "nudge"})
    require(follow.get("reply", "L2") in ("L1", "L2") and type(follow.get("nudge", True)) is bool, "followup 授权无效")
    greeting = object_fields(p.get("greeting", {}), {"mode", "text"})
    require(greeting.get("mode", "dynamic") in ("dynamic", "static"), "greeting.mode 无效")
    if "text" in greeting:
        require(text(greeting["text"]) and len(greeting["text"]) <= 80, "招呼语为空或超过80字")
    require(greeting.get("mode") != "static" or text(greeting.get("text")), "static 缺少文字")
    ra = object_fields(p.get("recruiterActivity", {}), {"maxAgeDays"})
    require("maxAgeDays" not in ra or type(ra["maxAgeDays"]) is int and ra["maxAgeDays"] >= 0, "maxAgeDays 无效")
    for field, defaults, evaluate in (("limits", ledger.DEFAULT_LIMITS, ledger.effective_limits), ("nudge", ledger.DEFAULT_NUDGE, ledger.effective_nudge)):
        cfg = object_fields(p.get(field, {}), {*defaults, "platforms"})
        overrides = cfg.get("platforms", {})
        require(isinstance(overrides, dict), field + ".platforms 须为对象")
        for platform, override in overrides.items():
            ledger.load_platform(platform)
            object_fields(override, set(defaults))
            evaluate(p, platform)
        evaluate(p, "boss")
    schedule = object_fields(p.get("schedule", {}), {"timezone", "activeHours"})
    if "timezone" in schedule:
        require(text(schedule["timezone"]), "timezone 无效")
        fsutil.tz_from_name(schedule["timezone"])
    if "activeHours" in schedule:
        hours = object_fields(schedule["activeHours"], {"weekdays", "start", "end"}, {"weekdays", "start", "end"})
        require(isinstance(hours["weekdays"], list) and bool(hours["weekdays"]) and all(type(v) is int and 1 <= v <= 7 for v in hours["weekdays"])
                and len(set(hours["weekdays"])) == len(hours["weekdays"]), "weekdays 无效")
        require(all(isinstance(hours[k], str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", hours[k]) for k in ("start", "end"))
                and hours["start"] < hours["end"], "activeHours 时间区间无效")
    style = object_fields(p.get("style", {}), {"tone", "length", "address", "selfIntro", "bannedWords", "emoji"})
    for key, values in (("tone", ("polite", "friendly", "concise")), ("length", ("short", "medium")), ("address", ("neutral", "surname"))):
        require(key not in style or style[key] in values, "style." + key + " 无效")
    for key in ("selfIntro", "bannedWords"):
        require(key not in style or strings(style[key]), "style." + key + " 须为字符串数组")
    require("selfIntro" not in style or 1 <= len(style["selfIntro"]) <= 3, "selfIntro 须为1–3条")
    require("emoji" not in style or type(style["emoji"]) is bool, "emoji 须为布尔")
    inquiry = object_fields(p.get("inquiry", {}), {"priority", "maxPerMessage"})
    priority = inquiry.get("priority", [])
    require(strings(priority) and len(set(priority)) == len(priority) and all(v in ledger.INFO_GROUPS for v in priority), "inquiry.priority 无效")
    require(type(inquiry.get("maxPerMessage", 2)) is int and 1 <= inquiry.get("maxPerMessage", 2) <= 2, "maxPerMessage 无效")
    try:
        notify.im_config(p)
    except notify.NotifyError as error:
        raise ledger.LedgerError(str(error)) from error


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

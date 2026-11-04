"""读取、校验随套件分发的平台描述；不访问网络或工作区。"""

import json
import os
import re
from pathlib import Path

import fsutil

ROOT = Path(__file__).resolve().parent.parent / "platforms"
ID_RE = re.compile(r"[a-z][a-z0-9_-]*")
REQUIRED = {"id", "name", "domains", "jobUrl", "conversationKey", "recruiterFormat",
            "activityLabels", "automationPolicy", "defaultLimits", "timeLabels", "messageId"}


class PlatformError(ValueError):
    pass


def validate_platform(data, platform_id):
    """字段错误包含路径；返回校验后的描述，加载时不缓存可变对象。"""
    def require(ok, field, message):
        if not ok:
            raise PlatformError(f"平台 {platform_id} 的 {field}：{message}")

    def text(value):
        return isinstance(value, str) and bool(value.strip())

    def strings(value):
        return isinstance(value, list) and bool(value) and all(text(v) for v in value) and len(set(value)) == len(value)

    require(isinstance(platform_id, str) and bool(ID_RE.fullmatch(platform_id)), "id", "须为有效平台目录名")
    require(isinstance(data, dict), "platform.json", "须为对象")
    require(not REQUIRED - data.keys(), "platform.json", "缺少字段 " + "、".join(sorted(REQUIRED - data.keys())))
    require(data["id"] == platform_id, "id", "须与目录名一致")
    for key in ("name", "recruiterFormat"):
        require(text(data[key]), key, "须为非空字符串")
    for key in ("domains", "conversationKey"):
        require(strings(data[key]), key, "须为不重复的非空字符串数组")
    for domain in data["domains"]:
        require(bool(re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", domain)), "domains", "须为域名，不含协议、路径或端口")
    url = data["jobUrl"]
    require(isinstance(url, dict), "jobUrl", "须为对象")
    require(text(url.get("idPattern")), "jobUrl.idPattern", "须为非空正则表达式")
    require(type(url.get("stripQuery")) is bool, "jobUrl.stripQuery", "须为布尔值")
    try:
        pattern = re.compile(url["idPattern"])
    except re.error as error:
        raise PlatformError(f"平台 {platform_id} 的 jobUrl.idPattern 非法：{error}") from error
    require(pattern.groups == 1, "jobUrl.idPattern", "须恰好有一个职位 ID 捕获组")
    labels = data["activityLabels"]
    require(isinstance(labels, dict) and bool(labels), "activityLabels", "须为非空对象")
    require(all(text(k) and type(v) is int and v >= 0 for k, v in labels.items()), "activityLabels", "值须为非负整数天数")
    policy = data["automationPolicy"]
    require(isinstance(policy, dict), "automationPolicy", "须为对象")
    for key in ("forbidCDP", "forbidHeadless"):
        require(type(policy.get(key)) is bool, "automationPolicy." + key, "须为布尔值")
    limits = data["defaultLimits"]
    require(isinstance(limits, dict), "defaultLimits", "须为对象")
    for key, value in limits.items():
        valid = (isinstance(value, str) and value in {"company", "job"}) if key == "dedupScope" else type(value) is int and value >= 0
        require(valid, "defaultLimits." + key, "须为非负整数，dedupScope 须为 company 或 job")
    labels = data["timeLabels"]
    require(isinstance(labels, dict), "timeLabels", "须为对象")
    for key in ("relativeDays", "weekdays"):
        values = labels.get(key)
        require(isinstance(values, dict) and bool(values), "timeLabels." + key, "须为非空对象")
        require(all(text(k) and type(v) is int and (v >= 0 if key == "relativeDays" else 1 <= v <= 7)
                    for k, v in values.items()), "timeLabels." + key, "值须为有效天数或星期编号（1–7）")
    for key in ("recent", "dateFormats"):
        require(strings(labels.get(key)), "timeLabels." + key, "须为不重复的非空字符串数组")
    for key in ("timeFormat", "dateTimeFormat"):
        require(text(labels.get(key)), "timeLabels." + key, "须为非空字符串")
    message_id = data["messageId"]
    require(isinstance(message_id, dict) and type(message_id.get("available")) is bool,
            "messageId.available", "须为布尔值")
    return data


def load_platform(platform_id):
    if not isinstance(platform_id, str) or not ID_RE.fullmatch(platform_id):
        raise PlatformError("平台 id 须为小写字母开头的字母、数字、下划线或连字符，不得含路径")
    roots = [ROOT]
    extra = os.environ.get("JOB_MATE_TEST_PLATFORM_ROOT") if os.environ.get("JOB_MATE_TESTING") == "1" else None
    if extra:
        roots.append(Path(extra).expanduser().resolve())
    for root in roots:
        path = root / platform_id / "platform.json"
        if not path.exists():
            continue
        try:
            data = json.loads(fsutil.read_text(path))
        except (OSError, ValueError) as error:
            raise PlatformError(f"无法读取平台 {platform_id} 的描述：{error}") from error
        return validate_platform(data, platform_id)
    raise PlatformError(f"未知平台 {platform_id}，缺少 platforms/{platform_id}/platform.json")

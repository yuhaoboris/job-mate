#!/usr/bin/env python3
"""T6.3离线CLI替身：采样响应树+明确合成状态，不执行真实CLI/网络/脚本。"""
import argparse
import copy
from datetime import datetime, timedelta
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import fsutil

SAMPLES = Path(__file__).resolve().parent / "lark_samples"
MID = re.compile(r"om_[0-9a-f]{32}\Z")
UID = re.compile(r"ou_[0-9a-f]{32}\Z")
CID = re.compile(r"oc_[0-9a-f]{32}\Z")
DECIMAL = re.compile(r"(?:0|[1-9]\d*)\Z")


class Rejected(Exception):
    def __init__(self, code, message, exit_code=2, payload=None):
        super().__init__(message)
        self.exit_code = exit_code
        self.payload = payload or {"ok": False, "identity": "bot", "error": {"type": "fake", "subtype": code, "code": code, "message": message}}


def require(ok, message):
    if not ok:
        raise Rejected("FAKE_INVALID", message)


def strict(raw):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            require(key not in out, "JSON重复字段")
            out[key] = value
        return out
    def invalid(value):
        raise ValueError("JSON非法常量")
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def sample(name):
    return strict((SAMPLES / (name + ".json")).read_bytes())


def initial_state(profile="synthetic-profile", seeded=True):
    """只构造合成数据，不写状态。profile/auth 用实测形状，contact 仍须显式配置。"""
    sent = sample("send")["data"]
    shortcut = sample("list")["data"]["messages"]
    native = [sample("native-list-page1")["data"]["items"][0], sample("native-list-page2")["data"]["items"][0]]
    bot = sample("mget")["data"]["messages"][0]["sender"]
    local = max(datetime.strptime(sent["create_time"], "%Y-%m-%d %H:%M:%S"),
                *(datetime.strptime(m["create_time"], "%Y-%m-%d %H:%M") for m in shortcut)) if seeded else datetime.strptime(sent["create_time"], "%Y-%m-%d %H:%M:%S")
    p = {"ownerId": native[1]["sender"]["id"], "chatId": sent["chat_id"], "appId": bot["id"], "botOpenId": bot["open_bot_id"],
         "tenantKey": bot["tenant_key"], "botName": bot["name"], "ownerName": shortcut[1]["sender"]["name"],
         "messages": copy.deepcopy(native) if seeded else [], "receipts": {sent["message_id"]: copy.deepcopy(sent)} if seeded else {},
         "displayTimes": {m["message_id"]: m["create_time"] for m in shortcut} if seeded else {}, "idempotency": {},
         "nativeClock": str(max(int(m["create_time"]) for m in native) + 1000) if seeded else native[0]["create_time"],
         "shortcutClock": (local + timedelta(seconds=1) if seeded else local).strftime("%Y-%m-%d %H:%M:%S"),
         "auxiliary": {"profileList": sample("profile-list"), "authStatus": sample("auth-status"), "contacts": {}}}
    return {"schemaVersion": 1, "synthetic": True, "profiles": {profile: p}, "calls": [], "faults": [], "tokens": {},
            "nextMessage": 3 if seeded else 1, "nextPosition": 103 if seeded else 101, "nextToken": 1}


def state_path():
    raw = os.environ.get("LARK_FAKE_STATE")
    require(bool(raw), "必须显式设置LARK_FAKE_STATE，不查询真实CLI")
    path = Path(raw)
    require(path.is_absolute() and not path.is_symlink() and path.resolve() == path.parent.resolve() / path.name, "状态路径须绝对且非链接")
    path = path.resolve()
    require(Path(tempfile.gettempdir()).resolve() in path.parents and ROOT not in path.parents and path.is_file(), "状态须为系统临时目录内、仓库外的已有合成JSON文件")
    lock = path.with_name(path.name + ".lock")
    require(not lock.is_symlink() and lock.resolve() == lock.absolute(), "状态锁不能是链接")
    return path, lock


def validate(state):
    require(isinstance(state, dict) and type(state.get("schemaVersion")) is int and state["schemaVersion"] == 1 and state.get("synthetic") is True, "状态不是合成v1")
    require(isinstance(state.get("profiles"), dict) and state["profiles"] and isinstance(state.get("calls"), list)
            and isinstance(state.get("faults"), list) and isinstance(state.get("tokens"), dict), "状态集合无效")
    for key in ("nextMessage", "nextPosition", "nextToken"):
        require(type(state.get(key)) is int and 1 <= state[key] < 2 ** 128, "状态计数无效")
    for name, p in state["profiles"].items():
        require(isinstance(name, str) and name and isinstance(p, dict), "配置档无效")
        require(isinstance(p.get("ownerId"), str) and UID.fullmatch(p["ownerId"]) and isinstance(p.get("chatId"), str) and CID.fullmatch(p["chatId"]), "合成路由无效")
        require(all(isinstance(p.get(k), str) and p[k] for k in ("appId", "botOpenId", "tenantKey", "botName", "ownerName", "nativeClock", "shortcutClock")), "合成身份/时间缺失")
        require(DECIMAL.fullmatch(p["nativeClock"]) is not None, "原生时钟须十进制毫秒字符串")
        datetime.strptime(p["shortcutClock"], "%Y-%m-%d %H:%M:%S")
        require(isinstance(p.get("messages"), list) and all(isinstance(m, dict) for m in p["messages"]), "消息须为对象数组")
        for m in p["messages"]:
            require(isinstance(m.get("message_id"), str) and MID.fullmatch(m["message_id"]) and isinstance(m.get("chat_id"), str)
                    and m["chat_id"] == p["chatId"] and isinstance(m.get("create_time"), str) and DECIMAL.fullmatch(m["create_time"]), "原生消息标识/时间无效")
            require(isinstance(m.get("sender"), dict) and isinstance(m["sender"].get("id"), str), "sender须为对象")
        require(len({m["message_id"] for m in p["messages"]}) == len(p["messages"]), "消息ID重复")
        require(all(isinstance(p.get(k), dict) for k in ("receipts", "displayTimes", "idempotency", "auxiliary")), "配置档状态集合无效")
        require(all(isinstance(v, str) for v in p["displayTimes"].values()) and isinstance(p["auxiliary"].get("contacts"), dict), "展示时间/辅助联系人映射无效")
        for value in p["idempotency"].values():
            require(isinstance(value, dict) and isinstance(value.get("intent"), list) and isinstance(value.get("receipt"), dict), "幂等状态无效")
    for token in state["tokens"].values():
        require(isinstance(token, dict) and isinstance(token.get("signature"), str) and isinstance(token.get("ids"), list)
                and all(isinstance(v, str) and MID.fullmatch(v) for v in token["ids"])
                and len(set(token["ids"])) == len(token["ids"]) and type(token.get("offset")) is int
                and 0 < token["offset"] < len(token["ids"]), "分页状态无效")
    for fault in state["faults"]:
        require(isinstance(fault, dict) and isinstance(fault.get("command"), str) and fault.get("kind") in
                ("timeout", "nonzero", "missing_message_id", "missing_create_time", "missing_page_token", "other_chat", "non_owner"), "未知故障")
        require(fault.get("phase", "before") in ("before", "after") and (fault.get("kind") != "nonzero" or fault.get("phase", "before") == "before"), "非零拒绝不能冒充已发送后拒绝")
        delay = fault.get("delaySeconds", 1)
        require(type(delay) in (int, float) and 0.05 <= delay <= 2 and math.isfinite(delay), "故障延时须0.05–2秒")
        message_commands = {"send", "reply", "mget", "list", "native-get", "native-list"}
        require(fault["command"] in message_commands | {"version", "profiles", "auth", "contact"}, "未知故障命令")
        require(fault["kind"] not in ("missing_page_token", "non_owner") or fault["command"] in ("list", "native-list"), "故障与分页/用户消息命令不匹配")
        require(fault["kind"] in ("timeout", "nonzero", "missing_page_token", "non_owner") or fault["command"] in message_commands, "故障与消息字段不匹配")


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Rejected("FAKE_INVALID_ARGS", message)


def parse(argv):
    parser = Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--version", action="store_true")
    sub = parser.add_subparsers(dest="group", parser_class=Parser)
    profile = sub.add_parser("profile", add_help=False, allow_abbrev=False)
    profile.add_argument("action", choices=("list",))
    auth = sub.add_parser("auth", add_help=False, allow_abbrev=False)
    auth.add_argument("action", choices=("status",))
    auth.add_argument("--json", action="store_true", required=True)
    auth.add_argument("--verify", action="store_true", required=True)
    contact = sub.add_parser("contact", add_help=False, allow_abbrev=False)
    contact.add_argument("arguments", nargs=argparse.REMAINDER)
    im = sub.add_parser("im", add_help=False, allow_abbrev=False)
    actions = im.add_subparsers(dest="action", required=True, parser_class=Parser)
    for action in ("+messages-send", "+messages-reply", "+messages-mget", "+chat-messages-list"):
        child = actions.add_parser(action, add_help=False, allow_abbrev=False)
        child.add_argument("--as", dest="identity", choices=("bot",), required=True)
        child.add_argument("--format", choices=("json",), required=True)
        if action in ("+messages-send", "+messages-reply"):
            child.add_argument("--user-id" if action == "+messages-send" else "--message-id", required=True)
            child.add_argument("--text", required=True)
            child.add_argument("--idempotency-key", required=True)
        elif action == "+messages-mget":
            child.add_argument("--message-ids", required=True)
            child.add_argument("--no-reactions", action="store_true")
        else:
            child.add_argument("--chat-id", required=True)
            child.add_argument("--start", required=True)
            child.add_argument("--order", choices=("asc",), required=True)
            child.add_argument("--page-all", action="store_true")
            child.add_argument("--page-limit", type=int, required=True)
            child.add_argument("--no-reactions", action="store_true")
    api = sub.add_parser("api", add_help=False, allow_abbrev=False)
    api.add_argument("method", choices=("GET",))
    api.add_argument("path")
    api.add_argument("--as", dest="identity", choices=("bot",), required=True)
    api.add_argument("--format", choices=("json",), required=True)
    api.add_argument("--params")
    args = parser.parse_args(argv)
    require(not args.version or args.group is None, "version不能与命令组合")
    require(args.version or args.group is not None, "缺少命令")
    if args.version:
        args.command = "version"
    elif args.group == "api":
        if args.path == "/open-apis/im/v1/messages":
            require(args.params is not None, "原生list须有params")
            args.command = "native-list"
        elif args.path.startswith("/open-apis/im/v1/messages/") and MID.fullmatch(args.path.rsplit("/", 1)[-1]) and args.params is None:
            args.command = "native-get"
        else:
            raise Rejected("FAKE_UNSUPPORTED", "只支持已采的原生GET消息路径，不支持其他API")
    elif args.group == "im":
        args.command = {"+messages-send": "send", "+messages-reply": "reply", "+messages-mget": "mget", "+chat-messages-list": "list"}[args.action]
    else:
        args.command = {"profile": "profiles", "auth": "auth", "contact": "contact"}[args.group]
    return args


def lookup(p, mid):
    message = next((m for m in p["messages"] if m["message_id"] == mid), None)
    if message is None:
        raise Rejected("FAKE_NOT_FOUND", "指定合成消息不存在", 1)
    return message


def render(p, message, mode):
    if mode == "native-list":
        return copy.deepcopy(message)
    if mode == "native-get":
        fields = sample("native-get")["data"]["items"][0]
        out = {key: copy.deepcopy(message[key]) for key in fields if key in message}
        out["sender"] = {key: copy.deepcopy(value) for key, value in message["sender"].items() if key != "is_agent"}
        for key in ("parent_id", "root_id"):
            if key in message:
                out[key] = message[key]
        return out
    app = message["sender"].get("sender_type") == "app"
    if mode == "mget" and app:
        out = sample("mget")["data"]["messages"][0]
    else:
        out = sample("list")["data"]["messages"][0 if app else 1]
    mid = message["message_id"]
    require(mid in p["displayTimes"], "shortcut展示时间须显式提供，不从epoch猜时区")
    content = strict(message.get("body", {}).get("content", ""))
    require(isinstance(content, dict) and isinstance(content.get("text"), str), "仅支持可解析text消息")
    out.update(chat_id=message["chat_id"], message_id=mid, create_time=p["displayTimes"][mid], content=content["text"],
               message_position=message["message_position"], deleted=message["deleted"], updated=message["updated"])
    out["message_app_link"] = "https://applink.feishu.cn/client/chat/open?openChatId=" + message["chat_id"] + "&position=" + message["message_position"]
    out["sender"].update(id=message["sender"]["id"], id_type=message["sender"]["id_type"], sender_type=message["sender"]["sender_type"], tenant_key=message["sender"]["tenant_key"], name=p["botName"] if app else p["ownerName"])
    if app:
        out["sender"]["open_bot_id"] = p["botOpenId"]
        out["sender"]["sender_i18n_names"] = {key: p["botName"] for key in out["sender"]["sender_i18n_names"]}
    if "parent_id" in message:
        out["reply_to"] = message["parent_id"]
    else:
        out.pop("reply_to", None)
    return out


def send(state, p, args):
    require(args.text.strip() and args.idempotency_key, "正文及幂等键不能为空")
    parent = lookup(p, args.message_id) if args.command == "reply" else None
    require(parent is None or parent["chat_id"] == p["chatId"], "引用目标不属于本配置档私聊")
    if args.command == "send":
        require(UID.fullmatch(args.user_id) is not None, "user-id格式不合法")
        if args.user_id != p["ownerId"]:
            payload = sample("send-reject")
            payload["error"]["message"] = payload["error"]["message"].replace("ou_" + "e" * 32, args.user_id)
            raise Rejected("API_REJECT", "合成拒绝", 1, payload)
    signature = [args.command, args.message_id if parent else args.user_id, args.text]
    existing = p["idempotency"].get(args.idempotency_key)
    if existing is not None:
        require(isinstance(existing, dict) and existing.get("intent") == signature, "同幂等键不能改变目标/正文/动作")
        return {"ok": True, "identity": "bot", "data": copy.deepcopy(existing["receipt"])}
    all_ids = {m["message_id"] for profile in state["profiles"].values() for m in profile["messages"]}
    for _ in range(len(all_ids) + 1):
        number = state["nextMessage"]
        state["nextMessage"] += 1
        mid = "om_" + (f"{number:x}" * 32 if number < 16 else f"{number:032x}")
        if mid not in all_ids:
            break
    require(MID.fullmatch(mid) is not None, "合成消息计数溢出")
    local = datetime.strptime(p["shortcutClock"], "%Y-%m-%d %H:%M:%S")
    receipt = dict(sample("send")["data"], message_id=mid, chat_id=p["chatId"], create_time=p["shortcutClock"])
    message = sample("native-list-page1")["data"]["items"][0]
    message.update(message_id=mid, chat_id=p["chatId"], create_time=p["nativeClock"], update_time=p["nativeClock"], message_position=str(state["nextPosition"]))
    message["body"]["content"] = json.dumps({"text": args.text}, ensure_ascii=False, separators=(",", ":"))
    message["sender"].update(id=p["appId"], tenant_key=p["tenantKey"])
    if parent:
        message.update(parent_id=parent["message_id"], root_id=parent.get("root_id", parent["message_id"]))
    p["messages"].append(message)
    p["displayTimes"][mid] = local.strftime("%Y-%m-%d %H:%M")
    p["receipts"][mid] = copy.deepcopy(receipt)
    p["idempotency"][args.idempotency_key] = {"intent": signature, "receipt": copy.deepcopy(receipt)}
    p["shortcutClock"] = (local + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
    p["nativeClock"] = str(int(p["nativeClock"]) + 1000)
    state["nextPosition"] += 1
    return {"ok": True, "identity": "bot", "data": receipt}


def params(raw, p):
    value = strict(raw)
    required = {"container_id_type", "container_id", "start_time", "end_time", "sort_type", "page_size"}
    require(isinstance(value, dict) and required <= value.keys() and not value.keys() - required - {"page_token"}, "原生list参数缺失或未知")
    require(value["container_id_type"] == "chat" and value["container_id"] == p["chatId"] and value["sort_type"] == "ByCreateTimeAsc", "原生会话/排序不匹配")
    for key in ("start_time", "end_time"):
        require(isinstance(value[key], str) and DECIMAL.fullmatch(value[key]) and int(value[key]) <= 253402300799, "时间窗口须十进制epoch秒字符串")
    require(int(value["start_time"]) < int(value["end_time"]) and type(value["page_size"]) is int and 1 <= value["page_size"] <= 50, "窗口或page_size无效")
    require("page_token" not in value or isinstance(value["page_token"], str) and bool(value["page_token"]), "显式分页token不能为空")
    return value


def native_list(state, p, args):
    query = params(args.params, p)
    signature = json.dumps([args.profile, {k: v for k, v in query.items() if k != "page_token"}], sort_keys=True, ensure_ascii=False)
    if "page_token" in query:
        token = state["tokens"].get(query["page_token"])
        require(isinstance(token, dict) and token.get("signature") == signature, "未知token或本次分页窗口/配置档改变")
        messages = [lookup(p, mid) for mid in token["ids"]]
        offset = token["offset"]
    else:
        messages = sorted((m for m in p["messages"] if int(query["start_time"]) * 1000 <= int(m["create_time"]) < int(query["end_time"]) * 1000), key=lambda m: (int(m["create_time"]), m["message_id"]))
        offset = 0
    size = query["page_size"]
    page = messages[offset:offset + size]
    more = offset + size < len(messages)
    next_token = ""
    if more:
        token_data = {"signature": signature, "ids": [m["message_id"] for m in messages], "offset": offset + size}
        candidate = sample("native-list-page1")["data"]["page_token"]
        if candidate in state["tokens"] and state["tokens"][candidate] != token_data:
            candidate = f"synthetic-page-token-{state['nextToken']:06d}"
            state["nextToken"] += 1
        state["tokens"][candidate] = token_data
        next_token = candidate
    out = sample("native-list-page1")
    out["data"].update(items=[render(p, m, "native-list") for m in page], has_more=more, page_token=next_token)
    return out


def dispatch(state, args):
    require(args.profile in state["profiles"], "未配置该合成profile，不使用真实默认配置")
    p = state["profiles"][args.profile]
    if args.command == "version":
        return "lark-cli version 1.0.96\n"
    if args.command in ("profiles", "auth", "contact"):
        auxiliary = p["auxiliary"]
        if args.command in ("profiles", "auth"):
            key, expected = ("profileList", list) if args.command == "profiles" else ("authStatus", dict)
            value = auxiliary.get(key)
            require(isinstance(value, expected), "采样辅助响应类型不符")
        else:
            value = auxiliary.get("contacts", {}).get(json.dumps(args.arguments, ensure_ascii=False, separators=(",", ":")))
            require(isinstance(value, dict), "contact未采样，必须显式提供合成响应及精确参数")
        return copy.deepcopy(value)
    if args.command in ("send", "reply"):
        return send(state, p, args)
    if args.command == "native-get":
        message = lookup(p, args.path.rsplit("/", 1)[-1])
        out = sample("native-get")
        out["data"]["items"] = [render(p, message, "native-get")]
        return out
    if args.command == "native-list":
        return native_list(state, p, args)
    require(args.no_reactions, "shortcut子集仅支持已采的--no-reactions形式")
    if args.command == "mget":
        ids = args.message_ids.split(",")
        require(all(MID.fullmatch(mid) for mid in ids) and len(set(ids)) == len(ids), "message-ids无效或重复")
        out = sample("mget")
        out["data"].update(messages=[render(p, lookup(p, mid), "mget") for mid in ids], total=len(ids))
        return out
    require(args.chat_id == p["chatId"] and args.page_all and 1 <= args.page_limit <= 20, "shortcut会话/聚合参数不匹配")
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00"))
    require(start.tzinfo is not None, "shortcut start须有显式时区，不猜展示时间时区")
    lower = int(start.timestamp() * 1000)
    messages = sorted((m for m in p["messages"] if int(m["create_time"]) >= lower), key=lambda m: (int(m["create_time"]), m["message_id"]))
    require(len(messages) <= args.page_limit * 50, "shortcut聚合超出替身支持范围，请用原生逐页查询")
    out = sample("list")
    out["data"].update(messages=[render(p, m, "list") for m in messages], total=len(messages), has_more=False, page_token="")
    out["meta"]["pagination"].update(complete=True, pages=1, items=len(messages))
    return out


def corrupt(out, kind):
    require(isinstance(out, dict) and out.get("ok") is True, "该故障要求成功JSON响应")
    data = out["data"]
    messages = data.get("items", data.get("messages"))
    targets = messages if isinstance(messages, list) else [data]
    require(bool(targets), "故障没有可修改的消息")
    if kind in ("missing_message_id", "missing_create_time"):
        field = "message_id" if kind == "missing_message_id" else "create_time"
        require(any(field in m for m in targets), "故障与响应字段不匹配")
        for item in targets:
            item.pop(field, None)
    elif kind == "missing_page_token":
        require("has_more" in data, "此响应不是分页")
        data.update(has_more=True)
        data.pop("page_token", None)
        if "meta" in out:
            out["meta"]["pagination"]["complete"] = False
    elif kind == "other_chat":
        for item in targets:
            item["chat_id"] = "oc_" + "f" * 32
    elif kind == "non_owner":
        users = [m for m in targets if m.get("sender", {}).get("sender_type") == "user"]
        require(bool(users), "non_owner要求响应含本人消息")
        for item in users:
            item["sender"]["id"] = "ou_" + "f" * 32


def execute(state, argv):
    record = {"argv": list(argv), "command": "invalid"}
    state["calls"].append(record)
    delay, code, channel = 0, 0, "stdout"
    try:
        args = parse(argv)
        record.update(command=args.command, profile=args.profile)
        require(args.profile in state["profiles"], "未知合成配置档")
        fault = next((f for f in state["faults"] if f["command"] == args.command), None)
        if fault:
            state["faults"].remove(fault)
        if fault and fault["kind"] == "nonzero":
            raise Rejected("INJECTED", "合成非零拒绝", 1, sample("send-reject"))
        if not fault or fault["kind"] != "timeout" or fault.get("phase", "before") == "after":
            out = dispatch(state, args)
        if fault and fault["kind"] == "timeout":
            delay = fault.get("delaySeconds", 1)
            record["timeoutPending"] = True
            raise Rejected("FAKE_TIMEOUT", "合成进程超时", 1)
        if fault:
            corrupt(out, fault["kind"])
    except Rejected as error:
        out, code, channel = error.payload, error.exit_code, "stderr"
    text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False) + "\n"
    record.update(channel=channel, exitCode=code, response=copy.deepcopy(out))
    return text, code, channel, delay


def main(argv=None):
    fsutil.utf8_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    delay = 0
    try:
        path, lock = state_path()
        with fsutil.locked(lock):
            require(state_path()[0] == path, "等待期间状态路径变化")
            state = strict(path.read_bytes())
            validate(state)
            try:
                text, code, channel, delay = execute(state, argv)
            except (ValueError, KeyError) as error:
                failure = Rejected("FAKE_STATE_INVALID", "合成状态或参数无法处理")
                state["calls"][-1].update(channel="stderr", exitCode=2, response=failure.payload)
                text, code, channel, delay = json.dumps(failure.payload, ensure_ascii=False) + "\n", 2, "stderr", 0
            fsutil.write_text_atomic(path, json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    except (Rejected, OSError, ValueError) as error:
        failure = error if isinstance(error, Rejected) else Rejected("FAKE_STATE_INVALID", "合成状态无法读写")
        failure.payload["error"]["argv"] = argv
        text, code, channel, delay = json.dumps(failure.payload, ensure_ascii=False) + "\n", failure.exit_code, "stderr", 0
    if delay:
        print("fake-timeout-ready", file=sys.stderr, flush=True)
        time.sleep(delay)
    print(text, end="", file=sys.stdout if channel == "stdout" else sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

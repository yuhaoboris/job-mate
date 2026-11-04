#!/usr/bin/env python3
"""只读写显式临时合成场景的桌面工具替身；不查找、不启动真实程序。"""
import argparse
import copy
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import fsutil

VERSION = "2.5"
TOOL_VERSION = "1.0.2"
OBSERVATIONS = {"version", "permissions", "apps", "windows", "snapshot", "find", "get"}
ACTIONS = {"click", "set-value", "type"}
SAFE = {"delivery": "not_delivered", "retry": "safe"}
UNKNOWN = {"delivery": "unknown", "retry": "unknown"}
DELIVERED = {"delivery": "delivered_unverified", "retry": "unsafe"}


class Rejected(Exception):
    def __init__(self, code, message, exit_code=1, disposition=None, details=None):
        super().__init__(message)
        self.code, self.exit_code = code, exit_code
        self.disposition = copy.deepcopy(disposition or SAFE)
        self.details = details or {}


def require(ok, message):
    if not ok:
        raise Rejected("FAKE_SCENARIO_INVALID", message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def read_json(path):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            require(key not in out, "场景JSON重复键：" + key)
            out[key] = value
        return out
    def constant(value):
        raise ValueError("非法JSON常量：" + value)
    return json.loads(path.read_bytes(), object_pairs_hook=unique, parse_constant=constant)


def scenario_path():
    raw = os.environ.get("AGENT_DESKTOP_FAKE_SCENARIO")
    if not raw:
        raise Rejected("FAKE_SCENARIO_REQUIRED", "必须显式指定临时 AGENT_DESKTOP_FAKE_SCENARIO")
    path = Path(raw)
    require(path.is_absolute(), "场景路径必须是绝对路径")
    require(not path.is_symlink() and path.resolve() == path.parent.resolve() / path.name, "场景不能是链接或目录链接别名")
    path = path.resolve()
    temporary = Path(tempfile.gettempdir()).resolve()
    require(temporary in path.parents and ROOT not in path.parents and path.is_file(), "场景必须是系统临时目录内、仓库外的已有普通文件；先复制合成夹具")
    lock = path.with_name(path.name + ".lock")
    require(not lock.is_symlink() and lock.resolve() == lock.absolute(), "状态锁不能是链接")
    return path, lock


def validate(scene):
    require(isinstance(scene, dict) and type(scene.get("schemaVersion")) is int and scene["schemaVersion"] == 1
            and scene.get("synthetic") is True, "只接受 schemaVersion:1 / synthetic:true 的合成场景")
    require(isinstance(scene.get("permissions"), dict) and isinstance(scene["permissions"].get("accessibility"), dict)
            and scene["permissions"]["accessibility"].get("state") in ("granted", "denied", "unknown"), "权限结构无效")
    for key in ("apps", "windows"):
        require(isinstance(scene.get(key), list), key + "须为数组")
        require(all(isinstance(v, dict) and nonempty(v.get("id")) for v in scene[key]), key + "条目缺少ID")
        require(len({v["id"] for v in scene[key]}) == len(scene[key]), key + "有重复ID")
    require(all(nonempty(a.get("name")) and type(a.get("running")) is bool for a in scene["apps"]), "应用字段无效")
    require(isinstance(scene.get("pages"), dict) and bool(scene["pages"]), "缺少页面")
    for page_id, page in scene["pages"].items():
        require(nonempty(page_id) and isinstance(page, dict) and isinstance(page.get("elements"), list), "页面结构无效")
        require("complete" not in page or type(page["complete"]) is bool, "complete须为布尔")
        require("thread" not in page or nonempty(page["thread"]), "thread须为非空字符串")
        nodes = page["elements"]
        require(bool(nodes) and all(isinstance(n, dict) and nonempty(n.get("id")) and nonempty(n.get("role")) for n in nodes), "页面元素无效")
        ids = {n["id"] for n in nodes}
        require(len(ids) == len(nodes) and not any(v.startswith("message-") for v in ids), "元素ID重复或占用消息前缀")
        require("root" in ids and ("thread" not in page or "messages" in ids), "缺少页面根或消息区域")
        parents = {n["id"]: n.get("parent") for n in nodes}
        for node in nodes:
            for field in ("text", "value", "nativeId"):
                require(field not in node or isinstance(node[field], str), "元素文字须为字符串")
            for field in ("enabled", "editable", "contenteditable"):
                require(field not in node or type(node[field]) is bool, "元素标志须为布尔")
            parent, visited = parents[node["id"]], {node["id"]}
            while parent is not None:
                require(isinstance(parent, str) and parent in ids and parent not in visited, "父元素缺失或形成循环")
                visited.add(parent)
                parent = parents[parent]
            action = node.get("action")
            if action is not None:
                require(isinstance(action, dict) and action.get("kind") in ("navigate", "send", "unsupported"), "未知页面动作")
                if action["kind"] == "navigate":
                    require(isinstance(action.get("page"), str) and action["page"] in scene["pages"], "跳转页面不存在")
                if action["kind"] == "send":
                    require(nonempty(page.get("thread")) and nonempty(action.get("input")) and action["input"] in ids, "发送目标输入框或会话缺失")
    for window in scene["windows"]:
        require(nonempty(window.get("app")) and window["app"] in {a["id"] for a in scene["apps"]} and nonempty(window.get("title"))
                and nonempty(window.get("page")) and window["page"] in scene["pages"] and type(window.get("generation")) is int and window["generation"] >= 0, "窗口字段无效")
    state = scene.get("state")
    require(isinstance(state, dict), "缺少场景状态")
    for key in ("calls", "faults", "inputEvents"):
        require(isinstance(state.get(key), list), key + "须为数组")
    for key in ("drafts", "messages", "snapshots"):
        require(isinstance(state.get(key), dict), key + "须为对象")
    require(type(state.get("locked")) is bool and state.get("deliveryMode") in ("delivered", "no_delivery")
            and isinstance(state.get("sendLabel"), str) and type(state.get("nextSnapshot")) is int and state["nextSnapshot"] >= 1, "状态标志无效")
    for snap in state["snapshots"].values():
        require(isinstance(snap, dict) and isinstance(snap.get("refs"), dict) and all(nonempty(v) for v in snap["refs"].values())
                and isinstance(snap.get("nodes"), list) and all(isinstance(n, dict) and nonempty(n.get("id")) for n in snap["nodes"])
                and type(snap.get("complete")) is bool, "已存快照结构无效")
    for draft in state["drafts"].values():
        require(isinstance(draft, dict) and isinstance(draft.get("displayValue"), str) and isinstance(draft.get("inputValue"), str), "草稿状态无效")
    for thread, messages in state["messages"].items():
        require(thread in {p.get("thread") for p in scene["pages"].values()} and isinstance(messages, list), "消息会话无效")
        for message in messages:
            require(isinstance(message, dict) and message.get("speaker") in ("us", "hr") and message.get("type") in ("text", "card")
                    and isinstance(message.get("text"), str) and isinstance(message.get("label"), str), "消息结构无效")
            require("buttons" not in message or isinstance(message["buttons"], list) and all(nonempty(v) for v in message["buttons"]), "卡片按钮无效")
    for fault in state["faults"]:
        require(isinstance(fault, dict) and nonempty(fault.get("command")) and fault.get("kind") in ("timeout", "locked", "error", "incomplete")
                and fault.get("phase", "before") in ("before", "after"), "故障配置无效")
        delay = fault.get("delaySeconds", 1)
        require(type(delay) in (int, float) and 0.05 <= delay <= 2 and math.isfinite(delay), "故障等待须为0.05–2秒")
        require("code" not in fault or nonempty(fault["code"]), "故障错误码无效")
        require(fault["kind"] != "incomplete" or fault["command"] in ("snapshot", "find"), "incomplete仅用于snapshot/find")


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Rejected("INVALID_ARGS", message, exit_code=2)


def parse(argv):
    if argv == ["--version"]:
        return argparse.Namespace(command="version")
    parser = Parser(add_help=False, allow_abbrev=False)
    subs = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    for name in ("permissions", "apps", "windows", "snapshot", "find", "get", "click", "set-value", "type"):
        p = subs.add_parser(name, add_help=False, allow_abbrev=False)
        if name == "permissions":
            p.add_argument("status", nargs="?", choices=("status",))
        if name == "windows":
            p.add_argument("--app")
        if name in ("snapshot", "find", "get", "click", "set-value", "type"):
            p.add_argument("--window", required=True)
        if name == "snapshot":
            p.add_argument("--root")
        if name in ("find", "get", "click", "set-value", "type"):
            p.add_argument("--snapshot", required=True)
        if name == "find":
            p.add_argument("--query", required=True)
        if name in ("get", "click", "set-value", "type"):
            target = p.add_mutually_exclusive_group(required=True)
            target.add_argument("--ref")
            target.add_argument("--id")
        if name == "set-value":
            p.add_argument("--value", required=True)
        if name == "type":
            p.add_argument("--text", required=True)
            p.add_argument("--headed", action="store_true")
    return parser.parse_args(argv)


def window_for(scene, name):
    window = next((w for w in scene["windows"] if w["id"] == name), None)
    if window is None:
        raise Rejected("WINDOW_NOT_FOUND", "找不到指定合成窗口")
    if not any(a["id"] == window["app"] and a["running"] for a in scene["apps"]):
        raise Rejected("APP_NOT_FOUND", "合成应用未运行")
    if scene["state"]["locked"]:
        raise Rejected("WINDOW_NOT_FOUND", "合成锁屏：窗口没有辅助功能元素", disposition=UNKNOWN,
                       details={"kind": "window_without_accessibility_element"})
    if scene["permissions"]["accessibility"]["state"] != "granted":
        raise Rejected("PERMISSION_DENIED", "合成辅助功能权限未授予")
    return window


def draft_for(scene, window, node):
    key = json.dumps([window["id"], window["page"], node["id"]], ensure_ascii=False)
    drafts = scene["state"]["drafts"]
    if key not in drafts:
        value = node.get("value", "")
        drafts[key] = {"displayValue": value, "inputValue": value}
    return drafts[key]


def nodes_for(scene, window):
    page = scene["pages"][window["page"]]
    nodes = copy.deepcopy(page["elements"])
    for node in nodes:
        if node.get("editable"):
            node["value"] = draft_for(scene, window, node)["displayValue"]
    if page.get("thread"):
        for index, message in enumerate(scene["state"]["messages"].get(page["thread"], [])):
            node = dict(copy.deepcopy(message), id=f"message-{index}", parent="messages", role="group" if message["type"] == "card" else "text")
            nodes.append(node)
            for number, label in enumerate(message.get("buttons", [])):
                nodes.append({"id": f"message-{index}-button-{number}", "parent": node["id"], "role": "button", "text": label,
                              "action": {"kind": "unsupported"}})
    return nodes


def select(nodes, value):
    matches = [node for node in nodes if value in (node["id"], node.get("nativeId"))]
    if not matches:
        raise Rejected("ELEMENT_NOT_FOUND", "指定合成元素不存在")
    if len(matches) != 1:
        raise Rejected("AMBIGUOUS_TARGET", "指定合成目标不唯一")
    return matches[0]


def public(node):
    return {k: copy.deepcopy(v) for k, v in node.items() if k != "action"}


def snapshot_for(scene, args, window):
    snap = scene["state"]["snapshots"].get(args.snapshot)
    if snap is None:
        raise Rejected("SNAPSHOT_NOT_FOUND", "指定合成快照不存在")
    require(isinstance(snap, dict) and isinstance(snap.get("refs"), dict) and isinstance(snap.get("nodes"), list)
            and type(snap.get("complete")) is bool, "已存快照结构无效")
    if snap.get("windowId") != window["id"] or snap.get("page") != window["page"] or snap.get("generation") != window["generation"]:
        raise Rejected("STALE_REF", "合成快照不属于当前窗口/页面")
    return snap


def dispatch(scene, args):
    command, state = args.command, scene["state"]
    if command == "version":
        return {"version": TOOL_VERSION}, {}
    if command == "permissions":
        return copy.deepcopy(scene["permissions"]), {}
    if command == "apps":
        return {"apps": copy.deepcopy(scene["apps"])}, {}
    if command == "windows":
        if args.app and not any(a["id"] == args.app and a["running"] for a in scene["apps"]):
            raise Rejected("APP_NOT_FOUND", "指定合成应用不存在或未运行")
        return [{k: w[k] for k in ("id", "app", "title")} for w in scene["windows"] if not args.app or w["app"] == args.app], {}
    window = window_for(scene, args.window)
    nodes = nodes_for(scene, window)
    if command == "snapshot":
        if args.root:
            root = select(nodes, args.root)["id"]
            parent = {n["id"]: n.get("parent") for n in nodes}
            def within(node):
                current = node["id"]
                while current is not None:
                    if current == root:
                        return True
                    current = parent.get(current)
                return False
            nodes = [node for node in nodes if within(node)]
        else:
            root = "root"
        sid = f"snapshot-{state['nextSnapshot']}"
        state["nextSnapshot"] += 1
        refs = {}
        visible = []
        for index, node in enumerate(nodes, 1):
            ref = f"{sid}:e{index}"
            refs[ref] = node["id"]
            visible.append(dict(public(node), ref=ref))
        complete = scene["pages"][window["page"]].get("complete", True)
        state["snapshots"][sid] = {"windowId": window["id"], "page": window["page"], "generation": window["generation"],
                                    "refs": refs, "nodes": visible, "complete": complete}
        return {"snapshotId": sid, "windowId": window["id"], "root": root, "elements": visible, "complete": complete}, {}
    snap = snapshot_for(scene, args, window)
    if command == "find":
        if not args.query.strip():
            raise Rejected("INVALID_ARGS", "find query不能为空", exit_code=2)
        matches = [n for n in snap["nodes"] if any(args.query.casefold() in str(n.get(key, "")).casefold() for key in ("text", "value", "id", "nativeId"))]
        return {"snapshotId": args.snapshot, "matches": copy.deepcopy(matches), "complete": snap["complete"]}, {}
    if args.ref:
        identifier = snap["refs"].get(args.ref)
        if identifier is None:
            raise Rejected("STALE_REF", "引用不属于指定快照")
    else:
        identifier = select(snap["nodes"], args.id)["id"]
    try:
        node = select(nodes, identifier)
    except Rejected as error:
        raise Rejected("STALE_REF", "引用的元素已不在当前页面") from error
    if command == "get":
        return {"element": public(node)}, {}
    if node.get("enabled") is False:
        raise Rejected("ACTION_NOT_SUPPORTED", "合成目标已禁用")
    effects = {}
    if command in ("set-value", "type"):
        if not node.get("editable"):
            raise Rejected("ACTION_NOT_SUPPORTED", "目标不是合成可编辑输入框")
        if command == "type" and not args.headed:
            raise Rejected("ACTION_NOT_SUPPORTED", "合成Chrome不支持后台type；不自动改用其他输入方式", disposition=UNKNOWN)
        if command == "type" and not args.text:
            raise Rejected("INVALID_ARGS", "type text不能为空", exit_code=2)
        draft = draft_for(scene, window, node)
        draft["displayValue"] = args.value if command == "set-value" else draft["displayValue"] + args.text
        fired = command == "type" or not node.get("contenteditable", False)
        if fired:
            draft["inputValue"] = draft["displayValue"]
            state["inputEvents"].append({"windowId": window["id"], "page": window["page"], "id": node["id"], "method": command, "value": draft["displayValue"]})
        effects = {"inputEvent": fired, "displayValue": draft["displayValue"], "inputValue": draft["inputValue"]}
    else:
        action = node.get("action", {})
        if action.get("kind") == "navigate":
            window["page"] = action["page"]
            window["generation"] += 1
            effects = {"page": window["page"]}
        elif action.get("kind") == "send":
            input_node = select(nodes, action["input"])
            require(input_node.get("editable") is True, "发送按钮关联的输入框不可编辑")
            draft = draft_for(scene, window, input_node)
            thread = scene["pages"][window["page"]]["thread"]
            payload = draft["inputValue"]
            if payload:
                message = {"speaker": "us", "type": "text", "text": payload, "label": state["sendLabel"]}
                if state["deliveryMode"] == "delivered":
                    message["deliveryLabel"] = "[送达]"
                else:
                    message["sending"] = True
                state["messages"].setdefault(thread, []).append(message)
                draft.update(displayValue="", inputValue="")
            effects = {"messageCreated": bool(payload), "thread": thread, "text": payload}
        else:
            raise Rejected("ACTION_NOT_SUPPORTED", "替身未支持此元素动作（含邀约卡按钮）")
    return {"action": command, "disposition": copy.deepcopy(DELIVERED)}, effects


def envelope(command, data=None, error=None):
    out = {"version": VERSION, "command": command, "ok": error is None}
    if error is None:
        out["data"] = data
    else:
        out["error"] = {"code": error.code, "message": str(error), "disposition": error.disposition, "details": error.details}
    return out


def call(scene, argv):
    command = "version" if argv == ["--version"] else argv[0] if argv and argv[0] in OBSERVATIONS | ACTIONS | {"launch"} else "unknown"
    record = {"argv": list(argv), "command": command, "effects": {}}
    scene["state"]["calls"].append(record)
    delay = 0
    try:
        if argv and argv[0] == "launch" and any(a == "--cdp" or a.startswith("--cdp=") for a in argv[1:]):
            raise Rejected("ACTION_NOT_SUPPORTED", "替身硬拒绝launch --cdp；不会调用真实程序")
        args = parse(argv)
        command = args.command
        fault = next((f for f in scene["state"]["faults"] if f["command"] == command), None)
        if fault:
            scene["state"]["faults"].remove(fault)
        phase = fault.get("phase", "before") if fault else None
        if phase != "before" or not fault or fault["kind"] == "incomplete":
            data, record["effects"] = dispatch(scene, args)
        if fault and fault["kind"] == "incomplete":
            require(command in ("snapshot", "find"), "incomplete仅用于snapshot/find")
            data.update(complete=False, truncated=True)
            if command == "snapshot":
                scene["state"]["snapshots"][data["snapshotId"]]["complete"] = False
        elif fault:
            details = {}
            disposition = DELIVERED if phase == "after" and command in ACTIONS else UNKNOWN
            if fault["kind"] == "timeout":
                delay = fault.get("delaySeconds", 1)
                error = Rejected("TIMEOUT", "合成工具超时", disposition=disposition)
            elif fault["kind"] == "locked":
                scene["state"]["locked"] = True
                details["kind"] = "window_without_accessibility_element"
                error = Rejected("WINDOW_NOT_FOUND", "合成锁屏窗口不可访问", disposition=disposition)
            else:
                error = Rejected(fault.get("code", "ACTION_NOT_SUPPORTED"), "合成故障拒绝", disposition=disposition if phase == "after" else SAFE)
            if phase == "after" and command in ACTIONS:
                details["steps"] = [{"action": command, "outcome": "performed"}]
                details["after_action"] = {"disposition": copy.deepcopy(error.disposition)}
            error.details = details
            raise error
        response, code = envelope(command, data=data), 0
    except Rejected as error:
        if error.exit_code == 2:
            command = "unknown"
        response, code = envelope(command, error=error), error.exit_code
    record.update(command=command, response=copy.deepcopy(response), exitCode=code)
    if delay:
        record["timeoutPending"] = True
    return response, code, delay


def main(argv=None):
    fsutil.utf8_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    command = "version" if argv == ["--version"] else argv[0] if argv else "unknown"
    delay = 0
    try:
        path, lock = scenario_path()
        with fsutil.locked(lock):
            # 等锁后复查路径，拒绝变成链接；场景和衍生锁只在系统临时目录。
            current, _ = scenario_path()
            require(current == path, "等待期间场景路径改变")
            scene = read_json(path)
            validate(scene)
            response, code, delay = call(scene, argv)
            fsutil.write_text_atomic(path, json.dumps(scene, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    except Rejected as error:
        delay = 0
        error.details = dict(error.details, argv=argv)
        response, code = envelope(command, error=error), error.exit_code
    except (OSError, ValueError) as error:
        delay = 0
        failure = Rejected("FAKE_SCENARIO_INVALID", "合成场景/状态无法读写：" + str(error), disposition=UNKNOWN, details={"argv": argv})
        response, code = envelope(command, error=failure), 1
    # 持久化调用、消费故障并释放状态锁之后才有限等待，便于真实进程超时测试。
    if delay:
        print("fake-timeout-ready", file=sys.stderr, flush=True)
        time.sleep(delay)
    print(json.dumps(response, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

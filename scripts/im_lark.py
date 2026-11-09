#!/usr/bin/env python3
"""飞书同步适配器：只由 Agent 显式运行，不安装服务、不启动 shell。

notify 保留队列、回执、游标与业务状态机；本模块只做进程边界及原生响应校验。
"""
import argparse
import contextlib
from contextvars import ContextVar
import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fsutil
import im_scan
import ledger
import notify

TIMEOUT = 20
MAX_PAGES = 20
CALL_DEADLINE = ContextVar("im_call_deadline", default=None)
API_MESSAGES = "/open-apis/im/v1/messages"
REJECT_CODES = {99992351}  # 仅实测发送拒绝；其他非零退出不证明未投递。
BAD_SUFFIXES = {".cmd", ".bat", ".ps1", ".psm1", ".vbs", ".vbe", ".wsf", ".wsh", ".hta", ".sh", ".com"}
HOSTS = {"cmd", "powershell", "pwsh", "sh", "bash", "zsh", "dash", "ksh", "fish", "csh", "tcsh",
         "wscript", "cscript", "mshta", "rundll32", "regsvr32", "env", "busybox", "xargs", "sudo",
         "perl", "ruby", "php", "lua", "luajit", "tclsh", "rscript", "osascript"}
NPM_TEMPLATE = r'''@ECHO off
GOTO start
:find_dp0
SET dp0=%~dp0
EXIT /b
:start
SETLOCAL
CALL :find_dp0
IF EXIST "%dp0%\node.exe" (
  SET "_prog=%dp0%\node.exe"
) ELSE (
  SET "_prog=node"
  SET PATHEXT=%PATHEXT:;.JS;=;%
)
endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%" "%dp0%\@SCRIPT@" %*
'''


class LarkError(Exception):
    """只携带本模块固定诊断码，不传递远端正文、命令行或凭据。"""


def require(ok, code):
    if not ok:
        raise LarkError(code)


def strict_json(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, "duplicate_json_key")
            value[key] = item
        return value
    def invalid(_):
        raise LarkError("invalid_json_constant")
    def finite(raw_number):
        value = float(raw_number)
        require(math.isfinite(value), "invalid_json_number")
        return value
    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid, parse_float=finite)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise LarkError("invalid_json") from None


def safe_name(path):
    name = Path(path).name.lower()
    stem = Path(name).stem
    require(Path(name).suffix not in BAD_SUFFIXES and name not in HOSTS and stem not in HOSTS, "unsafe_executable")


def locate(raw):
    require(isinstance(raw, str) and bool(raw) and not any(c in raw for c in "\x00\r\n"), "invalid_executable")
    safe_name(raw)
    path = Path(raw)
    if not path.is_absolute() and not any(c in raw for c in ("/", "\\")):
        found = shutil.which(raw)
        require(found is not None, "executable_not_found")
        path = Path(found)
    path = path.resolve(strict=True)
    safe_name(path)
    require(path.is_file(), "executable_not_file")
    return path


def script_path(raw, suffix):
    path = Path(raw)
    require(path.is_absolute() and path.suffix.lower() == suffix, "fixed_script_required")
    path = path.resolve(strict=True)
    require(path.is_file() and path.suffix.lower() == suffix, "unsafe_script_target")
    return path


def validate_prefix(candidate, windows=None):
    """来源无关的最终校验；解释器只允许一个固定脚本，不接受解释器开关。"""
    windows = os.name == "nt" if windows is None else windows
    require(isinstance(candidate, (list, tuple)) and candidate and all(isinstance(v, str) and v for v in candidate), "invalid_command")
    exe = locate(candidate[0])
    require(not windows or exe.suffix.lower() == ".exe", "windows_exe_required")
    require(os.access(exe, os.X_OK), "executable_not_runnable")
    with exe.open("rb") as stream:
        require(not stream.read(2).startswith(b"#!"), "script_host_entry_refused")
    name = exe.stem.lower() if exe.suffix.lower() == ".exe" else exe.name.lower()
    if name == "node":
        require(len(candidate) == 2, "fixed_node_script_required")
        return [str(exe), str(script_path(candidate[1], ".js"))]
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", name):
        require(os.environ.get("JOB_MATE_TESTING") == "1" and len(candidate) == 2, "python_test_script_only")
        return [str(exe), str(script_path(candidate[1], ".py"))]
    require(len(candidate) == 1, "direct_executable_only")
    return [str(exe)]


def npm_command(wrapper, windows):
    wrapper = Path(wrapper).resolve(strict=True)
    require(wrapper.is_file() and wrapper.suffix.lower() == ".cmd", "unsupported_npm_wrapper")
    body = wrapper.read_bytes().decode("utf-8").replace("\r\n", "\n")
    pattern = re.escape(NPM_TEMPLATE).replace(re.escape("@SCRIPT@"), r'(?P<script>[^\r\n"]+)')
    match = re.fullmatch(pattern, body)
    require(match is not None, "unsupported_npm_wrapper")
    relative = match.group("script")
    parts = re.split(r"[\\/]", relative)
    require(re.fullmatch(r"[A-Za-z0-9_@. /\\-]+", relative) is not None
            and not PureWindowsPath(relative).drive and not PureWindowsPath(relative).is_absolute()
            and all(p not in ("", ".", "..") for p in parts), "unsafe_npm_script")
    script = script_path(str(wrapper.parent.joinpath(*parts)), ".js")
    require(script.is_relative_to(wrapper.parent), "npm_script_escape")
    local_node = wrapper.parent / "node.exe"
    node = str(local_node) if local_node.exists() or local_node.is_symlink() else shutil.which("node")
    require(node is not None, "node_not_found")
    return validate_prefix([node, str(script)], windows)


def resolve_lark_command(profile_im, windows=None):
    windows = os.name == "nt" if windows is None else windows
    configured = profile_im.get("larkCommand")
    try:
        if configured is not None:
            return validate_prefix(configured, windows)
        found = shutil.which("lark-cli")
        require(found is not None, "lark_cli_not_found")
        # 只有自动发现的、原名就是 .cmd 的完整 npm 包装可作文本解析。
        if windows and Path(found).suffix.lower() == ".cmd":
            return npm_command(found, windows)
        target = locate(found)
        if not windows:
            with target.open("rb") as stream:
                first = stream.readline(256).rstrip(b"\r\n")
            if first in (b"#!/usr/bin/env node", b"#!/usr/bin/node"):
                return validate_prefix(["node", str(script_path(str(target), ".js"))], windows)
        return validate_prefix([found], windows)
    except (OSError, UnicodeError, ValueError, RuntimeError):
        raise LarkError("command_resolution_failed") from None


def run_lark(command, arguments, windows=None):
    """调用前再校验固定入口；变量正文永远是脚本之后的独立 argv。"""
    try:
        prefix = validate_prefix(command, windows)
        env = dict(os.environ)
        for key in ("NODE_OPTIONS", "NODE_PATH", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONINSPECT", "BASH_ENV", "ENV"):
            env.pop(key, None)
        env.update(PYTHONUTF8="1", LARKSUITE_CLI_NO_UPDATE_NOTIFIER="1", LARKSUITE_CLI_NO_SKILLS_NOTIFIER="1")
        deadline = CALL_DEADLINE.get()
        timeout = TIMEOUT if deadline is None else min(TIMEOUT, deadline - time.monotonic())
        require(timeout > 0, "lark_window_expired")
        return subprocess.run([*prefix, *arguments], shell=False, timeout=timeout,
                              capture_output=True, encoding="utf-8", env=env)
    except subprocess.TimeoutExpired:
        raise LarkError("lark_timeout") from None
    except (OSError, UnicodeError, ValueError, RuntimeError):
        raise LarkError("lark_process_error") from None


def data_of(response):
    require(response.returncode == 0, "lark_nonzero")
    require(not response.stderr.strip(), "unexpected_im_stderr")
    value = strict_json(response.stdout)
    require(isinstance(value, dict) and value.get("ok") is True and value.get("identity") == "bot"
            and isinstance(value.get("data"), dict) and "error" not in value, "invalid_im_envelope")
    return value["data"]


def platform_id(value, family):
    return isinstance(value, str) and re.fullmatch(family + r"_[0-9a-f]{32}", value) is not None


def millis(value):
    require(isinstance(value, str) and re.fullmatch(r"(?:0|[1-9][0-9]{0,14})", value) is not None, "invalid_epoch_milliseconds")
    number = int(value)
    require(notify.valid_ms(number) and number <= notify.now_ms() + 300000, "epoch_milliseconds_out_of_range")
    return number


def cli_call(ws, mode, command, profile, arguments):
    ledger.version_gate(ws, mode)
    return run_lark(command, ["--profile", profile, *arguments])


def doctor(ws):
    cfg = notify.context(ws)["cfg"]
    if cfg["channel"] == "none":
        return {"ok": True, "checks": [], "skipped": "channel_none"}
    checks = []
    stage = "executable"
    try:
        command = resolve_lark_command(cfg)
        checks.append({"name": stage, "ok": True})
        stage = "version"
        version = cli_call(ws, "read", command, cfg["larkProfile"], ["--version"])
        require(version.returncode == 0 and re.fullmatch(r"lark-cli version 1\.0\.96\r?\n?", version.stdout) is not None, "unsupported_lark_version")
        checks.append({"name": stage, "ok": True, "version": "1.0.96"})
        stage = "profile"
        response = cli_call(ws, "read", command, cfg["larkProfile"], ["profile", "list"])
        require(response.returncode == 0, "profile_query_failed")
        profiles = strict_json(response.stdout)
        require(isinstance(profiles, list) and all(isinstance(p, dict) and notify.nonempty(p.get("name")) for p in profiles), "invalid_profile_list")
        selected = [p for p in profiles if p["name"] == cfg["larkProfile"]]
        require(len(selected) == 1 and notify.nonempty(selected[0].get("appId")), "profile_not_found_or_ambiguous")
        checks.append({"name": stage, "ok": True})
        stage = "bot"
        response = cli_call(ws, "read", command, cfg["larkProfile"], ["auth", "status", "--json", "--verify"])
        require(response.returncode == 0, "bot_verification_failed")
        auth = strict_json(response.stdout)
        require(isinstance(auth, dict) and auth.get("appId") == selected[0]["appId"], "auth_profile_mismatch")
        identities = auth.get("identities")
        bot = identities.get("bot") if isinstance(identities, dict) else None
        require(isinstance(bot, dict) and bot.get("verified") is True and bot.get("available") is True, "bot_not_verified")
        require(cfg["ownerId"] != bot.get("openId"), "bot_is_not_owner")
        checks.append({"name": stage, "ok": True})
    except LarkError as error:
        checks.append({"name": stage, "ok": False, "error": str(error)[:500],
                       "hint": "核对直接可执行入口或配置 [node, JS绝对路径]；机器人权限不通过用户登录修复。"})
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


def add_error(result, code):
    # 诊断只由固定码构成，同时限制条数与总字符，永不拼接远端错误正文。
    if code not in result["errors"] and sum(map(len, result["errors"])) + len(code) <= 500:
        result["errors"].append(code[:500])


def definitely_rejected(response):
    if response.returncode == 0 or response.stdout.strip():
        return False
    try:
        value = strict_json(response.stderr)
        error = value.get("error") if isinstance(value, dict) else None
        return (value.get("ok") is False and value.get("identity") == "bot" and "data" not in value and isinstance(error, dict)
                and type(error.get("code")) is int and error["code"] in REJECT_CODES)
    except (LarkError, AttributeError):
        return False


def sent_time(ws, command, entry, mid, chat):
    response = cli_call(ws, "write", command, entry["larkProfile"],
                        ["api", "GET", API_MESSAGES + "/" + mid, "--as", "bot", "--format", "json"])
    items = data_of(response).get("items")
    require(isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict), "invalid_message_lookup")
    message = items[0]
    require(message.get("message_id") == mid and message.get("chat_id") == chat, "message_lookup_mismatch")
    return millis(message.get("create_time"))


def drain(ws, ctx, command, result, seen):
    while True:
        with ledger.gated(ws, "write"):
            require(notify.context(ws)["cfg"] == ctx["cfg"], "im_config_changed")
            entries = notify.outbox(ws, ctx, limit=1, claim=True, exclude=seen)["entries"]
        if not entries:
            return
        entry = entries[0]
        seen.add(entry["key"])
        notify.scan_crash("sync_after_claim")
        profile = entry["larkProfile"]
        args = ["im", "+messages-reply", "--message-id", entry["replyTo"]] if entry["replyTo"] else ["im", "+messages-send", "--user-id", entry["ownerId"]]
        args += ["--as", "bot", "--text", entry["text"], "--idempotency-key", entry["idempotencyKey"], "--format", "json"]
        status, mid, chat, created, error = "unknown", None, None, None, None
        try:
            response = cli_call(ws, "write", command, profile, args)
            if definitely_rejected(response):
                status, error = "failed", "lark_rejected_99992351"
            else:
                data = data_of(response)
                require(platform_id(data.get("message_id"), "om") and platform_id(data.get("chat_id"), "oc"), "missing_or_invalid_send_identity")
                status, mid, chat = "sent", data["message_id"], data["chat_id"]
                try:
                    created = millis(data.get("create_time"))
                except LarkError:
                    try:
                        created = sent_time(ws, command, entry, mid, chat)
                    except LarkError:
                        add_error(result, "sent_without_trusted_time")
        except LarkError as failure:
            error = str(failure)
        if error:
            add_error(result, error)
        notify.scan_crash("sync_after_remote")
        with ledger.gated(ws, "write"):
            notify.receipt(ws, ctx, key=entry["key"], token=entry["token"], result=status,
                           message_id=mid, chat_id=chat, create_time=created, error=error)
        result[status] += 1
        notify.scan_crash("sync_after_receipt")


def native_message(item, plan, lower, upper):
    require(isinstance(item, dict) and platform_id(item.get("message_id"), "om"), "invalid_message_id")
    require(item.get("chat_id") == plan["chatId"], "foreign_chat")
    created = millis(item.get("create_time"))
    require(lower <= created < upper, "message_outside_window")
    sender = item.get("sender")
    require(isinstance(sender, dict) and all(notify.nonempty(sender.get(k)) for k in ("id", "id_type", "sender_type")), "invalid_sender")
    require(type(item.get("deleted")) is bool, "invalid_deleted")
    if "updated" in item:
        require(type(item["updated"]) is bool, "invalid_updated")
    for key in ("parent_id", "root_id"):
        require(key not in item or platform_id(item[key], "om"), "invalid_parent_id")
    require("msg_type" not in item and item["deleted"] or notify.nonempty(item.get("msg_type")), "invalid_message_type")
    if sender["sender_type"] != "user" or sender["id_type"] != "open_id" or sender["id"] != plan["ownerId"]:
        return created, None
    text = ""
    if not item["deleted"]:
        require(item["msg_type"] == "text", "unsupported_owner_message_type")
        body = item.get("body")
        require(isinstance(body, dict) and isinstance(body.get("content"), str), "invalid_message_body")
        content = strict_json(body["content"])
        require(isinstance(content, dict) and isinstance(content.get("text"), str), "invalid_text_content")
        text = content["text"]
    return created, {"messageId": item["message_id"], "chatId": item["chat_id"], "createTime": created,
                     "senderId": sender["id"], "senderType": "user", "text": text,
                     "parentId": item.get("parent_id"), "deleted": item["deleted"]}


def fetch(ws, ctx, command, start_ms, max_pages=None):
    max_pages = MAX_PAGES if max_pages is None else max_pages
    with ledger.gated(ws, "write"):
        require(notify.context(ws)["cfg"] == ctx["cfg"], "im_config_changed")
        plan = notify.poll(ws, ctx)
        if not plan["readable"]:
            return {"skipped": "not_readable"}, False
        scan = notify.scan_open(ws, ctx, start_ms)
    if scan is None:
        return {"skipped": "boundary_after_sync_start"}, False
    lower, upper = scan["startMs"], scan["endMs"]
    params = {"container_id_type": "chat", "container_id": scan["chatId"], "start_time": str(lower // 1000),
              "end_time": str(upper // 1000), "sort_type": "ByCreateTimeAsc", "page_size": 50}
    if scan["nextToken"] is not None:
        params["page_token"] = scan["nextToken"]
    messages, tokens, versions = [], list(scan["seenTokens"]), dict(scan["versions"])
    previous, pages = scan["lastMs"], scan["pageCount"]
    complete = False
    for _ in range(max_pages):
        response = cli_call(ws, "write", command, plan["larkProfile"],
                            ["api", "GET", API_MESSAGES, "--as", "bot", "--params", notify.canonical(params), "--format", "json"])
        data = data_of(response)
        require(isinstance(data.get("items"), list) and len(data["items"]) <= 50
                and type(data.get("has_more")) is bool, "invalid_page")
        token = data.get("page_token")
        require(isinstance(token, str), "invalid_page_token")
        if data["has_more"]:
            require(bool(token) and token not in tokens and bool(data["items"]), "missing_or_repeated_page_token")
        else:
            require(token == "", "unexpected_terminal_token")
        pages += 1
        for item in data["items"]:
            created, message = native_message(item, plan, lower, upper)
            require(created >= previous, "page_order_gap")
            previous = created
            mid, version = item["message_id"], im_scan.digest(item)
            require(mid not in versions or versions[mid] == version, "conflicting_message_version")
            observed = scan.get("observedVersions", {})
            require(mid not in observed or observed[mid] == version, "conflicting_message_version")
            if mid not in versions and message is not None:
                messages.append(message)
            versions[mid] = version
        if not data["has_more"]:
            complete = True
            break
        tokens.append(token)
        params["page_token"] = token
    batch = {"since": lower, "complete": complete, "messages": messages}
    progress = im_scan.progress(scan, None if complete else tokens[-1], tokens, previous, versions, pages, complete)
    return notify.scan_accept(ws, ctx, scan, progress, batch), not complete


def sync(ws, send_only=False, fetch_only=False, max_pages=None):
    ctx = notify.context(ws)
    if ctx["cfg"]["channel"] == "none":
        return {"skipped": "channel_none"}
    with fsutil.try_locked(ws / "ledger/.im-sync.lock") as acquired:
        if not acquired:
            return {"busy": True, "incomplete": True, "incoming": {"new": [], "skipped": "busy"},
                    "sent": 0, "failed": 0, "unknown": 0, "errors": []}
        with ledger.gated(ws, "write"):
            ctx, start = notify.context(ws), notify.now_ms()
            ctx["profileDigest"] = hashlib.sha256((ws / "profile.json").read_bytes()).hexdigest()
        return sync_locked(ws, ctx, start, send_only, fetch_only, max_pages)


def sync_locked(ws, ctx, start, send_only, fetch_only, max_pages):
    # im-sync 只串行适配器；每个本地事务分别取 mutation，网络不持业务锁。
    command = resolve_lark_command(ctx["cfg"])  # 任何认领之前完成；失败不改变队列。
    result = {"incoming": {}, "sent": 0, "failed": 0, "unknown": 0, "incomplete": False, "errors": []}
    seen = set()
    if not fetch_only:
        drain(ws, ctx, command, result, seen)
    if not send_only:
        try:
            result["incoming"], result["incomplete"] = fetch(ws, ctx, command, start, max_pages)
        except (LarkError, im_scan.ScanError) as error:
            result["incomplete"] = True
            add_error(result, str(error))
        if not fetch_only:
            drain(ws, ctx, command, result, seen)  # 本次新排队的确认；failed 最早下一轮重试。
    return result


def watch_state(ws):
    ctx = notify.context(ws)
    return ctx, notify.reply_watch(ws, ctx, fsutil.now(ctx["tz"]))


def watch_probe(ws):
    try:
        with fsutil.try_locked(ws / "ledger/.im-watch.lock", create=False) as acquired:
            return not acquired, "not_running" if acquired else "running"
    except FileNotFoundError:
        return False, "not_running"
    except OSError:
        return None, "watch_lock_unknown"


def watch_event(ws, ctx, state, event, reason=None, message_ids=None):
    row = {"at": ledger.now_iso(ctx["tz"]), "pid": os.getpid(), "event": event,
           "reason": reason, "count": len(state["waiting"]), "deadlineAt": state["deadlineAt"]}
    if message_ids:
        row.update(workspace=str(ws.resolve()), route=ctx["cfg"]["route"], messageIds=message_ids)
    line = notify.canonical(row) + "\n"
    path = ws / "ledger/im-watch.log"
    if path.exists() and path.stat().st_size + len(line.encode("utf-8")) > 1024 * 1024:
        os.replace(path, path.with_name(path.name + ".1"))
    with path.open("a", encoding="utf-8", newline="") as stream:
        stream.write(line)
        stream.flush()
    try:
        print(line, end="", flush=True)
    except BrokenPipeError:
        pass  # 日志仍可供无即时事件入口的 runtime 查询。


def start_ack(ws, args, reason):
    if args.child_token is not None:
        path = ws / "ledger" / (".im-watch-start-" + args.child_token + ".json")
        fsutil.write_text_atomic(path, notify.canonical({"pid": os.getpid(), "token": args.child_token, "reason": reason}))


def watch_loop(ws, args):
    if args.max_ticks is not None:
        require(os.environ.get("JOB_MATE_TESTING") == "1" and args.max_ticks > 0, "test_ticks_refused")
    require(1 <= args.interval <= 60, "watch_interval_invalid")
    if args.child_token is not None or args.child_deadline is not None:
        require(isinstance(args.child_token, str) and re.fullmatch(r"[0-9a-f]{32}", args.child_token) is not None
                and os.environ.get("JOB_MATE_WATCH_CHILD_TOKEN") == args.child_token
                and isinstance(args.child_deadline, float) and math.isfinite(args.child_deadline)
                and 0 < args.child_deadline <= time.monotonic() + 5, "watch_child_arguments_invalid")
        if os.environ.get("JOB_MATE_TESTING") == "1":
            delay = float(os.environ.get("JOB_MATE_WATCH_START_DELAY", "0"))
            require(0 <= delay <= 10, "watch_test_delay_invalid")
            time.sleep(delay)
        if time.monotonic() >= args.child_deadline:
            return {"reason": "watch_start_expired"}, 1
    with contextlib.ExitStack() as lifetime:
        acquired = lifetime.enter_context(fsutil.try_locked(ws / "ledger/.im-watch.lock"))
        if not acquired:
            start_ack(ws, args, "already_running")
            return {"reason": "already_running"}, 3
        try:
            with ledger.gated(ws, "write"):
                ctx, state = watch_state(ws)
                if not state["active"]:
                    start_ack(ws, args, "inactive")
                    lifetime.close()
                    return {"reason": "expired" if state["expired"] else "inactive"}, 11 if state["expired"] else 0
                if args.child_deadline is not None and time.monotonic() >= args.child_deadline:
                    return {"reason": "watch_start_expired"}, 1
                start_ack(ws, args, "started")
            watch_event(ws, ctx, state, "watch_started")
            seen, ticks = set(), 0
            while True:
                ctx, state = watch_state(ws)
                replies = notify.pending_replies(notify.sync_view(ws, ctx["cfg"]))
                ids = sorted(r["messageId"] for r in replies if r["messageId"] not in seen)
                for offset in range(0, len(ids), 100):
                    watch_event(ws, ctx, state, "reply_available", message_ids=ids[offset:offset + 100])
                seen.update(ids)
                if not state["active"]:
                    # 与 ensure 的本地准入共用 mutation；复查和释放生命周期锁不可穿插。
                    with ledger.gated(ws, "write"):
                        ctx, state = watch_state(ws)
                        if state["active"]:
                            continue
                        lifetime.close()
                    reason = "expired" if state["expired"] else ("disabled" if ctx["cfg"]["waitMinutes"] == 0
                             or ctx["cfg"]["channel"] == "none" else "cleared")
                    watch_event(ws, ctx, state, "watch_exit", reason)
                    return {"reason": reason}, 11 if state["expired"] else 0
                remaining = max(0, (datetime_deadline(state) - fsutil.now(ctx["tz"])).total_seconds())
                token = CALL_DEADLINE.set(time.monotonic() + remaining)
                try:
                    if notify.poll(ws, ctx)["readable"]:
                        result = sync(ws, fetch_only=True, max_pages=1)
                        reason = "sync_busy" if result.get("busy") else ("im_incomplete" if result.get("incomplete") else "synced")
                    else:
                        reason = "not_readable"
                finally:
                    CALL_DEADLINE.reset(token)
                ctx, state = watch_state(ws)
                watch_event(ws, ctx, state, "watch_tick", reason)
                ticks += 1
                # 下一拍首先提示刚落盘的回复，即便集合已清空；不在这里提前退出。
                if args.max_ticks is not None and ticks >= args.max_ticks:
                    replies = notify.pending_replies(notify.sync_view(ws, ctx["cfg"]))
                    ids = sorted(r["messageId"] for r in replies if r["messageId"] not in seen)
                    for offset in range(0, len(ids), 100):
                        watch_event(ws, ctx, state, "reply_available", message_ids=ids[offset:offset + 100])
                    watch_event(ws, ctx, state, "watch_exit", "test_tick_limit")
                    return {"reason": "test_tick_limit"}, 0
                if not state["active"]:
                    continue
                remaining = max(0, (datetime_deadline(state) - fsutil.now(ctx["tz"])).total_seconds())
                time.sleep(min(args.interval, remaining))
        except (notify.NotifyError, ledger.LedgerError, LarkError, OSError, ValueError) as error:
            start_ack(ws, args, "error")
            # 固定码，不把配置、消息或外部错误正文写日志。
            try:
                watch_event(ws, ctx, state, "watch_exit", "watch_local_state_invalid")
            except (UnboundLocalError, OSError):
                pass
            return {"reason": "watch_local_state_invalid"}, 2


def datetime_deadline(state):
    from datetime import datetime
    return datetime.fromisoformat(state["deadlineAt"])


def spawn_watch(ws, interval, token, deadline, max_ticks=None):
    """唯一自启动入口：固定当前解释器、当前脚本与 watch 参数，不接受命令模板。"""
    argv = [sys.executable, str(Path(__file__).resolve()), "--workspace", str(ws.resolve()),
            "watch", "--interval", str(interval), "--child-token", token, "--child-deadline", str(deadline)]
    if max_ticks is not None:
        argv += ["--max-ticks", str(max_ticks)]
    env = dict(os.environ, JOB_MATE_WATCH_CHILD_TOKEN=token)
    flags = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    return subprocess.Popen(argv, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, close_fds=True, cwd=str(ws.resolve()), env=env, **flags)


def detach_watch(ws, args):
    require(1 <= args.interval <= 60, "watch_interval_invalid")
    require(args.max_ticks is None or (os.environ.get("JOB_MATE_TESTING") == "1" and args.max_ticks > 0), "test_ticks_refused")
    require(args.child_token is None and args.child_deadline is None, "watch_child_arguments_invalid")
    with ledger.gated(ws, "write"):
        _, state = watch_state(ws)
        running, reason = watch_probe(ws)
        if running is not False:
            return {"started": False, "alreadyRunning": running is True, "reason": reason, "pid": None}, 0 if running else 1
        if not state["active"]:
            return {"started": False, "alreadyRunning": False, "reason": "inactive", "pid": None}, 0
    token, deadline = uuid.uuid4().hex, time.monotonic() + 5
    path = ws / "ledger" / (".im-watch-start-" + token + ".json")
    try:
        child = spawn_watch(ws, args.interval, token, deadline, args.max_ticks)
    except (OSError, ValueError):
        return {"started": False, "alreadyRunning": False, "reason": "watch_spawn_failed", "pid": None}, 1
    try:
        while time.monotonic() < deadline:
            if path.exists():
                ack = strict_json(path.read_text(encoding="utf-8"))
                require(ack.get("token") == token and ack.get("pid") == child.pid, "watch_handshake_invalid")
                reason = ack.get("reason")
                require(reason in {"started", "already_running", "inactive", "error"}, "watch_handshake_invalid")
                return {"started": reason == "started", "alreadyRunning": reason == "already_running",
                        "reason": reason, "pid": child.pid if reason == "started" else None}, 0 if reason != "error" else 2
            if child.poll() is not None:
                return {"started": False, "alreadyRunning": False, "reason": "watch_start_failed", "pid": None}, 1
            time.sleep(.01)
        # 仅对自己刚启动、仍有句柄的子进程请求结束；绝不抢别人的实例锁。
        try:
            child.terminate()
            child.wait(timeout=1)
            reason = "watch_start_timeout"
        except (OSError, subprocess.TimeoutExpired):
            reason = "watch_start_unknown"
        return {"started": False, "alreadyRunning": False, "reason": reason, "pid": None}, 1
    finally:
        path.unlink(missing_ok=True)


def cmd_watch(args, ws):
    require(1 <= args.interval <= 60, "watch_interval_invalid")
    if args.status:
        require(args.child_token is None and args.child_deadline is None and args.max_ticks is None,
                "watch_child_arguments_invalid")
        _, state = watch_state(ws)
        running, reason = watch_probe(ws)
        return watch_emit({"running": running, "replyWatch": state, "reason": reason})
    result, code = detach_watch(ws, args) if args.detach else watch_loop(ws, args)
    return watch_emit(result, code)


def watch_emit(result, code=0):
    print(notify.canonical(result), flush=True)
    return code


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    parser.add_argument("--workspace")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", allow_abbrev=False)
    action = sub.add_parser("sync", allow_abbrev=False)
    modes = action.add_mutually_exclusive_group()
    modes.add_argument("--send-only", action="store_true")
    modes.add_argument("--fetch-only", action="store_true")
    watch = sub.add_parser("watch", allow_abbrev=False)
    controls = watch.add_mutually_exclusive_group()
    controls.add_argument("--detach", action="store_true")
    controls.add_argument("--status", action="store_true")
    watch.add_argument("--interval", type=int, default=60)
    watch.add_argument("--max-ticks", type=int, help=argparse.SUPPRESS)
    watch.add_argument("--child-token", help=argparse.SUPPRESS)
    watch.add_argument("--child-deadline", type=float, help=argparse.SUPPRESS)
    return parser


def cmd_doctor(args, ws):
    result = doctor(ws)
    return ledger.emit(result, 0 if result["ok"] else 1)


def cmd_sync(args, ws):
    return ledger.emit(sync(ws, args.send_only, args.fetch_only))


COMMANDS = {"doctor": cmd_doctor, "sync": cmd_sync, "watch": cmd_watch}


def entry_mode(args):
    return "read" if args.cmd == "doctor" else "write"


def main(argv=None):
    fsutil.utf8_stdio()
    args = build_parser().parse_args(argv)
    emit = watch_emit if args.cmd == "watch" else ledger.emit
    try:
        ws = ledger.find_workspace(args.workspace)
        # sync 自己只在本地事务期间取得写锁；main 不把网络包进长 gate。
        with ledger.gated(ws, "read"):
            return COMMANDS[args.cmd](args, ws)
    except ledger.VersionGateError as error:
        return emit({"error": error.code, "code": error.code}, 2)
    except LarkError as error:
        return emit({"error": str(error)[:500]}, 2)
    except (notify.NotifyError, ledger.LedgerError, OSError, UnicodeError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return emit({"error": "im_local_state_invalid"}, 2)


if __name__ == "__main__":
    sys.exit(main())

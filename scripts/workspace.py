#!/usr/bin/env python3
"""job-mate 工作区工具：建立骨架、setup 进度、体检、备份与恢复配置文件。

仅用 Python 标准库。所有命令向 stdout 输出一个 JSON 对象。
退出码：0 成功；1 体检发现问题；2 错误（参数、路径）。
"""

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fsutil  # noqa: E402
import ledger  # noqa: E402
import notify  # noqa: E402
import config_schema  # noqa: E402
import config_txn  # noqa: E402

MARKER = ledger.MARKER
CONFIG_FILES = list(config_txn.CONFIG_FILES)
PROGRESS_FILE = "setup-progress.json"

# 标准流程的唯一定义；SKILL.md 的步骤表由测试核对顺序。
STEPS = [
    {"id": "directory", "phase": "prepare", "name": "工作区目录", "required": True},
    {"id": "materials", "phase": "prepare", "name": "现成材料", "required": False},
    {"id": "account", "phase": "prepare", "name": "平台账号", "required": True},
    {"id": "browser", "phase": "prepare", "name": "浏览器操作", "required": False,
     "default": config_schema.default_profile()["browser"]},
    {"id": "roles", "phase": "criteria", "name": "目标职位", "required": True,
     "review": ["greeting"]},
    {"id": "locations", "phase": "criteria", "name": "城市", "required": True},
    {"id": "salary", "phase": "criteria", "name": "薪资", "required": False},
    {"id": "requirements", "phase": "criteria", "name": "硬性要求", "required": False},
    {"id": "exclusions", "phase": "criteria", "name": "排除条件", "required": False},
    {"id": "companies", "phase": "criteria", "name": "排除的公司", "required": False},
    {"id": "activity", "phase": "criteria", "name": "招聘者活跃度", "required": False,
     "default": config_schema.default_profile()["recruiterActivity"]},
    {"id": "candidate", "phase": "facts", "name": "个人事实", "required": True,
     "depends": ["materials"], "review": ["qa", "style", "greeting"]},
    {"id": "resumes", "phase": "facts", "name": "简历版本", "required": True,
     "depends": ["materials"]},
    {"id": "qa", "phase": "facts", "name": "问答口径", "required": False},
    {"id": "style", "phase": "communication", "name": "沟通风格", "required": False,
     "default": config_schema.default_profile()["style"], "depends": ["greeting"], "review": ["greeting"]},
    {"id": "inquiry", "phase": "communication", "name": "优先了解的信息", "required": False,
     "default": config_schema.default_profile()["inquiry"]},
    {"id": "greeting", "phase": "communication", "name": "招呼语", "required": True},
    {"id": "applyAuth", "phase": "authorization", "name": "首次沟通授权", "required": False,
     "default": config_schema.default_profile()["authorization"]["apply"]},
    {"id": "replyLevel", "phase": "authorization", "name": "代答级别", "required": False,
     "default": config_schema.default_profile()["authorization"]["followup"]["reply"]},
    {"id": "nudge", "phase": "authorization", "name": "主动跟进", "required": False,
     "default": {"authorization": {"followup": {"nudge": config_schema.default_profile()["authorization"]["followup"]["nudge"]}},
                 "nudge": config_schema.default_profile()["nudge"]}},
    {"id": "pacing", "phase": "authorization", "name": "限额与时段", "required": False,
     "default": {k: config_schema.default_profile()[k] for k in ("limits", "schedule")}},
    {"id": "im", "phase": "notify", "name": "飞书通知", "required": False},
]
TYPICAL_DEFAULT_STEPS = ("browser", "activity", "inquiry", "pacing")
STEP_BY_ID = {s["id"]: s for s in STEPS}

effective_profile = ledger.effective_profile


class WorkspaceError(Exception):
    pass


def emit(obj, code=0):
    return ledger.emit(obj, code)


def now_stamp():
    return fsutil.now().strftime("%Y%m%d-%H%M%S")


# ---------- init ----------

def init_path(path):
    ws = Path(path).expanduser().resolve()
    home = Path.home().resolve()
    if ws == home or ws == Path(ws.anchor):
        raise WorkspaceError(f"不在 {ws} 根部建立工作区，请另选一个目录")
    if (ws / "scripts" / "ledger.py").is_file() and (ws / "skills" / "apply" / "SKILL.md").is_file():
        raise WorkspaceError("这是 job-mate 自身目录，不在此建立工作区")
    return ws


def parse_accounts(values):
    accounts = {}
    for value in values:
        parts = value.split(":", 2)
        if len(parts) != 3 or any(not part.strip() for part in parts):
            raise WorkspaceError("--account 须为 platform:accountKey:displayName，分量不能为空")
        platform, key, name = parts
        ledger.load_platform(platform)
        if platform in accounts:
            raise WorkspaceError("当前每个平台只能配置一个账号，不能重复 --account 平台")
        accounts[platform] = {"accountKey": key, "displayName": name}
    return accounts


def init_workspace(path, accounts=None):
    accounts = {} if accounts is None else accounts
    if not isinstance(accounts, dict):
        raise WorkspaceError("accounts 须为每平台单账号对象")
    for platform, account in accounts.items():
        ledger.load_platform(platform)
        if not isinstance(account, dict) or not isinstance(account.get("accountKey"), str) or not account["accountKey"].strip():
            raise WorkspaceError("账号配置无效")
    ws = init_path(path)
    with ledger.gated(ws, "write", allow_create=True):
        return create_skeleton(ws, accounts)


def create_skeleton(ws, accounts):
    """调用方已持有创建准入临界区。"""
    ws.mkdir(parents=True, exist_ok=True)
    created = []
    marker = ws / MARKER
    if not marker.exists():
        marker.write_text(json.dumps({
            "schemaVersion": 1,
            "suiteVersion": ledger.SUITE_VERSION,
            "createdAt": fsutil.now().astimezone().isoformat(timespec="seconds"),
            "accounts": accounts,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        created.append(MARKER)
    for d in ("ledger", "inbox", "resumes"):
        if not (ws / d).exists():
            (ws / d).mkdir()
            created.append(d + "/")
    decisions = ws / "inbox" / "decisions.md"
    if not decisions.exists():
        decisions.write_text("# 待本人决定\n", encoding="utf-8")
        created.append("inbox/decisions.md")
    return {"workspace": str(ws), "created": created}


def cmd_init(args):
    return emit(init_workspace(args.path, parse_accounts(args.account)))


# ---------- check ----------

def check_profile(p, problems, warnings):
    issues = config_schema.profile_issues(p)
    problems.extend(issues)
    warnings.extend(config_schema.profile_warnings(p))
    if not issues:
        effective_profile(p)  # 与运行时相同的默认合并；不写回、不生成个人事实。


def check_im(p, bad, warnings):
    try:
        notify.im_config(p)
    except notify.NotifyError as e:
        bad("im", str(e))


def meaningful_lines(text):
    return [(no, line) for no, line in enumerate(text.splitlines(), 1)
            if line.strip() and not line.lstrip().startswith("#")]


def scan_yaml_list(path, required):
    """不依赖 YAML 库的轻量检查：按「- 」开头切分条目，检查必填键。
    文件只有注释或写成 [] 表示明确的空库；有内容却识别不出任何条目视为格式错误。"""
    problems, entries, cur = [], [], None
    lines = meaningful_lines(path.read_text(encoding="utf-8"))
    for no, line in lines:
        if line.startswith("- "):
            cur = {"line": no, "keys": set()}
            entries.append(cur)
            line = "  " + line[2:]
        m = re.match(r"^\s+([A-Za-z_]+):", line)
        if m and cur is not None:
            cur["keys"].add(m.group(1))
    for e in entries:
        missing = [k for k in required if k not in e["keys"]]
        if missing:
            problems.append({"file": path.name, "line": e["line"], "problem": f"缺少 {'、'.join(missing)}"})
    if not entries and lines and not (len(lines) == 1 and lines[0][1].strip() == "[]"):
        problems.append({"file": path.name, "problem": "有内容但识别不出任何条目（每条应以「- id:」开头）"})
    return len(entries), problems


def parse_resume_index(text):
    """解析 resumes/index.yaml：default 与 versions 中每个版本的键。"""
    default, versions, cur = None, [], None
    for no, line in meaningful_lines(text):
        m = re.match(r"^default:\s*(.*?)\s*$", line)
        if m:
            default = m.group(1).strip("\"'") or None
            continue
        m = re.match(r"^\s*-\s+id:\s*(.*?)\s*$", line)
        if m:
            cur = {"line": no, "id": m.group(1).strip("\"'"), "keys": {}}
            versions.append(cur)
            continue
        m = re.match(r"^\s+([A-Za-z_]+):\s*(.*?)\s*$", line)
        if m and cur is not None:
            cur["keys"][m.group(1)] = m.group(2).strip("\"'")
    return default, versions


def check_resumes(ws, problems, warnings):
    rel = "resumes/index.yaml"
    default, versions = parse_resume_index((ws / rel).read_text(encoding="utf-8"))
    if not versions:
        problems.append({"file": rel, "problem": "没有列出任何简历版本（每个版本以「- id:」开头）"})
        return
    ids = [v["id"] for v in versions]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append({"file": rel, "problem": f"版本 id 重复：{dup}"})
    if not default:
        problems.append({"file": rel, "problem": "缺少 default"})
    elif default not in ids:
        problems.append({"file": rel, "problem": f"default 指向不存在的版本：{default}"})
    for v in versions:
        where = {"file": rel, "line": v["line"]}
        for k in ("file", "use"):
            if not v["keys"].get(k):
                problems.append({**where, "problem": f"版本 {v['id']} 缺少 {k}"})
        f = v["keys"].get("file")
        if f and not (ws / "resumes" / f).exists():
            problems.append({**where, "problem": f"文件不存在：resumes/{f}"})
        if "uploaded" not in v["keys"]:
            warnings.append({**where, "warning": f"版本 {v['id']} 缺少 uploaded：followup 无法确认平台附件名，不会自动发送"})


def check_workspace(ws, include_progress=True):
    problems, warnings, info = [], [], {}
    if config_txn.pending(ws) is not None:
        problems.append({"file": config_txn.JOURNAL, "problem": "配置事务未完成，请先说明情况并用 commit --recover 恢复"})
    if include_progress and (ws / PROGRESS_FILE).exists():
        try:
            draft = read_progress(ws)
            summary = progress_summary(draft)
            if draft["mode"] == "new":
                problems.append({"file": PROGRESS_FILE,
                                 "problem": f"初始化未完成，第 {summary['position']}/{summary['total']} 步"})
            else:
                warnings.append({"file": PROGRESS_FILE,
                                 "warning": "有未完成的重新设置，当前配置仍然有效"})
        except WorkspaceError as e:
            problems.append({"file": PROGRESS_FILE, "problem": str(e)})
    for f in CONFIG_FILES:
        if not (ws / f).exists():
            problems.append({"file": f, "problem": "缺失"})
    try:
        meta = ledger.read_json(ws / MARKER, {}) or {}
        accounts = meta.get("accounts") or {}
        if not accounts:
            problems.append({"file": MARKER, "problem": "没有登记任何平台账号"})
        for plat, acc in accounts.items():
            if not (isinstance(acc, dict) and acc.get("accountKey") and acc.get("displayName")):
                problems.append({"file": MARKER, "field": f"accounts.{plat}", "problem": "需要 accountKey 与 displayName"})
        if (ws / "profile.json").exists():
            check_profile(ledger.load_profile(ws), problems, warnings)
    except ledger.LedgerError as e:
        problems.append({"problem": str(e)})
    if (ws / "qa.yaml").exists():
        n, ps = scan_yaml_list(ws / "qa.yaml", ["id", "ask", "answer", "basis"])
        info["qa"] = n
        problems += ps
    if (ws / "resumes" / "index.yaml").exists():
        check_resumes(ws, problems, warnings)
    try:
        info["stoplist"] = len(ledger.load_stoplist(ws))
    except ledger.LedgerError as e:
        problems.append({"file": "stoplist.yaml", "problem": str(e)})
    events, errors = ledger.read_events(ws, strict=False)
    info["events"] = len(events)
    for e in errors:
        problems.append({"file": "ledger/events.jsonl", "line": e["line"], "problem": e["error"]})
    if (ws / "candidate.md").exists() and len((ws / "candidate.md").read_text(encoding="utf-8").strip()) < 50:
        problems.append({"file": "candidate.md", "problem": "内容过少，沟通缺少事实依据"})
    return {"workspace": str(ws), "ok": not problems, "problems": problems,
            "warnings": warnings, "info": info}


def cmd_check(args):
    ws = ledger.find_workspace(args.workspace)
    out = check_workspace(ws)
    return emit(out, 0 if out["ok"] else 1)


# ---------- backup / restore ----------

def backups_dir(ws):
    return ws / "backups"


def do_backup(ws, label=None):
    return config_txn.new_backup(ws, label, config_txn.capture(ws))


def cmd_backup(args):
    ws = ledger.find_workspace(args.workspace)
    name, copied = do_backup(ws, args.label)
    return emit({"backup": name, "path": str(backups_dir(ws) / name), "files": copied})


def cmd_backups(args):
    ws = ledger.find_workspace(args.workspace)
    d = backups_dir(ws)
    items = sorted((p.name for p in d.iterdir() if p.is_dir()), reverse=True) if d.exists() else []
    return emit({"backups": items})


def cmd_restore(args):
    ws = ledger.find_workspace(args.workspace)
    config_txn.refuse_pending(ws)
    config_txn.backup_root(ws, args.name)
    tz = ledger.recovery_tz(ws)
    # 恢复全程持有运行锁：期间 apply / followup 无法启动，读不到前后混合的配置。
    # setup 在自己持锁时可传 --run 沿用；其他任何有效锁（包括别的 setup）都拒绝。
    with ledger.lock_mutex(ws):
        if args.run:
            current = ledger.read_lock(ws)
            if (not current or current.get("runId") != args.run or current.get("skill") != "setup"
                    or current.get("releasing") or ledger.lock_expired(current, fsutil.now(tz))):
                raise WorkspaceError("restore --run 必须是当前有效的 setup 运行锁")
            code, out = 0, {"lock": current, "reused": True, "reconciled": []}
        else:
            code, out = ledger.acquire_lock(ws, tz, "setup", recovery=True)
    if code != 0:
        lock = out.get("lock") or {}
        raise WorkspaceError(f"{lock.get('skill')}（{lock.get('runId')}）正在运行，等它结束后再恢复")
    run_id, reused = out["lock"]["runId"], out["reused"]
    safety, restored, kept = None, [], []
    body_error, corrupt_saved = None, None
    restore_error, release_error = None, None
    release = {"attempted": False, "released": False, "reason": "沿用调用者运行锁，由调用者收尾"}
    try:
        data, _ = config_txn.load_backup(ws, args.name)
        config_txn.validate(data)
        config_txn.live_setup(ws, run_id, "recover")
        current_path = config_txn.safe(ws, "stoplist.yaml")
        current_raw = current_path.read_bytes() if current_path.exists() else b""
        if args.stoplist_from_backup and "stoplist.yaml" not in data:
            raise config_txn.TransactionError("backup_corrupt", "所选备份没有停止名单，不能据此清空或修复")
        try:
            current = ledger.parse_stoplist(current_raw.decode("utf-8"))
        except (UnicodeError, ledger.LedgerError) as error:
            if not args.stoplist_from_backup:
                raise config_txn.TransactionError("stoplist_unreadable", "当前停止名单不可解析；本人确认后使用 --stoplist-from-backup", exit_code=1) from error
            corrupt_saved, _ = config_txn.new_backup(ws, "corrupt", {"stoplist.yaml": current_raw})
            current = []
        if "stoplist.yaml" in data:
            if not args.stoplist_from_backup:
                old_text = data["stoplist.yaml"].decode("utf-8")
                old = ledger.parse_stoplist(old_text)
                added = [ledger.public_entry(e) for e in current if not ledger.find_stop_entry(old, e["name"])]
                data["stoplist.yaml"] = ledger.stoplist_text_with(old_text, added).encode("utf-8")
                kept = [e["name"] for e in added]
        committed = config_txn.commit(ws, data, run_id, do_backup, mode="recover", backup_label="before-restore")
        safety, restored = committed["backup"], list(data)
    except (WorkspaceError, ledger.LedgerError, OSError, ValueError) as e:
        body_error, restore_error = e, str(e)
    finally:
        if not reused:
            try:
                with ledger.lock_mutex(ws):
                    release_code, release_out = ledger.release_lock(ws, run_id, tz)
                release = dict(release_out, attempted=True, code=release_code)
                if release_code != 0 or not release_out.get("released"):
                    release_error = release_out.get("reason") or "未确认释放运行锁"
            except (ledger.LedgerError, OSError, ValueError) as e:
                # 保留阻断锁，不让收尾异常遮蔽主体结果或假称恢复没有发生。
                release_error = str(e)
                release = {"attempted": True, "released": False, "code": 2, "error": release_error}
    reconciled = {ledger.attempt_key(a): a for a in out.get("reconciled", [])}
    reconciled.update({ledger.attempt_key(a): a for a in release.get("reconciled", [])})
    result = {"restored": restored, "from": args.name, "previousSavedAs": safety,
              "stoplistKeptSinceBackup": kept, "reconciled": list(reconciled.values()),
              "runId": run_id, "restoreSucceeded": restore_error is None,
              "restoreError": restore_error, "releaseError": release_error, "release": release,
              "corruptStoplistSavedAs": corrupt_saved}
    if isinstance(body_error, config_txn.TransactionError):
        result.update(code=body_error.code, txnId=body_error.txnId)
    errors = []
    if restore_error is not None:
        errors.append(f"恢复失败：{restore_error}")
    if release_error is not None:
        errors.append(f"释放运行锁失败（runId {run_id} 未确认释放，停止操作）：{release_error}")
    if errors:
        result["error"] = "；".join(errors)
    return emit(result, 2 if release_error else getattr(body_error, "exit_code", 2) if restore_error else 0)


def stoplist_ops(ws, filename):
    source = Path(filename).expanduser()
    if source.is_symlink() or not source.is_file():
        raise WorkspaceError("停止名单操作须是普通 JSON 文件")
    operations = config_txn.schema.json_value(source.read_bytes())
    if not isinstance(operations, list) or not operations:
        raise WorkspaceError("stoplist-ops 须为非空操作数组")
    path = config_txn.safe(ws, "stoplist.yaml")
    text = path.read_bytes().decode("utf-8") if path.exists() else ""
    ledger.parse_stoplist(text)
    for op in operations:
        config_txn.schema.object_fields(op, {"op", "name", "aliases", "reason", "source"}, {"op", "name"})
        if not config_txn.schema.text(op["name"]):
            raise WorkspaceError("stoplist 操作 name 须非空")
        entries = ledger.parse_stoplist(text)
        hit = next((e for e in entries if ledger.norm_company(e["name"]) == ledger.norm_company(op["name"])), None)
        if op["op"] == "remove":
            config_txn.schema.object_fields(op, {"op", "name"}, {"op", "name"})
            if hit is None:
                raise WorkspaceError("remove 必须明确命中已有 name，不按别名或包含关系删除")
            lines = text.splitlines(keepends=True)
            text = "".join(lines[:hit["_start"]] + lines[hit["_end"]:])
        elif op["op"] == "add":
            config_txn.schema.object_fields(op, {"op", "name", "aliases", "reason", "source"}, {"op", "name", "reason", "source"})
            if (not config_txn.schema.strings(op.get("aliases", [])) or not config_txn.schema.text(op["reason"])
                    or not config_txn.schema.text(op["source"])):
                raise WorkspaceError("add 须有合法 aliases、非空 reason/source")
            if not ledger.find_stop_entry(entries, op["name"]):
                entry = {key: op[key] for key in ("name", "reason", "source")}
                entry.update(aliases=op.get("aliases", []), added=fsutil.now(ledger.recovery_tz(ws)).date().isoformat())
                text = ledger.stoplist_text_with(text, [entry])
        else:
            raise WorkspaceError("stoplist-ops 只接受 add / remove")
        ledger.parse_stoplist(text)
    return text.encode("utf-8")


def cmd_commit(args):
    ws = ledger.find_workspace(args.workspace)
    if args.recover:
        if args.stoplist_ops or args.staging or args.abandon != args.confirm_mixed_config:
            raise WorkspaceError("recover 不接收暂存/操作；放弃须同时 --abandon --confirm-mixed-config")
        config_txn.live_setup(ws, args.run, "recover")
        return emit(config_txn.recover(ws, abandon=args.abandon, run=args.run))
    if args.abandon or args.confirm_mixed_config:
        raise WorkspaceError("放弃参数仅用于 --recover")
    config_txn.refuse_pending(ws)
    config_txn.live_setup(ws, args.run, "write")
    staged = config_txn.stage_files(ws, args.staging, allow_empty=bool(args.stoplist_ops))
    if args.stoplist_ops:
        if "stoplist.yaml" in staged:
            raise WorkspaceError("暂存停止名单与 --stoplist-ops 冲突，不能同时提交")
        staged["stoplist.yaml"] = stoplist_ops(ws, args.stoplist_ops)
    return emit(config_txn.commit(ws, staged, args.run, do_backup))


def cmd_changed(args):
    """对比备份与当前配置，列出备份之后被改动的文件（setup 写入前用来发现期间的变化）。"""
    ws = ledger.find_workspace(args.workspace)
    src = config_txn.backup_root(ws, args.since)
    config_txn.load_backup(ws, args.since)
    config_txn.guard_root(ws)
    changed = []
    for f in CONFIG_FILES:
        a, b = src / f, ws / f
        if a.exists() != b.exists():
            changed.append({"file": f, "change": "added" if b.exists() else "removed"})
        elif a.exists() and a.read_bytes() != b.read_bytes():
            changed.append({"file": f, "change": "modified"})
    return emit({"since": args.since, "changed": changed}, 0 if not changed else 1)


# ---------- setup progress ----------

def validate_im_answer(value, status):
    """只校验配置结构，不认证本人、不定位或运行工具；跳过且无值表示不改。"""
    if status == "skipped" and value is None:
        return
    try:
        notify.im_config({"im": value})
    except notify.NotifyError as error:
        raise ValueError("im 步骤：" + str(error)) from error


def read_progress(ws):
    path = ws / PROGRESS_FILE
    if not path.exists():
        raise WorkspaceError("没有 setup 草稿，请先 progress start")
    try:
        draft = config_schema.json_value(path.read_text(encoding="utf-8"))
        if not isinstance(draft, dict) or draft.get("schemaVersion") != 1:
            raise ValueError("不支持的草稿格式")
        if draft.get("mode") not in {"new", "resetup", "partial"}:
            raise ValueError("无效的模式")
        plan, answers, added = draft["plan"], draft["answers"], draft["added"]
        if (not isinstance(plan, list) or not plan or
                any(not isinstance(s, str) or s not in STEP_BY_ID for s in plan) or
                len(set(plan)) != len(plan)):
            raise ValueError("无效的步骤计划")
        if not isinstance(answers, dict) or not isinstance(added, dict):
            raise ValueError("无效的回答或复核步骤")
        if draft["current"] is not None and draft["current"] not in plan:
            raise ValueError("当前步骤不在计划中")
        for step, answer in answers.items():
            if (step not in plan or not isinstance(answer, dict) or
                    answer.get("status") not in {"changed", "kept", "default", "skipped"}):
                raise ValueError("无效的步骤回答")
            if step == "im":
                validate_im_answer(answer.get("value"), answer["status"])
        return draft
    except (OSError, ValueError, KeyError, TypeError, ledger.LedgerError) as e:
        raise WorkspaceError(f"setup 草稿损坏：{e}；请本人确认后用 progress clear 放弃草稿")


def progress_summary(draft):
    current = draft["current"]
    return {**draft, "position": draft["plan"].index(current) + 1 if current else len(draft["plan"]),
            "total": len(draft["plan"]), "currentStep": STEP_BY_ID[current] if current else None}


def next_unanswered(draft):
    return next((s for s in draft["plan"] if s not in draft["answers"]), None)


def save_progress(ws, draft):
    draft["updatedAt"] = fsutil.now().astimezone().isoformat(timespec="seconds")
    ledger.write_atomic(ws / PROGRESS_FILE, json.dumps(draft, ensure_ascii=False, indent=2) + "\n")


def start_progress(args, ws):
    path = ws / PROGRESS_FILE
    if path.exists():
        out = {"error": "已有 setup 草稿，请先选择继续或放弃"}
        try:
            out["progress"] = progress_summary(read_progress(ws))
        except WorkspaceError as e:
            out["error"] = str(e)
        return emit(out, 2)
    if args.mode == "new" and not args.variant:
        raise WorkspaceError("new 需要 --variant")
    if args.mode in {"resetup", "partial"}:
        name = args.base_backup
        if not name:
            raise WorkspaceError(f"{args.mode} 需要 --base-backup")
        if name in {".", ".."} or Path(name).name != name or not (backups_dir(ws) / name).is_dir():
            raise WorkspaceError(f"找不到备份：{name}")
    plan = [s["id"] for s in STEPS if args.mode == "new" or s["id"] != "directory"]
    added = {}
    if args.mode == "partial":
        if not args.select:
            raise WorkspaceError("partial 需要 --select")
        selected = set()
        phases = {s["phase"] for s in STEPS}
        for item in args.select.split(","):
            item = item.strip()
            if item in phases:
                selected.update(s["id"] for s in STEPS if s["phase"] == item and s["id"] != "directory")
            elif item in STEP_BY_ID and item != "directory":
                selected.add(item)
            else:
                raise WorkspaceError(f"未知或不适用的步骤 id：{item}")
        requested = set(selected)
        for step in STEPS:
            if step["id"] in requested:
                for dependency in step.get("depends", []):
                    if dependency not in selected and dependency in step.get("review", []):
                        added[dependency] = step["id"]
                    selected.add(dependency)
        plan = [s["id"] for s in STEPS if s["id"] in selected]
    at = fsutil.now().astimezone().isoformat(timespec="seconds")
    draft = {"schemaVersion": 1, "mode": args.mode, "variant": args.variant,
             "baseBackup": args.base_backup, "startedAt": at, "updatedAt": at,
             "plan": plan, "current": None, "added": added, "answers": {}}
    if args.mode == "new":
        draft["answers"]["directory"] = {"status": "kept", "value": str(ws), "source": "user", "at": at}
        if args.variant == "typical":
            for step in TYPICAL_DEFAULT_STEPS:
                draft["answers"][step] = {"status": "default", "value": STEP_BY_ID[step]["default"],
                                          "source": "default", "at": at}
    draft["current"] = next_unanswered(draft)
    save_progress(ws, draft)
    return emit(progress_summary(draft))


def cmd_progress(args):
    ws = ledger.find_workspace(args.workspace)
    path = ws / PROGRESS_FILE
    if args.action == "start":
        return start_progress(args, ws)
    if args.action == "clear":
        existed = path.exists()
        out = {"cleared": existed, "markerRemoved": False}
        mode = None
        if existed:
            try:
                mode = json.loads(path.read_text(encoding="utf-8"))["mode"]
            except (OSError, ValueError, KeyError, TypeError):
                out["reason"] = "草稿模式读不出，只删除草稿，保留工作区标记"
        if mode == "new":
            events = ledger.events_path(ws)
            try:
                if events.exists() and events.read_bytes().strip():
                    out["reason"] = "已有事件记录，保留工作区标记"
                else:
                    (ws / MARKER).unlink()
                    out["markerRemoved"] = True
            except OSError as e:
                out["reason"] = f"无法安全撤销工作区标记，保留标记：{e}"
        path.unlink(missing_ok=True)
        return emit(out)
    draft = read_progress(ws)
    if args.action == "show":
        return emit(progress_summary(draft))
    if args.action == "finish":
        out = check_workspace(ws, include_progress=False)
        if out["ok"]:
            path.unlink()
        return emit({**out, "finished": out["ok"]},
                    0 if out["ok"] else 1)
    if args.step not in draft["plan"]:
        raise WorkspaceError(f"步骤不在计划中：{args.step}")
    if args.action == "goto":
        draft["current"] = args.step
    else:
        step = STEP_BY_ID[args.step]
        if args.status == "skipped" and step["required"]:
            raise WorkspaceError(f"{step['name']} 是必答步骤，不能跳过")
        try:
            value = config_schema.json_value(args.value) if args.value is not None else None
        except (ValueError, ledger.LedgerError) as e:
            raise WorkspaceError(f"--value 不是合法 JSON：{e}")
        if args.step == "im":
            validate_im_answer(value, args.status)
        draft["answers"][args.step] = {"status": args.status, "value": value, "source": args.source,
                                      "at": fsutil.now().astimezone().isoformat(timespec="seconds")}
        if args.status == "changed":
            for review in step.get("review", []):
                if review not in draft["plan"]:
                    draft["plan"].append(review)
                elif review in draft["answers"]:
                    del draft["answers"][review]
                else:
                    continue
                draft["added"][review] = args.step
            draft["plan"] = [s["id"] for s in STEPS if s["id"] in draft["plan"]]
        draft["current"] = next_unanswered(draft)
    save_progress(ws, draft)
    return emit(progress_summary(draft))


# ---------- 入口 ----------

def build_parser():
    p = argparse.ArgumentParser(prog="workspace.py", description=__doc__.splitlines()[0])
    p.add_argument("--workspace", help="工作区路径；默认从当前目录向上查找 job-mate.json")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init", help="建立工作区骨架（不覆盖已有文件）")
    i.add_argument("path")
    i.add_argument("--account", action="append", default=[], help="platform:accountKey:displayName；可重复，不同平台各一项")
    sub.add_parser("check", help="体检：文件齐全、格式正确、记录无损坏")
    b = sub.add_parser("backup", help="备份配置文件到 backups/<时间>")
    b.add_argument("--label")
    sub.add_parser("backups", help="列出备份")
    r = sub.add_parser("restore", help="从备份恢复配置文件（先自动备份当前版本；保留备份后新增的停止项）")
    r.add_argument("name")
    r.add_argument("--run", help="setup 已持有运行锁时传入其 runId，沿用该锁")
    r.add_argument("--stoplist-from-backup", action="store_true", help="本人确认后只使用备份停止名单，保存损坏原字节")
    commit = sub.add_parser("commit", help="setup 配置事务：校验、备份、提交或恢复残留事务")
    commit.add_argument("--run", required=True)
    source = commit.add_mutually_exclusive_group(required=True)
    source.add_argument("--staging")
    source.add_argument("--recover", action="store_true")
    commit.add_argument("--stoplist-ops", help="本人确认的停止名单 add/remove JSON 操作文件")
    commit.add_argument("--abandon", action="store_true")
    commit.add_argument("--confirm-mixed-config", action="store_true")
    c = sub.add_parser("changed", help="列出某个备份之后被改动的配置文件")
    c.add_argument("--since", required=True)
    pg = sub.add_parser("progress", help="setup 引导进度")
    actions = pg.add_subparsers(dest="action", required=True)
    start = actions.add_parser("start", help="开始引导")
    start.add_argument("--mode", choices=["new", "resetup", "partial"], required=True)
    start.add_argument("--variant", choices=["typical", "custom"])
    start.add_argument("--select", help="阶段或步骤 id，以逗号分隔")
    start.add_argument("--base-backup")
    actions.add_parser("show", help="查看进度")
    record = actions.add_parser("record", help="记录一步")
    record.add_argument("step")
    record.add_argument("--status", choices=["changed", "kept", "default", "skipped"], required=True)
    record.add_argument("--value", help="JSON 取值")
    record.add_argument("--source", choices=["user", "current", "resume", "default"], default="user")
    go = actions.add_parser("goto", help="回到指定步骤")
    go.add_argument("step")
    actions.add_parser("finish", help="体检通过后完成并删除草稿")
    actions.add_parser("clear", help="放弃草稿，不做体检")
    return p


COMMANDS = {"init": cmd_init, "check": cmd_check, "backup": cmd_backup,
            "backups": cmd_backups, "restore": cmd_restore, "changed": cmd_changed,
            "progress": cmd_progress, "commit": cmd_commit}


def entry_mode(args):
    if args.cmd == "commit":
        return "recover" if args.recover else "write"
    if args.cmd == "restore":
        return "recover"
    if args.cmd == "progress":
        return "read" if args.action == "show" else "write"
    return "write" if args.cmd in {"init", "backup"} else "read"


def main(argv=None):
    fsutil.utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "init":
            parse_accounts(args.account)
        new_progress = args.cmd == "progress" and args.action == "start" and args.mode == "new"
        creating = args.cmd == "init" or new_progress
        path = args.path if args.cmd == "init" else args.workspace
        if creating and path:
            ws = init_path(path)  # 拒绝危险目录时尚未创建互斥文件。
            if new_progress and not args.variant:
                raise WorkspaceError("new 需要 --variant")
            with ledger.gated(ws, "write", allow_create=True):
                if new_progress and not (ws / MARKER).exists():
                    init_workspace(ws)
                return COMMANDS[args.cmd](args)
        ws = ledger.find_workspace(path)
        if args.cmd in ("commit", "restore", "backup"):
            config_txn.guard_root(ws)
        with ledger.gated(ws, entry_mode(args)):
            if args.cmd in ("commit", "restore", "backup"):
                with fsutil.locked(config_txn.safe(ws, "ledger/config.lock")):
                    ledger.version_gate(ws, entry_mode(args))
                    return COMMANDS[args.cmd](args)
            return COMMANDS[args.cmd](args)
    except (WorkspaceError, ledger.LedgerError, ValueError, OSError) as e:
        return emit(ledger.error_result(e), getattr(e, "exit_code", 2))


if __name__ == "__main__":
    sys.exit(main())

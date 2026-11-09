"""配置事务与不可信备份校验。调用方持中央 mutation → config.lock；不执行文件内容。"""
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

import config_schema as schema
import fsutil
import ledger

JOURNAL = ".job-mate-txn.json"
CONFIG_FILES = schema.CONFIG_FILES
SHA = re.compile(r"[0-9a-f]{64}\Z")


class TransactionError(ledger.VersionGateError):
    def __init__(self, code, message, txn_id=None, exit_code=2):
        super().__init__(code, message)
        self.txnId, self.exit_code = txn_id, exit_code


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fail(code, message):
    raise TransactionError(code, message)


def safe(root, rel):
    """规范路径逐分量检查；拒绝链接/Windows junction，不跟随逃逸后才检查。"""
    parts = schema.relative(rel)
    path = Path(root)
    for part in parts:
        path = path / part
        if path.is_symlink() or path.resolve() != path.absolute():
            fail("unsafe_path", "拒绝符号链接、路径别名或越界：" + rel)
    return path


def guard_root(ws):
    for name in (*CONFIG_FILES, "ledger", "backups", "ledger/config.lock",
                 "ledger/.mutation.mutex", "ledger/.lock.mutex", "ledger/.events.mutex"):
        path = safe(ws, name)
        if path.exists() and not (path.is_dir() if name in ("ledger", "backups") else path.is_file()):
            fail("unsafe_path", "配置/辅助路径类型无效：" + name)


def read_file(root, rel):
    path = safe(root, rel)
    if not path.is_file():
        fail("unsafe_path", "不是普通文件：" + rel)
    return path.read_bytes()


def paths(values):
    if not isinstance(values, list) or not values or any(not isinstance(p, str) or p not in CONFIG_FILES for p in values):
        fail("backup_corrupt", "配置路径须为 CONFIG_FILES 的非空白名单子集")
    if len(set(values)) != len(values):
        fail("backup_corrupt", "配置路径重复")


def pending(ws):
    path = ws / JOURNAL
    if not path.exists() and not path.is_symlink():
        return None
    txn_id = None
    if not path.is_symlink() and path.is_file():
        try:
            obj = schema.json_value(path.read_bytes())
            value = obj.get("txnId") if isinstance(obj, dict) else None
            txn_id = value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) else None
        except (OSError, ValueError, ledger.LedgerError):
            pass
    return {"txnId": txn_id}


def refuse_pending(ws):
    value = pending(ws)
    if value is not None:
        raise TransactionError("txn_pending", "存在未完成配置事务，只能先 commit --recover", value["txnId"])


def capture(ws):
    guard_root(ws)
    result = {}
    for name in CONFIG_FILES:
        path = safe(ws, name)
        if path.exists():
            if not path.is_file():
                fail("unsafe_path", "配置不是普通文件：" + name)
            result[name] = path.read_bytes()
    return result


def json_write(path, value):
    fsutil.write_text_atomic(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def new_backup(ws, label, data):
    safe(ws, "backups").mkdir(exist_ok=True)
    stamp = fsutil.now(ledger.recovery_tz(ws)).strftime("%Y%m%d-%H%M%S")
    label = re.sub(r"[^0-9A-Za-z_-]+", "-", label or "backup")
    name = f"{stamp}-{label}-{uuid.uuid4().hex}"
    dest = safe(ws, "backups/" + name)
    dest.mkdir()
    for name, raw in data.items():
        if name not in CONFIG_FILES:
            fail("unsafe_path", "备份文件超出白名单")
        path = safe(dest, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        fsutil.write_bytes_atomic(path, raw)
    manifest = {"schemaVersion": 1, "files": [{"path": name, "sha256": sha(raw)} for name, raw in data.items()]}
    json_write(dest / "MANIFEST.json", manifest)
    return dest.name, list(data)


def backup_root(ws, name):
    if not isinstance(name, str) or not re.fullmatch(r"[0-9A-Za-z_-]+", name) or name in (".", ".."):
        fail("unsafe_path", "备份名只能是 backups 下的单个目录名")
    path = safe(ws, "backups/" + name)
    if not path.is_dir():
        fail("backup_corrupt", "备份目录不存在")
    return path


def load_backup(ws, name, manifest_sha=None):
    root = backup_root(ws, name)
    try:
        raw = read_file(root, "MANIFEST.json")
        if manifest_sha is not None and sha(raw) != manifest_sha:
            fail("backup_corrupt", "清单摘要与事务日志不符")
        manifest = schema.json_value(raw)
        schema.object_fields(manifest, {"schemaVersion", "files"}, {"schemaVersion", "files"})
        if type(manifest["schemaVersion"]) is not int or manifest["schemaVersion"] != 1 or not isinstance(manifest["files"], list):
            fail("backup_corrupt", "不支持的备份清单")
        for item in manifest["files"]:
            schema.object_fields(item, {"path", "sha256"}, {"path", "sha256"})
        paths([item["path"] for item in manifest["files"]])
        actual = set()
        for directory, dirs, files in os.walk(root, followlinks=False):
            for entry in [*dirs, *files]:
                path = Path(directory) / entry
                safe(root, path.relative_to(root).as_posix())
            if any((Path(directory) / d).relative_to(root).as_posix() != "resumes" for d in dirs):
                fail("backup_corrupt", "备份含非白名单子目录")
            actual.update((Path(directory) / f).relative_to(root).as_posix() for f in files)
        if actual != {"MANIFEST.json", *(item["path"] for item in manifest["files"])}:
            fail("backup_corrupt", "备份路径集合与清单不符")
        contents = {}
        for item in manifest["files"]:
            digest = item["sha256"]
            if not isinstance(digest, str) or not SHA.fullmatch(digest):
                fail("backup_corrupt", "备份摘要字段无效")
            data = read_file(root, item["path"])
            if sha(data) != digest:
                fail("backup_corrupt", "备份文件摘要不符：" + item["path"])
            contents[item["path"]] = data
        return contents, sha(raw)
    except (OSError, ValueError, ledger.LedgerError) as error:
        if isinstance(error, TransactionError) and error.code == "backup_corrupt":
            raise
        raise TransactionError("backup_corrupt", "备份校验失败：" + str(error)) from error


def stage_files(ws, directory, allow_empty=False):
    root = Path(directory).expanduser()
    if root.is_symlink() or root.resolve() != root.parent.resolve() / root.name:
        fail("unsafe_path", "暂存根目录不能是符号链接")
    root = root.resolve()
    # 暂存与工作区整棵目录分离，避免输入/备份/目标自我覆盖。
    if root == ws or root in ws.parents or ws in root.parents or not root.is_dir():
        fail("unsafe_path", "暂存目录必须存在且与工作区互不包含")
    found = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            safe(root, rel)
            if path.is_dir():
                if rel != "resumes":
                    fail("unsafe_path", "暂存目录含未知子目录：" + rel)
            else:
                if rel not in CONFIG_FILES or not path.is_file():
                    fail("unsafe_path", "暂存含非白名单配置：" + rel)
                found[rel] = path.read_bytes()
    if not found and not allow_empty:
        fail("invalid_staging", "暂存目录没有配置文件")
    return found


def validate(contents):
    for name, data in contents.items():
        schema.validate(name, data)


def crash(point):
    if os.environ.get("JOB_MATE_TESTING") == "1" and os.environ.get("JOB_MATE_TEST_CRASH_AT") == point:
        os._exit(70)


def live_setup(ws, run, mode):
    # 已在中央/config临界区，不能只依赖等锁前的准入或旧profile。
    ledger.version_gate(ws, mode)
    with ledger.lock_mutex(ws):
        lock = ledger.read_lock(ws)
        if (not lock or lock.get("runId") != run or lock.get("skill") != "setup" or lock.get("releasing")
                or ledger.lock_expired(lock, fsutil.now(ledger.recovery_tz(ws)))):
            fail("setup_lock_required", "必须持有当前有效的 setup 运行锁")


def save_journal(ws, obj):
    json_write(safe(ws, JOURNAL), obj)


def load_journal(ws):
    try:
        raw = read_file(ws, JOURNAL)
        obj = schema.json_value(raw)
        schema.object_fields(obj, {"txnId", "state", "runId", "startedAt", "backupDir", "manifestSha", "files", "imChange"},
                             {"txnId", "state", "runId", "startedAt", "backupDir", "manifestSha", "files"})
        if (not isinstance(obj["txnId"], str) or not re.fullmatch(r"[0-9a-f]{32}", obj["txnId"])
                or obj["state"] not in ("committing", "rolling_back") or not schema.text(obj["runId"])
                or not isinstance(obj["manifestSha"], str) or not SHA.fullmatch(obj["manifestSha"])):
            fail("journal_corrupt", "事务日志身份或状态无效")
        if not schema.text(obj["startedAt"]):
            fail("journal_corrupt", "事务开始时间无效")
        ledger.parse_at(obj["startedAt"])
        if not isinstance(obj["files"], list):
            fail("journal_corrupt", "事务文件列表无效")
        for item in obj["files"]:
            schema.object_fields(item, {"path", "existed", "backupSha", "stagedSha", "done"}, {"path", "existed", "backupSha", "stagedSha", "done"})
            if (type(item["existed"]) is not bool or type(item["done"]) is not bool
                    or (item["stagedSha"] is not None and (not isinstance(item["stagedSha"], str) or not SHA.fullmatch(item["stagedSha"])))
                    or (item["existed"] and (not isinstance(item["backupSha"], str) or not SHA.fullmatch(item["backupSha"])))
                    or (not item["existed"] and item["backupSha"] is not None)):
                fail("journal_corrupt", "事务逐文件字段无效")
        paths([item["path"] for item in obj["files"]])
        if {item["path"] for item in obj["files"]} != set(CONFIG_FILES):
            fail("journal_corrupt", "日志必须完整列出全部配置的存在/缺失状态")
        if any(item["existed"] and item["stagedSha"] is None for item in obj["files"]):
            fail("journal_corrupt", "已有配置不能被提交删除")
        # 只验证备份名语法，abandon不依赖备份目录仍存在。
        if not isinstance(obj["backupDir"], str) or not re.fullmatch(r"backups/[0-9A-Za-z_-]+", obj["backupDir"]):
            fail("journal_corrupt", "备份目录无效")
        for item in obj["files"]:
            path = safe(ws, item["path"])
            if path.exists() and not path.is_file():
                fail("journal_corrupt", "目标不是普通文件")
        if "imChange" in obj:
            im_metadata(ws, obj["imChange"])
            profile = next(item for item in obj["files"] if item["path"] == "profile.json")
            if (profile["backupSha"] != obj["imChange"]["beforeHash"]
                    or profile["stagedSha"] != obj["imChange"]["afterHash"]):
                fail("journal_corrupt", "IM 事务摘要与配置日志不一致")
        return obj, raw
    except (OSError, ValueError, ledger.LedgerError) as error:
        raise TransactionError("journal_corrupt", "事务日志校验失败，保留证据：" + str(error)) from error


def im_metadata(ws, meta):
    import notify
    try:
        return notify.validate_im_metadata(ws, meta)
    except (notify.NotifyError, ledger.LedgerError, ValueError, KeyError, TypeError) as error:
        raise TransactionError("journal_corrupt", "IM事务关联或原计划损坏") from error


def audit_im_transaction(ws, obj, status):
    if "imChange" not in obj:
        return
    import notify
    try:
        notify.im_audit(ws, obj["imChange"], obj["txnId"], obj["backupDir"].split("/")[1], status)
    except (notify.NotifyError, ledger.LedgerError, ValueError, KeyError, TypeError) as error:
        raise TransactionError("im_audit_invalid", "IM事务审计失败，保留日志") from error


def recover(ws, abandon=False, preserve_version=True, run=None):
    if pending(ws) is None:
        return {"recovered": False, "reason": "没有未完成事务", "verified": []}
    obj, original_journal = load_journal(ws)
    if abandon:
        current = capture(ws)
        if run is not None:
            live_setup(ws, run, "recover")
        name, _ = new_backup(ws, "abandoned-txn", current)
        root = backup_root(ws, name)
        fsutil.write_bytes_atomic(root / "JOURNAL.json", original_journal)
        json_write(root / "STATE.json", {"files": [{"path": item["path"], "exists": item["path"] in current,
                                                   "sha256": sha(current[item["path"]]) if item["path"] in current else None} for item in obj["files"]]})
        if run is not None:
            live_setup(ws, run, "recover")
        audit_im_transaction(ws, obj, "needs_review")
        safe(ws, JOURNAL).unlink()
        return {"abandoned": True, "txnId": obj["txnId"], "savedAs": name, "warning": "当前配置可能新旧混合",
                "next": "请本人选择恢复另一份完整备份或重新设置；对照已保存状态核对配置"}
    contents, _ = load_backup(ws, obj["backupDir"].split("/")[1], obj["manifestSha"])
    existed = {item["path"]: item["backupSha"] for item in obj["files"] if item["existed"]}
    if existed != {name: sha(raw) for name, raw in contents.items()}:
        fail("backup_corrupt", "日志与清单的路径集合/逐文件摘要不符；未修改任何目标")
    if preserve_version and ledger.MARKER in contents:
        version = schema.json_value(read_file(ws, ledger.MARKER))["suiteVersion"]
        meta = schema.json_value(contents[ledger.MARKER])
        if not isinstance(meta, dict) or "suiteVersion" not in meta:
            fail("backup_corrupt", "回滚备份标记格式损坏")
        ledger.suite_version(meta["suiteVersion"])
        if meta["suiteVersion"] != version:
            meta["suiteVersion"] = version
            contents[ledger.MARKER] = (json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    # 所有路径、备份字节与摘要已经一次性全部预检并冻结，之后才允许目标写/删除。
    if run is not None:
        live_setup(ws, run, "recover")
    obj["state"] = "rolling_back"
    save_journal(ws, obj)
    for item in obj["files"]:
        if run is not None:
            live_setup(ws, run, "recover")
        path = safe(ws, item["path"])
        if item["existed"]:
            data = contents[item["path"]]
            if not path.exists() or path.read_bytes() != data:
                path.parent.mkdir(parents=True, exist_ok=True)
                fsutil.write_bytes_atomic(path, data)
        elif path.exists():
            path.unlink()
        crash("during_rollback")
    verified = [{"path": item["path"], "ok": (safe(ws, item["path"]).is_file()
                 and sha(safe(ws, item["path"]).read_bytes()) == sha(contents[item["path"]])) if item["existed"]
                 else not safe(ws, item["path"]).exists()} for item in obj["files"]]
    if not all(item["ok"] for item in verified):
        fail("rollback_incomplete", "回滚后逐文件核验未通过，保留日志")
    if run is not None:
        live_setup(ws, run, "recover")
    audit_im_transaction(ws, obj, "rolled_back")
    crash("im_after_rollback_audit")
    safe(ws, JOURNAL).unlink()
    return {"recovered": True, "txnId": obj["txnId"], "verified": verified}


def commit(ws, staged, run, backup_fn, mode="write", backup_label="commit", im_change=None):
    refuse_pending(ws)
    live_setup(ws, run, mode)
    validate(staged)
    if im_change is not None:
        if mode != "write" or set(staged) != {"profile.json"}:
            fail("im_change_fields_forbidden", "IM事务仅接受普通setup阶段的profile")
        im_metadata(ws, im_change)
        if sha(read_file(ws, "profile.json")) != im_change["beforeHash"]:
            fail("im_change_baseline_conflict", "配置基线已变化")
        if sha(staged["profile.json"]) != im_change["afterHash"]:
            fail("im_change_fields_forbidden", "暂存与计划不一致")
    before = capture(ws)
    version = schema.json_value(before[ledger.MARKER])["suiteVersion"]
    data = dict(staged)
    marker = schema.json_value(data.get(ledger.MARKER, before[ledger.MARKER]))
    marker["suiteVersion"] = version if mode == "recover" else max((version, ledger.SUITE_VERSION), key=ledger.suite_version)
    # 无需变更标记时原字节不变；回滚始终使用未经重排的提交前字节。
    if ledger.MARKER in data or marker != schema.json_value(before[ledger.MARKER]):
        data[ledger.MARKER] = (json.dumps(marker, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    backup, _ = backup_fn(ws, backup_label)
    saved, manifest_sha = load_backup(ws, backup)
    if saved != before:
        fail("backup_corrupt", "前置备份与提交前快照不符")
    crash("after_backup")
    live_setup(ws, run, mode)
    ordered = sorted(CONFIG_FILES, key=lambda name: (name == ledger.MARKER, name not in data, name in before, CONFIG_FILES.index(name)))
    obj = {"txnId": backup.rsplit("-", 1)[-1], "state": "committing", "runId": run, "startedAt": ledger.now_iso(ledger.recovery_tz(ws)),
           "backupDir": "backups/" + backup, "manifestSha": manifest_sha,
           "files": [{"path": name, "existed": name in before, "backupSha": sha(before[name]) if name in before else None,
                      "stagedSha": sha(data.get(name, before.get(name))) if data.get(name, before.get(name)) is not None else None, "done": False} for name in ordered]}
    if im_change is not None:
        obj["imChange"] = im_change
    save_journal(ws, obj)
    crash("after_journal")
    changed = 0
    try:
        for item in obj["files"]:
            live_setup(ws, run, mode)
            path = safe(ws, item["path"])
            desired = data.get(item["path"], before.get(item["path"]))
            if desired is None and path.exists():
                fail("commit_incomplete", "原本缺失的非暂存配置意外出现")
            if desired is not None and (not path.exists() or path.read_bytes() != desired):
                path.parent.mkdir(parents=True, exist_ok=True)
                fsutil.write_bytes_atomic(path, desired)
                changed += 1
            item["done"] = True
            save_journal(ws, obj)
            if changed == 1:
                crash("after_file_1")
        if any((safe(ws, item["path"]).exists() if item["stagedSha"] is None else
                not safe(ws, item["path"]).is_file() or sha(safe(ws, item["path"]).read_bytes()) != item["stagedSha"]) for item in obj["files"]):
            fail("commit_incomplete", "提交后摘要核验失败")
        audit_im_transaction(ws, obj, "applied")
        if im_change is not None:
            crash("im_after_applied_audit")
        safe(ws, JOURNAL).unlink()
        if im_change is not None:
            crash("im_after_journal_delete")
    except (OSError, ValueError, ledger.LedgerError) as error:
        try:
            rolled_back = recover(ws, preserve_version=False)
            if not rolled_back.get("recovered"):
                fail("rollback_incomplete", "日志缺失，不能确认回滚，不猜测删除文件")
        except (OSError, ValueError, ledger.LedgerError) as rollback_error:
            raise TransactionError("rollback_incomplete", f"提交失败：{error}；回滚失败：{rollback_error}；保留日志") from error
        raise TransactionError("commit_rolled_back", "提交失败，已逐文件回滚：" + str(error)) from error
    return {"committed": True, "txnId": obj["txnId"], "backup": backup, "files": list(data)}

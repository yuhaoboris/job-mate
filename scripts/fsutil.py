"""跨系统文件锁、UTF-8、原子替换、时区与可控时钟；仅用标准库。"""

import contextlib
import errno
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@contextlib.contextmanager
def locked(path):
    """锁文件从不删除。不可重入；调用者须避免嵌套获取同一路径。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    acquired = False
    try:
        # 并发初始化均写偏移 0，不截断或追加，不改变锁定区域。
        if os.fstat(fd).st_size == 0:
            try:
                os.write(fd, b"\0")
            except OSError:
                # Windows 上另一创建者可能已写入并锁住该字节。
                if os.fstat(fd).st_size == 0:
                    raise
        if os.name == "nt":
            import msvcrt
            deadline = time.monotonic() + 30
            while True:
                os.lseek(fd, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError as e:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"获取文件锁超时：{path}") from e
                    time.sleep(0.05)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        acquired = True
        yield
    finally:
        try:
            if acquired:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


@contextlib.contextmanager
def try_locked(path, create=True):
    """只尝试一次；仅真实锁争用返回 False，权限/文件系统错误原样上报。"""
    path = Path(path)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | (os.O_CREAT if create else 0), 0o600)
    acquired = False
    try:
        os.set_inheritable(fd, False)
        if os.fstat(fd).st_size == 0:
            try:
                os.write(fd, b"\0")
            except OSError:
                if os.fstat(fd).st_size == 0:
                    raise
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as error:
            busy = {errno.EACCES, errno.EAGAIN} if os.name == "nt" else {errno.EAGAIN, errno.EWOULDBLOCK}
            if error.errno not in busy:
                raise
        yield acquired
    finally:
        try:
            if acquired:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def replace_atomic(src, dst):
    for attempt in range(41):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 40:
                raise
            time.sleep(0.05)


def write_text_atomic(path, text):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        replace_atomic(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def write_bytes_atomic(path, data):
    """配置备份/回滚保留损坏文件的原字节；与文本替换共用跨系统封装。"""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        replace_atomic(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def publish_text_new(path, text):
    """同目录临时文件写完 fsync 后原子发布；目标若出现则拒绝，不覆盖任何旧文件。

    os.link 是跨系统标准库接口；文件系统不支持时失败关闭，不退回截断写入。
    """
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def read_text(path):
    return Path(path).read_text(encoding="utf-8")


def write_text(path, text):
    return Path(path).write_text(text, encoding="utf-8")


def utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def tz_from_name(name):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as e:
        offsets = {"Asia/Shanghai": 8, "Asia/Hong_Kong": 8, "Asia/Taipei": 8,
                   "Asia/Singapore": 8, "Asia/Tokyo": 9}
        if name in offsets:
            return timezone(timedelta(hours=offsets[name]), name)
        raise ValueError(f"找不到时区 {name}；请改用 Asia/Shanghai、Asia/Hong_Kong、"
                         "Asia/Taipei、Asia/Singapore、Asia/Tokyo 或安装时区数据") from e


def now(tz=None):
    value = os.environ.get("JOB_MATE_NOW") if os.environ.get("JOB_MATE_TESTING") == "1" else None
    if value:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            raise ValueError("JOB_MATE_NOW 必须含时区")
        return dt.astimezone(tz)
    return datetime.now(tz)

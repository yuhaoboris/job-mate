#!/usr/bin/env python3
"""把 job-mate 的技能构建成独立技能：每个技能自带 shared/ 与 scripts/，引用路径改写为技能内部路径。

产物：<out>/job-mate-<技能>/。可用 --install <目录> 安装到某个 Agent 的技能目录（如 <项目>/.agents/skills），
只替换同名的 job-mate-* 目录，不动其他技能。仅用 Python 标准库。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fsutil  # noqa: E402
import platforms  # noqa: E402
from ledger import SUITE_VERSION  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "job-mate-"
LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s#]+)(#[^)\s]*)?\)")
SKILL_NAMES = ("apply", "followup", "setup")


class BuildError(Exception):
    pass


def git_info():
    def run(*args):
        try:
            result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                                    env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
            return result.stdout.strip() if result.returncode == 0 else None
        except OSError:
            return None
    top = run("rev-parse", "--show-toplevel")
    if not top or Path(top).resolve() != ROOT.resolve():
        return None, None
    commit = run("rev-parse", "HEAD")
    status = run("status", "--porcelain")
    return (commit, bool(status)) if commit and status is not None else (None, None)


def checked_path(path, aliases=False):
    """技能和源码路径不跟随链接；只有显式分发根允许解析祖先别名。"""
    path = Path(path).expanduser()
    if ".." in path.parts:
        raise BuildError(f"路径不能含 ..：{path}")
    path = path.absolute()
    if aliases:
        return path.resolve()
    for part in (*reversed(path.parents), path):
        # macOS 的系统别名不是用户安装目录中的符号链接。
        system_alias = sys.platform == "darwin" and str(part) in ("/tmp", "/var", "/etc")
        if part.is_symlink() and not system_alias:
            raise BuildError(f"路径包含符号链接，不覆盖：{part}")
    return path.resolve()


def overlaps(a, b):
    return a == b or a in b.parents or b in a.parents


def source_path(path):
    path = checked_path(path)
    root = ROOT.resolve()
    if not path.is_relative_to(root) or path == root or path.relative_to(root).parts[0] not in ("skills", "shared", "scripts", "platforms"):
        raise BuildError(f"源文件不在技能、共享规则、平台或脚本目录内：{path}")
    return path


def check_sources(skills):
    for name in ("skills", "shared", "scripts", "platforms"):
        directory = source_path(ROOT / name)
        if not directory.is_dir():
            raise BuildError(f"缺少源目录：{directory}")
        for path in directory.rglob("*"):
            source_path(path)
            if not path.is_file() and not path.is_dir():
                raise BuildError(f"源目录中包含特殊文件：{path}")
    for directory in sorted((ROOT / "platforms").iterdir()):
        if not directory.is_dir():
            continue
        descriptor = directory / "platform.json"
        if not descriptor.is_file():
            raise BuildError(f"缺少平台描述：{descriptor}")
        platforms.validate_platform(json.loads(fsutil.read_text(descriptor)), directory.name)
    for skill in skills:
        if not (ROOT / "skills" / skill / "SKILL.md").is_file():
            raise BuildError(f"缺少 skills/{skill}/SKILL.md")


def inventory(directory):
    """先拒绝链接/特殊文件；普通 Python 缓存及 Finder 元数据不作归属证据。"""
    files, dirs = {}, set()
    for path in directory.rglob("*"):
        if path.is_symlink() or path.is_mount():
            raise BuildError(f"产物含符号链接或挂载点，不覆盖：{path}")
        rel = path.relative_to(directory).as_posix()
        if path.is_dir():
            if path.name != "__pycache__":
                dirs.add(rel)
        elif path.is_file():
            artifact = path.name == ".DS_Store" or (path.parent.name == "__pycache__" and path.suffix == ".pyc")
            if rel != "BUILD.json" and not artifact:
                files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            raise BuildError(f"产物含特殊文件，不覆盖：{path}")
    return files, dirs


def managed(directory, name):
    """旧格式清单、手工修改或未知额外文件均拒绝自动覆盖，请先人工迁出。"""
    checked_path(directory)
    if not directory.is_dir() or directory.is_mount():
        raise BuildError(f"{directory} 不是普通、非挂载的技能目录，不覆盖")
    files, dirs = inventory(directory)
    try:
        info = json.loads((directory / "BUILD.json").read_text(encoding="utf-8"))
        if (not isinstance(info, dict) or type(info.get("format")) is not int or info.get("format") != 1
                or info.get("source") != "job-mate" or info.get("skill") != name):
            raise ValueError("清单格式或技能身份不符")
        if not files or files != info.get("files") or sorted(dirs) != info.get("directories"):
            raise ValueError("文件摘要或目录结构与清单不符")
        skill_md = (directory / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = skill_md.split("---", 2)
        if len(frontmatter) != 3 or frontmatter[0] or f"name: {name}" not in frontmatter[1].splitlines():
            raise ValueError("SKILL.md 身份不符")
        if any(Path(f).parts[0] not in ("SKILL.md", "shared", "scripts", "platforms") for f in (*files, *dirs)):
            raise ValueError("存在非技能产物文件")
    except (OSError, ValueError, UnicodeError) as e:
        raise BuildError(f"{directory} 不是完整、未修改的受管产物，不覆盖：{e}。"
                         f"请将旧目录 {directory} 整体迁出 runtime 扫描的技能目录后重试；"
                         "不要仅在技能目录内改名，也不要仅删除 BUILD.json。") from e
    return files, dirs


def target_plan(target, names):
    # 显式输出/安装根可经过 dotfiles 祖先别名；解析后再核对源码边界。
    target = checked_path(target, aliases=True)
    root = ROOT.resolve()
    if target == root or target in root.parents or any(overlaps(target, root / part) for part in ("skills", "shared", "scripts", "platforms", ".git")):
        raise BuildError(f"目标与源码目录重叠，不覆盖：{target}")
    if target.exists() and not target.is_dir():
        raise BuildError(f"目标不是目录：{target}")
    for name in names:
        dest = target / name
        checked_path(dest)
        if dest.exists():
            managed(dest, name)
    return target


def dest_for(src, skill, skill_dir):
    """把套件内的源文件路径映射到独立技能内；跨技能共用内容须在共享目录。"""
    rel = source_path(src).relative_to(ROOT.resolve())
    parts = rel.parts
    if parts[0] in ("shared", "scripts", "platforms"):
        return skill_dir / rel
    if parts[0] == "skills" and parts[1] == skill:
        return skill_dir.joinpath(*parts[2:])
    raise BuildError(f"技能引用了其他技能或未知位置的文件，请移入共享目录：{rel}")


def rewrite_markdown(text, src, dest, skill, skill_dir, pending):
    def repl(m):
        label, link, anchor = m.group(1), m.group(2), m.group(3) or ""
        if re.match(r"^[a-z]+:", link):
            return m.group(0)
        # 相对链接允许 ../；在 resolve 前逐级核对链接，防止源内容外泄。
        linked = src.parent / link
        for part in (linked, *linked.parents):
            if part.is_symlink():
                raise BuildError(f"源引用包含符号链接：{linked}")
        target_src = source_path(linked.resolve())
        if not target_src.exists():
            raise BuildError(f"{src.relative_to(ROOT.resolve())} 中的链接不存在：{link}")
        target_dest = dest_for(target_src, skill, skill_dir)
        if target_src.is_file():
            pending.append((target_src, target_dest))
        new = os.path.relpath(target_dest, dest.parent).replace(os.sep, "/")
        if label == link:
            label = new
        return f"[{label}]({new}{anchor})"
    return LINK_RE.sub(repl, text)


def rewrite_skill_md(text, skill):
    """SKILL.md 专有的改写：技能名、描述及正文中的技能引用、脚本位置的说法。"""
    lines = text.split("\n")
    if lines[0] != "---":
        raise BuildError(f"skills/{skill}/SKILL.md 缺少 frontmatter")
    end = lines.index("---", 1)
    for i in range(1, end):
        if lines[i].startswith("name:"):
            lines[i] = f"name: {PREFIX}{skill}"
        elif lines[i].startswith("description:"):
            for other in SKILL_NAMES:
                lines[i] = re.sub(rf"用 {other}(?![-\w])", f"用 {PREFIX}{other}", lines[i])
    body = "\n".join(lines[end + 1:])
    for other in SKILL_NAMES:
        body = re.sub(rf"用 {other}(?![-\w])", f"用 {PREFIX}{other}", body)
    text = "\n".join(lines[:end + 1]) + "\n" + body
    text = text.replace("<本文件上两级目录>/scripts/", "<本文件所在目录>/scripts/")
    text = text.replace(".../scripts/", "<本文件所在目录>/scripts/")
    return text


def rewrite_shared_md(text):
    """shared/ 下的文件在独立技能中位于 <技能>/shared/，脚本在上一级目录。"""
    return text.replace("本文件上两级目录", "本文件上一级目录（技能目录）")


def build_skill(skill, out, info):
    if skill not in SKILL_NAMES:
        raise BuildError(f"未知技能名：{skill!r}")
    src_dir = ROOT.resolve() / "skills" / skill
    skill_dir = out / f"{PREFIX}{skill}"
    if skill_dir.exists() or skill_dir.is_symlink():
        raise BuildError(f"构建暂存目录已存在，不覆盖：{skill_dir}")
    done, pending = set(), []
    for src in sorted(src_dir.rglob("*.md")):
        pending.append((src, dest_for(src, skill, skill_dir)))
    # shared 全部带上（技能文档之间互相引用，整体复制更稳妥）
    for src in sorted((ROOT / "shared").rglob("*.md")):
        pending.append((src, dest_for(src, skill, skill_dir)))
    # 平台描述与全部页面说明随每份技能分发，不依赖本轮是否有 Markdown 引用。
    for src in sorted((ROOT / "platforms").rglob("*")):
        if src.is_file():
            pending.append((src, dest_for(src, skill, skill_dir)))
    while pending:
        src, dest = pending.pop()
        src = source_path(src)
        if src in done:
            continue
        done.add(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix != ".md":
            shutil.copy2(src, dest)
            continue
        text = src.read_text(encoding="utf-8")
        if src == src_dir / "SKILL.md":
            text = rewrite_skill_md(text, skill)
        elif src.parent == ROOT.resolve() / "shared":
            text = rewrite_shared_md(text)
        text = rewrite_markdown(text, src, dest, skill, skill_dir, pending)
        dest.write_text(text, encoding="utf-8")
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for py in sorted((ROOT / "scripts").glob("*.py")):
        if py.name != "build.py":
            shutil.copy2(source_path(py), scripts / py.name)
    verify(skill_dir)
    files, dirs = inventory(skill_dir)
    (skill_dir / "BUILD.json").write_text(json.dumps(dict(info, source="job-mate", format=1,
                                                       skill=f"{PREFIX}{skill}", files=files,
                                                       directories=sorted(dirs)),
                                                     ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return skill_dir


def verify(skill_dir):
    """产物自检：没有指向技能目录之外的链接，所有链接都存在。"""
    for md in skill_dir.rglob("*.md"):
        text = md.read_text(encoding="utf-8")
        for m in LINK_RE.finditer(text):
            link = m.group(2)
            if re.match(r"^[a-z]+:", link):
                continue
            target = (md.parent / link).resolve()
            if not target.exists():
                raise BuildError(f"产物中链接失效：{md.relative_to(skill_dir)} → {link}")
            if skill_dir.resolve() not in target.parents and target != skill_dir.resolve():
                raise BuildError(f"产物中链接指向技能目录之外：{md.relative_to(skill_dir)} → {link}")
        if "上两级目录" in text and (md.name == "SKILL.md" or md.parent.name == "shared"):
            raise BuildError(f"{md.relative_to(skill_dir)} 仍含「上两级目录」的说法")


def remove_package(directory, name, partial_source=None):
    """仅清理本事务持有的包；不递归删除未知文件或跟随链接。"""
    checked_path(directory)
    entries = sorted(directory.rglob("*"), key=lambda p: len(p.parts), reverse=True)
    if partial_source is None:
        managed(directory, name)
    else:
        # copytree 中途失败也可清理，但只允许源包已知路径；不要求半写文件摘要相等。
        files, dirs = inventory(directory)
        allowed_files, allowed_dirs = inventory(partial_source)
        if directory.is_mount() or set(files) - set(allowed_files) or dirs - allowed_dirs:
            raise BuildError(f"暂存包含未知数据，保留供人工处理：{directory}")
    for entry in entries:
        checked_path(entry)
        if entry.is_dir():
            entry.rmdir()
        else:
            entry.unlink()
    directory.rmdir()


def cleanup_transaction(stages, records):
    errors = []
    for record in records:
        for key in ("new", "backup"):
            path = record[key]
            if path.exists() or path.is_symlink():
                try:
                    remove_package(path, record["name"], record["source"] if key == "new" else None)
                except (BuildError, OSError, ValueError) as e:
                    errors.append(f"保留 {path}：{e}")
    for stage in stages:
        try:
            (stage / "new").rmdir()
            (stage / "old").rmdir()
            stage.rmdir()
        except OSError as e:
            errors.append(f"保留事务目录 {stage}：{e}")
    return errors


def publish(built, targets):
    """完整旁路暂存后逐包 rename；任何切换失败时逆序回滚所有输出/安装根。

    这是可回滚批次，不保证读者看不到中间版本，也不提供断电后的自动恢复。
    同权限用户伪造完整合法清单或并发恶意置换目录不在威胁范围。
    """
    names = [p.name for p in built]
    if sorted(names) != sorted(PREFIX + s for s in SKILL_NAMES):
        raise BuildError("必须构建并发布完整的三个技能，不允许子集或重复包")
    provenance = []
    for source in built:
        managed(source, source.name)
        manifest = json.loads((source / "BUILD.json").read_text(encoding="utf-8"))
        provenance.append((manifest.get("suiteVersion"), manifest.get("commit")))
    if (any(not isinstance(version, str) or commit is not None and not isinstance(commit, str)
            for version, commit in provenance) or len(set(provenance)) != 1):
        raise BuildError("整套技能的版本或提交号不一致，拒绝发布")
    targets = [target_plan(target, names) for target in targets]
    for i, target in enumerate(targets):
        if any(overlaps(target, other) for other in targets[:i]) or any(overlaps(target, p.resolve()) for p in built):
            raise BuildError("发布来源、输出或安装目录重叠")
    stages, records = [], []
    try:
        # 所有根的完整新包先放在目标同文件系统，复制失败不触动旧包。
        for target in targets:
            target.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix=".job-mate-transaction-", dir=target))
            stages.append(stage)
            (stage / "new").mkdir()
            (stage / "old").mkdir()
            for source in built:
                record = {"source": source, "name": source.name, "dest": target / source.name,
                          "new": stage / "new" / source.name, "backup": stage / "old" / source.name,
                          "old_moved": False, "new_moved": False}
                records.append(record)
                shutil.copytree(source, record["new"])
                managed(record["new"], source.name)
        for target in targets:
            target_plan(target, names)
        for record in records:
            dest = record["dest"]
            if dest.exists():
                managed(dest, record["name"])
                fsutil.replace_atomic(dest, record["backup"])
                record["old_moved"] = True
            fsutil.replace_atomic(record["new"], dest)
            record["new_moved"] = True
    except (BuildError, OSError, ValueError) as failure:
        rollback_errors = []
        for record in reversed(records):
            try:
                if record["new_moved"]:
                    managed(record["dest"], record["name"])
                    fsutil.replace_atomic(record["dest"], record["new"])
                    record["new_moved"] = False
                if record["old_moved"]:
                    managed(record["backup"], record["name"])
                    if record["dest"].exists() or record["dest"].is_symlink():
                        raise BuildError(f"恢复目标被占用：{record['dest']}")
                    fsutil.replace_atomic(record["backup"], record["dest"])
                    record["old_moved"] = False
            except (BuildError, OSError, ValueError) as e:
                rollback_errors.append(f"恢复 {record['backup']} → {record['dest']}：{e}")
        if rollback_errors:
            # 不清理任何事务目录：备份可能是旧版本唯一副本。
            locations = "; ".join(str(stage) for stage in stages)
            raise BuildError(f"发布失败：{failure}；回滚未完成，备份与新包均保留于 {locations}；"
                             + "; ".join(rollback_errors)) from failure
        cleanup_errors = cleanup_transaction(stages, records)
        raise BuildError(f"发布失败，已回滚：{failure}" + ("；" + "; ".join(cleanup_errors) if cleanup_errors else "")) from failure
    # 全批切换完成即提交；提交后清理异常只报警，绝不销毁未核对数据或声称已回滚。
    warnings = cleanup_transaction(stages, records)
    return [[str(target / name) for name in names] for target in targets], warnings


# 只探测常见配置根，不递归搜索 HOME，也不凭空创建所有 Agent 目录。
def discover_skill_dirs():
    home = Path.home()
    locations = (("Codex / DeepSeek Harness / 通用 Agent", home / ".agents"),
                 ("Claude Code", home / ".claude"),
                 ("Cursor", home / ".cursor"),
                 ("DeepSeek Harness", home / ".dsh"),
                 ("Pi", home / ".pi" / "agent"))
    return [(name, root / "skills") for name, root in locations if root.is_dir()]


def confirm_install_dir():
    candidates = discover_skill_dirs()
    if not candidates:
        raise BuildError("没有找到常见 Agent 的技能目录；请让 Agent 确认自己的技能目录，再用 --install <目录> 安装")
    print("找到以下技能目录，请确认你使用的 Agent：", file=sys.stderr)
    for number, (name, path) in enumerate(candidates, 1):
        print(f"{number}. {name}：{path}", file=sys.stderr)
    try:
        if len(candidates) == 1:
            chosen = candidates[0]
        else:
            print("请输入目录编号（直接回车取消）：", file=sys.stderr)
            choice = input().strip()
            if not choice.isdigit() or not 1 <= int(choice) <= len(candidates):
                raise BuildError("未选择有效目录，没有安装任何技能")
            chosen = candidates[int(choice) - 1]
        print(f"将整套安装到 {chosen[1]}，确认吗？[y/N]", file=sys.stderr)
        if input().strip().lower() not in {"y", "yes", "是", "确认"}:
            raise BuildError("未确认，没有安装任何技能")
    except (EOFError, KeyboardInterrupt) as error:
        raise BuildError("尚未确认安装目录；请让 Agent 向本人确认后用 --install <目录> 安装") from error
    return chosen[1]


def main(argv=None):
    fsutil.utf8_stdio()
    p = argparse.ArgumentParser(prog="build.py", description=__doc__.splitlines()[0])
    p.add_argument("--out", default=str(ROOT / "dist"), help="产物目录，默认 dist/")
    p.add_argument("--install", nargs="?", const="", metavar="DIR",
                   help="构建后整套安装；省略目录时自动探测并请本人确认")
    args = p.parse_args(argv)
    skills = SKILL_NAMES
    try:
        check_sources(skills)
        names = [PREFIX + s for s in skills]
        out = target_plan(args.out, names)
        install = confirm_install_dir() if args.install == "" else args.install
        target = target_plan(install, names) if install is not None else None
        if target is not None and overlaps(out, target):
            raise BuildError("构建目录与安装目录重叠")
        commit, dirty = git_info()
        info = {"source": "job-mate", "suiteVersion": SUITE_VERSION, "commit": commit, "dirty": dirty,
                "builtAt": fsutil.now().astimezone().isoformat(timespec="seconds")}
        # 所有参数、来源、已有目标先完整检查；所有技能构建成功才发布。
        with tempfile.TemporaryDirectory(prefix="job-mate-build-") as tmp:
            built = [build_skill(s, Path(tmp), info) for s in skills]
            paths, warnings = publish(built, [out, target] if target is not None else [out])
            result = {"built": paths[0], "commit": commit, "dirty": dirty}
            if target is not None:
                result["installed"] = paths[1]
            if warnings:
                result["warnings"] = warnings
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (BuildError, OSError, ValueError) as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())

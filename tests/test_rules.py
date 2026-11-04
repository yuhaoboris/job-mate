"""全核心与技能调用链的静态契约；不证明 Agent 行为、通道或 runtime 兼容。

检查分发边界、外部执行门禁、能力 / 收尾入口和 IM 状态要求。
只读源码与合成代码片段，不操作桌面、平台、IM 或真实工作区。
"""
import ast
import re
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent.parent
CORE_RULES = (ROOT / "shared/execution.md", ROOT / "shared/browser/README.md")
TOOL_DOCUMENTS = (ROOT / "shared/browser/agent-desktop.md", ROOT / "shared/im/lark.md")
TOOL_CALL = re.compile(
    r"lark-cli\b|agent-desktop\s+(?:--[\w-]+|[a-z][\w-]*\b)|desktop\.py|"
    r"launchctl\b|caffeinate\b|npm\s+install|subprocess|"
    r"notify\s+(?:wait|listen|service)\b|workspace\s+check\s+--env",
    re.IGNORECASE,
)
LINK = re.compile(r"\]\(([^\s)]+)\)")


def core_documents():
    return sorted(path for directory in ("skills", "shared", "platforms")
                  for path in (ROOT / directory).rglob("*.md"))


def external_execution_violations(content, allow_build_git=False, allow_lark=False):
    """仅 build 的常量 git 与飞书适配器的唯一受控 run 有例外。"""
    tree = ast.parse(content)
    aliases, violations = {}, []
    forbidden = {"desktop", "agent_desktop", "lark", "tools", "subprocess", "asyncio", "multiprocessing", "pty", "ctypes", "importlib",
                 "runpy", "requests", "urllib", "http", "httpx", "aiohttp", "webbrowser"}
    for node in ast.walk(tree):
        imported = []
        if isinstance(node, ast.Import):
            imported = [(a.asname or a.name, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported = [(a.asname or a.name, (node.module or "") + "." + a.name) for a in node.names]
        for local, name in imported:
            aliases[local] = name
            root = name.split(".")[0]
            if root == "integrations" or root in forbidden and not (root == "subprocess" and (allow_build_git or allow_lark)):
                violations.append((node.lineno, "import", name))

    def name_of(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return name_of(node.value) + "." + node.attr
        return ""

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = name_of(node.func)
        root, _, member = name.partition(".")
        safe_git = (allow_build_git and name == "subprocess.run" and node.args
                    and isinstance(node.args[0], (ast.List, ast.Tuple)) and node.args[0].elts
                    and isinstance(node.args[0].elts[0], ast.Constant) and node.args[0].elts[0].value == "git"
                    and not any(k.arg == "shell" and not (isinstance(k.value, ast.Constant) and k.value.value is False)
                                for k in node.keywords))
        keywords = {k.arg: k.value for k in node.keywords}
        safe_lark = (allow_lark and name == "subprocess.run" and len(node.args) == 1
                     and isinstance(node.args[0], ast.List) and len(node.args[0].elts) == 2
                     and all(isinstance(e, ast.Starred) and isinstance(e.value, ast.Name) and e.value.id == expected
                             for e, expected in zip(node.args[0].elts, ("prefix", "arguments")))
                     and all(isinstance(keywords.get(k), ast.Constant) and keywords[k].value == v
                             for k, v in (("shell", False), ("capture_output", True), ("encoding", "utf-8")))
                     and isinstance(keywords.get("timeout"), ast.Name) and keywords["timeout"].id == "TIMEOUT")
        if safe_git or safe_lark:
            continue
        if (root in forbidden or root == "socket" and member != "gethostname"
                or root == "os" and (member in {"system", "popen", "fork", "forkpty", "startfile"}
                                      or member.startswith(("exec", "spawn", "posix_spawn")))
                or name in {"__import__", "eval", "exec"}):
            violations.append((node.lineno, "call", name))
    return violations


def anchors(content):
    """本任务文档使用简单 Markdown 标题；保留中文并移除标题标点。"""
    return {re.sub(r"[^\w\- ]", "", title.strip().lower()).replace(" ", "-")
            for title in re.findall(r"^#{1,6} (.+)$", content, re.MULTILINE)}


def package_escape(source, target):
    """将链接规范化后判断是否超出核心分发路径，而非只找字面量。"""
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return False
    resolved = (source.parent / unquote(parsed.path)).resolve()
    return not any(resolved.is_relative_to(ROOT / directory)
                   for directory in ("skills", "shared", "scripts", "platforms"))


class ExecutionRulesTest(unittest.TestCase):
    def setUp(self):
        self.execution = CORE_RULES[0].read_text(encoding="utf-8")

    def assert_terms(self, content, terms):
        for term in terms:
            with self.subTest(term=term):
                self.assertIn(term, content)

    def test_contract_covers_all_execution_branches(self):
        sections = {
            "1. 加锁前能力自检": (
                "再获取运行锁", "不写任何记录", "已有可读资料筛选", "缺少外发证据时用 `--no-send` 锁",
                "不操作平台、不回退", "不能把已有可外发锁降级为禁发"),
            "2. 浏览器与目标身份": (
                "本人日常已登录的可见浏览器", "CDP", "Playwright", "内部 API",
                "不模拟人工、不规避平台检测", "恰好一个账号一致", "displayName",
                "不沿用上一页编号", "不把「点击 → 输入 → 发送」合并"),
            "3. 完整读取与否定判断": (
                "完整读取了目标区域", "没有人工活动", "零匹配", "无关区域不要求完整",
                "未读完不能写成没有新答复", "不能据此断言没有接管", "不承诺零留存"),
            "4. 输入与草稿保护": (
                "核对会话之前不读写输入框", "未知内容不清空", "不写 `pending`",
                "no-send、L1", "不输入、不清草稿", "读回必须与拟发文字完全一致",
                "只清除能确认是本次写入的草稿", "前缀", "完整读回确认为空"),
            "5. 外发前 guard 与证据三分类": (
                "每个可能外发的动作之前都执行", "通过后立即执行", "先重新读页面，再 guard",
                "最多重做 1 次", "超时不算未执行", "结构化未投递证据", "不重发",
                "环境状态分开判定", "外层未投递掩盖内层已执行"),
            "结果不明时的停止范围": (
                "工具或环境不明", "业务结果不明", "第二次", "全部 pending",
                "uncertaintyClass", "reconcileProof", "不能伪造", "不计本轮业务不明次数"),
            "6. 尝试阶段与停止前收尾": (
                "连续观察", "所有后续动作（含重做）", "超时", "即使后来读到空会话",
                "本 run 全部未终结的 attempt", "已有终态", "优先于任何停止原因",
                "没有调用过任何可能外发动作", "uncertain", "本人核对", "reconciled",
                "attemptId", "原决定 ID", "缺 ID 的历史事项先待对账", "释放失败保留锁",
                "不持原锁重发", "即使 `reconciled` 为空也须全量对账"),
            "7. 环境受阻、通知与恢复": (
                "纯环境受阻不全局暂停", "安全验证", "先写 inbox，再经 outbox", "去重只发一次",
                "送达不明不自动重发", "原幂等键", "YYYY-MM-DD", "环境提醒不能代替核对事项",
                "保留 `ready`", "不承诺环境恢复后自动续发", "原因、环境恢复办法、恢复后如何重新发起"),
            "8. runtime 调度与进程收尾": (
                "不操作工具、不访问网络、不后台等待或常驻", "进程单实例", "后代进程清理与防睡眠",
                "做不到时不设置无人值守唤起", "权限拒绝或探测不明不能算进程消失",
                "不影响无关进程", "并非原子操作", "不等于真实 Agent 行为或正式验收通过"),
        }
        headings = re.findall(r"^## (.+)$", self.execution, re.MULTILINE)
        self.assertEqual(headings, list(sections))
        for heading, terms in sections.items():
            section = self.execution.split("## " + heading + "\n", 1)[1].split("\n## ", 1)[0]
            self.assert_terms(section, terms)

    def test_browser_is_thin_generic_entry(self):
        content = CORE_RULES[1].read_text(encoding="utf-8")
        self.assertLessEqual(len(content.splitlines()), 25)
        self.assertIn("../execution.md", content)
        self.assertIn("(agent-desktop.md)", content)
        self.assertLess(content.index("agent-desktop"), content.index("Agent 自带"))
        self.assert_terms(content, ("macOS", "Chrome", "辅助功能", "不走 CDP", "不换工具", "锁屏"))
        self.assertNotIn("pi.md", content)
        self.assert_terms(content, ("加锁前", "不操作平台、不回退", "禁发锁", "完整读取", "全部 attempt"))

    def test_all_core_documents_have_no_tool_commands(self):
        for path in core_documents():
            with self.subTest(path=path):
                if path in TOOL_DOCUMENTS:
                    continue  # 仅这两份随包工具规则可写外部命令。
                self.assertIsNone(TOOL_CALL.search(path.read_text(encoding="utf-8")))

    def test_tool_call_detector_accepts_compatibility_literals_only(self):
        for literal in ('"lark"', '`agent-desktop`'):
            self.assertIsNone(TOOL_CALL.search(literal))
        for call in ("lark-cli im", "agent-desktop click", "agent-desktop --headed", "scripts/desktop.py",
                     "launchctl bootout", "caffeinate", "npm install", "import subprocess",
                     "notify wait", "notify listen", "notify service install", "workspace check --env"):
            self.assertIsNotNone(TOOL_CALL.search(call))

    def test_core_does_not_link_out_of_package(self):
        self.assertEqual(set(TOOL_DOCUMENTS), {ROOT / "shared/browser/agent-desktop.md", ROOT / "shared/im/lark.md"})
        self.assertTrue(set(TOOL_DOCUMENTS) <= set(core_documents()))
        for directory in ("skills", "shared", "platforms"):
            for source in (ROOT / directory).rglob("*.md"):
                for target in LINK.findall(source.read_text(encoding="utf-8")):
                    with self.subTest(source=source, target=target):
                        self.assertFalse(package_escape(source, target))

    def test_boundary_detector_normalizes_paths(self):
        source = ROOT / "shared/browser/README.md"
        for target in ("../../outside-tools/README.md", "../../%6futside-tools/README.md",
                       "../browser/../../outside-tools/README.md"):
            self.assertTrue(package_escape(source, target))
        for target in ("../execution.md#1-加锁前能力自检", "../../scripts/ledger.py",
                       "https://example.invalid/reference"):
            self.assertFalse(package_escape(source, target))

    def test_core_scripts_have_no_external_execution_except_build_git(self):
        scripts = sorted((ROOT / "scripts").glob("*.py"))
        self.assertEqual({p.name for p in scripts}, {"ledger.py", "workspace.py", "notify.py", "build.py", "fsutil.py", "platforms.py", "outbound.py", "config_txn.py", "config_schema.py", "im_lark.py", "im_scan.py"})
        for source in scripts:
            content = source.read_text(encoding="utf-8")
            with self.subTest(source=source):
                self.assertEqual(external_execution_violations(content, source.name == "build.py", source.name == "im_lark.py"), [])
                if source.name == "im_lark.py":
                    continue  # 适配器固定协议字符串由真实替身/Node及禁用进程测试校验。
                for node in ast.walk(ast.parse(content)):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        self.assertIsNone(TOOL_CALL.search(node.value), (source, node.lineno, node.value))

    def test_external_execution_detector_rejects_aliases_and_non_git_build_calls(self):
        bad = ("import subprocess as sp; sp.run(['tool'])", "from os import system as run; run('tool')",
               "import os; os.posix_spawn('tool', [], {})", "import socket; socket.create_connection(('host', 1))",
               "__import__('subprocess')", "import desktop", "from lark import send",
               "import integrations.pi", "import urllib.request")
        for content in bad:
            self.assertTrue(external_execution_violations(content), content)
        for content in ("import subprocess; subprocess.run(['tool'])",
                        "import subprocess; subprocess.run('git status', shell=True)",
                        "import subprocess; subprocess.Popen(['git'])"):
            self.assertTrue(external_execution_violations(content, True), content)
        for content in ("import subprocess; subprocess.run([\x27cmd.exe\x27], shell=False)",
                        "import subprocess; subprocess.Popen([\x27node\x27])",
                        "import os; os.system(\x27node\x27)"):
            self.assertTrue(external_execution_violations(content, allow_lark=True), content)
        self.assertFalse(external_execution_violations("import socket; socket.gethostname()"))
        self.assertFalse(external_execution_violations("import subprocess; subprocess.run(['git', 'status'])", True))

    def test_new_document_links_and_anchors_resolve(self):
        maintained = (ROOT / name for name in ("README.md", "CONTRIBUTING.md", "SECURITY.md",
                                                "docs/design.md", "docs/README.md"))
        for source in (*core_documents(), *maintained):
            for target in LINK.findall(source.read_text(encoding="utf-8")):
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc:
                    continue
                resolved = ((source.parent / unquote(parsed.path)).resolve()
                            if parsed.path else source.resolve())
                with self.subTest(source=source, target=target):
                    self.assertTrue(resolved.is_relative_to(ROOT), target)
                    self.assertTrue(resolved.is_file(), target)
                    if parsed.fragment:
                        self.assertIn(unquote(parsed.fragment), anchors(resolved.read_text(encoding="utf-8")))
        self.assert_terms(self.execution, ("(notify.md)", "(ledger.md#释放与对账契约)"))

    def test_reference_scope_and_preserved_safety_knowledge(self):
        desktop = TOOL_DOCUMENTS[0].read_text(encoding="utf-8")
        self.assert_terms(desktop, ("after_action", "INVALID_ARGS", "UTF-8", "8 MiB", "EPERM",
                                    "steps 缺失", "非空 steps", "data.complete", "subtree_truncated",
                                    "禁止所有后台 type", "后台写入尚未验证", "not_delivered 不作为动作成功前提"))
        self.assert_terms(desktop, ("macOS", "1.0.2 / JSON 2.5", "2026-11-04",
                                    "https://github.com/vercel-labs/agent-desktop", "#install",
                                    "主版本为 **1**", "主版本为 **2**", "granted", "可读取窗口",
                                    "禁止 `launch --cdp`", "所有 CDP", "不开 trace", "不做调试截图导出",
                                    "contenteditable 禁止 `set-value`", "type --headed", "profile.browser.headedInput",
                                    "未允许时不输入", "锁屏", "请本人解锁", "不重试绕过",
                                    "主版本只是必要条件", "2.6", "没有因此成为本项目已验证能力",
                                    "请求绑定", "data.action", "error.details.steps", "error.details.after_action",
                                    "超时或输出异常一律 unknown", "仅 retry:safe 不够", "最多重做该动作一次"))
        lark = (ROOT / "shared/im/lark.md").read_text(encoding="utf-8")
        self.assert_terms(lark, ("1.0.96", "doctor", "sync --fetch-only", "sync --send-only",
                                "顶层数组", "顶层诊断对象", "identities.bot.available/verified",
                                "bot.openId 不是 ownerId", "contact 响应未独立实测",
                                "contact +get-user --as user", "contact +search-user --query",
                                "message_id/chat_id", "parent_id", "连续前缀 complete:false",
                                "99992351", "sent、不带时间", "unknown", "不重发",
                                "整批拒收，游标不动", "固定模板", "shell=False", "30 秒",
                                "20 页是单次", "checkpoint", "businessCursorMs", "scanThroughMs", "接收凭据不是扫描进度"))
        self.assertFalse((ROOT / "integrations").exists())

    def test_all_skill_entries_check_capability_before_lock(self):
        for name in ("apply", "followup", "setup"):
            skill = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(skill=name):
                self.assertIn("../../shared/execution.md", skill)
                self.assertLess(skill.index("能力自检"), skill.index("ledger lock acquire --skill " + name))
                self.assert_terms(skill, ("不操作平台、不回退", "不写记录", "释放与对账契约", "reconciled", "释放失败"))
                self.assertNotRegex(skill, r"使用 agent-desktop 时|agent-desktop 的任何停止")
        for name in ("apply", "followup"):
            skill = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assert_terms(skill, ("本轮所有", "pending", "已有终态不覆盖", "attemptId", "原决定 ID",
                                     "released", "--no-send", "guard", "未知", "不重发"))
        self.assertIn("恢复后，任何外发前重新执行去重", self.execution)

    def test_followup_im_chain_and_fail_closed_states_are_explicit(self):
        skill = (ROOT / "skills/followup/SKILL.md").read_text(encoding="utf-8")
        sync = skill.split("### 2.0 同步 IM", 1)[1].split("### 2.1", 1)[0]
        self.assert_terms(sync, ("im_lark.py sync", "连续分页", "当前路由", "readable", "原 idempotencyKey",
                                "larkProfile", "handoff 无 receipt", "unknown 不重发", "IM 未读完",
                                "没有 IM 能力", "不另找通道", "incomingRejected", "ledger_unreadable",
                                "clarify 已 sent 才标 clarifying", "queued、blocked、failed、unknown"))
        self.assert_terms(skill, ("第 2.0 节完整同步链", "核心脚本不等待"))

    def test_setup_uses_lark_configuration_and_explicit_im_test(self):
        skill = (ROOT / "skills/setup/SKILL.md").read_text(encoding="utf-8")
        self.assert_terms(skill, ("ownerId", "larkProfile", "waitMinutes", "larkCommand",
                                 "notify test", "只排队（queued）", "im_lark.py doctor", "im_lark.py sync",
                                 "notify status", "notify replies --state test", "引用测试消息回复「收到」",
                                 "普通业务答复仍 received", "能力不足就停"))

    def test_skill_im_checkpoints_use_adapter_not_manual_transport(self):
        for relative in ("skills/apply/SKILL.md", "skills/followup/SKILL.md", "skills/setup/SKILL.md", "shared/notify.md"):
            with self.subTest(path=relative):
                content = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("im_lark.py sync", content)
                self.assertIn("shared/im/lark.md" if relative.startswith("skills/") else "im/lark.md", content)
                self.assertNotRegex(content, r"notify (?:outbox --claim|receipt|incoming)\b")
        followup = (ROOT / "skills/followup/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("每 10 分钟运行 `im_lark.py sync`", followup)

    def test_platform_facts_do_not_reintroduce_tool_instructions(self):
        for path in (ROOT / "platforms/boss").glob("*.md"):
            content = path.read_text(encoding="utf-8")
            self.assertIsNone(TOOL_CALL.search(content), path)
            self.assertNotIn("agent-desktop", content, path)
        common = (ROOT / "platforms/boss/common.md").read_text(encoding="utf-8")
        self.assert_terms(common, ("chat-input", "enabled", "草稿", "残留",
                                   "`jobUrl`、`jobId` 记 null", "不通过其他接口补取"))
        followup = (ROOT / "platforms/boss/followup.md").read_text(encoding="utf-8")
        self.assert_terms(followup, ("待本人实测确认", "messageId.available: false", "时间标签", "归组"))

    def test_all_repository_document_links_and_anchors_resolve(self):
        documents = sorted({*ROOT.glob("*.md"),
                            *(p for directory in ("docs", "assets", "shared", "skills", "platforms")
                              for p in (ROOT / directory).rglob("*.md"))})
        cached_anchors = {}
        for source in documents:
            for target in LINK.findall(source.read_text(encoding="utf-8")):
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc:
                    continue
                resolved = ((source.parent / unquote(parsed.path)).resolve()
                            if parsed.path else source.resolve())
                with self.subTest(source=source.relative_to(ROOT), target=target):
                    self.assertTrue(resolved.is_relative_to(ROOT), target)
                    self.assertTrue(resolved.exists(), target)
                    if parsed.fragment:
                        self.assertTrue(resolved.is_file(), target)
                        if resolved not in cached_anchors:
                            cached_anchors[resolved] = anchors(resolved.read_text(encoding="utf-8"))
                        self.assertIn(unquote(parsed.fragment), cached_anchors[resolved])

    def test_current_guides_do_not_embed_development_history(self):
        guides = [ROOT / name for name in ("README.md", "AGENTS.md", "CONTRIBUTING.md", "SECURITY.md",
                                           "docs/README.md", "docs/design.md")]
        guides.extend(TOOL_DOCUMENTS)
        for source in guides:
            with self.subTest(source=source.relative_to(ROOT)):
                self.assertNotRegex(source.read_text(encoding="utf-8"),
                                    r"父会话|W/N/X|已移除|解耦后|旧格式安装|执行层迁移|更名同步|原环境检测选项")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("其他路由待办的本地恢复", readme)
        self.assertNotRegex(readme, r"notify\s+(?:wait|listen|service)\b|scripts/desktop\.py")

    def test_document_examples_do_not_expose_account_ids_or_home_paths(self):
        documents = [*ROOT.glob("*.md"),
                     *(p for directory in ("docs", "assets", "shared", "skills", "platforms")
                       for p in (ROOT / directory).rglob("*.md"))]
        for source in documents:
            with self.subTest(source=source.relative_to(ROOT)):
                self.assertNotRegex(source.read_text(encoding="utf-8"),
                                    r"(?:cli_|ou_|oc_)[A-Za-z0-9]{4,}|/(?:Users|home)/(?![.<])[^/\s<>]+")

    def test_removed_executor_and_preserved_workspace_tests(self):
        for relative in ("scripts/desktop.py", "tests/test_desktop.py", "shared/browser/pi.md"):
            self.assertFalse((ROOT / relative).exists(), relative)
        # 保留纯配置、禁发锁与运行隔离回归，统一放在工作区测试中。
        maintained = ast.parse((ROOT / "tests/test_workspace.py").read_text(encoding="utf-8"))
        methods = {node.name for node in ast.walk(maintained) if isinstance(node, ast.FunctionDef)}
        self.assertTrue({"test_browser_accepts_boolean_headed_input_and_rejects_unknown_fields",
                         "test_new_browser_after_account_defaults_false_without_writing_profile",
                         "test_browser_partial_skip_keep_and_resume_do_not_write_profile",
                         "test_no_send_lock_rejects_pending_and_guard",
                         "test_reacquire_same_run_never_changes_no_send"} <= methods)
        self.assertFalse((ROOT / "tests/test_workspace_runtime.py").exists())


if __name__ == "__main__":
    unittest.main()

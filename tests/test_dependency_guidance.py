"""安装指引与分发回归；不下载依赖、不执行真实桌面/飞书工具。

这是 Agent 可读流程的静态检查与临时安装行为检查，不证明真实 Agent
会遵循全部步骤，也不把 CLI 安装或权限检查当作平台发送验证。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_build import ROOT, run_build


class DependencyGuidanceTest(unittest.TestCase):
    def setUp(self):
        self.dependencies = (ROOT / "shared/dependencies.md").read_text(encoding="utf-8")
        self.install = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
        self.desktop = (ROOT / "shared/browser/agent-desktop.md").read_text(encoding="utf-8")
        self.setup = (ROOT / "skills/setup/SKILL.md").read_text(encoding="utf-8")
        self.lark = (ROOT / "shared/im/lark.md").read_text(encoding="utf-8")

    def test_install_and_setup_reach_dependency_preparation_before_use(self):
        self.assertIn("(shared/dependencies.md)", self.install)
        self.assertLess(self.install.index("(shared/dependencies.md)"),
                        self.install.index("python3 scripts/build.py"))
        self.assertIn("(../../shared/dependencies.md)", self.setup.split("## 1.", 1)[0])
        self.assertIn("(../dependencies.md)", self.desktop)
        self.assertIn("../dependencies.md#3-飞书依赖仅选择飞书时", self.lark)
        for term in ("无需逐项确认", "自动补齐", "系统授权", "管理员认证", "登录", "扫码"):
            self.assertIn(term, self.dependencies + self.install)
        for old_rule in ("缺少时给本人一个安装办法，再停止", "缺工具时只向本人提供官方安装链接",
                         "Agent 不自动安装", "技能不安装、登录", "不安装、不 shell 回退"):
            self.assertNotIn(old_rule, self.install + self.desktop + self.lark + self.setup)

    def test_supported_versions_and_isolated_persistent_paths_are_explicit(self):
        for term in ("Python 3.9+", "py -3", "Node >=18", "受支持的 LTS",
                     "agent-desktop@1.0.2", "@larksuite/cli@1.0.96",
                     "XDG_DATA_HOME", "LOCALAPPDATA", "不放临时目录", "不降级已有工具",
                     "--prefix", "desktop/bin/agent-desktop", "lark/bin/lark-cli",
                     "node_modules/@larksuite/cli/scripts/run.js", "larkCommand"):
            self.assertIn(term, self.dependencies)
        self.assertNotIn("@latest", self.dependencies)
        # 保持实际适配器的精确版本门禁，不以安装文档放宽代码边界。
        adapter = (ROOT / "scripts/im_lark.py").read_text(encoding="utf-8")
        self.assertIn(r"lark-cli version 1\.0\.96", adapter)

    def test_desktop_loads_version_matched_skill_before_operating(self):
        section = self.desktop.split("## 加载上游操作 skill\n", 1)[1].split("## 原生浏览器与输入", 1)[0]
        for term in ("agent-desktop skills get desktop", "agent-desktop skills get platform",
                     "agent-desktop <command> --help", "CLI 版本变化", "绝对路径",
                     "加载失败就停止", "不能放宽", "禁止 CDP", "不抓取招聘页面"):
            self.assertIn(term, section)
        self.assertIn("Linux/Windows", self.dependencies)
        self.assertIn("平台操作受阻", self.dependencies)

    def test_optional_lark_and_human_authorization_remain_separate(self):
        lark = self.dependencies.split("## 3. 飞书依赖", 1)[1].split("## 4.", 1)[0]
        for term in ("未选择飞书则跳过", "1.0.96", "配置命令也显式指定目标档",
                     "机器人", "im:message:send_as_bot", "im:message:readonly",
                     "本人身份", "事务提交", "im_lark.py doctor", "另行征得测试内容同意"):
            self.assertIn(term, lark)
        self.assertIn("apply/followup 运行中不安装或升级", self.dependencies)
        self.assertIn("不安装后台服务", self.dependencies)

    def test_install_success_requires_readiness_not_only_package_copy(self):
        for term in ("scripts/workspace.py --help", "scripts/ledger.py --help",
                     "技能包已安装，运行准备尚未完成", "不删除持久依赖目录",
                     "不运行平台或飞书外发测试"):
            self.assertIn(term, self.install)
        for term in ("技能包", "脚本环境", "桌面环境", "飞书", "本人动作",
                     "上游 skill 已读", "不代表真实 BOSS 兼容", "不写验收状态"):
            self.assertIn(term, self.dependencies)

    def test_temporary_suite_install_contains_complete_reachable_guidance(self):
        with tempfile.TemporaryDirectory(prefix="job-mate-dependencies-") as temporary:
            root = Path(temporary)
            code, result = run_build("--out", str(root / "build"), "--install", str(root / "skills"))
            self.assertEqual(code, 0, result)
            self.assertEqual(len(result["installed"]), 3)
            for directory in map(Path, result["installed"]):
                with self.subTest(skill=directory.name):
                    dependency = directory / "shared/dependencies.md"
                    self.assertTrue(dependency.is_file())
                    text = dependency.read_text(encoding="utf-8")
                    self.assertIn("(browser/agent-desktop.md#加载上游操作-skill)", text)
                    self.assertIn("(im/lark.md)", text)
                    manifest = json.loads((directory / "BUILD.json").read_text(encoding="utf-8"))
                    self.assertIn("shared/dependencies.md", manifest["files"])
                    self.assertIn("agent-desktop skills get desktop",
                                  (directory / "shared/browser/agent-desktop.md").read_text(encoding="utf-8"))
                    if directory.name == "job-mate-setup":
                        self.assertIn("(shared/dependencies.md)",
                                      (directory / "SKILL.md").read_text(encoding="utf-8"))
                    for script in ("workspace.py", "ledger.py"):
                        completed = subprocess.run(
                            [sys.executable, "-B", str(directory / "scripts" / script), "--help"],
                            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=15,
                        )
                        self.assertEqual(completed.returncode, 0, completed.stderr)
                        self.assertIn("--help", completed.stdout)
            # 依赖由安装 Agent 编排；离线构建器不安装第三方软件或创建求职工作区。
            self.assertEqual({p.name for p in root.iterdir()}, {"build", "skills"})
            self.assertFalse(list(root.rglob("job-mate.json")))


if __name__ == "__main__":
    unittest.main()

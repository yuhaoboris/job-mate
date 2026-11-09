"""对实际规则测试注入文档变异；每个目标单独断言检出，不合并语料。"""
import io
import unittest
from pathlib import Path
from unittest.mock import patch

import test_rules
import test_setup

ROOT = Path(__file__).resolve().parents[1]


class MovedGuardMutationTest(unittest.TestCase):
    def mutated_result(self, relative, anchor, payload, case_type, method):
        original = Path.read_text
        target = (ROOT / relative).resolve()
        source = original(target, encoding="utf-8")
        # 注入前提在守卫 runner 外检查，不能把缺锚点误计为守卫检出。
        if anchor is None:
            mutated = source + "\n" + payload + "\n"
        else:
            self.assertEqual(source.count(anchor), 1)
            mutated = source.replace(anchor, anchor + "\n" + payload + "\n", 1)
        self.last_read_targets = set()

        def read(path, *args, **kwargs):
            self.last_read_targets.add(path.resolve())
            if path.resolve() == target:
                return mutated
            return original(path, *args, **kwargs)

        output = io.StringIO()
        with patch.object(Path, "read_text", read):
            result = unittest.TextTestRunner(stream=output).run(unittest.TestSuite([case_type(method)]))
        return result, output.getvalue()

    def assert_detected(self, relative, anchor, payload, case_type, method):
        result, output = self.mutated_result(relative, anchor, payload, case_type, method)
        self.assertEqual(result.errors, [], output)
        self.assertGreater(len(result.failures), 0, f"MUTATION_ESCAPED {relative}: {payload!r}\n{output}")

    def test_f6_manual_transport_mutations_are_detected_at_each_destination(self):
        for relative, anchor in (("shared/im/lark-setup.md", "4. bot 验证通过"),
                                 ("skills/setup/SKILL.md", "## 10. IM 通知"),
                                 ("shared/decision-exec.md", "### 2.2 执行决定")):
            with self.subTest(path=relative):
                self.assert_detected(relative, anchor, "notify incoming", test_rules.ExecutionRulesTest,
                                     "test_skill_im_checkpoints_use_adapter_not_manual_transport")

    def test_f11_manual_transport_guard_covers_the_entire_core_scope(self):
        # 独立从磁盘范围计算期望目标，不复用被测守卫的文件集合或审核清单。
        targets = sorted(p for p in ROOT.rglob("*.md") if p.relative_to(ROOT).parts[0] in {"shared", "skills", "platforms"})
        self.assertTrue(targets)
        for path in targets:
            relative = path.relative_to(ROOT).as_posix()
            with self.subTest(path=relative):
                self.assert_detected(relative, None, "notify incoming", test_rules.ExecutionRulesTest,
                                     "test_skill_im_checkpoints_use_adapter_not_manual_transport")
                self.assertIn(path.resolve(), self.last_read_targets)

    def test_f11_tool_branch_guard_covers_core_except_exact_tool_references(self):
        # T 是报告/既有接口固定的三份许可定义；主体仍独立从磁盘派生。
        references = {"shared/browser/agent-desktop.md", "shared/im/lark.md", "shared/dependencies.md"}
        targets = sorted(p for p in ROOT.rglob("*.md") if p.relative_to(ROOT).parts[0] in {"shared", "skills", "platforms"}
                         and p.relative_to(ROOT).as_posix() not in references)
        self.assertTrue(targets)
        for path in targets:
            relative = path.relative_to(ROOT).as_posix()
            with self.subTest(path=relative):
                self.assert_detected(relative, None, "使用 agent-desktop 时", test_rules.ExecutionRulesTest,
                                     "test_all_skill_entries_check_capability_before_lock")
                self.assertIn(path.resolve(), self.last_read_targets)

    def test_f7_each_obsolete_term_is_detected_in_each_policy_document(self):
        for relative, anchor in (("shared/ask.md", "## 对话规则"),
                                 ("skills/setup/SKILL.md", "### 3.2 按屏或步骤提问"),
                                 ("docs/design.md", "### 5.2.1 对话规则")):
            for term in ("工具名待", "待在 DSH 实测确认", "DeepSeek Harness 的提问卡片工具",
                         "未确认的授权保持待确认", "跳过不等于授权"):
                with self.subTest(path=relative, term=term):
                    self.assert_detected(relative, anchor, term, test_setup.SetupStepsTest,
                                         "test_dsh_question_contract_and_skip_defaults_match_policy")

    def test_f8_tool_scope_mutation_is_detected_after_decision_move(self):
        for relative, anchor in (("shared/decision-exec.md", "### 2.2 执行决定"),
                                 ("skills/followup/SKILL.md", "## 1.")):
            with self.subTest(path=relative):
                self.assert_detected(relative, anchor, "使用 agent-desktop 时", test_rules.ExecutionRulesTest,
                                     "test_all_skill_entries_check_capability_before_lock")


if __name__ == "__main__":
    unittest.main()

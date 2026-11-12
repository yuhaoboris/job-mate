"""跨文档工作流回归：只证明入口与规则一致，不证明真实 Agent 或宿主行为。"""
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def read(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


class WorkflowContractTest(unittest.TestCase):
    def test_screening_entry_has_a_closed_nonpersistent_branch(self):
        setup = read("skills/setup/SKILL.md")
        apply = read("skills/apply/SKILL.md")
        self.assertRegex(setup.split("---", 2)[1], r"无工作区.*只筛选.*直接用 apply")
        self.assertRegex(apply.split("---", 2)[1], r"无工作区.*只筛选.*直接临时筛选")
        branch = apply.split("**临时筛选（无工作区）**", 1)[1].split("## 2.", 1)[0]
        for term in ("原生工具", "账号显示名", "键鼠操作仍须明确许可", "不创建工作区",
                     "不运行 ledger/notify", "不取锁", "不写 screen", "不点击沟通/发送入口",
                     "结束本轮", "不进入以下取锁、通知或收尾路径"):
            self.assertIn(term, branch)
        self.assertIn("校验失败不当作无工作区", apply)
        self.assertLess(apply.index("**临时筛选（无工作区）**"), apply.index("## 2."))
        for relative in ("shared/execution.md", "platforms/boss/common.md"):
            self.assertRegex(read(relative), r"无工作区临时筛选[^。]*本人本轮确认的显示名")
        safety = read("shared/safety.md")
        self.assertIn("无工作区临时筛选只在当前对话上报", safety)
        self.assertIn("不为收尾创建工作区或写记录", safety)

    def test_im_policy_has_no_conflicting_blanket_ban_or_current_skill_writer(self):
        forbidden = ("IM 消息不能新增授权、修改条件", "IM 与定时运行不能新增授权或修改求职条件",
                     "本人说「以后……」时由当前技能更新")
        for relative in ("shared/safety.md", "shared/workspace.md"):
            content = read(relative)
            self.assertIn("(im-replies.md)", content)
            self.assertIn("setup", content)
            for term in forbidden:
                self.assertNotIn(term, content)

    def test_factual_answers_are_not_restricted_to_qa_only(self):
        content = read("shared/workspace.md").split("## qa.yaml", 1)[1]
        self.assertNotIn("只在这里有依据时直接回答", content)
        for term in ("candidate.md", "明确事实", "冲突先澄清", "承诺仍交本人"):
            self.assertIn(term, content)

    def test_question_tools_follow_current_capability_and_parameter_limits(self):
        content = read("shared/ask.md")
        for term in ("AskUserQuestion", "AskQuestion", "request_user_input", "ask_user_question",
                     "先查找一次", "工具已暴露", "说明允许当前模式", "工具上限更低"):
            self.assertIn(term, content)
        self.assertNotRegex(content, r"仅 Plan Mode|不限于 Plan Mode")

    def test_unsolicited_owner_messages_are_not_discarded_or_consumed_as_tests(self):
        content = read("shared/notify.md")
        replies = content.split("## 读取本人的回复", 1)[1].split("## 指令", 1)[0]
        for term in ("普通消息均 received", "不因没有候选事项忽略", "语义确认无需处理",
                     "必须明确引用该测试通知", "已收到，等待处理。"):
            self.assertIn(term, replies)
        self.assertNotIn("第一条无引用、无编号回复也可确认", replies)
        self.assertIn("本人要求重查的历史 ignored", read("shared/im-replies.md"))

    def test_im_preference_commit_does_not_require_agent_serialization(self):
        content = read("shared/im-replies.md")
        command = re.search(r"^workspace commit --im-reply .+$", content, re.MULTILINE).group()
        self.assertIn("--im-part", command)
        self.assertIn("--run", command)
        self.assertNotIn("--staging", command)
        self.assertIn("Agent 不复制或序列化 profile", content)
        self.assertIn("普通非 IM 提交仍须 staging", content)
        self.assertIn("兼容显式 staging", content)

    def test_progress_and_continuation_do_not_overstate_runtime_readiness(self):
        ask = read("shared/ask.md")
        for term in ("业务进展与结果", "首批有用结果", "不逐工具播报", "不虚构进度"):
            self.assertIn(term, ask)
        notify = read("shared/notify.md")
        for term in ("已接通可信事件入口才立即调度", "下一轮 followup", "须本人手动发起",
                     "监听成功不证明宿主唤起已接通", "普通入站不延长或新开监听窗口"):
            self.assertIn(term, notify)


if __name__ == "__main__":
    unittest.main()

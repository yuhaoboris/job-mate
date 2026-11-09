"""0–60 监听窗口从同一个 im_config 准入；不迁移旧配置。"""
import copy
import unittest

import test_workspace

C = test_workspace.workspace.config_schema
N = test_workspace.workspace.notify


class WaitMinutesTest(unittest.TestCase):
    def test_effective_profile_uses_one_boundary_with_platform_overrides(self):
        for platform in (None, "boss"):
            for wait in (None, 0, 1, 60):
                profile = copy.deepcopy(test_workspace.PROFILE)
                profile["im"] = {"channel": "lark", "ownerId": "synthetic", "larkProfile": "default"}
                if wait is not None:
                    profile["im"]["waitMinutes"] = wait
                self.assertEqual(C.effective_profile(profile, platform)["im"]["waitMinutes"],
                                 60 if wait is None else wait)
            for wait in (61, 240, 241, True, False, -1, "60", 60.0):
                profile["im"]["waitMinutes"] = wait
                before = copy.deepcopy(profile)
                with self.assertRaises((N.NotifyError, test_workspace.ledger.LedgerError)):
                    C.effective_profile(profile, platform)
                self.assertEqual(profile, before)


if __name__ == "__main__":
    unittest.main()

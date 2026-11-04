"""开源文件与仓库隐私边界检查。"""
import hashlib
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent


ID_PATTERN = re.compile(r"\b(?:ou_|oc_|om_|cli_)[A-Za-z0-9*]{8,}\b")
PUBLIC_ID_KEYS = {"method_id", "api_method_id", "methodid"}


def identity_key(key):
    key = re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()
    return key not in PUBLIC_ID_KEYS and (key == "id" or key.endswith("_id") or key.endswith("_key")
                                           or key in {"reply_to", "page_token", "appid", "logid", "tenantkey"})


def private_values(value):
    """按字段及URL上下文提取未知前缀标识，不把公共API方法/错误码当个人ID。"""
    identifiers, positions = set(), set()
    def walk(obj, key=""):
        if isinstance(obj, dict):
            for name, item in obj.items():
                walk(item, name)
        elif isinstance(obj, list):
            for item in obj:
                walk(item, key)
        elif isinstance(obj, str):
            normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()
            if obj and (identity_key(key) or normalized in {"name", "user_name", "app_name", "profile", "profile_name", "user"}):
                identifiers.add(obj)
            if obj and key == "message_position":
                positions.add(obj)
            identifiers.update(ID_PATTERN.findall(obj))
            try:
                nested = json.loads(obj)
            except ValueError:
                nested = None
            if isinstance(nested, (dict, list)):
                walk(nested)
            for url in re.findall(r"https?://[^\s<>]+", obj):
                for name, item in parse_qsl(urlsplit(url.rstrip("\"'.,)")).query, keep_blank_values=True):
                    if item and identity_key(name):
                        identifiers.add(item)
                    if item and name == "position":
                        positions.add(item)
        elif type(obj) is int and identity_key(key):
            identifiers.add(str(obj))
    walk(value)
    return identifiers, positions


def privacy_leaks(files, identifiers, positions):
    leaks = []
    for path in files:
        raw = path.read_text(encoding="utf-8")
        text = [raw, unquote(raw)]
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = raw
        def strings(obj):
            if isinstance(obj, str):
                text.extend((obj, unquote(obj)))
                try:
                    nested = json.loads(obj)
                except ValueError:
                    nested = None
                if isinstance(nested, (dict, list)):
                    strings(nested)
            elif isinstance(obj, dict):
                for item in obj.values():
                    strings(item)
            elif isinstance(obj, list):
                for item in obj:
                    strings(item)
        strings(parsed)
        _, found_positions = private_values(parsed)
        if any(token in part for token in identifiers for part in text) or positions & found_positions:
            leaks.append(path.name)  # 失败诊断不复述真实ID。
    return leaks


class RepoHygieneTest(unittest.TestCase):
    def test_privacy_scanner_catches_opaque_and_embedded_identifiers_not_public_codes(self):
        raw = {"sender": {"id": "opaque-person-123", "tenant_key": "opaque-tenant-456", "open_bot_id": "ou_" + "9" * 32},
               "error": {"log_id": "OpaqueLogABC789", "code": 99992351,
                         "troubleshooter": "https://example.invalid/?log_id=OpaqueLogABC789&method_id=6936075528891154460"},
               "page_token": "opaque-page-token", "message_position": "732",
               "name": "Private profile label", "userName": "Private owner name", "appName": "Private app name"}
        ids, positions = private_values(raw)
        self.assertIn("opaque-tenant-456", ids)
        self.assertIn("OpaqueLogABC789", ids)
        self.assertIn("opaque-person-123", ids)
        self.assertTrue({"Private profile label", "Private owner name", "Private app name"} <= ids)
        self.assertNotIn("99992351", ids)
        self.assertNotIn("6936075528891154460", ids)
        self.assertEqual(positions, {"732"})
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "fixture.json"
            file.write_text(json.dumps({"diagnostic": "https://example.invalid/?tenant_key=opaque%2Dtenant%2D456"}), encoding="utf-8")
            self.assertEqual(privacy_leaks([file], ids, positions), ["fixture.json"])
            file.write_text(json.dumps({"error": {"code": 99992351}, "method_id": "6936075528891154460"}), encoding="utf-8")
            self.assertEqual(privacy_leaks([file], ids, positions), [])
            file.write_text(json.dumps({"url": "https://example.invalid/?position=732"}), encoding="utf-8")
            self.assertEqual(privacy_leaks([file], ids, positions), ["fixture.json"])

    def test_lark_samples_preserve_types_precision_relations_and_channels(self):
        folder = ROOT / "tests/fakes/lark_samples"
        names = {"send", "send-dup", "mget", "list", "send-reject", "native-get", "native-list-page1", "native-list-page2", "profile-list", "auth-status"}
        self.assertEqual({p.stem for p in folder.glob("*.json")}, names)
        samples = {name: json.loads((folder / (name + ".json")).read_text(encoding="utf-8")) for name in names}
        sent = samples["send"]["data"]
        self.assertRegex(sent["create_time"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertEqual(samples["send"], samples["send-dup"])
        messages = samples["list"]["data"]["messages"]
        self.assertEqual(messages[0]["message_id"], sent["message_id"])
        self.assertEqual(messages[1]["reply_to"], sent["message_id"])
        self.assertNotIn("parent_id", messages[1])
        for message in messages:
            self.assertRegex(message["create_time"], r"^\d{4}-\d\d-\d\d \d\d:\d\d$")
            self.assertEqual(parse_qsl(urlsplit(message["message_app_link"]).query)[1], ("position", message["message_position"]))
        self.assertEqual(messages[0]["sender"]["sender_type"], "app")
        self.assertEqual(messages[0]["sender"]["id_type"], "app_id")
        self.assertEqual(messages[1]["sender"]["sender_type"], "user")
        self.assertFalse(samples["list"]["data"]["has_more"])
        self.assertTrue(samples["list"]["meta"]["pagination"]["complete"])
        first = samples["native-list-page1"]["data"]
        second = samples["native-list-page2"]["data"]
        self.assertTrue(first["has_more"])
        self.assertTrue(first["page_token"])
        self.assertFalse(second["has_more"])
        self.assertEqual(second["page_token"], "")
        for name in ("native-get", "native-list-page1", "native-list-page2"):
            for message in samples[name]["data"]["items"]:
                self.assertRegex(message["create_time"], r"^\d{13}$")
                self.assertRegex(message["update_time"], r"^\d{13}$")
                self.assertIsInstance(json.loads(message["body"]["content"])["text"], str)
        native_reply = second["items"][0]
        self.assertEqual(native_reply["parent_id"], sent["message_id"])
        self.assertEqual(native_reply["root_id"], sent["message_id"])
        self.assertEqual(samples["send-reject"]["error"]["code"], 99992351)
        self.assertFalse(samples["send-reject"]["ok"])
        profiles, auth = samples["profile-list"], samples["auth-status"]
        self.assertIsInstance(profiles, list)
        selected = next(p for p in profiles if p.get("effective"))
        self.assertEqual(selected["name"], "synthetic-profile")
        self.assertEqual(selected["appId"], auth["appId"])
        self.assertIs(auth["identities"]["bot"]["verified"], True)
        self.assertNotEqual(auth["identities"]["bot"]["openId"], auth["identities"]["user"]["openId"])
        self.assertNotIn("ok", auth)
        self.assertNotIn("data", auth)
        self.assertTrue((folder / "NOTES.md").is_file())

    def test_lark_raw_identifiers_do_not_leak_into_public_samples(self):
        raw = ROOT / ".private/lark-samples/raw"
        if not raw.is_dir():
            self.skipTest("无私有原始采样目录，不依赖私有数据")
        ids, positions = set(), set()
        for path in raw.iterdir():
            if path.suffix not in (".json", ".stdout", ".stderr") or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            try:
                value = json.loads(text)
            except ValueError:
                value = text
            found, located = private_values(value)
            ids.update(found)
            positions.update(located)
        public = list((ROOT / "tests/fakes/lark_samples").glob("*"))
        for path in (ROOT / "tests/fakes/lark_cli.py", ROOT / "tests/test_fake_lark_cli.py", ROOT / "tests/fakes/README.md"):
            if path.is_file():
                public.append(path)
        self.assertTrue(ids, "私有目录存在但未提取到标识")
        self.assertEqual(privacy_leaks(public, ids, positions), [], "发现原始标识；仅报告文件名，不输出标识内容")
        def shape(value):
            if isinstance(value, dict):
                return {key: shape(item) for key, item in value.items()}
            if isinstance(value, list):
                return [shape(item) for item in value]
            if isinstance(value, str):
                try:
                    decoded = json.loads(value)
                except ValueError:
                    decoded = None
                if isinstance(decoded, (dict, list)):
                    return {"encodedJSON": shape(decoded)}
            return type(value).__name__
        for path in (ROOT / "tests/fakes/lark_samples").glob("*.json"):
            original = json.loads((raw / path.name).read_text(encoding="utf-8"))
            sanitized = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(shape(original), shape(sanitized), path.name)


    def test_desktop_scenarios_are_synthetic_and_have_no_runtime_history(self):
        fake = ROOT / "tests/fakes"
        scenarios = sorted((fake / "scenarios").glob("*.json"))
        self.assertTrue(scenarios)
        for path in scenarios:
            with self.subTest(path=path.name):
                scene = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(scene["schemaVersion"], 1)
                self.assertIs(scene["synthetic"], True)
                self.assertEqual(scene["state"]["calls"], [])
                self.assertEqual(scene["state"]["snapshots"], {})
                self.assertEqual(scene["state"]["inputEvents"], [])
                self.assertEqual(scene["state"]["drafts"], {})
        self.assertFalse(list(fake.rglob("*.lock")))
        self.assertFalse(list(fake.rglob("*.cmd")))

    def test_license_is_apache_2(self):
        # https://www.apache.org/licenses/LICENSE-2.0.txt，下载于 2026-11-04。
        self.assertEqual(hashlib.sha256((ROOT / "LICENSE").read_bytes()).hexdigest(),
                         "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30")

    @unittest.skipUnless(shutil.which("git"), "需要 Git")
    def test_private_paths_are_ignored_at_every_depth(self):
        for path in ("profile.json", "x/profile.json", "workspace1/ledger/events.jsonl",
                     "a/b/resumes/r.pdf", ".private/plans/p.md", "dist/x",
                     "x/setup-progress.json", "x/.job-mate-txn.json", "lark-im-resources/a"):
            with self.subTest(path=path):
                result = subprocess.run(["git", "check-ignore", "--no-index", path],
                                        cwd=ROOT, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
        for path in ("scripts/ledger.py", "shared/ledger.md", "platforms/boss/platform.json"):
            with self.subTest(path=path):
                result = subprocess.run(["git", "check-ignore", "--no-index", path],
                                        cwd=ROOT, capture_output=True)
                self.assertEqual(result.returncode, 1, result.stderr)

    @unittest.skipUnless(shutil.which("git"), "需要 Git")
    def test_tracked_files_do_not_match_private_ignore_rules(self):
        result = subprocess.run(["git", "ls-files", "-ci", "--exclude-standard"],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()

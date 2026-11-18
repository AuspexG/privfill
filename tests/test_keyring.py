"""Keyring unlock and switching unlock methods (issue #9). Fake data and a fake keyring only."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from privfill.keyring import FakeService, KeyringUnlock, SecretService, default_service  # noqa: E402
from privfill.store import Locked, StoreError  # noqa: E402
from privfill.vault import PassphraseUnlock, VaultStore  # noqa: E402

TOOL = os.path.join(ROOT, "privfill.py")
PASSPHRASE = "keyring tests 口令"
VALUES = {"name": "Jane Roe", "phone": "13800138000"}
SPEC = {"fields": [{"name": "name"}, {"name": "phone", "type": "phone"}]}


class Asker:
    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def __call__(self, kind):
        self.asked.append(kind)
        return self.answers.pop(0) if self.answers else None


class KeyringCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "store", "vault.json")
        self.fake = os.path.join(self.tmp.name, "keyring.json")

    def tearDown(self):
        self.tmp.cleanup()

    def store(self, *answers, keyring=True):
        self.asker = Asker(*answers)
        methods = [KeyringUnlock(FakeService(self.fake if keyring else "none")),
                   PassphraseUnlock(self.asker, cost=2 ** 10)]
        return VaultStore(self.path, methods, history=True)

    def vault(self):
        with open(self.path, encoding="utf8") as f:
            return json.load(f)

    def secret(self):
        with open(self.fake, encoding="utf8") as f:
            return list(json.load(f).values())[0]


class TestDefaults(KeyringCase):
    def test_a_new_vault_uses_the_keyring_and_asks_nothing(self):
        s = self.store()
        s.write("name", VALUES["name"])
        self.assertEqual((s.methods(), self.asker.asked, s.notice), (["keyring"], [], None))
        self.assertEqual(self.store().read("name"), VALUES["name"])
        self.assertEqual(self.asker.asked, [])

    def test_without_a_keyring_the_passphrase_is_used_and_the_person_is_told(self):
        s = self.store(PASSPHRASE, keyring=False)
        s.write("name", VALUES["name"])
        self.assertEqual((s.methods(), self.asker.asked), (["passphrase"], ["new"]))
        self.assertIn("passphrase", s.notice)
        self.assertIn("unlock add keyring", s.notice)

    def test_test_hooks_never_reach_the_real_keyring(self):
        self.assertFalse(default_service(testing=True).available())
        with mock.patch.dict(os.environ, {"PRIVFILL_KEYRING_TEST": self.fake}):
            os.environ.pop("PRIVFILL_VAULT", None)
            with self.assertRaises(RuntimeError):
                default_service(testing=True)

    def test_a_missing_secretstorage_library_is_named(self):
        with mock.patch.dict(sys.modules, {"secretstorage": None}):
            service = SecretService()
            self.assertFalse(service.available())
            self.assertIn("python-secretstorage", service.why_not)


class TestSwitching(KeyringCase):
    def items(self):
        return [[i[k] for k in ("name", "nonce", "ciphertext")] for i in self.vault()["items"]]

    def test_keyring_to_passphrase_and_back_without_re_encrypting(self):
        s = self.store()
        for n, v in VALUES.items():
            s.write(n, v)
        items = self.items()
        steps = [("enroll", "passphrase", ["keyring", "passphrase"]),
                 ("withdraw", "keyring", ["passphrase"]),
                 ("enroll", "keyring", ["passphrase", "keyring"]),
                 ("withdraw", "passphrase", ["keyring"])]
        for action, method, methods in steps:
            s = self.store(PASSPHRASE, PASSPHRASE)
            unlock = next(u for u in s.unlocks if u.method == method)
            getattr(s, action)(unlock if action == "enroll" else method)
            self.assertEqual(s.methods(), methods, (action, method))
            self.assertEqual(self.items(), items, "an item was re-encrypted")
            fresh = self.store(PASSPHRASE)
            self.assertEqual({n: fresh.read(n) for n in VALUES}, VALUES)

    def test_removing_the_keyring_method_forgets_its_secret(self):
        s = self.store(PASSPHRASE, PASSPHRASE)
        s.write("name", VALUES["name"])
        s.enroll(s.unlocks[1])
        s.withdraw("keyring")
        with open(self.fake, encoding="utf8") as f:
            self.assertEqual(json.load(f), {})

    def test_the_last_method_cannot_be_removed(self):
        s = self.store()
        s.write("name", VALUES["name"])
        with self.assertRaises(StoreError):
            s.withdraw("keyring")
        self.assertEqual(s.methods(), ["keyring"])

    def test_a_new_passphrase_replaces_the_old_one(self):
        s = self.store("old passphrase", keyring=False)
        s.write("name", VALUES["name"])
        s = self.store("old passphrase", PASSPHRASE, PASSPHRASE, keyring=False)
        s.enroll(s.unlocks[1])
        self.assertEqual(s.methods(), ["passphrase"])
        self.assertEqual(self.store(PASSPHRASE, keyring=False).read("name"), VALUES["name"])
        with self.assertRaises(Locked):
            self.store("old passphrase", "old passphrase", "old passphrase", keyring=False).read("name")


class TestRecovery(KeyringCase):
    def test_with_both_methods_a_lost_keyring_entry_falls_back_to_the_passphrase(self):
        s = self.store(PASSPHRASE, PASSPHRASE)
        s.write("name", VALUES["name"])
        s.enroll(s.unlocks[1])
        os.unlink(self.fake)
        s = self.store(PASSPHRASE)
        self.assertEqual(s.read("name"), VALUES["name"])
        self.assertEqual(self.asker.asked, ["unlock"])

    def test_with_both_methods_an_unreachable_keyring_falls_back_to_the_passphrase(self):
        s = self.store(PASSPHRASE, PASSPHRASE)
        s.write("name", VALUES["name"])
        s.enroll(s.unlocks[1])
        self.assertEqual(self.store(PASSPHRASE, keyring=False).read("name"), VALUES["name"])

    def test_keyring_only_and_the_entry_lost_is_locked(self):
        s = self.store()
        s.write("name", VALUES["name"])
        os.unlink(self.fake)
        with self.assertRaises(Locked) as e:
            self.store().read("name")
        self.assertIn("keyring", str(e.exception))

    def test_a_wrong_secret_in_the_keyring_is_locked(self):
        s = self.store()
        s.write("name", VALUES["name"])
        with open(self.fake, "w", encoding="utf8") as f:
            json.dump({self.vault()["id"]: "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE="}, f)
        with self.assertRaises(Locked):
            self.store().read("name")


class TestCommand(KeyringCase):
    def tool(self, *args, keyring=None, answers=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("PRIVFILL_")}
        env.update(PRIVFILL_VAULT=self.path, PRIVFILL_LANG="en", PRIVFILL_PASSPHRASE_TEST=PASSPHRASE,
                   PRIVFILL_KEYRING_TEST=keyring or self.fake)
        if answers is not None:
            env["PRIVFILL_FORM_TEST"] = json.dumps(answers)
        p = subprocess.run([sys.executable, TOOL, *args], env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60)
        out = p.stdout + p.stderr
        self.outputs.append(out)
        return p.returncode, out

    def setUp(self):
        super().setUp()
        self.outputs = []

    def tearDown(self):
        """The unlock secret and the passphrase never appear in any output or in the activity log."""
        secrets = [PASSPHRASE, *VALUES.values()]
        if os.path.exists(self.fake):
            with open(self.fake, encoding="utf8") as f:
                secrets += list(json.load(f).values())
        log = os.path.join(os.path.dirname(self.path), "activity.log")
        texts = self.outputs + ([open(log, encoding="utf8").read()] if os.path.exists(log) else [])
        for text in texts:
            for secret in secrets:
                self.assertNotIn(secret, text)
        super().tearDown()

    def test_switching_through_the_command(self):
        rc, out = self.tool("--json", "form", json.dumps(SPEC), answers=VALUES)
        self.assertEqual(rc, 0, out)
        rc, out = self.tool("--json", "unlock")
        self.assertEqual((json.loads(out)["methods"], json.loads(out)["keyring_available"]), (["keyring"], True))
        for args, methods in ((["add", "passphrase"], ["keyring", "passphrase"]),
                              (["change", "passphrase"], ["keyring", "passphrase"]),
                              (["remove", "keyring"], ["passphrase"]),
                              (["add", "keyring"], ["passphrase", "keyring"])):
            rc, out = self.tool("--json", "unlock", *args)
            self.assertEqual((rc, json.loads(out)["methods"]), (0, methods), out)
        rc, out = self.tool("--json", "render", self.write("m.md", "{{phone|last4}}"), self.write("o.md"))
        self.assertEqual(rc, 0, out)

    def test_refusals(self):
        self.tool("form", json.dumps(SPEC), answers=VALUES)
        for args in (["remove", "keyring"], ["add", "secret"], ["change", "keyring"], ["remove"], ["change", "passphrase"]):
            rc, out = self.tool("unlock", *args)
            self.assertEqual(rc, 1, args)

    def test_no_keyring_says_so_on_creation(self):
        rc, out = self.tool("--json", "form", json.dumps(SPEC), answers=VALUES, keyring="none")
        self.assertEqual(rc, 0, out)
        self.assertIn("Note:", json.loads(out)["message"])
        rc, out = self.tool("--json", "unlock", keyring="none")
        self.assertEqual(json.loads(out)["methods"], ["passphrase"])

    def write(self, name, text=""):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf8") as f:
            f.write(text)
        return path


if __name__ == "__main__":
    unittest.main()

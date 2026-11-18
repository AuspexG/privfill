"""Moving a plain-text data file of an earlier version into the vault (issue #10). Fake data only."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from privfill.store import FileStore  # noqa: E402
from privfill.vault import PassphraseUnlock, VaultStore  # noqa: E402

TOOL = os.path.join(ROOT, "privfill.py")
PASSPHRASE = "migration tests 口令"
OLD = {"name": "Jane Roe", "phone": "13800138000", "地址": "示例路 1 号 ｜ Ünit 2", "email": "jane@example.com"}


class MigrateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.vault_path = os.path.join(self.tmp.name, "store", "vault.json")
        self.old = os.path.join(self.tmp.name, "old", "profile.md")
        old = FileStore(self.old)
        for k, v in OLD.items():
            old.write(k, v)
        with open(self.old, encoding="utf8") as f:
            self.old_text = f.read()
        self.outputs = []

    def tearDown(self):
        for out in self.outputs:
            for v in OLD.values():
                self.assertNotIn(v, out)
            self.assertNotIn(PASSPHRASE, out)
        self.tmp.cleanup()

    def tool(self, *args, confirm="no", data=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("PRIVFILL_")}
        env.update(HOME=self.home, PRIVFILL_VAULT=self.vault_path, PRIVFILL_LANG="en",
                   PRIVFILL_PASSPHRASE_TEST=PASSPHRASE, PRIVFILL_CONFIRM_TEST=confirm,
                   PRIVFILL_KEYRING_TEST="none")
        if data:
            env["PRIVFILL_DATA"] = data
        p = subprocess.run([sys.executable, TOOL, "--json", *args], env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60)
        self.outputs.append(p.stdout + p.stderr)
        return p.returncode, json.loads(p.stdout)

    def vault(self):
        return VaultStore(self.vault_path, PassphraseUnlock(lambda kind: PASSPHRASE, cost=2 ** 10))

    def old_untouched(self):
        with open(self.old, encoding="utf8") as f:
            return f.read() == self.old_text


class TestMigrate(MigrateCase):
    def test_every_item_arrives_identical_and_only_counts_are_printed(self):
        rc, out = self.tool("migrate", data=self.old)
        self.assertEqual(rc, 0, out)
        self.assertEqual((out["total"], out["added"], out["present"], out["mismatched"]), (4, 4, 0, 0))
        v = self.vault()
        self.assertEqual({n: v.read(n) for n in v.names()}, OLD)
        self.assertEqual(out["deleted"], False)
        self.assertTrue(self.old_untouched(), "the old file was changed without consent")
        self.assertIn("journaling", out["message"])

    def test_running_it_again_changes_nothing(self):
        self.tool("migrate", data=self.old)
        with open(self.vault_path, encoding="utf8") as f:
            before = f.read()
        rc, out = self.tool("migrate", data=self.old)
        self.assertEqual((rc, out["added"], out["present"]), (0, 0, 4), out)
        with open(self.vault_path, encoding="utf8") as f:
            self.assertEqual(f.read(), before)

    def test_a_conflicting_value_stops_and_leaves_both_alone(self):
        self.vault().write("phone", "13900139000", "phone")
        rc, out = self.tool("migrate", data=self.old, confirm="yes")
        self.assertEqual(rc, 1, out)
        self.assertEqual(out["conflicts"], ["phone"])
        self.assertEqual(self.vault().read("phone"), "13900139000")
        self.assertTrue(self.old_untouched())
        self.assertNotIn("deleted", out)

    def test_a_failed_verification_stops_and_leaves_the_old_file(self):
        from privfill import migrate as m

        class Liar:  # a vault that does not return what was written
            def __init__(self):
                self.items = {}

            def read(self, n):
                return self.items.get(n) and self.items[n] + "?"

            def write(self, n, v, t="text"):
                self.items[n] = v

        r = m.migrate(self.old, Liar())
        self.assertEqual(len(r["mismatched"]), 4)
        self.assertTrue(self.old_untouched())

    def test_removal_happens_only_after_a_local_yes(self):
        rc, out = self.tool("migrate", data=self.old, confirm="yes")
        self.assertEqual((rc, out["deleted"]), (0, True), out)
        self.assertFalse(os.path.exists(self.old))
        rc, out = self.tool("migrate", data=self.old)
        self.assertEqual((rc, out["found"]), (0, False))

    def test_copies_are_named_so_the_person_can_remove_them(self):
        backup = self.old + ".bak"
        with open(backup, "w", encoding="utf8") as f:
            f.write(self.old_text)
        rc, out = self.tool("migrate", data=self.old)
        self.assertEqual(out["copies"], [backup])

    def test_the_legacy_file_name_in_the_home_directory_is_found(self):
        legacy_dir = os.path.join(self.home, ".privfill")
        os.makedirs(legacy_dir)
        os.replace(self.old, os.path.join(legacy_dir, "个人信息.md"))
        rc, out = self.tool("migrate")
        self.assertEqual((rc, out["added"]), (0, 4), out)

    def test_a_test_run_never_reads_the_persons_own_data_directory(self):
        from privfill.migrate import off_limits, real_privfill_dir
        from unittest import mock

        own = os.path.join(real_privfill_dir(), "profile.md")
        with mock.patch.dict(os.environ, {"PRIVFILL_PASSPHRASE_TEST": "x"}):
            self.assertTrue(off_limits(own))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(off_limits(own))
        rc, out = self.tool("migrate", own)  # only the path is given; the guard refuses before any read
        self.assertEqual(rc, 1)
        self.assertIn("may not read", out["message"])

    def test_nothing_to_migrate(self):
        os.unlink(self.old)
        rc, out = self.tool("migrate")
        self.assertEqual((rc, out["found"]), (0, False))
        rc, out = self.tool("migrate", self.old)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()

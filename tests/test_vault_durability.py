"""The vault under concurrent writers, crashes, rollback and damage (issue #8). Fake data only."""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from privfill.store import Damaged  # noqa: E402
from privfill.vault import BACKUP_DIR, STATE_FILE, PassphraseUnlock, VaultStore  # noqa: E402

PASSPHRASE = "a durable 口令 for tests"
VALUES = {"name": "Jane Roe", "phone": "13800138000", "email": "jane@example.com", "street": "Quokka Street 42"}
TOOL = os.path.join(ROOT, "privfill.py")

# A child process that opens the vault the way the program does, then does one thing.
CHILD = textwrap.dedent("""
    import os, signal, sys
    sys.path.insert(0, {root!r})
    from privfill import atomic, vault
    from privfill.vault import PassphraseUnlock, VaultStore
    store = VaultStore({path!r}, PassphraseUnlock(lambda kind: {passphrase!r}, cost=2 ** 10), history=True)
    def die():
        os.kill(os.getpid(), signal.SIGKILL)
    crash = os.environ.get("CRASH")
    if crash == "half-written":
        real = vault.atomic_write
        def half(path, writer):
            def w(tmp):
                writer(tmp)
                size = os.path.getsize(tmp)
                with open(tmp, "r+b") as f:
                    f.truncate(size // 2)
                die()
            if path == store.path:
                real(path, w)
            else:
                real(path, writer)
        vault.atomic_write = half
    elif crash == "before-rename":
        real_replace = atomic.os.replace
        def replace(src, dst):
            if dst == store.path:
                die()
            real_replace(src, dst)
        atomic.os.replace = replace
    elif crash == "before-state":
        VaultStore._remember = lambda self, v: die()
    store.write(sys.argv[1], sys.argv[2])
""")


class DurableCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "store")
        self.path = os.path.join(self.dir, "vault.json")

    def tearDown(self):
        """No file the vault keeps beside itself holds a value or the passphrase."""
        for f in glob.glob(os.path.join(self.dir, "**", "*"), recursive=True) + \
                glob.glob(os.path.join(self.dir, "**", ".*"), recursive=True):
            if os.path.isfile(f):
                with open(f, "rb") as fh:
                    raw = fh.read().decode("utf8", "replace")
                for secret in list(VALUES.values()) + [PASSPHRASE]:
                    self.assertNotIn(secret, raw, os.path.basename(f))
        self.tmp.cleanup()

    def store(self, history=True):
        return VaultStore(self.path, PassphraseUnlock(lambda kind: PASSPHRASE, cost=2 ** 10), history=history)

    def seed(self, names=("name", "phone")):
        s = self.store()
        for n in names:
            s.write(n, VALUES[n])

    def child(self, name, value, crash=None):
        script = CHILD.format(root=ROOT, path=self.path, passphrase=PASSPHRASE)
        env = dict(os.environ, CRASH=crash or "")
        return subprocess.Popen([sys.executable, "-c", script, name, value], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def readable(self):
        s = self.store()
        return {n: s.read(n) for n in s.names()}


class TestConcurrentWriters(DurableCase):
    def test_concurrent_entries_are_all_stored(self):
        self.store().create()
        names = [f"item {i}" for i in range(8)]
        procs = [self.child(n, VALUES["street"]) for n in names]
        self.assertEqual([p.wait(60) for p in procs], [0] * len(procs))
        self.assertEqual(sorted(self.store().names()), sorted(names))
        self.assertEqual(self.store().read("item 3"), VALUES["street"])


class TestCrashes(DurableCase):
    def crash(self, point):
        self.seed()
        before = self.readable()
        p = self.child("email", VALUES["email"], crash=point)
        self.assertEqual(p.wait(60), -9, "the child did not crash where it was told to")
        return before

    def test_a_crash_while_writing_leaves_the_previous_vault(self):
        for point in ("half-written", "before-rename"):
            with self.subTest(point):
                before = self.crash(point)
                self.assertEqual(self.readable(), before)
                self.tearDown()
                self.setUp()

    def test_a_crash_after_the_rename_keeps_the_new_vault_and_no_false_rollback(self):
        before = self.crash("before-state")
        self.assertEqual(self.readable(), dict(before, email=VALUES["email"]))

    def test_the_next_write_removes_what_a_crash_left(self):
        self.crash("half-written")
        self.store().write("street", VALUES["street"])
        self.assertEqual([n for n in os.listdir(self.dir) if ".tmp-" in n], [])
        self.assertEqual(set(self.readable()), {"name", "phone", "street"})


class TestRollback(DurableCase):
    def test_an_older_copy_put_back_is_detected(self):
        self.seed()
        old = self.path + ".old"
        shutil.copyfile(self.path, old)
        self.store().write("email", VALUES["email"])
        os.replace(old, self.path)
        with self.assertRaises(Damaged) as e:
            self.store().names()
        self.assertIn("older", str(e.exception))

    def test_raising_the_version_without_the_key_is_detected(self):
        self.seed()
        old = self.path + ".old"
        shutil.copyfile(self.path, old)
        self.store().write("email", VALUES["email"])
        with open(old, encoding="utf8") as f:
            vault = json.load(f)
        vault["version"] += 5
        with open(self.path, "w", encoding="utf8") as f:
            json.dump(vault, f)
        self.store().names()  # listing needs no key, so only the seal can catch this
        with self.assertRaises(Damaged):
            self.store().read("name")

    def test_removing_or_adding_an_entry_is_detected(self):
        self.seed()
        with open(self.path, encoding="utf8") as f:
            vault = json.load(f)
        dropped = dict(vault, items=vault["items"][:1])
        duplicated = dict(vault, items=vault["items"] + [dict(vault["items"][0], name="copy")])
        for changed in (dropped, duplicated):
            with open(self.path, "w", encoding="utf8") as f:
                json.dump(changed, f)
            with self.assertRaises(Damaged):
                self.store().read("name")

    def test_a_store_without_history_keeps_no_state_and_no_backups(self):
        s = self.store(history=False)
        s.write("name", VALUES["name"])
        s.write("phone", VALUES["phone"])
        self.assertEqual(sorted(os.listdir(self.dir)), ["vault.json", "vault.json.lock"])


class TestBackups(DurableCase):
    def test_backups_rotate_and_hold_only_ciphertext(self):
        s = self.store()
        for i in range(12):
            s.write("name", VALUES["name"] + str(i))
        kept = s.backups()
        self.assertEqual(len(kept), 5)
        self.assertEqual([v for v, _, _ in kept], sorted((v for v, _, _ in kept), reverse=True))
        self.assertEqual(len(os.listdir(os.path.join(self.dir, BACKUP_DIR))), 5)
        # tearDown checks that no backup holds a value

    def test_restore_puts_an_older_set_of_items_back_as_a_new_version(self):
        self.seed()
        version = self.store().backups()[0][0] + 1  # the current version, about to become a backup
        self.store().write("email", VALUES["email"])
        self.assertEqual(self.store().restore(version), 2)
        self.assertEqual(self.readable(), {"name": VALUES["name"], "phone": VALUES["phone"]})
        with open(os.path.join(self.dir, STATE_FILE), encoding="utf8") as f:
            self.assertGreater(list(json.load(f).values())[0], version)
        self.assertIn(version + 1, [v for v, _, _ in self.store().backups()])  # the replaced vault was kept

    def test_restore_works_when_the_vault_is_damaged(self):
        self.seed()
        version = self.store().backups()[0][0]
        with open(self.path, "w", encoding="utf8") as f:
            f.write("{ damaged")
        self.assertEqual(self.store().restore(version), 1)
        self.assertEqual(self.readable(), {"name": VALUES["name"]})

    def test_restore_after_a_rollback(self):
        self.seed()
        old = self.path + ".old"
        shutil.copyfile(self.path, old)
        self.store().write("email", VALUES["email"])
        os.replace(old, self.path)
        newest = self.store().backups()[0][0]
        self.store().restore(newest)
        self.assertEqual(set(self.readable()), {"name", "phone"})


class TestRestoreCommand(DurableCase):
    def tool(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("PRIVFILL_")}
        env.update(PRIVFILL_VAULT=self.path, PRIVFILL_LANG="en", PRIVFILL_PASSPHRASE_TEST=PASSPHRASE)
        p = subprocess.run([sys.executable, TOOL, *args], env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60)
        for secret in list(VALUES.values()) + [PASSPHRASE]:
            self.assertNotIn(secret, p.stdout + p.stderr)
        return p.returncode, p.stdout

    def test_list_and_restore(self):
        self.seed(("name", "phone", "email"))
        rc, out = self.tool("--json", "restore")
        self.assertEqual(rc, 0, out)
        kept = json.loads(out)["backups"]
        self.assertEqual([b["items"] for b in kept], [2, 1, 0])
        rc, out = self.tool("--json", "restore", str(kept[1]["version"]))
        self.assertEqual((rc, json.loads(out)["restored"]), (0, 1), out)
        rc, out = self.tool("--json", "keys")
        self.assertEqual(json.loads(out)["items"], ["name"])
        rc, out = self.tool("restore", "12345")
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()

"""The encrypted vault, at the Store seam and through the commands.

Fake data only: every value and passphrase here is made up, and each vault lives in a temporary
directory. Vaults are created with a cheap key derivation so the suite stays fast.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from privfill.store import Cancelled, Damaged, Locked, vault_path  # noqa: E402
from privfill.vault import PassphraseUnlock, Unlock, VaultStore  # noqa: E402

SAMPLE_ID = "11010519491231002X"  # the specimen number used in published examples; belongs to no one
PASSPHRASE = "correct horse 电池 staple"
ITEMS = [  # one per item type, with multi-byte text among them
    ("name", "text", "Jane Roe"),
    ("地址", "text", "示例路 1 号 ｜ Ünit 2 🏠"),
    ("birthday", "date", "2005-03-04"),
    ("phone", "phone", "13800138000"),
    ("id number", "id_card", SAMPLE_ID),
    ("email", "email", "jane@example.com"),
    ("height", "number", "175"),
    ("size", "choice", "M"),
]


LONG_VALUES = [v for _, _, v in ITEMS if len(v) > 5]
BAD = "tr0ub4dor"  # a passphrase that opens nothing


class Asker:
    """Stands in for the person at the passphrase dialog: answers from a list, counts the questions."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = []

    def __call__(self, kind):
        self.asked.append(kind)
        return self.answers.pop(0) if self.answers else None


class UnboundUnlock(Unlock):
    """Another unlock method plugged into the vault. Unlike a real one it ties the key to no vault."""

    method = "unbound"

    def wrap(self, key, vault_id):
        return {"method": self.method, "key": key.hex()}

    def unwrap(self, entry, vault_id):
        return bytes.fromhex(entry["key"])


def unlock(*answers):
    return PassphraseUnlock(Asker(*answers), cost=2 ** 10)


class VaultCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "store", "vault.json")

    def tearDown(self):
        self.tmp.cleanup()

    def store(self, *answers):
        """A fresh VaultStore on the same file, as a new process would have."""
        return VaultStore(self.path, unlock(*(answers or (PASSPHRASE,))))

    def seed(self):
        store = self.store()
        for name, type_, value in ITEMS:
            store.write(name, value, type_)

    def raw(self):
        with open(self.path, encoding="utf8") as f:
            return f.read()

    def edit(self, change):
        """Tamper with the vault file: change(parsed JSON) edits it in place."""
        vault = json.loads(self.raw())
        change(vault)
        with open(self.path, "w", encoding="utf8") as f:
            json.dump(vault, f)

    def edit_sealed(self, change):
        """Change the vault file and seal it again with the real key, as only the key's holder could:
        what is then left to catch the change is the per-item encryption."""
        store = self.store()
        vault = store._opened()
        change(vault)
        vault["seal"] = store._seal(vault, store._data_key)
        with open(self.path, "w", encoding="utf8") as f:
            json.dump(vault, f)

    @staticmethod
    def item(vault, name):
        return next(i for i in vault["items"] if i["name"] == name)


class TestRoundTrip(VaultCase):
    def test_every_item_type_comes_back_as_written(self):
        self.seed()
        store = self.store()
        for name, _, value in ITEMS:
            self.assertEqual(store.read(name), value, name)
        self.assertEqual(store.names(), [name for name, _, _ in ITEMS])

    def test_write_replaces_and_delete_removes_one_item(self):
        store = self.store()
        self.assertFalse(store.write("phone", "13800138000", "phone"))
        self.assertFalse(store.write("city", "Example Town"))
        self.assertTrue(store.write("phone", "13900139000", "phone"))
        self.assertEqual(self.store().read("phone"), "13900139000")
        self.assertTrue(store.delete("phone"))
        self.assertFalse(store.delete("phone"))
        self.assertEqual(store.names(), ["city"])
        self.assertIsNone(self.store().read("phone"))

    def test_empty_store_reads_nothing_asks_nothing_and_creates_no_file(self):
        store = self.store()
        self.assertEqual(store.names(), [])
        self.assertIsNone(store.read("phone"))
        self.assertFalse(store.delete("phone"))
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(store.unlock.ask.asked, [])

    def test_create_is_private_and_idempotent(self):
        store = self.store()
        self.assertTrue(store.create())
        self.assertFalse(store.create())
        self.assertFalse(self.store().create())
        store.write("phone", "13800138000", "phone")
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.dirname(self.path)).st_mode & 0o777, 0o700)
        self.assertEqual(store.label, "vault.json")

    def test_the_file_holds_no_value_and_no_passphrase(self):
        self.seed()
        raw = self.raw()
        for value in LONG_VALUES:  # a one-letter value proves nothing against base64 text
            self.assertNotIn(value, raw)
        self.assertNotIn(PASSPHRASE, raw)

    def test_the_same_value_never_encrypts_to_the_same_bytes(self):
        store = self.store()
        store.write("a", "Jane Roe")
        store.write("b", "Jane Roe")
        vault = json.loads(self.raw())
        self.assertNotEqual(self.item(vault, "a")["ciphertext"], self.item(vault, "b")["ciphertext"])


class TestUnlocking(VaultCase):
    def test_listing_names_needs_no_unlock(self):
        self.seed()
        store = self.store()
        self.assertEqual(store.names(), [name for name, _, _ in ITEMS])
        self.assertEqual(store.unlock.ask.asked, [])

    def test_names_types_and_update_times_are_readable_in_the_file(self):
        self.seed()
        vault = json.loads(self.raw())
        self.assertEqual([(i["name"], i["type"]) for i in vault["items"]], [(n, t) for n, t, _ in ITEMS])
        for i in vault["items"]:
            self.assertRegex(i["updated"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_one_unlock_serves_every_read_and_write_of_a_run(self):
        self.seed()
        store = self.store()
        store.read("name")
        store.read("phone")
        store.write("city", "Example Town")
        self.assertEqual(store.unlock.ask.asked, ["unlock"])

    def test_a_new_vault_asks_for_a_new_passphrase_once(self):
        store = self.store()
        store.write("name", "Jane Roe")
        self.assertEqual(store.read("name"), "Jane Roe")
        self.assertEqual(store.unlock.ask.asked, ["new"])

    def test_a_wrong_passphrase_fails_and_reveals_nothing(self):
        self.seed()
        before = self.raw()
        store = self.store(BAD, BAD, BAD)
        with self.assertRaises(Locked) as caught:
            store.read("name")
        self.assertEqual(store.unlock.ask.asked, ["unlock", "retry", "retry"])
        for text in LONG_VALUES + [PASSPHRASE, BAD]:
            self.assertNotIn(text, str(caught.exception))
        with self.assertRaises(Locked):
            self.store(BAD, BAD, BAD).write("name", "Someone Else")
        self.assertEqual(self.raw(), before)

    def test_the_default_key_derivation_is_the_memory_hard_one(self):
        """Not the cheap one the rest of the suite uses: scrypt at 128 MiB per guess."""
        VaultStore(self.path, PassphraseUnlock(Asker(PASSPHRASE))).write("name", "Jane Roe")
        (entry,) = json.loads(self.raw())["unlock"]
        self.assertEqual((entry["kdf"], entry["n"], entry["r"], entry["p"]), ("scrypt", 2 ** 17, 8, 1))
        self.assertEqual(VaultStore(self.path, PassphraseUnlock(Asker(PASSPHRASE))).read("name"), "Jane Roe")
        with self.assertRaises(Locked):
            VaultStore(self.path, PassphraseUnlock(Asker(BAD, BAD, BAD))).read("name")

    def test_a_retry_with_the_right_passphrase_unlocks(self):
        self.seed()
        self.assertEqual(self.store(BAD, PASSPHRASE).read("name"), "Jane Roe")

    def test_cancelling_the_passphrase_question_stops_without_change(self):
        with self.assertRaises(Cancelled):
            VaultStore(self.path, unlock()).write("name", "Jane Roe")
        self.assertFalse(os.path.exists(self.path))
        self.seed()
        with self.assertRaises(Cancelled):
            VaultStore(self.path, unlock()).read("name")

    def test_a_passphrase_for_another_vault_does_not_unlock_a_copied_key(self):
        """The wrapped key is bound to the vault identity."""
        self.seed()
        self.edit(lambda v: v.update(id="00" * 16))
        with self.assertRaises(Locked):
            self.store(PASSPHRASE, PASSPHRASE, PASSPHRASE).read("name")


class TestTampering(VaultCase):
    def assert_damaged(self, name):
        with self.assertRaises(Damaged) as caught:
            self.store().read(name)
        for value in LONG_VALUES:
            self.assertNotIn(value, str(caught.exception))

    def test_an_altered_ciphertext_is_detected(self):
        self.seed()

        def flip(vault):
            c = self.item(vault, "phone")["ciphertext"]
            self.item(vault, "phone")["ciphertext"] = ("B" if c[0] == "A" else "A") + c[1:]

        self.edit(flip)
        self.assert_damaged("phone")

    def test_a_renamed_item_is_detected(self):
        self.seed()
        self.edit(lambda v: self.item(v, "phone").update(name="mobile"))
        self.assert_damaged("mobile")

    def test_an_altered_type_is_detected(self):
        self.seed()
        self.edit(lambda v: self.item(v, "phone").update(type="text"))
        self.assert_damaged("phone")

    def test_an_altered_update_time_is_detected(self):
        self.seed()
        self.edit(lambda v: self.item(v, "phone").update(updated="2001-01-01T00:00:00Z"))
        self.assert_damaged("phone")

    def test_swapping_two_items_ciphertexts_is_detected(self):
        self.seed()

        def swap(vault):
            a, b = self.item(vault, "name"), self.item(vault, "email")
            for k in ("nonce", "ciphertext"):
                a[k], b[k] = b[k], a[k]

        self.edit(swap)
        self.assert_damaged("name")
        self.assert_damaged("email")

    def test_items_are_bound_to_their_vault_even_when_the_key_is_not(self):
        """Under another identity the same key and the same entries no longer decrypt."""
        VaultStore(self.path, UnboundUnlock()).write("phone", "13800138000", "phone")
        self.assertEqual(VaultStore(self.path, UnboundUnlock()).read("phone"), "13800138000")
        self.edit(lambda v: v.update(id="00" * 16))
        with self.assertRaises(Damaged):
            VaultStore(self.path, UnboundUnlock()).read("phone")

    def test_reading_one_item_decrypts_only_that_item(self):
        """Every other ciphertext is ruined, so touching any of them would fail."""
        self.seed()

        def ruin_others(vault):
            for i in vault["items"]:
                if i["name"] != "phone":
                    i["ciphertext"] = i["ciphertext"][::-1]

        self.edit_sealed(ruin_others)
        self.assertEqual(self.store().read("phone"), "13800138000")

    def test_a_file_that_is_not_a_vault_is_reported_not_overwritten(self):
        os.makedirs(os.path.dirname(self.path))
        for text in ("", "not json", "[]", '{"privfill_vault": 2}', '{"privfill_vault": 1, "id": "x", "unlock": {}, "items": [{}]}'):
            with open(self.path, "w", encoding="utf8") as f:
                f.write(text)
            store = self.store()
            for call in (store.names, lambda: store.read("x"), lambda: store.write("x", "1"), lambda: store.delete("x")):
                with self.assertRaises(Damaged):
                    call()
            self.assertEqual(self.raw(), text)

    def test_a_truncated_nonce_is_reported_as_damage(self):
        self.seed()
        self.edit(lambda v: self.item(v, "phone").update(nonce="AAAA"))
        self.assert_damaged("phone")
        self.edit(lambda v: v["unlock"][0].update(nonce="AAAA"))
        self.assert_damaged("name")

    def test_a_vault_cannot_ask_for_an_absurd_key_derivation(self):
        self.seed()
        self.edit(lambda v: v["unlock"][0].update(n=2 ** 30))
        with self.assertRaises(Damaged):
            self.store().read("name")


class TestLocation(unittest.TestCase):
    def test_the_vault_is_in_the_home_directory_unless_told_otherwise(self):
        with mock.patch.dict(os.environ, {"HOME": "/home/nobody"}):
            os.environ.pop("PRIVFILL_VAULT", None)
            self.assertEqual(vault_path(), "/home/nobody/.privfill/vault.json")
            os.environ["PRIVFILL_VAULT"] = "/somewhere/else.json"
            self.assertEqual(vault_path(), "/somewhere/else.json")


TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "privfill.py")
SPEC = {"fields": [{"name": n, "type": t, **({"choices": ["S", "M", "L"]} if t == "choice" else {})} for n, t, _ in ITEMS]}
NO_CRYPTOGRAPHY = "raise ImportError('cryptography is not installed here')\n"


class TestCommands(VaultCase):
    """The real program on a real vault file, with the passphrase typed by the test hook."""

    def setUp(self):
        super().setUp()
        self.dir = self.tmp.name
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("PRIVFILL_")}
        self.env.update(PRIVFILL_VAULT=self.path, PRIVFILL_LANG="en", PRIVFILL_PASSPHRASE_TEST=PASSPHRASE)
        self.outputs = []

    def tearDown(self):
        """The no-value invariant: nothing any command printed holds a value or a passphrase."""
        for out in self.outputs:
            for secret in LONG_VALUES + [PASSPHRASE, BAD]:
                self.assertNotIn(secret, out)
        if os.path.exists(self.path):
            for secret in LONG_VALUES + [PASSPHRASE, BAD]:
                self.assertNotIn(secret, self.raw())
        super().tearDown()

    def tool(self, *args, answers=None, **env):
        env = dict(self.env, **env)
        if answers is not None:
            env["PRIVFILL_FORM_TEST"] = json.dumps(answers, ensure_ascii=False)
        p = subprocess.run([sys.executable, TOOL, *args], env=env, cwd=self.dir,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.outputs.append(p.stdout + p.stderr)
        return p.returncode, p.stdout + p.stderr

    def enter(self):
        rc, out = self.tool("--json", "form", json.dumps(SPEC, ensure_ascii=False), answers={n: v for n, _, v in ITEMS})
        self.assertEqual(rc, 0, out)

    def file(self, name, text=None):
        path = os.path.join(self.dir, name)
        if text is not None:
            with open(path, "w", encoding="utf8") as f:
                f.write(text)
        return path

    def without_cryptography(self):
        """Environment in which importing the encryption library fails, as on a machine without it."""
        stub = os.path.join(self.dir, "stub", "cryptography")
        os.makedirs(stub, exist_ok=True)
        with open(os.path.join(stub, "__init__.py"), "w", encoding="utf8") as f:
            f.write(NO_CRYPTOGRAPHY)
        return {"PYTHONPATH": os.path.dirname(stub)}

    def test_entered_items_are_encrypted_with_their_types_and_fill_documents(self):
        self.enter()
        vault = json.loads(self.raw())
        self.assertEqual([(i["name"], i["type"]) for i in vault["items"]], [(n, t) for n, t, _ in ITEMS])
        master = self.file("m.md", "{{name}} / {{地址}} / {{birthday|年月日}}")
        rc, out = self.tool("--json", "render", master, self.file("o.md"))
        self.assertEqual(rc, 0, out)
        with open(self.file("o.md"), encoding="utf8") as f:
            self.assertEqual(f.read(), "Jane Roe / 示例路 1 号 ｜ Ünit 2 🏠 / 2005年3月4日")

    def test_init_creates_a_private_vault_once(self):
        rc, out = self.tool("--json", "init")
        self.assertEqual((rc, json.loads(out)["created"]), (0, True))
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        rc, out = self.tool("--json", "init")
        self.assertEqual((rc, json.loads(out)["created"]), (0, False))

    def test_a_wrong_passphrase_exits_5_and_changes_nothing(self):
        self.enter()
        before = self.raw()
        master = self.file("m.md", "{{name}}")
        for args in (("render", master, self.file("o.md")), ("fill", master), ("pack", self.file("{{name}}.zip"), master)):
            rc, out = self.tool("--json", *args, PRIVFILL_PASSPHRASE_TEST=BAD)
            self.assertEqual(rc, 5, out)
            self.assertEqual((json.loads(out)["ok"], json.loads(out)["code"]), (False, 5))
            self.assertIn("wrong passphrase", out)
        rc, out = self.tool("add", "nickname", answers={"nickname": "JR-nickname"}, PRIVFILL_PASSPHRASE_TEST=BAD)
        self.assertEqual(rc, 5, out)
        self.assertNotIn("JR-nickname", out)
        self.assertEqual(self.raw(), before)
        self.assertFalse(os.path.exists(self.file("o.md")))
        with open(master, encoding="utf8") as f:
            self.assertEqual(f.read(), "{{name}}")

    def test_keys_needs_neither_the_passphrase_nor_the_encryption_library(self):
        self.enter()
        rc, out = self.tool("--json", "keys", PRIVFILL_PASSPHRASE_TEST=BAD, **self.without_cryptography())
        self.assertEqual((rc, json.loads(out)["items"]), (0, [n for n, _, _ in ITEMS]))

    def test_a_missing_encryption_library_names_the_package_to_install(self):
        self.enter()
        master = self.file("m.md", "{{name}}")
        for args in (("render", master, self.file("o.md")), ("fill", master), ("add", "nickname")):
            rc, out = self.tool(*args, answers={"nickname": "JR-nickname"}, **self.without_cryptography())
            self.assertEqual(rc, 1, out)
            self.assertIn("`cryptography` is not installed", out)
            self.assertIn("python-cryptography", out)
            self.assertIn("python3-cryptography", out)
            self.assertNotIn("Traceback", out)
        os.unlink(self.path)
        rc, out = self.tool("init", **self.without_cryptography())
        self.assertEqual(rc, 1, out)
        self.assertIn("python-cryptography", out)
        self.assertFalse(os.path.exists(self.path))

    def test_a_render_that_uses_one_item_decrypts_only_that_item(self):
        self.enter()

        def ruin_others(vault):
            for i in vault["items"]:
                if i["name"] != "phone":
                    i["ciphertext"] = i["ciphertext"][::-1]

        self.edit_sealed(ruin_others)
        rc, out = self.tool("--json", "render", self.file("m.md", "{{phone|last4}}"), self.file("o.md"))
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(out)["used"], {"phone": 1})

    def test_a_tampered_item_stops_the_command_and_leaves_no_output(self):
        self.enter()
        self.edit(lambda v: self.item(v, "phone").update(type="text"))
        master = self.file("m.md", "{{name}} {{phone}}")
        rc, out = self.tool("--json", "render", master, self.file("o.md"))
        self.assertEqual(rc, 1, out)
        self.assertIn("does not match its seal", out)
        self.assertFalse(os.path.exists(self.file("o.md")))
        rc, out = self.tool("fill", master)
        self.assertEqual(rc, 1, out)
        with open(master, encoding="utf8") as f:
            self.assertEqual(f.read(), "{{name}} {{phone}}")

    def test_the_passphrase_hook_refuses_the_persons_own_vault(self):
        home = os.path.join(self.dir, "home")
        mine = os.path.join(home, ".privfill", "vault.json")
        for env in ({"PRIVFILL_VAULT": mine}, {"PRIVFILL_VAULT": ""}):
            rc, out = self.tool("init", HOME=home, **env)
            self.assertNotEqual(rc, 0, out)
            self.assertFalse(os.path.exists(mine))

    def test_a_file_that_is_not_a_vault_is_reported_by_every_command(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf8") as f:
            f.write("| name | Jane Roe |\n")
        for args in (("keys",), ("render", self.file("m.md", "{{name}}"), self.file("o.md")), ("add", "name")):
            rc, out = self.tool(*args, answers={"name": "Someone Else"})
            self.assertEqual(rc, 1, out)
            self.assertIn("not in the vault format", out)
        os.unlink(self.path)  # the planted file is not a vault, so it is not held to the vault's invariant


if __name__ == "__main__":
    unittest.main()

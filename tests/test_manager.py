"""The manager's logic (no window): list, reveal, edit, delete, encrypted backup and import.

Fake data only; every vault is in a temporary directory with a cheap key derivation.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from privfill.manager import Manager  # noqa: E402
from privfill.normalize import Bad  # noqa: E402
from privfill.store import Cancelled, Damaged, Locked, StoreError  # noqa: E402
from privfill.vault import PassphraseUnlock, VaultStore  # noqa: E402

ITEMS = [("name", "text", "Jane Roe"), ("birthday", "date", "2005-03-04"), ("phone", "phone", "13800138000"),
         ("size", "choice", "M")]
MAIN, BACKUP = "main pass 电池", "backup pass"


def unlock(*answers):
    answers = list(answers)
    return PassphraseUnlock(lambda kind: answers.pop(0) if answers else None, cost=2 ** 10)


class ManagerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "vault", "vault.json")
        self.store = VaultStore(self.path, unlock(MAIN, MAIN))
        for name, type_, value in ITEMS:
            self.store.write(name, value, type_)
        self.manager = Manager(self.store, lambda: unlock(BACKUP, BACKUP))

    def tearDown(self):
        self.tmp.cleanup()

    def backup_file(self):
        return os.path.join(self.tmp.name, "backup.json")

    def read_from(self, path, *answers):
        return VaultStore(path, unlock(*answers))


class TestBrowse(ManagerCase):
    def test_list_shows_names_and_types_without_unlocking(self):
        fresh = Manager(VaultStore(self.path, unlock()), None)  # a cancelled dialog would fail any unlock
        self.assertEqual(fresh.items(), [(n, t) for n, t, _ in ITEMS])

    def test_reveal_returns_the_value(self):
        self.assertEqual(self.manager.reveal("phone"), "13800138000")

    def test_reveal_of_a_missing_item_is_an_error(self):
        with self.assertRaises(StoreError):
            self.manager.reveal("nope")


class TestEdit(ManagerCase):
    def test_edit_uses_the_entry_forms_normalization(self):
        self.assertEqual(self.manager.edit("phone", "+86 139-0000 1111"), "13900001111")
        self.assertEqual(self.manager.edit("birthday", "2006年7月8日"), "2006-07-08")
        self.assertEqual(self.store.read("phone"), "13900001111")
        self.assertEqual(self.manager.items()[2], ("phone", "phone"))  # the type is kept

    def test_edit_uses_the_entry_forms_validation(self):
        with self.assertRaises(Bad):
            self.manager.edit("birthday", "last spring")
        with self.assertRaises(Bad):
            self.manager.edit("name", "   ")
        self.assertEqual(self.store.read("birthday"), "2005-03-04")  # nothing changed

    def test_edit_keeps_the_table_safe_like_the_form(self):
        self.assertEqual(self.manager.edit("name", "A|B  C"), "A｜B C")

    def test_a_choice_item_can_be_edited_as_text(self):
        self.assertEqual(self.manager.edit("size", " L "), "L")


class TestDelete(ManagerCase):
    def test_delete_removes_the_item(self):
        self.manager.delete("phone")
        self.assertNotIn("phone", self.store.names())

    def test_delete_of_a_missing_item_is_an_error(self):
        with self.assertRaises(StoreError):
            self.manager.delete("nope")


class TestBackup(ManagerCase):
    def test_backup_is_encrypted_and_restores_into_an_empty_vault(self):
        self.manager.export(self.backup_file())
        text = open(self.backup_file(), encoding="utf8").read()
        for _, _, value in ITEMS:
            if len(value) > 5:
                self.assertNotIn(value, text)
        self.assertEqual(json.loads(text)["unlock"][0]["method"], "passphrase")
        other = VaultStore(os.path.join(self.tmp.name, "new", "vault.json"), unlock("other", "other"))
        added, replaced = Manager(other, lambda: unlock(BACKUP)).import_(self.backup_file(), lambda names: True)
        self.assertEqual((added, replaced), (len(ITEMS), 0))
        for name, type_, value in ITEMS:
            self.assertEqual(other.read(name), value)
        self.assertEqual(other.entries(), [(n, t) for n, t, _ in ITEMS])

    def test_backup_needs_its_own_passphrase(self):
        self.manager.export(self.backup_file())
        with self.assertRaises(Locked):
            self.read_from(self.backup_file(), MAIN, MAIN, MAIN).read("name")

    def test_export_never_overwrites_a_file(self):
        with open(self.backup_file(), "w") as f:
            f.write("keep me")
        with self.assertRaises(StoreError):
            self.manager.export(self.backup_file())
        self.assertEqual(open(self.backup_file()).read(), "keep me")

    def test_cancelling_the_backup_passphrase_leaves_no_file(self):
        manager = Manager(self.store, lambda: unlock())
        with self.assertRaises(Cancelled):
            manager.export(self.backup_file())
        self.assertEqual([f for f in os.listdir(self.tmp.name) if f != "vault"], [])

    def test_backup_file_is_private(self):
        self.manager.export(self.backup_file())
        self.assertEqual(os.stat(self.backup_file()).st_mode & 0o777, 0o600)

    def test_import_asks_before_replacing_and_can_skip(self):
        self.manager.export(self.backup_file())
        self.store.write("phone", "13911112222", "phone")
        self.store.delete("name")
        asked = []

        def no(names):
            asked.append(names)
            return False

        added, replaced = Manager(self.store, lambda: unlock(BACKUP)).import_(self.backup_file(), no)
        self.assertEqual((added, replaced, asked), (1, 0, [["birthday", "phone", "size"]]))
        self.assertEqual(self.store.read("phone"), "13911112222")
        added, replaced = Manager(self.store, lambda: unlock(BACKUP)).import_(self.backup_file(), lambda n: True)
        self.assertEqual((added, replaced), (0, 4))
        self.assertEqual(self.store.read("phone"), "13800138000")

    def test_import_of_something_that_is_not_a_backup_changes_nothing(self):
        with open(self.backup_file(), "w") as f:
            f.write("not json")
        with self.assertRaises(Damaged):
            self.manager.import_(self.backup_file(), lambda n: True)
        with self.assertRaises(StoreError):
            self.manager.import_(self.backup_file() + ".missing", lambda n: True)
        self.assertEqual(self.store.names(), [n for n, _, _ in ITEMS])

    def test_import_with_the_wrong_passphrase_changes_nothing(self):
        self.manager.export(self.backup_file())
        self.store.delete("name")
        with self.assertRaises(Locked):
            Manager(self.store, lambda: unlock("x", "y", "z")).import_(self.backup_file(), lambda n: True)
        self.assertNotIn("name", self.store.names())


if __name__ == "__main__":
    unittest.main()

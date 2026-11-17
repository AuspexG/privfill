"""The manager window, driven off-screen: in-process through its controls, and through the command's test hook.

Fake data only. Skipped where PyQt6 is not installed.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PyQt6 import QtCore, QtTest, QtWidgets
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

from privfill.texts import ui  # noqa: E402
from privfill.vault import PassphraseUnlock, VaultStore  # noqa: E402

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "privfill.py")
ITEMS = [("name", "text", "Jane Roe"), ("birthday", "date", "2005-03-04"), ("phone", "phone", "13800138000")]
PASSPHRASE = "correct horse 电池 staple"  # made up


def unlock(*answers):
    answers = list(answers)
    return PassphraseUnlock(lambda kind: answers.pop(0) if answers else None, cost=2 ** 10)


class FakeDialogs:
    def __init__(self, confirm=True, path=None):
        self.confirmed, self.path, self.asked = confirm, path, []

    def confirm(self, text):
        self.asked.append(text)
        return self.confirmed

    def save_path(self):
        return self.path

    open_path = save_path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "vault", "vault.json")
        self.store = VaultStore(self.path, unlock(PASSPHRASE))
        for n, t, v in ITEMS:
            self.store.write(n, v, t)

    def tearDown(self):
        self.tmp.cleanup()


@unittest.skipUnless(HAVE_QT, "PyQt6 is not installed")
class TestWindow(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["privfill-test"])

    def window(self, dialogs=None, seconds=8, store=None):
        from privfill.manager import Manager
        from privfill.manager_ui import make_window

        manager = Manager(store or self.store, lambda: unlock("backup pass", "backup pass"))
        w = make_window(manager, dialogs or FakeDialogs(), reveal_seconds=seconds)
        self.addCleanup(w.deleteLater)
        return w

    def test_lists_items_without_showing_or_unlocking_anything(self):
        locked = VaultStore(self.path, unlock())  # any unlock would be cancelled
        w = self.window(store=locked)
        self.assertEqual([w.list.item(i).text().split()[0] for i in range(w.list.count())], ["name", "birthday", "phone"])
        self.assertNotIn("Jane Roe", self.everything(w))

    @staticmethod
    def everything(w):
        return " ".join(x.text() for x in w.findChildren(QtWidgets.QLabel)) + w.edit_input.text()

    def test_value_is_hidden_until_reveal_is_clicked(self):
        w = self.window()
        w.select("phone")
        self.assertNotIn("13800138000", self.everything(w))
        w.reveal_btn.click()
        self.assertEqual(w.value.text(), "13800138000")
        w.reveal_btn.click()  # clicking again hides
        self.assertNotIn("13800138000", self.everything(w))

    def test_revealed_value_hides_itself_after_the_delay(self):
        w = self.window(seconds=0.05)
        w.select("name")
        w.reveal_btn.click()
        self.assertEqual(w.value.text(), "Jane Roe")
        QtTest.QTest.qWait(300)
        self.assertNotIn("Jane Roe", self.everything(w))
        self.assertFalse(w.is_revealed())

    def test_default_delay_is_a_few_seconds(self):
        w = self.window()
        self.assertTrue(1000 <= w.hide_timer.interval() <= 15000)

    def test_choosing_another_item_hides_the_value(self):
        w = self.window()
        w.select("name")
        w.reveal_btn.click()
        w.select("phone")
        self.assertNotIn("Jane Roe", self.everything(w))

    def test_edit_normalizes_like_the_form(self):
        w = self.window()
        w.select("phone")
        w.edit_input.setText("+86 139-0000 1111")
        w.save_btn.click()
        self.assertEqual(self.store.read("phone"), "13900001111")
        self.assertEqual(self.store.entries()[2], ("phone", "phone"))
        self.assertEqual(w.edit_input.text(), "")
        self.assertNotIn("13900001111", self.everything(w))

    def test_edit_that_cannot_be_read_says_why_and_changes_nothing(self):
        w = self.window()
        w.select("birthday")
        w.edit_input.setText("last spring")
        w.save_btn.click()
        self.assertEqual(self.store.read("birthday"), "2005-03-04")
        self.assertEqual(w.edit_error.text(), ui("bad_date"))
        self.assertEqual(w.edit_error.objectName(), "editerr")

    def test_delete_asks_first(self):
        no = FakeDialogs(confirm=False)
        w = self.window(no)
        w.select("phone")
        w.delete_btn.click()
        self.assertEqual(len(no.asked), 1)
        self.assertIn("phone", no.asked[0])
        self.assertIn("phone", self.store.names())
        yes = FakeDialogs()
        w = self.window(yes)
        w.select("phone")
        w.delete_btn.click()
        self.assertNotIn("phone", self.store.names())
        self.assertEqual(w.list.count(), 2)

    def test_export_then_import_restores_an_item(self):
        backup = os.path.join(self.tmp.name, "backup.json")
        w = self.window(FakeDialogs(path=backup))
        w.export_btn.click()
        self.assertTrue(os.path.exists(backup), w.status.text())
        self.store.delete("phone")
        w.refresh()
        w.import_btn.click()
        self.assertEqual(self.store.read("phone"), "13800138000")
        self.assertEqual(w.status.text(), ui("mgr_imported", a=1, r=2))
        self.assertEqual(w.status.property("bad"), "false")

    def test_errors_are_shown_in_the_window(self):
        w = self.window(FakeDialogs(path=os.path.join(self.tmp.name, "missing", "x.json")))
        w.import_btn.click()
        self.assertEqual(w.status.property("bad"), "true")
        self.assertTrue(w.status.text())

    def test_unlock_cancelled_is_shown_not_raised(self):
        w = self.window(store=VaultStore(self.path, unlock()))
        w.select("name")
        w.reveal_btn.click()
        self.assertEqual(w.status.property("bad"), "true")
        self.assertNotIn("Jane Roe", self.everything(w))


@unittest.skipUnless(HAVE_QT, "PyQt6 is not installed")
class TestHook(Base):
    """The command's off-screen hook: fake vault, passphrase from the environment, an image instead of a window."""

    def hook(self, script, shot=None):
        env = dict(os.environ, PRIVFILL_VAULT=self.path, PRIVFILL_PASSPHRASE_TEST=PASSPHRASE, PRIVFILL_LANG="en",
                   QT_QPA_PLATFORM="offscreen", PRIVFILL_MANAGER_SCRIPT=json.dumps(script),
                   PRIVFILL_MANAGER_SHOT=shot or os.path.join(self.tmp.name, "shot.png"))
        p = subprocess.run([sys.executable, TOOL, "manage"], env=env, capture_output=True, text=True, timeout=60)
        return p, env["PRIVFILL_MANAGER_SHOT"]

    def setUp(self):
        super().setUp()
        self.store = VaultStore(self.path, PassphraseUnlock(lambda kind: PASSPHRASE, cost=2 ** 10))

    def test_renders_an_image_and_writes_nothing_to_the_terminal(self):
        p, shot = self.hook([{"select": "phone"}, {"reveal": True}])
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))
        with open(shot, "rb") as f:
            self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")

    def test_script_edits_and_deletes_through_the_window(self):
        p, _ = self.hook([{"select": "phone"}, {"edit": "139 0000 1111"}, {"select": "name"}, {"delete": True}])
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""), p.stderr)
        self.assertEqual(self.store.read("phone"), "13900001111")
        self.assertNotIn("name", self.store.names())

    def test_hook_refuses_the_real_vault_location(self):
        env = dict(os.environ, PRIVFILL_MANAGER_SHOT=os.path.join(self.tmp.name, "s.png"), QT_QPA_PLATFORM="offscreen",
                   PRIVFILL_VAULT="", HOME=self.tmp.name)
        p = subprocess.run([sys.executable, TOOL, "manage"], env=env, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse(os.path.exists(env["PRIVFILL_MANAGER_SHOT"]))


if __name__ == "__main__":
    unittest.main()

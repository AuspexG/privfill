"""The managed settings wizard (issue #6), run against a temporary directory without sudo."""
import json
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIZARD = os.path.join(ROOT, "tools", "managed-settings-wizard.sh")


class TestWizard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = os.path.join(self.tmp.name, "etc", "claude-code", "managed-settings.json")
        self.env = dict(os.environ, PRIVFILL_MANAGED_SETTINGS=self.target, PRIVFILL_SUDO="",
                        PRIVFILL_HOME=os.path.join(self.tmp.name, "pf"))

    def tearDown(self):
        self.tmp.cleanup()

    def run_wizard(self, *args, answer="y"):
        p = subprocess.run(["sh", WIZARD, *args], input=answer + "\n", env=self.env, capture_output=True,
                           text=True, timeout=60)
        return p.returncode, p.stdout + p.stderr

    def get(self):
        with open(self.target, encoding="utf8") as f:
            return json.load(f)

    def test_shows_the_change_and_writes_nothing_without_a_yes(self):
        rc, out = self.run_wizard(answer="n")
        self.assertEqual(rc, 1)
        self.assertIn("+", out)
        self.assertIn("privfill.py guard", out)
        self.assertFalse(os.path.exists(self.target))

    def test_install_then_remove_leaves_no_file_when_there_was_none(self):
        rc, out = self.run_wizard()
        self.assertEqual(rc, 0, out)
        self.assertIn("privfill.py guard", json.dumps(self.get()))
        rc, out = self.run_wizard("--remove")
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(self.target))
        self.assertEqual(os.listdir(os.path.dirname(self.target)), [])

    def test_an_existing_file_is_merged_and_restored(self):
        os.makedirs(os.path.dirname(self.target))
        before = {"permissions": {"deny": ["Bash(curl *)"]}, "companyAnnouncements": ["hi"]}
        with open(self.target, "w", encoding="utf8") as f:
            json.dump(before, f)
        with open(self.target, encoding="utf8") as f:
            raw = f.read()
        self.assertEqual(self.run_wizard()[0], 0)
        s = self.get()
        self.assertEqual(s["companyAnnouncements"], ["hi"])
        self.assertIn("Bash(curl *)", s["permissions"]["deny"])
        self.assertIn("Read(~/.privfill/**)".replace("~/.privfill", self.env["PRIVFILL_HOME"]), s["permissions"]["deny"])
        rc, out = self.run_wizard()  # a second run has nothing to do
        self.assertIn("nothing to do", out)
        self.assertEqual(self.run_wizard("--remove")[0], 0)
        with open(self.target, encoding="utf8") as f:
            self.assertEqual(f.read(), raw)

    def test_verify_fails_on_a_file_the_user_can_write(self):
        self.run_wizard()
        rc, out = self.run_wizard("--verify")
        self.assertNotEqual(rc, 0)
        self.assertIn("FAIL", out)


if __name__ == "__main__":
    unittest.main()

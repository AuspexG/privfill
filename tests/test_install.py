"""The installer copies the launcher and the package, and the documented invocations work from
the installed locations. Runs against a temporary HOME; fake data only."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestInstall(unittest.TestCase):
    def test_installed_program_and_skill_copy_run(self):
        with tempfile.TemporaryDirectory() as home:
            env = {k: v for k, v in os.environ.items() if not k.startswith("PRIVFILL_")}
            env["HOME"] = home
            p = subprocess.run(["sh", os.path.join(ROOT, "install.sh"), "--skill"], env=env, cwd=home,
                               capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            data = os.path.join(home, ".privfill", "vault.json")
            self.assertFalse(os.path.exists(data), "the installer must not create a vault: that needs the person")
            self.assertTrue(os.path.exists(os.path.join(home, ".claude", "skills", "privfill", "SKILL.md")))
            fake = os.path.join(home, "fake", "vault.json")  # the test hooks refuse a vault in ~/.privfill
            run_env = dict(env, PRIVFILL_VAULT=fake, PRIVFILL_LANG="en", PRIVFILL_PASSPHRASE_TEST="made-up passphrase",
                           PRIVFILL_FORM_TEST=json.dumps({"nickname": "JR"}))
            for where in (".privfill", os.path.join(".claude", "skills", "privfill")):
                tool = os.path.join(home, where, "privfill.py")
                p = subprocess.run([sys.executable, tool, "--json", "add", "nickname"], env=run_env, cwd=home,
                                   capture_output=True, text=True, stdin=subprocess.DEVNULL)
                self.assertEqual(p.returncode, 0, (where, p.stdout + p.stderr))
                p = subprocess.run([sys.executable, tool, "--json", "keys"], env=run_env, cwd=home,
                                   capture_output=True, text=True, stdin=subprocess.DEVNULL)
                self.assertEqual(json.loads(p.stdout)["items"], ["nickname"], where)


if __name__ == "__main__":
    unittest.main()

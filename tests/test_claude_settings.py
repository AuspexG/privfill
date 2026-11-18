"""Merging the guard configuration into a Claude Code settings file and taking it out (issue #5)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "tools", "claude_settings.py")
PROGRAM = os.path.join(os.path.expanduser("~"), ".privfill", "privfill.py")  # only named, never read
BEFORE = {
    "theme": "dark",
    "env": {"A": "1"},
    "permissions": {"ask": ["Read(~/.privfill/**)", "Edit(~/.privfill/**)", "Bash(*privfill*)", "Bash(rm *)"],
                    "allow": ["Bash(git status)"], "defaultMode": "auto"},
    "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "log-it"}]}],
              "Stop": [{"hooks": [{"type": "command", "command": "beep"}]}]},
}


def normal(settings):
    """Order-insensitive form of the rule lists, for comparing round trips."""
    s = json.loads(json.dumps(settings))
    for k, v in s.get("permissions", {}).items():
        if isinstance(v, list):
            s["permissions"][k] = sorted(v)
    return s


class SettingsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "settings.json")

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, data):
        with open(self.path, "w", encoding="utf8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def get(self):
        with open(self.path, encoding="utf8") as f:
            return json.load(f)

    def run_script(self, action, *extra):
        args = [sys.executable, SCRIPT, action, "--settings", self.path]
        if action != "remove":
            args += ["--program", PROGRAM, "--python", "/usr/bin/python3"]
        p = subprocess.run(args + list(extra), capture_output=True, text=True, timeout=30)
        return p.returncode, p.stdout + p.stderr


class TestMerge(SettingsCase):
    def test_apply_keeps_everything_else_and_replaces_the_ask_rules(self):
        self.put(BEFORE)
        rc, out = self.run_script("apply")
        self.assertEqual(rc, 0, out)
        s = self.get()
        self.assertEqual((s["theme"], s["env"], s["hooks"]["Stop"]), ("dark", {"A": "1"}, BEFORE["hooks"]["Stop"]))
        self.assertEqual(s["permissions"]["ask"], ["Bash(rm *)"])
        self.assertIn("Read(~/.privfill/**)", s["permissions"]["deny"])
        self.assertIn("Edit(~/.privfill/**)", s["permissions"]["deny"])
        self.assertIn("Bash(git status)", s["permissions"]["allow"])
        self.assertIn("Bash(python3 ~/.privfill/privfill.py render *)", s["permissions"]["allow"])
        self.assertIn("Bash(python3 ~/.claude/skills/privfill/privfill.py --json keys *)", s["permissions"]["allow"])
        guard = [h for g in s["hooks"]["PreToolUse"] for h in g["hooks"] if "guard" in h["command"]]
        self.assertEqual([h["command"] for h in guard], ["/usr/bin/python3 ~/.privfill/privfill.py guard"])
        self.assertEqual([g["matcher"] for g in s["hooks"]["PreToolUse"]], ["Bash", "*"])
        self.assertNotIn("sandbox", s)

    def test_apply_twice_changes_nothing_the_second_time(self):
        self.put(BEFORE)
        self.run_script("apply")
        once = self.get()
        rc, out = self.run_script("apply")
        self.assertEqual((rc, self.get()), (0, once))
        self.assertIn("nothing changed", out)

    def test_remove_restores_the_previous_settings(self):
        self.put(BEFORE)
        self.run_script("apply")
        self.run_script("apply")
        rc, out = self.run_script("remove")
        self.assertEqual(rc, 0, out)
        self.assertEqual(normal(self.get()), normal(BEFORE))
        self.assertFalse(os.path.exists(self.path + ".privfill.json"))

    def test_rules_the_person_had_before_survive_removal(self):
        mine = dict(BEFORE, permissions=dict(BEFORE["permissions"], deny=["Read(~/.privfill/**)"]))
        self.put(mine)
        self.run_script("apply")
        self.run_script("remove")
        self.assertEqual(self.get()["permissions"]["deny"], ["Read(~/.privfill/**)"])

    def test_sandbox_option_and_its_removal(self):
        self.put({"sandbox": {"network": {"allowedDomains": ["example.org"]}}})
        rc, out = self.run_script("apply", "--sandbox")
        self.assertEqual(rc, 0, out)
        sb = self.get()["sandbox"]
        self.assertTrue(sb["enabled"])
        self.assertIn("python3 ~/.privfill/privfill.py *", sb["excludedCommands"])
        self.run_script("remove")
        self.assertEqual(self.get(), {"sandbox": {"network": {"allowedDomains": ["example.org"]}}})

    def test_a_missing_file_is_created_and_removed_to_empty(self):
        rc, out = self.run_script("apply")
        self.assertEqual(rc, 0, out)
        self.run_script("remove")
        self.assertEqual(self.get(), {})

    def test_an_unreadable_file_is_never_overwritten(self):
        self.put('{"theme": "dark",')
        rc, _ = self.run_script("apply")
        self.assertEqual(rc, 1)
        with open(self.path, encoding="utf8") as f:
            self.assertEqual(f.read(), '{"theme": "dark",')

    def test_plan_writes_nothing(self):
        self.put(BEFORE)
        rc, out = self.run_script("plan")
        self.assertEqual(rc, 0)
        self.assertEqual(self.get(), BEFORE)
        self.assertIn("privfill.py guard", out)


if __name__ == "__main__":
    unittest.main()

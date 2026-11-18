"""Audit presets (issue #14): assistant transcripts and the git pre-commit check. Fake data only."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from privfill.vault import PassphraseUnlock, VaultStore  # noqa: E402

TOOL = os.path.join(ROOT, "privfill.py")
PASSPHRASE = "preset tests 口令"
ITEMS = [("name", "text", "Jane Roe"), ("phone", "phone", "13800138000"), ("地址", "text", "示例路 1 号 Ünit 2")]
SECRETS = [v for _, _, v in ITEMS] + [PASSPHRASE, "138 0013 8000"]


class PresetCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = os.path.join(self.tmp.name, "store", "vault.json")
        store = VaultStore(self.vault, PassphraseUnlock(lambda kind: PASSPHRASE, cost=2 ** 10))
        for n, t, v in ITEMS:
            store.write(n, v, t)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("PRIVFILL_", "GIT_"))}
        self.env.update(HOME=os.path.join(self.tmp.name, "home"), PRIVFILL_VAULT=self.vault, PRIVFILL_LANG="en",
                        PRIVFILL_PASSPHRASE_TEST=PASSPHRASE, PRIVFILL_KEYRING_TEST="none",
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        os.makedirs(self.env["HOME"])

    def tearDown(self):
        self.tmp.cleanup()

    def clean(self, text):
        for s in SECRETS:
            self.assertNotIn(s, text)
        return text

    def tool(self, *args, cwd=None):
        p = subprocess.run([sys.executable, TOOL, *args], env=self.env, cwd=cwd or self.tmp.name,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.clean(p.stdout + p.stderr)
        return p.returncode, p.stdout


class TestTranscripts(PresetCase):
    def setUp(self):
        super().setUp()
        self.dir = os.path.join(self.tmp.name, "transcripts", "-home-someone-project")
        os.makedirs(self.dir)

    def session(self, name, *texts):
        with open(os.path.join(self.dir, name), "w", encoding="utf8") as f:
            for t in texts:  # as transcripts store them: JSON lines, non-ASCII escaped
                f.write(json.dumps({"type": "user", "message": {"content": t}}) + "\n")

    def test_reports_sessions_by_index_and_counts_only(self):
        self.session("a.jsonl", "hello", "nothing personal")
        self.session("b.jsonl", "my phone is 138 0013 8000", "I live at 示例路 1 号 Ünit 2")
        with open(os.path.join(self.dir, "notes.txt"), "w", encoding="utf8") as f:
            f.write("Jane Roe")  # not a transcript: not scanned
        rc, out = self.tool("--json", "audit", "--transcripts", os.path.dirname(self.dir))
        self.assertEqual(rc, 6, out)
        doc = json.loads(out)
        self.assertEqual(doc["locations"], [{"index": 1, "items": {"phone": 1, "地址": 1}}])
        self.assertEqual(doc["scanned"], 2)
        self.assertIn("session 1", doc["message"])
        self.assertIn("already been sent", doc["message"])

    def test_the_default_directory_is_under_home(self):
        rc, out = self.tool("--json", "audit", "--transcripts")
        self.assertEqual(rc, 1)  # ~/.claude/projects does not exist in the fake home
        os.makedirs(os.path.join(self.env["HOME"], ".claude", "projects"))
        rc, out = self.tool("--json", "audit", "--transcripts")
        self.assertEqual((rc, json.loads(out)["scanned"]), (0, 0))


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class TestPreCommit(PresetCase):
    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")

    def git(self, *args, check=True):
        p = subprocess.run(["git", *args], cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=60)
        if check and p.returncode:
            self.fail(f"git {args[0]} failed")
        return p

    def put(self, name, text):
        with open(os.path.join(self.repo, name), "w", encoding="utf8") as f:
            f.write(text)
        self.git("add", "--", name)

    def commit(self):
        start = time.monotonic()
        p = self.git("commit", "-q", "-m", "change", check=False)
        self.clean(p.stdout + p.stderr)
        return p.returncode, p.stderr, time.monotonic() - start

    def test_blocks_staged_values_and_lets_clean_commits_through_fast(self):
        rc, _ = self.tool("--json", "precommit", "install", self.repo)
        self.assertEqual(rc, 0)
        self.put("ok.txt", "nothing personal\n")
        rc, err, took = self.commit()
        self.assertEqual(rc, 0, err)
        self.assertLess(took, 3)  # about a second on a normal machine; slack for a loaded test run
        self.put("bad.txt", "call me at 138 0013 8000\n")
        rc, err, _ = self.commit()
        self.assertEqual(rc, 1)
        self.assertIn("phone", err)
        self.assertIn("commit was stopped", err)

    def test_a_value_in_a_staged_file_name_is_caught(self):
        self.tool("precommit", "install", self.repo)
        self.put("letter_Jane Roe.md", "x\n")
        rc, err, _ = self.commit()
        self.assertEqual(rc, 1)
        self.assertIn("name", err)

    def test_an_earlier_hook_keeps_running_and_comes_back_on_removal(self):
        hooks = os.path.join(self.repo, ".git", "hooks")
        os.makedirs(hooks, exist_ok=True)
        mine = os.path.join(hooks, "pre-commit")
        with open(mine, "w", encoding="utf8") as f:
            f.write("#!/bin/sh\necho earlier-hook-ran >&2\n")
        os.chmod(mine, 0o755)
        rc, out = self.tool("--json", "precommit", "install", self.repo)
        self.assertEqual(json.loads(out)["status"], "chained")
        self.put("ok.txt", "fine\n")
        rc, err, _ = self.commit()
        self.assertEqual(rc, 0, err)
        self.assertIn("earlier-hook-ran", err)
        rc, out = self.tool("--json", "precommit", "remove", self.repo)
        self.assertEqual(json.loads(out)["status"], "restored")
        with open(mine, encoding="utf8") as f:
            self.assertIn("earlier-hook-ran", f.read())

    def test_install_twice_and_remove_twice(self):
        statuses = [json.loads(self.tool("--json", "precommit", *a, self.repo)[1])["status"]
                    for a in (["install"], ["install"], ["remove"], ["remove"])]
        self.assertEqual(statuses, ["installed", "updated", "removed", "absent"])
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "hooks", "pre-commit")))

    def test_not_a_repository(self):
        rc, _ = self.tool("precommit", "install", self.tmp.name)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()

"""Sensitivity levels and the local confirmation (issue #15). Fake data only."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from privfill.sensitivity import Grants, default_level, grant_key  # noqa: E402
from privfill.store import Damaged  # noqa: E402
from privfill.vault import PassphraseUnlock, VaultStore  # noqa: E402

TOOL = os.path.join(ROOT, "privfill.py")
PASSPHRASE = "sensitivity tests 口令"
SAMPLE_ID = "11010519491231002X"  # the specimen number used in published examples; belongs to no one
ITEMS = [("name", "text", "Jane Roe"), ("phone", "phone", "13800138000"), ("id number", "id_card", SAMPLE_ID)]


class SensitivityCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.vault = os.path.join(self.dir, "store", "vault.json")
        for n, t, v in ITEMS:
            self.store().write(n, v, t)
        self.outputs = []

    def tearDown(self):
        for out in self.outputs:
            for _, _, v in ITEMS:
                self.assertNotIn(v, out)
        self.tmp.cleanup()

    def store(self):
        return VaultStore(self.vault, PassphraseUnlock(lambda kind: PASSPHRASE, cost=2 ** 10))

    def path(self, name, text=None):
        p = os.path.join(self.dir, name)
        if text is not None:
            with open(p, "w", encoding="utf8") as f:
                f.write(text)
        return p

    def tool(self, *args, answer="no", answers=None, **env):
        e = {k: v for k, v in os.environ.items() if not k.startswith("PRIVFILL_")}
        e.update(HOME=os.path.join(self.dir, "home"), PRIVFILL_VAULT=self.vault, PRIVFILL_LANG="en",
                 PRIVFILL_PASSPHRASE_TEST=PASSPHRASE, PRIVFILL_KEYRING_TEST="none", QT_QPA_PLATFORM="offscreen")
        e.update(env)
        if answer is not None:
            e["PRIVFILL_CONFIRM_TEST"] = answer
        if answers is not None:
            e["PRIVFILL_FORM_TEST"] = json.dumps(answers)
        p = subprocess.run([sys.executable, TOOL, "--json", *args], env=e, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60, cwd=self.dir)
        self.outputs.append(p.stdout + p.stderr)
        return p.returncode, json.loads(p.stdout)


class TestLevels(SensitivityCase):
    def test_defaults_by_type(self):
        self.assertEqual(self.store().levels(), {"name": "low", "phone": "medium", "id number": "high"})
        self.assertEqual(default_level("choice"), "low")

    def test_the_protocol_raises_a_level_but_never_lowers_one(self):
        spec = {"fields": [{"name": "name", "sensitivity": "high"}, {"name": "id number", "type": "id_card", "sensitivity": "low"},
                           {"name": "nickname", "sensitivity": "high"}]}
        rc, out = self.tool("form", json.dumps(spec), answers={"nickname": "Jay"})
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.store().levels(), {"name": "high", "phone": "medium", "id number": "high", "nickname": "high"})

    def test_an_unknown_level_is_an_invalid_protocol(self):
        rc, _ = self.tool("form", json.dumps({"fields": [{"name": "x", "sensitivity": "secret"}]}), answers={})
        self.assertEqual(rc, 4)

    def test_a_new_value_keeps_the_level(self):
        s = self.store()
        s.raise_level("name", "high")
        s.write("name", "Janet Roe", "text")
        self.assertEqual(self.store().levels()["name"], "high")

    def test_lowering_a_level_in_the_file_is_detected(self):
        self.store().raise_level("name", "high")
        with open(self.vault, encoding="utf8") as f:
            vault = json.load(f)
        next(i for i in vault["items"] if i["name"] == "name")["sensitivity"] = "low"
        with open(self.vault, "w", encoding="utf8") as f:
            json.dump(vault, f)
        with self.assertRaises(Damaged):
            self.store().read("name")


class TestConfirmation(SensitivityCase):
    def test_low_and_medium_items_never_ask(self):
        rc, out = self.tool("render", self.path("m.md", "{{name}} {{phone}}"), self.path("o.md"))
        self.assertEqual(rc, 0, out)

    def test_a_decline_is_exit_7_and_writes_nothing(self):
        rc, out = self.tool("render", self.path("m.md", "{{name}} {{id number|last4}}"), self.path("o.md"))
        self.assertEqual((rc, out["declined"]), (7, ["id number"]), out)
        self.assertFalse(os.path.exists(self.path("o.md")))
        log = open(os.path.join(self.dir, "store", "activity.log"), encoding="utf8").read()
        self.assertIn("declined", log)

    def test_allow_once_asks_again_next_time(self):
        master = self.path("m.md", "{{id number}}")
        self.assertEqual(self.tool("render", master, self.path("o.md"), answer="yes")[0], 0)
        self.assertEqual(self.tool("render", master, self.path("o2.md"))[0], 7)

    def test_a_remembered_grant_covers_the_same_master_only(self):
        master = self.path("m.md", "{{id number}}")
        self.assertEqual(self.tool("render", master, self.path("o.md"), answer="remember")[0], 0)
        rc, out = self.tool("render", master, self.path("o2.md"))  # "no" would be the answer if asked
        self.assertEqual(rc, 0, out)
        self.path("m.md", "{{id number}} changed")
        self.assertEqual(self.tool("render", master, self.path("o3.md"))[0], 7)

    def test_fill_and_pack_ask_too(self):
        self.assertEqual(self.tool("fill", self.path("f.md", "{{id number}}"))[0], 7)
        with open(self.path("f.md"), encoding="utf8") as f:
            self.assertEqual(f.read(), "{{id number}}")
        self.path("a.txt", "x")
        rc, out = self.tool("pack", self.path("{{id number|last4}}.zip"), self.path("a.txt"))
        self.assertEqual(rc, 7, out)
        self.assertEqual([n for n in os.listdir(self.dir) if n.endswith(".zip")], [])

    def test_the_answer_hook_refuses_the_persons_own_vault(self):
        own = os.path.join(self.dir, "home", ".privfill", "vault.json")
        os.makedirs(os.path.dirname(own))
        os.replace(self.vault, own)
        rc, out = self.tool("render", self.path("m.md", "{{id number}}"), self.path("o.md"), answer="yes",
                            PRIVFILL_VAULT=own)
        self.assertNotEqual(rc, 0)
        self.assertFalse(os.path.exists(self.path("o.md")))

    def test_the_window_draws_off_screen(self):
        try:
            import PyQt6.QtWidgets  # noqa: F401
        except ImportError:
            self.skipTest("PyQt6 is not installed")
        shot = self.path("confirm.png")
        rc, _ = self.tool("render", self.path("m.md", "{{id number}}"), self.path("o.md"), PRIVFILL_CONFIRM_SHOT=shot)
        self.assertEqual(rc, 7)
        self.assertGreater(os.path.getsize(shot), 1000)


class TestGrants(SensitivityCase):
    def test_a_grant_expires(self):
        master = self.path("m.md", "{{id number}}")
        now = [1000.0]
        grants = Grants(self.store(), clock=lambda: now[0])
        key = grant_key("render", [master], ["id number"])
        grants.add(key, seconds=600)
        self.assertTrue(grants.live(key))
        now[0] += 601
        self.assertFalse(grants.live(key))

    def test_a_forged_grant_is_ignored(self):
        master = self.path("m.md", "{{id number}}")
        key = grant_key("render", [master], ["id number"])
        with open(os.path.join(self.dir, "store", "grants.json"), "w", encoding="utf8") as f:
            json.dump([{"key": key, "expires": 4102444800, "tag": "AAAAAAAAAAAAAAAA:AAAAAAAAAAAAAAAAAAAAAA=="}], f)
        self.assertFalse(Grants(self.store()).live(key))
        self.assertEqual(self.tool("render", master, self.path("o.md"))[0], 7)


if __name__ == "__main__":
    unittest.main()

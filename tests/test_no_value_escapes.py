"""The invariant: whatever a command is given, nothing it prints contains a stored value.

Every command is run as the real program, with hostile or broken input, plainly and with --json.
Standard output and standard error are then searched for every stored value, for every answer
typed into the form, and for every fragment of either that is FRAGMENT characters long.

To add a command: list it in COMMANDS in privfill/cli.py (it cannot run otherwise) and give it
scenarios here. test_every_command_is_listed fails until both are done.

Fake data only, and of a kind that cannot belong to anyone: the ID number starts with a region
code that is not assigned (99) and the phone number is in no mobile range. The strings are also
chosen so that no fragment of them appears in the program's own messages by coincidence.
"""
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "privfill.py")
sys.path.insert(0, ROOT)

from privfill.cli import COMMANDS  # noqa: E402

PASSPHRASE = "plum-anvil-seventeen-kite"  # made up; typed by the test hook, never by a person
FRAGMENT = 4  # a run of this many characters of a value counts as a leak

SPEC = {"fields": [
    {"name": "name", "required": True},
    {"name": "birthday", "type": "date"},
    {"name": "phone", "type": "phone"},
    {"name": "id number", "type": "id_card"},
    {"name": "email", "type": "email"},
    {"name": "address"},
]}
STORED = {  # as typed into the form
    "name": "Qwyzbek Vorplux",
    "birthday": "1987-06-15",
    "phone": "99955509264",
    "id number": "990101198706151239",
    "email": "jq7@zubrix.wxyz",
    "address": "88 Grzmotowa Ulice",
}
# Answers the form must refuse. They are personal details too (a mistyped one is still mine).
REFUSED = {
    "birthday": "1987-13-45 Hxqvrt",
    "phone": "99955-Kzjwpq-9264",
    "id number": "990101198706151237",
    "email": "Wvbnmq.zubrix.wxyz",
    "name": "Zzyqxw Ploktuv",
}


def fragments(value):
    return {value[i:i + FRAGMENT] for i in range(len(value) - FRAGMENT + 1)}


# The same details as a format or a careless retyping would write them.
VARIANTS = ["1987.06.15", "1987/06/15", "19870615", "1987年6月15日", "QWYZBEK VORPLUX", "qwyzbek vorplux"]
SECRETS = sorted(set().union(*(fragments(v) for v in [*STORED.values(), *REFUSED.values(), *VARIANTS])))


class World:
    """A temporary directory with a seeded vault and the broken things the scenarios point at."""

    def __init__(self, case):
        self.case = case
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.data = self.path("store", "vault.json")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("PRIVFILL_")}
        self.env.update(PRIVFILL_VAULT=self.data, PRIVFILL_PASSPHRASE_TEST=PASSPHRASE, PRIVFILL_LANG="en",
                        QT_QPA_PLATFORM="offscreen")
        rc, out, err = self.run_tool(["form", json.dumps(SPEC)], answers=STORED)
        case.assertEqual(rc, 0, "seeding failed")
        self.write("master.md", "{{name}} / {{birthday|YYYY.MM.DD}} / {{phone|last4}}\n")
        self.write("no-such-item.md", "{{name}} {{shoe size}}\n")
        self.write("bad-format.md", "{{name|YYYY-MM-DD}} {{birthday|sideways}}\n")
        # An output that is filled a second time: its braces now hold values, not item names.
        self.write("filled-twice.md", "\\textbf{{Qwyzbek Vorplux}} {{ 99955509264 }}\n")
        self.write("value-as-format.md", "{{name|Qwyzbek Vorplux}} {{birthday|1987.06.15 at noon}}\n")
        self.write("value-as-name.json", '{"a": "{{QWYZBEK  VORPLUX}}", "b": ["{{jq7@zubrix.wxyz|last4}}"]}')
        self.write("broken.json", '{"a": "{{name}}", ')
        self.write("broken.docx", "{{name}} this is not a zip archive")
        self.write("not-utf8.md", b"{{name}} \xff\xfe\x00 {{phone}}")
        self.write("spec-not-utf8.json", b'{"fields": [{"name": "\xff\xfe"}]}')
        with zipfile.ZipFile(self.path("bad-xml.docx"), "w") as z:
            z.writestr("word/document.xml", b"<w:p><w:t>{{name}}\xff</w:t></w:p>")
        with zipfile.ZipFile(self.path("missing.docx"), "w") as z:
            z.writestr("word/document.xml", "<w:p><w:t>{{na</w:t><w:t>me}} {{shoe size}}</w:t></w:p>")
        os.mkdir(self.path("out"))
        os.mkdir(self.path("locked"))
        self.write("locked/master.md", "{{name}}\n")
        os.chmod(self.path("locked"), 0)
        self.write("unreadable.md", "{{name}}\n")
        os.chmod(self.path("unreadable.md"), 0)

    def close(self):
        os.chmod(self.path("locked"), 0o700)
        self.tmp.cleanup()

    def path(self, *parts):
        return os.path.join(self.dir, *parts)

    def write(self, name, content):
        os.makedirs(os.path.dirname(self.path(name)), exist_ok=True)
        with open(self.path(name), "wb") as f:
            f.write(content if isinstance(content, bytes) else content.encode("utf8"))

    def run_tool(self, args, answers=..., env=None):
        e = dict(self.env, **(env or {}))
        if answers is not ...:
            e["PRIVFILL_FORM_TEST"] = json.dumps(answers)
        p = subprocess.run([sys.executable, TOOL, *args], env=e, cwd=self.dir, capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=60)
        return p.returncode, p.stdout.decode("utf8", "replace"), p.stderr.decode("utf8", "replace")

    # Ways to break the store itself.
    def corrupt_store(self):
        with open(self.data, "ab") as f:
            f.write(b"\n| broken | \xff\xfe\x80 |\n\x00")

    def truncate_store(self):
        with open(self.data, "rb+") as f:
            f.truncate(os.path.getsize(self.data) * 2 // 3)

    def alter_store(self):
        """Still a well-formed vault file, but every stored ciphertext has been tampered with."""
        with open(self.data, encoding="utf8") as f:
            vault = json.load(f)

        def flip(node):
            if isinstance(node, dict):
                return {k: flip(v) for k, v in node.items()}
            if isinstance(node, list):
                return [flip(v) for v in node]
            if isinstance(node, str) and len(node) > 20:  # an encoded key, nonce or ciphertext
                return node[:8] + ("A" if node[8] != "A" else "B") + node[9:]
            return node

        with open(self.data, "w", encoding="utf8") as f:
            json.dump(flip(vault), f)

    def store_is_not_a_vault(self):
        with open(self.data, "w", encoding="utf8") as f:
            json.dump({"items": {"name": STORED["name"]}, "note": "{{name}}"}, f)

    def lock_store(self):
        os.chmod(self.data, 0)

    def store_is_a_directory(self):
        os.unlink(self.data)
        os.mkdir(self.data)

    def outputs_named_by_values(self):
        """Outputs whose paths hold values, so the output registry holds them too."""
        for args in (["render", "master.md", "out/{{name}}.md"], ["render", "master.md", "{{email}}.md"]):
            rc, _, _ = self.run_tool(args)
            self.case.assertEqual(rc, 0, "rendering a named output failed")

    def registry_is_garbage(self):
        self.outputs_named_by_values()
        with open(self.path("store", "outputs.json"), "ab") as f:
            f.write(b"\xff{" + STORED["phone"].encode("utf8"))

    # Ways to break the activity log beside it.
    @property
    def log(self):
        return self.path("store", "activity.log")

    def log_holds_a_value(self):
        """A record whose fields were rewritten to hold values: verify must not quote them."""
        with open(self.log, encoding="utf8") as f:
            rec = json.loads(f.readline())
        rec["items"] = {STORED["name"]: STORED["phone"]}
        rec["command"] = STORED["email"]
        with open(self.log, "a", encoding="utf8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def log_is_garbage(self):
        with open(self.log, "ab") as f:
            f.write(STORED["name"].encode("utf8") + b" \xff\xfe {not json\n")

    def log_is_locked(self):
        os.chmod(self.log, 0)

    def log_is_a_directory(self):
        os.unlink(self.log)
        os.mkdir(self.log)


def spec(*fields, **top):
    return json.dumps(dict(top, fields=list(fields)))


ONE = spec({"name": "name", "required": True})
FILES = ["filled-twice.md", "value-as-format.md", "value-as-name.json", "master.md", "no-such-item.md", "bad-format.md", "broken.json", "broken.docx", "not-utf8.md",
         "bad-xml.docx", "missing.docx", "unreadable.md", "locked/master.md", "absent.md", "out", ""]
LOG_BREAKS = ["log_holds_a_value", "log_is_garbage", "log_is_locked", "log_is_a_directory"]
STORE_BREAKS = ["corrupt_store", "truncate_store", "alter_store", "store_is_not_a_vault", "lock_store",
                "store_is_a_directory"]
SHOT = {"PRIVFILL_MANAGER_SHOT": "shot.png"}
WRONG_PASSPHRASE = {"PRIVFILL_PASSPHRASE_TEST": "not-the-passphrase"}


def scenario(*args, answers=..., before=None, env=None):
    return {"args": list(args), "answers": answers, "before": before, "env": env}


def with_broken_store(*args, **kw):
    """The run against each kind of damaged store, and against a sound one that will not unlock."""
    return [*(scenario(*args, before=b, **kw) for b in STORE_BREAKS), scenario(*args, env=WRONG_PASSPHRASE, **kw)]


# command -> the hostile runs it must survive without printing a value
SCENARIOS = {
    "help": [scenario("help"), scenario("-h"), scenario("--help", "{{name}}"), scenario()],
    "version": [scenario("version"), scenario("--version"), scenario("version", "{{name}}")],
    "protocol": [scenario("protocol"), scenario("protocol", "{{name}}"), *with_broken_store("protocol")],
    "init": [scenario("init"), scenario("init", "{{name}}"), *with_broken_store("init"),
             scenario("init", env={"PRIVFILL_VAULT": "locked/deeper/vault.json"}),
             scenario("init", env={"PRIVFILL_VAULT": "master.md/vault.json"}),
             scenario("init", env={"PRIVFILL_VAULT": "out/new.json", "PRIVFILL_PASSPHRASE_TEST": ""})],
    "keys": [scenario("keys"), scenario("keys", "{{name}}"), *with_broken_store("keys"),
             scenario("keys", env={"PRIVFILL_VAULT": "not-utf8.md"}),
             scenario("keys", env={"PRIVFILL_VAULT": "broken.json"}),
             scenario("keys", env={"PRIVFILL_VAULT": "value-as-name.json"})],
    "form": [
        scenario("form"),
        scenario("form", ONE, ONE),
        scenario("form", "{not json"),
        scenario("form", "[]"),
        scenario("form", "null"),
        scenario("form", "{{name}}"),
        scenario("form", spec({"name": "name", "value": "{{name}}"})),
        scenario("form", spec({"name": "name", "default": "x"})),
        scenario("form", spec({"name": "name"}, protocol=99)),
        scenario("form", spec({"name": "a|b"})),
        scenario("form", spec({"name": "name", "type": "telepathy"})),
        scenario("form", spec({"name": "name", "pattern": "("})),
        scenario("form", spec({"name": "name", "type": "choice"})),
        scenario("form", spec({"name": 7})),
        scenario("form", "spec-not-utf8.json"),
        scenario("form", "unreadable.md"),
        scenario("form", "broken.json"),
        scenario("form", "out"),
        scenario("form", json.dumps(SPEC), answers=None),  # the person cancels
        scenario("form", json.dumps(SPEC), answers={}),  # a required answer is left empty
        scenario("form", json.dumps(SPEC), answers=STORED),  # nothing changes
        scenario("form", json.dumps(SPEC), answers=dict(STORED, name=REFUSED["name"])),  # an update
        *[scenario("form", json.dumps(SPEC), answers=dict(STORED, **{k: v}))
          for k, v in REFUSED.items() if k != "name"],
        scenario("form", spec({"name": "name", "pattern": "[0-9]+"}), answers=STORED),
        scenario("form", spec({"name": "name", "pattern": "(a+)+$", "pattern_hint": "digits"}), answers=STORED),
        scenario("form", spec({"name": "name", "type": "choice", "choices": ["S", "M"]}), answers=STORED),
        scenario("form", spec({"name": "name", "type": "date"}), answers=STORED),
        scenario("form", ONE, answers="not an object"),
        scenario("form", ONE, answers={"name": {"nested": REFUSED["name"]}}),
        *with_broken_store("form", json.dumps(SPEC), answers=STORED),
        *with_broken_store("form", json.dumps(SPEC), answers=dict(STORED, name=REFUSED["name"])),
    ],
    "add": [
        scenario("add"),
        scenario("add", "name", "extra"),
        scenario("add", "a|b"),
        scenario("add", "{{name}}"),
        scenario("add", ""),
        scenario("add", "Item"),
        scenario("add", "name", answers=None),
        scenario("add", "name", answers={}),
        scenario("add", "name", answers={"name": REFUSED["name"]}),
        scenario("add", "nickname", answers={"nickname": REFUSED["name"]}),
        *with_broken_store("add", "name", answers={"name": REFUSED["name"]}),
    ],
    "render": [
        scenario("render"),
        scenario("render", "master.md"),
        scenario("render", "master.md", "out/a.md", "extra"),
        *[scenario("render", f, "out/result" + os.path.splitext(f)[1]) for f in FILES],
        *[scenario("render", f, "out/{{name}}_{{phone}}" + os.path.splitext(f)[1]) for f in FILES],
        scenario("render", "master.md", "master.md"),
        scenario("render", "master.md", "out/{{shoe size}}.md"),
        scenario("render", "master.md", "out/{{birthday|sideways}}.md"),
        scenario("render", "master.md", "{{name}}/{{email}}/deep.md"),  # no such directory
        scenario("render", "master.md", "locked/{{name}}.md"),
        scenario("render", "master.md", "out"),
        scenario("render", "master.md", "out/" + "{{id number}}" * 40 + ".md"),  # name too long
        *with_broken_store("render", "master.md", "out/{{name}}.md"),
    ],
    "fill": [
        scenario("fill"),
        *[scenario("fill", f) for f in FILES],
        scenario("fill", *FILES),
        scenario("fill", "{{name}}.md"),
        *with_broken_store("fill", "master.md", "no-such-item.md"),
    ],
    # The window is drawn off-screen into a picture and closed at once, never shown.
    "manage": [
        scenario("manage", "{{name}}"),
        scenario("manage", env=SHOT),
        scenario("manage", env=dict(SHOT, PRIVFILL_MANAGER_SCRIPT='[{"select": "name"}, {"reveal": true}]')),
        *(scenario("manage", before=b, env=SHOT) for b in STORE_BREAKS),
        scenario("manage", env=dict(SHOT, **WRONG_PASSPHRASE)),
    ],
    # Standard input is empty, so the server ends at once; tests/test_mcp_server.py drives the tools.
    "mcp": [
        scenario("mcp"),
        scenario("mcp", "{{name}}"),
        *with_broken_store("mcp"),
    ],
    "guard": [
        scenario("guard"),
        scenario("guard", "{{name}}"),
        scenario("guard", before="outputs_named_by_values"),
        scenario("guard", before="registry_is_garbage"),
        *with_broken_store("guard"),
    ],
    "restore": [
        scenario("restore"),
        scenario("restore", "{{name}}"),
        scenario("restore", "1"),
        scenario("restore", "999"),
        scenario("restore", "1", "2"),
        *with_broken_store("restore"),
        *with_broken_store("restore", "1"),
    ],
    "verify": [
        scenario("verify"),
        scenario("verify", "{{name}}"),
        *(scenario("verify", before=b) for b in LOG_BREAKS),
        *with_broken_store("verify"),
    ],
    "pack": [
        scenario("pack"),
        scenario("pack", "out/a.zip"),
        scenario("pack", "out/{{name}}.zip", "master.md"),
        *[scenario("pack", "out/{{name}}_{{phone}}.zip", f) for f in FILES],
        scenario("pack", "out/a.zip", "master.md", "locked/master.md"),
        scenario("pack", "out/a.zip", "master.md", "master.md"),
        scenario("pack", "master.md", "master.md"),
        scenario("pack", "out/{{shoe size}}.zip", "master.md"),
        scenario("pack", "out/a.zip", "{{name}}.md"),
        scenario("pack", "{{name}}/{{email}}/a.zip", "master.md"),
        scenario("pack", "locked/{{name}}.zip", "master.md"),
        scenario("pack", "out", "master.md"),
        *with_broken_store("pack", "out/{{name}}.zip", "master.md"),
    ],
}
NOT_COMMANDS = [scenario("frobnicate"), scenario("{{name}}"), scenario("--json"), scenario("--json", "--json"),
                scenario("run", "--", "echo", "{{name}}"), scenario("--bogus", "keys")]


class TestNoValueEscapes(unittest.TestCase):
    def check(self, sc):
        for prefix in ([], ["--json"]):
            world = World(self)
            try:
                if sc["before"]:
                    getattr(world, sc["before"])()
                rc, out, err = world.run_tool(prefix + sc["args"], answers=sc["answers"], env=sc["env"])
            finally:
                world.close()
            where = f"{' '.join(prefix + sc['args'])!r} before={sc['before']} exit={rc}"
            for name, text in (("standard output", out), ("standard error", err)):
                leaked = [s for s in SECRETS if s in text]
                # Say which stream leaked, never what: a failing test's report is output too.
                self.assertFalse(leaked, f"{len(leaked)} value fragment(s) in {name} of {where}")
            self.assertNotIn("Traceback", err, f"an exception escaped from {where}")
            self.assertIn(rc, (0, 1, 2, 3, 4, 5), where)
            # guard answers through its exit code alone and mcp speaks its own protocol: both ignore --json
            if prefix and sc["args"][:1] not in (["--json"], ["guard"], ["mcp"]):
                try:
                    doc = json.loads(out)
                except ValueError:
                    self.fail(f"--json did not print one JSON object for {where}")
                self.assertEqual((doc["code"], doc["ok"]), (rc, rc == 0), where)

    def test_every_command_is_listed(self):
        self.assertEqual(sorted(SCENARIOS), sorted(COMMANDS),
                         "every command needs hostile scenarios in SCENARIOS, and only commands belong there")
        for name, scenarios in SCENARIOS.items():
            self.assertTrue(scenarios, name)

    def test_a_command_that_is_not_listed_cannot_run(self):
        world = World(self)
        try:
            for name in COMMANDS:
                rc, out, err = world.run_tool([name + "x"])
                self.assertEqual((rc, "Unknown subcommand" in out), (1, True), name)
        finally:
            world.close()

    def test_the_search_would_notice_a_leak(self):
        """The suite is only worth something if the values really are where a leak would find them."""
        world = World(self)
        try:
            self.assertTrue(all(len(s) == FRAGMENT for s in SECRETS))
            with open(world.data, encoding="utf8") as f:
                vault = f.read()
            self.assertFalse([s for s in SECRETS if s in vault], "the vault file holds a value in the clear")
            rc, out, err = world.run_tool(["render", "master.md", "out/{{name}}.md"])
            self.assertEqual(rc, 0, out)
            with open(world.path("out", STORED["name"] + ".md"), encoding="utf8") as f:
                output = f.read()
            self.assertTrue(any(s in output for s in SECRETS))
        finally:
            world.close()

    @unittest.skipUnless(importlib.util.find_spec("PyQt6"), "needs PyQt6 to open the real form")
    def test_the_real_form_closed_by_the_person(self):
        """The form itself, pre-filled with the stored values and then closed without saving."""
        self.check(scenario("form", json.dumps(SPEC), env={"PRIVFILL_DIALOG_TIMEOUT": "0.3"}))

    @unittest.skipUnless(importlib.util.find_spec("PyQt6"), "needs PyQt6 to open the real form")
    def test_interrupted(self):
        """Ctrl-C while the form is open: the program stops without a traceback."""
        world = World(self)
        try:
            env = dict(world.env, PRIVFILL_DIALOG_TIMEOUT="4")
            p = subprocess.Popen([sys.executable, TOOL, "form", json.dumps(SPEC)], env=env, cwd=world.dir,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(1.5)
            p.send_signal(signal.SIGINT)
            out, err = (b.decode("utf8", "replace") for b in p.communicate(timeout=60))
        finally:
            world.close()
        self.assertNotIn("Traceback", err)
        self.assertFalse([s for s in SECRETS if s in out + err])

    def test_unknown_commands(self):
        for sc in NOT_COMMANDS:
            with self.subTest(args=sc["args"]):
                self.check(sc)


def _add(command, i, sc):
    def test(self):
        self.check(sc)
    test.__name__ = f"test_{command}_{i:02d}"
    setattr(TestNoValueEscapes, test.__name__, test)


for _command, _scenarios in SCENARIOS.items():
    for _i, _sc in enumerate(_scenarios, 1):
        _add(_command, _i, _sc)


@unittest.skipUnless(os.name == "posix", "the process is hardened on POSIX systems only")
class TestProcessHardening(unittest.TestCase):
    """Started through the real launcher, the process cannot dump core or be inspected."""

    PROBE = """
import ctypes, json, resource, runpy, sys
sys.argv = [sys.argv[1], "version"]
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
except SystemExit:
    pass
dumpable = ctypes.CDLL(None).prctl(3, 0, 0, 0, 0) if sys.platform.startswith("linux") else None
print(json.dumps({"core": list(resource.getrlimit(resource.RLIMIT_CORE)), "dumpable": dumpable}))
"""

    def test_no_core_dumps_and_not_dumpable(self):
        p = subprocess.run([sys.executable, "-c", self.PROBE, TOOL], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        state = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(state["core"], [0, 0])
        if sys.platform.startswith("linux"):
            self.assertEqual(state["dumpable"], 0)


if __name__ == "__main__":
    unittest.main()

"""The output registry and the guard command, through the command line.

Fake data only. Every test runs the program as a hook would: a JSON payload on standard input,
the decision in the exit code. The payloads are replayed from tests/hook_payloads.json.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_privfill import TOOL, Case, read_bytes  # noqa: E402

ALLOW, DENY = 0, 2
SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook_payloads.json")


class GuardCase(Case):
    """A person with stored items, a master, and a directory for outputs."""

    def setUp(self):
        super().setUp()
        self.seed()
        self.store_dir = os.path.dirname(self.data)
        self.registry = os.path.join(self.store_dir, "outputs.json")
        os.mkdir(self.path("out"))
        self.write("master.txt", "Name: {{name}}, born {{birthday}}.\n")

    def render(self, output="out/letter.txt"):
        rc, out = self.tool("render", "master.txt", output)
        self.assertEqual(rc, 0, out)

    def registered(self):
        with open(self.registry, encoding="utf8") as f:
            return json.load(f)["outputs"]


class TestRegistry(GuardCase):
    def test_render_records_the_output_path_and_content_hash_in_the_data_directory(self):
        self.render()
        output = os.path.realpath(self.path("out/letter.txt"))
        self.assertEqual(self.registered(), [{
            "path": output,
            "sha256": hashlib.sha256(read_bytes(output)).hexdigest(),
            "size": os.path.getsize(output),
        }])
        self.assertEqual(os.stat(self.registry).st_mode & 0o777, 0o600)

    def test_registry_holds_paths_and_hashes_but_no_value(self):
        self.render()
        with open(self.registry, encoding="utf8") as f:
            text = f.read()
        self.assert_no_values(text)
        self.assertEqual({k for o in self.registered() for k in o}, {"path", "sha256", "size"})

    def test_files_filled_in_place_and_archives_are_outputs_too(self):
        self.write("copy.txt", "{{name}}")
        self.write("plain.txt", "no placeholders here")
        rc, out = self.tool("fill", "copy.txt", "plain.txt")
        self.assertEqual(rc, 0, out)
        rc, out = self.tool("pack", "out/bundle.zip", "copy.txt")
        self.assertEqual(rc, 0, out)
        paths = [o["path"] for o in self.registered()]
        self.assertEqual(paths, [os.path.realpath(self.path(n)) for n in ("copy.txt", "out/bundle.zip")])

    def test_an_output_that_cannot_be_registered_is_not_kept(self):
        self.render()
        with open(self.registry, "w", encoding="utf8") as f:
            f.write("not a registry")
        rc, out = self.tool("render", "master.txt", "out/second.txt")
        self.assertEqual(rc, 1, out)
        self.assertIn("nothing was written", out)
        self.assertFalse(os.path.exists(self.path("out/second.txt")))
        rc, out = self.tool("pack", "out/bundle.zip", "master.txt")
        self.assertEqual(rc, 1, out)
        self.assertFalse(os.path.exists(self.path("out/bundle.zip")))
        self.write("copy.txt", "{{name}}")
        rc, out = self.tool("--json", "fill", "copy.txt")
        self.assertEqual(rc, 1, out)
        self.assertIn("not registered", json.loads(out)["message"])
        self.assertFalse(json.loads(out)["files"][0]["ok"])

    def test_rendering_again_keeps_the_hash_of_the_earlier_content(self):
        self.render()
        self.write("master.txt", "Phone: {{phone}}\n")
        self.render()
        self.assertEqual(len({o["sha256"] for o in self.registered()}), 2)
        self.render()
        self.assertEqual(len(self.registered()), 2)


class TestGuard(GuardCase):
    """Replays tests/hook_payloads.json against a small project: a master, a directory of sources,
    one output in out/, a moved copy of it, a link to it, and the data directory in store/."""

    def setUp(self):
        super().setUp()
        self.render()
        for d in ("src", "moved", "tools"):
            os.mkdir(self.path(d))
        shutil.copy(self.path("out/letter.txt"), self.path("moved/renamed.txt"))
        os.symlink(self.path("out/letter.txt"), self.path("link.txt"))
        self.write("src/notes.txt", "TODO: nothing personal here\n")
        self.write("src/same_size.txt", "x" * os.path.getsize(self.path("out/letter.txt")))
        shutil.copy(TOOL, self.path("tools/privfill.py"))
        self.names = {"$WORK": self.dir, "$DATA": self.store_dir, "$PROGRAM": TOOL, "$COPY": self.path("tools/privfill.py")}

    def guard(self, text):
        """Run the guard as a hook does. HOME is the project, so ~/store is the data directory."""
        p = subprocess.run([sys.executable, TOOL, "guard"], input=text, env=dict(self.env, HOME=self.dir),
                           cwd=self.dir, capture_output=True, text=True)
        return p.returncode, p.stdout, p.stderr

    def fill_in(self, text):
        for name, path in self.names.items():
            text = text.replace(name, path)
        return text

    def test_recorded_payloads_get_the_recorded_decision(self):
        with open(SAMPLES, encoding="utf8") as f:
            samples = json.load(f)
        self.assertGreater(len(samples), 80)
        for sample in samples:
            text = sample["raw"] if "raw" in sample else json.dumps(sample["payload"], ensure_ascii=False)
            with self.subTest(sample["name"]):
                rc, out, err = self.guard(self.fill_in(text))
                self.assertEqual(rc, {"allow": ALLOW, "deny": DENY}[sample["expect"]], err)
                self.assertEqual(out, "")
                if rc == ALLOW:
                    self.assertEqual(err, "")
                else:
                    self.assertRegex(err, r"\Aprivfill guard: denied; [a-z ]+\.\n\Z")

    def test_a_deny_reason_never_echoes_a_path_or_a_value(self):
        for command in ("cat out/letter.txt", "cat ~/store/vault.json", "ls out", "grep -r Jane ."):
            rc, out, err = self.guard(json.dumps({"cwd": self.dir, "tool_name": "Bash", "tool_input": {"command": command}}))
            self.assertEqual(rc, DENY)
            self.assert_no_values(out + err)
            for part in (self.dir, "letter", "out/", "store", "vault.json", "Jane"):
                self.assertNotIn(part, out + err)

    def test_nothing_is_guarded_as_an_output_before_it_is_registered(self):
        os.unlink(self.registry)
        payload = {"cwd": self.dir, "tool_name": "Read", "tool_input": {"file_path": self.path("out/letter.txt")}}
        self.assertEqual(self.guard(json.dumps(payload))[0], ALLOW)
        payload["tool_input"]["file_path"] = self.data
        self.assertEqual(self.guard(json.dumps(payload))[0], DENY)

    def test_a_damaged_registry_denies_every_call(self):
        with open(self.registry, "w", encoding="utf8") as f:
            f.write("{}")
        payload = {"cwd": self.dir, "tool_name": "Read", "tool_input": {"file_path": self.path("master.txt")}}
        rc, out, err = self.guard(json.dumps(payload))
        self.assertEqual((rc, out), (DENY, ""))
        self.assertIn("registry", err)

    def test_the_guard_needs_no_passphrase(self):
        env = {k: v for k, v in self.env.items() if k != "PRIVFILL_PASSPHRASE_TEST"}
        payload = {"cwd": self.dir, "tool_name": "Read", "tool_input": {"file_path": self.path("master.txt")}}
        p = subprocess.run([sys.executable, TOOL, "guard"], input=json.dumps(payload), env=env, cwd=self.dir,
                           capture_output=True, text=True, timeout=20)
        self.assertEqual((p.returncode, p.stdout + p.stderr), (ALLOW, ""))


if __name__ == "__main__":
    unittest.main()

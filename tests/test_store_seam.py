"""The Store seam: the commands driven in-process by a test double, and the file backend's contract.

Fake data only. The double keeps items in memory, so these tests also show that no command
reaches the data file by any other route: PRIVFILL_DATA points at a path that must never appear.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from privfill.cli import main  # noqa: E402
from privfill.store import FileStore  # noqa: E402


class MemoryStore:
    """A Store that is not a file. It deliberately does not inherit from anything in privfill."""

    label = "memory"

    def __init__(self, items=None):
        self.items = dict(items or {})
        self.created = 0

    def create(self):
        self.created += 1
        return self.created == 1

    def names(self):
        return list(self.items)

    def read(self, name):
        return self.items.get(name)

    def write(self, name, value):
        replaced = name in self.items
        self.items[name] = value
        return replaced

    def delete(self, name):
        return self.items.pop(name, None) is not None


class TestCommandsThroughDouble(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.data = os.path.join(self.dir, "never", "profile.md")
        self.store = MemoryStore({"name": "Jane Roe", "birthday": "2005-03-04"})

    def tearDown(self):
        self.assertFalse(os.path.exists(os.path.dirname(self.data)), "a command went around the Store")
        self.tmp.cleanup()

    def run_main(self, *args, answers=None):
        env = {"PRIVFILL_DATA": self.data, "PRIVFILL_LANG": "en"}
        if answers is not None:
            env["PRIVFILL_FORM_TEST"] = json.dumps(answers)
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(buf):
            rc = main(["privfill.py", *args], store=self.store)
        return rc, buf.getvalue()

    def path(self, name):
        return os.path.join(self.dir, name)

    def write(self, name, text):
        with open(self.path(name), "w", encoding="utf8") as f:
            f.write(text)

    def read(self, name):
        with open(self.path(name), encoding="utf8") as f:
            return f.read()

    def test_keys_lists_the_doubles_names(self):
        rc, out = self.run_main("--json", "keys")
        self.assertEqual((rc, json.loads(out)["items"]), (0, ["name", "birthday"]))

    def test_init_asks_the_store_to_create(self):
        rc, out = self.run_main("--json", "init")
        self.assertTrue(json.loads(out)["created"])
        rc, out = self.run_main("--json", "init")
        self.assertFalse(json.loads(out)["created"])
        self.assertEqual(self.store.created, 2)

    def test_form_writes_through_the_store(self):
        spec = json.dumps({"fields": [{"name": "name"}, {"name": "birthday", "type": "date"},
                                      {"name": "phone", "type": "phone"}, {"name": "email", "type": "email"}]})
        rc, out = self.run_main("--json", "form", spec,
                                answers={"name": "Jane Roe", "birthday": "2006/1/2", "phone": "138 0013 8000"})
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.store.items, {"name": "Jane Roe", "birthday": "2006-01-02", "phone": "13800138000"})
        items = {i["name"]: (i["status"], i["stored"]) for i in json.loads(out)["items"]}
        self.assertEqual(items, {"name": ("unchanged", True), "birthday": ("updated", True),
                                 "phone": ("created", True), "email": ("skipped", False)})
        self.assertNotIn("13800138000", out)

    def test_add_writes_one_item(self):
        rc, out = self.run_main("add", "nickname", answers={"nickname": "JR"})
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.store.items["nickname"], "JR")

    def test_render_and_fill_read_through_the_store(self):
        self.write("m.md", "{{name}} / {{birthday|年月日}}")
        rc, out = self.run_main("--json", "render", self.path("m.md"), self.path("o_{{name}}.md"))
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(out)["used"], {"name": 1, "birthday": 1})
        self.assertEqual(self.read("o_Jane Roe.md"), "Jane Roe / 2005年3月4日")
        self.assertNotIn("Jane Roe", out)
        rc, out = self.run_main("fill", self.path("m.md"))
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read("m.md"), "Jane Roe / 2005年3月4日")

    def test_missing_item_lists_the_doubles_names(self):
        self.write("m.md", "{{passport}}")
        rc, out = self.run_main("fill", self.path("m.md"))
        self.assertEqual(rc, 2, out)
        self.assertIn("Available items: name, birthday.", out)
        rc, out = self.run_main("render", self.path("m.md"), self.path("o.md"))
        self.assertEqual(rc, 2, out)
        self.assertFalse(os.path.exists(self.path("o.md")))

    def test_pack_names_the_archive_from_the_store(self):
        self.write("a.txt", "alpha")
        self.write("b.txt", "beta")
        rc, out = self.run_main("--json", "pack", self.path("hw_{{name}}.zip"), self.path("a.txt"), self.path("b.txt"))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("Jane Roe", out)
        res = json.loads(out)
        self.assertEqual((res["packed"], res["used"]), (2, {"name": 1}))
        with zipfile.ZipFile(self.path("hw_Jane Roe.zip")) as z:
            self.assertEqual({n: z.read(n) for n in z.namelist()}, {"a.txt": b"alpha", "b.txt": b"beta"})

    def test_json_extras_do_not_leak_between_calls(self):
        self.run_main("--json", "keys")
        rc, out = self.run_main("--json", "init")
        self.assertNotIn("items", json.loads(out))


class TestFileStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "store", "profile.md")
        self.store = FileStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_store_reads_nothing_and_creates_no_file(self):
        self.assertEqual(self.store.names(), [])
        self.assertIsNone(self.store.read("phone"))
        self.assertFalse(self.store.delete("phone"))
        self.assertFalse(os.path.exists(self.path))

    def test_create_is_private_and_idempotent(self):
        self.assertTrue(self.store.create())
        self.assertFalse(self.store.create())
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.dirname(self.path)).st_mode & 0o777, 0o700)

    def test_write_read_names(self):
        self.assertFalse(self.store.write("phone", "13800138000"))
        self.assertFalse(self.store.write("city", "Example Town"))
        self.assertTrue(self.store.write("phone", "13900139000"))
        self.assertEqual(self.store.names(), ["phone", "city"])
        self.assertEqual(self.store.read("phone"), "13900139000")
        self.assertEqual(self.store.read("city"), "Example Town")
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(self.store.label, "profile.md")

    def test_delete_removes_one_item_only(self):
        self.store.write("phone", "13800138000")
        self.store.write("city", "Example Town")
        self.assertTrue(self.store.delete("phone"))
        self.assertFalse(self.store.delete("phone"))
        self.assertEqual(self.store.names(), ["city"])
        self.assertIsNone(self.store.read("phone"))
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.store.write("phone", "13900139000")
        self.assertEqual(self.store.names(), ["city", "phone"])


if __name__ == "__main__":
    unittest.main()

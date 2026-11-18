"""The activity log: the ActivityLog module, and the commands that feed it. Fake data only."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hashlib  # noqa: E402

from privfill.activity import ActivityLog  # noqa: E402
from privfill.cli import main  # noqa: E402
from privfill.store import FileStore  # noqa: E402


class TestActivityLogModule(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "activity.log")
        self.log = ActivityLog(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def fill(self, n):
        for i in range(n):
            self.log.append("render", items={"phone": 1, "name": i + 1}, master="ab" * 32)

    def lines(self):
        with open(self.path, encoding="utf8") as f:
            return f.read().splitlines()

    def put(self, lines):
        with open(self.path, "w", encoding="utf8") as f:
            f.write("\n".join(lines) + "\n")

    def test_missing_log_verifies_as_empty(self):
        ok, count, _ = self.log.verify()
        self.assertEqual((ok, count), (True, 0))

    def test_untouched_chain_verifies(self):
        self.fill(4)
        ok, count, _ = self.log.verify()
        self.assertEqual((ok, count), (True, 4))

    def test_record_holds_names_counts_hash_and_time_only(self):
        self.log.append("render", items={"phone": 2}, master="cd" * 32)
        rec = json.loads(self.lines()[0])
        self.assertEqual(rec["command"], "render")
        self.assertEqual(rec["items"], {"phone": 2})
        self.assertEqual(rec["master"], "cd" * 32)
        self.assertRegex(rec["time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_edited_record_is_detected(self):
        self.fill(3)
        ls = self.lines()
        ls[1] = ls[1].replace('"phone": 1', '"phone": 9')
        self.put(ls)
        self.assertFalse(self.log.verify()[0])

    def test_deleted_record_is_detected(self):
        self.fill(4)
        ls = self.lines()
        del ls[1]
        self.put(ls)
        self.assertFalse(self.log.verify()[0])

    def test_reordered_records_are_detected(self):
        self.fill(4)
        ls = self.lines()
        ls[1], ls[2] = ls[2], ls[1]
        self.put(ls)
        self.assertFalse(self.log.verify()[0])

    def test_deleting_the_first_record_is_detected_when_nothing_was_rotated(self):
        self.fill(3)
        self.put(self.lines()[1:])
        self.assertFalse(self.log.verify()[0])

    def test_garbage_line_is_detected(self):
        self.fill(2)
        self.put(self.lines() + ["not json"])
        self.assertFalse(self.log.verify()[0])

    def test_log_file_is_private(self):
        self.fill(1)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_rotation_keeps_the_chain_and_bounds_growth(self):
        log = ActivityLog(self.path, max_bytes=600, keep=3)
        for i in range(40):
            log.append("render", items={"phone": i + 1}, master="ab" * 32)
        files = sorted(f for f in os.listdir(self.tmp.name) if not f.endswith(".lock"))
        self.assertEqual(files, ["activity.log", "activity.log.1", "activity.log.2"])
        ok, count, _ = log.verify()
        self.assertTrue(ok)
        self.assertGreater(count, 3)
        self.assertLess(count, 40)  # the oldest records were dropped by rotation
        for f in files:
            self.assertLess(os.path.getsize(os.path.join(self.tmp.name, f)), 600 + 400)

    def test_tampering_inside_a_rotated_file_is_detected(self):
        log = ActivityLog(self.path, max_bytes=600, keep=3)
        for i in range(20):
            log.append("render", items={"phone": i + 1}, master="ab" * 32)
        rotated = self.path + ".1"
        with open(rotated, encoding="utf8") as f:
            ls = f.read().splitlines()
        del ls[1]
        with open(rotated, "w", encoding="utf8") as f:
            f.write("\n".join(ls) + "\n")
        self.assertFalse(log.verify()[0])

    def test_a_missing_middle_file_is_detected(self):
        log = ActivityLog(self.path, max_bytes=600, keep=4)
        for i in range(30):
            log.append("render", items={"phone": i + 1}, master="ab" * 32)
        os.unlink(self.path + ".2")
        self.assertFalse(log.verify()[0])


NAME, PHONE = "Jane Roe", "13800138000"
MASTER_TEXT = "Name: {{name}} / {{name}}, phone {{phone|last4}}\n"


class TestCommandsFeedTheLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.store_dir = os.path.join(self.dir, "store")
        self.store = FileStore(os.path.join(self.store_dir, "profile.md"))
        self.store.write("name", NAME)
        self.store.write("phone", PHONE)
        self.log_path = os.path.join(self.store_dir, "activity.log")

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, *args, answers=None):
        # The form's test hook only runs against a fake vault path; the items themselves are in self.store.
        env = {"PRIVFILL_LANG": "en", "PRIVFILL_VAULT": os.path.join(self.store_dir, "vault.json")}
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

    def records(self):
        if not os.path.exists(self.log_path):
            return []
        with open(self.log_path, encoding="utf8") as f:
            return [json.loads(line) for line in f.read().splitlines()]

    def test_render_records_item_names_counts_and_master_hash(self):
        self.write("master.txt", MASTER_TEXT)
        rc, _ = self.run_main("render", self.path("master.txt"), self.path("out.txt"))
        self.assertEqual(rc, 0)
        (rec,) = self.records()
        self.assertEqual(rec["command"], "render")
        self.assertEqual(rec["items"], {"name": 2, "phone": 1})
        self.assertEqual(rec["master"], hashlib.sha256(MASTER_TEXT.encode()).hexdigest())

    def test_failed_render_appends_nothing(self):
        self.write("master.txt", "{{no such item}}\n")
        rc, _ = self.run_main("render", self.path("master.txt"), self.path("out.txt"))
        self.assertEqual(rc, 2)
        self.assertEqual(self.records(), [])

    def test_failed_pack_appends_nothing(self):
        rc, _ = self.run_main("pack", self.path("a.zip"), self.path("missing.txt"))
        self.assertEqual(rc, 1)
        self.assertEqual(self.records(), [])

    def test_pack_records_items_used_in_names(self):
        self.write("{}.txt".format("Jane Roe"), "x")
        rc, _ = self.run_main("pack", self.path("{{name}}.zip"), self.path("Jane Roe.txt"))
        self.assertEqual(rc, 0)
        (rec,) = self.records()
        self.assertEqual((rec["command"], rec["items"]), ("pack", {"name": 1}))

    def test_form_records_statuses_and_fill_records_each_filled_file(self):
        spec = json.dumps({"fields": [{"name": "email", "type": "email"}, {"name": "name"}]})
        rc, _ = self.run_main("form", spec, answers={"email": "jane@example.com", "name": NAME})
        self.assertEqual(rc, 0)
        self.write("a.txt", "{{email}}")
        self.write("b.txt", "{{nope}}")
        self.run_main("fill", self.path("a.txt"), self.path("b.txt"))
        form, fill = self.records()
        self.assertEqual(form["items"], {"email": "created", "name": "unchanged"})
        self.assertEqual((fill["command"], fill["items"]), ("fill", {"email": 1}))
        self.assertEqual(len(self.records()), 2)

    def test_read_only_commands_append_nothing(self):
        self.run_main("keys")
        self.run_main("init")
        self.run_main("protocol")
        self.assertEqual(self.records(), [])

    def test_log_holds_no_value_and_no_path(self):
        self.write("master.txt", MASTER_TEXT)
        self.run_main("render", self.path("master.txt"), self.path("{{name}}.txt"))
        self.write("Jane Roe.txt", "x")
        self.run_main("pack", self.path("bundle.zip"), self.path("Jane Roe.txt"))
        self.run_main("form", json.dumps({"fields": [{"name": "email", "type": "email"}]}),
                      answers={"email": "jane@example.com"})
        with open(self.log_path, encoding="utf8") as f:
            raw = f.read()
        for secret in (NAME, PHONE, "13800138000", "8000", "jane@example.com", "Roe", self.dir, "master.txt", "bundle.zip"):
            self.assertNotIn(secret, raw)
        self.assertEqual(len(self.records()), 3)

    def test_verify_command_reports_intact_and_tampered(self):
        self.write("master.txt", MASTER_TEXT)
        for n in range(3):
            self.run_main("render", self.path("master.txt"), self.path(f"out{n}.txt"))
        rc, out = self.run_main("verify")
        self.assertEqual(rc, 0, out)
        self.assertIn("3 record", out)
        rc, out = self.run_main("--json", "verify")
        self.assertEqual((json.loads(out)["ok"], json.loads(out)["records"]), (True, 3))
        with open(self.log_path, encoding="utf8") as f:
            ls = f.read().splitlines()
        ls[1], ls[2] = ls[2], ls[1]
        with open(self.log_path, "w", encoding="utf8") as f:
            f.write("\n".join(ls) + "\n")
        rc, out = self.run_main("verify")
        self.assertEqual(rc, 1)
        self.assertIn("changed", out)
        self.assertNotIn(self.dir, out)

    def test_verify_does_not_append(self):
        self.run_main("verify")
        self.assertEqual(self.records(), [])

    def test_store_without_a_directory_logs_nothing(self):
        class Bare:
            label = "bare"

            def create(self):
                return True

            def names(self):
                return []

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(main(["privfill.py", "init"], store=Bare()), 0)


if __name__ == "__main__":
    unittest.main()

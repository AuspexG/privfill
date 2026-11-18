"""The leak audit (issue #13). Fake data only, planted in temporary files."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from privfill.audit import audit  # noqa: E402
from privfill.vault import PassphraseUnlock, VaultStore  # noqa: E402

TOOL = os.path.join(ROOT, "privfill.py")
PASSPHRASE = "audit tests 口令"
SAMPLE_ID = "11010519491231002X"  # the specimen number used in published examples; belongs to no one
ITEMS = [
    ("name", "text", "Jane Roe"),
    ("birthday", "date", "2005-03-04"),
    ("phone", "phone", "13800138000"),
    ("id number", "id_card", SAMPLE_ID),
    ("email", "email", "jane@example.com"),
    ("height", "number", "1750"),
    ("pet", "text", "Bo"),  # too short to search by default
]
# How each item turns up in the wild.
VARIANTS = {
    "name": ["JANE  roe", "Ｊａｎｅ Ｒｏｅ"],
    "birthday": ["2005/3/4", "2005年3月4日", "4.3.2005", "03/04/2005", "20050304", "March 4, 2005", "4 Mar 2005"],
    "phone": ["+86 138-0013-8000", "138 0013 8000", "0086 13800138000", "１３８００１３８０００"],
    "id number": ["1101 0519 4912 3100 2x", "110105194912310 02X"],
    "email": ["JANE@EXAMPLE.COM", "ｊａｎｅ＠ｅｘａｍｐｌｅ．ｃｏｍ"],
    "height": ["1,750"],
}
NOT_A_DATE = "Order 120050304 and 2005-03-14 and 2005/3/41"


def docx(path, paragraphs):
    """A minimal Word file whose paragraphs are split into runs mid-value."""
    body = "".join("<w:p>" + "".join(f"<w:r><w:t>{p[i:i + 3]}</w:t></w:r>" for i in range(0, len(p), 3)) + "</w:p>"
                   for p in paragraphs)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f"<w:document><w:body>{body}</w:body></w:document>")


def xlsx(path, strings):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/sharedStrings.xml", "<sst>" + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")


def pptx(path, strings):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("ppt/slides/slide1.xml", "<p:sld>" + "".join(f"<a:p><a:r><a:t>{s}</a:t></a:r></a:p>" for s in strings) + "</p:sld>")


class AuditCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "scan")
        os.makedirs(self.dir)
        self.vault = os.path.join(self.tmp.name, "store", "vault.json")
        self.store = VaultStore(self.vault, PassphraseUnlock(lambda kind: PASSPHRASE, cost=2 ** 10))
        for n, t, v in ITEMS:
            self.store.write(n, v, t)

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, name):
        return os.path.join(self.dir, name)

    def write(self, name, text):
        with open(self.path(name), "w", encoding="utf8") as f:
            f.write(text)
        return self.path(name)


class TestVariants(AuditCase):
    def test_every_variant_is_found_in_text_files(self):
        for item, forms in VARIANTS.items():
            for k, form in enumerate(forms):
                with self.subTest(item=item, form=k):
                    p = self.write(f"t{k}.txt", f"Lorem ipsum {form} dolor.\n")
                    r = audit([p], self.store)
                    self.assertEqual(r["totals"], {item: 1})
                    os.unlink(p)

    def test_variants_are_found_in_word_excel_and_powerpoint(self):
        everything = [f for forms in VARIANTS.values() for f in forms]
        docx(self.path("a.docx"), everything)
        xlsx(self.path("b.xlsx"), everything)
        pptx(self.path("c.pptx"), everything)
        r = audit([self.dir], self.store)
        want = {item: 3 * len(forms) for item, forms in VARIANTS.items()}
        self.assertEqual(r["totals"], want)
        self.assertEqual(len(r["locations"]), 3)

    def test_near_misses_are_not_found(self):
        p = self.write("n.txt", NOT_A_DATE + " Janet Roebuck 1380013800 jane@example.org")
        self.assertEqual(audit([p], self.store)["totals"], {})

    def test_a_value_in_a_file_name_counts(self):
        p = self.write("letter_Jane Roe.txt", "nothing here")
        r = audit([p], self.store)
        self.assertEqual(r["totals"], {"name": 1})

    def test_short_values_are_skipped_unless_the_threshold_is_lowered(self):
        p = self.write("s.txt", "Bo was here")
        r = audit([p], self.store)
        self.assertEqual((r["totals"], r["short"]), ({}, ["pet"]))
        self.assertEqual(audit([p], self.store, min_length=2)["totals"], {"pet": 1})

    def test_binary_files_are_skipped(self):
        with open(self.path("b.bin"), "wb") as f:
            f.write(b"\x00\x01Jane Roe\x00" * 10)
        r = audit([self.dir], self.store)
        self.assertEqual((r["totals"], r["skipped"]), ({}, 1))

    def test_a_large_tree_is_scanned_in_reasonable_time(self):
        for d in range(20):
            os.makedirs(self.path(f"d{d}"))
            for f in range(100):
                self.write(f"d{d}/f{f}.txt", "Some ordinary text without anything personal in it.\n" * 20)
        self.write("d7/f7.txt", "call 138 0013 8000")
        start = time.monotonic()
        r = audit([self.dir], self.store)
        self.assertLess(time.monotonic() - start, 20)
        self.assertEqual((r["scanned"], r["totals"]), (2000, {"phone": 1}))


class TestCommand(AuditCase):
    def tool(self, *args, **env):
        e = {k: v for k, v in os.environ.items() if not k.startswith("PRIVFILL_")}
        e.update(PRIVFILL_VAULT=self.vault, PRIVFILL_LANG="en", PRIVFILL_PASSPHRASE_TEST=PASSPHRASE,
                 PRIVFILL_KEYRING_TEST="none", QT_QPA_PLATFORM="offscreen", **env)
        p = subprocess.run([sys.executable, TOOL, *args], env=e, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=120)
        out = p.stdout + p.stderr
        for _, _, v in ITEMS[:-1]:
            self.assertNotIn(v, out)
        for forms in VARIANTS.values():
            for f in forms:
                self.assertNotIn(f, out)
        self.assertNotIn(self.dir, out)
        return p.returncode, p.stdout

    def test_reports_names_counts_and_indexes_only_with_exit_code_6(self):
        self.write("Jane Roe.txt", "+86 138-0013-8000")
        self.write("other.txt", "nothing")
        rc, out = self.tool("--json", "audit", self.dir)
        self.assertEqual(rc, 6, out)
        doc = json.loads(out)
        self.assertEqual(doc["found"], {"name": 1, "phone": 1})
        self.assertEqual(doc["locations"], [{"index": 1, "items": {"name": 1, "phone": 1}}])

    def test_nothing_found_is_exit_code_0(self):
        self.write("other.txt", "nothing")
        rc, out = self.tool("--json", "audit", "--min-length", "3", self.dir)
        self.assertEqual((rc, json.loads(out)["found"]), (0, {}))

    def test_bad_arguments(self):
        for args in ([], ["--min-length", "x", self.dir], ["--bogus", self.dir], [self.path("absent")]):
            rc, _ = self.tool("audit", *args)
            self.assertEqual(rc, 1, args)

    def test_the_window_lists_full_paths_off_screen(self):
        try:
            import PyQt6.QtWidgets  # noqa: F401
        except ImportError:
            self.skipTest("PyQt6 is not installed")
        self.write("Jane Roe.txt", "x")
        shot = os.path.join(self.tmp.name, "audit.png")
        rc, out = self.tool("audit", "--window", self.dir, PRIVFILL_AUDIT_SHOT=shot)
        self.assertEqual(rc, 6, out)
        self.assertGreater(os.path.getsize(shot), 1000)


if __name__ == "__main__":
    unittest.main()

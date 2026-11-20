"""PDF forms and other binary files. Fake data only; skipped where pypdf is not installed
(on Arch: pacman -S python-pypdf).

Run:  python3 -m unittest tests.test_pdf
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_privfill import Case, read_bytes  # noqa: E402

try:
    import pypdf
    from pypdf.generic import (ArrayObject, BooleanObject, DictionaryObject, FloatObject, NameObject,
                               NumberObject, StreamObject, TextStringObject)
except ImportError:  # pragma: no cover - depends on the machine
    pypdf = None

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe"  # not UTF-8


def make_form(path, fields, encrypt=False):
    """A one-page PDF with a text field per (name, value, default); every widget has an appearance."""
    w = pypdf.PdfWriter()
    page = w.add_blank_page(300, 300)
    refs = ArrayObject()
    for i, (name, value, default) in enumerate(fields):
        ap = StreamObject()
        ap.set_data(b"BT /Helv 10 Tf 2 5 Td (old) Tj ET")
        ap.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                   NameObject("/BBox"): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(100), FloatObject(20)])})
        widget = DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/FT"): NameObject("/Tx"), NameObject("/T"): TextStringObject(name),
            NameObject("/Rect"): ArrayObject([FloatObject(10), FloatObject(250 - 30 * i), FloatObject(110), FloatObject(270 - 30 * i)]),
            NameObject("/DA"): TextStringObject("/Helv 10 Tf 0 g"), NameObject("/F"): NumberObject(4),
            NameObject("/AP"): DictionaryObject({NameObject("/N"): w._add_object(ap)}),
        })
        if value is not None:
            widget[NameObject("/V")] = TextStringObject(value)
        if default is not None:
            widget[NameObject("/DV")] = TextStringObject(default)
        refs.append(w._add_object(widget))
    page[NameObject("/Annots")] = ArrayObject(refs)
    w._root_object[NameObject("/AcroForm")] = w._add_object(DictionaryObject({
        NameObject("/Fields"): refs, NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g"),
        NameObject("/NeedAppearances"): BooleanObject(False)}))
    if encrypt:
        w.encrypt("pw")
    with open(path, "wb") as f:
        w.write(f)
    return path


def fields_of(path):
    r = pypdf.PdfReader(path)
    form = r.trailer["/Root"]["/AcroForm"].get_object()
    return form, {str(f.get_object()["/T"]): f.get_object() for f in form["/Fields"]}


@unittest.skipIf(pypdf is None, "pypdf is not installed")
class TestPdfForm(Case):
    def test_render_fills_fields_and_names_the_copy(self):
        self.seed()
        master = make_form(self.path("m.pdf"), [("who", "{{name}}", "{{name}}"), ("where", "{{地址}}", None),
                                                ("kept", "an answer", None)])
        before = read_bytes(master)
        rc, out = self.tool("--json", "render", master, self.path("{{name}}_hw.pdf"))
        self.assertEqual(rc, 0, out)
        self.assert_no_values(out)
        self.assertEqual(json.loads(out)["used"], {"name": 2, "地址": 1})
        self.assertEqual(read_bytes(master), before)
        form, f = fields_of(self.path("Jane Roe_hw.pdf"))
        self.assertEqual((f["who"]["/V"], f["who"]["/DV"]), ("Jane Roe", "Jane Roe"))
        self.assertEqual(f["where"]["/V"], "1 Example Road｜Unit 2")  # survives as UTF-16
        self.assertNotIn("/AP", f["who"])  # the reader draws the value
        self.assertIn("/AP", f["kept"])  # a field without placeholders keeps its picture
        self.assertTrue(form["/NeedAppearances"])

    def test_a_missing_item_writes_nothing(self):
        self.seed()
        master = make_form(self.path("m.pdf"), [("x", "{{passport}}", None)])
        rc, out = self.tool("render", master, self.path("o.pdf"))
        self.assertEqual(rc, 2, out)
        self.assertFalse(os.path.exists(self.path("o.pdf")))

    def test_without_placeholders_the_copy_is_byte_for_byte(self):
        self.seed()
        master = make_form(self.path("m.pdf"), [("x", "plain", None)])
        rc, out = self.tool("render", master, self.path("{{name}}.pdf"))
        self.assertEqual(rc, 0, out)
        self.assertIn("no placeholders", out)
        self.assertEqual(read_bytes(self.path("Jane Roe.pdf")), read_bytes(master))

    def test_fill_in_place(self):
        self.seed()
        p = make_form(self.path("f.pdf"), [("x", "Dear {{name}}", None)])
        rc, out = self.tool("fill", p)
        self.assertEqual(rc, 0, out)
        self.assert_no_values(out)
        self.assertEqual(fields_of(p)[1]["x"]["/V"], "Dear Jane Roe")

    def test_an_encrypted_pdf_is_refused(self):
        self.seed()
        master = make_form(self.path("m.pdf"), [("x", "{{name}}", None)], encrypt=True)
        rc, out = self.tool("render", master, self.path("o.pdf"))
        self.assertEqual(rc, 1, out)
        self.assertIn("encrypted", out)
        self.assertFalse(os.path.exists(self.path("o.pdf")))

    def test_a_high_item_in_a_field_is_confirmed_first(self):
        self.seed()
        master = make_form(self.path("m.pdf"), [("id", "{{id number}}", None)])
        rc, out = self.tool("render", master, self.path("o.pdf"),
                            env=dict(self.env, PRIVFILL_CONFIRM_TEST="no", QT_QPA_PLATFORM="offscreen"))
        self.assertEqual(rc, 7, out)
        self.assertFalse(os.path.exists(self.path("o.pdf")))

    def test_audit_finds_a_value_in_a_filled_field(self):
        self.seed()
        master = make_form(self.path("m.pdf"), [("x", "{{phone}}", None)])
        os.mkdir(self.path("out"))
        self.assertEqual(self.tool("render", master, self.path("out/o.pdf"))[0], 0)
        rc, out = self.tool("audit", self.path("out"))
        self.assertEqual(rc, 6, out)
        self.assertIn("phone", out)
        self.assert_no_values(out)

    def test_without_pypdf_the_message_says_how_to_install_it(self):
        self.seed()
        master = make_form(self.path("m.pdf"), [("x", "{{name}}", None)])
        block = self.path("block")
        os.makedirs(os.path.join(block, "pypdf"))
        with open(os.path.join(block, "pypdf", "__init__.py"), "w") as f:
            f.write("raise ImportError('blocked for the test')\n")
        rc, out = self.tool("render", master, self.path("o.pdf"), env=dict(self.env, PYTHONPATH=block))
        self.assertEqual(rc, 1, out)
        self.assertIn("python-pypdf", out)
        self.assertFalse(os.path.exists(self.path("o.pdf")))


class TestBinaryFiles(Case):
    def test_a_binary_file_is_copied_under_its_new_name(self):
        self.seed()
        with open(self.path("pic.png"), "wb") as f:
            f.write(PNG)
        rc, out = self.tool("render", self.path("pic.png"), self.path("{{name}}.png"))
        self.assertEqual(rc, 0, out)
        self.assertEqual(read_bytes(self.path("Jane Roe.png")), PNG)

    def test_a_non_utf8_file_that_seems_to_hold_a_placeholder_is_refused(self):
        self.seed()
        with open(self.path("gbk.txt"), "wb") as f:
            f.write("姓名：{{name}}".encode("gbk"))
        rc, out = self.tool("render", self.path("gbk.txt"), self.path("o.txt"))
        self.assertEqual(rc, 1, out)
        self.assertIn("not UTF-8 text", out)
        self.assertFalse(os.path.exists(self.path("o.txt")))


if __name__ == "__main__":
    unittest.main()

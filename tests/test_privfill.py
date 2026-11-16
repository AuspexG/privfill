"""Tests for privfill. Everything runs on fake data in a temporary directory; no window is shown
and no real data file is touched.

Run:  python3 -m unittest discover -s tests
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "privfill.py")
SAMPLE_ID = "11010519491231002X"  # the specimen number used in published examples; belongs to no one
SPEC = {
    "protocol": 1,
    "title": "Test",
    "fields": [
        {"name": "name", "required": True},
        {"name": "birthday", "type": "date"},
        {"name": "phone", "type": "phone"},
        {"name": "id number", "type": "id_card"},
        {"name": "email", "type": "email"},
        {"name": "height", "type": "number"},
        {"name": "size", "type": "choice", "choices": ["S", "M", "L"]},
        {"name": "地址"},
    ],
}
ANSWERS = {
    "name": "Jane Roe",
    "birthday": "２００５年3月4日",
    "phone": "+86 138-0013 8000",
    "id number": "11010519491231002x",
    "email": " jane@example.com ",
    "height": "１７５",
    "size": "M",
    "地址": "1 Example  Road|Unit 2",
}
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
VALUES = ["Jane Roe", "2005-03-04", "13800138000", SAMPLE_ID, "jane@example.com", "Example Road"]


def read(path):
    with open(path, encoding="utf8") as f:
        return f.read()


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.data = os.path.join(self.dir, "store", "profile.md")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("PRIVFILL_")}
        self.env.update(PRIVFILL_DATA=self.data, PRIVFILL_LANG="en")

    def tearDown(self):
        self.tmp.cleanup()

    def tool(self, *args, answers=None, env=None, tool=TOOL):
        env = dict(self.env if env is None else env)
        if answers is not None:
            env["PRIVFILL_FORM_TEST"] = json.dumps(answers, ensure_ascii=False)
        p = subprocess.run([sys.executable, tool, *args], env=env, cwd=self.dir,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout + p.stderr

    def seed(self):
        rc, out = self.tool("form", json.dumps(SPEC, ensure_ascii=False), answers=ANSWERS)
        self.assertEqual(rc, 0, out)
        return out

    def path(self, name):
        return os.path.join(self.dir, name)

    def write(self, name, text):
        with open(self.path(name), "w", encoding="utf8") as f:
            f.write(text)

    def assert_no_values(self, out):
        for v in VALUES:
            self.assertNotIn(v, out)


class TestStore(Case):
    def test_init_creates_private_file(self):
        rc, out = self.tool("--json", "init")
        self.assertEqual(rc, 0, out)
        self.assertTrue(json.loads(out)["created"])
        self.assertEqual(os.stat(self.data).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.dirname(self.data)).st_mode & 0o777, 0o700)
        rc, out = self.tool("--json", "init")
        self.assertFalse(json.loads(out)["created"])

    def test_keys_on_empty_store(self):
        rc, out = self.tool("--json", "keys")
        self.assertEqual((rc, json.loads(out)["items"]), (0, []))

    def test_default_location_is_home_not_program_dir(self):
        home = self.path("home")
        os.mkdir(home)
        env = {k: v for k, v in self.env.items() if k != "PRIVFILL_DATA"}
        env["HOME"] = home
        rc, out = self.tool("init", env=env)
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.exists(os.path.join(home, ".privfill", "profile.md")))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(TOOL), "profile.md")))

    def test_file_header_follows_language(self):
        self.tool("init", env=dict(self.env, PRIVFILL_LANG="zh"))
        self.assertIn("| 项目 | 内容 |", read(self.data))
        rc, out = self.tool("form", '{"fields": [{"name": "x"}]}', answers={"x": "1"})
        self.assertEqual(rc, 0, out)
        rc, out = self.tool("--json", "keys")
        self.assertEqual(json.loads(out)["items"], ["x"])


class TestForm(Case):
    def test_normalizes_and_never_prints_values(self):
        out = self.seed()
        self.assert_no_values(out)
        stored = read(self.data)
        for expect in ("| name | Jane Roe |", "| birthday | 2005-03-04 |", "| phone | 13800138000 |",
                       f"| id number | {SAMPLE_ID} |", "| email | jane@example.com |", "| height | 175 |",
                       "| size | M |", "| 地址 | 1 Example Road｜Unit 2 |"):
            self.assertIn(expect, stored)
        self.assertEqual(os.stat(self.data).st_mode & 0o777, 0o600)

    def test_json_statuses(self):
        self.seed()
        rc, out = self.tool("--json", "form", json.dumps(SPEC, ensure_ascii=False),
                            answers={"name": "Jane Roe", "phone": "13900139000"})
        self.assertEqual(rc, 0, out)
        self.assert_no_values(out)
        self.assertNotIn("13900139000", out)
        items = {i["name"]: i for i in json.loads(out)["items"]}
        self.assertEqual(items["name"]["status"], "unchanged")
        self.assertEqual(items["phone"]["status"], "updated")
        self.assertEqual(items["email"]["status"], "skipped")
        self.assertTrue(items["email"]["stored"])
        self.assertEqual(items["phone"]["placeholder"], "{{phone}}")
        self.assertIn("| email | jane@example.com |", read(self.data))

    def test_rejects_bad_answers(self):
        base = {"name": "Jane Roe"}
        for bad in ({"id number": "110105194912310021"}, {"birthday": "2005-02-30"}, {"birthday": "last spring"},
                    {"phone": "abc"}, {"email": "nope"}, {"height": "tall"}, {"size": "XL"}):
            rc, out = self.tool("form", json.dumps(SPEC, ensure_ascii=False), answers={**base, **bad})
            self.assertEqual(rc, 1, (bad, out))
            self.assertFalse(os.path.exists(self.data), bad)
        rc, out = self.tool("form", json.dumps(SPEC, ensure_ascii=False), answers={})
        self.assertEqual(rc, 1, out)

    def test_messages_for_the_person_follow_language(self):
        spec = '{"fields": [{"name": "birthday", "type": "date"}]}'
        rc, en = self.tool("form", spec, answers={"birthday": "soon"})
        rc, zh = self.tool("form", spec, answers={"birthday": "soon"}, env=dict(self.env, PRIVFILL_LANG="zh"))
        self.assertIn("Could not read this date", en)
        self.assertIn("没看懂这个日期", zh)

    def test_pattern(self):
        spec = json.dumps({"fields": [{"name": "year", "type": "number", "pattern": r"(19|20)\d{2}", "pattern_hint": "four digits please"}]})
        rc, out = self.tool("form", spec, answers={"year": "24"})
        self.assertEqual(rc, 1)
        self.assertIn("four digits please", out)
        rc, out = self.tool("form", spec, answers={"year": "2024"})
        self.assertEqual(rc, 0, out)

    def test_spec_from_file(self):
        self.write("spec.json", json.dumps(SPEC, ensure_ascii=False))
        rc, out = self.tool("form", self.path("spec.json"), answers=ANSWERS)
        self.assertEqual(rc, 0, out)

    def test_invalid_specs_exit_4(self):
        for spec in ('not json', '{"fields": []}', '{"protocol": 2, "fields": [{"name": "x"}]}',
                     '{"fields": [{"name": "x", "value": "secret"}]}', '{"fields": [{"name": "x", "default": "s"}]}',
                     '{"fields": [{"name": "x", "type": "idcard"}]}', '{"fields": [{"name": "a|b"}]}',
                     '{"fields": [{"name": "Item"}]}', '{"fields": [{"name": "x"}, {"name": "x"}]}',
                     '{"fields": [{"name": "x", "type": "choice"}]}', '{"fields": [{"name": "x", "choices": ["a"]}]}',
                     '{"fields": [{"name": "x", "pattern": "("}]}', '{"fields": [{"name": "x", "required": "yes"}]}',
                     '{"answers": {}, "fields": [{"name": "x"}]}'):
            rc, out = self.tool("form", spec, answers={"x": "1"})
            self.assertEqual(rc, 4, (spec, out))
            self.assertNotIn("secret", out)
            self.assertFalse(os.path.exists(self.data), spec)

    def test_test_hook_needs_explicit_data_file(self):
        home = self.path("home")
        os.mkdir(home)
        env = {k: v for k, v in self.env.items() if k != "PRIVFILL_DATA"}
        env["HOME"] = home
        rc, out = self.tool("form", '{"fields": [{"name": "x"}]}', answers={"x": "1"}, env=env)
        self.assertNotEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(home, ".privfill", "profile.md")))

    def test_bundled_examples_are_valid(self):
        examples = os.path.join(os.path.dirname(TOOL), "examples")
        specs = [f for f in sorted(os.listdir(examples)) if f.endswith(".json")]
        self.assertTrue(specs)
        for name in specs:
            spec = json.loads(read(os.path.join(examples, name)))
            required = {f["name"]: "x" for f in spec["fields"] if f.get("required") and f.get("type", "text") == "text"}
            rc, out = self.tool("form", os.path.join(examples, name), answers=required)
            self.assertIn(rc, (0, 1), (name, out))  # 0, or 1 when a required non-text field is unanswered
            self.assertNotEqual(rc, 4, (name, out))


class TestFill(Case):
    def test_text_and_formats(self):
        self.seed()
        self.write("a.txt", "{{name}}/{{ birthday }}/{{birthday|YYYY/MM/DD}}/{{birthday|YYYYMMDD}}/{{birthday|D.M.YYYY}}/"
                            "{{birthday|年月日}}/{{id number|last4}}/{{phone|first3}}/{{phone|后4位}}")
        rc, out = self.tool("fill", self.path("a.txt"))
        self.assertEqual(rc, 0, out)
        self.assert_no_values(out)
        self.assertEqual(read(self.path("a.txt")), "Jane Roe/2005-03-04/2005/03/04/20050304/4.3.2005/2005年3月4日/002X/138/8000")

    def test_missing_item_and_bad_format_leave_file_untouched(self):
        self.seed()
        for text in ("{{name}} {{passport}}", "{{name|YYYY}}"):
            self.write("b.txt", text)
            rc, out = self.tool("fill", self.path("b.txt"))
            self.assertEqual(rc, 2, out)
            self.assertEqual(read(self.path("b.txt")), text)
            self.assert_no_values(out)
        self.assertIn("Available items", out)

    def test_docx_escapes_and_keeps_binary_parts(self):
        rc, out = self.tool("form", '{"fields": [{"name": "org"}]}', answers={"org": "A&B <R&D>"})
        self.assertEqual(rc, 0, out)
        with zipfile.ZipFile(self.path("d.docx"), "w") as z:
            z.writestr("word/document.xml", "<d><t>{{org}}</t></d>")
            z.writestr("word/media/x.png", b"\x89PNG{{x}}")
        rc, out = self.tool("fill", self.path("d.docx"))
        self.assertEqual(rc, 0, out)
        with zipfile.ZipFile(self.path("d.docx")) as z:
            self.assertIsNone(z.testzip())
            self.assertEqual(z.read("word/document.xml").decode(), "<d><t>A&amp;B &lt;R&amp;D&gt;</t></d>")
            self.assertEqual(z.read("word/media/x.png"), b"\x89PNG{{x}}")

    def office(self, name, parts):
        with zipfile.ZipFile(self.path(name), "w") as z:
            for part, xml in parts.items():
                z.writestr(part, xml)
        return self.path(name)

    def part(self, name, part):
        with zipfile.ZipFile(self.path(name)) as z:
            self.assertIsNone(z.testzip())
            xml = z.read(part).decode()
        ElementTree.fromstring(xml)  # the part is still well-formed
        return xml

    def test_docx_placeholder_split_over_runs_keeps_formatting(self):
        self.seed()
        doc = (f'<w:document xmlns:w="{W}"><w:body><w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
               '<w:r><w:t xml:space="preserve">Name: </w:t></w:r>'
               '<w:r><w:rPr><w:b/></w:rPr><w:t>{{na</w:t></w:r>'
               '<w:proofErr w:type="spellStart"/><w:r><w:rPr><w:i/></w:rPr><w:t>me</w:t></w:r>'
               '<w:r><w:t>}} born {{</w:t></w:r><w:r><w:t>birthday|YYYY/MM/DD}}.</w:t></w:r>'
               '</w:p><w:p/><w:p><w:r><w:t>plain</w:t></w:r></w:p></w:body></w:document>')
        rc, out = self.tool("--json", "fill", self.office("s.docx", {"word/document.xml": doc}))
        self.assertEqual(rc, 0, out)
        self.assert_no_values(out)
        self.assertEqual(json.loads(out)["files"][0]["used"], {"name": 1, "birthday": 1})
        self.assertEqual(self.part("s.docx", "word/document.xml"), (
            f'<w:document xmlns:w="{W}"><w:body><w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
            '<w:r><w:t xml:space="preserve">Name: </w:t></w:r>'
            '<w:r><w:rPr><w:b/></w:rPr><w:t>Jane Roe</w:t></w:r>'
            '<w:proofErr w:type="spellStart"/><w:r><w:rPr><w:i/></w:rPr><w:t></w:t></w:r>'
            '<w:r><w:t xml:space="preserve"> born 2005/03/04</w:t></w:r><w:r><w:t>.</w:t></w:r>'
            '</w:p><w:p/><w:p><w:r><w:t>plain</w:t></w:r></w:p></w:body></w:document>'))

    def test_docx_headers_footers_and_text_boxes(self):
        self.seed()
        box = ('<w:p><w:r><w:pict><w:txbxContent><w:p><w:r><w:t>{{</w:t></w:r><w:r><w:t>phone}}</w:t></w:r></w:p>'
               '</w:txbxContent></w:pict></w:r><w:r><w:t>{{na</w:t></w:r><w:r><w:t>me}}</w:t></w:r></w:p>')
        name = self.office("h.docx", {
            "word/document.xml": f'<w:document xmlns:w="{W}"><w:body>{box}</w:body></w:document>',
            "word/header1.xml": f'<w:hdr xmlns:w="{W}"><w:p><w:r><w:t>{{{{em</w:t></w:r><w:r><w:t>ail}}}}</w:t></w:r></w:p></w:hdr>',
            "word/footer1.xml": f'<w:ftr xmlns:w="{W}"><w:p><w:r><w:t>{{{{</w:t></w:r><w:r><w:t>name</w:t></w:r><w:r><w:t>}}}}</w:t></w:r></w:p></w:ftr>',
        })
        rc, out = self.tool("fill", name)
        self.assertEqual(rc, 0, out)
        self.assert_no_values(out)
        body = self.part("h.docx", "word/document.xml")
        self.assertIn("<w:txbxContent><w:p><w:r><w:t>13800138000</w:t></w:r><w:r><w:t></w:t></w:r></w:p>", body)
        self.assertIn("<w:r><w:t>Jane Roe</w:t></w:r><w:r><w:t></w:t></w:r></w:p></w:body>", body)
        self.assertIn("<w:t>jane@example.com</w:t>", self.part("h.docx", "word/header1.xml"))
        self.assertIn("<w:t>Jane Roe</w:t>", self.part("h.docx", "word/footer1.xml"))

    def test_placeholders_are_not_joined_across_paragraphs(self):
        self.seed()
        doc = (f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>{{{{na</w:t></w:r></w:p><w:p><w:r><w:t>me}}}}</w:t></w:r>'
               '<w:r><w:delText>{{na</w:delText></w:r><w:r><w:delText>me}}</w:delText></w:r></w:p></w:body></w:document>')
        name = self.office("p.docx", {"word/document.xml": doc})
        before = read_bytes(name)
        rc, out = self.tool("fill", name)
        self.assertEqual(rc, 0, out)
        self.assertIn("no placeholders", out)
        self.assertEqual(read_bytes(name), before)

    def test_xlsx_shared_strings_split_over_runs(self):
        self.seed()
        sst = (f'<sst xmlns="{S}" count="2" uniqueCount="2"><si><t>plain</t></si>'
               '<si><r><t>Tel {{ph</t></r><r><rPr><b/></rPr><t>one}} ok</t></r>'
               '<rPh sb="0" eb="1"><t>{{ph</t></rPh><phoneticPr fontId="1"/></si></sst>')
        rc, out = self.tool("--json", "fill", self.office("b.xlsx", {"xl/sharedStrings.xml": sst}))
        self.assertEqual(rc, 0, out)
        self.assert_no_values(out)
        self.assertEqual(json.loads(out)["files"][0]["used"], {"phone": 1})
        self.assertEqual(self.part("b.xlsx", "xl/sharedStrings.xml"), (
            f'<sst xmlns="{S}" count="2" uniqueCount="2"><si><t>plain</t></si>'
            '<si><r><t>Tel 13800138000</t></r><r><rPr><b/></rPr><t xml:space="preserve"> ok</t></r>'
            '<rPh sb="0" eb="1"><t>{{ph</t></rPh><phoneticPr fontId="1"/></si></sst>'))

    def test_pptx_text_runs_split_over_runs(self):
        self.seed()
        slide = (f'<p:sld xmlns:a="{A}" xmlns:p="{P}"><p:cSld><p:spTree><p:sp><p:txBody><a:bodyPr/>'
                 '<a:p><a:r><a:rPr lang="en-US" b="1"/><a:t>Hi {{</a:t></a:r><a:r><a:rPr lang="en-US"/><a:t>name</a:t></a:r>'
                 '<a:r><a:rPr lang="en-US" i="1"/><a:t>}} !</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
        rc, out = self.tool("fill", self.office("s.pptx", {"ppt/slides/slide1.xml": slide}))
        self.assertEqual(rc, 0, out)
        self.assert_no_values(out)
        self.assertEqual(self.part("s.pptx", "ppt/slides/slide1.xml"), (
            f'<p:sld xmlns:a="{A}" xmlns:p="{P}"><p:cSld><p:spTree><p:sp><p:txBody><a:bodyPr/>'
            '<a:p><a:r><a:rPr lang="en-US" b="1"/><a:t>Hi Jane Roe</a:t></a:r><a:r><a:rPr lang="en-US"/><a:t></a:t></a:r>'
            '<a:r><a:rPr lang="en-US" i="1"/><a:t> !</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'))

    def test_office_special_characters_stay_valid(self):
        rc, out = self.tool("form", '{"fields": [{"name": "org"}, {"name": "R&D"}]}',
                            answers={"org": "A&B <R&D> \"q\" 'a' {{org}}", "R&D": "x"})
        self.assertEqual(rc, 0, out)
        doc = (f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>&lt;{{{{o</w:t></w:r><w:r><w:t>rg}}}}&gt; &#123;{{R&amp;</w:t></w:r>'
               '<w:r><w:t>D}}</w:t></w:r></w:p></w:body></w:document>')
        rels = '<Relationships><Relationship Id="r1" Target="https://example.com/?q={{org}}" TargetMode="External"/></Relationships>'
        name = self.office("e.docx", {"word/document.xml": doc, "word/_rels/document.xml.rels": rels})
        rc, out = self.tool("fill", name)
        self.assertEqual(rc, 0, out)
        root = ElementTree.fromstring(self.part("e.docx", "word/document.xml"))
        self.assertEqual("".join(root.itertext()), "<A&B <R&D> \"q\" 'a' {{org}}> x")  # &#123; is a visible brace
        rel = ElementTree.fromstring(self.part("e.docx", "word/_rels/document.xml.rels"))[0]
        self.assertEqual(rel.get("Target"), "https://example.com/?q=A&B <R&D> \"q\" 'a' {{org}}")

    def test_office_without_placeholders_is_byte_for_byte_untouched(self):
        self.seed()
        doc = f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>{{ no }} placeholders</w:t></w:r><w:r><w:t>here</w:t></w:r></w:p></w:body></w:document>'
        name = self.office("n.docx", {"word/document.xml": doc})
        before = read_bytes(name)
        stat = os.stat(name)
        rc, out = self.tool("fill", name)
        self.assertEqual(rc, 0, out)
        self.assertIn("no placeholders", out)
        self.assertEqual(read_bytes(name), before)
        self.assertEqual(os.stat(name).st_mtime_ns, stat.st_mtime_ns)
        rc, out = self.tool("render", name, self.path("copy.docx"))
        self.assertEqual(rc, 0, out)
        self.assertEqual(read_bytes(self.path("copy.docx")), before)

    def test_office_parts_without_placeholders_keep_their_bytes(self):
        self.seed()
        other = f'<w:styles xmlns:w="{W}"><w:style w:styleId="a&amp;b"/>\r\n<w:t>x</w:t ></w:styles>'
        doc = f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>&#65;&#13;{{{{</w:t></w:r><w:r><w:t>name}}}}</w:t></w:r></w:p><w:p><w:r><w:t>&#66;&quot;</w:t></w:r></w:p></w:body></w:document>'
        name = self.office("k.docx", {"word/document.xml": doc, "word/styles.xml": other})
        rc, out = self.tool("fill", name)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.part("k.docx", "word/styles.xml"), other)
        body = self.part("k.docx", "word/document.xml")
        self.assertIn("<w:t>A&#13;Jane Roe</w:t>", body)
        self.assertIn("<w:t>&#66;&quot;</w:t>", body)  # an untouched text node is not rewritten

    def test_split_placeholder_for_unknown_item_leaves_file_untouched(self):
        self.seed()
        doc = f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>{{{{name}}}} {{{{pass</w:t></w:r><w:r><w:t>port}}}}</w:t></w:r></w:p></w:body></w:document>'
        name = self.office("u.docx", {"word/document.xml": doc})
        before = read_bytes(name)
        rc, out = self.tool("fill", name)
        self.assertEqual(rc, 2, out)
        self.assertIn("'passport'", out)
        self.assert_no_values(out)
        self.assertEqual(read_bytes(name), before)

    def test_notebook_stays_valid_json(self):
        rc, out = self.tool("form", '{"fields": [{"name": "note"}]}', answers={"note": 'said "ok" \\ done'})
        self.assertEqual(rc, 0, out)
        self.write("n.ipynb", json.dumps({"cells": [{"source": ["{{note}}\n"]}]}))
        rc, out = self.tool("fill", self.path("n.ipynb"))
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(read(self.path("n.ipynb")))["cells"][0]["source"][0], 'said "ok" \\ done\n')

    def test_render_keeps_master(self):
        self.seed()
        master = "{{name}}_report.md"
        self.write(master, "Name: {{name}}")
        os.mkdir(self.path("out"))
        rc, out = self.tool("--json", "render", self.path(master), os.path.join(self.path("out"), "{{name}}_report.md"))
        self.assertEqual(rc, 0, out)
        self.assert_no_values(out)
        self.assertEqual(json.loads(out)["used"], {"name": 1})
        self.assertEqual(read(self.path(master)), "Name: {{name}}")
        self.assertEqual(read(self.path("out/Jane Roe_report.md")), "Name: Jane Roe")

    def test_render_failure_leaves_nothing_behind(self):
        self.seed()
        self.write("m.md", "{{passport}}")
        rc, out = self.tool("render", self.path("m.md"), self.path("o_{{name}}.md"))
        self.assertEqual(rc, 2, out)
        self.assertFalse(os.path.exists(self.path("o_Jane Roe.md")))
        rc, out = self.tool("render", self.path("m.md"), self.path("m.md"))
        self.assertEqual(rc, 1, out)
        self.assertEqual(read(self.path("m.md")), "{{passport}}")

    def test_pack_bundles_outputs_and_leaves_them_untouched(self):
        self.seed()
        self.write("Jane Roe_report.md", "filled")
        self.write("notes.txt", "plain")
        before = read_bytes(self.path("Jane Roe_report.md"))
        rc, out = self.tool("--json", "pack", self.path("{{name}}_hw.zip"), self.path("{{name}}_report.md"), self.path("notes.txt"))
        self.assertEqual(rc, 0, out)
        self.assert_no_values(out)
        res = json.loads(out)
        self.assertEqual((res["packed"], res["used"]), (2, {"name": 2}))
        with zipfile.ZipFile(self.path("Jane Roe_hw.zip")) as z:
            self.assertEqual(sorted(z.namelist()), ["Jane Roe_report.md", "notes.txt"])
            self.assertEqual(z.read("notes.txt"), b"plain")
        self.assertEqual(read_bytes(self.path("Jane Roe_report.md")), before)

    def test_pack_failure_leaves_no_archive_behind(self):
        self.seed()
        self.write("notes.txt", "plain")
        rc, out = self.tool("pack", self.path("{{passport}}.zip"), self.path("notes.txt"))
        self.assertEqual(rc, 2, out)
        self.assertIn("Available items: name,", out)
        rc, out = self.tool("pack", self.path("{{name|YYYY}}.zip"), self.path("notes.txt"))
        self.assertEqual(rc, 2, out)
        rc, out = self.tool("pack", self.path("{{name}}.zip"), self.path("notes.txt"), self.path("absent.txt"))
        self.assertEqual(rc, 1, out)
        self.assert_no_values(out)
        self.assertEqual(sorted(os.listdir(self.dir)), ["notes.txt", "store"])

    def test_run_is_gone(self):
        self.seed()
        rc, out = self.tool("run", "--", "true")
        self.assertEqual(rc, 1, out)
        self.assertIn("Unknown subcommand", out)
        self.assertNotIn("run", self.tool()[1].replace("runtime", ""))

if __name__ == "__main__":
    unittest.main()

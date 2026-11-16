#!/usr/bin/env python3
"""privfill - let an AI assistant use your personal details without seeing them.

Personal details live in one local file. The assistant writes documents with
placeholders such as {{name}}; this program fills in the real values locally.
New details are entered by you in a local form. Nothing this program prints
ever contains a value.

  privfill.py init                       create the data file (no-op if it exists)
  privfill.py keys                       list stored item names (never values)
  privfill.py protocol                   print a summary of the entry protocol
  privfill.py form '<JSON>' | <file>     show an entry form described by the protocol
  privfill.py add <item>                 shorthand: enter one free-text item
  privfill.py render <master> <output>   fill a copy; the master stays untouched
                                         (the output path may contain placeholders)
  privfill.py fill <file>...             fill files in place
  privfill.py run -- <cmd> [args]...     substitute placeholders in args, run the
                                         command with its output discarded
                                         (args starting with raw: are passed as is)

Put --json before the subcommand for a single JSON object as output.
Placeholders: {{item}}, {{item|YYYY/MM/DD}}, {{item|last4}}, {{item|first6}}

Exit codes: 0 ok, 1 usage or runtime error, 2 unknown item or bad format,
            3 cancelled by the person, 4 invalid protocol

Command output is in English (it is read by the assistant). The form, which is
read by the person, follows the system language (English or Chinese); override
with PRIVFILL_LANG=en|zh.
"""
import contextlib
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from xml.sax.saxutils import escape

__version__ = "1.0.0"
PROTOCOL_VERSION = 1
HERE = os.path.dirname(os.path.abspath(__file__))
LEGACY_NAME = "个人信息.md"  # file name used by early versions


def data_path():
    """$PRIVFILL_DATA, else a data file next to the program (early installs), else ~/.privfill/profile.md."""
    env = os.environ.get("PRIVFILL_DATA")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    for name in ("profile.md", LEGACY_NAME):
        if os.path.exists(os.path.join(HERE, name)):
            return os.path.join(HERE, name)
    return os.path.join(os.path.expanduser("~"), ".privfill", "profile.md")


def pick_lang():
    v = os.environ.get("PRIVFILL_LANG") or os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
    return "zh" if v.lower().startswith("zh") else "en"


DATA = data_path()
DATA_NAME = os.path.basename(DATA)
LANG = pick_lang()
PH = re.compile(r"\{\{\s*([^{}\s][^{}]*?)\s*\}\}")
ROW = re.compile(r"^\|([^|]+)\|([^|]*)\|\s*$")
HEADER_NAMES = {"项目", "Item"}
OFFICE = (".docx", ".xlsx", ".pptx")
FIELD_KEYS = {"name", "label", "hint", "type", "required", "choices", "pattern", "pattern_hint", "section"}
SPEC_KEYS = {"protocol", "title", "note", "fields"}
RESULT = {}  # structured extras for --json (names and statuses only, never values)

# Text shown to the person (form and data file). Everything else is English.
UI = {
    "title": ("Enter personal information", "录入个人信息"),
    "privacy": ("Stored only on this computer in {file}. It is never sent to the AI.\n"
                "Type it however is natural; the program tidies the format.",
                "内容只保存在本机的 {file}，不会发送给 AI。\n怎么顺手怎么写，格式由程序整理。"),
    "required": ("Required", "必填"),
    "keep": ("Already stored. Leave it as is to keep it.", "已有内容，不改就保持原样。"),
    "save": ("Save", "保存"),
    "cancel": ("Cancel", "取消"),
    "foot_optional": ("{n} items, all optional", "共 {n} 项，都可以留空"),
    "foot_required": ("{n} items, {r} required", "共 {n} 项，其中 {r} 项必填"),
    "foot_keys": ("Enter saves, Esc cancels", "回车保存，Esc 取消"),
    "bad_required": ("This one is required.", "这一项必填。"),
    "bad_date": ("Could not read this date. Write it with digits, like 2005-03-04 or 2005/3/4.",
                 "没看懂这个日期，请写成类似 2005年3月4日 或 2005-03-04。"),
    "bad_date_invalid": ("This date does not exist. Check the year, month and day.", "这个日期不存在，请核对年月日。"),
    "bad_phone": ("A phone number should contain only digits (7 to 15 of them).", "电话号码里应该只有数字（7 到 15 位），请核对。"),
    "bad_id_shape": ("This ID number should have 18 characters (the last may be X).",
                     "身份证号应为 18 位（最后一位可以是 X），现在位数或字符不对。"),
    "bad_id_check": ("The check digit does not match; one character is probably mistyped.",
                     "身份证号的校验位对不上，可能有一位输错了，请核对。"),
    "bad_email": ("This does not look like an email address (name@example.com).", "邮箱格式不对，应类似 name@example.com。"),
    "bad_number": ("A number is needed here.", "这里需要一个数字。"),
    "bad_choice": ("Pick one of the options in the list.", "请从下拉框里选择一项。"),
    "bad_pattern": ("This does not match the expected format; see the hint.", "格式不符合要求，请看提示。"),
    "file_head": ("# Personal details (private)\n\n"
                  "> Managed by privfill. Do not let an AI read this file and do not commit it anywhere.\n\n"
                  "| Item | Value |\n|---|---|\n",
                  "# 个人信息（私密）\n\n> 由 privfill 管理。不要把这个文件交给 AI 读取，也不要提交到任何仓库。\n\n"
                  "| 项目 | 内容 |\n|---|---|\n"),
}


def ui(key, **kw):
    text = UI[key][1 if LANG == "zh" else 0]
    return text.format(**kw) if kw else text


PROTOCOL = """privfill entry protocol v1 (summary; the full specification is PROTOCOL.md)

{
  "protocol": 1,
  "title": "form title",
  "note":  "optional: one or two sentences for the person - why this is needed, where it is used",
  "fields": [
    {
      "name":     "item name (required) = the placeholder {{name}}; no | { } or newline",
      "label":    "optional: the question shown on the form, in plain words",
      "hint":     "optional: how to fill it in; text after 'e.g.' becomes the grey example in the box",
      "type":     "optional, default text: text date phone id_card email number choice",
      "required": "optional, default false; when false an empty answer means skip / keep",
      "section":  "optional: start a new group before this field; the value is the group title",
      "choices":  ["required for type choice"],
      "pattern":  "optional: regex the normalized value must fully match",
      "pattern_hint": "optional: message shown to the person when pattern does not match"
    }
  ]
}

type      what the person may type                         stored as
text      any single line                                  as typed (whitespace collapsed)
date      2005-3-4, 2005/03/04, 2005.3.4, 20050304         2005-03-04
phone     138 0013 8000, +86 138-0013-8000                 13800138000
id_card   18-character PRC resident ID, spaces and x ok    no spaces, upper-case X, check digit verified
email     an email address                                 trimmed, shape checked
number    digits, commas and full-width digits ok          plain number
choice    picked from a list                               the chosen option

Rules:
- The protocol holds questions only, never answers: any undefined key (value, default, ...) is rejected.
- Run `keys` first to avoid duplicate or near-synonym names; put related items in one form.
- Items that already exist are pre-filled on the person's screen; changed = updated, untouched = kept.
- Output lists only item names, statuses, types and placeholders - never values.
- Formats when filling: {{birthday|YYYY/MM/DD}} {{birthday|YYYYMMDD}} {{item|last4}} {{item|first6}}
"""


# ------------------------------------------------------------------ data file
def ensure_data_file():
    """Create an empty data file (mode 600, directory 700) if there is none. Returns True if created."""
    if os.path.exists(DATA):
        return False
    os.makedirs(os.path.dirname(DATA), mode=0o700, exist_ok=True)
    fd = os.open(DATA, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf8") as f:
        f.write(ui("file_head"))
    return True


def load():
    data = {}
    if not os.path.exists(DATA):
        return data
    with open(DATA, encoding="utf8") as f:
        for line in f:
            m = ROW.match(line)
            if not m:
                continue
            k, v = m.group(1).strip(), m.group(2).strip()
            if not k or k in HEADER_NAMES or set(k) <= set("-: "):
                continue
            if v:
                data[k] = v
    return data


def atomic_write(path, writer):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".privfill-")
    os.close(fd)
    try:
        writer(tmp)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def save_items(items):
    """items: {name: value}. Update or append table rows. Returns {name: was_update}."""
    ensure_data_file()
    with open(DATA, encoding="utf8") as f:
        lines = f.read().split("\n")
    status = {}
    for key, value in items.items():
        row = f"| {key} | {value} |"
        hit, last = None, None
        for i, line in enumerate(lines):
            m = ROW.match(line)
            if m:
                last = i
                if m.group(1).strip() == key:
                    hit = i
        if hit is not None:
            lines[hit] = row
        elif last is not None:
            lines.insert(last + 1, row)
        else:
            lines += ui("file_head").rstrip("\n").split("\n")[-2:] + [row]
        status[key] = hit is not None

    def w(tmp):
        with open(tmp, "w", encoding="utf8") as f:
            f.write("\n".join(lines))

    atomic_write(DATA, w)
    os.chmod(DATA, 0o600)
    return status


# ------------------------------------------------------------------ normalization
class Bad(Exception):
    """The answer is not acceptable; the message is shown to the person."""


def nfkc(s):
    return unicodedata.normalize("NFKC", s)


def norm_date(s):
    t = nfkc(s).strip()
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", t) or re.fullmatch(r"\D*(\d{4})\D+(\d{1,2})\D+(\d{1,2})\D*", t)
    if not m:
        raise Bad(ui("bad_date"))
    try:
        return datetime.date(*map(int, m.groups())).isoformat()
    except ValueError:
        raise Bad(ui("bad_date_invalid"))


def norm_phone(s):
    t = re.sub(r"[\s\-()]", "", nfkc(s))
    t = re.sub(r"^(\+86|0086)", "", t)
    if len(t) == 13 and t.startswith("86"):
        t = t[2:]
    if not re.fullmatch(r"\+?\d{7,15}", t):
        raise Bad(ui("bad_phone"))
    return t


def norm_id_card(s):
    t = re.sub(r"[\s\-]", "", nfkc(s)).upper()
    if not re.fullmatch(r"\d{17}[\dX]", t):
        raise Bad(ui("bad_id_shape"))
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    if "10X98765432"[sum(int(c) * k for c, k in zip(t, weights)) % 11] != t[17]:
        raise Bad(ui("bad_id_check"))
    return t


def norm_email(s):
    t = nfkc(s).strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", t):
        raise Bad(ui("bad_email"))
    return t


def norm_number(s):
    t = re.sub(r"[\s,]", "", nfkc(s))
    if not re.fullmatch(r"-?\d+(\.\d+)?", t):
        raise Bad(ui("bad_number"))
    return t


def norm_text(s):
    return re.sub(r"\s+", " ", s).strip()


NORMS = {"text": norm_text, "date": norm_date, "phone": norm_phone, "id_card": norm_id_card,
         "email": norm_email, "number": norm_number, "choice": norm_text}


def normalize(field, raw):
    v = NORMS[field["type"]](raw)
    if field["type"] == "choice" and v not in field["choices"]:
        raise Bad(ui("bad_choice"))
    if field.get("pattern") and not re.fullmatch(field["pattern"], v):
        raise Bad(field.get("pattern_hint") or ui("bad_pattern"))
    return v.replace("|", "｜")  # keep the table intact


def parse_spec(text):
    """Validate an entry protocol. Returns (spec, None) or (None, reason)."""
    if os.path.isfile(text):
        with open(text, encoding="utf8") as f:
            text = f.read()
    try:
        spec = json.loads(text)
    except ValueError as e:
        return None, f"the protocol is not valid JSON ({e})"
    if not isinstance(spec, dict) or not isinstance(spec.get("fields"), list) or not spec["fields"]:
        return None, "the protocol must be an object with a non-empty 'fields' array"
    if spec.get("protocol", PROTOCOL_VERSION) != PROTOCOL_VERSION:
        return None, f"unsupported protocol version {spec.get('protocol')!r} (this program supports {PROTOCOL_VERSION})"
    unknown = sorted(set(spec) - SPEC_KEYS)
    if unknown:
        return None, f"undefined key(s) in the protocol: {', '.join(unknown)}"
    for k in ("title", "note"):
        if k in spec and not isinstance(spec[k], str):
            return None, f"'{k}' must be a string"
    seen = set()
    for i, f in enumerate(spec["fields"], 1):
        if not isinstance(f, dict) or not isinstance(f.get("name"), str):
            return None, f"field {i} has no 'name'"
        f["name"] = f["name"].strip()
        n = f["name"]
        if not n or re.search(r"[|{}\n]", n) or n in HEADER_NAMES:
            return None, f"field {i} has an invalid name (empty, reserved, or contains | {{ }} or a newline)"
        if n in seen:
            return None, f"field '{n}' appears twice"
        seen.add(n)
        unknown = sorted(set(f) - FIELD_KEYS)
        if unknown:
            return None, f"field '{n}' has undefined key(s): {', '.join(unknown)} (the protocol holds questions, never answers)"
        for k in ("label", "hint", "pattern", "pattern_hint", "section"):
            if k in f and not isinstance(f[k], str):
                return None, f"field '{n}': '{k}' must be a string"
        if not isinstance(f.get("required", False), bool):
            return None, f"field '{n}': 'required' must be true or false"
        f.setdefault("type", "text")
        if f["type"] not in NORMS:
            return None, f"field '{n}': unknown type '{f['type']}' (available: {', '.join(NORMS)})"
        if f["type"] == "choice":
            ch = f.get("choices")
            if not (isinstance(ch, list) and ch and all(isinstance(c, str) and c.strip() for c in ch)):
                return None, f"field '{n}' is a choice and needs a non-empty array of strings in 'choices'"
        elif "choices" in f:
            return None, f"field '{n}' is not a choice and must not have 'choices'"
        if f.get("pattern"):
            try:
                re.compile(f["pattern"])
            except re.error:
                return None, f"field '{n}': 'pattern' is not a valid regular expression"
    return spec, None


# ------------------------------------------------------------------ form
def run_form(spec, data):
    """Show the form. Returns {name: normalized value} for answered fields, or None if cancelled."""
    test = os.environ.get("PRIVFILL_FORM_TEST")
    if test is not None:  # test hook: answers from the environment, fake data file only
        if not os.environ.get("PRIVFILL_DATA"):
            raise RuntimeError("PRIVFILL_FORM_TEST requires PRIVFILL_DATA")
        answers, out = json.loads(test), {}
        for f in spec["fields"]:
            raw = str(answers.get(f["name"], "")).strip()
            if raw:
                out[f["name"]] = normalize(f, raw)
            elif f.get("required"):
                raise Bad(f"{f['name']}: {ui('bad_required')}")
        return out
    try:
        from PyQt6 import QtWidgets  # noqa: F401
    except ImportError:
        sys_py = "/usr/bin/python3"
        if os.path.exists(sys_py) and os.path.realpath(sys.executable) != os.path.realpath(sys_py) \
                and not os.environ.get("PRIVFILL_REEXEC"):
            # This interpreter has no Qt (a conda or venv Python, say): retry with the system one.
            os.environ["PRIVFILL_REEXEC"] = "1"
            os.execv(sys_py, [sys_py, os.path.abspath(__file__)] + sys.argv[1:])
        return tk_form(spec, data)
    return qt_form(spec, data)


def example_from(hint):
    m = re.search(r"(?:例如|e\.g\.,?|for example,?)\s*([^，,。；;.]+(?:\.[^\s，,。；;]+)*)", hint or "", flags=re.I)
    return m.group(1).strip() if m else ""


def qt_form(spec, data):
    from PyQt6 import QtCore, QtGui, QtWidgets

    shot = os.environ.get("PRIVFILL_FORM_SHOT")
    if shot and not os.environ.get("PRIVFILL_DATA"):
        raise RuntimeError("PRIVFILL_FORM_SHOT requires PRIVFILL_DATA")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["privfill"])
    app.setStyle("Fusion")  # one base style everywhere; the look comes from the stylesheet below
    font = QtGui.QFont()
    font.setFamilies(["Noto Sans CJK SC", "Source Han Sans CN", "Noto Sans", "sans-serif"])
    font.setPointSize(11)
    app.setFont(font)

    # Draw the combo-box arrow at run time so no icon theme is needed.
    tmpdir = tempfile.mkdtemp(prefix="privfill-ui-")
    arrow = os.path.join(tmpdir, "chevron.png")
    pm = QtGui.QPixmap(24, 24)
    pm.fill(QtCore.Qt.GlobalColor.transparent)
    qp = QtGui.QPainter(pm)
    qp.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    pen = QtGui.QPen(QtGui.QColor("#6b7280"), 2.4)
    pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
    qp.setPen(pen)
    qp.drawPolyline(QtCore.QPointF(6, 9), QtCore.QPointF(12, 15), QtCore.QPointF(18, 9))
    qp.end()
    pm.save(arrow)

    title = spec.get("title") or ui("title")
    fields = spec["fields"]
    n_req = sum(1 for f in fields if f.get("required"))

    dlg = QtWidgets.QDialog()
    dlg.setWindowTitle(title)
    dlg.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, True)
    dlg.setMinimumWidth(580)
    dlg.setStyleSheet("""
        QDialog { background: #f3f4f6; }
        QLabel { color: #111827; background: transparent; }
        QLabel#title { font-size: 18pt; font-weight: 700; }
        QLabel#note { color: #4b5563; font-size: 10.5pt; }
        QFrame#privacy { background: #ecfdf3; border: 1px solid #b7ebc9; border-radius: 8px; }
        QFrame#privacybar { background: #16a34a; border-radius: 2px; }
        QLabel#privacytext { color: #166534; font-size: 9.5pt; }
        QFrame#card { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 12px; }
        QWidget#body, QScrollArea#scroll, QScrollArea#scroll > QWidget { background: #ffffff; border: none; }
        QLabel#section { color: #2563eb; font-size: 9.5pt; font-weight: 700; }
        QFrame#rule { background: #eef0f3; max-height: 1px; min-height: 1px; border: none; }
        QLabel#q { font-size: 10.5pt; font-weight: 600; }
        QLabel#key { color: #6b7280; background: #f3f4f6; border-radius: 9px; padding: 1px 8px; font-size: 8.5pt; }
        QLabel#req { color: #b42318; background: #fef3f2; border-radius: 9px; padding: 1px 8px; font-size: 8.5pt; font-weight: 600; }
        QLabel#hint { color: #6b7280; font-size: 9.5pt; }
        QLabel#hint[bad="true"] { color: #b42318; font-weight: 600; }
        QLineEdit, QComboBox {
            min-height: 22px; padding: 8px 11px; border: 1px solid #d1d5db; border-radius: 8px;
            background: #ffffff; color: #111827; selection-background-color: #bfdbfe; selection-color: #111827;
        }
        QLineEdit:hover, QComboBox:hover { border: 1px solid #9ca3af; }
        QLineEdit:focus, QComboBox:focus { border: 2px solid #2563eb; padding: 7px 10px; }
        QLineEdit[bad="true"], QComboBox[bad="true"] { border: 2px solid #d92d20; padding: 7px 10px; background: #fffbfa; }
        QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; width: 34px; border: none; }
        QComboBox::down-arrow { image: url(ARROW); width: 16px; height: 16px; }
        QComboBox QAbstractItemView {
            background: #ffffff; color: #111827; border: 1px solid #d1d5db; border-radius: 6px; padding: 4px;
            selection-background-color: #eff6ff; selection-color: #1d4ed8; outline: 0;
        }
        QComboBox QAbstractItemView::item { min-height: 30px; padding-left: 8px; }
        QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 2px 4px 0; }
        QScrollBar::handle:vertical { background: #d1d5db; border-radius: 4px; min-height: 36px; }
        QScrollBar::handle:vertical:hover { background: #9ca3af; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        QLabel#foot { color: #6b7280; font-size: 9.5pt; }
        QPushButton {
            min-height: 22px; padding: 8px 24px; border-radius: 8px; border: 1px solid #d1d5db;
            background: #ffffff; color: #374151; font-weight: 600;
        }
        QPushButton:hover { background: #f9fafb; border: 1px solid #9ca3af; }
        QPushButton#save { background: #2563eb; border: 1px solid #2563eb; color: #ffffff; }
        QPushButton#save:hover { background: #1d4ed8; border: 1px solid #1d4ed8; }
        QPushButton#save:pressed { background: #1e40af; }
    """.replace("ARROW", arrow.replace("\\", "/")))

    def label(text, name, wrap=True):
        w = QtWidgets.QLabel(text)
        w.setObjectName(name)
        w.setWordWrap(wrap)
        return w

    outer = QtWidgets.QVBoxLayout(dlg)
    outer.setContentsMargins(26, 24, 26, 20)
    outer.setSpacing(0)
    outer.addWidget(label(title, "title"))
    if spec.get("note"):
        outer.addSpacing(6)
        outer.addWidget(label(spec["note"], "note"))
    outer.addSpacing(14)

    box = QtWidgets.QFrame()
    box.setObjectName("privacy")
    bl = QtWidgets.QHBoxLayout(box)
    bl.setContentsMargins(10, 9, 12, 9)
    bl.setSpacing(10)
    bar = QtWidgets.QFrame()
    bar.setObjectName("privacybar")
    bar.setFixedWidth(4)
    bl.addWidget(bar)
    bl.addWidget(label(ui("privacy", file=DATA_NAME), "privacytext"), 1)
    outer.addWidget(box)
    outer.addSpacing(14)

    card = QtWidgets.QFrame()
    card.setObjectName("card")
    cl = QtWidgets.QVBoxLayout(card)
    cl.setContentsMargins(4, 6, 4, 6)
    body = QtWidgets.QWidget()
    body.setObjectName("body")
    lay = QtWidgets.QVBoxLayout(body)
    lay.setContentsMargins(18, 12, 16, 14)
    lay.setSpacing(0)

    rows = []
    for i, f in enumerate(fields):
        name = f["name"]
        if f.get("section"):
            if i:
                lay.addSpacing(20)
                rule = QtWidgets.QFrame()
                rule.setObjectName("rule")
                lay.addWidget(rule)
                lay.addSpacing(14)
            lay.addWidget(label(f["section"], "section", wrap=False))
            lay.addSpacing(10)
        elif i:
            lay.addSpacing(16)
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(label(f.get("label") or name, "q", wrap=False))
        if f.get("label") and f["label"] != name:
            head.addWidget(label(name, "key", wrap=False))
        if f.get("required"):
            head.addWidget(label(ui("required"), "req", wrap=False))
        head.addStretch(1)
        lay.addLayout(head)
        lay.addSpacing(6)
        if f["type"] == "choice":
            w = QtWidgets.QComboBox()
            w.setView(QtWidgets.QListView())
            w.addItem("")
            w.addItems(f["choices"])
            w.setCurrentText(data.get(name, ""))
            w.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
            w.wheelEvent = lambda e: e.ignore()  # scrolling the form must not change the choice
            get, changed = w.currentText, w.currentTextChanged
        else:
            w = QtWidgets.QLineEdit(data.get(name, ""))
            w.setClearButtonEnabled(True)
            w.setPlaceholderText(example_from(f.get("hint")))
            get, changed = w.text, w.textChanged
        lay.addWidget(w)
        tip = f.get("hint") or ""
        if name in data:
            tip = (tip + "  " if tip else "") + ui("keep")
        hint = label(tip, "hint")
        hint.setContentsMargins(2, 5, 0, 0)
        hint.setVisible(bool(tip))
        lay.addWidget(hint)

        def mark(bad, text, w=w, hint=hint):
            for x in (w, hint):
                x.setProperty("bad", "true" if bad else "false")
                x.style().unpolish(x)
                x.style().polish(x)
            hint.setText(text)
            hint.setVisible(bool(text))

        changed.connect(lambda *_, mark=mark, tip=tip: mark(False, tip))
        rows.append((f, w, get, mark, tip))
    lay.addStretch(1)

    scroll = QtWidgets.QScrollArea()
    scroll.setObjectName("scroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(body)
    screen = app.primaryScreen()
    avail = screen.availableGeometry().height() if screen else 900
    scroll.setMinimumHeight(min(body.sizeHint().height() + 2, max(240, avail - 380)))
    cl.addWidget(scroll)
    outer.addWidget(card, 1)
    outer.addSpacing(16)

    result = {"out": None}

    def save():
        out, first_bad = {}, None
        for f, w, get, mark, tip in rows:
            raw = get().strip()
            try:
                if raw:
                    out[f["name"]] = normalize(f, raw)
                elif f.get("required"):
                    raise Bad(ui("bad_required"))
                mark(False, tip)
            except Bad as e:
                mark(True, str(e))
                first_bad = first_bad or w
        if first_bad is not None:
            first_bad.setFocus()
            scroll.ensureWidgetVisible(first_bad, 0, 70)
            return
        result["out"] = out
        dlg.accept()

    btns = QtWidgets.QHBoxLayout()
    btns.setSpacing(10)
    count = ui("foot_required", n=len(fields), r=n_req) if n_req else ui("foot_optional", n=len(fields))
    btns.addWidget(label(count + "  ·  " + ui("foot_keys"), "foot", wrap=False))
    btns.addStretch(1)
    cancel = QtWidgets.QPushButton(ui("cancel"))
    cancel.setAutoDefault(False)
    cancel.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    cancel.clicked.connect(dlg.reject)
    ok = QtWidgets.QPushButton(ui("save"))
    ok.setObjectName("save")
    ok.setDefault(True)
    ok.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    ok.clicked.connect(save)
    btns.addWidget(cancel)
    btns.addWidget(ok)
    outer.addLayout(btns)
    rows[0][1].setFocus()

    try:
        if shot:  # test hook: render off-screen to an image instead of showing a window
            answers = json.loads(os.environ.get("PRIVFILL_FORM_ANSWERS") or "{}")
            for f, w, get, mark, tip in rows:
                if f["name"] in answers:
                    (w.setCurrentText if f["type"] == "choice" else w.setText)(str(answers[f["name"]]))
            if answers:
                save()
            dlg.adjustSize()
            dlg.grab().save(shot)
            return None
        timeout = float(os.environ.get("PRIVFILL_DIALOG_TIMEOUT") or 0)
        if timeout:
            QtCore.QTimer.singleShot(int(timeout * 1000), dlg.reject)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        dlg.exec()
        return result["out"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def tk_form(spec, data):
    """Plain fallback when PyQt6 is not available."""
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(spec.get("title") or ui("title"))
    root.attributes("-topmost", True)
    pad = {"padx": 18}
    tk.Label(root, text=spec.get("title") or ui("title"), font=("", 13, "bold"), **pad).pack(anchor="w", pady=(14, 2))
    if spec.get("note"):
        tk.Label(root, text=spec["note"], wraplength=460, justify="left", **pad).pack(anchor="w")
    tk.Label(root, text=ui("privacy", file=DATA_NAME), fg="#166534", wraplength=460, justify="left", **pad).pack(anchor="w", pady=(2, 8))
    widgets = []
    for f in spec["fields"]:
        name = f["name"]
        if f.get("section"):
            tk.Label(root, text=f["section"], fg="#2563eb", font=("", 10, "bold"), **pad).pack(anchor="w", pady=(10, 0))
        text = f.get("label") or name
        if text != name:
            text = f"{text} ({name})"
        if f.get("required"):
            text += "  [" + ui("required") + "]"
        tk.Label(root, text=text, **pad).pack(anchor="w", pady=(6, 0))
        var = tk.StringVar(value=data.get(name, ""))
        if f["type"] == "choice":
            w = ttk.Combobox(root, textvariable=var, values=f["choices"], state="readonly", width=44)
        else:
            w = tk.Entry(root, textvariable=var, width=46)
        w.pack(anchor="w", **pad)
        tip = f.get("hint") or ""
        if name in data:
            tip = (tip + "  " if tip else "") + ui("keep")
        hint = tk.Label(root, text=tip, fg="#666", wraplength=460, justify="left", **pad)
        hint.pack(anchor="w")
        widgets.append((f, var, hint, tip, w))
    result = {"out": None}

    def ok(_=None):
        out, first_bad = {}, None
        for f, var, hint, tip, w in widgets:
            raw = var.get().strip()
            try:
                if raw:
                    out[f["name"]] = normalize(f, raw)
                elif f.get("required"):
                    raise Bad(ui("bad_required"))
                hint.config(text=tip, fg="#666")
            except Bad as e:
                hint.config(text=str(e), fg="#c0392b")
                first_bad = first_bad or w
        if first_bad is not None:
            first_bad.focus_set()
            return
        result["out"] = out
        root.destroy()

    row = tk.Frame(root)
    row.pack(pady=14)
    tk.Button(row, text=ui("save"), width=10, command=ok).pack(side="left", padx=6)
    tk.Button(row, text=ui("cancel"), width=10, command=root.destroy).pack(side="left", padx=6)
    root.bind("<Return>", ok)
    root.bind("<Escape>", lambda _: root.destroy())
    widgets[0][4].focus_set()
    timeout = float(os.environ.get("PRIVFILL_DIALOG_TIMEOUT") or 0)
    if timeout:
        root.after(int(timeout * 1000), root.destroy)
    root.mainloop()
    return result["out"]


STATUS_TEXT = {"created": "stored", "updated": "updated", "unchanged": "unchanged", "skipped": "left empty"}


def cmd_form(spec, data):
    try:
        out = run_form(spec, data)
    except Bad as e:
        print(f"Failed: {e}")
        return 1
    except Exception as e:
        print(f"Failed: could not show the form ({type(e).__name__}).")
        return 1
    if out is None:
        print("Cancelled by the person; nothing was changed.")
        return 3
    changed = {k: v for k, v in out.items() if data.get(k) != v}
    updated = save_items(changed) if changed else {}
    now = load()
    rc, items = 0, []
    for f in spec["fields"]:
        n = f["name"]
        note = ""
        if n in changed:
            code = "updated" if updated[n] else "created"
            if now.get(n) != changed[n]:
                rc, note = 1, " (read-back check FAILED)"
        elif n in out:
            code = "unchanged"
        else:
            code = "skipped"
            note = " (an earlier value is kept)" if n in data else ""
        placeholder = "{{" + n + "}}"
        items.append({"name": n, "status": code, "type": f["type"], "placeholder": placeholder, "stored": n in now})
        print(f"{n}: {STATUS_TEXT[code]}{note}; type {f['type']}; placeholder {placeholder}")
    RESULT.update(items=items)
    return rc


# ------------------------------------------------------------------ filling
class Missing(Exception):
    pass


def apply_format(value, fmt):
    fmt = fmt.strip()
    m = re.fullmatch(r"(first|last)\s*(\d+)", fmt, flags=re.I) or re.fullmatch(r"([前后])(\d+)位", fmt)
    if m:
        n = int(m.group(2))
        return value[:n] if m.group(1).lower() in ("first", "前") else value[-n:]
    d = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value)
    if d and (fmt == "年月日" or re.search(r"YYYY|MM|DD|M|D", fmt)):
        y, mo, da = d.groups()
        if fmt == "年月日":
            fmt = "YYYY年M月D日"
        out = fmt.replace("YYYY", y).replace("MM", "\0").replace("DD", "\1")
        out = out.replace("M", str(int(mo))).replace("D", str(int(da)))
        return out.replace("\0", mo).replace("\1", da)
    raise Missing(f"format '{fmt}' does not apply to this item (date formats need a date item)")


def sub(text, data, enc=lambda s: s, counter=None):
    def rep(m):
        key, _, fmt = m.group(1).partition("|")
        key = key.strip()
        if key not in data:
            raise Missing(f"there is no item named '{key}'")
        v = apply_format(data[key], fmt) if fmt else data[key]
        if counter is not None:
            counter[key] = counter.get(key, 0) + 1
        return enc(v)

    return PH.sub(rep, text)


def walk(obj, data, counter):
    if isinstance(obj, str):
        return sub(obj, data, counter=counter)
    if isinstance(obj, list):
        return [walk(x, data, counter) for x in obj]
    if isinstance(obj, dict):
        return {k: walk(v, data, counter) for k, v in obj.items()}
    return obj


def fill_file(path, data):
    counter = {}
    low = path.lower()
    if low.endswith(OFFICE):
        with zipfile.ZipFile(path) as zin:
            items = [(i, zin.read(i.filename)) for i in zin.infolist()]
        out = []
        for info, raw in items:
            if info.filename.endswith((".xml", ".rels")):
                raw = sub(raw.decode("utf8"), data, enc=escape, counter=counter).encode("utf8")
            out.append((info, raw))

        def w(tmp):
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                for info, raw in out:
                    zout.writestr(info, raw, compress_type=zipfile.ZIP_DEFLATED)
    elif low.endswith((".ipynb", ".json")):
        with open(path, encoding="utf8") as f:
            obj = walk(json.load(f), data, counter)

        def w(tmp):
            with open(tmp, "w", encoding="utf8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=1)
                f.write("\n")
    else:
        with open(path, encoding="utf8") as f:
            text = sub(f.read(), data, counter=counter)

        def w(tmp):
            with open(tmp, "w", encoding="utf8") as f:
                f.write(text)

    if counter:
        atomic_write(path, w)
    return counter


def used(counter):
    return ", ".join(f"{k} x{n}" for k, n in counter.items())


def available(data):
    return "Available items: " + (", ".join(data) or "(none yet)") + "."


def run_cmd(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = argv[1]
    if cmd in ("--version", "version"):
        print(f"privfill {__version__} (entry protocol v{PROTOCOL_VERSION})")
        RESULT.update(version=__version__, protocol=PROTOCOL_VERSION)
        return 0
    if cmd == "protocol":
        print(PROTOCOL)
        RESULT.update(protocol=PROTOCOL_VERSION, types=list(NORMS), field_keys=sorted(FIELD_KEYS))
        return 0
    if cmd == "init":
        created = ensure_data_file()
        os.chmod(DATA, 0o600)
        print("Created the data file." if created else "The data file already exists; left untouched.")
        RESULT.update(created=created)
        return 0
    data = load()
    if cmd == "keys":
        print(available(data))
        RESULT.update(items=list(data))
        return 0
    if cmd in ("form", "add"):
        if len(argv) != 3:
            print("Usage: form '<protocol JSON>' | form <file> | add <item>. Answers are typed into the form, never on the command line.")
            return 1
        text = argv[2] if cmd == "form" else json.dumps({"fields": [{"name": argv[2], "required": True}]})
        spec, err = parse_spec(text)
        if err:
            print(f"Invalid protocol: {err}. Run `privfill.py protocol` for the format.")
            return 4
        return cmd_form(spec, data)
    if cmd == "fill":
        if len(argv) < 3:
            print("Usage: fill <file>...")
            return 1
        rc = 0
        for i, path in enumerate(argv[2:], 1):  # file names may hold personal details: refer to files by index
            try:
                c = fill_file(path, data)
                RESULT.setdefault("files", []).append({"index": i, "ok": True, "used": c})
                print(f"File {i}: " + (f"filled {used(c)}." if c else "no placeholders; left untouched."))
            except Missing as e:
                RESULT.setdefault("files", []).append({"index": i, "ok": False, "used": {}})
                print(f"File {i}: failed, {e}; left untouched. {available(data)}")
                rc = 2
            except Exception as e:
                RESULT.setdefault("files", []).append({"index": i, "ok": False, "used": {}})
                print(f"File {i}: failed ({type(e).__name__}); left untouched.")
                rc = rc or 1
        return rc
    if cmd == "render":
        if len(argv) != 4:
            print("Usage: render <master file> <output path, may contain placeholders>")
            return 1
        src = argv[2]
        try:
            dst = sub(argv[3], data)
        except Missing as e:
            print(f"Failed: {e}; nothing was written. {available(data)}")
            return 2
        if os.path.abspath(dst) == os.path.abspath(src):
            print("Failed: the output path is the master itself; nothing was written.")
            return 1
        try:
            shutil.copy2(src, dst)
            c = fill_file(dst, data)
        except Missing as e:
            os.unlink(dst)
            print(f"Failed: {e}; nothing was written. {available(data)}")
            return 2
        except Exception as e:
            if os.path.exists(dst) and os.path.abspath(dst) != os.path.abspath(src):
                os.unlink(dst)
            print(f"Failed ({type(e).__name__}); nothing was written.")
            return 1
        RESULT.update(used=c)
        print("Output written; the master is untouched. " + (f"Filled {used(c)}." if c else "The master has no placeholders."))
        return 0
    if cmd == "run":
        args = argv[2:]
        if args and args[0] == "--":
            args = args[1:]
        if not args:
            print("Usage: run -- <command> [args]...")
            return 1
        counter = {}
        try:
            real = [a[4:] if a.startswith("raw:") else sub(a, data, counter=counter) for a in args]
        except Missing as e:
            print(f"Failed: {e}; the command was not run. {available(data)}")
            return 2
        try:
            p = subprocess.run(real, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            print(f"Failed: the command could not be started ({type(e).__name__}).")
            return 1
        RESULT.update(used=counter, exit_code=p.returncode)
        print(f"Command ran, exit code {p.returncode}. Items used: {used(counter) or 'none'}.")
        return p.returncode
    print("Unknown subcommand. Available: init, keys, protocol, form, add, render, fill, run.")
    return 1


def main(argv):
    if "--json" in argv[1:2]:
        argv = argv[:1] + argv[2:]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run_cmd(argv)
        out = {"ok": code == 0, "code": code, "message": buf.getvalue().strip()}
        out.update(RESULT)
        print(json.dumps(out, ensure_ascii=False))
        return code
    return run_cmd(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv))

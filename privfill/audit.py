"""Find stored values in files, locally, without ever reporting a value or a path.

Each file is read as text (Word, Excel and PowerPoint files through the text of their XML parts)
and looked at in three normalized views: Unicode NFKC with case folded (full-width characters and
case stop mattering), the same with whitespace runs collapsed, and the same with separators removed
(spaces, dashes, dots, slashes, brackets, commas, plus signs), which is how phone and identity
numbers are written in the wild. Dates are matched by a pattern over their common written forms.
A file name is checked as well, since a name can hold a value.

Results name items and counts; locations are numbered in scan order. Full paths are shown only in a
local window (show_window), never printed.
"""
import os
import re
import unicodedata
import zipfile

MIN_LENGTH = 4  # values shorter than this are not searched: they would match everywhere
MAX_BYTES = 50 * 1024 * 1024  # bigger files are skipped
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv"}
OFFICE = (".docx", ".docm", ".dotx", ".xlsx", ".xlsm", ".xltx", ".pptx", ".pptm", ".potx")
SEPARATORS = re.compile(r"[\s\-\.\(\)/,_+·・—–]+")
TAGS = re.compile(r"<[^>]+>")
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
FOUND = 6  # exit code: at least one value was found


def fold(text):
    """NFKC with case folded and whitespace runs collapsed."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold())


def compact(text):
    return SEPARATORS.sub("", text)


def date_pattern(value):
    """A pattern for the written forms of a YYYY-MM-DD date, or None if the value is not one."""
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value)
    if not m:
        return None
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    M, D = f"0?{mo}", f"0?{d}"
    sep = r"\s*[-/.年月]\s*"
    month = MONTHS[mo - 1] if 1 <= mo <= 12 else "x"
    name = rf"{month[:3]}[a-z]*\.?"
    forms = [
        rf"{y}{sep}{M}{sep}{D}\s*日?",          # 2005-3-4, 2005/03/04, 2005年3月4日
        rf"{D}{sep}{M}{sep}{y}",                # 4.3.2005
        rf"{M}{sep}{D}{sep}{y}",                # 03/04/2005
        rf"{y}{mo:02d}{d:02d}",                 # 20050304
        rf"{name}\s+{D}(?:st|nd|rd|th)?,?\s+{y}",  # March 4, 2005
        rf"{D}(?:st|nd|rd|th)?\s+{name},?\s+{y}",  # 4 March 2005
    ]
    return re.compile(r"(?<!\d)(?:" + "|".join(forms) + r")(?!\d)")


class Needle:
    """How one item is looked for."""

    def __init__(self, name, type_, value):
        self.name = name
        self.date = date_pattern(value) if type_ == "date" else None
        self.folded = fold(value).strip()
        self.compact = compact(self.folded)

    def length(self):
        return len(self.compact or self.folded)

    def count(self, views):
        folded, packed = views
        if self.date:
            return len(self.date.findall(folded))
        hits = folded.count(self.folded) if self.folded else 0
        if self.compact:
            hits = max(hits, packed.count(self.compact))
        return hits


def needles(store, min_length):
    """(the needles, the names of items too short to search). Values stay inside the needles."""
    entries = store.entries() if hasattr(store, "entries") else [(n, "text") for n in store.names()]
    found, short = [], []
    for name, type_ in entries:
        value = store.read(name)
        if not value:
            continue
        n = Needle(name, type_, value)
        if n.date is None and n.length() < min_length:
            short.append(name)
        else:
            found.append(n)
    return found, short


def office_text(path):
    """The text of every XML part of an Office file, tags removed; runs of one paragraph come out joined."""
    parts = []
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if info.filename.endswith(".xml") and info.file_size < MAX_BYTES:
                xml = z.read(info).decode("utf8", "replace")
                xml = re.sub(r"</w:p>|</a:p>|</si>|</c>|<w:tab/>|<w:br/>", "\n", xml)
                parts.append(TAGS.sub("", xml))
    text = "\n".join(parts)
    for entity, char in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&")):
        text = text.replace(entity, char)
    return text


def json_strings(raw):
    """Every string (keys included) in a JSON or JSON Lines document, unescaped; None if it is neither.
    Transcripts store text as JSON strings, where non-ASCII characters may be written as \\uXXXX."""
    import json

    out = []

    def collect(node):
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, dict):
            for k, v in node.items():
                out.append(k)
                collect(v)
        elif isinstance(node, list):
            for v in node:
                collect(v)

    text = raw.decode("utf8", "replace")
    try:
        collect(json.loads(text))
    except ValueError:
        try:
            for line in text.splitlines():
                if line.strip():
                    collect(json.loads(line))
        except ValueError:
            return None
    return "\n".join(out)


def file_text(path):
    """The text of a file, or None if it is binary, too big or unreadable."""
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        if path.lower().endswith(OFFICE) and zipfile.is_zipfile(path):
            return office_text(path)
        with open(path, "rb") as f:
            raw = f.read()
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError):
        return None
    for bom, codec in ((b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if raw.startswith(bom):
            return raw.decode(codec, "replace")
    if b"\x00" in raw[:8192]:
        return None
    if path.lower().endswith((".json", ".jsonl", ".ipynb")):
        strings = json_strings(raw)
        if strings is not None:
            return strings
    return raw.decode("utf8", "replace")


def views(text):
    folded = fold(text)
    return folded, compact(folded)


def walk(paths, suffix=None):
    """Every file under the given paths (only those ending in suffix, if given), in a stable order.
    Unreadable directories are passed over."""
    for p in paths:
        if os.path.isfile(p):
            yield p
        elif os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                for f in sorted(files):
                    if suffix is None or f.endswith(suffix):
                        yield os.path.join(root, f)


def audit(paths, store, min_length=MIN_LENGTH, skip=(), suffix=None):
    """Scan the paths. Returns a dict: locations [(path, {item: count})] of files with a find (paths
    stay with the caller of this function, never printed), totals {item: count}, scanned, skipped
    (binary, too big or unreadable), short (items not searched)."""
    found, short = needles(store, min_length)
    skip = {os.path.realpath(s) for s in skip}
    locations, totals, scanned, skipped = [], {}, 0, 0
    for path in walk(paths, suffix):
        if os.path.realpath(path) in skip:
            continue
        text = file_text(path)
        hits = {}
        name_views = views(os.path.basename(path))
        text_views = views(text) if text is not None else None
        for n in found:
            c = n.count(name_views) + (n.count(text_views) if text_views else 0)
            if c:
                hits[n.name] = c
        if text is None:
            skipped += 1
        else:
            scanned += 1
        if hits:
            locations.append((path, hits))
            for k, c in hits.items():
                totals[k] = totals.get(k, 0) + c
    return {"locations": locations, "totals": totals, "scanned": scanned, "skipped": skipped, "short": short}


AUDIT_SHOT = "PRIVFILL_AUDIT_SHOT"  # test hook: draw the window off-screen into this picture and return


def show_window(locations):
    """List each numbered location with its full path and what was found there, in a local window."""
    from .form import have_qt
    from .texts import ui

    if not have_qt():
        raise RuntimeError("the audit window needs PyQt6")
    from PyQt6 import QtWidgets

    shot = os.environ.get(AUDIT_SHOT)
    if shot and not os.environ.get("PRIVFILL_VAULT"):
        raise RuntimeError(f"{AUDIT_SHOT} requires PRIVFILL_VAULT")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["privfill"])
    app.setStyle("Fusion")
    w = QtWidgets.QWidget()
    w.setWindowTitle(ui("audit_title"))
    layout = QtWidgets.QVBoxLayout(w)
    note = QtWidgets.QLabel(ui("audit_note"))
    note.setWordWrap(True)
    layout.addWidget(note)
    table = QtWidgets.QTableWidget(len(locations), 3)
    table.setHorizontalHeaderLabels([ui("audit_index"), ui("audit_path"), ui("audit_items")])
    for row, (path, hits) in enumerate(locations):
        for col, text in enumerate((str(row + 1), path, ", ".join(f"{k} ×{c}" for k, c in hits.items()))):
            item = QtWidgets.QTableWidgetItem(text)
            item.setFlags(item.flags() & ~QtCore_editable())
            table.setItem(row, col, item)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(True)
    table.resizeColumnsToContents()
    layout.addWidget(table)
    w.resize(900, 420)
    if shot:
        w.grab().save(shot)
        return 0
    w.show()
    w.raise_()
    w.activateWindow()
    app.exec()
    return 0


def QtCore_editable():
    from PyQt6 import QtCore

    return QtCore.Qt.ItemFlag.ItemIsEditable

"""Placeholders in the fields of a PDF form.

A PDF is filled through its form: a placeholder may stand in the value of any field that holds text
(`/V`, and the default `/DV` that a reset goes back to). Text printed on the page itself is not
touched: it is drawn glyph by glyph in fonts that hold only the characters the page needed.

The value of a filled field is drawn by the PDF reader, not by privfill: the field's old picture
(its appearance stream) is dropped and the form is marked `NeedAppearances`, so the reader lays the
value out in a font that has its characters. privfill does not draw it itself because the form's
font often lacks them (a Latin font for a Chinese name).

Needs the pypdf library (Arch: `pacman -S python-pypdf`). Everything else in privfill works without it.
"""
import contextlib
import logging
import warnings

from .filling import PH, Unsupported, sub

NEED_PYPDF = ("PDF forms need the pypdf library, which is not installed. "
              "Install it for the Python that runs privfill (on Arch: sudo pacman -S python-pypdf).")


@contextlib.contextmanager
def quiet():
    """pypdf logs and warns with document text in the message (a field value it could not encode,
    for one), and a filled field holds a value. Nothing it says may reach the output."""
    log = logging.getLogger("pypdf")
    disabled = log.disabled
    log.disabled = True
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            yield
    finally:
        log.disabled = disabled


def pypdf():
    try:
        import pypdf as lib
    except ImportError:
        raise Unsupported(NEED_PYPDF) from None
    return lib


def nodes(fields, seen=None):
    """Every node of the field tree (fields and their widgets), each once."""
    seen = set() if seen is None else seen
    for ref in fields or ():
        node = ref.get_object()
        if id(node) in seen:
            continue
        seen.add(id(node))
        yield node
        yield from nodes(node.get("/Kids"), seen)


def widgets(field):
    """The widgets that show a field: the field itself if it is one, and every widget below it."""
    for node in nodes([field]):
        if node.get("/Subtype") == "/Widget":
            yield node


def has_placeholder(v):
    return isinstance(v, str) and PH.search(v) is not None


def fill(path, store, counter):
    """None if the PDF has no placeholder in a form field; else a function that writes the filled
    PDF to the path it is given. Raises Missing before anything is written if an item is missing."""
    lib = pypdf()
    from pypdf.generic import BooleanObject, NameObject, TextStringObject

    with quiet():
        reader = lib.PdfReader(path)
        if reader.is_encrypted:
            raise Unsupported("the PDF is encrypted; privfill fills only PDFs without a password.")
        form = reader.trailer["/Root"].get("/AcroForm")
        if form is None or not any(has_placeholder(n.get(k)) for n in nodes(form.get_object().get("/Fields"))
                                   for k in ("/V", "/DV")):
            return None
        writer = lib.PdfWriter(clone_from=reader)
        form = writer._root_object["/AcroForm"].get_object()
        shown = []
        for node in nodes(form.get("/Fields")):
            for key in ("/V", "/DV"):
                v = node.get(key)
                if has_placeholder(v):
                    node[NameObject(key)] = TextStringObject(sub(str(v), store, counter=counter))
                    if key == "/V":
                        shown.append(node)
        for field in shown:
            for w in widgets(field):
                if "/AP" in w:
                    del w["/AP"]
        form[NameObject("/NeedAppearances")] = BooleanObject(True)

    def write(tmp):
        with quiet(), open(tmp, "wb") as f:
            writer.write(f)

    return write


def field_text(path):
    """The text of every field value of a PDF form, one per line; None if pypdf is missing or the
    file cannot be read as a PDF. For the audit."""
    try:
        lib = pypdf()
    except Unsupported:
        return None
    try:
        with quiet():
            reader = lib.PdfReader(path)
            if reader.is_encrypted:
                return None
            form = reader.trailer["/Root"].get("/AcroForm")
            if form is None:
                return ""
            return "\n".join(str(n.get(k)) for n in nodes(form.get_object().get("/Fields"))
                             for k in ("/V", "/DV") if isinstance(n.get(k), str))
    except Exception:  # noqa: BLE001 - a damaged file is simply not readable here
        return None

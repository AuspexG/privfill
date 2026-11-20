"""Put item values in place of placeholders, in text and in files."""
import json
import re
import zipfile
from xml.sax.saxutils import escape

from .atomic import atomic_write

PH = re.compile(r"\{\{\s*([^{}\s][^{}]*?)\s*\}\}")
OFFICE = (".docx", ".xlsx", ".pptx")


class Unsupported(Exception):
    """A file privfill cannot fill. The message is fixed text and never holds a value or a file's content."""


class Missing(Exception):
    """A placeholder names an item the store does not have, or a format that does not apply.

    The name or format was copied from a document, and a document that was filled before holds
    values where it held placeholders. So that text is kept apart from the message: str() never
    includes it, and describe() includes it only if the caller's check allows.
    """

    HIDDEN = "(not shown: it may hold a value)"

    def __init__(self, reason, quoted):
        """reason has one {} where quoted, the text copied from the document, belongs."""
        super().__init__(reason.format(self.HIDDEN))
        self.reason, self.quoted = reason, quoted

    def describe(self, may_show):
        """The message, quoting the document text only if may_show(text) is true."""
        return self.reason.format(f"'{self.quoted}'" if may_show(self.quoted) else self.HIDDEN)


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
    raise Missing("format {} does not apply to this item (date formats need a date item)", fmt)


def resolve(m, store, counter=None):
    """The value for one placeholder match, read from the store."""
    key, _, fmt = m.group(1).partition("|")
    key = key.strip()
    value = store.read(key)
    if value is None:
        raise Missing("there is no item named {}", key)
    v = apply_format(value, fmt) if fmt else value
    if counter is not None:
        counter[key] = counter.get(key, 0) + 1
    return v


def sub(text, store, enc=lambda s: s, counter=None, pattern=PH):
    """Fill the placeholders in text with items read from the store, one read per placeholder."""
    return pattern.sub(lambda m: enc(resolve(m, store, counter)), text)


# Office documents. A word processor splits visible text into several runs whenever formatting,
# spell-check or editing history intervenes, so a placeholder is looked for in the joined text of
# each paragraph, not in the raw XML. The XML is edited as text, never re-serialized, so everything
# that holds no placeholder keeps its bytes.
BLOCKS = "p|si|is|rPh|text"  # a Word or slide paragraph, a shared or inline workbook string, its phonetic text, a comment
XML_TOKEN = re.compile(
    rf"<(?:\w+:)?(?:{BLOCKS})(?=[\s/>])[^>]*>"  # a block opens (or is empty: <w:p/>)
    rf"|(?P<close></(?:\w+:)?(?:{BLOCKS})\s*>)"
    r"|<(?P<tag>(?:(?P<prefix>\w+):)?t)(?P<attrs>(?:\s[^>]*)?)(?<!/)>(?P<body>[^<]*)</(?P=tag)\s*>",  # a text node
    re.S)
PH_MARKUP = re.compile(r"\{\{\s*([^{}<\s][^{}<]*?)\s*\}\}")  # outside text nodes: never across a tag
XML_ENTITY = re.compile(r"&(?:#(\d+)|#x([0-9a-fA-F]+)|(lt|gt|amp|quot|apos));")
XML_UNFIT = re.compile("[^\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")  # outside XML 1.0's Char
XML_NAMED = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}


def xml_unescape(s):
    return XML_ENTITY.sub(lambda m: chr(int(m.group(1))) if m.group(1) else chr(int(m.group(2), 16)) if m.group(2)
                          else XML_NAMED[m.group(3)], s)


def xml_escape(s):
    """Escaped for XML text and attribute values alike, without the characters XML cannot carry even escaped."""
    return escape(XML_UNFIT.sub("", s), {'"': "&quot;", "\r": "&#13;"})  # a bare CR would be read as LF


def fill_block(nodes, store, counter):
    """Fill the placeholders in the joined text of one block. nodes: [match, new text or None]."""
    texts = [xml_unescape(m.group("body")) for m, _ in nodes]
    starts = [0]
    for t in texts:
        starts.append(starts[-1] + len(t))
    hits = [(m.start(), m.end(), resolve(m, store, counter)) for m in PH.finditer("".join(texts))]
    for s, e, value in reversed(hits):  # right to left, so earlier offsets stay valid
        first = True
        for i, node in enumerate(nodes):
            lo, hi = max(s, starts[i]), min(e, starts[i + 1])
            if lo >= hi:
                continue
            cur = texts[i] if node[1] is None else node[1]
            # The value goes where the placeholder begins and takes that run's formatting.
            node[1] = cur[:lo - starts[i]] + (value if first else "") + cur[hi - starts[i]:]
            first = False


def text_node(m, text):
    attrs = m.group("attrs")
    # Word and Excel drop leading and trailing spaces unless told otherwise; slide text (a:t) keeps them.
    if text != text.strip() and m.group("prefix") != "a" and "xml:space" not in attrs:
        attrs += ' xml:space="preserve"'
    return f"<{m.group('tag')}{attrs}>{xml_escape(text)}</{m.group('tag')}>"


def sub_office_xml(xml, store, counter):
    """Fill one XML part: paragraph by paragraph for visible text, plainly for the rest (link targets, properties)."""
    parts, stack, pos = [], [[]], 0  # parts: markup strings and [match, new text] text nodes
    for m in XML_TOKEN.finditer(xml):
        if m.group("tag"):
            parts.append(xml[pos:m.start()])
            pos = m.end()
            node = [m, None]
            parts.append(node)
            if len(stack) == 1:  # text outside any block stands alone
                fill_block([node], store, counter)
            else:
                stack[-1].append(node)
        elif m.group("close"):
            if len(stack) > 1:
                fill_block(stack.pop(), store, counter)
        elif not m.group().endswith("/>"):
            stack.append([])
    for nodes in stack[1:]:
        fill_block(nodes, store, counter)
    parts.append(xml[pos:])
    return "".join(sub(p, store, xml_escape, counter, PH_MARKUP) if isinstance(p, str)
                   else p[0].group() if p[1] is None else text_node(p[0], p[1]) for p in parts)


def walk(obj, store, counter):
    if isinstance(obj, str):
        return sub(obj, store, counter=counter)
    if isinstance(obj, list):
        return [walk(x, store, counter) for x in obj]
    if isinstance(obj, dict):
        return {k: walk(v, store, counter) for k, v in obj.items()}
    return obj


def fill_file(path, store):
    counter = {}
    low = path.lower()
    if low.endswith(OFFICE):
        with zipfile.ZipFile(path) as zin:
            items = [(i, zin.read(i.filename)) for i in zin.infolist()]
        out = []
        for info, raw in items:
            if info.filename.endswith((".xml", ".rels")):
                text = raw.decode("utf8")
                filled = sub_office_xml(text, store, counter)
                if filled != text:
                    raw = filled.encode("utf8")
            out.append((info, raw))

        def w(tmp):
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                for info, raw in out:
                    zout.writestr(info, raw, compress_type=zipfile.ZIP_DEFLATED)
    elif low.endswith(".pdf"):
        from .pdfform import fill as fill_pdf

        w = fill_pdf(path, store, counter)
        if w is None:
            return counter
    elif low.endswith((".ipynb", ".json")):
        with open(path, encoding="utf8") as f:
            obj = walk(json.load(f), store, counter)

        def w(tmp):
            with open(tmp, "w", encoding="utf8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=1)
                f.write("\n")
    else:
        with open(path, "rb") as f:
            raw = f.read()
        try:
            text = raw.decode("utf8")
        except UnicodeDecodeError:
            # Not text: an image, an archive, a font. It is left as it is,
            # so render still gives it a name. One that seems to hold a placeholder is refused
            # rather than passed on unfilled.
            if PH.search(raw.decode("latin-1")):
                raise Unsupported("the file is not UTF-8 text, but seems to hold a placeholder; "
                                  "privfill fills text, Office, notebook, JSON and PDF form files.") from None
            return counter
        text = sub(text.replace("\r\n", "\n").replace("\r", "\n"), store, counter=counter)  # as a text-mode read

        def w(tmp):
            with open(tmp, "w", encoding="utf8") as f:
                f.write(text)

    if counter:
        atomic_write(path, w)
    return counter

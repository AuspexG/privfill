"""Put item values in place of placeholders, in text and in files."""
import json
import re
import zipfile
from xml.sax.saxutils import escape

from .atomic import atomic_write

PH = re.compile(r"\{\{\s*([^{}\s][^{}]*?)\s*\}\}")
OFFICE = (".docx", ".xlsx", ".pptx")


class Missing(Exception):
    """A placeholder names an item the store does not have, or a format that does not apply."""


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


def sub(text, store, enc=lambda s: s, counter=None):
    """Fill the placeholders in text with items read from the store, one read per placeholder."""
    def rep(m):
        key, _, fmt = m.group(1).partition("|")
        key = key.strip()
        value = store.read(key)
        if value is None:
            raise Missing(f"there is no item named '{key}'")
        v = apply_format(value, fmt) if fmt else value
        if counter is not None:
            counter[key] = counter.get(key, 0) + 1
        return enc(v)

    return PH.sub(rep, text)


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
                raw = sub(raw.decode("utf8"), store, enc=escape, counter=counter).encode("utf8")
            out.append((info, raw))

        def w(tmp):
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                for info, raw in out:
                    zout.writestr(info, raw, compress_type=zipfile.ZIP_DEFLATED)
    elif low.endswith((".ipynb", ".json")):
        with open(path, encoding="utf8") as f:
            obj = walk(json.load(f), store, counter)

        def w(tmp):
            with open(tmp, "w", encoding="utf8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=1)
                f.write("\n")
    else:
        with open(path, encoding="utf8") as f:
            text = sub(f.read(), store, counter=counter)

        def w(tmp):
            with open(tmp, "w", encoding="utf8") as f:
                f.write(text)

    if counter:
        atomic_write(path, w)
    return counter

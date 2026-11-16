"""Turn what the person typed into the stored form of each type, or say why it cannot be read."""
import datetime
import re
import unicodedata

from .texts import ui


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

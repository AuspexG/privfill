"""The entry protocol: its summary for the caller and the validation of a protocol document."""
import json
import os
import re

from .normalize import NORMS
from .texts import HEADER_NAMES

PROTOCOL_VERSION = 1
FIELD_KEYS = {"name", "label", "hint", "type", "required", "choices", "pattern", "pattern_hint", "section", "sensitivity"}
SPEC_KEYS = {"protocol", "title", "note", "fields"}

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
      "pattern_hint": "optional: message shown to the person when pattern does not match",
      "sensitivity": "optional: low medium high; default by type (id_card high; phone, date, email medium; else low).
                      high makes render/fill/pack ask the person first. Raises a stored level, never lowers it"
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
- A high-sensitivity item (id_card by default) makes render/fill/pack ask the person in a local window;
  a refusal is exit code 7: tell the person, do not retry.
- Formats when filling: {{birthday|YYYY/MM/DD}} {{birthday|YYYYMMDD}} {{item|last4}} {{item|first6}}
"""


def parse_spec(text):
    """Validate an entry protocol. Returns (spec, None) or (None, reason)."""
    if os.path.isfile(text):
        try:
            with open(text, encoding="utf8") as f:
                text = f.read()
        except (OSError, ValueError) as e:
            return None, f"the protocol file could not be read ({type(e).__name__})"
    try:
        spec = json.loads(text)
    except json.JSONDecodeError as e:  # say where, without quoting the decoder or the text
        return None, f"the protocol is not valid JSON (line {e.lineno}, column {e.colno})"
    except ValueError:
        return None, "the protocol is not valid JSON"
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
        if "sensitivity" in f and f["sensitivity"] not in ("low", "medium", "high"):
            return None, f"field '{n}': 'sensitivity' must be low, medium or high"
        if f.get("pattern"):
            try:
                re.compile(f["pattern"])
            except re.error:
                return None, f"field '{n}': 'pattern' is not a valid regular expression"
    return spec, None

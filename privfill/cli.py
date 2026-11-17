"""The command line: runs one command and reports item names, counts and exit codes, never values."""
import contextlib
import io
import json
import os
import shutil

from . import __version__
from .filling import Missing, fill_file, sub
from .form import run_form
from .hardening import harden
from .normalize import NORMS, Bad
from .packing import PackError, pack
from .protocol import FIELD_KEYS, PROTOCOL, PROTOCOL_VERSION, parse_spec
from .store import StoreError, default_store
from .texts import USAGE

RESULT = {}  # structured extras for --json (names and statuses only, never values)
# Every command. A name that is not here cannot run, and tests/test_no_value_escapes.py refuses a
# command that has no hostile-input scenarios: add a command in both places.
COMMANDS = ("help", "version", "protocol", "init", "keys", "form", "add", "render", "fill", "pack")
FRAGMENT = 4  # this many characters of a value in a row are treated as the value
ALIASES = {"-h": "help", "--help": "help", "--version": "version"}
STATUS_TEXT = {"created": "stored", "updated": "updated", "unchanged": "unchanged", "skipped": "left empty"}


def read_items(store, names):
    """{name: value} for those of names the store has."""
    found = ((n, store.read(n)) for n in names)
    return {n: v for n, v in found if v is not None}


def cmd_form(spec, store):
    names = [f["name"] for f in spec["fields"]]
    data = read_items(store, names)
    try:
        out = run_form(spec, data, store.label)
    except Bad as e:
        print(f"Failed: {e}")
        return 1
    except Exception as e:
        print(f"Failed: could not show the form ({type(e).__name__}).")
        return 1
    if out is None:
        print("Cancelled by the person; nothing was changed.")
        return 3
    types = {f["name"]: f["type"] for f in spec["fields"]}
    changed = {k: v for k, v in out.items() if data.get(k) != v}
    updated = {k: store.write(k, v, types[k]) for k, v in changed.items()}
    now = read_items(store, names)
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


def letters(text):
    """text reduced to its letters and digits, so that spacing, case and punctuation do not hide a match."""
    return "".join(c for c in text.casefold() if c.isalnum())


def resembles_a_value(text, store):
    """Whether text copied from a document could be, or contain part of, a stored value."""
    if len(text) > 60 or not text.isprintable():
        return True
    t = letters(text)
    for name in store.names():
        v = letters(store.read(name) or "")
        if v and (v == t or any(v[i:i + FRAGMENT] in t for i in range(len(v) - FRAGMENT + 1))):
            return True
    return False


def missing(e, store):
    """What to print for a Missing: its message, without document text that resembles a value."""
    return e.describe(lambda text: not resembles_a_value(text, store))


def used(counter):
    return ", ".join(f"{k} x{n}" for k, n in counter.items())


def available(store):
    return "Available items: " + (", ".join(store.names()) or "(none yet)") + "."


def run_cmd(argv, store):
    cmd = ALIASES.get(argv[1], argv[1]) if len(argv) > 1 else "help"
    if cmd not in COMMANDS:
        print("Unknown subcommand. Available: " + ", ".join(c for c in COMMANDS if c not in ("help", "version")) + ".")
        return 1
    if cmd == "help":
        print(USAGE)
        return 0
    if cmd == "version":
        print(f"privfill {__version__} (entry protocol v{PROTOCOL_VERSION})")
        RESULT.update(version=__version__, protocol=PROTOCOL_VERSION)
        return 0
    if cmd == "protocol":
        print(PROTOCOL)
        RESULT.update(protocol=PROTOCOL_VERSION, types=list(NORMS), field_keys=sorted(FIELD_KEYS))
        return 0
    if cmd == "init":
        created = store.create()
        print("Created the vault." if created else "The vault already exists; left untouched.")
        RESULT.update(created=created)
        return 0
    if cmd == "keys":
        print(available(store))
        RESULT.update(items=store.names())
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
        return cmd_form(spec, store)
    if cmd == "fill":
        if len(argv) < 3:
            print("Usage: fill <file>...")
            return 1
        rc = 0
        for i, path in enumerate(argv[2:], 1):  # file names may hold personal details: refer to files by index
            try:
                c = fill_file(path, store)
                RESULT.setdefault("files", []).append({"index": i, "ok": True, "used": c})
                print(f"File {i}: " + (f"filled {used(c)}." if c else "no placeholders; left untouched."))
            except Missing as e:
                RESULT.setdefault("files", []).append({"index": i, "ok": False, "used": {}})
                print(f"File {i}: failed, {missing(e, store)}; left untouched. {available(store)}")
                rc = 2
            except StoreError:
                raise
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
            dst = sub(argv[3], store)
        except Missing as e:
            print(f"Failed: {missing(e, store)}; nothing was written. {available(store)}")
            return 2
        if os.path.abspath(dst) == os.path.abspath(src):
            print("Failed: the output path is the master itself; nothing was written.")
            return 1
        try:
            shutil.copy2(src, dst)
            c = fill_file(dst, store)
        except Missing as e:
            os.unlink(dst)
            print(f"Failed: {missing(e, store)}; nothing was written. {available(store)}")
            return 2
        except Exception as e:
            if os.path.exists(dst) and os.path.abspath(dst) != os.path.abspath(src):
                os.unlink(dst)
            if isinstance(e, StoreError):
                raise
            print(f"Failed ({type(e).__name__}); nothing was written.")
            return 1
        RESULT.update(used=c)
        print("Output written; the master is untouched. " + (f"Filled {used(c)}." if c else "The master has no placeholders."))
        return 0
    if cmd == "pack":
        if len(argv) < 4:
            print("Usage: pack <archive path> <file>... (every path may contain placeholders)")
            return 1
        counter = {}
        try:
            dst, *files = [sub(a, store, counter=counter) for a in argv[2:]]
        except Missing as e:
            print(f"Failed: {missing(e, store)}; nothing was written. {available(store)}")
            return 2
        try:
            pack(dst, files)
        except PackError as e:
            print(f"Failed: {e}; nothing was written.")
            return 1
        except Exception as e:
            print(f"Failed ({type(e).__name__}); nothing was written.")
            return 1
        RESULT.update(packed=len(files), used=counter)
        print(f"Archive written with {len(files)} file(s); the files are untouched. Items used: {used(counter) or 'none'}.")
        return 0
    raise AssertionError("a command is listed in COMMANDS but not handled")


def run_without_traceback(argv, store):
    """run_cmd, with any error it did not expect reduced to its type name.

    An exception's message or traceback can quote the data it was handling, so neither is ever
    printed: this is what keeps a value out of the output on the paths nobody thought of.
    A store that cannot go on (locked, damaged, unlock cancelled) is a failure it does expect: a
    StoreError's message never holds a value and is printed, with the error's own exit code.
    The store is opened here too (store=None: the default one), so that failing is covered as well.
    """
    try:
        return run_cmd(argv, default_store() if store is None else store)
    except StoreError as e:
        print(f"Failed: {e}.")
        return e.code
    except KeyboardInterrupt:
        print("Interrupted; the command did not finish.")
        return 1
    except Exception as e:
        print(f"Failed: unexpected error ({type(e).__name__}); see the exit code.")
        return 1


def main(argv, store=None):
    """Run one command. Items are read and written only through store (default: the vault)."""
    harden()  # before anything personal is read
    RESULT.clear()
    if "--json" in argv[1:2]:
        argv = argv[:1] + argv[2:]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run_without_traceback(argv, store)
        out = {"ok": code == 0, "code": code, "message": buf.getvalue().strip()}
        out.update(RESULT)
        print(json.dumps(out, ensure_ascii=False))
        return code
    return run_without_traceback(argv, store)

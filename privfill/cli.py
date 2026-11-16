"""The command line: runs one command and reports item names, counts and exit codes, never values."""
import contextlib
import io
import json
import os
import shutil

from . import __version__
from .filling import Missing, fill_file, sub
from .form import run_form
from .normalize import NORMS, Bad
from .packing import PackError, pack
from .protocol import FIELD_KEYS, PROTOCOL, PROTOCOL_VERSION, parse_spec
from .store import StoreError, default_store
from .texts import USAGE

RESULT = {}  # structured extras for --json (names and statuses only, never values)
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


def used(counter):
    return ", ".join(f"{k} x{n}" for k, n in counter.items())


def available(store):
    return "Available items: " + (", ".join(store.names()) or "(none yet)") + "."


def run_cmd(argv, store):
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(USAGE)
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
                print(f"File {i}: failed, {e}; left untouched. {available(store)}")
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
            print(f"Failed: {e}; nothing was written. {available(store)}")
            return 2
        if os.path.abspath(dst) == os.path.abspath(src):
            print("Failed: the output path is the master itself; nothing was written.")
            return 1
        try:
            shutil.copy2(src, dst)
            c = fill_file(dst, store)
        except Missing as e:
            os.unlink(dst)
            print(f"Failed: {e}; nothing was written. {available(store)}")
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
            print(f"Failed: {e}; nothing was written. {available(store)}")
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
    print("Unknown subcommand. Available: init, keys, protocol, form, add, render, fill, pack.")
    return 1


def run_checked(argv, store):
    """run_cmd, with a store that cannot go on (locked, damaged, unlock cancelled) reported as a failure."""
    try:
        return run_cmd(argv, store)
    except StoreError as e:
        print(f"Failed: {e}.")
        return e.code


def main(argv, store=None):
    """Run one command. Items are read and written only through store (default: the vault)."""
    if store is None:
        store = default_store()
    RESULT.clear()
    if "--json" in argv[1:2]:
        argv = argv[:1] + argv[2:]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run_checked(argv, store)
        out = {"ok": code == 0, "code": code, "message": buf.getvalue().strip()}
        out.update(RESULT)
        print(json.dumps(out, ensure_ascii=False))
        return code
    return run_checked(argv, store)

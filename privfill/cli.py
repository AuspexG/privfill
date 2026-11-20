"""The command line: runs one command and reports item names, counts and exit codes, never values."""
import contextlib
import hashlib
import io
import json
import os
import shutil

from . import __version__
from .activity import ActivityLog
from .filling import Missing, Unsupported, fill_file, sub
from .form import run_form
from .hardening import harden
from .guard import run as run_guard
from .normalize import NORMS, Bad
from .packing import PackError, pack
from .protocol import FIELD_KEYS, PROTOCOL, PROTOCOL_VERSION, parse_spec
from .registry import FILE_NAME, Registry, RegistryError
from .store import StoreError, default_store, vault_path
from .texts import USAGE

EVENTS = []  # (command, items, master hash) of what the running command changed; flushed to the log
LOG = [None]  # the activity log of the running command, for verify
RESULT = {}  # structured extras for --json (names and statuses only, never values)
# Every command. A name that is not here cannot run, and tests/test_no_value_escapes.py refuses a
# command that has no hostile-input scenarios: add a command in both places.
COMMANDS = ("help", "version", "protocol", "init", "keys", "form", "add", "manage", "render", "fill", "pack", "verify", "restore", "unlock", "migrate", "audit", "precommit", "guard", "mcp")
FRAGMENT = 4  # this many characters of a value in a row are treated as the value
ALIASES = {"-h": "help", "--help": "help", "--version": "version"}
STATUS_TEXT = {"created": "stored", "updated": "updated", "unchanged": "unchanged", "skipped": "left empty"}


def digest(path):
    """SHA-256 of a file's content, for the log; the path itself is never logged."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


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
    if hasattr(store, "raise_level"):  # the protocol may raise an item's sensitivity, never lower it
        for f in spec["fields"]:
            if "sensitivity" in f and f["name"] in (data.keys() | changed.keys()):
                store.raise_level(f["name"], f["sensitivity"])
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
    if rc == 0:
        EVENTS.append(("form", {i["name"]: i["status"] for i in items}, None))
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


def record_output(registry, path):
    """Record a file this command filled as an output. False if it could not be recorded."""
    try:
        if registry:
            registry.record(path)
        return True
    except (OSError, RegistryError):
        return False


UNLOCK_USAGE = ("Usage: unlock (list the methods) | unlock add keyring | unlock add passphrase | "
                "unlock change passphrase | unlock remove keyring | unlock remove passphrase")


def cmd_unlock(args, store):
    """List or change how the vault is unlocked. The data key is rewrapped; no item is re-encrypted."""
    if not hasattr(store, "methods"):
        print("This store is not unlocked.")
        return 1
    by_method = {u.method: u for u in store.unlocks}
    if not args:
        enrolled = store.methods()
        keyring = by_method.get("keyring")
        available = bool(keyring and keyring.available())
        RESULT.update(methods=enrolled, keyring_available=available)
        print("Unlocked by: " + (", ".join(enrolled) or "(no vault yet)") + ". Desktop keyring: "
              + ("available." if available else f"not available ({keyring.why_not if keyring else 'not supported'})."))
        return 0
    if len(args) != 2 or args[0] not in ("add", "change", "remove") or args[1] not in by_method \
            or (args[0] == "change" and args[1] != "passphrase"):
        print(UNLOCK_USAGE)
        return 1
    action, method = args
    enrolled = store.methods()
    if action == "add" and method == "passphrase" and method in enrolled:
        print("Failed: the vault already has a passphrase; `unlock change passphrase` replaces it.")
        return 1
    if action == "change" and method not in enrolled:
        print("Failed: the vault has no passphrase yet; `unlock add passphrase` adds one.")
        return 1
    if action == "remove":
        store.withdraw(method)
    else:
        store.enroll(by_method[method])
    EVENTS.append(("unlock", {method: action}, None))
    RESULT.update(methods=store.methods())
    print(f"Done: the vault is now unlocked by {', '.join(store.methods())}. No item was re-encrypted.")
    return 0


def cmd_migrate(args, store):
    """Move a plain-text data file of an earlier version into the vault. Prints counts, never values."""
    from .form import ask_confirm
    from .migrate import WIPE_LIMITS, copies_of, find_old_file, migrate, off_limits, wipe
    from .texts import ui

    if len(args) > 1:
        print("Usage: migrate [<old data file>]")
        return 1
    program_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    old = os.path.abspath(args[0]) if args else find_old_file(program_dir)
    if not old or not os.path.isfile(old):
        print("No plain-text data file was found; there is nothing to migrate." if not args
              else "Failed: that file does not exist.")
        RESULT.update(found=False)
        return 0 if not args else 1
    if off_limits(old):
        print("Failed: a test run may not read the person's own data directory.")
        return 1
    if os.path.abspath(old) == os.path.abspath(getattr(store, "path", "")):
        print("Failed: that is the vault itself.")
        return 1
    r = migrate(old, store)
    EVENTS.append(("migrate", dict({n: "added" for n in r["added"]}, **{n: "present" for n in r["present"]}), None))
    RESULT.update(found=True, total=r["total"], added=len(r["added"]), present=len(r["present"]),
                  conflicts=r["conflicts"], mismatched=len(r["mismatched"]))
    print(f"Old file: {r['total']} item(s). Added to the vault: {len(r['added'])}. Already there with the same value: {len(r['present'])}.")
    if r["conflicts"] or r["mismatched"]:
        if r["conflicts"]:
            print(f"Stopped: {len(r['conflicts'])} item(s) are already in the vault with a different value and were not "
                  f"changed: {', '.join(r['conflicts'])}. Decide which value is right in `privfill manage`, then run migrate again.")
        if r["mismatched"]:
            print(f"Stopped: {len(r['mismatched'])} item(s) did not read back the same from the vault.")
        print("The old file was left untouched.")
        return 1
    print(f"Verified: all {r['total']} item(s) read back from the vault exactly as in the old file.")
    copies = copies_of(old, program_dir)
    RESULT.update(copies=copies)
    if copies:
        print("These look like copies of the old file and also hold your details in plain text; remove them yourself: "
              + ", ".join(copies) + ".")
    if ask_confirm(ui("migrate_confirm")):
        wipe(old)
        RESULT.update(deleted=True)
        print("The old file was overwritten and deleted. " + WIPE_LIMITS)
    else:
        RESULT.update(deleted=False)
        print("The old file was kept, still in plain text. Once you no longer need it, run migrate again "
              "to be offered its removal, or delete it yourself. " + WIPE_LIMITS)
    return 0


AUDIT_USAGE = ("Usage: audit [--min-length N] [--window] <file or directory>... | "
               "audit --transcripts [--min-length N] [--window] [<transcript directory>]")
TRANSCRIPTS = os.path.join("~", ".claude", "projects")  # where Claude Code keeps its session transcripts


def cmd_audit(args, store):
    """Look for stored values in files. Prints item names, counts and location numbers, never a value or a path."""
    from .audit import FOUND, MIN_LENGTH, audit, show_window

    min_length, window, transcripts, paths = MIN_LENGTH, False, False, []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--window":
            window = True
        elif a == "--transcripts":
            transcripts = True
        elif a == "--min-length" and i + 1 < len(args) and args[i + 1].isdigit() and int(args[i + 1]) >= 1:
            min_length = int(args[i + 1])
            i += 1
        elif a.startswith("--"):
            print(AUDIT_USAGE)
            return 1
        else:
            paths.append(a)
        i += 1
    if transcripts and not paths:
        paths = [os.path.expanduser(TRANSCRIPTS)]
    if not paths or (transcripts and len(paths) != 1):
        print(AUDIT_USAGE)
        return 1
    missing = [n for n, p in enumerate(paths, 1) if not os.path.exists(p)]
    if missing:
        print(f"Failed: path(s) number {', '.join(map(str, missing))} do not exist; nothing was scanned.")
        return 1
    vault_file = getattr(store, "path", None)
    r = audit(paths, store, min_length, skip=[vault_file] if vault_file else [], suffix=".jsonl" if transcripts else None)
    where = "session" if transcripts else "location"  # a transcript file is one session
    RESULT.update(found=r["totals"], scanned=r["scanned"], skipped=r["skipped"], too_short=r["short"],
                  locations=[{"index": n, "items": hits} for n, (_, hits) in enumerate(r["locations"], 1)])
    print(f"Scanned {r['scanned']} {'session transcript' if transcripts else 'file'}(s); skipped {r['skipped']} (binary, too big or unreadable).")
    if r["short"]:
        print(f"Not searched, shorter than {min_length} characters: {', '.join(r['short'])}.")
    if not r["totals"]:
        print("No stored value was found.")
        return 0
    print("Found: " + ", ".join(f"{k} x{c}" for k, c in r["totals"].items()) + f", in {len(r['locations'])} {where}(s).")
    if transcripts:
        print("A transcript only shows what has already been sent; the values found there have left this machine.")
    for n, (_, hits) in enumerate(r["locations"], 1):
        print(f"  {where} {n}: " + ", ".join(f"{k} x{c}" for k, c in hits.items()))
    if window:
        try:
            show_window(r["locations"])
        except RuntimeError as e:
            print(f"The window could not be shown ({e}); `--window` needs PyQt6.")
    else:
        print("The paths are not printed, since a path can hold a value; `audit --window` shows them to the person.")
    return FOUND


def cmd_precommit(args):
    """Install or remove the git pre-commit check in a repository (default: the current directory)."""
    from .precommit import HookError, install, remove

    if not args or args[0] not in ("install", "remove") or len(args) > 2:
        print("Usage: precommit install [<repository>] | precommit remove [<repository>]")
        return 1
    repo = os.path.abspath(args[1] if len(args) == 2 else ".")
    try:
        status = (install if args[0] == "install" else remove)(repo)
    except (HookError, OSError) as e:
        print(f"Failed: {e if isinstance(e, HookError) else type(e).__name__}.")
        return 1
    RESULT.update(status=status)
    print({"installed": "The pre-commit check is installed.",
           "updated": "The pre-commit check was already installed; it is now up to date.",
           "chained": "The pre-commit check is installed; the hook that was there runs first, as before.",
           "removed": "The pre-commit check was removed.",
           "restored": "The pre-commit check was removed and the earlier hook put back.",
           "absent": "The pre-commit check was not installed here; nothing changed."}[status])
    return 0


def confirm_use(command, store, masters, output=None, files=(), texts=()):
    """None if the command may go on; else the exit code of a decline. Asks the person, in a local
    window, before an item of high sensitivity is used, unless a live grant covers the same masters."""
    from .sensitivity import DECLINED, Grants, ask, grant_key, items_used, levels

    level_of = levels(store)
    used = items_used(store, files, texts)
    high = sorted(n for n in used if level_of.get(n) == "high")
    if not high:
        return None
    key = grant_key(command, masters, high)
    grants = Grants(store)
    if grants.live(key):
        print(f"Using {', '.join(high)} (high sensitivity) under the person's earlier permission for this master.")
        return None
    answer = ask(command, masters, output, used, level_of)
    if answer == "no":
        EVENTS.append(("confirm", {n: "declined" for n in high}, None))
        RESULT.update(declined=high)
        print(f"Declined by the person: {', '.join(high)} (high sensitivity) may not be used here; nothing was written. "
              "Do not try again unless the person asks.")
        return DECLINED
    if answer == "remember":
        grants.add(key)
    EVENTS.append(("confirm", {n: "granted" for n in high}, None))
    return None


def run_cmd(argv, store, registry=None):
    cmd = ALIASES.get(argv[1], argv[1]) if len(argv) > 1 else "help"
    if cmd not in COMMANDS:
        print("Unknown subcommand. Available: " + ", ".join(c for c in COMMANDS if c not in ("help", "version")) + ".")
        return 1
    if cmd == "mcp":  # main serves it before anything is printed; a direct call has no channel to serve
        print("Usage: mcp (run as `privfill.py mcp`, by an assistant tool)")
        return 1
    if cmd == "guard":  # main answers a hook before any store is opened; this is for direct calls
        return guard(registry)
    if cmd == "help":
        print(USAGE)
        return 0
    if cmd == "version":
        print(f"privfill {__version__} (entry protocol v{PROTOCOL_VERSION})")
        RESULT.update(version=__version__, protocol=PROTOCOL_VERSION)
        return 0
    if cmd == "unlock":
        return cmd_unlock(argv[2:], store)
    if cmd == "migrate":
        return cmd_migrate(argv[2:], store)
    if cmd == "audit":
        return cmd_audit(argv[2:], store)
    if cmd == "precommit":
        return cmd_precommit(argv[2:])
    if cmd == "restore":
        if len(argv) > 3 or not hasattr(store, "backups"):
            print("Usage: restore [<version>]" if len(argv) > 3 else "This store keeps no backups.")
            return 1
        kept = store.backups()
        if len(argv) == 2:
            RESULT.update(backups=[{"version": v, "saved": t, "items": n} for v, t, n in kept])
            if not kept:
                print("No backups are kept yet.")
            for v, t, n in kept:
                print(f"version {v}: saved {t}, " + (f"{n} item(s)" if n is not None else "unreadable"))
            return 0
        if not argv[2].isdigit() or int(argv[2]) not in [v for v, _, _ in kept]:
            print("Failed: there is no backup of that version. `restore` with no argument lists them.")
            return 1
        n = store.restore(int(argv[2]))
        EVENTS.append(("restore", {}, None))
        RESULT.update(restored=n)
        print(f"Restored the backup as the current vault: {n} item(s). The vault it replaced was kept as a backup.")
        return 0
    if cmd == "verify":
        log = LOG[0]
        if log is None:
            print("This store has no activity log.")
            return 1
        ok, count, message = log.verify()
        print(f"{message[0].upper()}{message[1:]}." if ok else f"Failed: {message}.")
        RESULT.update(records=count, intact=ok)
        return 0 if ok else 1
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
    if cmd == "manage":
        if len(argv) != 2:
            print("Usage: manage")
            return 1
        from .manager_ui import run_manager
        return run_manager(store)
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
        files = argv[2:]
        declined = confirm_use("fill", store, [f for f in files if os.path.isfile(f)],
                               files=[f for f in files if os.path.isfile(f)])
        if declined:
            return declined
        rc = 0
        for i, path in enumerate(argv[2:], 1):  # file names may hold personal details: refer to files by index
            try:
                before = digest(path)
                c = fill_file(path, store)
                if c:
                    EVENTS.append(("fill", c, before))
                if c and not record_output(registry, path):
                    RESULT.setdefault("files", []).append({"index": i, "ok": False, "used": c})
                    print(f"File {i}: filled {used(c)}, but not registered as an output; the guard does not protect it.")
                    rc = rc or 1
                    continue
                RESULT.setdefault("files", []).append({"index": i, "ok": True, "used": c})
                print(f"File {i}: " + (f"filled {used(c)}." if c else "no placeholders; left untouched."))
            except Missing as e:
                RESULT.setdefault("files", []).append({"index": i, "ok": False, "used": {}})
                print(f"File {i}: failed, {missing(e, store)}; left untouched. {available(store)}")
                rc = 2
            except StoreError:
                raise
            except Unsupported as e:  # fixed text, never a value
                RESULT.setdefault("files", []).append({"index": i, "ok": False, "used": {}})
                print(f"File {i}: failed, {e} Left untouched.")
                rc = rc or 1
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
        counter = {}
        try:
            dst = sub(argv[3], store, counter=counter)
        except Missing as e:
            print(f"Failed: {missing(e, store)}; nothing was written. {available(store)}")
            return 2
        if os.path.abspath(dst) == os.path.abspath(src):
            print("Failed: the output path is the master itself; nothing was written.")
            return 1
        if os.path.isfile(src):
            declined = confirm_use("render", store, [src], output=dst, files=[src], texts=[argv[3]])
            if declined:
                return declined
        try:
            before = digest(src)
            shutil.copy2(src, dst)
            c = fill_file(dst, store)
            if not record_output(registry, dst):
                raise RegistryError("not registered")  # an output that cannot be registered is not kept
        except Missing as e:
            os.unlink(dst)
            print(f"Failed: {missing(e, store)}; nothing was written. {available(store)}")
            return 2
        except Exception as e:
            if os.path.exists(dst) and os.path.abspath(dst) != os.path.abspath(src):
                os.unlink(dst)
            if isinstance(e, StoreError):
                raise
            if isinstance(e, Unsupported):  # fixed text, never a value
                print(f"Failed: {e} Nothing was written.")
                return 1
            print(f"Failed ({type(e).__name__}); nothing was written.")
            return 1
        for k, n in c.items():
            counter[k] = counter.get(k, 0) + n
        EVENTS.append(("render", counter, before))
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
        declined = confirm_use("pack", store, [f for f in files if os.path.isfile(f)], output=dst, texts=argv[2:])
        if declined:
            return declined
        try:
            pack(dst, files)
            if not record_output(registry, dst):
                os.unlink(dst)
                print("Failed: the archive could not be registered as an output; nothing was written.")
                return 1
        except PackError as e:
            print(f"Failed: {e}; nothing was written.")
            return 1
        except Exception as e:
            print(f"Failed ({type(e).__name__}); nothing was written.")
            return 1
        EVENTS.append(("pack", counter, None))
        RESULT.update(packed=len(files), used=counter)
        print(f"Archive written with {len(files)} file(s); the files are untouched. Items used: {used(counter) or 'none'}.")
        return 0
    raise AssertionError("a command is listed in COMMANDS but not handled")


def log_events(log):
    """Append what the finished command changed. Returns an error exit code if the log cannot be written."""
    try:
        for command, items, master in EVENTS:
            log.append(command, items, master)
    except OSError as e:
        print(f"Failed to write the activity log ({type(e).__name__}); the command itself did run.")
        return 1
    return 0


def run_without_traceback(argv, store, log=None, registry=None):
    """run_cmd, with any error it did not expect reduced to its type name.

    An exception's message or traceback can quote the data it was handling, so neither is ever
    printed: this is what keeps a value out of the output on the paths nobody thought of.
    A store that cannot go on (locked, damaged, unlock cancelled) is a failure it does expect: a
    StoreError's message never holds a value and is printed, with the error's own exit code.
    The store is opened here too (store=None: the default one), so that failing is covered as well.
    """
    try:
        store = default_store() if store is None else store
        if log is None and getattr(store, "directory", None):
            log = ActivityLog(os.path.join(store.directory, "activity.log"))
        if registry is None and getattr(store, "directory", None):
            registry = Registry(os.path.join(store.directory, FILE_NAME))
        LOG[0] = log
        code = run_cmd(argv, store, registry)
        if getattr(store, "notice", None):
            print(f"Note: {store.notice}.")
        if log and EVENTS:  # also after a partial failure: what did happen is recorded
            logged = log_events(log)
            code = code or logged
        return code
    except StoreError as e:
        print(f"Failed: {e}.")
        return e.code
    except KeyboardInterrupt:
        print("Interrupted; the command did not finish.")
        return 1
    except Exception as e:
        print(f"Failed: unexpected error ({type(e).__name__}); see the exit code.")
        return 1


def guard(registry=None):
    """Answer a tool hook from standard input. Without a registry, the one beside the default vault."""
    registry = registry or Registry(os.path.join(os.path.dirname(vault_path()), FILE_NAME))
    return run_guard(registry, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "privfill.py"))


def main(argv, store=None, log=None, registry=None):
    """Run one command. Items are read and written only through store (default: the vault).

    The log (default: activity.log beside the store, if the store has a directory) records what changed.
    Outputs are recorded in registry (default: a file in the store's directory, if it has one).
    """
    harden()  # before anything personal is read
    if argv[1:] and argv[1:3][-1] == "guard" and argv[1:3][0] in ("guard", "--json"):
        return guard(registry)  # a hook needs the exit code alone: no store, no passphrase, no --json
    if argv[1:2] == ["mcp"]:  # stdout is the protocol channel; each tool call comes back here with --json
        from .mcp_server import run
        return run(argv, store)
    RESULT.clear()
    del EVENTS[:]
    LOG[0] = None
    if "--json" in argv[1:2]:
        argv = argv[:1] + argv[2:]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run_without_traceback(argv, store, log, registry)
        out = {"ok": code == 0, "code": code, "message": buf.getvalue().strip()}
        out.update(RESULT)
        print(json.dumps(out, ensure_ascii=False))
        return code
    return run_without_traceback(argv, store, log, registry)

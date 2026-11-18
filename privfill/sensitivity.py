"""Sensitivity levels, and the person's local confirmation before a high-sensitivity item is used.

Every item has a level: low, medium or high. The entry protocol may set it per field; otherwise it
follows the item's type (DEFAULT_BY_TYPE). A render, fill or pack that would use a high item asks
the person first, in a local window that names the masters, the output and every item used. They
may allow once, allow for GRANT_SECONDS for the same master content, or decline (exit code 7,
nothing written). Which items will be used is found before any value is read, by filling a scratch
copy with stand-in values.

A remembered grant is stored beside the vault with a tag made with the vault's data key, so it
cannot be written by anyone without the key. A changed master has another content hash and asks again.
"""
import base64
import hashlib
import json
import os
import shutil
import tempfile
import time

from .filling import Missing, fill_file, sub

LEVELS = ("low", "medium", "high")
DEFAULT_BY_TYPE = {"id_card": "high", "phone": "medium", "date": "medium", "email": "medium"}
GRANT_SECONDS = 600
GRANTS_FILE = "grants.json"
DECLINED = 7  # exit code: the person declined the use of a high-sensitivity item
STAND_IN = "2000-01-01"  # fits every format: a date, and long enough for first/last N


def default_level(type_):
    return DEFAULT_BY_TYPE.get(type_, "low")


def higher(a, b):
    return a if LEVELS.index(a) >= LEVELS.index(b) else b


def levels(store):
    """{item name: level} for every item; a store that keeps no levels gets the defaults by type."""
    if hasattr(store, "levels"):
        return store.levels()
    entries = store.entries() if hasattr(store, "entries") else [(n, "text") for n in store.names()]
    return {n: default_level(t) for n, t in entries}


class Recorder:
    """A stand-in store: answers every item it has with a value that is not the item's, and notes which were asked for."""

    def __init__(self, names):
        self.known, self.used = set(names), set()

    def read(self, name):
        if name not in self.known:
            return None
        self.used.add(name)
        return STAND_IN

    def names(self):
        return sorted(self.known)


def items_used(store, files=(), texts=()):
    """The names of the items a fill of these files and these texts (paths with placeholders) would read.

    No value is read: the files are filled, as scratch copies in a private directory, with stand-ins.
    A file that cannot be filled is passed over; the real run reports it.
    """
    rec = Recorder(store.names())
    for t in texts:
        try:
            sub(t, rec)
        except Missing:
            pass
    if files:
        scratch = tempfile.mkdtemp(prefix="privfill-scan-")
        try:
            for i, f in enumerate(files):
                copy = os.path.join(scratch, f"{i}{os.path.splitext(f)[1]}")
                try:
                    shutil.copyfile(f, copy)
                    fill_file(copy, rec)
                except Exception:  # noqa: BLE001 - only the names matter here
                    pass
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    return rec.used


def content_hash(paths):
    h = hashlib.sha256()
    for p in paths:
        try:
            with open(p, "rb") as f:
                h.update(hashlib.sha256(f.read()).digest())
        except OSError:
            h.update(b"\0unreadable")
    return h.hexdigest()


def grant_key(command, masters, items):
    return hashlib.sha256(json.dumps([command, content_hash(masters), sorted(items)]).encode("utf8")).hexdigest()


class Grants:
    """Remembered grants, each tagged with the vault's data key. A store without a key remembers none."""

    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        d = getattr(store, "directory", None)
        self.path = os.path.join(d, GRANTS_FILE) if d and hasattr(store, "tag") else None

    def _load(self):
        if not self.path:
            return []
        try:
            with open(self.path, encoding="utf8") as f:
                data = json.load(f)
            return [g for g in data if isinstance(g, dict)] if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def live(self, key):
        """Whether a grant for this key exists, has not expired, and carries a valid tag."""
        now = self.clock()
        for g in self._load():
            if g.get("key") == key and isinstance(g.get("expires"), (int, float)) and g["expires"] > now:
                if self.store.check_tag(g.get("tag"), "grant", key, g["expires"]):
                    return True
        return False

    def add(self, key, seconds=GRANT_SECONDS):
        if not self.path:
            return
        from .atomic import atomic_write

        now = self.clock()
        expires = int(now + seconds)
        kept = [g for g in self._load() if isinstance(g.get("expires"), (int, float)) and g["expires"] > now and g.get("key") != key]
        kept.append({"key": key, "expires": expires, "tag": self.store.tag("grant", key, expires)})

        def w(tmp):
            with open(tmp, "w", encoding="utf8") as f:
                json.dump(kept, f)

        atomic_write(self.path, w)
        os.chmod(self.path, 0o600)


CONFIRM_TEST = "PRIVFILL_CONFIRM_TEST"  # test hook: "yes", "remember" or "no" instead of the window
CONFIRM_SHOT = "PRIVFILL_CONFIRM_SHOT"
SECRET_HOOKS = ("PRIVFILL_PASSPHRASE_TEST", "PRIVFILL_KEYRING_TEST")  # hooks through which the caller supplies the unlock secret  # test hook: also draw the window off-screen into this picture


def ask(command, masters, output, items, level_of):
    """Ask the person in a local window. Returns "once", "remember" or "no"."""
    from .form import have_qt
    from .texts import ui

    lines = [ui("conf_intro", command=command)]
    for m in masters:
        lines.append(ui("conf_master", path=os.path.abspath(m)))
    if output:
        lines.append(ui("conf_output", path=os.path.abspath(output)))
    lines.append(ui("conf_items"))
    for n in sorted(items, key=lambda n: (-LEVELS.index(level_of[n]), n)):
        lines.append(f"  • {n}  ({ui('level_' + level_of[n])})")
    text = "\n".join(lines)
    test = os.environ.get(CONFIRM_TEST)
    if test is None and any(os.environ.get(h) is not None for h in SECRET_HOOKS):
        # The caller supplied the vault's secret, so the vault is not the person's: nobody to ask.
        test = "yes"
    if test is not None:
        vault = os.environ.get("PRIVFILL_VAULT")
        home = os.path.realpath(os.path.join(os.path.expanduser("~"), ".privfill")) + os.sep
        if not vault or os.path.realpath(os.path.expanduser(vault)).startswith(home):
            raise RuntimeError(f"{CONFIRM_TEST} requires PRIVFILL_VAULT to name a vault outside ~/.privfill")
        shot = os.environ.get(CONFIRM_SHOT)
        if shot and have_qt():
            qt_ask(text, shot)
        return {"yes": "once", "remember": "remember"}.get(test, "no")
    if have_qt():
        return qt_ask(text)
    return tk_ask(text)


def qt_ask(text, shot=None):
    from PyQt6 import QtWidgets

    from .texts import ui

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["privfill"])  # noqa: F841
    box = QtWidgets.QMessageBox()
    box.setWindowTitle(ui("conf_title"))
    box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
    box.setText(text)
    once = box.addButton(ui("conf_once"), QtWidgets.QMessageBox.ButtonRole.AcceptRole)
    remember = box.addButton(ui("conf_remember", minutes=GRANT_SECONDS // 60), QtWidgets.QMessageBox.ButtonRole.AcceptRole)
    no = box.addButton(ui("conf_no"), QtWidgets.QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(no)
    box.setEscapeButton(no)
    if shot:
        box.adjustSize()
        box.grab().save(shot)
        return "no"
    box.raise_()
    box.activateWindow()
    box.exec()
    clicked = box.clickedButton()
    return "once" if clicked is once else "remember" if clicked is remember else "no"


def tk_ask(text):
    import tkinter as tk
    from tkinter import messagebox

    from .texts import ui

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return "once" if messagebox.askyesno(ui("conf_title"), text + "\n\n" + ui("conf_tk"), default="no", parent=root) else "no"
    finally:
        root.destroy()


def b64(data):
    return base64.b64encode(data).decode("ascii")

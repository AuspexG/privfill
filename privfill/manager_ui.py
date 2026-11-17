"""The local manager window (PyQt6): see which items exist, reveal a value on click, edit, delete, back up, restore.

The window is for the person. It writes nothing to the terminal: every result and every error is shown in
the window. The logic is in manager.py; this file only draws it and asks the person to confirm.

Test hook: PRIVFILL_MANAGER_SHOT=<png> renders the window off-screen to an image instead of showing it, after
running the actions in PRIVFILL_MANAGER_SCRIPT (a JSON list of {"select": name}, {"reveal": true},
{"hide": true}, {"edit": text}, {"delete": true}, {"export": path}, {"import": path}); confirmations are
accepted. It requires PRIVFILL_VAULT, like the form's hook.
"""
import json
import os

from .form import have_qt
from .manager import Manager
from .normalize import Bad
from .store import StoreError
from .texts import ui
from .vault import COST, TEST_COST, PassphraseUnlock

REVEAL_SECONDS = 8
MASK = "•" * 8  # always the same length: the mask does not hint at the value's length

STYLE = """
    QWidget#root { background: #f3f4f6; }
    QLabel { color: #111827; background: transparent; }
    QLabel#title { font-size: 18pt; font-weight: 700; }
    QFrame#privacy { background: #ecfdf3; border: 1px solid #b7ebc9; border-radius: 8px; }
    QLabel#privacytext { color: #166534; font-size: 9.5pt; }
    QFrame#card { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 12px; }
    QLabel#name { font-size: 14pt; font-weight: 700; }
    QLabel#kind { color: #6b7280; background: #f3f4f6; border-radius: 9px; padding: 1px 8px; font-size: 8.5pt; }
    QLabel#value { font-size: 14pt; padding: 10px 12px; background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; }
    QLabel#value[shown="true"] { background: #fffbeb; border: 1px solid #fcd34d; }
    QLabel#note, QLabel#count { color: #6b7280; font-size: 9.5pt; }
    QLabel#status { color: #166534; font-size: 10pt; }
    QLabel#status[bad="true"], QLabel#editerr { color: #b42318; font-weight: 600; }
    QListWidget { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 4px; outline: 0; }
    QListWidget::item { padding: 9px 10px; border-radius: 8px; color: #111827; }
    QListWidget::item:selected { background: #eff6ff; color: #1d4ed8; }
    QLineEdit { min-height: 22px; padding: 8px 11px; border: 1px solid #d1d5db; border-radius: 8px; background: #ffffff; }
    QLineEdit:focus { border: 2px solid #2563eb; padding: 7px 10px; }
    QPushButton { min-height: 22px; padding: 8px 18px; border-radius: 8px; border: 1px solid #d1d5db;
                  background: #ffffff; color: #374151; font-weight: 600; }
    QPushButton:hover { background: #f9fafb; border: 1px solid #9ca3af; }
    QPushButton:disabled { color: #9ca3af; background: #f3f4f6; }
    QPushButton#primary { background: #2563eb; border: 1px solid #2563eb; color: #ffffff; }
    QPushButton#primary:disabled { background: #93c5fd; border: 1px solid #93c5fd; }
    QPushButton#danger { color: #b42318; }
"""


class Dialogs:
    """The questions the window asks the person; tests and the hook replace this class."""

    def confirm(self, text):
        from PyQt6 import QtWidgets

        box = QtWidgets.QMessageBox
        return box.question(None, ui("mgr_title"), text, box.StandardButton.Yes | box.StandardButton.No,
                            box.StandardButton.No) == box.StandardButton.Yes

    def save_path(self):
        from PyQt6 import QtWidgets

        return QtWidgets.QFileDialog.getSaveFileName(None, ui("mgr_save_dlg"))[0] or None

    def open_path(self):
        from PyQt6 import QtWidgets

        return QtWidgets.QFileDialog.getOpenFileName(None, ui("mgr_open_dlg"))[0] or None


def make_window(manager, dialogs=None, reveal_seconds=REVEAL_SECONDS):
    """Build the manager window (a QWidget with the controls as attributes). Needs a QApplication to exist."""
    from PyQt6 import QtCore, QtWidgets

    dialogs = dialogs or Dialogs()

    class Window(QtWidgets.QWidget):
        pass

    w = Window()
    w.setObjectName("root")
    w.setWindowTitle(ui("mgr_title"))
    w.setMinimumSize(760, 460)
    w.setStyleSheet(STYLE)

    def label(text, name, wrap=True):
        x = QtWidgets.QLabel(text)
        x.setObjectName(name)
        x.setWordWrap(wrap)
        return x

    def button(text, name=None):
        b = QtWidgets.QPushButton(text)
        if name:
            b.setObjectName(name)
        b.setAutoDefault(False)
        return b

    def mark(widget, prop, on):
        widget.setProperty(prop, "true" if on else "false")
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    outer = QtWidgets.QVBoxLayout(w)
    outer.setContentsMargins(24, 22, 24, 18)
    outer.setSpacing(12)
    top = QtWidgets.QHBoxLayout()
    top.addWidget(label(ui("mgr_title"), "title", wrap=False))
    top.addStretch(1)
    w.export_btn = button(ui("mgr_export"))
    w.import_btn = button(ui("mgr_import"))
    top.addWidget(w.export_btn)
    top.addWidget(w.import_btn)
    outer.addLayout(top)

    box = QtWidgets.QFrame()
    box.setObjectName("privacy")
    bl = QtWidgets.QHBoxLayout(box)
    bl.setContentsMargins(12, 8, 12, 8)
    bl.addWidget(label(ui("mgr_privacy"), "privacytext"))
    outer.addWidget(box)

    body = QtWidgets.QHBoxLayout()
    body.setSpacing(14)
    left = QtWidgets.QVBoxLayout()
    w.list = QtWidgets.QListWidget()
    w.list.setFixedWidth(250)
    left.addWidget(w.list, 1)
    w.count = label("", "count", wrap=False)
    left.addWidget(w.count)
    body.addLayout(left)

    card = QtWidgets.QFrame()
    card.setObjectName("card")
    cl = QtWidgets.QVBoxLayout(card)
    cl.setContentsMargins(20, 18, 20, 18)
    cl.setSpacing(10)
    head = QtWidgets.QHBoxLayout()
    w.name = label(ui("mgr_pick"), "name", wrap=False)
    w.kind = label("", "kind", wrap=False)
    head.addWidget(w.name)
    head.addWidget(w.kind)
    head.addStretch(1)
    cl.addLayout(head)
    w.value = label(MASK, "value", wrap=False)
    cl.addWidget(w.value)
    row = QtWidgets.QHBoxLayout()
    w.reveal_btn = button(ui("mgr_reveal"))
    w.delete_btn = button(ui("mgr_delete"), "danger")
    row.addWidget(w.reveal_btn)
    row.addWidget(label(ui("mgr_hides_in", s=reveal_seconds), "note", wrap=False))
    row.addStretch(1)
    row.addWidget(w.delete_btn)
    cl.addLayout(row)
    cl.addSpacing(8)
    w.edit_input = QtWidgets.QLineEdit()
    w.edit_input.setPlaceholderText(ui("mgr_new_value"))
    cl.addWidget(w.edit_input)
    w.edit_error = label(ui("mgr_edit_hint"), "note")
    cl.addWidget(w.edit_error)
    w.save_btn = button(ui("mgr_save"), "primary")
    cl.addWidget(w.save_btn, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
    cl.addStretch(1)
    body.addWidget(card, 1)
    outer.addLayout(body, 1)
    w.status = label("", "status")
    outer.addWidget(w.status)

    timer = QtCore.QTimer(w)
    timer.setSingleShot(True)
    timer.setInterval(int(reveal_seconds * 1000))
    w.hide_timer = timer
    state = {"name": None}

    def say(text, bad=False):
        w.status.setText(text)
        mark(w.status, "bad", bad)

    def edit_error(text, bad):
        w.edit_error.setText(text)
        w.edit_error.setObjectName("editerr" if bad else "note")
        w.edit_error.style().unpolish(w.edit_error)
        w.edit_error.style().polish(w.edit_error)

    def fail(e):
        say(f"{e}", bad=True)

    def hide():
        timer.stop()
        w.value.setText(MASK)
        mark(w.value, "shown", False)
        w.reveal_btn.setText(ui("mgr_reveal"))

    def selected():
        return state["name"]

    def refresh(keep=None):
        try:
            items = manager.items()
        except (StoreError, OSError) as e:
            items = []
            fail(e)
        w.list.blockSignals(True)
        w.list.clear()
        for n, t in items:
            w.list.addItem(f"{n}   ·  {t}")
        w.list.blockSignals(False)
        names = [n for n, _ in items]
        w.count.setText(ui("mgr_items", n=len(items)) if items else ui("mgr_none"))
        w.item_names = names
        if keep in names:
            w.list.setCurrentRow(names.index(keep))
        else:
            w.list.setCurrentRow(-1)
        choose(w.list.currentRow())

    def choose(row):
        hide()
        names = getattr(w, "item_names", [])
        name = names[row] if 0 <= row < len(names) else None
        state["name"] = name
        kinds = dict(manager.items()) if name else {}
        w.name.setText(name or ui("mgr_pick"))
        w.kind.setText(kinds.get(name, ""))
        w.kind.setVisible(bool(name))
        for c in (w.reveal_btn, w.delete_btn, w.save_btn, w.edit_input):
            c.setEnabled(bool(name))
        w.edit_input.clear()
        edit_error(ui("mgr_edit_hint"), False)

    def on_reveal():
        name = selected()
        if name is None:
            return
        if timer.isActive():
            hide()
            return
        try:
            value = manager.reveal(name)
        except (StoreError, OSError) as e:
            fail(e)
            return
        say("")
        w.value.setText(value)
        mark(w.value, "shown", True)
        w.reveal_btn.setText(ui("mgr_hide"))
        timer.start()

    def on_save():
        name = selected()
        if name is None:
            return
        try:
            manager.edit(name, w.edit_input.text())
        except Bad as e:
            edit_error(str(e), True)
            return
        except (StoreError, OSError) as e:
            fail(e)
            return
        refresh(name)
        say(ui("mgr_saved"))

    def on_delete():
        name = selected()
        if name is None or not dialogs.confirm(ui("mgr_confirm_delete", name=name)):
            return
        try:
            manager.delete(name)
        except (StoreError, OSError) as e:
            fail(e)
            return
        refresh()
        say(ui("mgr_deleted"))

    def on_export():
        path = dialogs.save_path()
        if not path:
            return
        try:
            n = manager.export(path)
        except (StoreError, OSError) as e:
            fail(e)
            return
        say(ui("mgr_exported", n=n))

    def on_import():
        path = dialogs.open_path()
        if not path:
            return
        try:
            added, replaced = manager.import_(
                path, lambda names: dialogs.confirm(ui("mgr_confirm_replace", names="\n".join(names))))
        except (StoreError, OSError) as e:
            fail(e)
            return
        refresh(selected())
        say(ui("mgr_imported", a=added, r=replaced))

    w.list.currentRowChanged.connect(choose)
    timer.timeout.connect(hide)
    w.reveal_btn.clicked.connect(on_reveal)
    w.save_btn.clicked.connect(on_save)
    w.edit_input.returnPressed.connect(on_save)
    w.delete_btn.clicked.connect(on_delete)
    w.export_btn.clicked.connect(on_export)
    w.import_btn.clicked.connect(on_import)
    w.refresh = refresh
    w.select = lambda name: w.list.setCurrentRow(w.item_names.index(name))
    w.is_revealed = timer.isActive
    w.hide_value = hide
    refresh()
    return w


class ScriptDialogs(Dialogs):
    """The hook's answers: every confirmation is yes, and file paths come from the script."""

    def __init__(self):
        self.path = None

    def confirm(self, text):
        return True

    def save_path(self):
        return self.path

    def open_path(self):
        return self.path


def run_script(w, dialogs, actions):
    for act in actions:
        if "select" in act:
            w.select(act["select"])
        elif act.get("reveal"):
            w.reveal_btn.click()
        elif act.get("hide"):
            w.hide_value()
        elif "edit" in act:
            w.edit_input.setText(act["edit"])
            w.save_btn.click()
        elif act.get("delete"):
            w.delete_btn.click()
        elif "export" in act:
            dialogs.path = act["export"]
            w.export_btn.click()
        elif "import" in act:
            dialogs.path = act["import"]
            w.import_btn.click()


def run_manager(store):
    """Open the manager for a store and wait until the person closes it. Returns an exit code; prints nothing."""
    if not have_qt():
        print(f"Failed: {ui('mgr_no_qt')}.")
        return 1
    from PyQt6 import QtGui, QtWidgets

    shot = os.environ.get("PRIVFILL_MANAGER_SHOT")
    if shot and not os.environ.get("PRIVFILL_VAULT"):
        raise RuntimeError("PRIVFILL_MANAGER_SHOT requires PRIVFILL_VAULT")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["privfill"])
    app.setStyle("Fusion")
    font = QtGui.QFont()
    font.setFamilies(["Noto Sans CJK SC", "Source Han Sans CN", "Noto Sans", "sans-serif"])
    font.setPointSize(11)
    app.setFont(font)

    from .form import PASSPHRASE_TEST, ask_passphrase

    cost = TEST_COST if os.environ.get(PASSPHRASE_TEST) is not None else COST
    manager = Manager(store, lambda: PassphraseUnlock(ask_passphrase, cost=cost))
    if shot:
        dialogs = ScriptDialogs()
        w = make_window(manager, dialogs, reveal_seconds=REVEAL_SECONDS)
        run_script(w, dialogs, json.loads(os.environ.get("PRIVFILL_MANAGER_SCRIPT") or "[]"))
        w.resize(820, 500)
        w.grab().save(shot)
        return 0
    w = make_window(manager)
    w.show()
    w.raise_()
    w.activateWindow()
    app.exec()
    return 0

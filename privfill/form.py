"""The entry form shown to the person: Qt when available, Tk otherwise."""
import json
import os
import re
import shutil
import sys
import tempfile

from .normalize import Bad, normalize
from .texts import ui

PASSPHRASE_TEST = "PRIVFILL_PASSPHRASE_TEST"  # test hook, see ask_passphrase

def run_form(spec, data, store_label):
    """Show the form. Returns {name: normalized value} for answered fields, or None if cancelled.

    data holds the stored values of the form's items, for pre-filling; store_label names the store to the person.
    """
    test = os.environ.get("PRIVFILL_FORM_TEST")
    if test is not None:  # test hook: answers from the environment, fake vault only
        if not os.environ.get("PRIVFILL_VAULT"):
            raise RuntimeError("PRIVFILL_FORM_TEST requires PRIVFILL_VAULT")
        answers, out = json.loads(test), {}
        if answers is None:  # the person cancels
            return None
        for f in spec["fields"]:
            raw = str(answers.get(f["name"], "")).strip()
            if raw:
                out[f["name"]] = normalize(f, raw)
            elif f.get("required"):
                raise Bad(f"{f['name']}: {ui('bad_required')}")
        return out
    return qt_form(spec, data, store_label) if have_qt() else tk_form(spec, data, store_label)


def have_qt():
    """Whether PyQt6 can be used. Without it, the command is started again on the system Python, once."""
    try:
        from PyQt6 import QtWidgets  # noqa: F401
    except ImportError:
        sys_py = "/usr/bin/python3"
        if os.path.exists(sys_py) and os.path.realpath(sys.executable) != os.path.realpath(sys_py) \
                and not os.environ.get("PRIVFILL_REEXEC"):
            # This interpreter has no Qt (a conda or venv Python, say): retry with the system one.
            os.environ["PRIVFILL_REEXEC"] = "1"
            os.execv(sys_py, [sys_py, os.path.abspath(sys.argv[0])] + sys.argv[1:])
        return False
    return True


def ask_passphrase(kind):
    """Ask the person for the vault passphrase in a local dialog. Returns it, or None if they cancel.

    kind is "new" (choose one, typed twice), "unlock" (enter it) or "retry" (the last one was wrong).
    """
    test = os.environ.get(PASSPHRASE_TEST)
    if test is not None:  # test hook: the passphrase from the environment, fake vault only
        vault = os.environ.get("PRIVFILL_VAULT")
        if not vault or os.path.realpath(os.path.expanduser(vault)).startswith(
                os.path.realpath(os.path.join(os.path.expanduser("~"), ".privfill")) + os.sep):
            raise RuntimeError(f"{PASSPHRASE_TEST} requires PRIVFILL_VAULT to name a vault outside ~/.privfill")
        return test
    prompt = qt_secret if have_qt() else tk_secret
    if kind != "new":
        return prompt(ui("pass_retry" if kind == "retry" else "pass_unlock"))
    text = ui("pass_new")
    while True:
        first = prompt(text)
        if first is None:
            return None
        if not first:
            text = ui("pass_empty") + "\n\n" + ui("pass_new")
            continue
        again = prompt(ui("pass_again"))
        if again is None:
            return None
        if again == first:
            return first
        text = ui("pass_mismatch") + "\n\n" + ui("pass_new")


def qt_secret(text):
    """One hidden line typed by the person, or None if they cancel."""
    from PyQt6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["privfill"])  # noqa: F841
    dlg = QtWidgets.QInputDialog()
    dlg.setWindowTitle(ui("pass_title"))
    dlg.setLabelText(text)
    dlg.setTextEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
    dlg.setOkButtonText(ui("pass_ok"))
    dlg.setCancelButtonText(ui("cancel"))
    dlg.raise_()
    dlg.activateWindow()
    return dlg.textValue() if dlg.exec() else None


def tk_secret(text):
    """Plain fallback when PyQt6 is not available."""
    import tkinter as tk
    from tkinter import simpledialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return simpledialog.askstring(ui("pass_title"), text, show="\u2022", parent=root)
    finally:
        root.destroy()


def example_from(hint):
    m = re.search(r"(?:例如|e\.g\.,?|for example,?)\s*([^，,。；;.]+(?:\.[^\s，,。；;]+)*)", hint or "", flags=re.I)
    return m.group(1).strip() if m else ""


def qt_form(spec, data, store_label):
    from PyQt6 import QtCore, QtGui, QtWidgets

    shot = os.environ.get("PRIVFILL_FORM_SHOT")
    if shot and not os.environ.get("PRIVFILL_VAULT"):
        raise RuntimeError("PRIVFILL_FORM_SHOT requires PRIVFILL_VAULT")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["privfill"])
    app.setStyle("Fusion")  # one base style everywhere; the look comes from the stylesheet below
    font = QtGui.QFont()
    font.setFamilies(["Noto Sans CJK SC", "Source Han Sans CN", "Noto Sans", "sans-serif"])
    font.setPointSize(11)
    app.setFont(font)

    # Draw the combo-box arrow at run time so no icon theme is needed.
    tmpdir = tempfile.mkdtemp(prefix="privfill-ui-")
    arrow = os.path.join(tmpdir, "chevron.png")
    pm = QtGui.QPixmap(24, 24)
    pm.fill(QtCore.Qt.GlobalColor.transparent)
    qp = QtGui.QPainter(pm)
    qp.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    pen = QtGui.QPen(QtGui.QColor("#6b7280"), 2.4)
    pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
    qp.setPen(pen)
    qp.drawPolyline(QtCore.QPointF(6, 9), QtCore.QPointF(12, 15), QtCore.QPointF(18, 9))
    qp.end()
    pm.save(arrow)

    title = spec.get("title") or ui("title")
    fields = spec["fields"]
    n_req = sum(1 for f in fields if f.get("required"))

    dlg = QtWidgets.QDialog()
    dlg.setWindowTitle(title)
    dlg.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, True)
    dlg.setMinimumWidth(580)
    dlg.setStyleSheet("""
        QDialog { background: #f3f4f6; }
        QLabel { color: #111827; background: transparent; }
        QLabel#title { font-size: 18pt; font-weight: 700; }
        QLabel#note { color: #4b5563; font-size: 10.5pt; }
        QFrame#privacy { background: #ecfdf3; border: 1px solid #b7ebc9; border-radius: 8px; }
        QFrame#privacybar { background: #16a34a; border-radius: 2px; }
        QLabel#privacytext { color: #166534; font-size: 9.5pt; }
        QFrame#card { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 12px; }
        QWidget#body, QScrollArea#scroll, QScrollArea#scroll > QWidget { background: #ffffff; border: none; }
        QLabel#section { color: #2563eb; font-size: 9.5pt; font-weight: 700; }
        QFrame#rule { background: #eef0f3; max-height: 1px; min-height: 1px; border: none; }
        QLabel#q { font-size: 10.5pt; font-weight: 600; }
        QLabel#key { color: #6b7280; background: #f3f4f6; border-radius: 9px; padding: 1px 8px; font-size: 8.5pt; }
        QLabel#req { color: #b42318; background: #fef3f2; border-radius: 9px; padding: 1px 8px; font-size: 8.5pt; font-weight: 600; }
        QLabel#hint { color: #6b7280; font-size: 9.5pt; }
        QLabel#hint[bad="true"] { color: #b42318; font-weight: 600; }
        QLineEdit, QComboBox {
            min-height: 22px; padding: 8px 11px; border: 1px solid #d1d5db; border-radius: 8px;
            background: #ffffff; color: #111827; selection-background-color: #bfdbfe; selection-color: #111827;
        }
        QLineEdit:hover, QComboBox:hover { border: 1px solid #9ca3af; }
        QLineEdit:focus, QComboBox:focus { border: 2px solid #2563eb; padding: 7px 10px; }
        QLineEdit[bad="true"], QComboBox[bad="true"] { border: 2px solid #d92d20; padding: 7px 10px; background: #fffbfa; }
        QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; width: 34px; border: none; }
        QComboBox::down-arrow { image: url(ARROW); width: 16px; height: 16px; }
        QComboBox QAbstractItemView {
            background: #ffffff; color: #111827; border: 1px solid #d1d5db; border-radius: 6px; padding: 4px;
            selection-background-color: #eff6ff; selection-color: #1d4ed8; outline: 0;
        }
        QComboBox QAbstractItemView::item { min-height: 30px; padding-left: 8px; }
        QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 2px 4px 0; }
        QScrollBar::handle:vertical { background: #d1d5db; border-radius: 4px; min-height: 36px; }
        QScrollBar::handle:vertical:hover { background: #9ca3af; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        QLabel#foot { color: #6b7280; font-size: 9.5pt; }
        QPushButton {
            min-height: 22px; padding: 8px 24px; border-radius: 8px; border: 1px solid #d1d5db;
            background: #ffffff; color: #374151; font-weight: 600;
        }
        QPushButton:hover { background: #f9fafb; border: 1px solid #9ca3af; }
        QPushButton#save { background: #2563eb; border: 1px solid #2563eb; color: #ffffff; }
        QPushButton#save:hover { background: #1d4ed8; border: 1px solid #1d4ed8; }
        QPushButton#save:pressed { background: #1e40af; }
    """.replace("ARROW", arrow.replace("\\", "/")))

    def label(text, name, wrap=True):
        w = QtWidgets.QLabel(text)
        w.setObjectName(name)
        w.setWordWrap(wrap)
        return w

    outer = QtWidgets.QVBoxLayout(dlg)
    outer.setContentsMargins(26, 24, 26, 20)
    outer.setSpacing(0)
    outer.addWidget(label(title, "title"))
    if spec.get("note"):
        outer.addSpacing(6)
        outer.addWidget(label(spec["note"], "note"))
    outer.addSpacing(14)

    box = QtWidgets.QFrame()
    box.setObjectName("privacy")
    bl = QtWidgets.QHBoxLayout(box)
    bl.setContentsMargins(10, 9, 12, 9)
    bl.setSpacing(10)
    bar = QtWidgets.QFrame()
    bar.setObjectName("privacybar")
    bar.setFixedWidth(4)
    bl.addWidget(bar)
    bl.addWidget(label(ui("privacy", file=store_label), "privacytext"), 1)
    outer.addWidget(box)
    outer.addSpacing(14)

    card = QtWidgets.QFrame()
    card.setObjectName("card")
    cl = QtWidgets.QVBoxLayout(card)
    cl.setContentsMargins(4, 6, 4, 6)
    body = QtWidgets.QWidget()
    body.setObjectName("body")
    lay = QtWidgets.QVBoxLayout(body)
    lay.setContentsMargins(18, 12, 16, 14)
    lay.setSpacing(0)

    rows = []
    for i, f in enumerate(fields):
        name = f["name"]
        if f.get("section"):
            if i:
                lay.addSpacing(20)
                rule = QtWidgets.QFrame()
                rule.setObjectName("rule")
                lay.addWidget(rule)
                lay.addSpacing(14)
            lay.addWidget(label(f["section"], "section", wrap=False))
            lay.addSpacing(10)
        elif i:
            lay.addSpacing(16)
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(label(f.get("label") or name, "q", wrap=False))
        if f.get("label") and f["label"] != name:
            head.addWidget(label(name, "key", wrap=False))
        if f.get("required"):
            head.addWidget(label(ui("required"), "req", wrap=False))
        head.addStretch(1)
        lay.addLayout(head)
        lay.addSpacing(6)
        if f["type"] == "choice":
            w = QtWidgets.QComboBox()
            w.setView(QtWidgets.QListView())
            w.addItem("")
            w.addItems(f["choices"])
            w.setCurrentText(data.get(name, ""))
            w.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
            w.wheelEvent = lambda e: e.ignore()  # scrolling the form must not change the choice
            get, changed = w.currentText, w.currentTextChanged
        else:
            w = QtWidgets.QLineEdit(data.get(name, ""))
            w.setClearButtonEnabled(True)
            w.setPlaceholderText(example_from(f.get("hint")))
            get, changed = w.text, w.textChanged
        lay.addWidget(w)
        tip = f.get("hint") or ""
        if name in data:
            tip = (tip + "  " if tip else "") + ui("keep")
        hint = label(tip, "hint")
        hint.setContentsMargins(2, 5, 0, 0)
        hint.setVisible(bool(tip))
        lay.addWidget(hint)

        def mark(bad, text, w=w, hint=hint):
            for x in (w, hint):
                x.setProperty("bad", "true" if bad else "false")
                x.style().unpolish(x)
                x.style().polish(x)
            hint.setText(text)
            hint.setVisible(bool(text))

        changed.connect(lambda *_, mark=mark, tip=tip: mark(False, tip))
        rows.append((f, w, get, mark, tip))
    lay.addStretch(1)

    scroll = QtWidgets.QScrollArea()
    scroll.setObjectName("scroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(body)
    screen = app.primaryScreen()
    avail = screen.availableGeometry().height() if screen else 900
    scroll.setMinimumHeight(min(body.sizeHint().height() + 2, max(240, avail - 380)))
    cl.addWidget(scroll)
    outer.addWidget(card, 1)
    outer.addSpacing(16)

    result = {"out": None}

    def save():
        out, first_bad = {}, None
        for f, w, get, mark, tip in rows:
            raw = get().strip()
            try:
                if raw:
                    out[f["name"]] = normalize(f, raw)
                elif f.get("required"):
                    raise Bad(ui("bad_required"))
                mark(False, tip)
            except Bad as e:
                mark(True, str(e))
                first_bad = first_bad or w
        if first_bad is not None:
            first_bad.setFocus()
            scroll.ensureWidgetVisible(first_bad, 0, 70)
            return
        result["out"] = out
        dlg.accept()

    btns = QtWidgets.QHBoxLayout()
    btns.setSpacing(10)
    count = ui("foot_required", n=len(fields), r=n_req) if n_req else ui("foot_optional", n=len(fields))
    btns.addWidget(label(count + "  ·  " + ui("foot_keys"), "foot", wrap=False))
    btns.addStretch(1)
    cancel = QtWidgets.QPushButton(ui("cancel"))
    cancel.setAutoDefault(False)
    cancel.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    cancel.clicked.connect(dlg.reject)
    ok = QtWidgets.QPushButton(ui("save"))
    ok.setObjectName("save")
    ok.setDefault(True)
    ok.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    ok.clicked.connect(save)
    btns.addWidget(cancel)
    btns.addWidget(ok)
    outer.addLayout(btns)
    rows[0][1].setFocus()

    try:
        if shot:  # test hook: render off-screen to an image instead of showing a window
            answers = json.loads(os.environ.get("PRIVFILL_FORM_ANSWERS") or "{}")
            for f, w, get, mark, tip in rows:
                if f["name"] in answers:
                    (w.setCurrentText if f["type"] == "choice" else w.setText)(str(answers[f["name"]]))
            if answers:
                save()
            dlg.adjustSize()
            dlg.grab().save(shot)
            return None
        timeout = float(os.environ.get("PRIVFILL_DIALOG_TIMEOUT") or 0)
        if timeout:
            QtCore.QTimer.singleShot(int(timeout * 1000), dlg.reject)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        dlg.exec()
        return result["out"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def tk_form(spec, data, store_label):
    """Plain fallback when PyQt6 is not available."""
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(spec.get("title") or ui("title"))
    root.attributes("-topmost", True)
    pad = {"padx": 18}
    tk.Label(root, text=spec.get("title") or ui("title"), font=("", 13, "bold"), **pad).pack(anchor="w", pady=(14, 2))
    if spec.get("note"):
        tk.Label(root, text=spec["note"], wraplength=460, justify="left", **pad).pack(anchor="w")
    tk.Label(root, text=ui("privacy", file=store_label), fg="#166534", wraplength=460, justify="left", **pad).pack(anchor="w", pady=(2, 8))
    widgets = []
    for f in spec["fields"]:
        name = f["name"]
        if f.get("section"):
            tk.Label(root, text=f["section"], fg="#2563eb", font=("", 10, "bold"), **pad).pack(anchor="w", pady=(10, 0))
        text = f.get("label") or name
        if text != name:
            text = f"{text} ({name})"
        if f.get("required"):
            text += "  [" + ui("required") + "]"
        tk.Label(root, text=text, **pad).pack(anchor="w", pady=(6, 0))
        var = tk.StringVar(value=data.get(name, ""))
        if f["type"] == "choice":
            w = ttk.Combobox(root, textvariable=var, values=f["choices"], state="readonly", width=44)
        else:
            w = tk.Entry(root, textvariable=var, width=46)
        w.pack(anchor="w", **pad)
        tip = f.get("hint") or ""
        if name in data:
            tip = (tip + "  " if tip else "") + ui("keep")
        hint = tk.Label(root, text=tip, fg="#666", wraplength=460, justify="left", **pad)
        hint.pack(anchor="w")
        widgets.append((f, var, hint, tip, w))
    result = {"out": None}

    def ok(_=None):
        out, first_bad = {}, None
        for f, var, hint, tip, w in widgets:
            raw = var.get().strip()
            try:
                if raw:
                    out[f["name"]] = normalize(f, raw)
                elif f.get("required"):
                    raise Bad(ui("bad_required"))
                hint.config(text=tip, fg="#666")
            except Bad as e:
                hint.config(text=str(e), fg="#c0392b")
                first_bad = first_bad or w
        if first_bad is not None:
            first_bad.focus_set()
            return
        result["out"] = out
        root.destroy()

    row = tk.Frame(root)
    row.pack(pady=14)
    tk.Button(row, text=ui("save"), width=10, command=ok).pack(side="left", padx=6)
    tk.Button(row, text=ui("cancel"), width=10, command=root.destroy).pack(side="left", padx=6)
    root.bind("<Return>", ok)
    root.bind("<Escape>", lambda _: root.destroy())
    widgets[0][4].focus_set()
    timeout = float(os.environ.get("PRIVFILL_DIALOG_TIMEOUT") or 0)
    if timeout:
        root.after(int(timeout * 1000), root.destroy)
    root.mainloop()
    return result["out"]

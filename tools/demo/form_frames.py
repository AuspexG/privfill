"""Draw the real privfill form off-screen at each typing stage. Fake vault and fake data only.

Usage: python3 form_frames.py <work dir> <out dir>   (normally called by make.sh; needs PyQt6)
Each frame is a separate process (the form draws once per run): this script re-runs itself
with STAGE set, and in that mode patches QDialog.adjustSize to type the stage's text first.
"""
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def child(work, stage_json, shot):
    sys.path.insert(0, REPO)
    from PyQt6 import QtWidgets
    texts = json.loads(stage_json)
    orig = QtWidgets.QDialog.adjustSize

    def adjust(self):
        edits = self.findChildren(QtWidgets.QLineEdit)
        for w, t in zip(edits, texts):
            w.setText(t)
        if texts:
            idx = max(i for i, t in enumerate(texts) if t is not None)
            edits[idx].setFocus()
        orig(self)

    QtWidgets.QDialog.adjustSize = adjust
    from privfill.cli import main
    spec = open(os.path.join(work, "spec.json"), encoding="utf8").read()
    os.environ["PRIVFILL_FORM_SHOT"] = shot
    os.environ["PRIVFILL_FORM_ANSWERS"] = "{}"
    main(["privfill.py", "form", spec])


def stages(answers):
    """Yield the text of each field while the person types, one field after another."""
    vals = list(answers.values())
    done = []
    yield ["" for _ in vals]
    for i, v in enumerate(vals):
        step = 1 if len(v) < 12 else 2
        for n in range(step, len(v) + step, step):
            cur = done + [v[:n]] + ["" for _ in vals[i + 1:]]
            yield cur
        done.append(v)


def main_parent(work, out):
    os.makedirs(out, exist_ok=True)
    answers = json.load(open(os.path.join(work, "answers.json"), encoding="utf8"))
    env = dict(os.environ)
    env.update(HOME=os.path.join(work, "fakehome"), PRIVFILL_VAULT=os.path.join(work, "fakevault2", "vault.json"),
               PRIVFILL_PASSPHRASE_TEST="demo passphrase only", QT_QPA_PLATFORM="offscreen",
               PRIVFILL_LANG=os.path.basename(work).split("-")[-1])
    n = 0
    for st in stages(answers):
        shot = os.path.join(out, "form_%03d.png" % n)
        e = dict(env, STAGE=json.dumps(st, ensure_ascii=False), STAGE_SHOT=shot, STAGE_WORK=work)
        subprocess.run([sys.executable, "-I", os.path.abspath(__file__)], env=e, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        n += 1
    # The final frame: the form's own save with every answer, as the person presses Save.
    shot = os.path.join(out, "form_%03d.png" % n)
    e = dict(env, PRIVFILL_FORM_SHOT=shot, PRIVFILL_FORM_ANSWERS=json.dumps(answers, ensure_ascii=False))
    spec = open(os.path.join(work, "spec.json"), encoding="utf8").read()
    # Exit code 3 is expected: in screenshot mode the form draws itself and reports "not submitted".
    subprocess.run([sys.executable, os.path.join(REPO, "privfill.py"), "form", spec], env=e,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not os.path.exists(shot):
        sys.exit("the final form frame was not drawn")
    print("frames:", n + 1)


if __name__ == "__main__":
    if os.environ.get("STAGE") is not None:
        child(os.environ["STAGE_WORK"], os.environ["STAGE"], os.environ["STAGE_SHOT"])
    else:
        main_parent(sys.argv[1], sys.argv[2])

#!/usr/bin/env python3
"""Merge privfill's guard configuration into a Claude Code settings file, or take it out again.

    claude_settings.py plan    --settings FILE --program PATH --python PATH [--sandbox]
    claude_settings.py apply   --settings FILE --program PATH --python PATH [--sandbox]
    claude_settings.py remove  --settings FILE

What it adds (see docs/claude-code.md):
  permissions.deny   Read(<data dir>/**), Edit(<data dir>/**)       the file tools never reach the data directory
  permissions.allow  Bash(<python> <program> <command> *)            the program's own commands run without a prompt
  hooks.PreToolUse   matcher "*": <python> <program> guard           every tool call is judged by the guard
  sandbox (with --sandbox)  enabled, and the program's commands excluded from it, so they can read the vault
It removes the earlier ask rules on the data directory and on any command mentioning privfill.

Everything else in the file is kept as it is. What was added is recorded in a sidecar file
(<settings>.privfill.json) so that remove takes out exactly that and nothing the person had before.
The file is written atomically, and a copy of the previous version is kept as <settings>.privfill-backup.
This script is not part of the installed program: the assistant's own tool cannot undo it.
"""
import argparse
import copy
import json
import os
import re
import shlex
import sys
import tempfile

OLD_ASK = ["Read(~/.privfill/**)", "Edit(~/.privfill/**)", "Bash(*privfill*)"]
# Commands the assistant runs; guard is run by the hook, not by the assistant.
OWN_COMMANDS = ["keys", "form", "add", "render", "fill", "pack", "protocol", "version", "help", "verify", "audit"]


def shell_path(path):
    """A path for a shell command line: ~/... is left unquoted so the shell expands it; anything
    with characters that need quoting is written out in full and quoted."""
    form = home_form(path)
    if re.fullmatch(r"[\w@%+=:,./~-]+", form):
        return form
    return shlex.quote(os.path.abspath(os.path.expanduser(path)))


def home_form(path):
    """The path with the home directory written as ~, as permission rules usually spell it."""
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


SKILL_PROGRAM = os.path.join("~", ".claude", "skills", "privfill", "privfill.py")  # the copy the skill points to


def additions(program, python, sandbox):
    """What to add. The hook runs the program with the installing interpreter; the allow rules match
    the commands as the assistant writes them (python3, and either installed copy of the program)."""
    data_dir = home_form(os.path.dirname(os.path.abspath(program)))
    hook_run = f"{shell_path(python)} {shell_path(os.path.abspath(program))}"
    typed = [f"python3 {home_form(os.path.abspath(program))}", f"python3 {SKILL_PROGRAM}"]
    add = {
        "deny": [f"Read({data_dir}/**)", f"Edit({data_dir}/**)"],
        "allow": [f"Bash({run} {pre}{c} *)" for run in typed for pre in ("", "--json ") for c in OWN_COMMANDS],
        "hook": {"type": "command", "command": f"{hook_run} guard", "timeout": 30},
        "sandbox": None,
    }
    if sandbox:
        add["sandbox"] = {"enabled": True, "excludedCommands": [f"{run} *" for run in typed]}
    return add


def is_our_hook(h):
    return isinstance(h, dict) and re.search(r"privfill\.py'?\s+guard\b", str(h.get("command", ""))) is not None


def merged(settings, add):
    """(the new settings, a record of what was added). settings is not changed."""
    s = copy.deepcopy(settings)
    record = {"deny": [], "allow": [], "removed_ask": [], "hook": False, "sandbox": {}}
    perms = s.setdefault("permissions", {})
    for kind in ("deny", "allow"):
        rules = perms.setdefault(kind, [])
        for r in add[kind]:
            if r not in rules:
                rules.append(r)
                record[kind].append(r)
    ask = perms.get("ask", [])
    for r in OLD_ASK:
        if r in ask:
            ask.remove(r)
            record["removed_ask"].append(r)
    if "ask" in perms and not perms["ask"]:
        del perms["ask"]
    pre = s.setdefault("hooks", {}).setdefault("PreToolUse", [])
    for group in pre:  # an earlier version of our hook is replaced
        group["hooks"] = [h for h in group.get("hooks", []) if not is_our_hook(h)]
    pre[:] = [g for g in pre if g.get("hooks")]
    pre.append({"matcher": "*", "hooks": [add["hook"]]})
    record["hook"] = True
    if add["sandbox"]:
        sb = s.setdefault("sandbox", {})
        if sb.get("enabled") is not True:
            record["sandbox"]["enabled_was"] = sb.get("enabled", None)
            sb["enabled"] = True
        ex = sb.setdefault("excludedCommands", [])
        record["sandbox"]["excluded"] = [c for c in add["sandbox"]["excludedCommands"] if c not in ex]
        ex.extend(record["sandbox"]["excluded"])
    return s, record


def unmerged(settings, record):
    """The settings with exactly what the record says was added taken out, and the old ask rules back."""
    s = copy.deepcopy(settings)
    perms = s.get("permissions", {})
    for kind in ("deny", "allow"):
        if kind in perms:
            perms[kind] = [r for r in perms[kind] if r not in record.get(kind, [])]
            if not perms[kind]:
                del perms[kind]
    if record.get("removed_ask"):
        ask = perms.setdefault("ask", [])
        ask.extend(r for r in record["removed_ask"] if r not in ask)
    if not perms and "permissions" in s:
        del s["permissions"]
    pre = s.get("hooks", {}).get("PreToolUse")
    if pre is not None:
        for group in pre:
            group["hooks"] = [h for h in group.get("hooks", []) if not is_our_hook(h)]
        pre[:] = [g for g in pre if g.get("hooks")]
        if not pre:
            del s["hooks"]["PreToolUse"]
        if not s["hooks"]:
            del s["hooks"]
    sb_rec = record.get("sandbox") or {}
    if sb_rec and "sandbox" in s:
        sb = s["sandbox"]
        if "excludedCommands" in sb:
            sb["excludedCommands"] = [c for c in sb["excludedCommands"] if c not in sb_rec.get("excluded", [])]
            if not sb["excludedCommands"]:
                del sb["excludedCommands"]
        if "enabled_was" in sb_rec:
            if sb_rec["enabled_was"] is None:
                sb.pop("enabled", None)
            else:
                sb["enabled"] = sb_rec["enabled_was"]
        if not sb:
            del s["sandbox"]
    return s


def load(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf8") as f:
        text = f.read()
    if not text.strip():
        return {}
    data = json.loads(text)  # a ValueError stops everything: never overwrite a file we cannot read
    if not isinstance(data, dict):
        raise ValueError("the settings file does not hold a JSON object")
    return data


def write(path, data):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".settings-")
    with os.fdopen(fd, "w", encoding="utf8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    if os.path.exists(path):
        os.chmod(tmp, os.stat(path).st_mode & 0o777)
    os.replace(tmp, path)


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("action", choices=["plan", "apply", "remove"])
    p.add_argument("--settings", required=True)
    p.add_argument("--program")
    p.add_argument("--python", default="python3")
    p.add_argument("--sandbox", action="store_true")
    a = p.parse_args(argv)
    sidecar = a.settings + ".privfill.json"
    try:
        current = load(a.settings)
    except ValueError as e:
        print(f"Stopped: {a.settings} is not valid JSON ({e}); nothing was changed.", file=sys.stderr)
        return 1
    if a.action == "remove":
        if not os.path.exists(sidecar):
            print("privfill's guard configuration is not installed in this file; nothing was changed.")
            return 0
        with open(sidecar, encoding="utf8") as f:
            record = json.load(f)
        write(a.settings, unmerged(current, record))
        os.unlink(sidecar)
        print(f"Removed privfill's guard configuration from {a.settings}.")
        return 0
    if not a.program:
        print("--program is required", file=sys.stderr)
        return 1
    new, record = merged(current, additions(a.program, a.python, a.sandbox))
    if os.path.exists(sidecar):  # installed before: keep the first record of what the person had
        with open(sidecar, encoding="utf8") as f:
            old = json.load(f)
        for k in ("deny", "allow", "removed_ask"):
            record[k] = old.get(k, []) + [r for r in record[k] if r not in old.get(k, [])]
        if old.get("sandbox"):
            record["sandbox"] = {**record["sandbox"], **{k: v for k, v in old["sandbox"].items() if k == "enabled_was"}}
            record["sandbox"]["excluded"] = sorted(set(old["sandbox"].get("excluded", [])) | set(record["sandbox"].get("excluded", [])))
    if a.action == "plan":
        print(json.dumps(new, indent=2, ensure_ascii=False))
        return 0
    if new == current:
        print("Already installed; nothing changed.")
        return 0
    if os.path.exists(a.settings):
        with open(a.settings, encoding="utf8") as f, open(a.settings + ".privfill-backup", "w", encoding="utf8") as b:
            b.write(f.read())
    write(a.settings, new)
    with open(sidecar, "w", encoding="utf8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    print(f"Installed privfill's guard configuration in {a.settings} (previous version: {a.settings}.privfill-backup).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

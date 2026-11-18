"""A git pre-commit check that blocks a commit whose staged content holds a stored value.

The hook is a short shell script: git itself copies the staged version of every added, changed or
renamed file into a temporary directory, and `privfill audit` scans that directory. privfill never
starts git (it starts no process at all); git starts it. A hook that was there before is kept and
run first.
"""
import os
import re
import shlex
import sys

MARK = "# privfill pre-commit check"
SAVED = "pre-commit.before-privfill"

HOOK = """#!/bin/sh
{mark} (installed by `privfill precommit install`; `privfill precommit remove` takes it out)
# Blocks the commit if a staged file, or its name, contains a value stored in privfill.
# Only item names and counts are printed. To commit anyway: git commit --no-verify
hooks=$(dirname "$0")
if [ -x "$hooks/{saved}" ]; then
    "$hooks/{saved}" "$@" || exit $?
fi
tmp=$(mktemp -d) || exit 1
trap 'rm -rf "$tmp"' EXIT HUP INT TERM
git diff --cached --name-only -z --diff-filter=ACMR | git checkout-index -z --stdin --prefix="$tmp/" || exit 1
{program} audit "$tmp"
rc=$?
case $rc in
    0) exit 0 ;;
    6) echo "privfill: the staged changes contain stored personal details (see above); the commit was stopped." >&2
       echo "privfill: replace them with {{{{placeholders}}}}, or run 'privfill audit --window' on the files to see where." >&2
       exit 1 ;;
    *) echo "privfill: the check could not run (exit code $rc); the commit was stopped. git commit --no-verify skips it." >&2
       exit 1 ;;
esac
"""


class HookError(Exception):
    """The hook could not be installed or removed. The message holds no path."""


def git_dir(repo):
    """The repository's git directory, following a .git file (a worktree or submodule)."""
    dot = os.path.join(repo, ".git")
    if os.path.isdir(dot):
        return dot
    if os.path.isfile(dot):
        with open(dot, encoding="utf8") as f:
            m = re.match(r"gitdir:\s*(.+)", f.read().strip())
        if m:
            return os.path.normpath(os.path.join(repo, m.group(1)))
    raise HookError("that is not the top directory of a git repository")


def hooks_dir(repo):
    """Where git looks for hooks: core.hooksPath if the repository's config sets it, else the common git dir's hooks."""
    gd = git_dir(repo)
    common = gd
    if os.path.isfile(os.path.join(gd, "commondir")):
        with open(os.path.join(gd, "commondir"), encoding="utf8") as f:
            common = os.path.normpath(os.path.join(gd, f.read().strip()))
    try:
        with open(os.path.join(common, "config"), encoding="utf8") as f:
            config = f.read()
        m = re.search(r"^\s*hookspath\s*=\s*(.+?)\s*$", config, re.I | re.M)
    except OSError:
        m = None
    if m:
        return os.path.join(repo, os.path.expanduser(m.group(1).strip('"')))
    return os.path.join(common, "hooks")


def program_command():
    """How the hook runs this program: the interpreter and launcher doing the installing."""
    launcher = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "privfill.py")
    return " ".join(shlex.quote(p) for p in (sys.executable, launcher))


def is_ours(path):
    try:
        with open(path, encoding="utf8", errors="replace") as f:
            return MARK in f.read()
    except OSError:
        return False


def install(repo):
    """Install the check. Returns "installed", "updated", or "chained" (an earlier hook is kept and runs first)."""
    d = hooks_dir(repo)
    os.makedirs(d, exist_ok=True)
    hook, saved = os.path.join(d, "pre-commit"), os.path.join(d, SAVED)
    status = "installed"
    if os.path.lexists(hook):
        if is_ours(hook):
            status = "updated"
        elif os.path.lexists(saved):
            raise HookError("an earlier hook was already set aside; remove the check first")
        else:
            os.replace(hook, saved)
            status = "chained"
    with open(hook + ".tmp", "w", encoding="utf8") as f:
        f.write(HOOK.format(mark=MARK, saved=SAVED, program=program_command()))
    os.chmod(hook + ".tmp", 0o755)
    os.replace(hook + ".tmp", hook)
    return status


def remove(repo):
    """Remove the check and put back the hook that was there before. Returns "removed", "restored" or "absent"."""
    d = hooks_dir(repo)
    hook, saved = os.path.join(d, "pre-commit"), os.path.join(d, SAVED)
    if not is_ours(hook):
        return "absent"
    os.unlink(hook)
    if os.path.lexists(saved):
        os.replace(saved, hook)
        return "restored"
    return "removed"

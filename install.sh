#!/bin/sh
# Install privfill.
#   ./install.sh            program to ~/.privfill; the vault is created there on first use
#   ./install.sh --skill    also install the Claude Code skill to ~/.claude/skills/privfill
#   ./install.sh --claude   also put the guard into ~/.claude/settings.json (deny rules, hook, allow rules)
#   ./install.sh --claude-sandbox   the same, and turn on Claude Code's Bash sandbox
#   ./install.sh --claude-remove    take the guard configuration out of ~/.claude/settings.json again
# Only the program is copied: the launcher privfill.py and the privfill/ package beside it.
# An existing vault or data file is never read or overwritten.
set -eu

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${PRIVFILL_HOME:-$HOME/.privfill}"
SKILL=0
CLAUDE=""
for a in "$@"; do
    case "$a" in
        --skill) SKILL=1 ;;
        --claude) CLAUDE=apply ;;
        --claude-sandbox) CLAUDE=sandbox ;;
        --claude-remove) CLAUDE=remove ;;
        -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $a" >&2; exit 1 ;;
    esac
done

PY=""
for c in /usr/bin/python3 python3; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "python3 was not found" >&2; exit 1; }

# Copy the launcher and the package it needs into directory $1.
install_program() {
    mkdir -p "$1/privfill"
    rm -f "$1"/privfill/*.py  # modules dropped by a newer version must not linger
    cp "$SRC/privfill.py" "$1/privfill.py"
    cp "$SRC"/privfill/*.py "$1/privfill/"
    chmod 700 "$1/privfill.py" "$1/privfill"
}

mkdir -p "$DEST"
chmod 700 "$DEST"
install_program "$DEST"
echo "Program:   $DEST/privfill.py"
echo "Vault:     $DEST/vault.json (created the first time you enter a detail; unlocked by the desktop keyring, or a passphrase you choose)"
OLD=""
for n in profile.md 个人信息.md; do
    if [ -f "$DEST/$n" ]; then OLD="$DEST/$n"; break; fi
done

if [ "$SKILL" = 1 ]; then
    SK="$HOME/.claude/skills/privfill"
    mkdir -p "$SK"
    cp "$SRC/skills/privfill/SKILL.md" "$SK/SKILL.md"
    install_program "$SK"
    echo "Skill:     $SK"
fi

SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
case "$CLAUDE" in
    apply)   "$PY" "$SRC/tools/claude_settings.py" apply --settings "$SETTINGS" --program "$DEST/privfill.py" --python "$PY" ;;
    sandbox) "$PY" "$SRC/tools/claude_settings.py" apply --settings "$SETTINGS" --program "$DEST/privfill.py" --python "$PY" --sandbox ;;
    remove)  "$PY" "$SRC/tools/claude_settings.py" remove --settings "$SETTINGS" ;;
esac
if [ -n "$CLAUDE" ] && [ "$CLAUDE" != remove ]; then
    echo "Guard:     restart Claude Code, then follow docs/verify-guard.md to check it in a real session."
fi

if "$PY" -c "import PyQt6.QtWidgets" >/dev/null 2>&1; then
    echo "Form UI:   PyQt6"
else
    echo "Form UI:   PyQt6 not found, falling back to a plain Tk form."
    echo "           Install it for the full form: python-pyqt6, python3-pyqt6 or 'pip install PyQt6'."
fi

if "$PY" -c "import cryptography" >/dev/null 2>&1; then
    echo "Vault:     encryption library found"
else
    echo "Vault:     the Python library 'cryptography' is NOT installed; privfill cannot store or fill anything without it."
    echo "           Install it with the system package manager:"
    echo "           python-cryptography (Arch), python3-cryptography (Debian, Ubuntu, Fedora)."
fi

if "$PY" -c "import secretstorage" >/dev/null 2>&1; then
    echo "Keyring:   secretstorage found; a new vault is unlocked by the desktop keyring"
else
    echo "Keyring:   the Python library 'secretstorage' is not installed; a new vault will use a passphrase."
    echo "           For keyring unlock: python-secretstorage (Arch), python3-secretstorage (Debian, Ubuntu, Fedora)."
fi

if [ -n "$OLD" ]; then
    echo
    echo "Upgrade:   a plain-text data file from an earlier version is still there: $OLD"
    echo "           Move it into the encrypted vault with:  $PY $DEST/privfill.py migrate"
    echo "           It prints counts only and asks you, in a local window, before removing the old file."
fi

cat <<EOF

Next:
  1. Give your assistant the rules: see AGENT_GUIDE.md.
  2. Claude Code: ./install.sh --claude puts the guard into your settings (docs/claude-code.md);
     tools/managed-settings-wizard.sh moves it where only root can change it.
EOF

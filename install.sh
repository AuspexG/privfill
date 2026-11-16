#!/bin/sh
# Install privfill.
#   ./install.sh            program to ~/.privfill, data file created there
#   ./install.sh --skill    also install the Claude Code skill to ~/.claude/skills/privfill
# Only the program is copied. An existing data file is never read or overwritten.
set -eu

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${PRIVFILL_HOME:-$HOME/.privfill}"
SKILL=0
for a in "$@"; do
    case "$a" in
        --skill) SKILL=1 ;;
        -h|--help) sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $a" >&2; exit 1 ;;
    esac
done

PY=""
for c in /usr/bin/python3 python3; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "python3 was not found" >&2; exit 1; }

mkdir -p "$DEST"
chmod 700 "$DEST"
cp "$SRC/privfill.py" "$DEST/privfill.py"
chmod 700 "$DEST/privfill.py"
PRIVFILL_DATA="$DEST/profile.md" "$PY" "$DEST/privfill.py" init
echo "Program:   $DEST/privfill.py"
echo "Data file: $DEST/profile.md"

if [ "$SKILL" = 1 ]; then
    SK="$HOME/.claude/skills/privfill"
    mkdir -p "$SK"
    cp "$SRC/skills/privfill/SKILL.md" "$SK/SKILL.md"
    cp "$SRC/privfill.py" "$SK/privfill.py"
    chmod 700 "$SK/privfill.py"
    echo "Skill:     $SK"
fi

if "$PY" -c "import PyQt6.QtWidgets" >/dev/null 2>&1; then
    echo "Form UI:   PyQt6"
else
    echo "Form UI:   PyQt6 not found, falling back to a plain Tk form."
    echo "           Install it for the full form: python-pyqt6, python3-pyqt6 or 'pip install PyQt6'."
fi

cat <<EOF

Next:
  1. Give your assistant the rules: see AGENT_GUIDE.md.
  2. Claude Code: add the permission rules from docs/claude-code.md.
EOF

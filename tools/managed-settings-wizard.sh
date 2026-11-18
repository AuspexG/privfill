#!/bin/sh
# privfill: put the guard rules into Claude Code's managed settings, which only root can change.
#
#   tools/managed-settings-wizard.sh            install (or update)
#   tools/managed-settings-wizard.sh --remove   put the managed settings back as they were
#   tools/managed-settings-wizard.sh --verify   check that the file cannot be changed without root
#
# Run it yourself, in a terminal. It shows the exact change and asks before it uses sudo.
# The assistant must not run it: the point is that the assistant cannot change these rules.
set -eu

SRC="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${PRIVFILL_MANAGED_SETTINGS:-/etc/claude-code/managed-settings.json}"
BACKUP="$TARGET.privfill-backup"
NONE="$TARGET.privfill-none"     # marks that there was no managed settings file before
RECORD="$TARGET.privfill.json"
PROGRAM="${PRIVFILL_HOME:-$HOME/.privfill}/privfill.py"
SUDO="${PRIVFILL_SUDO-sudo}"     # tests set this to empty and point TARGET at a temporary directory
PY=""
for c in /usr/bin/python3 python3; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "python3 was not found" >&2; exit 1; }

say() { printf '%s\n' "$*"; }
confirm() {
    printf '%s [y/N] ' "$1"
    read -r answer || answer=""
    case "$answer" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

do_verify() {
    say "Checking $TARGET ..."
    if [ ! -e "$TARGET" ]; then
        say "  FAIL: the file does not exist."; return 1
    fi
    owner=$(stat -c %U "$TARGET")
    mode=$(stat -c %a "$TARGET")
    say "  owner: $owner, mode: $mode"
    if [ -w "$TARGET" ] || [ -w "$(dirname "$TARGET")" ]; then
        say "  FAIL: you (and so the assistant) can write the file or its directory without root."; return 1
    fi
    if ( : >> "$TARGET" ) 2>/dev/null; then
        say "  FAIL: appending to the file as $(id -un) worked."; return 1
    fi
    say "  ok: a write as $(id -un) is refused."
    if grep -q 'privfill.py guard' "$TARGET"; then
        say "  ok: the guard hook is in the file."
    else
        say "  FAIL: the guard hook is not in the file."; return 1
    fi
    say ""
    say "Last check, in Claude Code: restart it, then ask Claude to add a line to $TARGET."
    say "It must fail (permission denied). /status in Claude Code lists the managed settings as a source."
}

do_remove() {
    if [ ! -e "$RECORD" ]; then
        say "privfill's rules are not installed in $TARGET; nothing to do."; return 0
    fi
    if [ -e "$NONE" ]; then
        say "This will delete $TARGET (there was no managed settings file before privfill's)."
        confirm "Go ahead (uses $SUDO)?" || { say "Nothing was changed."; return 1; }
        $SUDO rm -f "$TARGET" "$RECORD" "$NONE"
    else
        say "This will put back the managed settings file as it was before privfill:"
        diff -u "$TARGET" "$BACKUP" || true
        confirm "Go ahead (uses $SUDO)?" || { say "Nothing was changed."; return 1; }
        $SUDO mv "$BACKUP" "$TARGET"
        $SUDO rm -f "$RECORD"
    fi
    say "Done. Restart Claude Code. The rules in ~/.claude/settings.json, if any, still apply (./install.sh --claude-remove)."
}

do_install() {
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    if [ -e "$TARGET" ]; then
        cp "$TARGET" "$tmp/managed-settings.json"
    fi
    if [ -e "$RECORD" ]; then
        cp "$RECORD" "$tmp/managed-settings.json.privfill.json"
    fi
    "$PY" "$SRC/tools/claude_settings.py" apply --settings "$tmp/managed-settings.json" \
        --program "$PROGRAM" --python "$PY" >/dev/null
    say "privfill will write the guard rules into $TARGET, owned by root, so that neither you"
    say "nor the assistant can change them without sudo. This is the exact change:"
    say ""
    if [ -e "$TARGET" ]; then
        diff -u "$TARGET" "$tmp/managed-settings.json" || true
    else
        diff -u /dev/null "$tmp/managed-settings.json" || true
    fi
    say ""
    if [ -e "$TARGET" ] && cmp -s "$TARGET" "$tmp/managed-settings.json"; then
        say "The managed settings already hold these rules; nothing to do."; return 0
    fi
    confirm "Write this (uses $SUDO)?" || { say "Nothing was changed."; return 1; }
    $SUDO mkdir -p "$(dirname "$TARGET")"
    if [ ! -e "$RECORD" ]; then  # first install: remember what was there, once
        if [ -e "$TARGET" ]; then
            $SUDO cp -p "$TARGET" "$BACKUP"
        else
            $SUDO touch "$NONE"
        fi
    fi
    $SUDO install -m 644 "$tmp/managed-settings.json" "$TARGET"
    $SUDO install -m 644 "$tmp/managed-settings.json.privfill.json" "$RECORD"
    if [ -n "$SUDO" ]; then
        $SUDO chown root:root "$TARGET" "$RECORD"
    fi
    say "Written. Restart Claude Code."
    say ""
    do_verify || true
}

case "${1:-}" in
    "") do_install ;;
    --remove) do_remove ;;
    --verify) do_verify ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//' ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
esac

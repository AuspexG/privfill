#!/bin/sh
# Rebuild docs/demo-en.gif and docs/demo-zh.gif.
#
# Everything runs against a throwaway vault with made-up data, in a fake HOME under the work
# directory; the real vault is never touched. The form frames are the real form drawn off-screen,
# and every line of command output in the GIF is what the program printed.
#
# Usage: tools/demo/make.sh [<python with Pillow>]
#   Needs PyQt6 and the cryptography library for python3, Pillow for the given Python
#   (default python3), and the Noto Sans CJK fonts in /usr/share/fonts/noto-cjk/.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PIL_PY="${1:-python3}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/privfill-demo.XXXXXX")"
for l in en zh; do
    sh "$HERE/run_demo.sh" "$l" "$WORK"
    python3 -I "$HERE/form_frames.py" "$WORK/work-$l" "$WORK/frames-$l"
    "$PIL_PY" -I "$HERE/compose.py" "$l" "$REPO/docs/demo-$l.gif" "$WORK"
done
rm -rf "$WORK"

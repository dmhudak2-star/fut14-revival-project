#!/bin/zsh
# Walk from wherever the title is to Ultimate Team.
#
# The screen navigator gives up when the title sits on "APPUYEZ SUR START", and
# every missing route drops the flow back to the FIFA main menu, so this walk
# gets repeated often. START, then A through the notices, then Ultimate Team.
set -u
HOST=${HOST:-192.168.1.25}
REPO=${REPO:-~/Downloads/fifa14-fut-stable/fifa14-fut-offline-revival}
PY="$REPO/.venv/bin/python"
press() { "$PY" "$REPO/tools/xbox360_virtual_input.py" "$HOST" press "$1" --frames 6 >/dev/null 2>&1; sleep "${2:-3}"; }
shot()  { "$PY" "$REPO/tools/xbdm_screenshot.py" "$HOST" "$REPO/runtime/screens/walk-$1-$(date +%H%M%S).png" >/dev/null 2>&1; }

print "== START"
press START 8
print "== clear notices / storage prompts"
for _ in 1 2 3 4; do press A 4; done
shot menu
print "== Ultimate Team"
press A 30
shot fut
print "done; newest screenshots:"
ls -t "$REPO"/runtime/screens/walk-*.png | head -2

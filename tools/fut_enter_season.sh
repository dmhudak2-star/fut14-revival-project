#!/bin/zsh
# Walk from the FIFA main menu into FUT and on to Saison Joueur Solo.
#
# Each missing route drops the title back to the FIFA main menu, so this walk
# gets repeated once per route discovered.  Doing it by hand costs a couple of
# minutes and a screenshot each time; this makes an iteration one command.
set -u
HOST=${HOST:-192.168.1.25}
REPO=${REPO:-~/Downloads/fifa14-fut-stable/fifa14-fut-offline-revival}
PY="$REPO/.venv/bin/python"
press() { "$PY" "$REPO/tools/xbox360_virtual_input.py" "$HOST" press "$1" --frames 6 >/dev/null 2>&1; sleep "${2:-3}"; }

print "== enter Ultimate Team"
press A 25
print "== JOUER tab"
press RB 4
print "== Saison Joueur Solo"
press A 15

shot="$REPO/runtime/screens/season-walk-$(date +%H%M%S).png"
"$PY" "$REPO/tools/xbdm_screenshot.py" "$HOST" "$shot" >/dev/null 2>&1
print "screenshot: $shot"

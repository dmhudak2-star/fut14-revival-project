#!/bin/zsh
# Start FIFA 14 FUT, end to end, without anyone driving it.
#
#   tools/fut.sh            launch and patch, then hand the pad back
#   tools/fut.sh --patch    apply the menu patch only (title already up)
#   tools/fut.sh --server   restart the server only
#
# Entering FUT needs two runtime patches and they are not interchangeable:
#
#   1. at launch      hostnames, plaintext redirector, native FUT-resource
#                     redirect -- applied while the title boots
#   2. at the FIFA    three TU3 helperFunctions continuation branches
#      main menu      -- and it has to land *before* Ultimate Team is picked
#
# Applied from inside the FUT loader the second one does nothing: the launcher
# has already been walked past, the trace stops at accountinfo, /ut/auth never
# follows and CardsDLL is never mapped. That ordering is the whole reason this
# script exists.
#
# It also clears the account state and restarts the Blaze server first, because
# the title rewrites that state from its in-memory session within seconds of
# the file being cleared -- so re-entering FUT without a relaunch cannot work.
set -u

REPO=${REPO:-~/Downloads/fifa14-fut-stable/fifa14-fut-offline-revival}
XBOX=${XBOX:-192.168.1.25}
MAC=${MAC:-192.168.1.36}
TITLE=${TITLE:-'Hdd:\Games\FIFA 14'}
PY="$REPO/.venv/bin/python"

cd "$REPO" || { print -u2 "repo introuvable: $REPO"; exit 1 }

step() { print "\n== $1" }
fail() { print -u2 "\n!! $1"; exit 1 }

start_server() {
    step "serveur"
    print '{}' > runtime/local-account.json
    pkill -f "server/fifa14_blaze_server.py" 2>/dev/null
    sleep 2
    local journal="runtime/live-easw-$(date +%Y%m%d-%H%M%S).jsonl"
    nohup "$PY" server/fifa14_blaze_server.py \
        --listen 0.0.0.0 --advertise "$MAC" \
        --ports 10041,42124,42126,42127 \
        --journal "$journal" \
        --account-state runtime/local-account.json \
        >> runtime/server.log 2>&1 &
    sleep 4
    if ! pgrep -f "server/fifa14_blaze_server.py" >/dev/null; then
        fail "le serveur n'a pas démarré -- voir runtime/server.log"
    fi
    print "   démarré, journal $journal"
}

launch_title() {
    step "lancement du titre"
    "$PY" tools/fifa14_early_local_server.py "$XBOX" \
        --local-ip "$MAC" --timeout 900 --launch-title "$TITLE" \
        --redirector-transport plaintext --redirect-fut-resource 2>&1 | tail -1
}

reach_menu() {
    step "navigation vers le menu principal"
    # The pad keeps its last button after the frame counter drains, and a held
    # button produces no edge -- so nothing the title watches for ever fires.
    # Clearing it first costs a second and saves the whole run.
    "$PY" tools/xbox360_virtual_input.py "$XBOX" restore >/dev/null 2>&1
    sleep 1
    "$PY" tools/xbox360_virtual_input.py "$XBOX" apply >/dev/null 2>&1
    "$PY" tools/fifa14_screen_navigator.py "$XBOX" goto main_menu --timeout 540 2>&1 | tail -1
}

apply_patch() {
    step "patch helperFunctions"
    "$PY" tools/fifa14_tu3_helperfunctions_runtime_patch.py "$XBOX" \
        --timeout 540 --chunk-size 0x800000 2>&1 | tail -1
}

release_pad() {
    "$PY" tools/xbox360_virtual_input.py "$XBOX" restore >/dev/null 2>&1
}

case "${1:-}" in
    --server) start_server; exit 0 ;;
    --patch)  apply_patch; release_pad; exit 0 ;;
esac

start_server
launch_title
reach_menu || fail "le menu principal n'a pas été atteint"
apply_patch
release_pad

print "\n=========================================="
print " PRÊT. Manette rendue, tu es au menu principal."
print " Lance Ultimate Team."
print "=========================================="

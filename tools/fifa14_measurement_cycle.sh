#!/bin/zsh
# One full measurement cycle: relaunch the title, arm the passive traces,
# navigate to Ultimate Team, then read what the traces recorded.
#
# The cycle assumes nothing about where the console starts from except that
# XBDM answers; fifa14_overnight_driver.sh is what makes that true and reruns
# this on failure.
set -u

REPO=${REPO:-${0:a:h:h}}
cd "$REPO" || exit 1

XBOX=${XBOX:-192.168.1.25}
MAC=${MAC:-192.168.1.36}
JOURNAL=${JOURNAL:-$REPO/runtime/live-easw-v46.jsonl}
TITLE=${TITLE:-'Hdd:\Games\FIFA 14'}

# A previous cycle killed mid-flight leaves a navigator or a modload listener
# holding the console, which makes this one look like a console fault.
pkill -f screen_navigator 2>/dev/null
pkill -f fut_api_trace 2>/dev/null
sleep 1

print "== relaunch title"
python3 tools/fifa14_early_local_server.py "$XBOX" --local-ip "$MAC" \
    --timeout 900 --launch-title "$TITLE" \
    --redirector-transport plaintext --redirect-fut-resource 2>&1 | tail -2

print "== arm virtual input"
python3 tools/xbox360_virtual_input.py "$XBOX" apply 2>&1 | tail -1

print "== navigate to main menu"
python3 tools/fifa14_screen_navigator.py "$XBOX" goto main_menu --timeout 900 || exit 2

print "== arm notification listener trace"
python3 tools/fifa14_fut_notification_listener_trace.py "$XBOX" apply 2>&1 | tail -1

# The helperFunctions APT patch is deliberately not run here.  The cycle that
# failed to find the APT still reached CardsDLL load, the security question and
# the same FUT loader state, with the same two recorded calls -- so it buys
# nothing at this stage while costing ~20 min of heap sweep per cycle.  Run
# tools/fifa14_tu3_helperfunctions_runtime_patch.py by hand if a gate needs it.

print "== arm FUT API traces on CardsDLL load"
python3 tools/fifa14_fut_api_trace.py "$XBOX" arm-on-load --timeout 600 \
    > runtime/cycle-arm.log 2>&1 &
arm_pid=$!
sleep 4

print "== select FUT"
python3 tools/xbox360_virtual_input.py "$XBOX" press A --frames 12 >/dev/null 2>&1

# The security question needs its pre-filled answer confirmed, then the
# acceptance dialog dismissed.  Wait for the server to see the question so the
# presses land on that screen rather than on whatever precedes it.
(
    for _ in {1..90}; do
        if grep -q fut_phishing_question_request "$JOURNAL" 2>/dev/null; then
            /bin/sleep 8
            python3 tools/xbox360_virtual_input.py "$XBOX" press DOWN --frames 8 >/dev/null 2>&1
            /bin/sleep 3
            python3 tools/xbox360_virtual_input.py "$XBOX" press A --frames 12 >/dev/null 2>&1
            /bin/sleep 14
            python3 tools/xbox360_virtual_input.py "$XBOX" press A --frames 12 >/dev/null 2>&1
            print "== security answered"
            break
        fi
        /bin/sleep 5
    done
) &

wait $arm_pid
cat runtime/cycle-arm.log

# The FUT API journal lives inside CardsDLL, so it can only be read while that
# module is loaded.  When the bootstrap gives up, FUT unloads it and a single
# late read returns nothing but "module missing" -- which is indistinguishable
# from the operations never having been called.  Sample while it is still up.
for at in 60 130 200; do
    print "== read at ${at}s"
    sleep $(( at == 60 ? 60 : 70 ))
    python3 tools/fifa14_fut_api_trace.py "$XBOX" read 2>&1
    python3 tools/fifa14_screen_navigator.py "$XBOX" identify 2>&1
done

# This one is in default.xex and survives FUT unloading, so it is read last.
print "== read notification bus"
python3 tools/fifa14_fut_notification_listener_trace.py "$XBOX" read 2>&1
print "== CYCLE_DONE"

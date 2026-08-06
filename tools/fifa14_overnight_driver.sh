#!/bin/zsh
# Run measurement cycles unattended, recovering from the faults that ended a
# night's run before this existed:
#
#   * the console sitting at the dashboard with no title loaded, because a
#     magicboot landed while nothing was driving it;
#   * a cycle killed mid-flight leaving a navigator holding the console;
#   * the local Blaze server not running, which turns every cycle into a
#     no-op that still costs twenty minutes.
#
# Each cycle's full output is kept, so a morning reader can see what every
# attempt did rather than only the last one.
set -u

REPO=${REPO:-${0:a:h:h}}
cd "$REPO" || exit 1

XBOX=${XBOX:-192.168.1.25}
MAC=${MAC:-192.168.1.36}
TITLE=${TITLE:-'Hdd:\Games\FIFA 14'}
RUNS=${RUNS:-100}
LOGDIR=${LOGDIR:-$REPO/runtime/overnight}

mkdir -p "$LOGDIR"

console_up() {
    python3 - "$XBOX" <<'PY'
import sys, socket
sys.path.insert(0, "tools")
from fifa14_plain_send_hook import Xbdm
try:
    client = Xbdm(sys.argv[1])
except Exception:
    raise SystemExit(1)
client.close()
raise SystemExit(0)
PY
}

title_loaded() {
    python3 - "$XBOX" <<'PY'
import sys
sys.path.insert(0, "tools")
from fifa14_plain_send_hook import Xbdm
try:
    client = Xbdm(sys.argv[1])
    names = [
        line.split('name="')[1].split('"')[0]
        for line in client.multiline("modules")
        if 'name="' in line
    ]
    client.close()
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if any("default.xex" in name for name in names) else 1)
PY
}

boot_title() {
    python3 - "$XBOX" "$TITLE" <<'PY'
import sys
sys.path.insert(0, "tools")
from fifa14_plain_send_hook import Xbdm
host, directory = sys.argv[1], sys.argv[2]
command = f'magicboot title="{directory}\\default.xex" directory="{directory}"'
try:
    client = Xbdm(host)
    client.sock.sendall((command + "\r\n").encode("ascii"))
except Exception:
    # The console drops the connection as it boots; that is the success path.
    pass
PY
}

server_up() {
    pgrep -f fifa14_blaze_server.py >/dev/null
}

for run in $(seq 1 $RUNS); do
    stamp=$(date +%Y%m%d-%H%M%S)
    log="$LOGDIR/cycle-$stamp.log"
    print "=== run $run/$RUNS -> $log"

    if ! server_up; then
        print "!! the local Blaze server is not running; every cycle would be"
        print "!! a no-op, so stopping rather than burning the night on it."
        exit 1
    fi

    if ! console_up; then
        print "-- console unreachable, waiting"
        sleep 60
        continue
    fi

    if ! title_loaded; then
        print "-- no title loaded, booting it"
        boot_title
        # Booting drops XBDM; give the title time to come back before a cycle
        # tries to talk to it, or the cycle spends its whole timeout retrying.
        for _ in $(seq 1 30); do
            sleep 10
            title_loaded && break
        done
    fi

    zsh tools/fifa14_measurement_cycle.sh > "$log" 2>&1
    status=$?
    print "-- cycle exited $status"
    grep -E 'call\(s\)|never called|notifications carrying|^screen =|CYCLE_DONE' "$log" | tail -20
done

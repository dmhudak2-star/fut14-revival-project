#!/bin/zsh
# Patch FIFA 14 whenever it starts, however it was started.
#
# The runtime patches -- the hostname redirect, the plaintext redirector, the
# native FUT-resource redirect -- live in memory, so they have to be applied
# once per launch. Until now that only happened when the title was launched
# through fifa14_early_local_server.py, which meant starting the game from the
# console by hand produced an unpatched FIFA that talks to nobody.
#
# fifa14_early_local_server.py already knows how to wait: without
# --launch-title it subscribes to the debug notification channel and patches on
# the modload event. All that was missing was something to keep it waiting. So
# this loops it: patch, then go straight back to waiting for the next launch.
#
# It also keeps the Blaze server up, because a patched title with no server is
# no more playable than an unpatched one.
#
#   zsh tools/fifa14_autopatch.sh              run in the foreground
#   tools/fifa14_autopatch.sh --install        install as a login agent
#
set -u

REPO=${REPO:-~/Downloads/fifa14-fut-stable/fifa14-fut-offline-revival}
XBOX=${XBOX:-192.168.1.25}
MAC=${MAC:-192.168.1.36}
PY="$REPO/.venv/bin/python"
LABEL=com.fifa14.autopatch
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ "${1:-}" == "--install" ]]; then
    mkdir -p "$HOME/Library/LaunchAgents" "$REPO/runtime"
    cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>$REPO/tools/fifa14_autopatch.sh</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$REPO/runtime/autopatch.log</string>
  <key>StandardErrorPath</key><string>$REPO/runtime/autopatch.log</string>
</dict>
</plist>
PLIST
    launchctl unload "$PLIST" 2>/dev/null
    launchctl load "$PLIST" || exit 1
    print "installed and started: $LABEL"
    print "log: $REPO/runtime/autopatch.log"
    print "stop with: launchctl unload $PLIST"
    exit 0
fi

if [[ "${1:-}" == "--uninstall" ]]; then
    launchctl unload "$PLIST" 2>/dev/null
    rm -f "$PLIST"
    print "removed: $LABEL"
    exit 0
fi

print "autopatch watching $XBOX (server on $MAC)"

server_up() {
    ps -ax -o command= | grep -q '[f]ifa14_blaze_server.py'
}

start_server() {
    print "$(date '+%H:%M:%S') starting the Blaze server"
    ( cd "$REPO" && nohup "$PY" server/fifa14_blaze_server.py \
        --listen 0.0.0.0 --advertise "$MAC" \
        --ports 10041,42124,42126,42127 \
        --journal runtime/live-easw-v60.jsonl \
        --account-state runtime/local-account.json \
        >> runtime/server-autopatch.log 2>&1 & )
    sleep 3
}

while true; do
    server_up || start_server

    # Waits for the modload event and patches on it. Returns once the title is
    # patched and running, or once the timeout expires with no launch -- either
    # way the next pass goes back to waiting, so a console left idle overnight
    # costs nothing and a launch at any hour is caught.
    "$PY" "$REPO/tools/fifa14_early_local_server.py" "$XBOX" \
        --local-ip "$MAC" --timeout 3600 \
        --redirector-transport plaintext --redirect-fut-resource 2>&1 \
        | sed "s/^/$(date '+%H:%M:%S') /"

    # A console that is off or rebooting refuses the debug connection
    # immediately, and retrying in a tight loop would spam the log for hours.
    sleep 5
done

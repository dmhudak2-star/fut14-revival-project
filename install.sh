#!/bin/sh
# One command to install the console client, on Termux or anywhere else.
#
#   curl -sL https://raw.githubusercontent.com/nygmasx/fut14-revival-project/main/install.sh | sh -s -- 192.168.1.50
#
# The argument is your Xbox's IP. Leave it out and the config keeps its
# placeholder, which you then edit by hand.
#
# Why this exists: the manual version is six steps, and when one of them fails
# on somebody else's phone the report that comes back is "still nothing". Every
# step here says what it did, and the failures say what to do about them.
set -u

RELEASE="https://github.com/nygmasx/fut14-revival-project/releases/download/client-2026.08.20/fifa14-revival-client.tgz"
TARBALL="fifa14-revival-client.tgz"
DIR="fifa14-revival-client"
XBOX="${1:-}"

say()  { printf '\n== %s\n' "$1"; }
ok()   { printf '   ok: %s\n' "$1"; }
die()  { printf '\n!! %s\n' "$1" >&2; exit 1; }

say "checking what is installed"
if command -v pkg >/dev/null 2>&1; then
    # Termux. Install quietly; if it fails we find out from the checks below
    # rather than from a wall of package manager output.
    pkg install -y python curl tar >/dev/null 2>&1 || true
fi
command -v python3 >/dev/null 2>&1 || die "python3 is missing. On Termux: pkg install python"
command -v curl    >/dev/null 2>&1 || die "curl is missing. On Termux: pkg install curl"
ok "python3 $(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)"

# 3.10 is the floor: the server and the patchers use match statements and the
# newer typing syntax. A phone with 3.9 fails later, in an import, from inside
# a subprocess, which reads like a broken download.
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' \
    || die "python3 is older than 3.10. On Termux: pkg upgrade python"

say "downloading"
cd "$HOME" 2>/dev/null || die "cannot enter \$HOME"
rm -f "$TARBALL"
# -f so an HTTP error is an error instead of a 9-byte file called a tarball,
# which is exactly how "tar: not in gzip format" happens.
curl -fL --progress-bar --retry 3 -o "$TARBALL" "$RELEASE" || die "download failed. Check the phone is online."
SIZE=$(wc -c < "$TARBALL" | tr -d ' ')
[ "$SIZE" -gt 40000 ] 2>/dev/null || die "downloaded only $SIZE bytes -- that is not the package."
ok "$TARBALL, $SIZE bytes, in $HOME"

say "extracting"
rm -rf "$DIR"
tar xzf "$TARBALL" || die "tar failed. Delete $TARBALL and run this again."
[ -f "$DIR/tools/revival_client.py" ] || die "the package is missing revival_client.py"
ok "$HOME/$DIR"

say "configuring"
cd "$DIR" || die "cannot enter $DIR"
[ -f fifa14revival.ini ] || cp fifa14revival.example.ini fifa14revival.ini
if [ -n "$XBOX" ]; then
    python3 - "$XBOX" <<'PY'
import re, sys, pathlib
ip = sys.argv[1]
path = pathlib.Path("fifa14revival.ini")
text = re.sub(r"(?m)^\s*address\s*=.*$", f"address = {ip}", path.read_text(), count=1)
path.write_text(text)
print(f"   ok: console address = {ip}")
PY
else
    printf '   !! no Xbox IP given. Edit fifa14revival.ini and set, under [console]:\n'
    printf '        address = <your Xbox IP>\n'
fi
printf '   ok: server = %s\n' "$(python3 tools/revival_config.py server.host 2>/dev/null)"

say "checking the client runs"
python3 tools/revival_client.py --help >/dev/null 2>&1 \
    || die "the client will not start. Send the output of: python3 tools/revival_client.py --help"
ok "client runs"

if [ -n "$XBOX" ]; then
    say "checking the console answers XBDM on $XBOX"
    python3 - "$XBOX" <<'PY'
import socket, sys
host = sys.argv[1]
try:
    link = socket.create_connection((host, 730), timeout=6)
    banner = link.recv(64).decode("latin-1", "replace").strip()
    link.close()
    print(f"   ok: {banner}")
except OSError as error:
    print(f"   !! no answer on {host}:730 -- {error}")
    print("      The console must be ON, on the same Wi-Fi, and XBDM must be")
    print("      loaded as a Dashlaunch plugin:  pluginN = Usb:\\xbdm.xex")
    print("      If you have a launch.ini on the HDD *and* on the USB stick,")
    print("      the USB one is the one Dashlaunch reads.")
PY
fi

cat <<'DONE'

==========================================
 Installed. To play, with the console
 sitting on the dashboard:

   cd ~/fifa14-revival-client
   python3 tools/revival_client.py

 Leave this window open while you play.
 On Termux: notification shade -> Termux
 -> Acquire wakelock, or Android kills it.
==========================================
DONE

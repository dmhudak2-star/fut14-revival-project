#!/bin/zsh

set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT=${SCRIPT_DIR:h}
PYTHON=${PYTHON:-python3}
XBOX=${FIFA14_XBOX_IP:-192.168.1.25}
MAC=${FIFA14_SERVER_IP:-192.168.1.36}
RUNTIME="$PROJECT/runtime"
JOURNAL=${FIFA14_JOURNAL:-$RUNTIME/live-blaze-auth-next.jsonl}
SERVER_LOG=${FIFA14_SERVER_LOG:-$RUNTIME/live-blaze-auth-next.log}
SERVER_ERR=${FIFA14_SERVER_ERR:-$RUNTIME/live-blaze-auth-next.err.log}
EARLY_LOG=${FIFA14_EARLY_LOG:-$RUNTIME/live-early-auth-next.log}
CERT_DIR="$RUNTIME/old-protossl"
CERT="$CERT_DIR/gosredirector-old-protossl.crt.pem"
KEY="$CERT_DIR/gosredirector.key.pem"
# The retail Xbox Redirector negotiates EA's OldProtoSSL, which Python's
# OpenSSL rejects at the record layer ("wrong version number"), so the title
# reports "EA servers unavailable" before it ever logs in.  Plaintext is the
# transport this project has actually driven to a local login end to end.
# Export FIFA14_REDIRECTOR_TRANSPORT=tls only to work on that handshake.
TRANSPORT=${FIFA14_REDIRECTOR_TRANSPORT:-plaintext}
# Set to an installed title directory to boot it over XBDM instead of doing it
# by hand from XeXMenu.  Empty keeps the original manual-launch behaviour.
TITLE_DIRECTORY=${FIFA14_TITLE_DIRECTORY:-}

mkdir -p "$RUNTIME"
cd "$PROJECT"

server_pid=''
cleanup() {
    if [[ -n "$server_pid" ]]; then
        /bin/kill "$server_pid" 2>/dev/null || true
    fi
}
trap cleanup INT TERM HUP EXIT

print "Project: $PROJECT"
print "Xbox:   $XBOX"
print "Server: $MAC"

server_tls_arguments=()
if [[ "$TRANSPORT" == "tls" ]]; then
    if [[ ! -f "$CERT" || ! -f "$KEY" ]]; then
        "$PYTHON" tools/generate_old_protossl_certificate.py \
            --output "$CERT_DIR"
    fi
    server_tls_arguments=(
        --redirector-tls-ports 42124,42126,42127
        --redirector-tls-cert "$CERT"
        --redirector-tls-key "$KEY"
    )
fi

print "Redirector transport: $TRANSPORT"

if ! /usr/bin/curl -fsS --max-time 1 \
    http://127.0.0.1:18080/futBoot.xml >/dev/null 2>&1; then
    : >| "$SERVER_LOG"
    : >| "$SERVER_ERR"
    "$PYTHON" server/fifa14_blaze_server.py \
        --listen 0.0.0.0 \
        --advertise "$MAC" \
        --ports 10041,42124,42126,42127 \
        "${server_tls_arguments[@]}" \
        --journal "$JOURNAL" \
        --account-state runtime/local-account.json \
        >>"$SERVER_LOG" 2>>"$SERVER_ERR" &
    server_pid=$!

    for attempt in {1..50}; do
        if /usr/bin/curl -fsS --max-time 1 \
            http://127.0.0.1:18080/futBoot.xml >/dev/null 2>&1; then
            break
        fi
        /bin/sleep 0.1
    done
fi

if ! /usr/bin/curl -fsS --max-time 1 \
    http://127.0.0.1:18080/futBoot.xml >/dev/null 2>&1; then
    print "ERROR: le serveur Blaze/HTTP local ne répond pas sur 18080."
    [[ -f "$SERVER_ERR" ]] && /usr/bin/tail -40 "$SERVER_ERR"
    exit 1
fi

print "SERVER_READY"
early_launch_arguments=()
if [[ -n "$TITLE_DIRECTORY" ]]; then
    print "Lancement automatique de $TITLE_DIRECTORY via XBDM."
    early_launch_arguments=(--launch-title "$TITLE_DIRECTORY")
else
    print "Lance maintenant XeXMenu, puis FIFA 14 depuis XeXMenu."
fi

set +e
"$PYTHON" tools/fifa14_early_local_server.py \
    "$XBOX" \
    --local-ip "$MAC" \
    --timeout 600 \
    "${early_launch_arguments[@]}" \
    --redirector-transport "$TRANSPORT" \
    --redirect-fut-resource \
    --trace-ion-unload \
    --trace-fut-launcher-transition \
    --trace-nav-transition-dispatch \
    2>&1 | /usr/bin/tee "$EARLY_LOG"
early_status=${pipestatus[1]}
set -e
if (( early_status != 0 )); then
    print "ERROR: le lanceur précoce a quitté avec le statut $early_status."
    exit "$early_status"
fi

"$PYTHON" tools/fifa14_cards_auth_runtime_setup.py \
    "$XBOX" \
    --local-ip "$MAC" \
    --port 18080 \
    --timeout 300

"$PYTHON" tools/fifa14_tu3_helperfunctions_runtime_patch.py \
    "$XBOX" \
    --timeout 300 \
    --chunk-size 0x800000

print
print "READY_FOR_FUT_CLICK"
print "Clique sur FUT. Le prochain jalon attendu est fut_ut_auth_request /pow/auth."
print
"$PYTHON" tools/fifa14_cards_auth_endpoint_patch.py \
    "$XBOX" status --local-ip "$MAC" --port 18080
"$PYTHON" tools/fifa14_cards_auth_credentials_patch.py "$XBOX" status

if [[ -n "$server_pid" ]]; then
    print
    print "Journal serveur: $JOURNAL"
    print "Surveillance en direct (Ctrl-C pour arrêter la session locale)."
    /usr/bin/tail -n 0 -F "$JOURNAL"
else
    print
    print "Un serveur local existant est déjà actif. Garde son terminal ouvert."
    while /usr/bin/curl -fsS --max-time 1 \
        http://127.0.0.1:18080/futBoot.xml >/dev/null 2>&1; do
        /bin/sleep 2
    done
    print "Le serveur local existant ne répond plus."
    exit 1
fi

#!/usr/bin/env python3
"""Listen on the EAS FC session port and record whatever the module sends.

The EAS FC banner has survived two theories. Pointing the module's endpoint at
this server at launch produced no connection at all; re-pointing it live, with
the title up and in FUT, produced none either -- ten minutes, nothing. So the
question underneath both is still open, and it is a simple one:

    does the module dial at all, and if it does, what does it say?

This answers it without assuming the answer. The endpoint is rewritten to this
machine on the module's **own port**, 8094, rather than onto the Blaze core
port -- so nothing here replies in FIFA's dialect and mistakes a wrong answer
for silence. Every byte that arrives is logged as hex, and the socket is held
open, because a listener that closes immediately is indistinguishable from a
listener that was never reached.

    tools/easfc_listen.py --local-ip 192.168.1.40

Ctrl-C stops it. It does not patch anything back; run
`tools/fifa14_easfc_endpoint_patch.py` or relaunch to restore the usual
routing.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fifa14_plain_send_hook import Xbdm  # noqa: E402

SESSION = (0x89706250, b"pal.gt.easfc.ea.com:8094")


def point_at(host: str, local: str, port: int) -> bool:
    """Rewrite the session endpoint to this machine, keeping the module's port."""
    replacement = f"{local}:{port}".encode()
    original = SESSION[1]
    if len(replacement) > len(original):
        print("replacement is longer than the string it replaces")
        return False
    client = Xbdm(host)
    try:
        current = client.read(SESSION[0], len(original) + 1)
        if current[: len(replacement)] == replacement:
            return True
        if current[: len(original)] != original:
            # Already pointed somewhere else -- most likely at the Blaze core
            # port by the launcher. Overwrite it anyway; that is the point.
            pass
        client.write(
            SESSION[0],
            replacement + b"\x00" * (len(original) + 1 - len(replacement)),
        )
        return client.read(SESSION[0], len(replacement)) == replacement
    finally:
        try:
            client.close()
        except Exception:
            pass


def serve(port: int) -> None:
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", port))
    listener.listen(4)
    print(f"écoute sur 0.0.0.0:{port}", flush=True)

    def handle(sock: socket.socket, peer) -> None:
        stamp = time.strftime("%H:%M:%S")
        print(f"{stamp}  CONNEXION de {peer[0]}:{peer[1]}", flush=True)
        sock.settimeout(120)
        total = 0
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    print(f"{time.strftime('%H:%M:%S')}  fermée par le pair "
                          f"après {total} octets", flush=True)
                    return
                total += len(chunk)
                print(f"{time.strftime('%H:%M:%S')}  {len(chunk)} octets: "
                      f"{chunk[:64].hex(' ')}", flush=True)
        except socket.timeout:
            print(f"{time.strftime('%H:%M:%S')}  silence, {total} octets reçus",
                  flush=True)
        except Exception as error:
            print(f"{time.strftime('%H:%M:%S')}  {type(error).__name__}: {error}",
                  flush=True)
        finally:
            sock.close()

    while True:
        sock, peer = listener.accept()
        threading.Thread(target=handle, args=(sock, peer), daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="192.168.1.25")
    parser.add_argument("--local-ip", required=True)
    parser.add_argument("--port", type=int, default=8094)
    parser.add_argument("--no-patch", action="store_true")
    args = parser.parse_args()

    if not args.no_patch:
        if point_at(args.host, args.local_ip, args.port):
            print(f"endpoint de session -> {args.local_ip}:{args.port}")
        else:
            print("le patch de l'endpoint a échoué")
            return 1
    serve(args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

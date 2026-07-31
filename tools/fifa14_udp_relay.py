#!/usr/bin/env python3
"""Relay FIFA 14's Xbox 360 UDP/3074 traffic through the Mac.

The Xbox endpoint is redirected to the Mac in memory. This relay forwards the
unchanged secure payload to the original EA endpoint and sends replies back
from the Mac's UDP/3074 socket.
"""

from __future__ import annotations

import argparse
import socket
import time


def timestamp() -> str:
    return time.strftime("%H:%M:%S")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3074)
    parser.add_argument("--xbox", default="192.0.2.25")
    parser.add_argument("--upstream", default="159.153.52.75")
    parser.add_argument("--upstream-port", type=int, default=3074)
    args = parser.parse_args()

    upstream = (args.upstream, args.upstream_port)
    client: tuple[str, int] | None = None

    relay = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    relay.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    relay.bind((args.listen, args.port))

    print(
        f"Listening on {args.listen}:{args.port}; "
        f"upstream {args.upstream}:{args.upstream_port}",
        flush=True,
    )
    while True:
        payload, sender = relay.recvfrom(65535)
        if sender[0] == args.xbox:
            client = sender
            relay.sendto(payload, upstream)
            print(
                f"{timestamp()} Xbox -> EA  {len(payload):4d} "
                f"{payload.hex()}",
                flush=True,
            )
        elif sender == upstream:
            if client is None:
                print(
                    f"{timestamp()} EA reply dropped (Xbox endpoint unknown)",
                    flush=True,
                )
                continue
            relay.sendto(payload, client)
            print(
                f"{timestamp()} EA -> Xbox  {len(payload):4d} "
                f"{payload.hex()}",
                flush=True,
            )
        else:
            print(
                f"{timestamp()} Ignored {sender[0]}:{sender[1]} "
                f"{len(payload)} bytes",
                flush=True,
            )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nRelay stopped.")
        raise SystemExit(130)

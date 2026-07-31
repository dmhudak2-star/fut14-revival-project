#!/usr/bin/env python3
"""Observe the first connection to the local FIFA 14 Blaze endpoint."""

from __future__ import annotations

import argparse
import socket
import time
from pathlib import Path

from blaze_tdf import decode_frame


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="192.0.2.35")
    parser.add_argument("--port", type=int, default=10041)
    parser.add_argument(
        "--output", type=Path, default=Path("outputs/fifa14_blaze_local")
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.listen, args.port))
    server.listen(4)
    print(f"BLAZE_LISTENER_READY {args.listen}:{args.port}", flush=True)
    connection_index = 0
    while True:
        client, peer = server.accept()
        connection_index += 1
        destination = args.output / (
            f"connection_{connection_index:03d}_{stamp()}.bin"
        )
        print(
            f"Connection {connection_index} from {peer[0]}:{peer[1]}",
            flush=True,
        )
        client.settimeout(30)
        capture = bytearray()
        try:
            while True:
                block = client.recv(65535)
                if not block:
                    break
                capture.extend(block)
                destination.write_bytes(capture)
                print(
                    f"  received {len(block)} bytes "
                    f"(total {len(capture)}): {block[:96].hex().upper()}",
                    flush=True,
                )
                if len(capture) >= 12:
                    frame_length = 12 + int.from_bytes(capture[0:2], "big")
                    if len(capture) >= frame_length:
                        try:
                            frame = decode_frame(bytes(capture[:frame_length]))
                            print(
                                f"  ProtoFire component={frame['component']} "
                                f"command={frame['command']} "
                                f"type={frame['message_type']} "
                                f"msg={frame['message_number']}",
                                flush=True,
                            )
                        except Exception as error:
                            print(f"  not plain ProtoFire: {error}", flush=True)
        except socket.timeout:
            print("  connection read timeout", flush=True)
        finally:
            client.close()
            destination.write_bytes(capture)
            print(
                f"Connection {connection_index} closed; saved {destination}",
                flush=True,
            )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nListener stopped.")
        raise SystemExit(130)

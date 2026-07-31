#!/usr/bin/env python3
"""Download one Xbox 360 file through XBDM's getfile command."""

from __future__ import annotations

import argparse
import socket
import struct
from pathlib import Path


def recv_exact(sock: socket.socket, length: int, output) -> None:
    received = 0
    while received < length:
        block = sock.recv(min(65536, length - received))
        if not block:
            raise EOFError("XBDM closed the connection during transfer")
        output.write(block)
        received += len(block)
        print(
            f"\r{received:#010x}/{length:#010x} ({received * 100.0 / length:6.2f}%)",
            end="",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("remote")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    sock = socket.create_connection((args.host, 730), timeout=10)
    sock.settimeout(15)
    reader = sock.makefile("rb")
    banner = reader.readline().rstrip(b"\r\n")
    if not banner.startswith(b"201-"):
        raise RuntimeError(f"Unexpected XBDM banner: {banner!r}")

    command = f'getfile name="{args.remote}"\r\n'
    sock.sendall(command.encode("ascii"))
    response = reader.readline().rstrip(b"\r\n")
    if not response.startswith(b"203-"):
        raise RuntimeError(response.decode("ascii", "replace"))

    length_raw = reader.read(4)
    if len(length_raw) != 4:
        raise EOFError("Missing XBDM file length")
    length = struct.unpack("<I", length_raw)[0]
    if length <= 0 or length > 1024 * 1024 * 1024:
        raise RuntimeError(f"Invalid XBDM file length: {length}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("wb") as output:
        # Continue through the buffered reader first, then the underlying socket.
        received = 0
        while received < length:
            block = reader.read(min(65536, length - received))
            if not block:
                raise EOFError("XBDM closed the connection during transfer")
            output.write(block)
            received += len(block)
            print(
                f"\r{received:#010x}/{length:#010x} "
                f"({received * 100.0 / length:6.2f}%)",
                end="",
                flush=True,
            )
    print(f"\nDownload complete: {args.output}")


if __name__ == "__main__":
    main()

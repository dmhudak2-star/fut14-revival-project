#!/usr/bin/env python3
"""Move files to and from the console over XBDM, without corrupting them.

``getfile`` frames its body with a four-byte little-endian length before the
content. Reading the body as if it began immediately shifts every byte of the
result: the copy gains a four-byte header and loses its last four bytes.

That is not a theoretical hazard. Every archive this project pulled off the
console was shifted that way, which made ``BIG4`` look as though it lived at
offset 4 behind a length word, made each entry look as though it began with
four zero bytes, and put the index records four bytes further along than they
are. Patched archives built on those readings were uploaded back and froze the
title four times before the framing was noticed; the compression and the
bytecode edit blamed for it were both fine.

``sendfile`` has no such framing: the body follows the ``204`` reply directly.

``verify`` exists because a 39 MB upload that silently lands wrong is worth
far more than the minute it costs to read back and compare.
"""

from __future__ import annotations

import argparse
import hashlib
import socket
import struct
import time
from pathlib import Path


PORT = 730


class Console:
    def __init__(self, host: str, timeout: float = 300.0) -> None:
        self.socket = socket.create_connection((host, PORT), timeout=30)
        self.socket.settimeout(timeout)
        self.reader = self.socket.makefile("rb")
        if not self.reader.readline().startswith(b"201-"):
            raise RuntimeError("Unexpected XBDM banner")

    def close(self) -> None:
        try:
            self.reader.close()
        finally:
            self.socket.close()

    def command(self, text: str) -> bytes:
        self.socket.sendall((text + "\r\n").encode("ascii"))
        return self.reader.readline().rstrip(b"\r\n")

    def download(self, remote: str) -> bytes:
        status = self.command(f'getfile name="{remote}"')
        if not status.startswith(b"203"):
            raise RuntimeError(status.decode("ascii", "replace"))
        # The length prefix is framing, not content.
        length = struct.unpack("<I", self.reader.read(4))[0]
        body = bytearray()
        while len(body) < length:
            chunk = self.reader.read(min(1 << 20, length - len(body)))
            if not chunk:
                raise RuntimeError(
                    f"{remote}: body ended at {len(body)} of {length} bytes"
                )
            body += chunk
        return bytes(body)

    def upload(self, remote: str, data: bytes, pace: int = 1 << 18) -> None:
        """Send a file, paced so a large one does not take the console down.

        Pushing a 337 MB archive as fast as the socket accepts it dropped the
        console off the network mid-transfer and needed a power cycle. Writing
        it in quarter-megabyte pieces, yielding between them, keeps the debug
        channel responsive for the whole upload.
        """
        status = self.command(f'sendfile name="{remote}" length={len(data)}')
        if not status.startswith(b"204"):
            raise RuntimeError(status.decode("ascii", "replace"))
        for start in range(0, len(data), pace):
            self.socket.sendall(data[start : start + pace])
            time.sleep(0.002)
        reply = self.reader.readline().rstrip(b"\r\n")
        if not reply.startswith(b"200"):
            raise RuntimeError(reply.decode("ascii", "replace"))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("get", "put", "verify"))
    parser.add_argument("remote")
    parser.add_argument("local", type=Path)
    args = parser.parse_args()

    console = Console(args.host)
    try:
        if args.action == "get":
            body = console.download(args.remote)
            args.local.write_bytes(body)
            print(f"{len(body)} bytes  sha256={digest(body)}")
            return 0
        if args.action == "put":
            data = args.local.read_bytes()
            console.upload(args.remote, data)
            print(f"{len(data)} bytes sent  sha256={digest(data)}")
            return 0
        local = args.local.read_bytes()
        remote = console.download(args.remote)
        print(f"local  : {len(local):>10} bytes  sha256={digest(local)}")
        print(f"console: {len(remote):>10} bytes  sha256={digest(remote)}")
        if local == remote:
            print("Verified: the console copy is identical.")
            return 0
        raise RuntimeError("The console copy differs from the local file")
    finally:
        console.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

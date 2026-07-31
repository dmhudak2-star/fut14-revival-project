#!/usr/bin/env python3
"""Inspect the current FIFA 14 EA IPv4 references without modifying memory."""

from __future__ import annotations

import argparse
import re
import socket


EA_IP = bytes.fromhex("9F99344B")
HITS = (0x3049E068, 0x3049E1A8, 0x304E50BC, 0x304F1DAC, 0x304F1EA8)


class Xbdm:
    def __init__(self, host: str) -> None:
        self.sock = socket.create_connection((host, 730), timeout=5)
        self.file = self.sock.makefile("rwb", buffering=0)
        banner = self.file.readline().decode("ascii", "replace").strip()
        if not banner.startswith("201"):
            raise RuntimeError(f"Unexpected XBDM banner: {banner}")

    def close(self) -> None:
        self.file.close()
        self.sock.close()

    def read(self, address: int, length: int) -> bytes:
        command = f"getmem addr=0x{address:08X} length=0x{length:X}\r\n"
        self.file.write(command.encode("ascii"))
        status = self.file.readline().decode("ascii", "replace").strip()
        if not status.startswith("202"):
            raise RuntimeError(f"getmem failed: {status}")
        lines: list[str] = []
        while True:
            line = self.file.readline().decode("ascii", "replace").strip()
            if line == ".":
                break
            lines.append(line)
        encoded = "".join(lines)
        if not re.fullmatch(r"[0-9A-Fa-f]+", encoded):
            raise RuntimeError(f"Invalid memory response at 0x{address:08X}")
        return bytes.fromhex(encoded)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        for hit in HITS:
            base = hit - 0x1C
            data = client.read(base, 0x40)
            offset = data.find(EA_IP)
            print(
                f"hit=0x{hit:08X} base=0x{base:08X} "
                f"offset=0x{offset:X} data={data.hex().upper()}"
            )
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

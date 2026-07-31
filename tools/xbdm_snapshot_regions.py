#!/usr/bin/env python3
"""Snapshot selected mapped Xbox 360 memory regions through XBDM."""

from __future__ import annotations

import argparse
import json
import re
import socket
from pathlib import Path


class Xbdm:
    def __init__(self, host: str):
        self.sock = socket.create_connection((host, 730), timeout=10)
        self.sock.settimeout(15)
        self.reader = self.sock.makefile("rb")
        if not self.line().startswith(b"201-"):
            raise RuntimeError("Unexpected XBDM banner")

    def line(self) -> bytes:
        line = self.reader.readline()
        if not line:
            raise EOFError("XBDM closed the connection")
        return line.rstrip(b"\r\n")

    def multiline(self, command: str) -> list[bytes]:
        self.sock.sendall(command.encode("ascii") + b"\r\n")
        status = self.line()
        if not status.startswith(b"202-"):
            raise RuntimeError(status.decode("ascii", "replace"))
        lines = []
        while True:
            line = self.line()
            if line == b".":
                return lines
            lines.append(line)

    def regions(self) -> list[tuple[int, int, int]]:
        result = []
        for raw in self.multiline("walkmem"):
            match = re.search(
                rb"base=0x([0-9a-f]+) size=0x([0-9a-f]+) "
                rb"protect=0x([0-9a-f]+)",
                raw,
                re.IGNORECASE,
            )
            if match:
                result.append(tuple(int(value, 16) for value in match.groups()))
        return result

    def read(self, address: int, length: int) -> bytes:
        lines = self.multiline(
            f"getmem addr=0x{address:08X} length=0x{length:X}"
        )
        encoded = b"".join(line.strip() for line in lines)
        if not re.fullmatch(rb"[0-9A-Fa-f]+", encoded):
            raise RuntimeError(f"Invalid response at 0x{address:08X}")
        data = bytes.fromhex(encoded.decode("ascii"))
        if len(data) != length:
            raise RuntimeError(
                f"Short read at 0x{address:08X}: {len(data):#x}/{length:#x}"
            )
        return data


def number(value: str) -> int:
    return int(value, 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("output", type=Path)
    parser.add_argument("--minimum", type=number, required=True)
    parser.add_argument("--maximum", type=number, required=True)
    parser.add_argument("--chunk-size", type=number, default=0x4000)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    client = Xbdm(args.host)
    regions = [
        (base, size, protection)
        for base, size, protection in client.regions()
        if base >= args.minimum and base + size <= args.maximum
    ]
    manifest = []
    total = sum(size for _, size, _ in regions)
    completed = 0
    for base, size, protection in regions:
        path = args.output / f"{base:08X}_{size:08X}.bin"
        with path.open("wb") as stream:
            offset = 0
            while offset < size:
                amount = min(args.chunk_size, size - offset)
                stream.write(client.read(base + offset, amount))
                offset += amount
                completed += amount
                print(
                    f"\r{completed:#010x}/{total:#010x} "
                    f"({completed * 100.0 / total:6.2f}%)",
                    end="",
                    flush=True,
                )
        manifest.append(
            {
                "base": f"0x{base:08X}",
                "size": f"0x{size:08X}",
                "protection": f"0x{protection:08X}",
                "file": path.name,
            }
        )
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\nSnapshot complete: {args.output} ({len(regions)} regions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

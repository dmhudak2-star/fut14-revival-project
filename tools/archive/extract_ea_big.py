#!/usr/bin/env python3
"""List or extract the entries of a local EA BIG4/BIGF archive."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


def read_cstring(stream) -> str:
    value = bytearray()
    while True:
        byte = stream.read(1)
        if not byte:
            raise EOFError("truncated BIG directory")
        if byte == b"\0":
            return value.decode("ascii")
        value += byte


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    entries: list[tuple[str, int, int]] = []
    with args.archive.open("rb") as stream:
        header = stream.read(16)
        if header[:4] not in (b"BIG4", b"BIGF"):
            raise SystemExit("not an EA BIG4/BIGF archive")
        count = struct.unpack(">I", header[8:12])[0]
        for _ in range(count):
            offset, size = struct.unpack(">II", stream.read(8))
            entries.append((read_cstring(stream), offset, size))

        for index, (name, offset, size) in enumerate(entries):
            print(f"{index:5d} 0x{offset:08X} 0x{size:08X} {name}")
            if args.output is None or not size:
                continue
            destination = args.output / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            stream.seek(offset)
            destination.write_bytes(stream.read(size))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

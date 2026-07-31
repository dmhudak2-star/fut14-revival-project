#!/usr/bin/env python3
"""Extract every .nav payload from an EA BIG4 archive."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


def cstring(stream) -> str:
    data = bytearray()
    while True:
        byte = stream.read(1)
        if not byte:
            raise EOFError("Truncated BIG directory")
        if byte == b"\0":
            return data.decode("ascii")
        data += byte


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    entries: list[tuple[str, int, int]] = []
    with args.archive.open("rb") as stream:
        header = stream.read(16)
        if header[:4] != b"BIG4":
            raise SystemExit("Not a BIG4 archive")
        count = struct.unpack(">I", header[8:12])[0]
        for _ in range(count):
            offset, size = struct.unpack(">II", stream.read(8))
            name = cstring(stream)
            if name.lower().endswith(".nav"):
                entries.append((name, offset, size))

        for name, offset, size in entries:
            stream.seek(offset)
            destination = args.output / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.with_suffix(destination.suffix + ".raw").write_bytes(
                stream.read(size)
            )
    print(f"Extracted {len(entries)} NAV resources")


if __name__ == "__main__":
    main()

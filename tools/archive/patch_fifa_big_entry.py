#!/usr/bin/env python3
"""Append a replacement BIG4 payload and retarget its BIG/BH index entries."""

from __future__ import annotations

import argparse
import shutil
import struct
from pathlib import Path


def read_cstring(stream) -> str:
    value = bytearray()
    while True:
        byte = stream.read(1)
        if not byte:
            raise EOFError("Unexpected end of BIG directory")
        if byte == b"\0":
            return value.decode("ascii")
        value += byte


def find_entry(big: Path, wanted: str) -> tuple[int, int, int, int]:
    with big.open("rb") as stream:
        header = stream.read(16)
        if header[:4] != b"BIG4":
            raise RuntimeError("Not a BIG4 archive")
        count = struct.unpack(">I", header[8:12])[0]
        for index in range(count):
            table_offset = stream.tell()
            data_offset, size = struct.unpack(">II", stream.read(8))
            name = read_cstring(stream)
            if name.lower() == wanted.lower():
                return index, table_offset, data_offset, size
    raise RuntimeError(f"Entry not found: {wanted}")


def align(value: int, boundary: int) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_big", type=Path)
    parser.add_argument("source_bh", type=Path)
    parser.add_argument("payload", type=Path)
    parser.add_argument("output_big", type=Path)
    parser.add_argument("output_bh", type=Path)
    parser.add_argument(
        "--entry", default="data/ui/nav/mainfeflow.nav"
    )
    args = parser.parse_args()

    # FAT volumes may expose flags that cannot be reproduced in the workspace.
    shutil.copyfile(args.source_big, args.output_big)
    shutil.copyfile(args.source_bh, args.output_bh)
    payload = args.payload.read_bytes()

    index, table_offset, old_offset, old_size = find_entry(
        args.output_big, args.entry
    )
    original_length = args.output_big.stat().st_size
    new_offset = align(original_length, 0x80)

    with args.output_big.open("r+b") as stream:
        stream.seek(0, 2)
        stream.write(b"\0" * (new_offset - original_length))
        stream.write(payload)
        new_length = stream.tell()

        stream.seek(4)
        stream.write(struct.pack("<I", new_length))
        stream.seek(table_offset)
        stream.write(struct.pack(">II", new_offset, len(payload)))

    bh_record = 16 + index * 20
    with args.output_bh.open("r+b") as stream:
        stream.seek(bh_record)
        bh_old_offset, bh_old_size = struct.unpack(">II", stream.read(8))
        if (bh_old_offset, bh_old_size) != (old_offset, old_size):
            raise RuntimeError(
                "BH record does not match BIG entry: "
                f"{bh_old_offset:#x}/{bh_old_size:#x} != "
                f"{old_offset:#x}/{old_size:#x}"
            )
        stream.seek(bh_record)
        stream.write(struct.pack(">II", new_offset, len(payload)))

    print(f"Entry index: {index}")
    print(f"BIG table field: {table_offset:#x}")
    print(f"BH record: {bh_record:#x}")
    print(f"Old payload: {old_offset:#x}, {old_size:#x} bytes")
    print(f"New payload: {new_offset:#x}, {len(payload):#x} bytes")
    print(f"New archive size: {new_length:#x}")


if __name__ == "__main__":
    main()

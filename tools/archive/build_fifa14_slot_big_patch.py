#!/usr/bin/env python3
"""Build an in-place FIFA BIG/BH patch that fits before the next payload."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import struct
from pathlib import Path


ENTRY_INDEX = 16503
ENTRY_TABLE_OFFSET = 0x000D18C2
BH_RECORD_OFFSET = 0x0005095C
DATA_OFFSET = 0x13D40500
NEXT_DATA_OFFSET = 0x13D40F80
ORIGINAL_SIZE = 0xA60
ARCHIVE_SIZE = 0x1412B9F2
ORIGINAL_SHA256 = "dd335dd34d37a4b200316e58f937e3b5ed65eebe186c2294d80abf8bb23730e3"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("failed_big", type=Path)
    parser.add_argument("original_bh", type=Path)
    parser.add_argument("replacement", type=Path)
    parser.add_argument("output_big", type=Path)
    parser.add_argument("output_bh", type=Path)
    args = parser.parse_args()

    payload = args.replacement.read_bytes()
    capacity = NEXT_DATA_OFFSET - DATA_OFFSET
    if len(payload) > capacity:
        raise SystemExit(f"Payload {len(payload):#x} exceeds slot {capacity:#x}")

    shutil.copyfile(args.failed_big, args.output_big)
    with args.output_big.open("r+b") as stream:
        stream.seek(4)
        stream.write(struct.pack("<I", ARCHIVE_SIZE))
        stream.seek(ENTRY_TABLE_OFFSET)
        stream.write(struct.pack(">II", DATA_OFFSET, ORIGINAL_SIZE))
        stream.truncate(ARCHIVE_SIZE)
    if sha256(args.output_big) != ORIGINAL_SHA256:
        raise SystemExit("Could not reconstruct the original BIG")

    with args.output_big.open("r+b") as stream:
        stream.seek(DATA_OFFSET)
        stream.write(payload)
        stream.write(b"\0" * (capacity - len(payload)))
        stream.seek(ENTRY_TABLE_OFFSET)
        stream.write(struct.pack(">II", DATA_OFFSET, len(payload)))

    shutil.copyfile(args.original_bh, args.output_bh)
    with args.output_bh.open("r+b") as stream:
        stream.seek(BH_RECORD_OFFSET)
        old_offset, old_size = struct.unpack(">II", stream.read(8))
        if (old_offset, old_size) != (DATA_OFFSET, ORIGINAL_SIZE):
            raise SystemExit("Unexpected original BH record")
        stream.seek(BH_RECORD_OFFSET)
        stream.write(struct.pack(">II", DATA_OFFSET, len(payload)))

    print(f"Entry index: {ENTRY_INDEX}")
    print(f"Payload: {DATA_OFFSET:#x}/{len(payload):#x}")
    print(f"Slot capacity: {capacity:#x}")
    print(f"BIG SHA-256: {sha256(args.output_big)}")
    print(f"BH SHA-256: {sha256(args.output_bh)}")


if __name__ == "__main__":
    main()

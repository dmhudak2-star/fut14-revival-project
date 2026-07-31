#!/usr/bin/env python3
"""Restore the original BIG layout, then replace one same-sized payload."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import struct
from pathlib import Path


ENTRY_TABLE_OFFSET = 0x000D18C2
ORIGINAL_DATA_OFFSET = 0x13D40500
ORIGINAL_ENTRY_SIZE = 0x00000A60
ORIGINAL_ARCHIVE_SIZE = 0x1412B9F2
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
    parser.add_argument("replacement", type=Path)
    parser.add_argument("output_big", type=Path)
    args = parser.parse_args()

    replacement = args.replacement.read_bytes()
    if len(replacement) != ORIGINAL_ENTRY_SIZE:
        raise SystemExit(
            f"Replacement must be {ORIGINAL_ENTRY_SIZE:#x} bytes, "
            f"got {len(replacement):#x}"
        )

    shutil.copyfile(args.failed_big, args.output_big)
    with args.output_big.open("r+b") as stream:
        stream.seek(4)
        stream.write(struct.pack("<I", ORIGINAL_ARCHIVE_SIZE))
        stream.seek(ENTRY_TABLE_OFFSET)
        stream.write(struct.pack(">II", ORIGINAL_DATA_OFFSET, ORIGINAL_ENTRY_SIZE))
        stream.truncate(ORIGINAL_ARCHIVE_SIZE)

    restored_hash = sha256(args.output_big)
    if restored_hash != ORIGINAL_SHA256:
        raise SystemExit(
            "Failed to reconstruct original BIG: "
            f"{restored_hash} != {ORIGINAL_SHA256}"
        )

    with args.output_big.open("r+b") as stream:
        stream.seek(ORIGINAL_DATA_OFFSET)
        stream.write(replacement)

    print(f"Original reconstruction verified: {restored_hash}")
    print(f"Patched {len(replacement):#x} bytes at {ORIGINAL_DATA_OFFSET:#x}")
    print(f"Patched BIG SHA-256: {sha256(args.output_big)}")


if __name__ == "__main__":
    main()

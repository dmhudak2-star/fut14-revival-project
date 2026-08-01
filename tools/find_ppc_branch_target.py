#!/usr/bin/env python3
"""Find direct PowerPC b/bl instructions targeting one virtual address."""

from __future__ import annotations

import argparse
from pathlib import Path


def number(value: str) -> int:
    return int(value, 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("base", type=number)
    parser.add_argument("target", type=number)
    args = parser.parse_args()

    data = args.file.read_bytes()
    for offset in range(0, len(data) - 3, 4):
        word = int.from_bytes(data[offset : offset + 4], "big")
        if word >> 26 != 18:
            continue
        displacement = word & 0x03FFFFFC
        if displacement & 0x02000000:
            displacement -= 0x04000000
        address = args.base + offset
        destination = (
            displacement & 0xFFFFFFFF
            if word & 2
            else (address + displacement) & 0xFFFFFFFF
        )
        if destination == args.target:
            mnemonic = "bl" if word & 1 else "b"
            print(f"0x{address:08X}: {word:08X}  {mnemonic} 0x{destination:08X}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

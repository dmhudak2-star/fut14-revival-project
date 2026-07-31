#!/usr/bin/env python3
"""Disassemble a virtual-address range from a raw Xbox 360 section dump."""

from __future__ import annotations

import argparse
from pathlib import Path

from capstone import Cs, CS_ARCH_PPC, CS_MODE_32, CS_MODE_BIG_ENDIAN


def number(text: str) -> int:
    return int(text, 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("base", type=number)
    parser.add_argument("start", type=number)
    parser.add_argument("length", type=number)
    args = parser.parse_args()

    offset = args.start - args.base
    if offset < 0:
        raise SystemExit("start precedes section base")
    with args.file.open("rb") as stream:
        stream.seek(offset)
        code = stream.read(args.length)
    if len(code) != args.length:
        raise SystemExit(f"short read: {len(code):#x}/{args.length:#x}")

    decoder = Cs(CS_ARCH_PPC, CS_MODE_32 | CS_MODE_BIG_ENDIAN)
    for instruction in decoder.disasm(code, args.start):
        print(
            f"0x{instruction.address:08X}: "
            f"{instruction.bytes.hex().upper():8s}  "
            f"{instruction.mnemonic:10s} {instruction.op_str}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

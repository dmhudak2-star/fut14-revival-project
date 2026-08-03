#!/usr/bin/env python3
"""Find simple PowerPC lis/addi or lis/ori references to a virtual address."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


def number(text: str) -> int:
    return int(text, 0)


def signed16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("base", type=number)
    parser.add_argument("target", type=number)
    parser.add_argument("--window", type=int, default=12)
    args = parser.parse_args()

    data = args.file.read_bytes()
    words = struct.unpack(f">{len(data) // 4}I", data[: len(data) & ~3])
    matches: set[tuple[int, int, str]] = set()

    for index, first in enumerate(words):
        opcode = first >> 26
        rt = (first >> 21) & 31
        ra = (first >> 16) & 31
        if opcode != 15 or ra != 0:  # lis == addis rT,r0,imm16
            continue
        high = (first & 0xFFFF) << 16
        stop = min(len(words), index + 1 + args.window)
        for second_index in range(index + 1, stop):
            second = words[second_index]
            second_opcode = second >> 26
            second_ra = (second >> 16) & 31
            if second_ra != rt:
                continue
            low = second & 0xFFFF
            if second_opcode == 14:  # addi
                value = (high + signed16(low)) & 0xFFFFFFFF
                form = "addi"
            elif second_opcode == 24:  # ori
                value = high | low
                form = "ori"
            else:
                continue
            if value == args.target:
                matches.add(
                    (
                        args.base + index * 4,
                        args.base + second_index * 4,
                        form,
                    )
                )

    for first, second, form in sorted(matches):
        print(f"lis=0x{first:08X} {form}=0x{second:08X}")
    print(f"matches={len(matches)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

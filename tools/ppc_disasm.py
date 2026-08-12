#!/usr/bin/env python3
"""Disassemble a dumped Xbox 360 memory range.

PowerPC, big-endian, 32-bit -- the 360's Xenon. Every measurement in this
repository that needed instructions has been read by hand out of a hex dump so
far, which is fine for a six-byte branch patch and hopeless for following a
call chain.

    tools/ppc_disasm.py work/submit.bin 0x83593A00
    tools/ppc_disasm.py work/submit.bin 0x83593A00 --from 0x83593B28 --count 60

Branch targets are annotated with the absolute address they reach, because that
is the whole point of reading these: what does this call, and where does that
land.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from capstone import CS_ARCH_PPC, CS_MODE_32, CS_MODE_BIG_ENDIAN, Cs


def disassemble(data: bytes, base: int, start: int | None = None,
                count: int = 0) -> list[str]:
    engine = Cs(CS_ARCH_PPC, CS_MODE_32 | CS_MODE_BIG_ENDIAN)
    engine.detail = False
    offset = 0 if start is None else max(0, start - base)
    lines: list[str] = []
    for instruction in engine.disasm(data[offset:], base + offset):
        lines.append(
            f"0x{instruction.address:08X}  {instruction.mnemonic:<10}"
            f"{instruction.op_str}"
        )
        if count and len(lines) >= count:
            break
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dump", type=Path)
    parser.add_argument("base", type=lambda value: int(value, 0))
    parser.add_argument("--from", dest="start", type=lambda value: int(value, 0))
    parser.add_argument("--count", type=int, default=0)
    args = parser.parse_args()

    for line in disassemble(
        args.dump.read_bytes(), args.base, args.start, args.count
    ):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

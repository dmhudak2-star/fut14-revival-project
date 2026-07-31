#!/usr/bin/env python3
"""Find direct PowerPC branch/call sites from FIFA's text dump to XAM exports."""

from __future__ import annotations

import argparse
import struct


TARGETS = {
    0x81741C50: "NetDll_select",
    0x81741C78: "NetDll_WSAGetOverlappedResult",
    0x81741CA0: "NetDll_WSACancelOverlappedIO",
    0x81741CB8: "NetDll_recv",
    0x81741CD8: "NetDll_WSARecv",
    0x81741D30: "NetDll_recvfrom",
    0x81741D58: "NetDll_WSARecvFrom",
    0x81741DB0: "NetDll_send",
    0x81741DD0: "NetDll_WSASend",
    0x81741E28: "NetDll_sendto",
    0x81741E50: "NetDll_WSASendTo",
}


def sign_extend_26(value: int) -> int:
    return value - (1 << 26) if value & (1 << 25) else value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("text_dump")
    parser.add_argument("--base", type=lambda value: int(value, 0), default=0x823D0000)
    args = parser.parse_args()

    data = open(args.text_dump, "rb").read()
    hits = 0
    for offset in range(0, len(data) - 3, 4):
        instruction = struct.unpack_from(">I", data, offset)[0]
        if instruction >> 26 != 18 or not (instruction & 1):
            continue
        displacement = sign_extend_26(instruction & 0x03FFFFFC)
        address = args.base + offset
        target = displacement if instruction & 2 else (address + displacement) & 0xFFFFFFFF
        name = TARGETS.get(target)
        if name:
            print(
                f"0x{address:08X}: 0x{instruction:08X} -> "
                f"0x{target:08X} {name}"
            )
            hits += 1
    print(f"{hits} direct call site(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

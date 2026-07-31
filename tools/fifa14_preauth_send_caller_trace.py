#!/usr/bin/env python3
"""Record the true caller of DirtySock send for Util.PreAuth."""

from __future__ import annotations

import argparse

from fifa14_plain_recv_hook import cmpwi, conditional_branch
from fifa14_plain_send_hook import (
    Xbdm,
    addi,
    addis,
    branch,
    insn,
    stw,
    verify_module,
)


SITE = 0x82D69FF8
ORIGINAL = bytes.fromhex("7D8802A6")  # mflr r12
STUB = 0x83C8DA00
JOURNAL = 0x83C8DA80
JOURNAL_SIZE = 0x20


def lhz(rt: int, ra: int, displacement: int) -> int:
    return 0xA0000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def build_stub() -> bytes:
    words = [
        cmpwi(5, 6),
        0,                            # blt fallback
        lhz(11, 4, 0x02),
        cmpwi(11, 9),
        0,                            # bne fallback
        lhz(11, 4, 0x04),
        cmpwi(11, 7),
        0,                            # bne fallback
        0x7C0802A6,                  # mflr r0
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x2580),      # r12 = JOURNAL
        stw(0, 12, 0x00),
        stw(3, 12, 0x04),           # DirtySock owner
        stw(4, 12, 0x08),           # send buffer
        stw(5, 12, 0x0C),           # send length
    ]
    fallback = len(words)
    words.extend(
        [
            int.from_bytes(ORIGINAL, "big"),
            0,
        ]
    )

    def address(index: int) -> int:
        return STUB + index * 4

    words[1] = conditional_branch(
        address(1), address(fallback), 12, 0
    )                                # blt
    for index in (4, 7):
        words[index] = conditional_branch(
            address(index), address(fallback), 4, 2
        )                            # bne
    words[-1] = branch(address(len(words) - 1), SITE + 4, False)
    return b"".join(insn(word) for word in words)


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    lr = int.from_bytes(raw[0:4], "big")
    print(f"return_address = 0x{lr:08X}")
    print(f"callsite       = 0x{(lr - 4) & 0xFFFFFFFF:08X}")
    print(f"owner          = 0x{int.from_bytes(raw[4:8], 'big'):08X}")
    print(f"buffer         = 0x{int.from_bytes(raw[8:12], 'big'):08X}")
    print(f"length         = {int.from_bytes(raw[12:16], 'big')}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    args = parser.parse_args()

    stub = build_stub()
    patch = insn(branch(SITE, STUB, False))
    client = Xbdm(args.host)
    try:
        verify_module(client)
        current = client.read(SITE, 4)
        print(f"PreAuth send caller trace site: {current.hex().upper()}")
        if args.action in ("status", "read"):
            describe(client)
            return 0
        if args.action == "apply":
            if current not in (ORIGINAL, patch):
                raise RuntimeError("Unexpected DirtySock send entry")
            cave = client.read(STUB, len(stub))
            if cave not in (bytes(len(stub)), stub):
                raise RuntimeError("PreAuth send trace code cave is not free")
            client.write(JOURNAL, bytes(JOURNAL_SIZE))
            client.write(STUB, stub)
            client.write(SITE, patch)
            if client.read(SITE, 4) != patch:
                raise RuntimeError("PreAuth send trace verification failed")
            print("Verified: PreAuth send caller trace armed.")
            return 0
        if current == patch:
            client.write(SITE, ORIGINAL)
        elif current != ORIGINAL:
            raise RuntimeError("Unexpected PreAuth send trace site")
        print("Verified: PreAuth send caller trace restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

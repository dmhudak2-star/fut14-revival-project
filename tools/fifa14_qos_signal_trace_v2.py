#!/usr/bin/env python3
"""Trace FIFA 14's QoS-complete signal without sharing another probe's cave."""

from __future__ import annotations

import argparse

from fifa14_plain_recv_hook import cmpwi, conditional_branch
from fifa14_plain_send_hook import (
    Xbdm,
    addi,
    addis,
    branch,
    cmpw,
    insn,
    lwz,
    stw,
    verify_module,
)


SITE = 0x82F02F60
ORIGINAL = bytes.fromhex("3D6082EB")  # lis r11,-0x7D15
STUB = 0x83C8C600
JOURNAL = 0x83C8C680
JOURNAL_SIZE = 0x40


def build_stub() -> bytes:
    words = [
        addi(9, 3, 0x0A8C),         # signal = callback context + 0xA8C
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x3980),      # JOURNAL
        lwz(11, 12, 0x00),
        addi(11, 11, 1),
        stw(11, 12, 0x00),          # invocation count
        stw(3, 12, 0x04),           # callback context
        stw(9, 12, 0x08),           # signal object
        lwz(10, 9, 0x04),           # subscriber begin
        lwz(11, 9, 0x08),           # subscriber end
        stw(10, 12, 0x0C),
        stw(11, 12, 0x10),
        lwz(9, 9, 0x40),            # dispatch depth
        stw(9, 12, 0x14),
        cmpw(10, 11),
        0,                           # beq finish
        lwz(10, 10, 0x00),          # first callback object
        stw(10, 12, 0x18),
        cmpwi(10, 0),
        0,                           # beq finish
    ]
    for index, offset in enumerate((0x00, 0x04, 0x08, 0x0C)):
        words.append(lwz(11, 10, offset))
        words.append(stw(11, 12, 0x1C + index * 4))
    finish = len(words)
    words.extend((int.from_bytes(ORIGINAL, "big"), 0))

    def address(index: int) -> int:
        return STUB + index * 4

    for index in (15, 19):
        words[index] = conditional_branch(
            address(index), address(finish), 12, 2
        )
    words[-1] = branch(
        address(len(words) - 1), SITE + 4, False
    )
    return b"".join(insn(word) for word in words)


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    begin = u32(raw, 0x0C)
    end = u32(raw, 0x10)
    print(f"invocation_count = {u32(raw, 0x00)}")
    print(f"context          = 0x{u32(raw, 0x04):08X}")
    print(f"signal           = 0x{u32(raw, 0x08):08X}")
    print(f"begin            = 0x{begin:08X}")
    print(f"end              = 0x{end:08X}")
    print(
        f"subscriber_count = {(end - begin) // 4 if end >= begin else -1}"
    )
    print(f"dispatch_depth   = {u32(raw, 0x14)}")
    print(f"listener         = 0x{u32(raw, 0x18):08X}")
    for index in range(4):
        print(
            f"listener_word{index}   = "
            f"0x{u32(raw, 0x1C + index * 4):08X}"
        )


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
        state = (
            "original"
            if current == ORIGINAL
            else "patched"
            if current == patch
            else f"unexpected:{current.hex().upper()}"
        )
        print(f"QoS signal trace v2: {state}")
        if args.action in ("status", "read"):
            describe(client)
            return 0
        if args.action == "apply":
            if state not in ("original", "patched"):
                raise RuntimeError("Unexpected QoS signal entry")
            cave = client.read(STUB, len(stub))
            if cave not in (bytes(len(stub)), stub):
                raise RuntimeError("QoS signal v2 code cave is not free")
            client.write(JOURNAL, bytes(JOURNAL_SIZE))
            client.write(STUB, stub)
            client.write(SITE, patch)
            if client.read(SITE, 4) != patch:
                raise RuntimeError("QoS signal v2 verification failed")
            print("Verified: independent QoS-complete signal trace armed.")
            return 0
        if state == "patched":
            client.write(SITE, ORIGINAL)
        elif state != "original":
            raise RuntimeError("Unexpected QoS signal entry")
        print("Verified: original QoS signal entry restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

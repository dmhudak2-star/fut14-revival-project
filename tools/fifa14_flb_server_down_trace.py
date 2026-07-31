#!/usr/bin/env python3
"""Trace entry into the FIFA Live Browser server-down message block."""

from __future__ import annotations

import argparse

from fifa14_plain_send_hook import (
    Xbdm,
    addi,
    addis,
    branch,
    insn,
    lwz,
    stw,
    verify_module,
)


SITE = 0x828595A0
ORIGINAL = bytes.fromhex("3D608207")  # lis r11, 0x8207
STUB = 0x83C8EA00
JOURNAL = 0x83C8EB00
JOURNAL_SIZE = 0x80

CAPTURED_REGISTERS = (
    1,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    12,
    24,
    25,
    26,
    27,
    28,
    29,
    30,
    31,
)


def build_stub() -> bytes:
    words = [
        addis(11, 0, 0x83C9),
        addi(11, 11, -0x1500),  # JOURNAL
        lwz(12, 11, 0x00),
        addi(12, 12, 1),
        stw(12, 11, 0x00),
    ]
    for index, register in enumerate(CAPTURED_REGISTERS):
        words.append(stw(register, 11, 0x04 + index * 4))
    # Preserve r12 while recording the caller's link register.
    words.extend(
        [
            0x7D8802A6,  # mflr r12
            stw(12, 11, 0x4C),
            lwz(12, 11, 0x28),
            int.from_bytes(ORIGINAL, "big"),
            0,
        ]
    )
    words[-1] = branch(STUB + (len(words) - 1) * 4, SITE + 4, False)
    return b"".join(insn(word) for word in words)


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    print(f"invocation_count = {u32(raw, 0x00)}")
    for index, register in enumerate(CAPTURED_REGISTERS):
        print(f"r{register:<2}              = 0x{u32(raw, 0x04 + index * 4):08X}")
    print(f"lr               = 0x{u32(raw, 0x4C):08X}")


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
        print(f"FLB_SERVER_DOWN trace: {state}")
        if args.action in ("status", "read"):
            describe(client)
            return 0
        if args.action == "apply":
            if state not in ("original", "patched"):
                raise RuntimeError("Unexpected FLB_SERVER_DOWN block entry")
            cave = client.read(STUB, len(stub))
            if cave not in (bytes(len(stub)), stub):
                raise RuntimeError("FLB_SERVER_DOWN trace cave is not free")
            client.write(JOURNAL, bytes(JOURNAL_SIZE))
            client.write(STUB, stub)
            client.write(SITE, patch)
            if client.read(SITE, 4) != patch:
                raise RuntimeError("FLB_SERVER_DOWN trace verification failed")
            print("Verified: FLB_SERVER_DOWN decision trace armed.")
            return 0
        if state == "patched":
            client.write(SITE, ORIGINAL)
        elif state != "original":
            raise RuntimeError("Unexpected FLB_SERVER_DOWN block entry")
        print("Verified: original FLB_SERVER_DOWN block restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

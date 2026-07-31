#!/usr/bin/env python3
"""Journal FIFA 14's Blaze connection-result handler without breakpoints."""

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
from fifa14_qos_signal_trace import build_stub as build_old_signal_stub


SITE = 0x82F02D90
ORIGINAL = bytes.fromhex("7D8802A6")  # mflr r12
STUB = 0x83C8E100
JOURNAL = 0x83C8E180
JOURNAL_SIZE = 0x40


def build_stub() -> bytes:
    words = [
        int.from_bytes(ORIGINAL, "big"),
        addis(11, 0, 0x83C9),
        addi(11, 11, -0x1E80),      # JOURNAL
        stw(3, 11, 0x00),           # parent connection manager
        stw(4, 11, 0x04),           # Blaze/connection result code
        stw(12, 11, 0x08),          # caller LR
        lwz(10, 11, 0x0C),
        addi(10, 10, 1),
        stw(10, 11, 0x0C),          # invocation count
        addi(9, 3, 0x0A8C),         # signal A8C
        lwz(10, 9, 0x04),
        stw(10, 11, 0x10),          # A8C subscriber begin
        lwz(10, 9, 0x08),
        stw(10, 11, 0x14),          # A8C subscriber end
        lwz(10, 9, 0x40),
        stw(10, 11, 0x2C),          # A8C dispatch depth
        addi(9, 3, 0x0B0C),         # signal B0C
        lwz(10, 9, 0x04),
        stw(10, 11, 0x18),          # B0C subscriber begin
        lwz(10, 9, 0x08),
        stw(10, 11, 0x1C),          # B0C subscriber end
        lwz(10, 9, 0x40),
        stw(10, 11, 0x30),          # B0C dispatch depth
        lwz(10, 3, 0x80),
        stw(10, 11, 0x20),
        lwz(10, 3, 0x2AC),
        stw(10, 11, 0x24),
        lwz(10, 3, 0x33C),
        stw(10, 11, 0x28),
        0,
    ]
    words[-1] = branch(STUB + (len(words) - 1) * 4, SITE + 4, False)
    return b"".join(insn(word) for word in words)


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    manager = u32(raw, 0x00)
    result = u32(raw, 0x04)
    lr = u32(raw, 0x08)
    count = u32(raw, 0x0C)
    a_begin = u32(raw, 0x10)
    a_end = u32(raw, 0x14)
    b_begin = u32(raw, 0x18)
    b_end = u32(raw, 0x1C)
    print(f"invocation_count = {count}")
    print(f"manager          = 0x{manager:08X}")
    print(f"result_r4        = 0x{result:08X} ({result})")
    print(f"caller_lr        = 0x{lr:08X}")
    print(f"caller_callsite  = 0x{(lr - 4) & 0xFFFFFFFF:08X}" if lr else "caller_callsite  = 0")
    print(
        f"signal_A8C       = begin=0x{a_begin:08X} end=0x{a_end:08X} "
        f"count={(a_end - a_begin) // 4 if a_end >= a_begin else -1} "
        f"depth={u32(raw, 0x2C)}"
    )
    print(
        f"signal_B0C       = begin=0x{b_begin:08X} end=0x{b_end:08X} "
        f"count={(b_end - b_begin) // 4 if b_end >= b_begin else -1} "
        f"depth={u32(raw, 0x30)}"
    )
    print(f"manager_0080     = 0x{u32(raw, 0x20):08X}")
    print(f"manager_02AC     = 0x{u32(raw, 0x24):08X}")
    print(f"manager_033C     = 0x{u32(raw, 0x28):08X}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    args = parser.parse_args()

    stub = build_stub()
    old_stub = build_old_signal_stub()
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
        print(f"Connection-result trace site: {state}")
        if args.action in ("status", "read"):
            describe(client)
            return 0
        if args.action == "apply":
            if state not in ("original", "patched"):
                raise RuntimeError("Unexpected connection-result handler entry")
            cave = client.read(STUB, max(len(stub), len(old_stub)))
            if (
                cave[: len(stub)] not in (bytes(len(stub)), stub)
                and not cave.startswith(old_stub)
            ):
                raise RuntimeError("Connection-result trace code cave is not free")
            client.write(JOURNAL, bytes(JOURNAL_SIZE))
            client.write(STUB, stub)
            client.write(SITE, patch)
            if client.read(SITE, 4) != patch:
                raise RuntimeError("Connection-result trace verification failed")
            print("Verified: connection-result journal armed.")
            return 0
        if state == "patched":
            client.write(SITE, ORIGINAL)
        elif state != "original":
            raise RuntimeError("Unexpected connection-result handler entry")
        print("Verified: original connection-result handler restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

#!/usr/bin/env python3
"""Trace the B0C subscriber that fans connection state into game systems."""

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


SITE = 0x82EB50D0
ORIGINAL = bytes.fromhex("7D8802A6")  # mflr r12
STUB = 0x83C8C900
JOURNAL = 0x83C8CA00
JOURNAL_SIZE = 0x40


def build_stub() -> bytes:
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x3600),      # JOURNAL
        lwz(11, 12, 0x00),
        addi(11, 11, 1),
        stw(11, 12, 0x00),
        stw(3, 12, 0x04),
    ]
    for index, offset in enumerate((0x6F8, 0x778, 0x7F8)):
        words.extend(
            [
                addi(9, 3, offset),
                lwz(10, 9, 0x04),
                stw(10, 12, 0x08 + index * 8),
                lwz(10, 9, 0x08),
                stw(10, 12, 0x0C + index * 8),
            ]
        )
    words.extend(
        [
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
    print(f"listener         = 0x{u32(raw, 0x04):08X}")
    for index, signal_offset in enumerate((0x6F8, 0x778, 0x7F8)):
        begin = u32(raw, 0x08 + index * 8)
        end = u32(raw, 0x0C + index * 8)
        print(
            f"signal_{signal_offset:03X}       = "
            f"begin=0x{begin:08X} end=0x{end:08X} "
            f"count={(end - begin) // 4 if end >= begin else -1}"
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
        print(f"B0C listener trace: {state}")
        if args.action in ("status", "read"):
            describe(client)
            return 0
        if args.action == "apply":
            if state not in ("original", "patched"):
                raise RuntimeError("Unexpected B0C listener entry")
            cave = client.read(STUB, len(stub))
            if cave not in (bytes(len(stub)), stub):
                raise RuntimeError("B0C listener trace cave is not free")
            client.write(JOURNAL, bytes(JOURNAL_SIZE))
            client.write(STUB, stub)
            client.write(SITE, patch)
            if client.read(SITE, 4) != patch:
                raise RuntimeError("B0C listener trace verification failed")
            print("Verified: B0C listener fan-out trace armed.")
            return 0
        if state == "patched":
            client.write(SITE, ORIGINAL)
        elif state != "original":
            raise RuntimeError("Unexpected B0C listener entry")
        print("Verified: original B0C listener entry restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

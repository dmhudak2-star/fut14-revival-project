#!/usr/bin/env python3
"""Snapshot QoS-complete signal subscribers before they are dispatched."""

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
STUB = 0x83C8E100
JOURNAL = 0x83C8E180
JOURNAL_SIZE = 0x30


def build_stub() -> bytes:
    words = [
        addi(9, 3, 0x0A8C),         # signal = callback context + 0xA8C
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x1E80),      # JOURNAL
        stw(3, 12, 0x00),           # callback context
        stw(9, 12, 0x04),           # signal object
        lwz(10, 9, 0x04),           # subscriber begin
        lwz(11, 9, 0x08),           # subscriber end
        stw(10, 12, 0x08),
        stw(11, 12, 0x0C),
        lwz(9, 9, 0x40),            # dispatch depth
        stw(9, 12, 0x10),
        cmpw(10, 11),
        0,                           # beq finish
        lwz(10, 10, 0x00),          # first callback object
        stw(10, 12, 0x14),
        cmpwi(10, 0),
        0,                           # beq finish
        lwz(11, 10, 0x00),
        stw(11, 12, 0x18),
        lwz(11, 10, 0x04),
        stw(11, 12, 0x1C),
        lwz(11, 10, 0x08),
        stw(11, 12, 0x20),
        lwz(11, 10, 0x0C),
        stw(11, 12, 0x24),
    ]
    finish = len(words)
    words.extend(
        [
            int.from_bytes(ORIGINAL, "big"),
            0,
        ]
    )

    def address(index: int) -> int:
        return STUB + index * 4

    for index in (12, 16):
        words[index] = conditional_branch(
            address(index), address(finish), 12, 2
        )                           # beq
    words[-1] = branch(address(len(words) - 1), SITE + 4, False)
    return b"".join(insn(word) for word in words)


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    names = (
        "context",
        "signal",
        "begin",
        "end",
        "dispatch_depth",
        "listener",
        "listener_word0",
        "listener_word1",
        "listener_word2",
        "listener_word3",
    )
    for index, name in enumerate(names):
        value = int.from_bytes(raw[index * 4 : index * 4 + 4], "big")
        print(f"{name:16} = 0x{value:08X}")
    begin = int.from_bytes(raw[8:12], "big")
    end = int.from_bytes(raw[12:16], "big")
    print(f"subscriber_count = {(end - begin) // 4 if end >= begin else -1}")


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
        print(f"QoS signal trace site: {state}")
        if args.action in ("status", "read"):
            describe(client)
            return 0
        if args.action == "apply":
            if state not in ("original", "patched"):
                raise RuntimeError("Unexpected QoS signal entry")
            cave = client.read(STUB, len(stub))
            if cave not in (bytes(len(stub)), stub):
                raise RuntimeError("QoS signal trace code cave is not free")
            client.write(JOURNAL, bytes(JOURNAL_SIZE))
            client.write(STUB, stub)
            client.write(SITE, patch)
            if client.read(SITE, 4) != patch:
                raise RuntimeError("QoS signal trace verification failed")
            print("Verified: QoS-complete subscriber snapshot armed.")
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

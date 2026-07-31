#!/usr/bin/env python3
"""Record localization keys resolved by FIFA's presentation text service."""

from __future__ import annotations

import argparse

from fifa14_plain_send_hook import (
    Xbdm,
    add,
    addi,
    addis,
    andi_dot,
    branch,
    insn,
    lwz,
    rlwinm,
    stw,
    verify_module,
    write_chunks,
)


SITE = 0x82D657F8
ORIGINAL = bytes.fromhex("7D8802A6")  # mflr r12
STUB = 0x83C8FB00
JOURNAL = 0x83C8FE00
SLOT_COUNT = 32
JOURNAL_SIZE = 0x100


def build_stub() -> bytes:
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x0200),      # JOURNAL
        lwz(11, 12, 0x00),
        addi(11, 11, 1),
        stw(11, 12, 0x00),
        andi_dot(11, 11, SLOT_COUNT - 1),
        rlwinm(11, 11, 2, 0, 29),  # (sequence & 31) * 4
        add(11, 12, 11),
        stw(5, 11, 0x04),           # static localization-key pointer
        int.from_bytes(ORIGINAL, "big"),
        0,
    ]
    words[-1] = branch(STUB + (len(words) - 1) * 4, SITE + 4, False)
    return b"".join(insn(word) for word in words)


def read_c_string(client: Xbdm, address: int, limit: int = 96) -> str:
    # Runtime localization keys are frequently allocated in the title heap
    # (0xB...); keep the read bounded and let XBDM reject unmapped pointers.
    if not 0x80000000 <= address < 0xE0000000:
        return f"<pointer 0x{address:08X}>"
    try:
        raw = client.read(address, limit)
    except Exception:
        return f"<unreadable 0x{address:08X}>"
    raw = raw.split(b"\0", 1)[0]
    return raw.decode("ascii", "replace")


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    sequence = int.from_bytes(raw[0:4], "big")
    print(f"localization_calls = {sequence}")
    available = min(sequence, SLOT_COUNT)
    if not available:
        print("No localization key was resolved.")
        return
    first_sequence = sequence - available + 1
    for item_sequence in range(first_sequence, sequence + 1):
        slot = item_sequence & (SLOT_COUNT - 1)
        pointer = int.from_bytes(raw[4 + slot * 4 : 8 + slot * 4], "big")
        print(
            f"{item_sequence:8d}  0x{pointer:08X}  "
            f"{read_c_string(client, pointer)}"
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
        print(f"Localization-key trace: {state}")
        if args.action in ("status", "read"):
            describe(client)
            return 0
        if args.action == "apply":
            if state not in ("original", "patched"):
                raise RuntimeError("Unexpected localization resolver entry")
            cave = client.read(STUB, len(stub))
            if cave not in (bytes(len(stub)), stub):
                raise RuntimeError("Localization trace cave is not free")
            write_chunks(client, JOURNAL, bytes(JOURNAL_SIZE))
            client.write(STUB, stub)
            client.write(SITE, patch)
            if client.read(SITE, 4) != patch:
                raise RuntimeError("Localization trace verification failed")
            print("Verified: localization-key ring trace armed.")
            return 0
        if state == "patched":
            client.write(SITE, ORIGINAL)
        elif state != "original":
            raise RuntimeError("Unexpected localization resolver entry")
        print("Verified: original localization resolver restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

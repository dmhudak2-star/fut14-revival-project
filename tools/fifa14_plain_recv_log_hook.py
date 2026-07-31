#!/usr/bin/env python3
"""Log successful direct DirtySock receives without debugger breakpoints."""

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
)
from fifa14_plain_recv_hook import cmpwi, conditional_branch, or_register


RESULT_SITE = 0x82D6A2A4
ORIGINAL_RESULT_INSTRUCTION = bytes.fromhex("7C7E1B78")  # mr r30,r3

LOG_STUB = 0x83C8E000
LOG_COUNTER = 0x83C8E100
LOG_RING = 0x83C8E200
LOG_RECORD_SIZE = 0x20
LOG_RECORD_COUNT = 16


def build_stub() -> bytes:
    words = [
        cmpwi(3, 0),
        0,                          # ble fallback
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x1F00),      # -> 0x83C8E100
        lwz(10, 12, 0),
        addi(10, 10, 1),
        andi_dot(9, 10, 0xF),
        rlwinm(9, 9, 5, 0, 26),     # slot * 0x20
        addis(11, 0, 0x83C9),
        addi(11, 11, -0x1E00),      # -> 0x83C8E200
        add(11, 11, 9),
        stw(10, 11, 0x00),          # sequence
        stw(27, 11, 0x04),          # receive destination buffer
        stw(3, 11, 0x08),           # successful receive length
        stw(31, 11, 0x0C),          # owning DirtySock socket object
        stw(10, 12, 0),             # publish sequence last
    ]
    fallback_index = len(words)
    words.extend(
        [
            or_register(30, 3, 3),  # displaced mr r30,r3
            0,                      # b RESULT_SITE+4
        ]
    )

    def address(index: int) -> int:
        return LOG_STUB + index * 4

    words[1] = conditional_branch(
        address(1), address(fallback_index), 4, 1
    )  # ble
    words[-1] = branch(address(len(words) - 1), RESULT_SITE + 4, False)
    return b"".join(insn(word) for word in words)


LOG_STUB_BYTES = build_stub()
PATCHED_RESULT_INSTRUCTION = insn(
    branch(RESULT_SITE, LOG_STUB, link=False)
)


def state(client: Xbdm) -> str:
    value = client.read(RESULT_SITE, 4)
    if value == ORIGINAL_RESULT_INSTRUCTION:
        return "original"
    if value == PATCHED_RESULT_INSTRUCTION:
        return "hooked"
    return f"unexpected:{value.hex().upper()}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        current = state(client)
        print(f"Direct recv result site: {current}")
        if args.action == "status":
            count = int.from_bytes(client.read(LOG_COUNTER, 4), "big")
            print(f"Successful direct receives: {count}")
            return 0
        if args.action == "apply":
            if current == "hooked":
                print("Already hooked.")
                return 0
            if current != "original":
                raise RuntimeError("Refusing to overwrite unknown result site")
            cave = client.read(LOG_STUB, len(LOG_STUB_BYTES))
            if cave not in (bytes(len(LOG_STUB_BYTES)), LOG_STUB_BYTES):
                raise RuntimeError("Receive logger code cave is not empty")
            client.write(LOG_COUNTER, bytes(4))
            client.write(LOG_STUB, LOG_STUB_BYTES)
            if client.read(LOG_STUB, len(LOG_STUB_BYTES)) != LOG_STUB_BYTES:
                raise RuntimeError("Receive logger stub verification failed")
            client.write(RESULT_SITE, PATCHED_RESULT_INSTRUCTION)
            if state(client) != "hooked":
                client.write(RESULT_SITE, ORIGINAL_RESULT_INSTRUCTION)
                raise RuntimeError("Receive logger hook verification failed")
            print("Verified: direct plaintext receive logger active.")
            return 0
        if current == "original":
            print("Already restored.")
            return 0
        if current != "hooked":
            raise RuntimeError("Refusing to restore unknown result site")
        client.write(RESULT_SITE, ORIGINAL_RESULT_INSTRUCTION)
        if state(client) != "original":
            raise RuntimeError("Receive logger restore verification failed")
        print("Verified: original direct recv result instruction restored.")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

#!/usr/bin/env python3
"""Report the captured FUT XNet socket as connected after nonblocking connect."""

from __future__ import annotations

import argparse

from fifa14_connect_bypass import CONNECT_LOG
from fifa14_plain_recv_hook import conditional_branch
from fifa14_plain_send_hook import (
    Xbdm,
    addi,
    addis,
    insn,
    lwz,
    stw,
    verify_module,
)


STATUS_SITE = 0x82D6B9E8
STATUS_ORIGINAL = bytes.fromhex("4B75E9C9")  # bl 0x824CA3B0
STATUS_WRAPPER = 0x824CA3B0
STATUS_STUB = 0x83C8E500


def cmpw(ra: int, rb: int) -> int:
    return 0x7C000000 | (ra << 16) | (rb << 11)


def build_stub() -> bytes:
    # r3=socket, r4=0x4004667F, r5=int* status.
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x1900),      # -> CONNECT_LOG
        lwz(10, 12, 0x04),          # captured FUT NetDll handle
        cmpw(3, 10),
        0,                           # bne fallback
        addi(11, 0, 1),
        stw(11, 5, 0x00),           # connected
        addi(3, 0, 0),              # option query succeeded
        0x4E800020,                 # blr
    ]
    fallback = len(words)
    high = (STATUS_WRAPPER + 0x8000) >> 16
    words.extend(
        [
            addis(11, 0, high),
            addi(11, 11, STATUS_WRAPPER & 0xFFFF),
            0x7D6903A6,
            0x4E800420,
        ]
    )
    words[4] = conditional_branch(
        STATUS_STUB + 4 * 4,
        STATUS_STUB + fallback * 4,
        4,
        2,
    )
    return b"".join(insn(word) for word in words)


STATUS_STUB_BYTES = build_stub()
STATUS_PATCH = insn(
    0x48000001 | ((STATUS_STUB - STATUS_SITE) & 0x03FFFFFC)
)


def state(client: Xbdm) -> str:
    value = client.read(STATUS_SITE, 4)
    if value == STATUS_ORIGINAL:
        return "original"
    if value == STATUS_PATCH:
        return "patched"
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
        print(f"FUT connect-status option site: {current}")
        if args.action == "status":
            return 0
        if args.action == "apply":
            if current == "patched":
                print("Already patched.")
                return 0
            if current != "original":
                raise RuntimeError("Refusing to overwrite unexpected code")
            cave = client.read(STATUS_STUB, len(STATUS_STUB_BYTES))
            if cave not in (bytes(len(STATUS_STUB_BYTES)), STATUS_STUB_BYTES):
                raise RuntimeError("Connect-status code cave is not free")
            client.write(STATUS_STUB, STATUS_STUB_BYTES)
            if client.read(STATUS_STUB, len(STATUS_STUB_BYTES)) != STATUS_STUB_BYTES:
                raise RuntimeError("Connect-status stub verification failed")
            client.write(STATUS_SITE, STATUS_PATCH)
            if state(client) != "patched":
                client.write(STATUS_SITE, STATUS_ORIGINAL)
                raise RuntimeError("Connect-status patch verification failed")
            print("Verified: captured FUT socket reports connected.")
            return 0
        if current == "original":
            print("Already restored.")
            return 0
        if current != "patched":
            raise RuntimeError("Refusing to restore unexpected code")
        client.write(STATUS_SITE, STATUS_ORIGINAL)
        if state(client) != "original":
            raise RuntimeError("Connect-status restore failed")
        print("Verified: original connect-status option restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

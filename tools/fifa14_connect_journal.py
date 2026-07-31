#!/usr/bin/env python3
"""Journal FIFA 14 DirtySock connect calls while preserving real behavior."""

from __future__ import annotations

import argparse

from fifa14_connect_bypass import (
    CONNECT_CALLSITE,
    CONNECT_LOG,
    CONNECT_STUB,
    CONNECT_STUB_BYTES as BYPASS_STUB_BYTES,
    ORIGINAL_CONNECT_CALL,
)
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


CONNECT_WRAPPER = 0x824CA450


def build_stub() -> bytes:
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x1900),      # -> 0x83C8E700
        lwz(11, 12, 0),
        addi(11, 11, 1),
        stw(11, 12, 0x00),          # connect call count
        stw(3, 12, 0x04),           # NetDll socket handle
        stw(5, 12, 0x08),           # sockaddr length
    ]
    for offset in range(0, 0x10, 4):
        words.append(lwz(10, 4, offset))
        words.append(stw(10, 12, 0x10 + offset))
    high = (CONNECT_WRAPPER + 0x8000) >> 16
    words.extend(
        [
            addis(11, 0, high),
            addi(11, 11, CONNECT_WRAPPER & 0xFFFF),
            0x7D6903A6,             # mtctr r11
            0x4E800420,             # bctr; keep LR from the callsite
        ]
    )
    return b"".join(insn(word) for word in words)


CONNECT_STUB_BYTES = build_stub()
PATCHED_CONNECT_CALL = insn(
    branch(CONNECT_CALLSITE, CONNECT_STUB, link=True)
)


def state(client: Xbdm) -> str:
    call = client.read(CONNECT_CALLSITE, 4)
    if call == ORIGINAL_CONNECT_CALL:
        return "original"
    if call != PATCHED_CONNECT_CALL:
        return f"unexpected-call:{call.hex().upper()}"
    stub = client.read(CONNECT_STUB, len(CONNECT_STUB_BYTES))
    if stub == CONNECT_STUB_BYTES:
        return "journaled"
    if stub[: len(BYPASS_STUB_BYTES)] == BYPASS_STUB_BYTES:
        return "bypassed"
    return "unexpected-stub"


def print_log(client: Xbdm) -> None:
    record = client.read(CONNECT_LOG, 0x20)
    count = int.from_bytes(record[0:4], "big")
    handle = int.from_bytes(record[4:8], "big")
    length = int.from_bytes(record[8:12], "big")
    sockaddr = record[0x10:0x20]
    family = int.from_bytes(sockaddr[0:2], "big")
    port = int.from_bytes(sockaddr[2:4], "big")
    ip = ".".join(str(octet) for octet in sockaddr[4:8])
    print(
        f"Connect calls: {count}; last handle=0x{handle:08X} "
        f"family={family} addrlen={length} target={ip}:{port}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        current = state(client)
        print(f"DirtySock connect callsite: {current}")
        if args.action == "status":
            if current == "journaled":
                print_log(client)
            return 0

        if args.action == "apply":
            if current == "journaled":
                print_log(client)
                return 0
            if current != "original":
                raise RuntimeError("Refusing to replace a non-original hook")
            client.write(CONNECT_LOG, bytes(0x20))
            client.write(CONNECT_STUB, CONNECT_STUB_BYTES)
            if client.read(CONNECT_STUB, len(CONNECT_STUB_BYTES)) != CONNECT_STUB_BYTES:
                raise RuntimeError("Connect journal stub verification failed")
            client.write(CONNECT_CALLSITE, PATCHED_CONNECT_CALL)
            if state(client) != "journaled":
                client.write(CONNECT_CALLSITE, ORIGINAL_CONNECT_CALL)
                raise RuntimeError("Connect journal publication failed")
            print("Verified: DirtySock connect journal active.")
            return 0

        if current == "original":
            print("Already restored.")
            return 0
        if current != "journaled":
            raise RuntimeError("Refusing to restore an unknown connect hook")
        client.write(CONNECT_CALLSITE, ORIGINAL_CONNECT_CALL)
        if state(client) != "original":
            raise RuntimeError("Connect journal restore failed")
        print("Verified: original DirtySock connect call restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

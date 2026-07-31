#!/usr/bin/env python3
"""Temporarily make FIFA 14's DirtySock connect call return success."""

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


CONNECT_CALLSITE = 0x82D69E5C
ORIGINAL_CONNECT_CALL = bytes.fromhex("4B7605F5")  # bl 0x824CA450
CONNECT_STUB = 0x83C8E600
CONNECT_LOG = 0x83C8E700


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
    words.extend(
        [
            addi(3, 0, 0),          # simulated connect success
            0x4E800020,             # blr
        ]
    )
    return b"".join(insn(word) for word in words)


CONNECT_STUB_BYTES = build_stub()
PATCHED_CONNECT_CALL = insn(
    branch(CONNECT_CALLSITE, CONNECT_STUB, link=True)
)


def state(client: Xbdm) -> str:
    value = client.read(CONNECT_CALLSITE, 4)
    if value == ORIGINAL_CONNECT_CALL:
        return "original"
    if value == PATCHED_CONNECT_CALL:
        return "bypassed"
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
        print(f"DirtySock connect callsite: {current}")
        if args.action == "status":
            if current == "bypassed":
                record = client.read(CONNECT_LOG, 0x20)
                count = int.from_bytes(record[0:4], "big")
                handle = int.from_bytes(record[4:8], "big")
                length = int.from_bytes(record[8:12], "big")
                sockaddr = record[0x10:0x20]
                ip = ".".join(str(octet) for octet in sockaddr[4:8])
                port = int.from_bytes(sockaddr[2:4], "big")
                print(
                    f"Bypassed connect calls: {count}; "
                    f"last handle=0x{handle:08X} "
                    f"addrlen={length} target={ip}:{port}"
                )
            return 0
        if args.action == "apply":
            if current == "bypassed":
                print("Already bypassed.")
                return 0
            if current != "original":
                raise RuntimeError("Refusing to overwrite unknown connect call")
            cave = client.read(CONNECT_STUB, len(CONNECT_STUB_BYTES))
            if cave not in (bytes(len(CONNECT_STUB_BYTES)), CONNECT_STUB_BYTES):
                raise RuntimeError("Connect bypass code cave is not empty")
            client.write(CONNECT_LOG, bytes(0x20))
            client.write(CONNECT_STUB, CONNECT_STUB_BYTES)
            if client.read(CONNECT_STUB, len(CONNECT_STUB_BYTES)) != CONNECT_STUB_BYTES:
                raise RuntimeError("Connect bypass stub verification failed")
            client.write(CONNECT_CALLSITE, PATCHED_CONNECT_CALL)
            if state(client) != "bypassed":
                client.write(CONNECT_CALLSITE, ORIGINAL_CONNECT_CALL)
                raise RuntimeError("Connect bypass verification failed")
            print("Verified: DirtySock connect will return immediate success.")
            return 0
        if current == "original":
            print("Already restored.")
            return 0
        if current != "bypassed":
            raise RuntimeError("Refusing to restore unknown connect call")
        client.write(CONNECT_CALLSITE, ORIGINAL_CONNECT_CALL)
        if state(client) != "original":
            raise RuntimeError("Connect restore verification failed")
        print("Verified: original DirtySock connect call restored.")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

#!/usr/bin/env python3
"""Journal XNetServerToInAddr inputs without debugger breakpoints."""

from __future__ import annotations

import argparse

from fifa14_plain_send_hook import Xbdm, branch, insn, verify_module


WRAPPER = 0x83995090
ORIGINAL_WRAPPER = bytes.fromhex("482EE8C4")  # b 0x83C83954
XNET_SERVER_TO_IN_ADDR = 0x81740FC0

STUB = 0x83C8E900
LOG = 0x83C8EA00
LOG_SIZE = 0x30


def addi(rt: int, ra: int, immediate: int) -> int:
    return 0x38000000 | (rt << 21) | (ra << 16) | (immediate & 0xFFFF)


def addis(rt: int, ra: int, immediate: int) -> int:
    return 0x3C000000 | (rt << 21) | (ra << 16) | (immediate & 0xFFFF)


def lwz(rt: int, ra: int, displacement: int) -> int:
    return 0x80000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def stw(rs: int, ra: int, displacement: int) -> int:
    return 0x90000000 | (rs << 21) | (ra << 16) | (displacement & 0xFFFF)


def build_stub() -> bytes:
    words = [
        addis(12, 0, 0x83C9),       # lis  r12,0x83C9
        addi(12, 12, -0x1600),      # -> LOG (0x83C8EA00)
        lwz(11, 12, 0),
        addi(11, 11, 1),
        stw(11, 12, 0),
    ]
    for register in range(3, 11):
        words.append(stw(register, 12, 4 + (register - 3) * 4))
    words.extend(
        [
            addis(11, 0, 0x8174),
            addi(11, 11, 0x0FC0),
            0x7D6903A6,             # mtctr r11
            0x4E800420,             # bctr (preserves caller LR)
        ]
    )
    return b"".join(insn(word) for word in words)


STUB_BYTES = build_stub()
PATCHED_WRAPPER = insn(branch(WRAPPER, STUB, link=False))


def wrapper_state(client: Xbdm) -> str:
    value = client.read(WRAPPER, 4)
    if value == ORIGINAL_WRAPPER:
        return "original"
    if value == PATCHED_WRAPPER:
        return "hooked"
    return f"unexpected:{value.hex().upper()}"


def print_log(client: Xbdm) -> None:
    data = client.read(LOG, LOG_SIZE)
    values = [
        int.from_bytes(data[offset : offset + 4], "big")
        for offset in range(0, 0x24, 4)
    ]
    print(f"XNetServerToInAddr calls: {values[0]}")
    print(
        "Last inputs: "
        + " ".join(
            f"r{register}=0x{values[register - 2]:08X}"
            for register in range(3, 11)
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        current = wrapper_state(client)
        print(f"XNetServerToInAddr wrapper: {current}")
        if args.action == "status":
            print_log(client)
            return 0

        if args.action == "apply":
            if current == "hooked":
                print_log(client)
                return 0
            if current != "original":
                raise RuntimeError("Refusing to overwrite unknown wrapper")
            cave = client.read(STUB, len(STUB_BYTES))
            if cave not in (bytes(len(STUB_BYTES)), STUB_BYTES):
                raise RuntimeError("XNet journal code cave is not empty")
            client.write(LOG, bytes(LOG_SIZE))
            client.write(STUB, STUB_BYTES)
            if client.read(STUB, len(STUB_BYTES)) != STUB_BYTES:
                raise RuntimeError("XNet journal stub verification failed")
            client.write(WRAPPER, PATCHED_WRAPPER)
            if wrapper_state(client) != "hooked":
                client.write(WRAPPER, ORIGINAL_WRAPPER)
                raise RuntimeError("XNet wrapper hook verification failed")
            print("Verified: XNetServerToInAddr journal active.")
            return 0

        if current == "original":
            print("Already restored.")
            return 0
        if current != "hooked":
            raise RuntimeError("Refusing to restore unknown wrapper")
        client.write(WRAPPER, ORIGINAL_WRAPPER)
        if wrapper_state(client) != "original":
            raise RuntimeError("XNet wrapper restore failed")
        print("Verified: XNetServerToInAddr wrapper restored.")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

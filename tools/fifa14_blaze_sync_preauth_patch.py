#!/usr/bin/env python3
"""Synchronously dispatch a staged PreAuth reply immediately after its send."""

from __future__ import annotations

import argparse
from pathlib import Path

from fifa14_plain_recv_hook import (
    MAX_PENDING_PAYLOAD,
    PENDING_CURSOR,
    PENDING_LENGTH,
    PENDING_PAYLOAD,
    PENDING_SOCKET,
    conditional_branch,
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


SITE = 0x83AC7808
SITE_ORIGINAL = bytes.fromhex("7C7B1B78")  # mr r27,r3
STUB = 0x83C8E300
BLAZE_PUMP = 0x83AC83F0
DEFAULT_RESPONSE = Path(__file__).with_name("fifa14_preauth_local_reply.bin")


def lhz(rt: int, ra: int, displacement: int) -> int:
    return 0xA0000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def lbz(rt: int, ra: int, displacement: int) -> int:
    return 0x88000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def stb(rs: int, ra: int, displacement: int) -> int:
    return 0x98000000 | (rs << 21) | (ra << 16) | (displacement & 0xFFFF)


def cmpwi(ra: int, immediate: int) -> int:
    return 0x2C000000 | (ra << 16) | (immediate & 0xFFFF)


def build_stub(response_length: int) -> bytes:
    words = [
        0x9421FFA0,                  # stwu r1,-0x60(r1)
        0x7C0802A6,                  # mflr r0
        stw(0, 1, 0x54),
        stw(3, 1, 0x50),             # preserve send result
        lhz(10, 30, 0x02),           # outgoing component
        cmpwi(10, 9),
        0,                            # bne finish
        lhz(10, 30, 0x04),           # outgoing command
        cmpwi(10, 7),
        0,                            # bne finish
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x300),        # -> pending queue metadata
        lbz(10, 30, 0x09),
        stb(10, 12, 0x29),           # response transaction byte 0
        lbz(10, 30, 0x0A),
        stb(10, 12, 0x2A),
        lbz(10, 30, 0x0B),
        stb(10, 12, 0x2B),
        lwz(10, 31, 0x8C),           # Blaze connection's DirtySock owner
        stw(10, 12, 0x04),
        addis(10, 0, 0x83C9),
        addi(10, 10, -0x2E0),        # -> PENDING_PAYLOAD
        stw(10, 12, 0x08),
        addi(10, 0, response_length),
        stw(10, 12, 0x00),           # publish response last
        addi(3, 0, 0),
        addis(11, 0, (BLAZE_PUMP + 0x8000) >> 16),
        addi(11, 11, BLAZE_PUMP & 0xFFFF),
        0x7D6903A6,                  # mtctr r11
        0x4E800421,                  # bctrl
    ]
    finish = len(words)
    words.extend(
        [
            lwz(3, 1, 0x50),
            lwz(0, 1, 0x54),
            0x7C0803A6,              # mtlr r0
            addi(1, 1, 0x60),
            0x7C7B1B78,              # displaced mr r27,r3
            0,                       # b SITE+4
        ]
    )

    def address(index: int) -> int:
        return STUB + index * 4

    for index in (6, 9):
        words[index] = conditional_branch(
            address(index), address(finish), 4, 2
        )                             # bne
    words[-1] = branch(address(len(words) - 1), SITE + 4, False)
    return b"".join(insn(word) for word in words)


def patch_bytes(response_length: int) -> tuple[bytes, bytes]:
    stub = build_stub(response_length)
    patch = insn(branch(SITE, STUB, False))
    return stub, patch


def write_chunks(client: Xbdm, address: int, data: bytes) -> None:
    for offset in range(0, len(data), 0x80):
        client.write(address + offset, data[offset : offset + 0x80])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore"))
    parser.add_argument("--response", type=Path, default=DEFAULT_RESPONSE)
    args = parser.parse_args()

    response = args.response.read_bytes()
    if not 12 <= len(response) <= MAX_PENDING_PAYLOAD:
        raise RuntimeError("Invalid staged PreAuth response length")
    stub, patch = patch_bytes(len(response))

    client = Xbdm(args.host)
    try:
        verify_module(client)
        current = client.read(SITE, 4)
        state = (
            "original"
            if current == SITE_ORIGINAL
            else "patched"
            if current == patch
            else f"unexpected:{current.hex().upper()}"
        )
        print(f"Synchronous Blaze PreAuth site: {state}")
        if args.action == "status":
            print(
                f"Staged response: {len(response)} bytes; "
                f"pending={int.from_bytes(client.read(PENDING_LENGTH, 4), 'big')}"
            )
            return 0
        if args.action == "apply":
            if state not in ("original", "patched"):
                raise RuntimeError("Refusing to overwrite unexpected code")
            client.write(PENDING_LENGTH, bytes(4))
            write_chunks(client, PENDING_PAYLOAD, response)
            client.write(PENDING_CURSOR, PENDING_PAYLOAD.to_bytes(4, "big"))
            client.write(PENDING_SOCKET, bytes(4))
            if state == "original":
                cave = client.read(STUB, len(stub))
                if cave not in (bytes(len(stub)), stub):
                    raise RuntimeError("Synchronous PreAuth code cave is not free")
                write_chunks(client, STUB, stub)
                if client.read(STUB, len(stub)) != stub:
                    raise RuntimeError("Synchronous PreAuth stub verification failed")
                client.write(SITE, patch)
            if client.read(SITE, 4) != patch:
                raise RuntimeError("Synchronous PreAuth patch verification failed")
            print(
                "Verified: PreAuth response staged and same-flow Blaze "
                "dispatch active."
            )
            return 0
        if state == "original":
            print("Already restored.")
            return 0
        if state != "patched":
            raise RuntimeError("Refusing to restore unexpected code")
        client.write(SITE, SITE_ORIGINAL)
        client.write(PENDING_LENGTH, bytes(4))
        if client.read(SITE, 4) != SITE_ORIGINAL:
            raise RuntimeError("Synchronous PreAuth restore failed")
        print("Verified: original post-send instruction restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

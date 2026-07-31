#!/usr/bin/env python3
"""Queue a local PreAuth reply after the real ProtoSSL send path."""

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


SITE = 0x82D8D4A0
ORIGINAL = bytes.fromhex("7C7C1B78")  # mr r28,r3
STUB = 0x83C8DB00
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
        lhz(10, 29, 0x02),          # outgoing component
        cmpwi(10, 9),
        0,                           # bne finish
        lhz(10, 29, 0x04),          # outgoing command
        cmpwi(10, 7),
        0,                           # bne finish
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x300),       # pending queue metadata
        lbz(10, 29, 0x09),
        stb(10, 12, 0x29),          # response transaction byte 0
        lbz(10, 29, 0x0A),
        stb(10, 12, 0x2A),
        lbz(10, 29, 0x0B),
        stb(10, 12, 0x2B),
        lwz(10, 30, 0x00),          # ProtoSSL's DirtySock owner
        stw(10, 12, 0x04),
        addis(10, 0, 0x83C9),
        addi(10, 10, -0x2E0),       # PENDING_PAYLOAD
        stw(10, 12, 0x08),
        addi(10, 0, response_length),
        stw(10, 12, 0x00),          # publish last
    ]
    finish = len(words)
    words.extend(
        [
            0x7C7C1B78,             # displaced mr r28,r3
            0,                       # b SITE+4
        ]
    )

    def address(index: int) -> int:
        return STUB + index * 4

    for index in (2, 5):
        words[index] = conditional_branch(
            address(index), address(finish), 4, 2
        )                            # bne
    words[-1] = branch(address(len(words) - 1), SITE + 4, False)
    return b"".join(insn(word) for word in words)


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
    stub = build_stub(len(response))
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
        print(f"ProtoSSL PreAuth site: {state}")
        if args.action == "status":
            pending = int.from_bytes(client.read(PENDING_LENGTH, 4), "big")
            print(f"Staged response: {len(response)} bytes; pending={pending}")
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
                    raise RuntimeError("ProtoSSL PreAuth code cave is not free")
                write_chunks(client, STUB, stub)
                client.write(SITE, patch)
            if client.read(SITE, 4) != patch:
                raise RuntimeError("ProtoSSL PreAuth patch verification failed")
            print(
                "Verified: local PreAuth will be queued after the real "
                "ProtoSSL send."
            )
            return 0
        if state == "original":
            print("Already restored.")
            return 0
        if state != "patched":
            raise RuntimeError("Refusing to restore unexpected code")
        client.write(SITE, ORIGINAL)
        client.write(PENDING_LENGTH, bytes(4))
        if client.read(SITE, 4) != ORIGINAL:
            raise RuntimeError("ProtoSSL PreAuth restore failed")
        print("Verified: original ProtoSSL post-send instruction restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

#!/usr/bin/env python3
"""Trace the high-level Blaze receive callback used for a decoded frame."""

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


PRE_SITE = 0x83AC82F8
PRE_ORIGINAL = bytes.fromhex("7D6903A6")  # mtctr r11
PRE_STUB = 0x83C8D000

POST_SITE = 0x83AC8300
POST_ORIGINAL = bytes.fromhex("817F0108")  # lwz r11,0x108(r31)
POST_STUB = 0x83C8D080

JOURNAL = 0x83C8D200
JOURNAL_SIZE = 0x80


def build_pre_stub() -> bytes:
    words = [
        addis(10, 0, 0x83C9),
        addi(10, 10, -0x2E00),      # r10 = JOURNAL
        stw(11, 10, 0x00),          # receive callback
        stw(3, 10, 0x04),           # Blaze connection
        stw(4, 10, 0x08),           # decoded payload pointer
        stw(5, 10, 0x0C),           # decoded payload length
        stw(6, 10, 0x10),           # current tick
        lwz(9, 3, 0xA0),
        stw(9, 10, 0x14),           # connection state before callback
        lwz(9, 3, 0xA4),
        stw(9, 10, 0x18),           # current message number
        lwz(9, 3, 0xAC),
        stw(9, 10, 0x1C),
        lwz(9, 4, 0x00),
        stw(9, 10, 0x40),           # first 16 bytes of TDF payload
        lwz(9, 4, 0x04),
        stw(9, 10, 0x44),
        lwz(9, 4, 0x08),
        stw(9, 10, 0x48),
        lwz(9, 4, 0x0C),
        stw(9, 10, 0x4C),
        0x7D6903A6,                  # displaced mtctr r11
        0,                           # b PRE_SITE+4
    ]
    words[-1] = branch(
        PRE_STUB + (len(words) - 1) * 4, PRE_SITE + 4, False
    )
    return b"".join(insn(word) for word in words)


def build_post_stub() -> bytes:
    words = [
        addis(10, 0, 0x83C9),
        addi(10, 10, -0x2E00),      # r10 = JOURNAL
        stw(3, 10, 0x20),           # callback return value
        lwz(9, 31, 0xA0),
        stw(9, 10, 0x24),           # connection state after callback
        lwz(9, 31, 0x108),
        stw(9, 10, 0x28),
        lwz(9, 31, 0x10C),
        stw(9, 10, 0x2C),
        lwz(11, 31, 0x108),         # displaced instruction
        0,                           # b POST_SITE+4
    ]
    words[-1] = branch(
        POST_STUB + (len(words) - 1) * 4, POST_SITE + 4, False
    )
    return b"".join(insn(word) for word in words)


def write_chunks(client: Xbdm, address: int, data: bytes) -> None:
    for offset in range(0, len(data), 0x80):
        client.write(address + offset, data[offset : offset + 0x80])


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    names = [
        ("callback", 0x00),
        ("connection", 0x04),
        ("payload", 0x08),
        ("length", 0x0C),
        ("tick", 0x10),
        ("state_before", 0x14),
        ("message_number", 0x18),
        ("connection_ac", 0x1C),
        ("callback_return", 0x20),
        ("state_after", 0x24),
        ("callbacks_active", 0x28),
        ("deferred_flags", 0x2C),
    ]
    for name, offset in names:
        value = int.from_bytes(raw[offset : offset + 4], "big")
        print(f"{name:16} = 0x{value:08X} ({value})")
    print(f"payload_prefix   = {raw[0x40:0x50].hex().upper()}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    args = parser.parse_args()

    pre_stub = build_pre_stub()
    post_stub = build_post_stub()
    pre_patch = insn(branch(PRE_SITE, PRE_STUB, False))
    post_patch = insn(branch(POST_SITE, POST_STUB, False))

    client = Xbdm(args.host)
    try:
        verify_module(client)
        pre = client.read(PRE_SITE, 4)
        post = client.read(POST_SITE, 4)
        print(
            "Blaze callback trace: "
            f"pre={pre.hex().upper()} post={post.hex().upper()}"
        )
        if args.action in ("status", "read"):
            describe(client)
            return 0
        if args.action == "apply":
            if pre not in (PRE_ORIGINAL, pre_patch):
                raise RuntimeError("Unexpected pre-callback instruction")
            if post not in (POST_ORIGINAL, post_patch):
                raise RuntimeError("Unexpected post-callback instruction")
            client.write(JOURNAL, bytes(JOURNAL_SIZE))
            for address, stub in ((PRE_STUB, pre_stub), (POST_STUB, post_stub)):
                current = client.read(address, len(stub))
                if current not in (bytes(len(stub)), stub):
                    raise RuntimeError(f"Code cave 0x{address:08X} is not free")
                write_chunks(client, address, stub)
            client.write(PRE_SITE, pre_patch)
            client.write(POST_SITE, post_patch)
            if (
                client.read(PRE_SITE, 4) != pre_patch
                or client.read(POST_SITE, 4) != post_patch
            ):
                raise RuntimeError("Callback trace patch verification failed")
            print("Verified: Blaze callback trace armed.")
            return 0
        if pre == pre_patch:
            client.write(PRE_SITE, PRE_ORIGINAL)
        if post == post_patch:
            client.write(POST_SITE, POST_ORIGINAL)
        if (
            client.read(PRE_SITE, 4) != PRE_ORIGINAL
            or client.read(POST_SITE, 4) != POST_ORIGINAL
        ):
            raise RuntimeError("Callback trace restore failed")
        print("Verified: Blaze callback trace restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

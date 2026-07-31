#!/usr/bin/env python3
"""Trace successful ProtoSSLRecv calls and their direct caller without breakpoints."""

from __future__ import annotations

import argparse

from fifa14_plain_recv_hook import cmpwi, conditional_branch
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


ENTRY_SITE = 0x82D8D4B0
ENTRY_ORIGINAL = bytes.fromhex("7D8802A6")  # mflr r12
ENTRY_STUB = 0x83C8CC00

POST_SITE = 0x82D8D5E0
POST_ORIGINAL = bytes.fromhex("7C7E1B78")  # mr r30,r3
POST_STUB = 0x83C8CC80

JOURNAL = 0x83C8CD00
JOURNAL_SIZE = 0x80


def build_entry_stub() -> bytes:
    words = [
        int.from_bytes(ENTRY_ORIGINAL, "big"),
        addis(11, 0, 0x83C9),
        addi(11, 11, -0x3300),      # JOURNAL
        stw(12, 11, 0x00),          # current ProtoSSLRecv caller LR
        stw(3, 11, 0x04),           # ProtoSSL object
        stw(4, 11, 0x08),           # destination
        stw(5, 11, 0x0C),           # requested length
        0,
    ]
    words[-1] = branch(
        ENTRY_STUB + (len(words) - 1) * 4, ENTRY_SITE + 4, False
    )
    return b"".join(insn(word) for word in words)


def build_post_stub() -> bytes:
    words = [
        cmpwi(3, 0),
        0,                           # ble fallback
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x3300),      # JOURNAL
        lwz(11, 12, 0x00),
        stw(11, 12, 0x10),          # successful call's caller LR
        stw(3, 12, 0x14),           # returned byte count
        stw(29, 12, 0x18),          # ProtoSSL object
        stw(28, 12, 0x1C),          # destination
        stw(27, 12, 0x20),          # requested length
        stw(1, 12, 0x24),           # current stack pointer
        lwz(11, 12, 0x28),
        addi(11, 11, 1),
        stw(11, 12, 0x28),          # successful return count
        lwz(11, 28, 0x00),
        stw(11, 12, 0x30),
        lwz(11, 28, 0x04),
        stw(11, 12, 0x34),
        lwz(11, 28, 0x08),
        stw(11, 12, 0x38),
        lwz(11, 28, 0x0C),
        stw(11, 12, 0x3C),
    ]
    fallback = len(words)
    words.extend(
        [
            int.from_bytes(POST_ORIGINAL, "big"),
            0,
        ]
    )
    words[1] = conditional_branch(
        POST_STUB + 4, POST_STUB + fallback * 4, 4, 1
    )                                # ble
    words[-1] = branch(
        POST_STUB + (len(words) - 1) * 4, POST_SITE + 4, False
    )
    return b"".join(insn(word) for word in words)


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    lr = u32(raw, 0x10)
    print(f"successful_calls = {u32(raw, 0x28)}")
    print(f"caller_lr        = 0x{lr:08X}")
    print(
        f"caller_callsite  = 0x{(lr - 4) & 0xFFFFFFFF:08X}"
        if lr
        else "caller_callsite  = 0"
    )
    print(f"returned_bytes   = {u32(raw, 0x14)}")
    print(f"protossl         = 0x{u32(raw, 0x18):08X}")
    print(f"destination      = 0x{u32(raw, 0x1C):08X}")
    print(f"requested        = {u32(raw, 0x20)}")
    print(f"stack_pointer    = 0x{u32(raw, 0x24):08X}")
    print(f"payload_prefix   = {raw[0x30:0x40].hex().upper()}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    args = parser.parse_args()

    entry_stub = build_entry_stub()
    post_stub = build_post_stub()
    entry_patch = insn(branch(ENTRY_SITE, ENTRY_STUB, False))
    post_patch = insn(branch(POST_SITE, POST_STUB, False))
    client = Xbdm(args.host)
    try:
        verify_module(client)
        entry = client.read(ENTRY_SITE, 4)
        post = client.read(POST_SITE, 4)
        print(
            "ProtoSSLRecv trace: "
            f"entry={entry.hex().upper()} post={post.hex().upper()}"
        )
        if args.action in ("status", "read"):
            describe(client)
            return 0
        if args.action == "apply":
            if entry not in (ENTRY_ORIGINAL, entry_patch):
                raise RuntimeError("Unexpected ProtoSSLRecv entry")
            if post not in (POST_ORIGINAL, post_patch):
                raise RuntimeError("Unexpected ProtoSSLRecv post-call site")
            client.write(JOURNAL, bytes(JOURNAL_SIZE))
            for address, stub in (
                (ENTRY_STUB, entry_stub),
                (POST_STUB, post_stub),
            ):
                cave = client.read(address, len(stub))
                if cave not in (bytes(len(stub)), stub):
                    raise RuntimeError(f"Code cave 0x{address:08X} is not free")
                client.write(address, stub)
            client.write(ENTRY_SITE, entry_patch)
            client.write(POST_SITE, post_patch)
            if (
                client.read(ENTRY_SITE, 4) != entry_patch
                or client.read(POST_SITE, 4) != post_patch
            ):
                raise RuntimeError("ProtoSSLRecv trace verification failed")
            print("Verified: successful ProtoSSLRecv trace armed.")
            return 0
        if entry == entry_patch:
            client.write(ENTRY_SITE, ENTRY_ORIGINAL)
        elif entry != ENTRY_ORIGINAL:
            raise RuntimeError("Unexpected ProtoSSLRecv entry")
        if post == post_patch:
            client.write(POST_SITE, POST_ORIGINAL)
        elif post != POST_ORIGINAL:
            raise RuntimeError("Unexpected ProtoSSLRecv post-call site")
        print("Verified: original ProtoSSLRecv sites restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

#!/usr/bin/env python3
"""Record the caller that consumes the queued synthetic recv response."""

from __future__ import annotations

import argparse

from fifa14_plain_recv_hook import (
    RECV_STUB,
    cmpw,
    cmpwi,
    conditional_branch,
)
from fifa14_plain_send_hook import (
    Xbdm,
    addi,
    addis,
    branch,
    insn,
    stw,
    verify_module,
)


SITE = RECV_STUB + 8 * 4
ORIGINAL = insn(cmpw(11, 5))
STUB = 0x83C8D900
JOURNAL = 0x83C8D980
JOURNAL_SIZE = 0x20
MIN_TRACED_LENGTH = 0x100


def build_stub() -> bytes:
    words = [
        cmpwi(11, MIN_TRACED_LENGTH),
        0,                            # ble restore_queue_base
        0x7C0802A6,                  # mflr r0: caller return address
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x2680),      # r12 = JOURNAL
        stw(0, 12, 0x00),
        stw(3, 12, 0x04),           # DirtySock owner
        stw(4, 12, 0x08),           # recv destination
        stw(5, 12, 0x0C),           # requested length
        stw(11, 12, 0x10),          # queued length before consumption
    ]
    restore_queue_base = len(words)
    words.extend(
        [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x300),       # restore pending-queue metadata base
        int.from_bytes(ORIGINAL, "big"),
        0,
        ]
    )
    words[1] = conditional_branch(
        STUB + 4,
        STUB + restore_queue_base * 4,
        4,
        1,
    )                                # ble
    words[-1] = branch(
        STUB + (len(words) - 1) * 4, SITE + 4, False
    )
    return b"".join(insn(word) for word in words)


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    lr = int.from_bytes(raw[0:4], "big")
    print(f"return_address = 0x{lr:08X}")
    print(f"callsite       = 0x{(lr - 4) & 0xFFFFFFFF:08X}")
    print(f"owner          = 0x{int.from_bytes(raw[4:8], 'big'):08X}")
    print(f"destination    = 0x{int.from_bytes(raw[8:12], 'big'):08X}")
    print(f"requested      = {int.from_bytes(raw[12:16], 'big')}")
    print(f"queued_before  = {int.from_bytes(raw[16:20], 'big')}")


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
        print(f"Pending recv caller trace site: {current.hex().upper()}")
        if args.action in ("status", "read"):
            describe(client)
            return 0
        if args.action == "apply":
            if current not in (ORIGINAL, patch):
                raise RuntimeError(
                    "Plain recv hook is absent or has an unexpected body"
                )
            if current == patch:
                # Unpublish before replacing this tool's private code cave.
                client.write(SITE, ORIGINAL)
            cave = client.read(STUB, len(stub))
            if cave not in (bytes(len(stub)), stub) and current != patch:
                raise RuntimeError("Caller trace code cave is not free")
            client.write(JOURNAL, bytes(JOURNAL_SIZE))
            client.write(STUB, stub)
            client.write(SITE, patch)
            if client.read(SITE, 4) != patch:
                raise RuntimeError("Caller trace verification failed")
            print("Verified: pending recv caller trace armed.")
            return 0
        if current == patch:
            client.write(SITE, ORIGINAL)
        elif current != ORIGINAL:
            raise RuntimeError("Unexpected caller trace site")
        print("Verified: pending recv caller trace restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

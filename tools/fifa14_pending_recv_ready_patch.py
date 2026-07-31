#!/usr/bin/env python3
"""Make DirtySock poll the queued local response as readable."""

from __future__ import annotations

import argparse
import struct

from fifa14_plain_recv_hook import (
    PENDING_LENGTH,
    PENDING_SOCKET,
    conditional_branch,
)
from fifa14_plain_send_hook import (
    Xbdm,
    addi,
    addis,
    insn,
    lwz,
    stw,
    verify_module,
)
from fifa14_pending_response_pump_patch import DELAY_CONTROL


SELECT_SITE = 0x82D6A7DC
SELECT_ORIGINAL = bytes.fromhex("4B75FCB5")  # bl 0x824CA490
SELECT_WRAPPER = 0x824CA490
SELECT_STUB = 0x83C8EA00
SELECT_LOG = 0x83C8EAC0
SELECT_LOG_SIZE = 0x20


def cmpwi(ra: int, immediate: int) -> int:
    return 0x2C000000 | (ra << 16) | (immediate & 0xFFFF)


def build_stub() -> bytes:
    # PENDING_LENGTH and PENDING_SOCKET are adjacent at 0x83C8FD00.
    assert PENDING_SOCKET == PENDING_LENGTH + 4
    words = [
        # Diagnostic journal.  It is deliberately written before examining the
        # pending queue so we can distinguish "select was never called" from a
        # socket/argument mismatch.
        addis(8, 0, 0x83C9),
        addi(8, 8, -0x1540),        # -> SELECT_LOG (0x83C8EAC0)
        lwz(9, 8, 0x00),
        addi(9, 9, 1),
        stw(9, 8, 0x00),            # total calls
        stw(4, 8, 0x04),            # last read fd_set pointer
        stw(5, 8, 0x08),            # last write fd_set pointer
        stw(6, 8, 0x0C),            # last except fd_set pointer
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x300),       # -> PENDING_LENGTH
        lwz(11, 12, 0x00),
        stw(11, 8, 0x10),           # last pending length
        cmpwi(11, 0),
        0,                            # beq fallback
        addis(10, 0, 0x83C9),
        addi(10, 10, -0x3180),       # -> DELAY_CONTROL
        lwz(11, 10, 0x00),           # delayed-response ticks
        cmpwi(11, 0),
        0,                            # bne fallback
        lwz(10, 12, 0x04),           # queued DirtySock owner
        stw(10, 8, 0x14),           # last pending owner
        cmpwi(10, 0),
        0,                            # beq fallback
        lwz(9, 10, 0x18),            # NetDll socket handle
        cmpwi(9, -1),
        0,                            # beq fallback (closed owner)
        stw(9, 8, 0x18),             # last queued handle
        lwz(10, 8, 0x1C),
        addi(10, 10, 1),
        stw(10, 8, 0x1C),            # calls forced readable
        addi(11, 0, 1),
        stw(11, 4, 0x00),            # read fd_set count
        stw(9, 4, 0x04),             # readable handle
        addi(11, 0, 0),
        stw(11, 6, 0x00),            # clear except fd_set
        addi(3, 0, 1),                # one readable socket
        0x4E800020,                   # blr
    ]
    fallback = len(words)
    high = (SELECT_WRAPPER + 0x8000) >> 16
    words.extend(
        [
            addis(11, 0, high),
            addi(11, 11, SELECT_WRAPPER & 0xFFFF),
            0x7D6903A6,
            0x4E800420,
        ]
    )
    for index in (13, 22, 25):
        words[index] = conditional_branch(
            SELECT_STUB + index * 4,
            SELECT_STUB + fallback * 4,
            12,
            2,
        )                             # beq
    words[18] = conditional_branch(
        SELECT_STUB + 18 * 4,
        SELECT_STUB + fallback * 4,
        4,
        2,
    )                                 # bne
    return b"".join(insn(word) for word in words)


SELECT_STUB_BYTES = build_stub()
SELECT_PATCH = insn(
    0x48000001 | ((SELECT_STUB - SELECT_SITE) & 0x03FFFFFC)
)


def state(client: Xbdm) -> str:
    value = client.read(SELECT_SITE, 4)
    if value == SELECT_ORIGINAL:
        return "original"
    if value == SELECT_PATCH:
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
        print(f"Pending-recv select site: {current}")
        if args.action == "status":
            values = client.read(SELECT_LOG, SELECT_LOG_SIZE)
            fields = struct.unpack(">8I", values)
            print(
                "Select journal: "
                f"calls={fields[0]} forced={fields[7]} "
                f"read=0x{fields[1]:08X} write=0x{fields[2]:08X} "
                f"except=0x{fields[3]:08X} pending={fields[4]} "
                f"owner=0x{fields[5]:08X} handle=0x{fields[6]:08X}"
            )
            return 0
        if args.action == "apply":
            if current == "patched":
                live = client.read(SELECT_STUB, len(SELECT_STUB_BYTES))
                if live == SELECT_STUB_BYTES:
                    client.write(SELECT_LOG, bytes(SELECT_LOG_SIZE))
                    print("Already patched; diagnostics reset.")
                    return 0
                # Never rewrite executable cave bytes while another title
                # thread can still branch into them.  If this is a recognized
                # older image, unpublish first and leave the original callsite
                # on any failed upgrade.
                if live[:0x50] != build_stub()[:0x50]:
                    raise RuntimeError("Unexpected live pending-recv stub")
                client.write(SELECT_SITE, SELECT_ORIGINAL)
                try:
                    client.write(SELECT_LOG, bytes(SELECT_LOG_SIZE))
                    client.write(SELECT_STUB, SELECT_STUB_BYTES)
                    if (
                        client.read(SELECT_STUB, len(SELECT_STUB_BYTES))
                        != SELECT_STUB_BYTES
                    ):
                        raise RuntimeError(
                            "Instrumented pending-recv stub verification failed"
                        )
                    client.write(SELECT_SITE, SELECT_PATCH)
                except Exception:
                    try:
                        client.write(SELECT_SITE, SELECT_ORIGINAL)
                    except Exception:
                        pass
                    raise
                print("Verified: pending-recv patch safely upgraded.")
                return 0
            if current != "original":
                raise RuntimeError("Refusing to overwrite unexpected code")
            cave = client.read(SELECT_STUB, len(SELECT_STUB_BYTES))
            if cave not in (bytes(len(SELECT_STUB_BYTES)), SELECT_STUB_BYTES):
                # An older, shorter version of this same stub may occupy the
                # prefix.  It is safe to replace while the callsite is still
                # original.
                if cave[:0x50] != build_stub()[:0x50]:
                    raise RuntimeError("Pending-recv code cave is not free")
            client.write(SELECT_LOG, bytes(SELECT_LOG_SIZE))
            client.write(SELECT_STUB, SELECT_STUB_BYTES)
            if client.read(SELECT_STUB, len(SELECT_STUB_BYTES)) != SELECT_STUB_BYTES:
                raise RuntimeError("Pending-recv stub verification failed")
            client.write(SELECT_SITE, SELECT_PATCH)
            if state(client) != "patched":
                client.write(SELECT_SITE, SELECT_ORIGINAL)
                raise RuntimeError("Pending-recv patch verification failed")
            print("Verified: queued local response polls readable.")
            return 0
        if current == "original":
            print("Already restored.")
            return 0
        if current != "patched":
            raise RuntimeError("Refusing to restore unexpected code")
        client.write(SELECT_SITE, SELECT_ORIGINAL)
        if state(client) != "original":
            raise RuntimeError("Pending-recv restore failed")
        print("Verified: original read poll restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

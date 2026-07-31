#!/usr/bin/env python3
"""Complete the queued FUT response through DirtySock's overlapped receive path."""

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


# Socket mode 1 posts WSARecvFrom asynchronously and polls its completion here.
RESULT_SITE = 0x82D69AC8
RESULT_ORIGINAL = bytes.fromhex("4B7609E9")  # bl 0x824CA4B0
RESULT_WRAPPER = 0x824CA4B0               # NetDll_WSAGetOverlappedResult
RESULT_STUB = 0x83C8E400
RESULT_LOG = 0x83C8E580
RESULT_LOG_SIZE = 0x24


def cmpwi(ra: int, immediate: int) -> int:
    return 0x2C000000 | (ra << 16) | (immediate & 0xFFFF)


def cmpw(ra: int, rb: int) -> int:
    return 0x7C000000 | (ra << 16) | (rb << 11)


def lbz(rt: int, ra: int, displacement: int) -> int:
    return 0x88000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def stb(rs: int, ra: int, displacement: int) -> int:
    return 0x98000000 | (rs << 21) | (ra << 16) | (displacement & 0xFFFF)


def build_stub() -> bytes:
    # At this callsite:
    #   r3 = socket handle
    #   r4 = WSAOVERLAPPED*
    #   r5 = owner+0xB4 (bytes transferred)
    #   r7 = owner+0x9C (flags)
    # The receive WSABUF posted by DirtySock points at owner+0xB8.
    words = [
        addis(8, 0, 0x83C9),
        addi(8, 8, -0x1A80),        # -> RESULT_LOG
        lwz(9, 8, 0x00),
        addi(9, 9, 1),
        stw(9, 8, 0x00),            # total calls
        stw(3, 8, 0x04),            # last socket handle
        stw(4, 8, 0x08),            # last overlapped pointer
        stw(5, 8, 0x0C),            # last byte-count pointer
        stw(7, 8, 0x10),            # last flags pointer
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x300),       # -> PENDING_LENGTH
        lwz(11, 12, 0x00),          # queued response length
        stw(11, 8, 0x14),           # last pending length
        cmpwi(11, 0),
        0,                           # beq fallback
        lwz(9, 8, 0x18),
        addi(9, 9, 1),
        stw(9, 8, 0x18),            # calls observed while pending
        lwz(10, 12, 0x04),          # queued DirtySock owner
        stw(10, 8, 0x1C),           # last pending owner
        cmpwi(10, 0),
        0,                           # beq fallback
        addi(9, 10, 0xB4),
        cmpw(5, 9),                  # completion belongs to queued owner?
        0,                           # bne fallback
        lwz(9, 8, 0x20),
        addi(9, 9, 1),
        stw(9, 8, 0x20),            # matching completions
        lwz(9, 12, 0x08),           # queued response cursor
        addi(10, 10, 0xB8),         # posted receive buffer
        0x7D6903A6,                 # mtctr r11
    ]
    loop = len(words)
    words.extend(
        [
            lbz(8, 9, 0),
            stb(8, 10, 0),
            addi(9, 9, 1),
            addi(10, 10, 1),
            0,                       # bdnz loop
            stw(11, 5, 0),           # *bytes_transferred = queued length
            addi(8, 0, 0),
            stw(8, 7, 0),            # *flags = 0
            stw(8, 12, 0),           # consume queue
            stw(9, 12, 8),           # publish advanced cursor
            addi(3, 0, 1),           # overlapped operation completed
            0x4E800020,              # blr
        ]
    )
    fallback = len(words)
    high = (RESULT_WRAPPER + 0x8000) >> 16
    words.extend(
        [
            addis(11, 0, high),
            addi(11, 11, RESULT_WRAPPER & 0xFFFF),
            0x7D6903A6,              # mtctr r11
            0x4E800420,              # bctr; retain callsite LR
        ]
    )

    def address(index: int) -> int:
        return RESULT_STUB + index * 4

    for index in (14, 21, 24):
        words[index] = conditional_branch(
            address(index), address(fallback), 12 if index != 24 else 4, 2
        )
    words[loop + 4] = conditional_branch(
        address(loop + 4), address(loop), 16, 0
    )                               # bdnz
    return b"".join(insn(word) for word in words)


RESULT_STUB_BYTES = build_stub()
RESULT_PATCH = insn(
    0x48000001 | ((RESULT_STUB - RESULT_SITE) & 0x03FFFFFC)
)


def state(client: Xbdm) -> str:
    value = client.read(RESULT_SITE, 4)
    if value == RESULT_ORIGINAL:
        return "original"
    if value == RESULT_PATCH:
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
        print(f"Overlapped receive completion site: {current}")
        if args.action == "status":
            pending = int.from_bytes(client.read(PENDING_LENGTH, 4), "big")
            owner = int.from_bytes(client.read(PENDING_SOCKET, 4), "big")
            print(f"Pending: {pending} bytes for owner 0x{owner:08X}")
            fields = struct.unpack(
                ">9I", client.read(RESULT_LOG, RESULT_LOG_SIZE)
            )
            print(
                "Completion journal: "
                f"calls={fields[0]} pending_calls={fields[6]} "
                f"matches={fields[8]} socket=0x{fields[1]:08X} "
                f"overlapped=0x{fields[2]:08X} bytes_ptr=0x{fields[3]:08X} "
                f"flags_ptr=0x{fields[4]:08X} pending={fields[5]} "
                f"owner=0x{fields[7]:08X}"
            )
            return 0
        if args.action == "apply":
            if current == "patched":
                client.write(RESULT_LOG, bytes(RESULT_LOG_SIZE))
                client.write(RESULT_STUB, RESULT_STUB_BYTES)
                if client.read(RESULT_STUB, len(RESULT_STUB_BYTES)) != RESULT_STUB_BYTES:
                    raise RuntimeError("Instrumented completion stub verification failed")
                print("Verified: completion patch upgraded with diagnostics.")
                return 0
            if current != "original":
                raise RuntimeError("Refusing to overwrite unexpected code")
            cave = client.read(RESULT_STUB, len(RESULT_STUB_BYTES))
            if cave not in (bytes(len(RESULT_STUB_BYTES)), RESULT_STUB_BYTES):
                raise RuntimeError("Overlapped receive code cave is not free")
            client.write(RESULT_LOG, bytes(RESULT_LOG_SIZE))
            client.write(RESULT_STUB, RESULT_STUB_BYTES)
            if client.read(RESULT_STUB, len(RESULT_STUB_BYTES)) != RESULT_STUB_BYTES:
                raise RuntimeError("Overlapped receive stub verification failed")
            client.write(RESULT_SITE, RESULT_PATCH)
            if state(client) != "patched":
                client.write(RESULT_SITE, RESULT_ORIGINAL)
                raise RuntimeError("Overlapped receive patch verification failed")
            print("Verified: queued response completes the matching async receive.")
            return 0
        if current == "original":
            print("Already restored.")
            return 0
        if current != "patched":
            raise RuntimeError("Refusing to restore unexpected code")
        client.write(RESULT_SITE, RESULT_ORIGINAL)
        if state(client) != "original":
            raise RuntimeError("Overlapped receive restore failed")
        print("Verified: original overlapped completion restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

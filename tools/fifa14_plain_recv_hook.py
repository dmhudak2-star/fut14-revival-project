#!/usr/bin/env python3
"""Install a volatile DirtySock recv queue hook in FIFA 14."""

from __future__ import annotations

import argparse
import struct

from fifa14_plain_send_hook import Xbdm, branch, insn, verify_module


RECV_ENTRY = 0x82D6A108
ORIGINAL_RECV_ENTRY = bytes.fromhex("7D8802A6")  # mflr r12

RECV_STUB = 0x83C8FC00
PENDING_LENGTH = 0x83C8FD00
PENDING_SOCKET = 0x83C8FD04
PENDING_CURSOR = 0x83C8FD08
PENDING_PAYLOAD = 0x83C8FD20
MAX_PENDING_PAYLOAD = 0x2E0


def addi(rt: int, ra: int, immediate: int) -> int:
    return 0x38000000 | (rt << 21) | (ra << 16) | (immediate & 0xFFFF)


def addis(rt: int, ra: int, immediate: int) -> int:
    return 0x3C000000 | (rt << 21) | (ra << 16) | (immediate & 0xFFFF)


def lwz(rt: int, ra: int, displacement: int) -> int:
    return 0x80000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def stw(rs: int, ra: int, displacement: int) -> int:
    return 0x90000000 | (rs << 21) | (ra << 16) | (displacement & 0xFFFF)


def lbz(rt: int, ra: int, displacement: int) -> int:
    return 0x88000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def stb(rs: int, ra: int, displacement: int) -> int:
    return 0x98000000 | (rs << 21) | (ra << 16) | (displacement & 0xFFFF)


def or_register(ra: int, rs: int, rb: int) -> int:
    return 0x7C000378 | (rs << 21) | (ra << 16) | (rb << 11)


def cmpwi(ra: int, immediate: int) -> int:
    return 0x2C000000 | (ra << 16) | (immediate & 0xFFFF)


def cmpw(ra: int, rb: int) -> int:
    return 0x7C000000 | (ra << 16) | (rb << 11)


def subf(rt: int, ra: int, rb: int) -> int:
    return 0x7C000050 | (rt << 21) | (ra << 16) | (rb << 11)


def conditional_branch(
    source: int, target: int, branch_option: int, condition_bit: int
) -> int:
    displacement = target - source
    if displacement % 4 or not -(1 << 15) <= displacement < (1 << 15):
        raise ValueError("PowerPC conditional branch target is out of range")
    return (
        0x40000000
        | (branch_option << 21)
        | (condition_bit << 16)
        | (displacement & 0xFFFC)
    )


def build_stub(*, guard_zero_length: bool = True) -> bytes:
    # The queued response is published by writing PENDING_LENGTH last.
    words: list[int] = [
        addis(12, 0, 0x83C9),      # lis  r12,0x83C9
        addi(12, 12, -0x300),      # -> 0x83C8FD00
        lwz(11, 12, 0),            # queued length
        cmpwi(11, 0),
        0,                          # beq fallback
        lwz(10, 12, 4),            # queued socket object
        cmpw(3, 10),
        0,                          # bne fallback
    ]
    empty_branch = 4
    socket_branch = 7
    zero_branch: int | None = None
    if guard_zero_length:
        words.extend(
            (
                cmpwi(5, 0),
                0,                  # ble fallback; CTR=0 would underflow
            )
        )
        zero_branch = len(words) - 1
    count_branch = len(words) + 1
    words.extend(
        [
            cmpw(11, 5),            # remaining bytes vs requested bytes
            0,                      # ble count_ready
            or_register(11, 5, 5),  # mr r11,r5 (cap to recv size)
        ]
    )
    count_ready = len(words)
    words.extend(
        [
        lwz(9, 12, 8),              # current response source
        or_register(10, 4, 4),     # mr r10,r4 (recv destination)
        0x7D6903A6,                # mtctr r11
        ]
    )
    loop_index = len(words)
    words.extend(
        [
            lbz(8, 9, 0),
            stb(8, 10, 0),
            addi(9, 9, 1),
            addi(10, 10, 1),
            0,                      # bdnz copy_loop
            lwz(8, 12, 0),
            subf(8, 11, 8),         # remaining -= returned count
            stw(8, 12, 0),
            stw(9, 12, 8),          # advance response cursor
            stw(11, 3, 0x1C),       # DirtySock last result
            or_register(3, 11, 11), # mr r3,r11
            0x4E800020,             # blr
        ]
    )
    fallback_index = len(words)
    words.extend(
        [
            0x7D8802A6,             # displaced mflr r12
            0,                      # b RECV_ENTRY+4
        ]
    )

    def address(index: int) -> int:
        return RECV_STUB + index * 4

    words[empty_branch] = conditional_branch(
        address(empty_branch), address(fallback_index), 12, 2
    )  # beq
    words[socket_branch] = conditional_branch(
        address(socket_branch), address(fallback_index), 4, 2
    )  # bne
    if zero_branch is not None:
        words[zero_branch] = conditional_branch(
            address(zero_branch), address(fallback_index), 4, 1
        )  # ble
    words[count_branch] = conditional_branch(
        address(count_branch), address(count_ready), 4, 1
    )  # ble count_ready
    words[loop_index + 4] = conditional_branch(
        address(loop_index + 4), address(loop_index), 16, 0
    )  # bdnz
    words[fallback_index + 1] = branch(
        address(fallback_index + 1), RECV_ENTRY + 4, link=False
    )
    return b"".join(insn(word) for word in words)


RECV_STUB_BYTES = build_stub()
LEGACY_RECV_STUB_BYTES = build_stub(guard_zero_length=False)
PATCHED_RECV_ENTRY = insn(branch(RECV_ENTRY, RECV_STUB, link=False))


def state(client: Xbdm) -> str:
    value = client.read(RECV_ENTRY, 4)
    if value == ORIGINAL_RECV_ENTRY:
        return "original"
    if value == PATCHED_RECV_ENTRY:
        return "hooked"
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
        print(f"Recv entry: {current}")
        if args.action == "status":
            pending = int.from_bytes(client.read(PENDING_LENGTH, 4), "big")
            print(f"Pending response: {pending} bytes")
            return 0

        if args.action == "apply":
            if current == "hooked":
                cave = client.read(RECV_STUB, len(RECV_STUB_BYTES))
                if cave == RECV_STUB_BYTES:
                    print("Already hooked.")
                    return 0
                if not cave.startswith(LEGACY_RECV_STUB_BYTES):
                    raise RuntimeError("Unexpected live recv code cave")
                # Unpublish before replacing the executable image.
                client.write(RECV_ENTRY, ORIGINAL_RECV_ENTRY)
                try:
                    client.write(RECV_STUB, RECV_STUB_BYTES)
                    if (
                        client.read(RECV_STUB, len(RECV_STUB_BYTES))
                        != RECV_STUB_BYTES
                    ):
                        raise RuntimeError("Recv stub upgrade verification failed")
                    client.write(RECV_ENTRY, PATCHED_RECV_ENTRY)
                except Exception:
                    try:
                        client.write(RECV_ENTRY, ORIGINAL_RECV_ENTRY)
                    except Exception:
                        pass
                    raise
                print("Verified: zero-length-safe receive hook upgraded.")
                return 0
            if current != "original":
                raise RuntimeError("Refusing to overwrite unknown recv entry")
            cave = client.read(RECV_STUB, len(RECV_STUB_BYTES))
            legacy_image = LEGACY_RECV_STUB_BYTES.ljust(
                len(RECV_STUB_BYTES), b"\0"
            )
            if cave not in (
                bytes(len(RECV_STUB_BYTES)),
                RECV_STUB_BYTES,
                legacy_image,
            ):
                raise RuntimeError("Recv code cave is not empty")
            client.write(PENDING_LENGTH, bytes(8))
            client.write(RECV_STUB, RECV_STUB_BYTES)
            if client.read(RECV_STUB, len(RECV_STUB_BYTES)) != RECV_STUB_BYTES:
                raise RuntimeError("Recv stub verification failed")
            client.write(RECV_ENTRY, PATCHED_RECV_ENTRY)
            if state(client) != "hooked":
                client.write(RECV_ENTRY, ORIGINAL_RECV_ENTRY)
                raise RuntimeError("Recv entry hook verification failed")
            print("Verified: plaintext receive queue active.")
            return 0

        if current == "original":
            print("Already restored.")
            return 0
        if current != "hooked":
            raise RuntimeError("Refusing to restore unknown recv entry")
        client.write(RECV_ENTRY, ORIGINAL_RECV_ENTRY)
        client.write(PENDING_LENGTH, bytes(8))
        if state(client) != "original":
            raise RuntimeError("Recv entry restore verification failed")
        print("Verified: original recv entry restored.")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

#!/usr/bin/env python3
"""Capture the PowerPC call stack when updateNetworkInfo is sent."""

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
    write_chunks,
)


SITE = 0x82D69FF8
ORIGINAL = bytes.fromhex("7D8802A6")  # mflr r12
STUB = 0x83C8DE00
JOURNAL = 0x83C8E000
FRAME_COUNT = 16
QOS_OBJECT_OFFSET = 8 + FRAME_COUNT * 8
JOURNAL_SIZE = QOS_OBJECT_OFFSET + 4
LEGACY_JOURNAL = 0x83C8DF00
LEGACY_FRAME_COUNT = 8


def lhz(rt: int, ra: int, displacement: int) -> int:
    return 0xA0000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def build_stub(
    frame_count: int = FRAME_COUNT,
    journal: int = JOURNAL,
    capture_qos_object: bool = True,
) -> bytes:
    words = [
        cmpwi(5, 12),
        0,                           # blt fallback
        lhz(11, 4, 0x02),
        cmpwi(11, 0x7802),
        0,                           # bne fallback
        lhz(11, 4, 0x04),
        cmpwi(11, 20),
        0,                           # bne fallback
        0x7C0802A6,                 # mflr r0
        addis(12, 0, 0x83C9),
        addi(12, 12, journal & 0xFFFF),
        stw(0, 12, 0x00),
        stw(1, 12, 0x04),
        lwz(11, 1, 0x00),           # caller's pre-frame stack pointer
    ]
    for index in range(frame_count):
        words.append(stw(11, 12, 0x08 + index * 8))
        words.append(lwz(10, 11, -8))
        words.append(stw(10, 12, 0x0C + index * 8))
        if capture_qos_object and index == 8:
            # 82F020B8 saves its caller's r31 (the QosManager object) as
            # a 64-bit GPR at incoming_sp-0x10; the low address word is -0xC.
            words.append(lwz(9, 11, -0x0C))
            words.append(stw(9, 12, QOS_OBJECT_OFFSET))
        if index + 1 < frame_count:
            words.append(lwz(11, 11, 0))
    fallback = len(words)
    words.extend(
        [
            int.from_bytes(ORIGINAL, "big"),
            0,
        ]
    )

    def address(index: int) -> int:
        return STUB + index * 4

    words[1] = conditional_branch(
        address(1), address(fallback), 12, 0
    )                               # blt
    for index in (4, 7):
        words[index] = conditional_branch(
            address(index), address(fallback), 4, 2
        )                           # bne
    words[-1] = branch(address(len(words) - 1), SITE + 4, False)
    return b"".join(insn(word) for word in words)


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    lr = int.from_bytes(raw[0:4], "big")
    sp = int.from_bytes(raw[4:8], "big")
    print(f"entry_lr = 0x{lr:08X}  callsite=0x{(lr - 4) & 0xFFFFFFFF:08X}")
    print(f"entry_sp = 0x{sp:08X}")
    last_frame_sp = 0
    for index in range(FRAME_COUNT):
        offset = 8 + index * 8
        frame_sp = int.from_bytes(raw[offset : offset + 4], "big")
        frame_lr = int.from_bytes(raw[offset + 4 : offset + 8], "big")
        last_frame_sp = frame_sp
        callsite = (frame_lr - 4) & 0xFFFFFFFF if frame_lr else 0
        print(
            f"frame[{index}] sp=0x{frame_sp:08X} "
            f"lr=0x{frame_lr:08X} callsite=0x{callsite:08X}"
        )
    qos_object = int.from_bytes(
        raw[QOS_OBJECT_OFFSET : QOS_OBJECT_OFFSET + 4], "big"
    )
    print(f"qos_object_saved_r31 = 0x{qos_object:08X}")
    # Stack memory normally remains intact after the short send call returns.
    # Continue walking it live so the small inline hook need not grow further.
    frame_sp = last_frame_sp
    for index in range(FRAME_COUNT, 20):
        try:
            next_sp = int.from_bytes(client.read(frame_sp, 4), "big")
            if (
                next_sp <= frame_sp
                or next_sp - frame_sp > 0x10000
                or not 0x30000000 <= next_sp < 0x80000000
            ):
                break
            frame_lr = int.from_bytes(client.read(next_sp - 8, 4), "big")
        except Exception:
            break
        callsite = (frame_lr - 4) & 0xFFFFFFFF if frame_lr else 0
        print(
            f"frame[{index}] sp=0x{next_sp:08X} "
            f"lr=0x{frame_lr:08X} callsite=0x{callsite:08X} (live)"
        )
        frame_sp = next_sp


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    args = parser.parse_args()

    stub = build_stub()
    legacy_stub = build_stub(
        LEGACY_FRAME_COUNT,
        LEGACY_JOURNAL,
        capture_qos_object=False,
    )
    previous_extended_stub = build_stub(
        FRAME_COUNT,
        JOURNAL,
        capture_qos_object=False,
    )
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
        print(f"QoS send stack trace site: {state}")
        if args.action in ("status", "read"):
            describe(client)
            return 0
        if args.action == "apply":
            if state not in ("original", "patched"):
                raise RuntimeError("Unexpected DirtySock send entry")
            cave = client.read(STUB, len(stub))
            if (
                cave not in (bytes(len(stub)), stub)
                and not cave.startswith(legacy_stub)
                and not cave.startswith(previous_extended_stub)
            ):
                raise RuntimeError("QoS stack trace code cave is not free")
            client.write(JOURNAL, bytes(JOURNAL_SIZE))
            write_chunks(client, STUB, stub)
            client.write(SITE, patch)
            if client.read(SITE, 4) != patch:
                raise RuntimeError("QoS stack trace verification failed")
            print("Verified: updateNetworkInfo stack capture armed.")
            return 0
        if state == "patched":
            client.write(SITE, ORIGINAL)
        elif state != "original":
            raise RuntimeError("Unexpected DirtySock send entry")
        print("Verified: QoS stack trace restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

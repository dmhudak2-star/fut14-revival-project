#!/usr/bin/env python3
"""Passively trace the UXFunction asynchronous completion used by FUT launch.

The stock FUT screen callback reaches the UX/Lua bridge at ``0x837296FC``
and supplies ``0x83728EF8`` as its completion routine.  This probe records
entries to that completion routine without invoking it, changing its
arguments, or acknowledging any pending frontend operation.
"""

from __future__ import annotations

import argparse
import time

from fifa14_plain_send_hook import (
    Xbdm,
    add,
    addi,
    addis,
    andi_dot,
    branch,
    conditional_branch,
    insn,
    stw,
    verify_module,
    write_chunks,
)


SITE = 0x83728EF8
ORIGINAL_ENTRY = bytes.fromhex("7D8802A6")  # mflr r12

# Verified title padding between the ION unload trace and the provider trace.
# Keep a ring here because this completion is shared by several UX calls and a
# last-record-only journal hides which concrete callback object consumes FUT.
STUB = 0x83C88000
STUB_SIZE = 0x100
JOURNAL = 0x83C88100
RING = 0x83C88200
RING_COUNT = 16
RECORD_SIZE = 0x60
CAVE_END = RING + RING_COUNT * RECORD_SIZE
NEXT_KNOWN_CAVE = 0x83C88900


def lwarx(rt: int, ra: int, rb: int) -> int:
    return 0x7C000028 | (rt << 21) | (ra << 16) | (rb << 11)


def stwcx_dot(rs: int, ra: int, rb: int) -> int:
    return 0x7C00012D | (rs << 21) | (ra << 16) | (rb << 11)


def mulli(rt: int, ra: int, immediate: int) -> int:
    return 0x1C000000 | (rt << 21) | (ra << 16) | (immediate & 0xFFFF)


def lwz(rt: int, ra: int, displacement: int) -> int:
    return 0x80000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def patch_bytes() -> bytes:
    return insn(branch(SITE, STUB, False))


def build_stub() -> bytes:
    words = [
        int.from_bytes(ORIGINAL_ENTRY, "big"),
        addis(11, 0, (JOURNAL + 0x8000) >> 16),
        addi(11, 11, JOURNAL & 0xFFFF),
    ]
    reserve = len(words)
    words.extend(
        (
            lwarx(10, 0, 11),
            addi(10, 10, 1),
            stwcx_dot(10, 0, 11),
            0,
        )
    )
    retry = len(words) - 1
    words[retry] = conditional_branch(
        STUB + retry * 4,
        STUB + reserve * 4,
        4,
        2,
    )
    words.extend(
        (
            andi_dot(9, 10, RING_COUNT - 1),
            mulli(9, 9, RECORD_SIZE),
            addis(8, 0, (RING + 0x8000) >> 16),
            addi(8, 8, RING & 0xFFFF),
            add(9, 8, 9),
            addi(8, 0, 0),
            stw(8, 9, 0x00),
            stw(3, 9, 0x04),
            stw(4, 9, 0x08),
            stw(5, 9, 0x0C),
            stw(6, 9, 0x10),
            stw(7, 9, 0x14),
            stw(12, 9, 0x18),
            lwz(8, 3, 0x00),
            stw(8, 9, 0x1C),
            lwz(8, 8, 0x04),
            stw(8, 9, 0x20),
            lwz(8, 3, 0x04),
            stw(8, 9, 0x24),
            lwz(7, 8, 0x00),
            stw(7, 9, 0x28),
            lwz(7, 8, 0x04),
            stw(7, 9, 0x2C),
            lwz(7, 8, 0x08),
            stw(7, 9, 0x30),
            lwz(7, 8, 0x0C),
            stw(7, 9, 0x34),
            lwz(7, 3, 0x08),
            stw(7, 9, 0x38),
            lwz(7, 3, 0x0C),
            stw(7, 9, 0x3C),
            lwz(8, 8, 0x08),
            stw(8, 9, 0x40),
            lwz(7, 8, 0x00),
            stw(7, 9, 0x44),
            lwz(7, 8, 0x04),
            stw(7, 9, 0x48),
            lwz(7, 8, 0x08),
            stw(7, 9, 0x4C),
            lwz(7, 8, 0x0C),
            stw(7, 9, 0x50),
            lwz(7, 8, 0x10),
            stw(7, 9, 0x54),
            lwz(7, 8, 0x14),
            stw(7, 9, 0x58),
            lwz(7, 8, 0x1C),
            stw(7, 9, 0x5C),
            0x7C0004AC,  # sync
            stw(10, 9, 0x00),
            0,
        )
    )
    tail = len(words) - 1
    words[tail] = branch(STUB + tail * 4, SITE + 4, False)
    raw = b"".join(insn(word) for word in words)
    if len(raw) > STUB_SIZE:
        raise RuntimeError("UX completion stub exceeds its slot")
    return raw.ljust(STUB_SIZE, b"\0")


STUB_BYTES = build_stub()
PATCH = patch_bytes()


def verify_layout() -> None:
    if STUB + STUB_SIZE > JOURNAL:
        raise RuntimeError("UX completion stub overlaps its journal")
    if JOURNAL + 4 > RING:
        raise RuntimeError("UX completion journal overlaps its ring")
    if CAVE_END > NEXT_KNOWN_CAVE:
        raise RuntimeError("UX completion trace overlaps provider trace")


def state(client: Xbdm) -> str:
    current = client.read(SITE, 4)
    if current == ORIGINAL_ENTRY:
        return "original"
    if current == PATCH:
        return "traced"
    return f"unexpected:{current.hex().upper()}"


def arm(client: Xbdm) -> None:
    verify_module(client)
    verify_layout()
    current_state = state(client)
    if current_state not in ("original", "traced"):
        raise RuntimeError(f"refusing unknown completion entry: {current_state}")

    cave = client.read(STUB, STUB_SIZE)
    if cave not in (bytes(STUB_SIZE), STUB_BYTES):
        raise RuntimeError("UX completion trace cave is occupied")

    if current_state == "traced":
        client.write(SITE, ORIGINAL_ENTRY)
        time.sleep(0.02)
    try:
        write_chunks(client, JOURNAL, bytes(CAVE_END - JOURNAL))
        write_chunks(client, STUB, STUB_BYTES)
        if client.read(STUB, STUB_SIZE) != STUB_BYTES:
            raise RuntimeError("UX completion stub verification failed")
        client.write(SITE, PATCH)
        if state(client) != "traced":
            raise RuntimeError("UX completion probe did not publish")
    except Exception:
        try:
            if state(client) == "traced":
                client.write(SITE, ORIGINAL_ENTRY)
        except Exception:
            pass
        raise


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def describe(client: Xbdm) -> None:
    total = u32(client.read(JOURNAL, 4), 0)
    raw = client.read(RING, RING_COUNT * RECORD_SIZE)
    print(f"invocations = {total}")
    records: list[tuple[int, bytes]] = []
    for slot in range(RING_COUNT):
        record = raw[slot * RECORD_SIZE : (slot + 1) * RECORD_SIZE]
        sequence = u32(record, 0)
        if sequence:
            records.append((sequence, record))
    records.sort(key=lambda item: item[0])
    if not records:
        print("No committed UX asynchronous completion.")
        return
    if total > RING_COUNT:
        print(f"WARNING: only the newest {RING_COUNT} records remain.")
    for sequence, record in records:
        r3, r4, r5, r6, r7, lr = (
            u32(record, offset) for offset in range(0x04, 0x1C, 4)
        )
        vtable = u32(record, 0x1C)
        target = u32(record, 0x20)
        context = u32(record, 0x24)
        context_words = tuple(u32(record, offset) for offset in range(0x28, 0x38, 4))
        wrapper_word2 = u32(record, 0x38)
        wrapper_word3 = u32(record, 0x3C)
        handler_object = u32(record, 0x40)
        handler_words = tuple(u32(record, offset) for offset in range(0x44, 0x60, 4))
        print(
            f"{sequence:8d} LR=0x{lr:08X} callback=0x{r3:08X} "
            f"vtable/target=0x{vtable:08X}/0x{target:08X} "
            f"r4=0x{r4:08X} r5=0x{r5:08X} r6=0x{r6:08X} "
            f"r7=0x{r7:08X} context=0x{context:08X}"
        )
        print(
            "             context[0/+4/+8/+C]="
            + "/".join(f"0x{value:08X}" for value in context_words)
            + f" wrapper+8/+C=0x{wrapper_word2:08X}/0x{wrapper_word3:08X}"
        )
        print(
            f"             handler_object=0x{handler_object:08X} "
            "words[0/+4/+8/+C/+10/+14/+1C]="
            + "/".join(f"0x{value:08X}" for value in handler_words)
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "read", "restore"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        verify_layout()
        current_state = state(client)
        print(f"UX asynchronous completion trace: {current_state}")
        if args.action == "status":
            return 0
        if args.action == "read":
            describe(client)
            return 0
        if args.action == "restore":
            if current_state == "traced":
                client.write(SITE, ORIGINAL_ENTRY)
            elif current_state != "original":
                raise RuntimeError(f"unexpected completion entry: {current_state}")
            if state(client) != "original":
                raise RuntimeError("UX completion entry did not restore")
            print("Verified: UX asynchronous completion entry restored.")
            return 0

        arm(client)
        print("Verified: passive UX asynchronous completion trace armed.")
        print("No completion, event, result or frontend route was synthesized.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

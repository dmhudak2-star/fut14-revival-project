#!/usr/bin/env python3
"""Trace the live B0C receiver callback without debugger breakpoints.

The callback at 0x8251A560 is the only non-noop vslot+4 receiver observed in
the 0x82EB50D0 fan-out.  This probe records its entry state in a four-record
ring and never calls game code or changes callback arguments.
"""

from __future__ import annotations

import argparse

from fifa14_plain_send_hook import (
    Xbdm,
    add,
    addi,
    addis,
    branch,
    insn,
    lwz,
    rlwinm,
    stw,
    verify_module,
)


SITE = 0x8251A560
ORIGINAL = bytes.fromhex("7D8802A6")  # mflr r12

# This gap follows the pending-pump delay control (ending at CE88) and ends
# before the D000 PreAuth flow allocation.  Exclusive ends are used below.
STUB = 0x83C8CEA0
STUB_SLOT_END = 0x83C8CF20
COUNTER = 0x83C8CF20
RING = 0x83C8CF40
RECORD_SIZE = 0x20
RECORD_COUNT = 4
RING_SIZE = RECORD_SIZE * RECORD_COUNT
JOURNAL_END = RING + RING_SIZE

KNOWN_NEIGHBOURS = (
    ("ProtoSSL receive journal", 0x83C8CD00, 0x83C8CD80),
    ("pending-pump delay stub/control", 0x83C8CE00, 0x83C8CE88),
    ("PreAuth flow", 0x83C8D000, 0x83C8D400),
)

SYNC = 0x7C0004AC


def build_stub() -> bytes:
    """Build a CR-preserving entry logger.

    The displaced ``mflr r12`` runs first, so r12 both retains its original
    value and supplies the entry LR.  Only volatile scratch registers
    r0/r10/r11 are subsequently clobbered; r3 and the callback arguments are
    untouched.  The global counter is published after the completed record.
    """
    words = [
        int.from_bytes(ORIGINAL, "big"),
        addis(11, 0, 0x83C9),
        addi(11, 11, -0x30C0),      # r11 = RING
        lwz(10, 11, -0x20),         # old invocation count
        rlwinm(0, 10, 0, 30, 31),  # slot = count & 3, without changing CR
        rlwinm(0, 0, 5, 0, 26),    # slot *= RECORD_SIZE
        add(11, 11, 0),             # selected record
        addi(10, 10, 1),            # sequence / new invocation count
        stw(10, 11, 0x00),
        stw(3, 11, 0x04),
        stw(12, 11, 0x08),          # entry LR
        lwz(0, 3, -0x48),
        stw(0, 11, 0x0C),
        lwz(0, 3, -0x44),
        stw(0, 11, 0x10),
        lwz(0, 3, 0x0974),
        stw(0, 11, 0x14),
        SYNC,
        addis(11, 0, 0x83C9),
        stw(10, 11, -0x30E0),      # publish COUNTER last
        0,
    ]
    words[-1] = branch(
        STUB + (len(words) - 1) * 4,
        SITE + 4,
        False,
    )
    return b"".join(insn(word) for word in words)


def overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def validate_layout(stub: bytes) -> None:
    stub_region = (STUB, STUB + len(stub))
    slot_region = (STUB, STUB_SLOT_END)
    journal_region = (COUNTER, JOURNAL_END)
    if stub_region[1] > slot_region[1]:
        raise AssertionError(
            f"stub ends at 0x{stub_region[1]:08X}, beyond its slot"
        )
    if RECORD_COUNT & (RECORD_COUNT - 1):
        raise AssertionError("RECORD_COUNT must be a power of two")
    if overlaps(slot_region, journal_region):
        raise AssertionError("stub slot overlaps its journal")
    for name, start, end in KNOWN_NEIGHBOURS:
        reserved = (start, end)
        if overlaps(slot_region, reserved) or overlaps(journal_region, reserved):
            raise AssertionError(f"B0C probe overlaps {name}")


def write_chunks(client: Xbdm, address: int, data: bytes) -> None:
    for offset in range(0, len(data), 0x80):
        client.write(address + offset, data[offset : offset + 0x80])


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def read_ring(client: Xbdm) -> tuple[int, bytes, bool]:
    """Obtain a best-effort coherent snapshot while the title may be running."""
    raw = bytes(RING_SIZE)
    before = after = 0
    for _ in range(3):
        before = int.from_bytes(client.read(COUNTER, 4), "big")
        raw = client.read(RING, RING_SIZE)
        after = int.from_bytes(client.read(COUNTER, 4), "big")
        if before == after:
            return after, raw, True
    return after, raw, False


def describe(client: Xbdm) -> None:
    count, raw, coherent = read_ring(client)
    print(f"invocation_count = {count}")
    if not coherent:
        print("snapshot         = changed while reading")
    first = max(1, count - RECORD_COUNT + 1)
    for expected in range(first, count + 1):
        slot = (expected - 1) & (RECORD_COUNT - 1)
        offset = slot * RECORD_SIZE
        sequence = u32(raw, offset)
        receiver = u32(raw, offset + 0x04)
        lr = u32(raw, offset + 0x08)
        suffix = "" if sequence == expected else " (overwritten/torn)"
        print(
            f"seq={sequence:10} slot={slot} "
            f"r3=0x{receiver:08X} lr=0x{lr:08X} "
            f"callsite=0x{(lr - 4) & 0xFFFFFFFF:08X} "
            f"owner=0x{(receiver - 0x48) & 0xFFFFFFFF:08X} "
            f"[r3-48]=0x{u32(raw, offset + 0x0C):08X} "
            f"[r3-44]=0x{u32(raw, offset + 0x10):08X} "
            f"[r3+974]=0x{u32(raw, offset + 0x14):08X}"
            f"{suffix}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "read", "restore"))
    args = parser.parse_args()

    stub = build_stub()
    validate_layout(stub)
    slot_size = STUB_SLOT_END - STUB
    stub_image = stub.ljust(slot_size, b"\0")
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
        print(f"B0C receiver ring probe: {state}")

        if args.action in ("status", "read"):
            describe(client)
            return 0

        if state not in ("original", "patched"):
            raise RuntimeError("Unexpected B0C receiver entry instruction")

        if args.action == "restore":
            if state == "patched":
                client.write(SITE, ORIGINAL)
            if client.read(SITE, 4) != ORIGINAL:
                raise RuntimeError("B0C receiver restore verification failed")
            print("Verified: original B0C receiver entry restored.")
            return 0

        cave = client.read(STUB, slot_size)
        if cave not in (bytes(slot_size), stub_image):
            raise RuntimeError("B0C receiver probe code cave is occupied")
        if state == "patched":
            if cave != stub_image:
                raise RuntimeError("Live B0C receiver stub does not match")
            print("Already armed; ring preserved.")
            return 0

        try:
            write_chunks(client, COUNTER, bytes(JOURNAL_END - COUNTER))
            write_chunks(client, STUB, stub_image)
            if client.read(STUB, slot_size) != stub_image:
                raise RuntimeError("B0C receiver stub verification failed")
            client.write(SITE, patch)
            if client.read(SITE, 4) != patch:
                raise RuntimeError("B0C receiver patch verification failed")
        except Exception:
            try:
                if client.read(SITE, 4) == patch:
                    client.write(SITE, ORIGINAL)
            except Exception:
                pass
            raise

        print("Verified: breakpoint-free B0C receiver ring armed.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

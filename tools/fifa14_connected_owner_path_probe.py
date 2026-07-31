#!/usr/bin/env python3
"""Trace the connected-owner entry and its gated observer call.

Both hooks are breakpoint-free and observational.  The callsite hook replaces
the original ``bl`` with ``bl CALL_STUB``; the stub then tail-branches to the
original callee, preserving the LR value that the callee must return through.
"""

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


ENTRY_SITE = 0x825B3E98
ENTRY_ORIGINAL = bytes.fromhex("7D8802A6")  # mflr r12
ENTRY_STUB = 0x83C8D700
ENTRY_SLOT_END = 0x83C8D740

CALL_SITE = 0x825B3F38
CALL_TARGET = 0x8250CEB0
CALL_ORIGINAL = bytes.fromhex("4BF58F79")  # bl 0x8250CEB0
CALL_STUB = 0x83C8D740
CALL_SLOT_END = 0x83C8D780

JOURNAL = 0x83C8D780
JOURNAL_SIZE = 0x20

# Exclusive ranges belonging to probes/patches that may be live alongside
# this one.  D700-D79F is the verified gap between the receive-path stubs and
# their D800 journal.
KNOWN_NEIGHBOURS = (
    ("B0C receiver ring", 0x83C8CEA0, 0x83C8CFC0),
    ("PreAuth flow", 0x83C8D000, 0x83C8D400),
    ("Blaze receive-path stubs", 0x83C8D400, 0x83C8D700),
    ("Blaze receive-path journal", 0x83C8D800, 0x83C8D900),
    ("PreAuth/Ping callback probe", 0x83C8D900, 0x83C8D9C0),
)

SYNC = 0x7C0004AC


def build_entry_stub() -> bytes:
    """Record count/r3/LR while replaying the entry mflr first."""
    words = [
        int.from_bytes(ENTRY_ORIGINAL, "big"),
        addis(11, 0, 0x83C9),
        addi(11, 11, -0x2880),      # r11 = JOURNAL
        lwz(10, 11, 0x00),
        addi(10, 10, 1),
        stw(3, 11, 0x04),
        stw(12, 11, 0x08),          # entry LR from displaced mflr
        SYNC,
        stw(10, 11, 0x00),         # publish entry count last
        0,
    ]
    words[-1] = branch(
        ENTRY_STUB + (len(words) - 1) * 4,
        ENTRY_SITE + 4,
        False,
    )
    return b"".join(insn(word) for word in words)


def build_call_stub() -> bytes:
    """Count a reached callsite, then enter its original target with LR intact."""
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x2880),      # r12 = JOURNAL
        lwz(11, 12, 0x0C),
        addi(11, 11, 1),
        stw(11, 12, 0x0C),          # observer-call reach count
        0,
    ]
    words[-1] = branch(
        CALL_STUB + (len(words) - 1) * 4,
        CALL_TARGET,
        False,                      # tail branch preserves CALL_SITE+4 in LR
    )
    return b"".join(insn(word) for word in words)


def overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def validate_layout(entry_stub: bytes, call_stub: bytes) -> None:
    regions = (
        ("entry stub slot", (ENTRY_STUB, ENTRY_SLOT_END)),
        ("call stub slot", (CALL_STUB, CALL_SLOT_END)),
        ("journal", (JOURNAL, JOURNAL + JOURNAL_SIZE)),
    )
    if ENTRY_STUB + len(entry_stub) > ENTRY_SLOT_END:
        raise AssertionError("connected-owner entry stub exceeds its slot")
    if CALL_STUB + len(call_stub) > CALL_SLOT_END:
        raise AssertionError("connected-owner call stub exceeds its slot")
    for index, (name, region) in enumerate(regions):
        for other_name, other_region in regions[index + 1 :]:
            if overlaps(region, other_region):
                raise AssertionError(f"{name} overlaps {other_name}")
        for reserved_name, start, end in KNOWN_NEIGHBOURS:
            if overlaps(region, (start, end)):
                raise AssertionError(f"{name} overlaps {reserved_name}")


def write_chunks(client: Xbdm, address: int, data: bytes) -> None:
    for offset in range(0, len(data), 0x80):
        client.write(address + offset, data[offset : offset + 0x80])


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    lr = u32(raw, 0x08)
    print(f"entry_count      = {u32(raw, 0x00)}")
    print(f"last_owner_r3    = 0x{u32(raw, 0x04):08X}")
    print(f"last_entry_lr    = 0x{lr:08X}")
    print(
        f"last_callsite    = 0x{(lr - 4) & 0xFFFFFFFF:08X}"
        if lr
        else "last_callsite    = 0x00000000"
    )
    print(f"observer_reached = {u32(raw, 0x0C)}")


def state(current: bytes, original: bytes, patch: bytes) -> str:
    if current == original:
        return "original"
    if current == patch:
        return "patched"
    return f"unexpected:{current.hex().upper()}"


def rollback_sites(
    client: Xbdm,
    entry_patch: bytes,
    call_patch: bytes,
) -> None:
    for site, original, patch in (
        (ENTRY_SITE, ENTRY_ORIGINAL, entry_patch),
        (CALL_SITE, CALL_ORIGINAL, call_patch),
    ):
        try:
            if client.read(site, 4) == patch:
                client.write(site, original)
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "read", "restore"))
    args = parser.parse_args()

    entry_stub = build_entry_stub()
    call_stub = build_call_stub()
    validate_layout(entry_stub, call_stub)
    entry_image = entry_stub.ljust(ENTRY_SLOT_END - ENTRY_STUB, b"\0")
    call_image = call_stub.ljust(CALL_SLOT_END - CALL_STUB, b"\0")
    entry_patch = insn(branch(ENTRY_SITE, ENTRY_STUB, False))
    call_patch = insn(branch(CALL_SITE, CALL_STUB, True))

    client = Xbdm(args.host)
    try:
        verify_module(client)
        entry_current = client.read(ENTRY_SITE, 4)
        call_current = client.read(CALL_SITE, 4)
        entry_state = state(entry_current, ENTRY_ORIGINAL, entry_patch)
        call_state = state(call_current, CALL_ORIGINAL, call_patch)
        print(
            "Connected-owner path probe: "
            f"entry={entry_state} observer-call={call_state}"
        )

        if args.action in ("status", "read"):
            describe(client)
            return 0

        if entry_state not in ("original", "patched"):
            raise RuntimeError("Unexpected connected-owner entry instruction")
        if call_state not in ("original", "patched"):
            raise RuntimeError("Unexpected observer callsite instruction")

        if args.action == "restore":
            if entry_state == "patched":
                client.write(ENTRY_SITE, ENTRY_ORIGINAL)
            if call_state == "patched":
                client.write(CALL_SITE, CALL_ORIGINAL)
            if (
                client.read(ENTRY_SITE, 4) != ENTRY_ORIGINAL
                or client.read(CALL_SITE, 4) != CALL_ORIGINAL
            ):
                raise RuntimeError("Connected-owner probe restore failed")
            print("Verified: connected-owner path restored.")
            return 0

        entry_cave = client.read(ENTRY_STUB, len(entry_image))
        call_cave = client.read(CALL_STUB, len(call_image))
        if entry_cave not in (bytes(len(entry_image)), entry_image):
            raise RuntimeError("Connected-owner entry cave is occupied")
        if call_cave not in (bytes(len(call_image)), call_image):
            raise RuntimeError("Connected-owner call cave is occupied")
        if entry_state == "patched" and entry_cave != entry_image:
            raise RuntimeError("Live connected-owner entry stub does not match")
        if call_state == "patched" and call_cave != call_image:
            raise RuntimeError("Live connected-owner call stub does not match")

        if entry_state == call_state == "patched":
            print("Already armed; journal preserved.")
            return 0

        # Normalize a partial prior installation before replacing either cave.
        if entry_state == "patched":
            client.write(ENTRY_SITE, ENTRY_ORIGINAL)
        if call_state == "patched":
            client.write(CALL_SITE, CALL_ORIGINAL)
        if (
            client.read(ENTRY_SITE, 4) != ENTRY_ORIGINAL
            or client.read(CALL_SITE, 4) != CALL_ORIGINAL
        ):
            raise RuntimeError("Could not unpublish partial connected-owner hooks")

        try:
            client.write(JOURNAL, bytes(JOURNAL_SIZE))
            write_chunks(client, ENTRY_STUB, entry_image)
            write_chunks(client, CALL_STUB, call_image)
            if (
                client.read(ENTRY_STUB, len(entry_image)) != entry_image
                or client.read(CALL_STUB, len(call_image)) != call_image
            ):
                raise RuntimeError("Connected-owner stub verification failed")
            client.write(ENTRY_SITE, entry_patch)
            client.write(CALL_SITE, call_patch)
            if (
                client.read(ENTRY_SITE, 4) != entry_patch
                or client.read(CALL_SITE, 4) != call_patch
            ):
                raise RuntimeError("Connected-owner patch verification failed")
        except Exception:
            rollback_sites(client, entry_patch, call_patch)
            raise

        print("Verified: breakpoint-free connected-owner path probe armed.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

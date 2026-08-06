#!/usr/bin/env python3
"""Passively trace FIFA 14's retail DLC/XEX loader lifecycle.

The retail executable exposes two native routes into the DLC loader:

* the DLC manager receives ``INITIALIZATION_COMPLETE`` and may load the first
  configured ``dll`` item;
* the frontend action registry exposes ``LoadDLL(dll=...)``.

Both routes converge on ``0x823E8A88``, whose r3 argument is the final path
passed to the title's XEX-loading wrapper.  This diagnostic only journals the
retail calls and their arguments.  It does not call a loader, publish a DLC
event, alter an action result, or synthesize a frontend transition.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

from fifa14_plain_send_hook import (
    Xbdm,
    add,
    addi,
    addis,
    andi_dot,
    branch,
    cmplwi,
    conditional_branch,
    insn,
    lbz,
    lwz,
    or_register,
    rlwinm,
    stb,
    stw,
    verify_module,
    write_chunks,
)


# This page was historically used by the standalone plaintext send logger.
# The early local-server watcher does not arm that logger, and checks every
# slot before publishing this trace.  The next known diagnostic starts at
# 0x83C8FB00.
STUB_BASE = 0x83C8F000
STUB_STRIDE = 0x100
JOURNAL = 0x83C8F400
RECORD_SIZE = 0xC0
PATH_OFFSET = 0x30
PATH_CAPACITY = RECORD_SIZE - PATH_OFFSET
EVENT_RING = 0x83C8F700
EVENT_RECORD_SIZE = 0x10
EVENT_RING_COUNT = 8
CAVE_END = EVENT_RING + EVENT_RING_COUNT * EVENT_RECORD_SIZE
PAGE_END = 0x83C8FB00


@dataclass(frozen=True)
class Probe:
    event_id: int
    name: str
    site: int
    original: bytes
    copy_r3_path: bool = False
    event_ring: bool = False

    @property
    def stub(self) -> int:
        return STUB_BASE + (self.event_id - 1) * STUB_STRIDE

    @property
    def record(self) -> int:
        return JOURNAL + (self.event_id - 1) * RECORD_SIZE


PROBES = (
    Probe(
        1,
        "dlc_event_callback",
        0x823E9038,
        bytes.fromhex("7D8802A6"),
        event_ring=True,
    ),
    Probe(2, "dlc_automatic_item", 0x823E8D90, bytes.fromhex("7D8802A6")),
    Probe(3, "load_dll_action", 0x823E8E38, bytes.fromhex("7D8802A6")),
    Probe(
        4,
        "load_image_path",
        0x823E8A88,
        bytes.fromhex("7D8802A6"),
        copy_r3_path=True,
    ),
)


def patch_for(probe: Probe) -> bytes:
    return insn(branch(probe.site, probe.stub, False))


def build_stub(probe: Probe) -> bytes:
    # Every selected site displaces the retail `mflr r12` prologue.  Execute it
    # first so the function observes the original caller after the logger.
    words: list[int] = [int.from_bytes(probe.original, "big")]
    words.extend(
        (
            addis(11, 0, (probe.record + 0x8000) >> 16),
            addi(11, 11, probe.record & 0xFFFF),
            lwz(10, 11, 0x00),
            addi(10, 10, 1),
            stw(10, 11, 0x00),
            stw(3, 11, 0x04),
            stw(4, 11, 0x08),
            stw(5, 11, 0x0C),
            stw(6, 11, 0x10),
            stw(7, 11, 0x14),
            stw(8, 11, 0x18),
            stw(12, 11, 0x1C),
        )
    )

    if probe.event_ring:
        # Keep the last eight DLC event IDs.  The monotonically increasing hit
        # count stored in each slot lets the reader restore chronological order.
        words.extend(
            (
                andi_dot(9, 10, EVENT_RING_COUNT - 1),
                rlwinm(9, 9, 4, 0, 27),  # slwi r9,r9,4
                addis(11, 0, (EVENT_RING + 0x8000) >> 16),
                addi(11, 11, EVENT_RING & 0xFFFF),
                add(9, 9, 11),
                stw(10, 9, 0x00),
                stw(4, 9, 0x04),
                stw(3, 9, 0x08),
                stw(12, 9, 0x0C),
            )
        )

    if probe.copy_r3_path:
        words.extend(
            (
                or_register(9, 3, 3),
                cmplwi(9, 0),
                0,  # beq copy_done
                addi(0, 0, PATH_CAPACITY - 1),
                0x7C0903A6,  # mtctr r0
                addis(10, 0, (probe.record + PATH_OFFSET + 0x8000) >> 16),
                addi(10, 10, (probe.record + PATH_OFFSET) & 0xFFFF),
            )
        )
        null_branch = len(words) - 5
        copy_loop = len(words)
        words.extend(
            (
                lbz(0, 9, 0),
                stb(0, 10, 0),
                cmplwi(0, 0),
                0,  # beq copy_done
                addi(9, 9, 1),
                addi(10, 10, 1),
                0,  # bdnz copy_loop
            )
        )
        zero_branch = copy_loop + 3
        loop_branch = copy_loop + 6
        copy_done = len(words)
        words[null_branch] = conditional_branch(
            probe.stub + null_branch * 4,
            probe.stub + copy_done * 4,
            12,
            2,
        )
        words[zero_branch] = conditional_branch(
            probe.stub + zero_branch * 4,
            probe.stub + copy_done * 4,
            12,
            2,
        )
        words[loop_branch] = conditional_branch(
            probe.stub + loop_branch * 4,
            probe.stub + copy_loop * 4,
            16,
            0,
        )

    tail = probe.stub + len(words) * 4
    words.append(branch(tail, probe.site + 4, False))
    image = b"".join(insn(word) for word in words)
    if len(image) > STUB_STRIDE:
        raise RuntimeError(f"{probe.name} stub exceeds its slot")
    return image.ljust(STUB_STRIDE, b"\0")


def verify_layout() -> None:
    if STUB_BASE + len(PROBES) * STUB_STRIDE != JOURNAL:
        raise RuntimeError("DLC-loader stubs do not end at the journal")
    if JOURNAL + len(PROBES) * RECORD_SIZE != EVENT_RING:
        raise RuntimeError("DLC-loader records do not end at the event ring")
    if CAVE_END > PAGE_END:
        raise RuntimeError("DLC-loader trace exceeds its diagnostic page")


def state_for(client: Xbdm, probe: Probe) -> str:
    current = client.read(probe.site, 4)
    if current == probe.original:
        return "original"
    if current == patch_for(probe):
        return "traced"
    return f"unexpected:{current.hex().upper()}"


def arm(client: Xbdm) -> None:
    verify_module(client)
    verify_layout()
    states = [state_for(client, probe) for probe in PROBES]
    unexpected = [
        f"0x{probe.site:08X}={state}"
        for probe, state in zip(PROBES, states)
        if state.startswith("unexpected:")
    ]
    if unexpected:
        raise RuntimeError("refusing unknown DLC-loader site(s): " + ", ".join(unexpected))

    for probe, state in zip(PROBES, states):
        existing = client.read(probe.stub, STUB_STRIDE)
        expected = build_stub(probe)
        if state == "traced" and existing != expected:
            raise RuntimeError(f"owned trace image changed at 0x{probe.stub:08X}")
        if state == "original" and existing not in (bytes(STUB_STRIDE), expected):
            raise RuntimeError(f"DLC-loader cave 0x{probe.stub:08X} is occupied")

    if "traced" in states:
        for probe, state in zip(PROBES, states):
            if state == "traced":
                client.write(probe.site, probe.original)
        time.sleep(0.02)

    try:
        write_chunks(client, JOURNAL, bytes(CAVE_END - JOURNAL))
        for probe in PROBES:
            write_chunks(client, probe.stub, build_stub(probe))
        for probe in PROBES:
            client.write(probe.site, patch_for(probe))
        if any(state_for(client, probe) != "traced" for probe in PROBES):
            raise RuntimeError("one or more DLC-loader probes did not publish")
    except Exception:
        for probe in PROBES:
            try:
                if state_for(client, probe) == "traced":
                    client.write(probe.site, probe.original)
            except Exception:
                pass
        raise


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, CAVE_END - JOURNAL)
    for probe in PROBES:
        offset = (probe.event_id - 1) * RECORD_SIZE
        record = raw[offset : offset + RECORD_SIZE]
        count = u32(record, 0x00)
        print(
            f"{probe.name:22s} hits={count:4d} "
            f"r3=0x{u32(record, 0x04):08X} "
            f"r4=0x{u32(record, 0x08):08X} "
            f"r5=0x{u32(record, 0x0C):08X} "
            f"r6=0x{u32(record, 0x10):08X} "
            f"caller=0x{(u32(record, 0x1C) - 4) & 0xFFFFFFFF:08X}"
        )
        if probe.copy_r3_path:
            path = record[PATH_OFFSET:].split(b"\0", 1)[0]
            print(f"  final path={path.decode('ascii', 'backslashreplace')!r}")

    event_total = u32(raw, 0x00)
    events: list[tuple[int, int, int, int]] = []
    ring_offset = EVENT_RING - JOURNAL
    for index in range(EVENT_RING_COUNT):
        slot = raw[
            ring_offset + index * EVENT_RECORD_SIZE :
            ring_offset + (index + 1) * EVENT_RECORD_SIZE
        ]
        sequence = u32(slot, 0x00)
        if sequence:
            events.append(
                (sequence, u32(slot, 0x04), u32(slot, 0x08), u32(slot, 0x0C))
            )
    if events:
        print(f"DLC event history (last {min(event_total, EVENT_RING_COUNT)}):")
        for sequence, event, owner, caller in sorted(events):
            print(
                f"  #{sequence:03d} event=0x{event:08X} "
                f"owner=0x{owner:08X} caller=0x{(caller - 4) & 0xFFFFFFFF:08X}"
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
        states = [state_for(client, probe) for probe in PROBES]
        print(
            "DLC-loader trace: "
            f"{states.count('traced')} traced, "
            f"{states.count('original')} original, "
            f"{sum(state.startswith('unexpected:') for state in states)} unexpected"
        )
        if args.action == "status":
            for probe, state in zip(PROBES, states):
                print(f"  0x{probe.site:08X} {probe.name}: {state}")
            return 0
        if args.action == "read":
            describe(client)
            return 0
        if args.action == "restore":
            for probe, state in zip(PROBES, states):
                if state == "traced":
                    client.write(probe.site, probe.original)
                elif state != "original":
                    raise RuntimeError(f"unexpected entry at 0x{probe.site:08X}: {state}")
            if any(state_for(client, probe) != "original" for probe in PROBES):
                raise RuntimeError("one or more DLC-loader entries did not restore")
            print("Verified: DLC-loader entries restored.")
            return 0

        arm(client)
        print("Verified: passive retail DLC/LoadDLL trace armed.")
        print("No DLL, DLC event, action result or frontend route was synthesized.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

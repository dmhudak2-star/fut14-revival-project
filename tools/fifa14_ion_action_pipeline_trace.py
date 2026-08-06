#!/usr/bin/env python3
"""Passively trace ION action processing after a screen-unload callback.

The screen provider callback ultimately publishes into ActionScript through
``UIFPublishToObject``.  A healthy navigation transition should then reach
the native ION action controller's ProcessAction, ChangeState and
PreScreenComplete stages.  These entry trampolines only record their retail
arguments and selected controller fields, execute the displaced ``mflr`` and
resume.  They do not acknowledge a screen, change a state or emit an event.
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
    conditional_branch,
    insn,
    lwz,
    rlwinm,
    stw,
    verify_module,
    write_chunks,
)


ORIGINAL_ENTRY = bytes.fromhex("7D8802A6")  # mflr r12

# 0x83C8A000..0x83C8A9FF is free in the active trace set.  The login-state
# diagnostics begin at 0x83C8AA00.
STUB_BASE = 0x83C8A000
STUB_STRIDE = 0x100
JOURNAL = 0x83C8A300
RING = 0x83C8A400
RING_COUNT = 16
RECORD_SIZE = 0x40
CAVE_END = RING + RING_COUNT * RECORD_SIZE
NEXT_KNOWN_CAVE = 0x83C8AA00


@dataclass(frozen=True)
class Probe:
    event_id: int
    name: str
    site: int
    layout: str

    @property
    def stub(self) -> int:
        return STUB_BASE + (self.event_id - 1) * STUB_STRIDE


PROBES = (
    Probe(1, "ProcessAction", 0x82D62138, "controller"),
    Probe(2, "ChangeState", 0x82D61928, "controller"),
    Probe(3, "PreScreenComplete", 0x82D62398, "completion"),
)


def lwarx(rt: int, ra: int, rb: int) -> int:
    return 0x7C000028 | (rt << 21) | (ra << 16) | (rb << 11)


def stwcx_dot(rs: int, ra: int, rb: int) -> int:
    return 0x7C00012D | (rs << 21) | (ra << 16) | (rb << 11)


def patch_for(probe: Probe) -> bytes:
    return insn(branch(probe.site, probe.stub, False))


def state_for(client: Xbdm, probe: Probe) -> str:
    current = client.read(probe.site, 4)
    if current == ORIGINAL_ENTRY:
        return "original"
    if current == patch_for(probe):
        return "traced"
    return f"unexpected:{current.hex().upper()}"


def build_stub(probe: Probe) -> bytes:
    words = [int.from_bytes(ORIGINAL_ENTRY, "big")]
    words.extend(
        (
            addis(11, 0, (JOURNAL + 0x8000) >> 16),
            addi(11, 11, JOURNAL & 0xFFFF),
        )
    )
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
        probe.stub + retry * 4,
        probe.stub + reserve * 4,
        4,
        2,
    )

    words.extend(
        (
            andi_dot(9, 10, RING_COUNT - 1),
            rlwinm(9, 9, 6, 0, 25),
            addis(8, 0, (RING + 0x8000) >> 16),
            addi(8, 8, RING & 0xFFFF),
            add(9, 8, 9),
            addi(8, 0, 0),
            stw(8, 9, 0x00),
            addi(8, 0, probe.event_id),
            stw(8, 9, 0x04),
            stw(3, 9, 0x08),
            stw(4, 9, 0x0C),
            stw(5, 9, 0x10),
            stw(6, 9, 0x14),
            stw(7, 9, 0x18),
            stw(12, 9, 0x1C),
        )
    )

    if probe.layout == "controller":
        for source, destination in (
            (0x10, 0x20),
            (0x38, 0x24),
            (0x40, 0x28),
            (0x48, 0x2C),
        ):
            words.extend((lwz(8, 3, source), stw(8, 9, destination)))
    elif probe.layout == "completion":
        for source, destination in (
            (-0x08, 0x20),
            (0x04, 0x24),
            (0x08, 0x28),
            (0x3C, 0x2C),
        ):
            words.extend((lwz(8, 3, source), stw(8, 9, destination)))
    else:
        raise ValueError(probe.layout)

    words.extend(
        (
            0x7C0004AC,  # sync
            stw(10, 9, 0x00),
            0,
        )
    )
    tail = len(words) - 1
    words[tail] = branch(probe.stub + tail * 4, probe.site + 4, False)
    raw = b"".join(insn(word) for word in words)
    if len(raw) > STUB_STRIDE:
        raise RuntimeError(f"{probe.name} stub exceeds its slot")
    return raw.ljust(STUB_STRIDE, b"\0")


def verify_layout() -> None:
    if STUB_BASE + len(PROBES) * STUB_STRIDE != JOURNAL:
        raise RuntimeError("ION action stub allocation is inconsistent")
    if CAVE_END > NEXT_KNOWN_CAVE:
        raise RuntimeError("ION action trace overlaps login-state diagnostics")


def arm(client: Xbdm) -> None:
    verify_module(client)
    verify_layout()
    states = [state_for(client, probe) for probe in PROBES]
    unexpected = [
        f"0x{probe.site:08X}={state}"
        for probe, state in zip(PROBES, states)
        if state not in ("original", "traced")
    ]
    if unexpected:
        raise RuntimeError("Refusing unknown entry site(s): " + ", ".join(unexpected))

    images = [build_stub(probe) for probe in PROBES]
    for probe, image in zip(PROBES, images):
        current = client.read(probe.stub, STUB_STRIDE)
        if current not in (bytes(STUB_STRIDE), image):
            raise RuntimeError(f"ION action cave 0x{probe.stub:08X} is occupied")

    if "traced" in states:
        for probe, state in zip(PROBES, states):
            if state == "traced":
                client.write(probe.site, ORIGINAL_ENTRY)
        time.sleep(0.02)

    try:
        write_chunks(client, JOURNAL, bytes(CAVE_END - JOURNAL))
        for probe, image in zip(PROBES, images):
            write_chunks(client, probe.stub, image)
            if client.read(probe.stub, STUB_STRIDE) != image:
                raise RuntimeError(f"stub verification failed at 0x{probe.stub:08X}")
        for probe in PROBES:
            client.write(probe.site, patch_for(probe))
        if any(state_for(client, probe) != "traced" for probe in PROBES):
            raise RuntimeError("one or more ION action probes did not publish")
    except Exception:
        for probe in PROBES:
            try:
                if state_for(client, probe) == "traced":
                    client.write(probe.site, ORIGINAL_ENTRY)
            except Exception:
                pass
        raise


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def describe(client: Xbdm) -> None:
    counter = u32(client.read(JOURNAL, 4), 0)
    raw = client.read(RING, RING_COUNT * RECORD_SIZE)
    records: list[tuple[int, bytes]] = []
    for slot in range(RING_COUNT):
        record = raw[slot * RECORD_SIZE : (slot + 1) * RECORD_SIZE]
        sequence = u32(record, 0)
        if sequence:
            records.append((sequence, record))
    records.sort(key=lambda item: item[0])
    print(f"reserved_sequence = {counter}")
    if not records:
        print("No committed ION action-pipeline event.")
        return
    if counter > RING_COUNT:
        print(f"WARNING: only the newest {RING_COUNT} records remain.")

    by_id = {probe.event_id: probe for probe in PROBES}
    for sequence, record in records:
        probe = by_id.get(u32(record, 0x04))
        name = probe.name if probe else f"unknown-{u32(record, 0x04)}"
        r3, r4, r5, r6, r7 = (u32(record, offset) for offset in range(0x08, 0x1C, 4))
        lr = u32(record, 0x1C)
        snap = [u32(record, offset) for offset in range(0x20, 0x30, 4)]
        print(
            f"{sequence:8d}  {name:18s} LR=0x{lr:08X} "
            f"r3=0x{r3:08X} r4=0x{r4:08X} r5=0x{r5:08X} "
            f"r6=0x{r6:08X} r7=0x{r7:08X}"
        )
        if probe and probe.layout == "controller":
            print(
                f"             service=0x{snap[0]:08X} state={snap[1]} "
                f"pending=0x{snap[2]:08X} action=0x{snap[3]:08X}"
            )
        elif probe:
            print(
                f"             owner=0x{snap[0]:08X} manager=0x{snap[1]:08X} "
                f"state_source=0x{snap[2]:08X} screen=0x{snap[3]:08X}"
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
            "ION action-pipeline trace: "
            f"{states.count('traced')} traced, "
            f"{states.count('original')} original, "
            f"{sum(item.startswith('unexpected:') for item in states)} unexpected"
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
                    client.write(probe.site, ORIGINAL_ENTRY)
                elif state != "original":
                    raise RuntimeError(f"unexpected entry at 0x{probe.site:08X}: {state}")
            if any(state_for(client, probe) != "original" for probe in PROBES):
                raise RuntimeError("one or more ION action entries did not restore")
            # This diagnostic exclusively owns 0x83C8A000..0x83C8A9FF.
            # Once every callsite is retail again, its old trampolines are
            # unreachable. Clear that reserved page so the mutually-exclusive
            # Lua file-loader trace can claim it without mistaking stale probe
            # images for foreign code.
            page_size = NEXT_KNOWN_CAVE - STUB_BASE
            write_chunks(client, STUB_BASE, bytes(page_size))
            if client.read(STUB_BASE, page_size) != bytes(page_size):
                raise RuntimeError("ION action diagnostic page did not clear")
            print("Verified: ION action-pipeline entries restored.")
            return 0

        arm(client)
        print("Verified: passive ION action-pipeline trace armed.")
        print("No event, result, state, completion or frontend route was synthesized.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

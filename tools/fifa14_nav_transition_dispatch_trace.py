#!/usr/bin/env python3
"""Passively trace NavTransitionEnd into the native flow-action dispatcher.

The stock FUT tile finishes its asynchronous screen transition by calling
``_global.NavTransitionEnd``. This trace correlates that exact callback with
the filtered ION unload action ``0x28``, its selected subscriber, its boolean
result and dispatcher exit. It records boundaries only; it never publishes an
event, selects a route, changes a result, or edits frontend state.
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
    cmpwi,
    conditional_branch,
    insn,
    lwz,
    or_register,
    stw,
    verify_module,
    write_chunks,
)


# The early local-server launch path leaves this historical diagnostic page
# unused. Do not combine this trace with the old Cards-init/network-state tools
# that also reserved parts of 0x83C8B000..0x83C8BFFF.
STUB_BASE = 0x83C8B000
STUB_STRIDE = 0x100
JOURNAL = 0x83C8B400
RING = 0x83C8B500
RING_COUNT = 16
RECORD_SIZE = 0x60
CAVE_END = RING + RING_COUNT * RECORD_SIZE
PAGE_END = 0x83C8C000


@dataclass(frozen=True)
class Probe:
    event_id: int
    name: str
    site: int
    original_hex: str
    layout: str
    prologue_mflr: bool = False
    event_filter: int | None = None

    @property
    def stub(self) -> int:
        return STUB_BASE + (self.event_id - 1) * STUB_STRIDE

    @property
    def original(self) -> bytes:
        return bytes.fromhex(self.original_hex)


PROBES = (
    Probe(1, "NavTransitionEnd", 0x82861998, "7D8802A6", "handler", True),
    Probe(2, "IONSubscriberCall", 0x82D59E6C, "816B0004", "ion-call", False, 0x28),
    Probe(3, "IONSubscriberResult", 0x82D59E78, "546B063F", "ion-result", False, 0x28),
    Probe(4, "IONDispatchExit", 0x82D59E90, "38210080", "ion-exit", False, 0x28),
)


def lwarx(rt: int, ra: int, rb: int) -> int:
    return 0x7C000028 | (rt << 21) | (ra << 16) | (rb << 11)


def stwcx_dot(rs: int, ra: int, rb: int) -> int:
    return 0x7C00012D | (rs << 21) | (ra << 16) | (rb << 11)


def mulli(rt: int, ra: int, immediate: int) -> int:
    return 0x1C000000 | (rt << 21) | (ra << 16) | (immediate & 0xFFFF)


def patch_for(probe: Probe) -> bytes:
    return insn(branch(probe.site, probe.stub, False))


def state_for(client: Xbdm, probe: Probe) -> str:
    current = client.read(probe.site, 4)
    if current == probe.original:
        return "original"
    if current == patch_for(probe):
        return "traced"
    return f"unexpected:{current.hex().upper()}"


def _snapshot_object(
    words: list[int], base_register: int, offsets: tuple[int, ...]
) -> None:
    destination = 0x20
    for source in offsets:
        words.extend((lwz(8, base_register, source), stw(8, 9, destination)))
        destination += 4


def build_stub(probe: Probe) -> bytes:
    words: list[int] = []
    if probe.prologue_mflr:
        words.append(int.from_bytes(probe.original, "big"))
    else:
        words.append(0x7D8802A6)  # mflr r12 for this diagnostic record
    preserve_r11 = probe.layout == "ion-call"
    if preserve_r11:
        # The displaced instruction dereferences the vtable currently held in
        # r11. Keep it across the logger, which uses r11 for its journal.
        words.append(or_register(0, 11, 11))  # mr r0,r11

    filter_branch: int | None = None
    if probe.event_filter is not None:
        words.extend((cmpwi(29, probe.event_filter), 0))
        filter_branch = len(words) - 1

    words.extend(
        (
            addis(11, 0, (JOURNAL + 0x8000) >> 16),
            addi(11, 11, JOURNAL & 0xFFFF),
        )
    )
    reserve = len(words)
    words.extend((lwarx(10, 0, 11), addi(10, 10, 1), stwcx_dot(10, 0, 11), 0))
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
            mulli(9, 9, RECORD_SIZE),
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

    if probe.layout == "handler":
        _snapshot_object(words, 3, tuple(range(0x00, 0x20, 4)))
    elif probe.layout == "ion-call":
        # r3 is the concrete subscriber selected for action kind 0x28. r28 is
        # the stack-backed unload payload and r30 is the current list cursor.
        words.extend(
            (
                stw(29, 9, 0x20),
                stw(28, 9, 0x24),
                stw(30, 9, 0x28),
                stw(31, 9, 0x2C),
                stw(3, 9, 0x30),
                lwz(8, 3, 0x00),
                stw(8, 9, 0x34),
                lwz(8, 8, 0x04),
                stw(8, 9, 0x38),
            )
        )
        for source, destination in zip(
            range(0x00, 0x20, 4), range(0x3C, 0x5C, 4)
        ):
            words.extend((lwz(8, 28, source), stw(8, 9, destination)))
    elif probe.layout == "ion-result":
        words.extend(
            (
                stw(29, 9, 0x20),
                stw(28, 9, 0x24),
                stw(30, 9, 0x28),
                stw(31, 9, 0x2C),
                stw(3, 9, 0x30),
                lwz(8, 30, 0x04),
                lwz(8, 8, 0x08),
                stw(8, 9, 0x34),
            )
        )
        for source, destination in zip(
            range(0x00, 0x20, 4), range(0x38, 0x58, 4)
        ):
            words.extend((lwz(8, 28, source), stw(8, 9, destination)))
    elif probe.layout == "ion-exit":
        words.extend(
            (
                stw(29, 9, 0x20),
                stw(28, 9, 0x24),
                stw(30, 9, 0x28),
                stw(31, 9, 0x2C),
                stw(3, 9, 0x30),
            )
        )
        for source, destination in zip(
            range(0x00, 0x20, 4), range(0x34, 0x54, 4)
        ):
            words.extend((lwz(8, 28, source), stw(8, 9, destination)))
    else:
        raise ValueError(probe.layout)

    words.extend((0x7C0004AC, stw(10, 9, 0x00)))  # sync; commit sequence
    if not probe.prologue_mflr:
        restore_index = len(words)
        if preserve_r11:
            words.append(or_register(11, 0, 0))  # mr r11,r0
        original_index = len(words)
        words.append(int.from_bytes(probe.original, "big"))
    words.append(0)
    tail = len(words) - 1
    words[tail] = branch(probe.stub + tail * 4, probe.site + 4, False)
    if filter_branch is not None:
        words[filter_branch] = conditional_branch(
            probe.stub + filter_branch * 4,
            probe.stub + (restore_index if preserve_r11 else original_index) * 4,
            4,
            2,
        )
    raw = b"".join(insn(word) for word in words)
    if len(raw) > STUB_STRIDE:
        raise RuntimeError(f"{probe.name} stub exceeds its slot ({len(raw):#x})")
    return raw.ljust(STUB_STRIDE, b"\0")


def verify_layout() -> None:
    if STUB_BASE + len(PROBES) * STUB_STRIDE != JOURNAL:
        raise RuntimeError("navigation trace stub allocation is inconsistent")
    if JOURNAL + 4 > RING:
        raise RuntimeError("navigation trace journal overlaps its ring")
    if CAVE_END > PAGE_END:
        raise RuntimeError("navigation trace exceeds its owned page")


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
        raise RuntimeError("Refusing unknown navigation site(s): " + ", ".join(unexpected))

    images = [build_stub(probe) for probe in PROBES]
    for probe, image in zip(PROBES, images):
        current = client.read(probe.stub, STUB_STRIDE)
        if current not in (bytes(STUB_STRIDE), image):
            raise RuntimeError(f"navigation cave 0x{probe.stub:08X} is occupied")

    if "traced" in states:
        for probe, state in zip(PROBES, states):
            if state == "traced":
                client.write(probe.site, probe.original)
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
            raise RuntimeError("one or more navigation probes did not publish")
    except Exception:
        for probe in PROBES:
            try:
                if state_for(client, probe) == "traced":
                    client.write(probe.site, probe.original)
            except Exception:
                pass
        raise


def restore(client: Xbdm) -> None:
    for probe in PROBES:
        current = state_for(client, probe)
        if current == "traced":
            client.write(probe.site, probe.original)
        elif current != "original":
            raise RuntimeError(f"unexpected entry at 0x{probe.site:08X}: {current}")


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
        print("No committed NavTransitionEnd/flow-dispatch event.")
        return
    if counter > RING_COUNT:
        print(f"WARNING: only the newest {RING_COUNT} records remain.")

    by_id = {probe.event_id: probe for probe in PROBES}
    for sequence, record in records:
        event_id = u32(record, 0x04)
        probe = by_id.get(event_id)
        name = probe.name if probe else f"unknown-{event_id}"
        r3, r4, r5, r6, r7, lr = (
            u32(record, off) for off in range(0x08, 0x20, 4)
        )
        print(
            f"{sequence:8d}  {name:18s} LR=0x{lr:08X} "
            f"r3=0x{r3:08X} r4=0x{r4:08X} r5=0x{r5:08X} "
            f"r6=0x{r6:08X} r7=0x{r7:08X}"
        )
        if probe and probe.layout == "handler":
            values = [u32(record, off) for off in range(0x20, 0x40, 4)]
            print("             object[0..1C]=" + "/".join(f"0x{x:08X}" for x in values))
        elif probe and probe.layout == "ion-call":
            kind, payload, cursor, sentinel, receiver, vtable, target = (
                u32(record, off) for off in range(0x20, 0x3C, 4)
            )
            payload_words = [u32(record, off) for off in range(0x3C, 0x5C, 4)]
            print(
                f"             kind=0x{kind:X} payload=0x{payload:08X} "
                f"cursor/sentinel=0x{cursor:08X}/0x{sentinel:08X} "
                f"receiver/vtbl/target=0x{receiver:08X}/0x{vtable:08X}/0x{target:08X}"
            )
            print("             payload[0..1C]=" + "/".join(f"0x{x:08X}" for x in payload_words))
        elif probe and probe.layout == "ion-result":
            payload_words = [u32(record, off) for off in range(0x38, 0x58, 4)]
            print(
                f"             kind=0x{u32(record, 0x20):X} "
                f"payload=0x{u32(record, 0x24):08X} "
                f"cursor/sentinel=0x{u32(record, 0x28):08X}/0x{u32(record, 0x2C):08X} "
                f"accepted={u32(record, 0x30)} receiver=0x{u32(record, 0x34):08X}"
            )
            print("             payload[0..1C]=" + "/".join(f"0x{x:08X}" for x in payload_words))
        elif probe and probe.layout == "ion-exit":
            payload_words = [u32(record, off) for off in range(0x34, 0x54, 4)]
            print(
                f"             kind=0x{u32(record, 0x20):X} "
                f"payload=0x{u32(record, 0x24):08X} "
                f"cursor/sentinel=0x{u32(record, 0x28):08X}/0x{u32(record, 0x2C):08X} "
                f"dispatch_result={u32(record, 0x30)}"
            )
            print("             payload[0..1C]=" + "/".join(f"0x{x:08X}" for x in payload_words))


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
            "NavTransitionEnd flow-dispatch trace: "
            f"{states.count('traced')} traced, {states.count('original')} original, "
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
            restore(client)
            print("Verified: NavTransitionEnd flow-dispatch entries restored.")
            return 0
        arm(client)
        print("Verified: passive NavTransitionEnd flow-dispatch trace armed.")
        print("No event, result, route or frontend state was synthesized.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

#!/usr/bin/env python3
"""Passively trace FIFA 14's native ION unload-to-EnterFlow pipeline.

The FUT launcher publishes ``screen=unload``.  The loader's vtable +0x70
method dispatches native action kind 0x28.  A successful flow transition then
enters ViewManager::EnterFlow and constructs a ScreenFlowController.  The four
entry trampolines below only record those retail boundaries, execute the exact
displaced instruction and resume.  They never change an argument, result,
state, event, completion or frontend route.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

from fifa14_plain_send_hook import (
    Xbdm,
    addi,
    addis,
    branch,
    cmpwi,
    conditional_branch,
    insn,
    lwz,
    stw,
    verify_module,
    write_chunks,
)


ORIGINAL_ENTRY = bytes.fromhex("7D8802A6")  # mflr r12
ION_LOADER_VTABLE = 0x820ED398

# The current startup set leaves 0x83C87D00..0x83C87FFF untouched.  The
# neighbouring passive FUT-completion allocation ends at 0x83C87CFF and the
# older ION diagnostics start at 0x83C88000.
STUB_BASE = 0x83C87D00
STUB_STRIDE = 0x80
JOURNAL = 0x83C87F00
RECORD_SIZE = 0x40
CAVE_END = 0x83C88000


@dataclass(frozen=True)
class Probe:
    event_id: int
    name: str
    site: int
    layout: str
    event_filter: int | None = None

    @property
    def stub(self) -> int:
        return STUB_BASE + (self.event_id - 1) * STUB_STRIDE

    @property
    def record(self) -> int:
        return JOURNAL + (self.event_id - 1) * RECORD_SIZE


PROBES = (
    Probe(1, "IONUnloadViewEnqueue", 0x82D5DE28, "loader"),
    Probe(2, "IONActionDispatch28", 0x82D59E30, "dispatcher", 0x28),
    Probe(3, "ViewManagerEnterFlow", 0x82D5FA40, "enter-flow"),
    Probe(4, "ScreenFlowConstructor", 0x82D5C268, "constructor"),
)


STATIC_GUARDS = (
    (0x820ED408, (0x82D5DE28).to_bytes(4, "big"), "Loader vtable +0x70"),
    (0x820ED420, (0x82D59E30).to_bytes(4, "big"), "dispatcher vtable +0x04"),
    (0x820ED3D4, (0x82D5FA40).to_bytes(4, "big"), "Loader vtable +0x3C"),
    (0x82D5FAB0, bytes.fromhex("4BFFC7B9"), "EnterFlow constructor call"),
)


def snapshot_words(probe: Probe) -> list[int]:
    """Return six snapshots at record offsets 0x20..0x34."""
    words: list[int] = []

    def capture(displacement: int, destination: int) -> None:
        words.extend((lwz(8, 3, displacement), stw(8, 11, destination)))

    if probe.layout == "loader":
        capture(0x00, 0x20)  # concrete Loader vtable
        capture(0x30, 0x24)  # action queue/dispatcher
        capture(0x10, 0x28)  # controller vector begin
        capture(0x14, 0x2C)  # controller vector end
        capture(0x18, 0x30)  # controller vector capacity
        capture(0x34, 0x34)  # secondary dispatcher/service
        return words

    if probe.layout == "dispatcher":
        capture(0x00, 0x20)  # dispatcher vtable
        capture(0x24, 0x24)  # terminal node
        capture(0x28, 0x28)  # first node
        capture(0x08, 0x2C)  # route tree field 0
        capture(0x0C, 0x30)  # route tree field 1
        capture(0x10, 0x34)  # route tree root
        return words

    if probe.layout == "enter-flow":
        capture(0x00, 0x20)  # Loader vtable
        capture(0x10, 0x24)  # controller vector begin
        capture(0x14, 0x28)  # controller vector end
        capture(0x18, 0x2C)  # controller vector capacity
        capture(0x30, 0x30)  # action dispatcher
        capture(0x34, 0x34)  # secondary dispatcher/service
        return words

    if probe.layout == "constructor":
        # r4 is the owning Loader/ViewManager and r5 the copied flow config.
        for displacement, destination in (
            (0x00, 0x20),
            (0x10, 0x24),
            (0x14, 0x28),
            (0x18, 0x2C),
            (0x30, 0x30),
            (0x34, 0x34),
        ):
            words.extend((lwz(8, 4, displacement), stw(8, 11, destination)))
        return words

    raise ValueError(f"unknown layout: {probe.layout}")


def build_stub(probe: Probe) -> bytes:
    # Each probe owns a fixed record.  Offset zero is invalidated before the
    # snapshot and recommitted with its invocation count only after sync.
    words = [int.from_bytes(ORIGINAL_ENTRY, "big")]
    filter_branch: int | None = None
    if probe.event_filter is not None:
        words.extend((cmpwi(5, probe.event_filter), 0))
        filter_branch = len(words) - 1
    words.extend([
        addis(11, 0, (probe.record + 0x8000) >> 16),
        addi(11, 11, probe.record & 0xFFFF),
        lwz(10, 11, 0x00),
        addi(10, 10, 1),
        addi(8, 0, 0),
        stw(8, 11, 0x00),
        addi(8, 0, probe.event_id),
        stw(8, 11, 0x04),
        stw(3, 11, 0x08),
        stw(4, 11, 0x0C),
        stw(5, 11, 0x10),
        stw(6, 11, 0x14),
        stw(7, 11, 0x18),
        stw(12, 11, 0x1C),
    ])
    words.extend(snapshot_words(probe))
    words.extend(
        (
            0x7C0004AC,  # sync
            stw(10, 11, 0x00),
            0,
        )
    )
    tail = len(words) - 1
    words[tail] = branch(probe.stub + tail * 4, probe.site + 4, False)
    if filter_branch is not None:
        words[filter_branch] = conditional_branch(
            probe.stub + filter_branch * 4,
            probe.stub + tail * 4,
            4,
            2,
        )
    raw = b"".join(insn(word) for word in words)
    if len(raw) > STUB_STRIDE:
        raise RuntimeError(f"{probe.name} stub exceeds its slot")
    return raw.ljust(STUB_STRIDE, b"\0")


def patch_for(probe: Probe) -> bytes:
    return insn(branch(probe.site, probe.stub, False))


def state_for(client: Xbdm, probe: Probe) -> str:
    current = client.read(probe.site, 4)
    if current == ORIGINAL_ENTRY:
        return "original"
    if current == patch_for(probe):
        return "traced"
    return f"unexpected:{current.hex().upper()}"


def verify_static_guards(client: Xbdm) -> None:
    if STUB_BASE + len(PROBES) * STUB_STRIDE != JOURNAL:
        raise RuntimeError("ION unload stub allocation is inconsistent")
    if JOURNAL + len(PROBES) * RECORD_SIZE != CAVE_END:
        raise RuntimeError("ION unload journal allocation is inconsistent")
    for address, expected, description in STATIC_GUARDS:
        current = client.read(address, len(expected))
        if current != expected:
            raise RuntimeError(
                f"Unexpected {description} at 0x{address:08X}: "
                f"{current.hex().upper()}"
            )


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, len(PROBES) * RECORD_SIZE)
    any_record = False
    for probe in PROBES:
        offset = (probe.event_id - 1) * RECORD_SIZE
        record = raw[offset : offset + RECORD_SIZE]
        invocations = u32(record, 0x00)
        if not invocations:
            print(f"{probe.name:22s} invocations=0")
            continue
        any_record = True
        r3, r4, r5, r6, r7 = (u32(record, off) for off in range(0x08, 0x1C, 4))
        lr = u32(record, 0x1C)
        snap = [u32(record, off) for off in range(0x20, 0x38, 4)]
        print(
            f"{probe.name:22s} invocations={invocations} LR=0x{lr:08X} "
            f"r3=0x{r3:08X} r4=0x{r4:08X} r5=0x{r5:08X} "
            f"r6=0x{r6:08X} r7=0x{r7:08X}"
        )
        if probe.layout == "loader":
            print(
                f"             loader_vtbl=0x{snap[0]:08X} "
                f"queue=0x{snap[1]:08X} controllers="
                f"0x{snap[2]:08X}..0x{snap[3]:08X}/0x{snap[4]:08X}"
            )
        elif probe.layout == "dispatcher":
            print(
                f"             dispatcher_vtbl=0x{snap[0]:08X} "
                f"terminal=0x{snap[1]:08X} first=0x{snap[2]:08X} "
                f"event=0x{r5:08X}"
            )
        elif probe.layout == "enter-flow":
            print(
                f"             loader_vtbl=0x{snap[0]:08X} "
                f"controllers=0x{snap[1]:08X}..0x{snap[2]:08X}/"
                f"0x{snap[3]:08X} flow_args=0x{r4:08X}/0x{r5:08X}"
            )
        elif probe.layout == "constructor":
            print(
                f"             owner_vtbl=0x{snap[0]:08X} "
                f"controllers=0x{snap[1]:08X}..0x{snap[2]:08X}/"
                f"0x{snap[3]:08X} allocated=0x{r3:08X} config=0x{r5:08X}"
            )
    if not any_record:
        print("No committed ION unload-pipeline event.")


def arm(client: Xbdm) -> None:
    """Publish the four passive entry trampolines before title execution."""
    verify_module(client)
    verify_static_guards(client)
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
            raise RuntimeError(f"ION trace cave 0x{probe.stub:08X} is not free/owned")
    if all(state == "original" for state in states):
        journal = client.read(JOURNAL, CAVE_END - JOURNAL)
        if journal != bytes(len(journal)):
            raise RuntimeError("ION unload journal is not free")

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
                raise RuntimeError(f"Stub verification failed at 0x{probe.stub:08X}")
        for probe in PROBES:
            client.write(probe.site, patch_for(probe))
        if any(state_for(client, probe) != "traced" for probe in PROBES):
            raise RuntimeError("One or more ION unload probes did not publish")
    except Exception:
        for probe in PROBES:
            try:
                if state_for(client, probe) == "traced":
                    client.write(probe.site, ORIGINAL_ENTRY)
            except Exception:
                pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "read", "restore"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        verify_static_guards(client)
        states = [state_for(client, probe) for probe in PROBES]
        print(
            "ION unload-to-EnterFlow trace: "
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
            if any(state_for(client, probe) != "original" for probe in PROBES):
                raise RuntimeError("One or more ION unload entries did not restore")
            print("Verified: ION unload-pipeline entries restored.")
            return 0

        arm(client)
        print("Verified: passive ION unload-to-EnterFlow trace armed.")
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

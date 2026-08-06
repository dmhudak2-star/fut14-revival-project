#!/usr/bin/env python3
"""Passively trace the retail FUT launcher-to-screen-unload transition.

Two native boundaries are recorded:

* ION's generic action-method invocation bridge used by navigation actions;
* the generic notification callback dispatcher, filtered to the retail
  ``screen`` provider notification ``0x276A``.

The existing provider/publication trace records the downstream core event
sender and therefore supplies the actual event text (including ``FUTStartUp``
if the stock ``futLauncher`` state is entered).  This trace complements it by
proving whether the screen notification callback itself executes.

Both entry trampolines execute the displaced retail instruction and resume at
the following instruction.  They never publish an event, change an argument
or result, acknowledge an unload, or select a frontend route.
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
    rlwinm,
    stw,
    verify_module,
    write_chunks,
)


ORIGINAL_ENTRY = bytes.fromhex("7D8802A6")  # mflr r12

# This is the verified free tail between the provider trace (ending at
# 0x83C89B00) and the older 0x83C8A000 diagnostics.
STUB_BASE = 0x83C89B00
STUB_STRIDE = 0xC0
JOURNAL = 0x83C89C80
RING = 0x83C89D00
RING_COUNT = 16
RECORD_SIZE = 0x20
CAVE_END = RING + RING_COUNT * RECORD_SIZE
NEXT_KNOWN_CAVE = 0x83C8A000


@dataclass(frozen=True)
class Probe:
    event_id: int
    name: str
    site: int
    event_filter: int | None = None

    @property
    def stub(self) -> int:
        return STUB_BASE + (self.event_id - 1) * STUB_STRIDE


PROBES = (
    Probe(1, "ion_action_method_invoke", 0x8288BF68),
    # ABI: r3=notification id, r4=subscriber, r5=payload/event object.
    Probe(2, "screen_notification_callback", 0x82D58910, 0x276A),
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
    # The displaced mflr executes first so r12 retains the incoming LR that
    # the retail prologue expects.
    words = [int.from_bytes(ORIGINAL_ENTRY, "big")]
    filter_branch: int | None = None
    if probe.event_filter is not None:
        words.extend((cmpwi(3, probe.event_filter), 0))
        filter_branch = len(words) - 1

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
            rlwinm(9, 9, 5, 0, 26),  # slot * RECORD_SIZE
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
            0x7C0004AC,  # sync
            stw(10, 9, 0x00),
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


def verify_layout() -> None:
    if STUB_BASE + len(PROBES) * STUB_STRIDE != JOURNAL:
        raise RuntimeError("launcher trace stub allocation is inconsistent")
    if CAVE_END > NEXT_KNOWN_CAVE:
        raise RuntimeError("launcher trace overlaps 0x83C8A000 diagnostics")


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
            raise RuntimeError(f"launcher trace cave 0x{probe.stub:08X} is occupied")

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
            raise RuntimeError("one or more launcher probes did not publish")
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


def safe_cstring(client: Xbdm, pointer: int, limit: int = 0x80) -> str | None:
    if not 0x80000000 <= pointer < 0xE0000000:
        return None
    try:
        raw = client.read(pointer, limit).split(b"\0", 1)[0]
    except Exception:
        return None
    if not raw:
        return ""
    if any(byte < 0x20 or byte > 0x7E for byte in raw):
        return None
    return raw.decode("ascii")


def read_string_arg(client: Xbdm, pointer: int, limit: int = 0x80) -> str | None:
    if not 0x80000000 <= pointer < 0xE0000000:
        return None
    try:
        header = client.read(pointer, 12)
    except Exception:
        return None
    begin, end, capacity = (u32(header, offset) for offset in (0, 4, 8))
    if (
        0x80000000 <= begin < 0xE0000000
        and begin <= end <= capacity
        and end - begin <= limit
    ):
        try:
            raw = client.read(begin, end - begin) if begin != end else b""
        except Exception:
            return None
        return raw.decode("utf-8", errors="backslashreplace")
    return safe_cstring(client, pointer, limit)


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
        print("No committed FUT launcher transition event.")
        return
    if counter > RING_COUNT:
        print(f"WARNING: only the newest {RING_COUNT} records remain.")

    by_id = {probe.event_id: probe for probe in PROBES}
    for sequence, record in records:
        event_id = u32(record, 0x04)
        probe = by_id.get(event_id)
        name = probe.name if probe else f"unknown-{event_id}"
        r3, r4, r5, r6, r7 = (u32(record, offset) for offset in range(0x08, 0x1C, 4))
        lr = u32(record, 0x1C)
        print(
            f"{sequence:8d}  {name:30s} LR=0x{lr:08X} "
            f"r3=0x{r3:08X} r4=0x{r4:08X} r5=0x{r5:08X} "
            f"r6=0x{r6:08X} r7=0x{r7:08X}"
        )
        decoded = []
        for register, value in (("r3", r3), ("r4", r4), ("r5", r5), ("r6", r6), ("r7", r7)):
            text = read_string_arg(client, value)
            if text is not None:
                decoded.append(f"{register}={text!r}")
        if decoded:
            print("             strings: " + ", ".join(decoded))
        if probe and probe.name == "screen_notification_callback":
            subscriber_vtable = 0
            payload_vtable = 0
            try:
                subscriber_vtable = u32(client.read(r4, 4), 0)
            except Exception:
                pass
            try:
                payload_vtable = u32(client.read(r5, 4), 0)
            except Exception:
                pass
            print(
                f"             notification=0x{r3:04X} "
                f"subscriber_vtable=0x{subscriber_vtable:08X} "
                f"payload_vtable=0x{payload_vtable:08X}"
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
            "FUT launcher transition trace: "
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
                raise RuntimeError("one or more launcher entries did not restore")
            print("Verified: FUT launcher transition entries restored.")
            return 0

        arm(client)
        print("Verified: passive FUT launcher transition trace armed.")
        print("No event, result, completion or frontend route was synthesized.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

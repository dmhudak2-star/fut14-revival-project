#!/usr/bin/env python3
"""Passively trace the rare completion boundaries after FUT screen unload.

The FUT tile's retail transition publishes one of the KeyValueDataProvider
notifications ``0x2768..0x276D`` (background/video/screen/subScreen/popup),
builds the corresponding ION payload and finally invokes
``_global.Handle_NavTransitionEnd``.  This compact trace records only those
rare boundaries plus the provider's completion actions ``0x35..0x3C``.

Every trampoline executes its displaced retail instruction and resumes.  It
does not publish or acknowledge an event, change a result, load a view, enter
a flow, or edit frontend state.
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
    cmpw,
    cmpwi,
    conditional_branch,
    insn,
    lwz,
    stw,
    verify_module,
    write_chunks,
)


# This is the verified free tail of the page owned by the passive
# NavTransitionEnd trace.  The next historical diagnostics start at
# 0x83C8C000.
STUB_BASE = 0x83C8BB00
STUB_STRIDE = 0xA0
JOURNAL = 0x83C8BE20
RING = 0x83C8BE40
RING_COUNT = 16
RECORD_SIZE = 0x1C
CAVE_END = RING + RING_COUNT * RECORD_SIZE
PAGE_END = 0x83C8C000

PROVIDER_VTABLE = 0x82081728
PROVIDER_EVENT_FIRST = 0x2768
PROVIDER_EVENT_LAST = 0x276D
PROVIDER_COMPLETION_FIRST = 0x35
PROVIDER_COMPLETION_LAST = 0x3C

# These three sites are also owned by fifa14_provider_publication_trace.py.
# The early launcher may intentionally combine both diagnostics: in that case
# keep the broader provider trampoline published and arm only this trace's two
# non-overlapping tail boundaries.  Values are the build-guarded PPC branches
# emitted by that passive trace, not retail/frontend state changes.
EXTERNAL_PROVIDER_PATCHES = {
    0x82E6E1A8: bytes.fromhex("48E1A998"),
    0x8293CA98: bytes.fromhex("4934C3A8"),
    0x82974100: bytes.fromhex("49314800"),
}


@dataclass(frozen=True)
class Probe:
    event_id: int
    name: str
    site: int
    original_hex: str
    filter_kind: str | None = None
    prologue_mflr: bool = True

    @property
    def stub(self) -> int:
        return STUB_BASE + (self.event_id - 1) * STUB_STRIDE

    @property
    def original(self) -> bytes:
        return bytes.fromhex(self.original_hex)


PROBES = (
    Probe(1, "ProviderPublish", 0x82E6E1A8, "7D8802A6", "provider-event"),
    Probe(2, "ProviderNotification", 0x82D58910, "7D8802A6", "event-r3"),
    Probe(3, "ScreenPayloadBuilder", 0x8293CA98, "7D8802A6"),
    Probe(4, "ProviderCompletion", 0x82974100, "7D8802A6", "completion-r4"),
    Probe(
        5,
        "HandleNavTransitionEndResult",
        0x828619C0,
        "38210060",  # addi r1,r1,0x60
        None,
        False,
    ),
)

PROVIDER_EVENT_NAMES = {
    0x2768: "background",
    0x2769: "video",
    0x276A: "screen",
    0x276B: "subScreen",
    0x276C: "popup",
    0x276D: "provider-complete",
}


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
    if current == EXTERNAL_PROVIDER_PATCHES.get(probe.site):
        return "provider-traced"
    return f"unexpected:{current.hex().upper()}"


def _range_filter(
    words: list[int], register: int, first: int, last: int
) -> list[tuple[int, int, int]]:
    """Append inclusive range checks and return pending branch conditions."""

    words.extend((cmpwi(register, first), 0))
    below = len(words) - 1
    words.extend((cmpwi(register, last), 0))
    above = len(words) - 1
    # bc BO=12,BI=0 is blt; BO=12,BI=1 is bgt.
    return [(below, 12, 0), (above, 12, 1)]


def build_stub(probe: Probe) -> bytes:
    words: list[int] = []
    if probe.prologue_mflr:
        words.append(int.from_bytes(probe.original, "big"))
    else:
        words.append(0x7D8802A6)  # mflr r12 for the diagnostic LR

    pending_filters: list[tuple[int, int, int]] = []
    if probe.filter_kind == "provider-event":
        words.extend(
            (
                lwz(11, 3, 0),
                addis(10, 0, 0x8208),
                addi(10, 10, 0x1728),
                cmpw(11, 10),
                0,
            )
        )
        pending_filters.append((len(words) - 1, 4, 2))  # bne
        pending_filters.extend(
            _range_filter(words, 4, PROVIDER_EVENT_FIRST, PROVIDER_EVENT_LAST)
        )
    elif probe.filter_kind == "event-r3":
        pending_filters.extend(
            _range_filter(words, 3, PROVIDER_EVENT_FIRST, PROVIDER_EVENT_LAST)
        )
    elif probe.filter_kind == "completion-r4":
        pending_filters.extend(
            _range_filter(
                words, 4, PROVIDER_COMPLETION_FIRST, PROVIDER_COMPLETION_LAST
            )
        )
    elif probe.filter_kind is not None:
        raise ValueError(probe.filter_kind)

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
            andi_dot(9, 10, 0x0F),
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
            # ProviderCompletion is the boundary whose producer we need to
            # identify.  Its displaced mflr has preserved the caller in r12;
            # r7 is only an action argument and is less useful here.  Keep the
            # historical r7 field for every other probe.
            stw(12 if probe.name == "ProviderCompletion" else 7, 9, 0x18),
            0x7C0004AC,  # sync
            stw(10, 9, 0x00),
        )
    )

    resume_index = len(words)
    if not probe.prologue_mflr:
        words.append(int.from_bytes(probe.original, "big"))
    branch_back_index = len(words)
    words.append(branch(probe.stub + branch_back_index * 4, probe.site + 4, False))

    for index, bo, bi in pending_filters:
        words[index] = conditional_branch(
            probe.stub + index * 4,
            probe.stub + resume_index * 4,
            bo,
            bi,
        )

    raw = b"".join(insn(word) for word in words)
    if len(raw) > STUB_STRIDE:
        raise RuntimeError(
            f"{probe.name} stub is 0x{len(raw):X}, over 0x{STUB_STRIDE:X}"
        )
    return raw.ljust(STUB_STRIDE, b"\0")


def verify_layout() -> None:
    if STUB_BASE + len(PROBES) * STUB_STRIDE != JOURNAL:
        raise RuntimeError("unload-completion stub allocation is inconsistent")
    if JOURNAL + 4 > RING:
        raise RuntimeError("unload-completion journal overlaps its ring")
    if CAVE_END > PAGE_END:
        raise RuntimeError("unload-completion trace exceeds its owned page")


def arm(client: Xbdm) -> None:
    verify_module(client)
    verify_layout()
    states = [state_for(client, probe) for probe in PROBES]
    unexpected = [
        f"0x{probe.site:08X}={state}"
        for probe, state in zip(PROBES, states)
        if state not in ("original", "traced", "provider-traced")
    ]
    if unexpected:
        raise RuntimeError("refusing unknown trace site(s): " + ", ".join(unexpected))

    images = [build_stub(probe) for probe in PROBES]
    for probe, image in zip(PROBES, images):
        current = client.read(probe.stub, STUB_STRIDE)
        if current not in (bytes(STUB_STRIDE), image):
            raise RuntimeError(f"trace cave 0x{probe.stub:08X} is occupied")

    if "traced" in states:
        for probe, state in zip(PROBES, states):
            if state == "traced":
                client.write(probe.site, probe.original)
        time.sleep(0.02)

    try:
        write_chunks(client, JOURNAL, bytes(CAVE_END - JOURNAL))
        for probe, image in zip(PROBES, images):
            write_chunks(client, probe.stub, image)
        for probe, state in zip(PROBES, states):
            if state != "provider-traced":
                client.write(probe.site, patch_for(probe))
        if any(
            state_for(client, probe)
            not in ("traced", "provider-traced")
            for probe in PROBES
        ):
            raise RuntimeError("one or more unload-completion probes did not publish")
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
        print("No committed unload-completion event.")
        return
    if counter > RING_COUNT:
        print(f"WARNING: only the newest {RING_COUNT} records remain.")

    by_id = {probe.event_id: probe for probe in PROBES}
    for sequence, record in records:
        probe = by_id.get(u32(record, 0x04))
        name = probe.name if probe else f"unknown-{u32(record, 0x04)}"
        r3, r4, r5, r6, r7_or_lr = (
            u32(record, offset) for offset in range(0x08, 0x1C, 4)
        )
        tail_label = "LR" if probe and probe.name == "ProviderCompletion" else "r7"
        print(
            f"{sequence:8d}  {name:28s} "
            f"r3=0x{r3:08X} r4=0x{r4:08X} r5=0x{r5:08X} "
            f"r6=0x{r6:08X} {tail_label}=0x{r7_or_lr:08X}"
        )
        if probe and probe.name == "ProviderPublish":
            print(
                "             provider event="
                f"0x{r4:04X} ({PROVIDER_EVENT_NAMES.get(r4, 'unknown')})"
            )
        elif probe and probe.name == "ProviderNotification":
            print(
                "             notification="
                f"0x{r3:04X} ({PROVIDER_EVENT_NAMES.get(r3, 'unknown')}) "
                f"subscriber=0x{r4:08X} payload=0x{r5:08X}"
            )
        elif probe and probe.name == "ProviderCompletion":
            print(f"             provider completion action=0x{r4:02X}")
        elif probe and probe.name == "HandleNavTransitionEndResult":
            print(f"             VM invocation result=0x{r3:08X}")


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
            "Unload-completion trace: "
            f"{states.count('traced')} traced, "
            f"{states.count('provider-traced')} provider-traced, "
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
                elif state not in ("original", "provider-traced"):
                    raise RuntimeError(
                        f"unexpected entry at 0x{probe.site:08X}: {state}"
                    )
            if any(state_for(client, probe) != "original" for probe in PROBES):
                raise RuntimeError("one or more unload-completion entries did not restore")
            print("Verified: unload-completion entries restored.")
            return 0

        arm(client)
        print("Verified: passive unload-completion trace armed.")
        print("No event, completion, result, flow or frontend state was synthesized.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

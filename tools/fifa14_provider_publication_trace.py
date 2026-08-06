#!/usr/bin/env python3
"""Passively trace the KeyValueDataProvider-to-ION publication boundary.

The FUT click leaves prepared values such as ``background/load``,
``screen/unload`` and ``popup/ToFe`` in FIFA 14's retail
``FrontEnd::KeyValueDataProvider``.  This tool records the native callbacks
that parse, publish and consume those values.  Every trampoline executes the
displaced retail instruction and resumes at the following instruction; it
does not call game code, change an argument/result, publish an event or select
a frontend route.

The trace owns only 0x83C88900..0x83C89AFF.  That range starts immediately
after the passive ION LoadView trace and ends before the older 0x83C8A000+
diagnostics.
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
    conditional_branch,
    insn,
    lwz,
    rlwinm,
    stw,
    verify_module,
    write_chunks,
)


PROVIDER_VTABLE = 0x82081728
ION_LOADER_VTABLE = 0x820ED398

STUB_BASE = 0x83C88900
STUB_STRIDE = 0xC0
JOURNAL = 0x83C89200
RING = 0x83C89300
RING_COUNT = 32
RECORD_SIZE = 0x40
CAVE_END = RING + RING_COUNT * RECORD_SIZE
NEXT_KNOWN_CAVE = 0x83C8A000

ORIGINAL_ENTRY = bytes.fromhex("7D8802A6")  # mflr r12


@dataclass(frozen=True)
class Probe:
    event_id: int
    name: str
    site: int
    layout: str
    provider_filter_register: int | None = None
    original: bytes = ORIGINAL_ENTRY

    @property
    def stub(self) -> int:
        return STUB_BASE + (self.event_id - 1) * STUB_STRIDE


PROBES = (
    # KeyValueDataProvider's vtable +0x04 event dispatcher.  r4 is the event
    # kind and r5 is the retail payload/interface object.
    Probe(1, "provider_handle", 0x82974100, "provider"),
    # Branches 0x27 and 0x28 from provider_handle parse scalar and collection
    # payloads respectively, then publish the corresponding 0x2768..0x276D
    # provider notification.
    Probe(2, "provider_parse_27", 0x82970FA0, "provider"),
    Probe(3, "provider_parse_28", 0x82971BC0, "provider"),
    # Shared DataProvider notification helper.  It is logged only when r3 is
    # this exact concrete KeyValueDataProvider; all other retail callers pass
    # straight through without reserving a ring record.
    Probe(4, "provider_publish", 0x82E6E1A8, "provider", 3),
    # Downstream boundaries already observed separately.  Keeping them in the
    # same ring gives an unambiguous ordering if this run reaches either one.
    Probe(5, "ion_core_send_event", 0x8288D9F0, "event"),
    Probe(6, "ion_loadview_enqueue", 0x82D5DCA8, "loader"),
    # The 0x276A notification must build a screen payload and then reach an
    # active dispatcher receiver before LoadView can be requested.
    Probe(7, "provider_event_factory", 0x8296E7C0, "provider"),
    Probe(8, "screen_payload_builder", 0x8293CA98, "provider"),
    Probe(9, "active_receiver_handler", 0x8293BA40, "receiver"),
    # Native viewmodel creation/registration/enable path.  The detached
    # globalviewmodel has PROVIDER_VTABLE, so register/enable are filtered on
    # their consumer register and cannot fill the ring with unrelated models.
    Probe(10, "viewmodel_factory", 0x82D5B4A0, "factory"),
    Probe(11, "viewmodel_register", 0x82D5AC28, "register", 5),
    Probe(
        12,
        "viewmodel_enable",
        0x82E6E0C0,
        "provider",
        3,
        bytes.fromhex("80630068"),  # lwz r3,0x68(r3)
    ),
)


STATIC_GUARDS = (
    (0x82081728, (0x82970F50).to_bytes(4, "big"), "provider vtable +0x00"),
    (0x8208172C, (0x82974100).to_bytes(4, "big"), "provider vtable +0x04"),
    (0x82081744, (0x8296E7C0).to_bytes(4, "big"), "provider vtable +0x1C"),
    (0x82081748, (0x82E6E0C0).to_bytes(4, "big"), "provider vtable +0x20"),
    (0x8207CE5C, (0x8293BA40).to_bytes(4, "big"), "active receiver vtable +0x04"),
    (0x829742D0, bytes.fromhex("4BFFCCD1"), "provider branch 0x27"),
    (0x829742C0, bytes.fromhex("4BFFD901"), "provider branch 0x28"),
    (0x8296E818, bytes.fromhex("4BFCE281"), "screen payload builder branch"),
    (0x820ED404, (0x82D5DCA8).to_bytes(4, "big"), "ION Loader vtable +0x6C"),
)


def lwarx(rt: int, ra: int, rb: int) -> int:
    return 0x7C000028 | (rt << 21) | (ra << 16) | (rb << 11)


def stwcx_dot(rs: int, ra: int, rb: int) -> int:
    return 0x7C00012D | (rs << 21) | (ra << 16) | (rb << 11)


def build_stub(probe: Probe) -> bytes:
    """Build a native-safe entry logger with an optional provider filter."""

    # The displaced mflr must run first so r12 contains the incoming LR that
    # the retail prologue expects.  r8..r11 and CR0 are volatile in the Xenon
    # ABI; r3..r7 are only read.
    words: list[int] = [int.from_bytes(probe.original, "big")]

    filter_branch: int | None = None
    if probe.provider_filter_register is not None:
        owner = probe.provider_filter_register
        words.extend(
            (
                lwz(11, owner, 0),
                addis(10, 0, 0x8208),
                addi(10, 10, 0x1728),
                cmpw(11, 10),
                0,  # bne straight to the displaced-instruction return path
            )
        )
        filter_branch = len(words) - 1

    words.extend(
        (
            addis(11, 0, 0x83C9),
            addi(11, 11, -0x6E00),  # JOURNAL = 0x83C89200
        )
    )
    reserve = len(words)
    words.extend(
        (
            lwarx(10, 0, 11),
            addi(10, 10, 1),
            stwcx_dot(10, 0, 11),
            0,  # bne reserve
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
            rlwinm(9, 9, 6, 0, 25),  # slot * RECORD_SIZE
            addis(8, 0, 0x83C9),
            addi(8, 8, -0x6D00),     # RING = 0x83C89300
            add(9, 8, 9),
            addi(8, 0, 0),
            stw(8, 9, 0x00),         # invalidate before overwrite
            addi(8, 0, probe.event_id),
            stw(8, 9, 0x04),
            stw(3, 9, 0x08),
            stw(4, 9, 0x0C),
            stw(5, 9, 0x10),
            stw(6, 9, 0x14),
            stw(7, 9, 0x18),
            stw(12, 9, 0x1C),
            lwz(8, 3, 0x00),
            stw(8, 9, 0x20),
        )
    )

    if probe.layout == "provider":
        # Snapshot only the begin pointers of retail byte vectors.  The trace
        # does not traverse or mutate any vector.  These offsets are the live
        # fields printed by fifa14_loadview_state.py.
        for source, destination in (
            (0x8C, 0x24),   # background.name
            (0x9C, 0x28),   # background.param1
            (0xDC, 0x2C),   # screen.name
            (0xEC, 0x30),   # screen.param1
            (0x15C, 0x34),  # popup vector/value0
            (0x16C, 0x38),  # popup value1
        ):
            words.extend((lwz(8, 3, source), stw(8, 9, destination)))
    elif probe.layout == "event":
        # Snapshot the transient event argument while the caller's stack is
        # still valid.  0x8288D9F0 forwards this object synchronously.
        for source, destination in zip(range(0, 0x18, 4), range(0x24, 0x3C, 4)):
            words.extend((lwz(8, 4, source), stw(8, 9, destination)))
    elif probe.layout == "register":
        # r4 is the name byte vector and r5 the consumer being registered.
        for owner, source, destination in (
            (4, 0x00, 0x24),
            (4, 0x04, 0x28),
            (5, 0x00, 0x2C),
            (5, 0x7C, 0x30),
            (5, 0x80, 0x34),
            (5, 0x84, 0x38),
        ):
            words.extend((lwz(8, owner, source), stw(8, 9, destination)))
    elif probe.layout == "receiver":
        # r5 is the event payload object delivered to the active receiver.
        words.extend((lwz(8, 5, 0), stw(8, 9, 0x24)))
        words.append(addi(8, 0, 0))
        for destination in range(0x28, 0x3C, 4):
            words.append(stw(8, 9, destination))
    else:
        words.append(addi(8, 0, 0))
        for destination in range(0x24, 0x3C, 4):
            words.append(stw(8, 9, destination))

    words.extend(
        (
            0x7C0004AC,       # sync
            stw(10, 9, 0x00), # commit complete record
            0,                # branch back to retail site+4
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
        raise RuntimeError(
            f"{probe.name} stub is 0x{len(raw):X}, over its 0x{STUB_STRIDE:X} slot"
        )
    return raw.ljust(STUB_STRIDE, b"\0")


def patch_for(probe: Probe) -> bytes:
    return insn(branch(probe.site, probe.stub, False))


def state_for(client: Xbdm, probe: Probe) -> str:
    current = client.read(probe.site, 4)
    if current == probe.original:
        return "original"
    if current == patch_for(probe):
        return "traced"
    return f"unexpected:{current.hex().upper()}"


def verify_static_guards(client: Xbdm) -> None:
    if CAVE_END > NEXT_KNOWN_CAVE:
        raise RuntimeError("provider trace allocation overlaps 0x83C8A000 diagnostics")
    for address, expected, description in STATIC_GUARDS:
        current = client.read(address, len(expected))
        if current != expected:
            raise RuntimeError(
                f"Unexpected {description} at 0x{address:08X}: "
                f"{current.hex().upper()}"
            )


def arm(client: Xbdm) -> None:
    """Arm the complete passive trace on an already loaded retail title.

    The caller may keep the title stopped while this runs.  Accepting an
    existing XBDM-compatible connection lets the early startup launcher
    publish the probes before any title code (and therefore any native
    viewmodel registration) has executed.
    """
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
    cave_states: list[str] = []
    for probe, image in zip(PROBES, images):
        current = client.read(probe.stub, STUB_STRIDE)
        if current == bytes(STUB_STRIDE):
            cave_states.append("zero")
        elif current == image:
            cave_states.append("ours")
        else:
            raise RuntimeError(
                f"Code cave slot 0x{probe.stub:08X} is not free/owned"
            )
    if all(item == "zero" for item in cave_states):
        journal = client.read(JOURNAL, CAVE_END - JOURNAL)
        if journal != bytes(len(journal)):
            raise RuntimeError("Provider trace journal range is not free")

    # When re-arming our exact image, unpublish hooks before clearing it.
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
                raise RuntimeError(f"Stub verification failed at 0x{probe.stub:08X}")
        for probe in PROBES:
            client.write(probe.site, patch_for(probe))
        if any(state_for(client, probe) != "traced" for probe in PROBES):
            raise RuntimeError("One or more provider trace entries did not publish")
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


def safe_cstring(client: Xbdm, pointer: int, limit: int = 0x100) -> str | None:
    if not 0x80000000 <= pointer < 0xE0000000:
        return None
    try:
        raw = client.read(pointer, limit)
    except Exception:
        return None
    raw = raw.split(b"\0", 1)[0]
    if not raw:
        return ""
    if any(byte < 0x20 or byte > 0x7E for byte in raw):
        return None
    return raw.decode("ascii")


def read_string_arg(client: Xbdm, pointer: int, limit: int = 0x100) -> str | None:
    """Decode a retail byte-vector argument, then try a plain C string."""
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
            value = client.read(begin, end - begin) if end != begin else b""
        except Exception:
            return None
        return value.decode("utf-8", errors="backslashreplace")
    return safe_cstring(client, pointer, limit)


def describe(client: Xbdm) -> None:
    counter = int.from_bytes(client.read(JOURNAL, 4), "big")
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
        print("No committed provider/publication event in the ring.")
        return
    if counter > RING_COUNT:
        print(f"WARNING: only the newest {RING_COUNT} records remain.")

    by_id = {probe.event_id: probe for probe in PROBES}
    for sequence, record in records:
        event_id = u32(record, 0x04)
        probe = by_id.get(event_id)
        name = probe.name if probe else f"unknown-{event_id}"
        r3, r4, r5, r6, r7 = (u32(record, off) for off in range(0x08, 0x1C, 4))
        lr = u32(record, 0x1C)
        vtable = u32(record, 0x20)
        print(
            f"{sequence:10d}  {name:22s} LR=0x{lr:08X} "
            f"r3=0x{r3:08X} r4=0x{r4:08X} r5=0x{r5:08X} "
            f"r6=0x{r6:08X} r7=0x{r7:08X} vtable=0x{vtable:08X}"
        )
        if probe and probe.layout == "provider":
            labels = (
                "background.name",
                "background.param1",
                "screen.name",
                "screen.param1",
                "popup.value0",
                "popup.value1",
            )
            values = []
            for label, offset in zip(labels, range(0x24, 0x3C, 4)):
                pointer = u32(record, offset)
                value = safe_cstring(client, pointer)
                values.append(
                    f"{label}={value!r}" if value is not None
                    else f"{label}=<0x{pointer:08X}>"
                )
            print("             " + " | ".join(values))
            if vtable != PROVIDER_VTABLE:
                print("             WARNING: unexpected provider vtable")
        elif probe and probe.name == "ion_core_send_event":
            event_words = [u32(record, offset) for offset in range(0x24, 0x3C, 4)]
            decoded = [safe_cstring(client, value) for value in event_words]
            print(
                "             event_words="
                + " ".join(f"0x{value:08X}" for value in event_words)
            )
            strings = [value for value in decoded if value]
            if strings:
                print(f"             event_strings={strings!r}")
        elif probe and probe.name == "ion_loadview_enqueue":
            decoded = [read_string_arg(client, value) for value in (r4, r5, r6, r7)]
            print("             request=" + " / ".join(repr(item) for item in decoded))
            if vtable != ION_LOADER_VTABLE:
                print("             WARNING: unexpected ION Loader vtable")
        elif probe and probe.layout == "register":
            name_begin, name_end = u32(record, 0x24), u32(record, 0x28)
            name = None
            if (
                0x80000000 <= name_begin <= name_end < 0xE0000000
                and name_end - name_begin <= 0x100
            ):
                try:
                    name = client.read(name_begin, name_end - name_begin).decode(
                        "ascii", errors="backslashreplace"
                    )
                except Exception:
                    pass
            consumer_vtable = u32(record, 0x2C)
            consumer_name = safe_cstring(client, u32(record, 0x30))
            print(
                f"             register_name={name!r} "
                f"consumer_vtable=0x{consumer_vtable:08X} "
                f"consumer_name={consumer_name!r}"
            )
        elif probe and probe.layout == "factory":
            name = read_string_arg(client, r4)
            if name is not None:
                print(f"             requested_viewmodel={name!r}")
        elif probe and probe.layout == "receiver":
            print(
                f"             delivered_event=0x{r4:04X} "
                f"payload_vtable=0x{u32(record, 0x24):08X}"
            )


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
            "Provider publication trace: "
            f"{states.count('traced')} traced, "
            f"{states.count('original')} original, "
            f"{sum(item.startswith('unexpected:') for item in states)} unexpected"
        )

        if args.action == "read":
            describe(client)
            return 0
        if args.action == "status":
            for probe, state in zip(PROBES, states):
                print(f"  0x{probe.site:08X} {probe.name}: {state}")
            return 0

        unexpected = [
            f"0x{probe.site:08X}={state}"
            for probe, state in zip(PROBES, states)
            if state not in ("original", "traced")
        ]
        if unexpected:
            raise RuntimeError("Refusing unknown entry site(s): " + ", ".join(unexpected))

        if args.action == "restore":
            for probe, state in zip(PROBES, states):
                if state == "traced":
                    client.write(probe.site, probe.original)
            if any(state_for(client, probe) != "original" for probe in PROBES):
                raise RuntimeError("One or more provider trace entries did not restore")
            print("Verified: provider publication entry instructions restored.")
            return 0

        arm(client)

        print(
            "Verified: passive KeyValueDataProvider/publication/ION trace armed."
        )
        print("No event, return value or frontend route was synthesized.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)

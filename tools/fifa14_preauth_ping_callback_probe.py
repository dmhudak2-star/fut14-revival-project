#!/usr/bin/env python3
"""Probe PreAuth/Ping completion paths without debugger breakpoints.

The two hooks are deliberately observational: they count calls, retain the
last r3-r6 values, and snapshot the request component/command at +0x28/+0x2A.
They neither call game code nor alter callback arguments.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from fifa14_plain_send_hook import (
    Xbdm,
    addi,
    addis,
    branch,
    insn,
    lhz,
    lwz,
    stw,
    verify_module,
)


COMPLETION_SITE = 0x82EAF3F0
COMPLETION_ORIGINAL = bytes.fromhex("7D8802A6")  # mflr r12
COMPLETION_STUB = 0x83C8D900

CALLBACK_SITE = 0x82EAE254
CALLBACK_ORIGINAL = bytes.fromhex("7D6903A6")  # mtctr r11
CALLBACK_STUB = 0x83C8D940

JOURNAL = 0x83C8D980
RECORD_SIZE = 0x20
JOURNAL_SIZE = 2 * RECORD_SIZE

# Known neighbouring allocations. Ranges use an exclusive end.
PREAUTH_FLOW_RANGE = (0x83C8D000, 0x83C8D400)
PROTOSSL_STUB_RANGE = (0x83C8DC00, 0x83C8DD80)

SYNC = 0x7C0004AC


@dataclass(frozen=True)
class Probe:
    name: str
    site: int
    original: bytes
    stub: int
    slot_end: int
    record_offset: int


PROBES = (
    Probe(
        "completion_entry",
        COMPLETION_SITE,
        COMPLETION_ORIGINAL,
        COMPLETION_STUB,
        CALLBACK_STUB,
        0x00,
    ),
    Probe(
        "user_callback",
        CALLBACK_SITE,
        CALLBACK_ORIGINAL,
        CALLBACK_STUB,
        JOURNAL,
        RECORD_SIZE,
    ),
)


def build_stub(probe: Probe) -> bytes:
    """Build one 0x40-byte, fall-through-equivalent journal stub.

    Count is published last, after ``sync``, so a concurrent XBDM reader that
    observes a new count also observes the associated register snapshot.
    r0/r9/r12 are volatile scratch registers; r3-r6 and r11 remain untouched.
    """
    offset = probe.record_offset
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x2680),      # r12 = JOURNAL
        lwz(9, 12, offset),
        addi(9, 9, 1),
        stw(3, 12, offset + 0x04),
        stw(4, 12, offset + 0x08),
        stw(5, 12, offset + 0x0C),
        stw(6, 12, offset + 0x10),
        lhz(0, 3, 0x28),            # request component
        stw(0, 12, offset + 0x14),
        lhz(0, 3, 0x2A),            # request command
        stw(0, 12, offset + 0x18),
        SYNC,
        stw(9, 12, offset),         # publish invocation count last
        int.from_bytes(probe.original, "big"),
        0,
    ]
    words[-1] = branch(
        probe.stub + (len(words) - 1) * 4,
        probe.site + 4,
        False,
    )
    return b"".join(insn(word) for word in words)


def ranges_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def validate_layout(stubs: dict[Probe, bytes]) -> None:
    regions: list[tuple[str, tuple[int, int]]] = []
    for probe, stub in stubs.items():
        region = (probe.stub, probe.stub + len(stub))
        if region[1] > probe.slot_end:
            raise AssertionError(
                f"{probe.name} stub ends at 0x{region[1]:08X}, beyond "
                f"its slot end 0x{probe.slot_end:08X}"
            )
        regions.append((probe.name, region))
    regions.append(("journal", (JOURNAL, JOURNAL + JOURNAL_SIZE)))

    for index, (name, region) in enumerate(regions):
        for other_name, other_region in regions[index + 1 :]:
            if ranges_overlap(region, other_region):
                raise AssertionError(f"{name} overlaps {other_name}")
        for reserved_name, reserved in (
            ("D000-D3xx PreAuth flow", PREAUTH_FLOW_RANGE),
            ("DC00 ProtoSSL stub", PROTOSSL_STUB_RANGE),
        ):
            if ranges_overlap(region, reserved):
                raise AssertionError(f"{name} overlaps {reserved_name}")


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    for probe in PROBES:
        offset = probe.record_offset
        print(
            f"{probe.name:18} count={u32(raw, offset)} "
            f"r3=0x{u32(raw, offset + 0x04):08X} "
            f"r4=0x{u32(raw, offset + 0x08):08X} "
            f"r5=0x{u32(raw, offset + 0x0C):08X} "
            f"r6=0x{u32(raw, offset + 0x10):08X} "
            f"component={u32(raw, offset + 0x14)} "
            f"command={u32(raw, offset + 0x18)}"
        )


def site_state(current: bytes, probe: Probe, patch: bytes) -> str:
    if current == probe.original:
        return "original"
    if current == patch:
        return "patched"
    return f"unexpected:{current.hex().upper()}"


def restore_sites(client: Xbdm, patches: dict[Probe, bytes]) -> None:
    """Best-effort rollback used only after all sites were prevalidated."""
    for probe in PROBES:
        try:
            if client.read(probe.site, 4) == patches[probe]:
                client.write(probe.site, probe.original)
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "read", "restore"))
    args = parser.parse_args()

    stubs = {probe: build_stub(probe) for probe in PROBES}
    patches = {
        probe: insn(branch(probe.site, probe.stub, False)) for probe in PROBES
    }
    validate_layout(stubs)

    client = Xbdm(args.host)
    try:
        verify_module(client)
        current = {probe: client.read(probe.site, 4) for probe in PROBES}
        states = {
            probe: site_state(current[probe], probe, patches[probe])
            for probe in PROBES
        }
        print(
            "PreAuth/Ping callback probe: "
            + " ".join(f"{probe.name}={states[probe]}" for probe in PROBES)
        )

        if args.action in ("status", "read"):
            describe(client)
            return 0

        unexpected = [
            probe for probe in PROBES if states[probe] not in ("original", "patched")
        ]
        if unexpected:
            raise RuntimeError(
                "Unexpected instruction at "
                + ", ".join(f"0x{probe.site:08X}" for probe in unexpected)
            )

        if args.action == "restore":
            for probe in PROBES:
                if states[probe] == "patched":
                    client.write(probe.site, probe.original)
            for probe in PROBES:
                if client.read(probe.site, 4) != probe.original:
                    raise RuntimeError(
                        f"Restore verification failed at 0x{probe.site:08X}"
                    )
            print("Verified: both original instructions restored.")
            return 0

        caves = {
            probe: client.read(probe.stub, probe.slot_end - probe.stub)
            for probe in PROBES
        }
        for probe in PROBES:
            allowed = (bytes(len(caves[probe])), stubs[probe])
            if caves[probe] not in allowed:
                raise RuntimeError(
                    f"Code slot 0x{probe.stub:08X}-0x{probe.slot_end - 1:08X} "
                    "is occupied by another tool"
                )
            if states[probe] == "patched" and caves[probe] != stubs[probe]:
                raise RuntimeError(f"Live {probe.name} stub does not match")

        if all(states[probe] == "patched" for probe in PROBES):
            print("Already armed; journal preserved.")
            return 0

        # Normalize a possible partial prior apply before changing executable
        # code. Unpublished stubs can then be replaced without an execution race.
        for probe in PROBES:
            if states[probe] == "patched":
                client.write(probe.site, probe.original)
        for probe in PROBES:
            if client.read(probe.site, 4) != probe.original:
                raise RuntimeError("Could not unpublish a partial probe")

        try:
            client.write(JOURNAL, bytes(JOURNAL_SIZE))
            for probe in PROBES:
                client.write(probe.stub, stubs[probe])
                if client.read(probe.stub, len(stubs[probe])) != stubs[probe]:
                    raise RuntimeError(f"{probe.name} stub verification failed")
            for probe in PROBES:
                client.write(probe.site, patches[probe])
                if client.read(probe.site, 4) != patches[probe]:
                    raise RuntimeError(f"{probe.name} patch verification failed")
        except Exception:
            restore_sites(client, patches)
            raise

        print("Verified: both breakpoint-free probes armed.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
